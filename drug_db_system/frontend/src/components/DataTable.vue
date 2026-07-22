<template>
  <div class="data-table-wrapper">
    <el-table
      :data="rows"
      v-loading="loading"
      element-loading-text="数据加载中..."
      class="excel-table"
      :header-cell-style="headerStyle"
      :cell-style="cellStyle"
      @sort-change="handleSortChange"
      stripe
      border
      size="default"
    >
      <el-table-column
        v-for="col in displayColumns"
        :key="col.name"
        :prop="col.name"
        :label="col.name"
        :min-width="columnWidth(col.name)"
        sortable="custom"
        show-overflow-tooltip
      />
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface ColumnDef {
  name: string
  type: string
}

const props = defineProps<{
  columns: ColumnDef[]
  rows: Record<string, any>[]
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'sort-change', payload: { prop: string; order: 'ascending' | 'descending' | null }): void
}>()

const displayColumns = computed(() => {
  if (!props.columns?.length) {
    return props.rows?.length
      ? Object.keys(props.rows[0]).map((name) => ({ name, type: 'TEXT' }))
      : []
  }
  return props.columns
})

function columnWidth(name: string): number {
  const widths: Record<string, number> = {
    id: 60,
    日期: 155,
    月度: 80,
    公司: 200,
    来源公司: 220,
    门店编码: 110,
    门店名称: 160,
    门店详细名称: 220,
    省份: 80,
    城市: 90,
    区县: 80,
    地址: 240,
    商品编码: 110,
    商品名称: 220,
    规格: 120,
    生产厂家: 220,
    批号: 120,
    有效期: 110,
    数量: 80,
    金额: 90,
    单价: 80,
  }
  return widths[name] || 140
}

function headerStyle(): any {
  return {
    background: '#f5f7fa',
    color: '#303133',
    fontWeight: 600,
    fontSize: '13px',
    padding: '10px 0',
    borderBottom: '1px solid #e4e7ed',
  }
}

function cellStyle(): any {
  return {
    fontSize: '13px',
    color: '#606266',
    padding: '9px 0',
    borderBottom: '1px solid #ebeef5',
  }
}

function handleSortChange({ prop, order }: { prop?: string; order?: 'ascending' | 'descending' | null }) {
  if (!prop) return
  emit('sort-change', { prop, order: order || null })
}
</script>

<style scoped>
.data-table-wrapper {
  width: 100%;
  overflow-x: auto;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
}

.excel-table {
  min-width: 1400px;
}

:deep(.el-table__header-wrapper th) {
  background: #f5f7fa !important;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td) {
  background: #fafbfc;
}

:deep(.el-table__body tr:hover > td) {
  background: #f0f5ff;
}

:deep(.el-table__empty-block) {
  min-height: 160px;
}
</style>
