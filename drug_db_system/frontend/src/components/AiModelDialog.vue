<template>
  <el-dialog
    v-model="visible"
    title="AI 模型配置"
    :width="isMobile ? '94%' : '720px'"
    :close-on-click-modal="false"
    append-to-body
    @open="onOpen"
  >
    <div class="intro">
      <el-icon><InfoFilled /></el-icon>
      <span>
        这里保存的是<b>你自己</b>的 API Key，只存后端、只回显后 4 位，别人看不到。
        可以保存多套并在它们之间切换（例如日常用便宜的，复杂问题切强模型）。
      </span>
    </div>

    <el-alert v-if="errMsg" :title="errMsg" type="error" show-icon :closable="false" class="mt" />

    <!-- ======== 已保存的配置 ======== -->
    <div v-if="items.length" class="list">
      <div
        v-for="it in items"
        :key="it.id"
        class="cfg"
        :class="{ active: it.is_active }"
      >
        <div class="cfg-main">
          <div class="cfg-head">
            <span class="cfg-name">{{ it.name }}</span>
            <el-tag v-if="it.is_active" type="success" size="small" effect="dark">使用中</el-tag>
            <el-tag size="small" effect="plain">{{ it.provider_label }}</el-tag>
          </div>
          <div class="cfg-meta">
            <span class="mono">{{ it.model }}</span>
            <span class="dot">·</span>
            <span class="mono">{{ it.api_key_masked || '未配置密钥' }}</span>
            <template v-if="it.base_url">
              <span class="dot">·</span>
              <span class="mono ellipsis" :title="it.base_url">{{ it.base_url }}</span>
            </template>
          </div>
        </div>
        <div class="cfg-actions">
          <el-button
            v-if="!it.is_active"
            size="small"
            type="primary"
            plain
            :loading="busyId === it.id"
            @click="onActivate(it)"
          >
            启用
          </el-button>
          <el-button size="small" text @click="startEdit(it)">编辑</el-button>
          <el-button size="small" text type="danger" @click="onDelete(it)">删除</el-button>
        </div>
      </div>
    </div>

    <el-empty v-else description="还没有配置模型，填一份就能用 AI 兜底了" :image-size="60" />

    <!-- ======== 编辑 / 新建表单 ======== -->
    <div v-if="formVisible" class="form">
      <div class="form-title">{{ form.id ? '编辑配置' : '新增配置' }}</div>

      <el-form label-width="86px" label-position="left" size="default">
        <el-form-item label="配置名称">
          <el-input v-model="form.name" maxlength="40" placeholder="如：DeepSeek 主力" />
        </el-form-item>

        <el-form-item label="供应商">
          <el-select v-model="form.provider" style="width: 100%" @change="onProviderChange">
            <el-option
              v-for="p in providers"
              :key="p.key"
              :label="p.label"
              :value="p.key"
            />
          </el-select>
          <div v-if="currentProvider?.note" class="hint">{{ currentProvider.note }}</div>
        </el-form-item>

        <el-form-item label="Base URL">
          <el-input v-model="form.base_url" placeholder="留空用供应商默认地址" />
          <div class="hint">
            <template v-if="currentProvider?.base_url">
              默认：<span class="mono">{{ currentProvider.base_url }}</span>
            </template>
            <template v-else>自定义供应商必须填写（OpenAI 兼容地址）</template>
          </div>
        </el-form-item>

        <el-form-item label="模型">
          <el-select
            v-model="form.model"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="选择或直接输入模型名"
          >
            <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
          </el-select>
          <div v-if="currentProvider?.docs_url" class="hint">
            密钥申请：<a :href="currentProvider.docs_url" target="_blank" rel="noreferrer">点此打开</a>
          </div>
        </el-form-item>

        <el-form-item label="API Key">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="form.id ? '留空表示不修改原密钥' : currentProvider?.key_hint || 'sk-...'"
          />
        </el-form-item>
      </el-form>

      <div v-if="testResult" class="test-result" :class="testResult.valid ? 'ok' : 'bad'">
        <el-icon><component :is="testResult.valid ? CircleCheck : CircleClose" /></el-icon>
        <span>{{ testResult.message }}</span>
      </div>

      <div class="form-actions">
        <el-button :loading="testing" @click="onTest">测试连接</el-button>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </div>
    </div>

    <template #footer v-if="!formVisible">
      <span class="foot-tip">没有合适的大模型？可先只填一份，AI 兜底会自动使用「使用中」的那套。</span>
      <el-button type="primary" @click="startCreate">新增配置</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import {
  fetchAiProviders,
  fetchAiModels,
  saveAiModel,
  activateAiModel,
  deleteAiModel,
  validateAiModel,
} from '@/api/client'
import type { AiProvider, AiModelConfig, AiModelValidateResult } from '@/api/client'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const isMobile = window.matchMedia('(max-width: 768px)').matches

const providers = ref<AiProvider[]>([])
const items = ref<AiModelConfig[]>([])
const errMsg = ref('')
const busyId = ref<number | null>(null)
const saving = ref(false)
const testing = ref(false)
const testResult = ref<AiModelValidateResult | null>(null)

