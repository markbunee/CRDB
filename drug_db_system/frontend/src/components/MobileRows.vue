<template>
  <div class="mobile-rows">
    <div class="rows-summary">
      <span>共 {{ total }} 条</span>
      <span>第 {{ page }}/{{ totalPages }} 页</span>
    </div>

    <div class="sort-bar">
      <span class="sort-label">排序</span>
      <el-select
        :model-value="sortField"
        size="small"
        placeholder="选择字段"
        class="sort-field"
        @update:model-value="onFieldChange"
      >
        <el-option v-for="c in columns" :key="c.name" :label="c.name" :value="c.name" />
      </el-select>
      <el-button size="small" plain :disabled="!sortField" @click="toggleDir">
        {{ sortDir === 'desc' ? '降序' : '升序' }}
      </el-button>
    </div>
    <div v-if="sortField" class="sort-status">
      当前按「{{ sortField }}」{{ sortDir === 'desc' ? '降序' : '升序' }}排列
    </div>

    <div v-if="loading" class="rows-state">数据加载中…</div>
    <div v-else-if="!rows.length" class="rows-state">暂无数据</div>
    <div v-else class="card-list">
      <div v-for="(row, i) in rows" :key="i" class="rec-card">
        <div v-if="primaryKey" class="rec-title">{{ row[primaryKey] ?? '' }}</div>
        <div v-for="c in displayColumns" :key="c.name" class="rec-row">
          <span class="rec-key">{{ c.name }}</span>
          <span class="rec-val">{{ row[c.name] ?? '' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

interface ColumnDef {
  name: string
  type: string
}

const props = defineProps<{
  columns: ColumnDef[]
  rows: Record<string, any>[]
  loading?: boolean
  total?: number
  page?: number
  totalPages?: number
}>()

const emit = defineEmits<{
  (e: 'sort-change', payload: { prop: string; order: 'ascending' | 'descending' | null }): void
}>()

const sortField = ref('')
const sortDir = ref<'asc' | 'desc'>('asc')

// 主标题字段优先级：优先用门店 / 商品 / 企业名称等可读性强的字段
const PRIMARY_KEYS = [
  '门店名称',
  '门店详细名称',
  '商品名称',
  '企业名称',
  '事业部名称',
  '商品SAP编码',
  '商品编码',
  '来源公司',
  '公司',
]
const primaryKey = computed(
  () => props.columns.find((c) => PRIMARY_KEYS.includes(c.name))?.name || '',
)
const displayColumns = computed(() =>
  props.columns.filter((c) => c.name !== primaryKey.value),
)

function onFieldChange(val: string) {
  sortField.value = val
  emitSort()
}

function toggleDir() {
  sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  if (sortField.value) emitSort()
}

function emitSort() {
  emit('sort-change', {
    prop: sortField.value,
    order: sortDir.value === 'desc' ? 'descending' : 'ascending',
  })
}
</script>

<style scoped>
.mobile-rows {
  display: none; /* 仅手机端显示 */
  padding: 4px 0 8px;
}

.rows-summary {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #909399;
  padding: 0 4px 10px;
}

.sort-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.sort-label {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
}

.sort-field {
  flex: 1;
}

.rows-state {
  text-align: center;
  color: #909399;
  font-size: 13px;
  padding: 32px 0;
}

.card-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rec-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}

.rec-row {
  display: flex;
  gap: 10px;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed #f2f4f7;
}

.rec-row:last-child {
  border-bottom: none;
}

.sort-status {
  font-size: 12px;
  color: #909399;
  margin: -4px 4px 10px;
}

.rec-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a4fc0;
  padding-bottom: 6px;
  margin-bottom: 4px;
  border-bottom: 1px solid #ebeef5;
  word-break: break-all;
}

.rec-key {
  flex: 0 0 92px;
  color: #909399;
  font-weight: 500;
}

.rec-val {
  flex: 1;
  color: #303133;
  word-break: break-all;
  text-align: right;
}

@media (max-width: 768px) {
  .mobile-rows {
    display: block;
  }
}
</style>
