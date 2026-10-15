<template>
  <div class="ai-card">
    <div class="card-head">
      <el-icon><EditPen /></el-icon>
      <span class="head-title">请补齐查询条件</span>
      <el-tag v-if="card.capability_label" type="info" size="small" effect="plain">
        {{ card.capability_label }}
      </el-tag>
    </div>

    <div v-if="card.data_range?.max_date" class="data-hint">
      <el-icon><InfoFilled /></el-icon>
      <span>库内数据截至 <b>{{ card.data_range.max_date }}</b>，超出部分会自动按此日期统计。</span>
    </div>

    <!-- 核心区：必问的槽位 -->
    <div class="slot-list">
      <div v-for="s in card.core" :key="s.key" class="slot" :class="s.state">
        <div class="slot-line">
          <span class="slot-label">{{ s.label }}</span>
          <span class="slot-badge">{{ stateText(s.state) }}</span>
          <span v-if="s.display" class="slot-display">{{ s.display }}</span>
        </div>
        <div v-if="s.note" class="slot-note">{{ s.note }}</div>

        <!-- 排名：数字输入 -->
        <el-input-number
          v-if="s.key === 'top_n'"
          v-model="answers.top_n"
          :min="1"
          :max="500"
          size="small"
          placeholder="不限"
        />

        <!-- 其余：选项 chips -->
        <div v-else-if="s.options?.length" class="chip-row">
          <button
            v-for="opt in s.options"
            :key="opt.v"
            type="button"
            class="chip"
            :class="{ on: isPicked(s, opt) }"
            @click="pick(s, opt)"
          >
            {{ opt.label }}
          </button>
        </div>

        <!-- 子项：省是否拆成城市 / 时间粒度 -->
        <div v-if="s.sub?.length" class="sub-row">
          <template v-for="sub in s.sub" :key="sub.key">
            <span class="sub-label">{{ sub.label }}</span>
            <template v-if="sub.type === 'bool'">
              <el-switch v-model="subValues[`${s.key}.${sub.key}`]" size="small" />
            </template>
            <template v-else>
              <button
                v-for="o in sub.options || []"
                :key="o.v"
                type="button"
                class="chip sm"
                :class="{ on: subValues[`${s.key}.${sub.key}`] === o.v }"
                @click="subValues[`${s.key}.${sub.key}`] = o.v"
              >
                {{ o.label }}
              </button>
            </template>
          </template>
        </div>
      </div>
    </div>

    <!-- 高级区：已填默认值，看得见、可改，但不强制打断 -->
    <div class="adv-toggle" @click="showAdv = !showAdv">
      <span>高级条件（已按默认值填好，可展开修改）</span>
      <el-icon><component :is="showAdv ? ArrowUp : ArrowDown" /></el-icon>
    </div>
    <div v-show="showAdv" class="slot-list adv">
      <div v-for="s in card.advanced" :key="s.key" class="slot" :class="s.state">
        <div class="slot-line">
          <span class="slot-label">{{ s.label }}</span>
          <span class="slot-badge">{{ stateText(s.state) }}</span>
          <span v-if="s.display" class="slot-display">{{ s.display }}</span>
        </div>
        <el-input-number
          v-if="s.key === 'top_n'"
          v-model="answers.top_n"
          :min="1"
          :max="500"
          size="small"
        />
        <div v-else-if="s.options?.length" class="chip-row">
          <button
            v-for="opt in s.options"
            :key="opt.v"
            type="button"
            class="chip"
            :class="{ on: isPicked(s, opt) }"
            @click="pick(s, opt)"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
    </div>

    <div class="card-actions">
      <span v-if="!canSubmit" class="pending-tip">
        还需补：{{ pendingLabels.join('、') }}
      </span>
      <el-button type="primary" :disabled="!canSubmit" :loading="busy" @click="onSubmit">
        查询
      </el-button>
      <el-button :disabled="busy" @click="emit('cancel')">放弃</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { EditPen, InfoFilled, ArrowUp, ArrowDown } from '@element-plus/icons-vue'
import type { AiCard, AiSlot, AiSlotOption } from '@/api/client'

const props = defineProps<{
  card: AiCard
  busy?: boolean
}>()
const emit = defineEmits<{
  (e: 'submit', payload: { answers: Record<string, any>; edited_from: string }): void
  (e: 'cancel'): void
}>()

const showAdv = ref(false)
/** 用户填好的答案（已确定的槽位会预填进来，提交时一起带走） */
const answers = reactive<Record<string, any>>({})
/** 子项的值（如 省拆成城市 / 时间粒度），key 为 `slot.sub` */
const subValues = reactive<Record<string, any>>({})
/** 用户手动改动过的字段 —— 埋点用，用来定位提取器错在哪 */
const edited = new Set<string>()

function stateText(st: string) {
  return st === 'resolved' ? '已确定' : st === 'ambiguous' ? '待确认' : '必填'
}

