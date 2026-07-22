<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { getCurrentMonthRange } from '@/utils/date'

export interface FilterModel {
  dateRange: [string, string]
  productCodes: string
  cities: string
  provinces: string
}

const emit = defineEmits<{
  (e: 'search', filters: FilterModel): void
  (e: 'reset'): void
}>()

const dateRange = ref<[string, string]>(['', ''])
const productCodes = ref('')
const cities = ref('')
const provinces = ref('')

function reset() {
  const { start, end } = getCurrentMonthRange()
  dateRange.value = [start, end]
  productCodes.value = ''
  cities.value = ''
  provinces.value = ''
  emitSearch()
}

function emitSearch() {
  emit('search', {
    dateRange: dateRange.value,
    productCodes: productCodes.value,
    cities: cities.value,
    provinces: provinces.value,
  })
}

onMounted(() => {
  reset()
})

watch(dateRange, (val) => {
  if (val && val[0] && val[1]) {
    emitSearch()
  }
}, { deep: true })
</script>

<template>
  <el-card shadow="never" class="filter-panel">
    <el-form label-position="top" inline>
      <el-form-item label="日期范围">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 260px"
        />
      </el-form-item>

      <el-form-item label="商品编码">
        <el-input
          v-model="productCodes"
          placeholder="例: 1058746,1086127,1091138"
          clearable
          style="width: 220px"
          @keyup.enter="emitSearch"
        />
      </el-form-item>

      <el-form-item label="城市">
        <el-input
          v-model="cities"
          placeholder="例: 广州,深圳"
          clearable
          style="width: 180px"
          @keyup.enter="emitSearch"
        />
      </el-form-item>

      <el-form-item label="省份">
        <el-input
          v-model="provinces"
          placeholder="例: 广东,广西"
          clearable
          style="width: 180px"
          @keyup.enter="emitSearch"
        />
      </el-form-item>

      <el-form-item label=" " class="filter-actions">
        <el-button type="primary" @click="emitSearch">
          <el-icon><Search /></el-icon> 统计
        </el-button>
        <el-button @click="reset">
          <el-icon><RefreshRight /></el-icon> 重置
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<style scoped>
.filter-panel {
  margin-bottom: 16px;
}
.filter-actions {
  display: flex;
  align-items: flex-end;
}
</style>