const formVisible = ref(false)
const form = reactive<{
  id?: number
  name: string
  provider: string
  model: string
  base_url: string
  api_key: string
}>({ name: '', provider: '', model: '', base_url: '', api_key: '' })

const currentProvider = computed(() => providers.value.find((p) => p.key === form.provider))
const modelOptions = computed(() => currentProvider.value?.models || [])

async function onOpen() {
  errMsg.value = ''
  formVisible.value = false
  await Promise.all([loadProviders(), loadItems()])
}

async function loadProviders() {
  if (providers.value.length) return
  try {
    const r = await fetchAiProviders()
    providers.value = r.providers || []
  } catch (e: any) {
    errMsg.value = `供应商目录加载失败：${e?.message || e}`
  }
}

async function loadItems() {
  try {
    const r = await fetchAiModels()
    items.value = r.items || []
  } catch (e: any) {
    errMsg.value = `配置加载失败：${e?.message || e}`
  }
}

function onProviderChange() {
  const p = currentProvider.value
  form.model = p?.default_model || ''
  form.base_url = ''
  if (!form.name) form.name = p?.label || ''
  testResult.value = null
}

function startCreate() {
  const p = providers.value[0]
  form.id = undefined
  form.provider = p?.key || ''
  form.model = p?.default_model || ''
  form.base_url = ''
  form.api_key = ''
  form.name = p?.label || ''
  testResult.value = null
  formVisible.value = true
}

function startEdit(it: AiModelConfig) {
  form.id = it.id
  form.name = it.name
  form.provider = it.provider
  form.model = it.model
  form.base_url = it.base_url
  // 明文拿不到，留空 = 不修改；用户想换 key 才填
  form.api_key = ''
  testResult.value = null
  formVisible.value = true
}

async function onTest() {
  // 编辑状态下用户没重填 key：后端也拿不到明文，提示先填一个或依赖已有配置
  if (!form.api_key && form.id) {
    ElMessage.info('编辑状态下测试连接需要重新输入一次 API Key')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await validateAiModel({
      provider: form.provider,
      model: form.model,
      api_key: form.api_key,
      base_url: form.base_url || undefined,
    })
  } catch (e: any) {
    testResult.value = { valid: false, message: e?.message || '测试失败' }
  } finally {
    testing.value = false
  }
}

async function onSave() {
  if (!form.name.trim()) return ElMessage.warning('请填写配置名称')
  if (!form.provider) return ElMessage.warning('请选择供应商')
  if (!form.model.trim()) return ElMessage.warning('请填写模型名')
  if (!form.id && !form.api_key.trim()) return ElMessage.warning('首次保存必须填写 API Key')

  saving.value = true
  try {
    await saveAiModel({
      id: form.id,
      name: form.name.trim(),
      provider: form.provider,
      model: form.model.trim(),
      api_key: form.api_key.trim() || undefined,
      base_url: form.base_url.trim() || undefined,
    })
    ElMessage.success('已保存')
    formVisible.value = false
    await loadItems()
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onActivate(it: AiModelConfig) {
  busyId.value = it.id
  try {
    await activateAiModel(it.id)
    await loadItems()
    emit('saved')
    ElMessage.success(`已切换到「${it.name}」`)
  } catch (e: any) {
    ElMessage.error(e?.message || '切换失败')
  } finally {
    busyId.value = null
  }
}

async function onDelete(it: AiModelConfig) {
  try {
    await ElMessageBox.confirm(`确定删除「${it.name}」？删除后无法找回。`, '删除配置', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteAiModel(it.id)
    await loadItems()
    emit('saved')
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败')
  }
}
</script>

<style scoped>
.intro {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: #606266;
  background: #f6f4ff;
  border: 1px solid #e6e2ff;
  border-radius: 8px;
  padding: 10px 12px;
}
.intro .el-icon {
  color: #7c4dff;
  margin-top: 2px;
  flex-shrink: 0;
}
.mt {
  margin-top: 12px;
}
.list {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 46vh;
  overflow-y: auto;
}
.cfg {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fff;
}
.cfg.active {
  border-color: #b7eb8f;
  background: #f6ffed;
}
.cfg-main {
  flex: 1;
  min-width: 0;
}
.cfg-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cfg-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}
.cfg-meta {
  margin-top: 4px;
  font-size: 11px;
  color: #909399;
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
.mono {
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.dot {
  color: #dcdfe6;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cfg-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}
.form {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid #e6e2ff;
  border-radius: 10px;
  background: #fcfcff;
}
.form-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 10px;
}
.hint {
  font-size: 11px;
  color: #909399;
  line-height: 1.6;
  margin-top: 2px;
}
.test-result {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}
.test-result.ok {
  color: #529b2e;
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
}
.test-result.bad {
  color: #c45656;
  background: #fef0f0;
  border: 1px solid #fde2e2;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.foot-tip {
  float: left;
  font-size: 11px;
  color: #c0c4cc;
  line-height: 32px;
}
</style>