/** 卡片变化时重建预填（同一会话可能连续问多个问题） */
watch(
  () => props.card,
  (c) => {
    Object.keys(answers).forEach((k) => delete answers[k])
    Object.keys(subValues).forEach((k) => delete subValues[k])
    edited.clear()
    for (const s of [...(c.core || []), ...(c.advanced || [])]) {
      if (s.value !== null && s.value !== undefined && s.value !== '') {
        answers[s.key] = s.value
      }
      for (const sub of s.sub || []) {
        subValues[`${s.key}.${sub.key}`] = sub.value
      }
    }
  },
  { immediate: true, deep: false },
)

const pendingLabels = computed(() => {
  return (props.card.core || [])
    .filter((s) => s.state === 'missing' && isEmpty(answers[s.key]))
    .map((s) => s.label)
})

const canSubmit = computed(() => pendingLabels.value.length === 0)

function isEmpty(v: any) {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length)
}

function isPicked(s: AiSlot, opt: AiSlotOption) {
  const cur = answers[s.key]
  if (s.key === 'region') {
    if (opt.level === 'nation') return cur?.level === 'nation'
    return Array.isArray(cur?.values) && cur.values.includes(opt.v)
  }
  if (s.key === 'product') return (cur?.names || []).includes(opt.v)
  if (s.key === 'time') return cur?.preset === opt.v
  return cur === opt.v
}

function pick(s: AiSlot, opt: AiSlotOption) {
  edited.add(s.key)
  const key = s.key
  if (key === 'region') {
    if (opt.level === 'nation') {
      answers.region = { level: 'nation', values: [] }
      return
    }
    const cur = answers.region && Array.isArray(answers.region.values)
      ? { ...answers.region }
      : { level: opt.level, values: [] as string[] }
    if (cur.level !== opt.level) {
      cur.level = opt.level
      cur.values = []
    }
    const i = cur.values.indexOf(opt.v)
    if (i >= 0) cur.values.splice(i, 1)
    else cur.values.push(opt.v)
    answers.region = cur
  } else if (key === 'time') {
    answers.time = { preset: opt.v }
  } else if (key === 'product') {
    const set = new Set<string>(answers.product?.names || [])
    if (set.has(opt.v)) set.delete(opt.v)
    else set.add(opt.v)
    answers.product = { names: [...set] }
  } else {
    answers[key] = opt.v
  }
}

function onSubmit() {
  // 子项回填到各自槽位
  for (const s of [...(props.card.core || []), ...(props.card.advanced || [])]) {
    for (const sub of s.sub || []) {
      const v = subValues[`${s.key}.${sub.key}`]
      if (v === undefined) continue
      if (s.key === 'region') {
        answers.region = { ...(answers.region || { level: 'province', values: [] }), [sub.key]: v }
      } else if (s.key === 'time') {
        answers.time = { ...(answers.time || {}), [sub.key]: v }
      }
    }
  }
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(answers)) {
    if (!isEmpty(v)) out[k] = v
  }
  emit('submit', { answers: out, edited_from: [...edited].join(',') })
}
</script>

<style scoped>
.ai-card {
  border: 1px solid #e6e2ff;
  border-radius: 10px;
  background: #fcfcff;
  padding: 12px 14px;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.card-head .el-icon {
  color: #7c4dff;
}
.data-hint {
  display: flex;
  gap: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
}
.slot-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.slot-list.adv {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e6e2ff;
}
.slot {
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #ebeef5;
}
.slot.missing {
  border-color: #fbc4c4;
  background: #fef6f6;
}
.slot.ambiguous {
  border-color: #f5dab1;
  background: #fdfaf4;
}
.slot.resolved {
  border-color: #d7e8cd;
  background: #f8fcf6;
}
.slot-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.slot-label {
  font-size: 12px;
  font-weight: 600;
  color: #303133;
}
.slot-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 6px;
  background: #ebeef5;
  color: #909399;
}
.slot.missing .slot-badge {
  background: #fde2e2;
  color: #f56c6c;
}
.slot.ambiguous .slot-badge {
  background: #fdf0dc;
  color: #e6a23c;
}
.slot.resolved .slot-badge {
  background: #e8f5e3;
  color: #67c23a;
}
.slot-display {
  font-size: 12px;
  color: #606266;
}
.slot-note {
  margin-top: 4px;
  font-size: 11px;
  color: #e6a23c;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.chip {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid #dcdfe6;
  border-radius: 14px;
  background: #fff;
  color: #606266;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.chip:hover {
  border-color: #b39dff;
  color: #7c4dff;
}
.chip.on {
  background: #7c4dff;
  border-color: #7c4dff;
  color: #fff;
}
.chip.sm {
  padding: 2px 8px;
  font-size: 11px;
}
.sub-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #ebeef5;
}
.sub-label {
  font-size: 11px;
  color: #909399;
}
.adv-toggle {
  margin-top: 10px;
  font-size: 11px;
  color: #909399;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
}
.adv-toggle:hover {
  color: #7c4dff;
}
.card-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.pending-tip {
  margin-right: auto;
  font-size: 11px;
  color: #f56c6c;
}
</style>
