<template>
  <el-select
    v-model="selected"
    multiple
    filterable
    remote
    reserve-keyword
    clearable
    collapse-tags
    collapse-tags-tooltip
    allow-create
    default-first-option
    :remote-method="onKeyword"
    :loading="loading"
    :placeholder="placeholder"
    :style="{ width }"
    @visible-change="onVisibleChange"
  >
    <el-option
      v-for="o in mergedOptions"
      :key="o.value"
      :label="o.label"
      :value="o.value"
    />
  </el-select>
</template>

<script setup lang="ts">
/**
 * 关键词候选选择器：输入关键词 → 后端模糊查询 → 弹出候选 → 多选。
 *
 * 统计各子功能（门店数 / 盒数 / 趋势 / 门店能力）原本只能手打城市、省份、
 * 商品编码、门店名称。这里统一改为与数字看板一致的下拉候选交互：
 * - 对外仍是「英文逗号分隔的字符串」，与各组件现有的 ref<string> 直接对接；
 * - remote 模式：每次输入向后端要候选，避免一次性把上万候选塞进前端；
 * - allow-create：保留手工输入能力，后端查不到的值也能照样提交。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  fetchFilterOptions,
  type FilterOption,
  type FilterOptionField,
} from '@/api/client'
import { logger } from '@/utils/logger'

const props = withDefaults(
  defineProps<{
    /** 逗号分隔的字符串 */
    modelValue: string
    dbKey: string
    /** city=城市 province=省份 product=商品编码 store=门店名称 */
    field: FilterOptionField
    /** sales=销售表候选（默认）；inventory=库存表候选 */
    source?: 'sales' | 'inventory'
    placeholder?: string
    width?: string
    limit?: number
  }>(),
  { source: 'sales', placeholder: '输入关键词搜索', width: '260px', limit: 30 },
)

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const selected = ref<string[]>([])
const options = ref<FilterOption[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

function split(v: string): string[] {
  return (v || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

/** 已选值即使不在当前候选里也要能正常显示（remote 下拉只保留当次结果） */
const mergedOptions = computed<FilterOption[]>(() => {
  const known = new Set(options.value.map((o) => o.value))
  const extra = selected.value
    .filter((v) => !known.has(v))
    .map((v) => ({ value: v, label: v }))
  return [...options.value, ...extra]
})

watch(
  () => props.modelValue,
  (v) => {
    const arr = split(v)
    if (arr.join(',') !== selected.value.join(',')) selected.value = arr
  },
  { immediate: true },
)

watch(selected, (arr) => {
  const s = arr
    .map((x) => String(x).trim())
    .filter(Boolean)
    .join(',')
  if (s !== props.modelValue) emit('update:modelValue', s)
})

async function load(keyword: string) {
  loading.value = true
  try {
    const res = await fetchFilterOptions(
      props.dbKey,
      props.field,
      keyword,
      props.limit,
      props.source,
    )
    options.value = res.items || []
  } catch (e: any) {
    logger.warn(
      `加载候选值失败 ${props.field}: ${e?.message || e}`,
      'KeywordSelect',
    )
    options.value = []
  } finally {
    loading.value = false
  }
}

/** 输入即查，250ms 防抖，避免逐字打请求 */
function onKeyword(kw: string) {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => load(kw || ''), 250)
}

/** 首次展开时给一批默认候选，未输入关键词也能直接挑 */
function onVisibleChange(visible: boolean) {
  if (visible && !options.value.length) load('')
}

// 切库后旧候选不再适用，清空让下次展开重新拉取
watch(
  () => props.dbKey,
  () => {
    options.value = []
  },
)

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>
