<template>
  <div class="mfs">
    <div class="mfs-row">
      <span v-if="dateText" class="mfs-chip">{{ dateText }}</span>
      <span v-if="productCodes" class="mfs-chip">{{ productLabel }}：{{ productCodes }}</span>
      <span v-if="cities" class="mfs-chip">{{ cityLabel }}：{{ cities }}</span>
      <span v-if="showProvinces && provinces" class="mfs-chip">省份：{{ provinces }}</span>
      <button class="mfs-entry" type="button" @click="emit('filter')">
        <el-icon :size="14"><Filter /></el-icon>
        <span>筛选</span>
        <span v-if="active" class="mfs-dot"></span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Filter } from '@element-plus/icons-vue'

const props = defineProps<{
  dateRange?: [string, string] | null
  productCodes?: string
  cities?: string
  provinces?: string
  showProvinces?: boolean
  productLabel?: string
  cityLabel?: string
}>()

const emit = defineEmits<{ (e: 'filter'): void }>()

const dateText = computed(() => {
  const r = props.dateRange
  if (!r || !r[0] || !r[1]) return ''
  return `${r[0]} ~ ${r[1]}`
})

/** 是否已挂了筛选条件（用于「筛选」入口的红点提示） */
const active = computed(
  () =>
    !!dateText.value ||
    !!props.productCodes ||
    !!props.cities ||
    (!!props.showProvinces && !!props.provinces),
)
</script>

<style scoped>
.mfs {
  display: none; /* 仅手机端显示，见下方媒体查询 */
}

.mfs-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-bottom: 12px;
}

.mfs-chip {
  max-width: 100%;
  padding: 7px 10px;
  background: #fff;
  border: 1px solid #e8edf6;
  border-radius: 8px;
  font-size: 11px;
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mfs-entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  padding: 7px 12px;
  background: #fff;
  border: 1px solid #d6e4ff;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  color: #3b6bd6;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mfs-entry:active {
  background: #f0f5ff;
}

.mfs-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f56c6c;
}

@media (max-width: 768px) {
  .mfs {
    display: block;
  }
}
</style>
