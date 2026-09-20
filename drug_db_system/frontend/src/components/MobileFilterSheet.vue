<template>
  <el-drawer
    v-model="visible"
    title="筛选条件"
    direction="b"
    size="auto"
    :close-on-click-modal="true"
    class="mobile-filter-sheet"
  >
    <div class="filter-sheet-body">
      <div class="fs-item">
        <label>日期范围</label>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          size="default"
          style="width: 100%"
        />
      </div>

      <div class="fs-item">
        <label>{{ productLabel }}</label>
        <el-input
          v-model="productCodes"
          :placeholder="`例: ${productPlaceholder}`"
          clearable
          size="default"
          @keyup.enter="emit('query')"
          @clear="emit('query')"
        />
      </div>

      <div class="fs-item">
        <label>{{ cityLabel }}</label>
        <el-input
          v-model="cities"
          :placeholder="`例: ${cityPlaceholder}`"
          clearable
          size="default"
          @keyup.enter="emit('query')"
          @clear="emit('query')"
        />
      </div>

      <div v-if="showProvinces" class="fs-item">
        <label>省份</label>
        <el-input
          v-model="provinces"
          placeholder="例: 广东,广西"
          clearable
          size="default"
          @keyup.enter="emit('query')"
          @clear="emit('query')"
        />
      </div>

      <div class="fs-actions">
        <el-button size="default" @click="emit('reset')">重置</el-button>
        <el-button type="primary" size="default" @click="emit('query')">查询</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
const visible = defineModel<boolean>('visible', { required: true })
const dateRange = defineModel<[string, string] | null>('dateRange')
const productCodes = defineModel<string>('productCodes')
const cities = defineModel<string>('cities')
const provinces = defineModel<string>('provinces')

defineProps<{
  showProvinces?: boolean
  productLabel?: string
  cityLabel?: string
  productPlaceholder?: string
  cityPlaceholder?: string
}>()

const emit = defineEmits<{
  (e: 'query'): void
  (e: 'reset'): void
}>()
</script>

<style scoped>
.filter-sheet-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 2px 12px;
}

.fs-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.fs-item label {
  font-size: 13px;
  font-weight: 500;
  color: #606266;
}

.fs-actions {
  display: flex;
  gap: 12px;
  margin-top: 6px;
}

.fs-actions .el-button {
  flex: 1;
}
</style>
