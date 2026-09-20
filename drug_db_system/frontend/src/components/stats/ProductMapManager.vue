<template>
  <el-dialog
    v-model="visible"
    title="品类映射管理（商品编码 → 中文名 / 开票价）"
    width="640px"
    align-center
    destroy-on-close
  >
    <div v-loading="loading" class="pm-body">
      <div class="pm-toolbar">
        <span class="pm-hint">
          无映射的编码将保持原编码显示；保存后立即对当前库的统计与导出生效。
        </span>
        <el-button size="small" type="primary" plain @click="addRow">
          <el-icon><Plus /></el-icon> 新增一行
        </el-button>
      </div>

      <el-table :data="rows" border size="small" max-height="380" empty-text="暂无映射，点击右上角新增">
        <el-table-column label="商品编码" width="200">
          <template #default="{ row }">
            <el-input v-model="row.code" size="small" placeholder="如 8106225" />
          </template>
        </el-table-column>
        <el-table-column label="品类中文名">
          <template #default="{ row }">
            <el-input v-model="row.name" size="small" placeholder="如 易善复" />
          </template>
        </el-table-column>
        <el-table-column label="开票价（元）" width="150">
          <template #default="{ row }">
            <el-input-number
              v-model="row.price"
              size="small"
              :min="0"
              :precision="2"
              :controls="false"
              placeholder="用于实销金额"
              style="width: 100%"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="center">
          <template #default="{ $index }">
            <el-button link type="danger" size="small" @click="rows.splice($index, 1)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  fetchProductMap,
  saveProductMap,
  type ProductMapItem,
} from '@/api/client'
import { logger } from '@/utils/logger'

const props = defineProps<{
  modelValue: boolean
  dbKey: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  /** 保存成功后通知父页面用新映射重新统计 */
  (e: 'saved'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const saving = ref(false)
/** 可编辑行；code 为空或 name 为空的行保存时会被后端清洗掉 */
const rows = ref<ProductMapItem[]>([])

function addRow() {
  rows.value.push({ code: '', name: '', price: null })
}

async function load() {
  loading.value = true
  try {
    const res = await fetchProductMap(props.dbKey)
    rows.value = res.items.map((i) => ({ ...i }))
  } catch (e: any) {
    ElMessage.error('加载映射失败: ' + e.message)
    logger.error('品类映射加载失败: ' + e.message, 'ProductMapManager')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  // 过滤空行；编码去重（保留最后一条）
  const valid = rows.value.filter((r) => r.code.trim() && r.name.trim())
  const map = new Map<string, ProductMapItem>()
  for (const r of valid) {
    map.set(r.code.trim(), {
      code: r.code.trim(),
      name: r.name.trim(),
      price: r.price ?? null,
    })
  }
  const items = Array.from(map.values())

  saving.value = true
  try {
    await saveProductMap(props.dbKey, items)
    ElMessage.success(`已保存 ${items.length} 条映射`)
    visible.value = false
    emit('saved')
  } catch (e: any) {
    ElMessage.error('保存失败: ' + e.message)
    logger.error('品类映射保存失败: ' + e.message, 'ProductMapManager')
  } finally {
    saving.value = false
  }
}

watch(visible, (v) => {
  if (v) load()
})
</script>

<style scoped>
.pm-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 160px;
}

.pm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pm-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}
</style>
