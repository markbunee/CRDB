<template>
  <div class="mobile-table">
    <!-- 概览 + 视图切换 -->
    <div class="mt-head">
      <div class="mt-summary">
        <span>共 <b>{{ total ?? 0 }}</b> 条</span>
        <span class="mt-sep">·</span>
        <span>第 {{ page ?? 1 }}/{{ totalPages ?? 1 }} 页</span>
      </div>
      <div class="mt-seg">
        <button :class="{ on: view === 'table' }" type="button" @click="view = 'table'">表格</button>
        <button :class="{ on: view === 'card' }" type="button" @click="view = 'card'">卡片</button>
      </div>
    </div>

    <!-- 排序 -->
    <div class="mt-sort">
      <span class="mt-sort-label">排序</span>
      <el-select
        :model-value="sortField"
        size="small"
        placeholder="选择字段"
        class="mt-sort-field"
        @update:model-value="onFieldChange"
      >
        <el-option v-for="c in columns" :key="c.name" :label="c.name" :value="c.name" />
      </el-select>
      <button class="mt-dir" type="button" :disabled="!sortField" @click="toggleDir">
        {{ sortDir === 'desc' ? '降序' : '升序' }}
      </button>
    </div>

    <!-- ============ 表格视图：全表 + 自由滑动 + 缩放 ============ -->
    <div v-if="view === 'table'" class="mt-stage">
      <div ref="viewportEl" class="mt-viewport">
        <table class="mt-grid" :style="gridStyle">
          <thead>
            <tr>
              <th class="mt-pin mt-idx">#</th>
              <th v-for="c in columns" :key="c.name" :style="colStyle(c.name)">{{ c.name }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in rows" :key="i">
              <td class="mt-pin mt-idx">{{ i + 1 }}</td>
              <td
                v-for="c in columns"
                :key="c.name"
                :class="cellClass(c.name)"
                :style="colStyle(c.name)"
              >{{ display(row[c.name]) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="loading" class="mt-mask">数据加载中…</div>
        <div v-else-if="!rows.length" class="mt-mask">暂无数据</div>
      </div>

      <!-- 缩放控件（悬浮在表格右下角，不随内容滚动） -->
      <div class="mt-zoom">
        <button type="button" aria-label="缩小" @click="zoomOut">−</button>
        <button type="button" class="mt-zoom-val" title="恢复 100%" @click="resetZoom">{{ pct }}%</button>
        <button type="button" aria-label="放大" @click="zoomIn">＋</button>
      </div>
      <p class="mt-tip">双指缩放 · 拖动查看全表</p>
    </div>

    <!-- ============ 卡片视图：快速浏览 ============ -->
    <div v-else class="mt-cards">
      <div v-if="loading" class="mt-mask">数据加载中…</div>
      <div v-else-if="!rows.length" class="mt-mask">暂无数据</div>
      <div v-for="(row, i) in rows" :key="i" class="mt-card">
        <div v-if="primaryKey" class="mt-c-title">{{ row[primaryKey] ?? '' }}</div>
        <div v-for="c in displayColumns" :key="c.name" class="mt-c-row">
          <span class="mt-c-key">{{ c.name }}</span>
          <span class="mt-c-val" :class="cellClass(c.name)">{{ display(row[c.name]) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

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

/* ---------------- 视图 / 缩放 ---------------- */
const view = ref<'table' | 'card'>('table')

const ZOOM_MIN = 0.7
const ZOOM_MAX = 2.2
const ZOOM_STEP = 0.15
const zoom = ref(1)
const pct = computed(() => Math.round(zoom.value * 100))

function clampZoom(v: number) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(v * 100) / 100))
}
function zoomIn() {
  zoom.value = clampZoom(zoom.value + ZOOM_STEP)
}
function zoomOut() {
  zoom.value = clampZoom(zoom.value - ZOOM_STEP)
}
function resetZoom() {
  zoom.value = 1
}

/**
 * 缩放不用 transform: scale，而是「字号 + em 内边距/列宽」等比联动：
 * 文字不会模糊、sticky 表头与滚动条都正常，也不会出现 transform 导致的变形。
 */
const BASE_PX = 13
const gridStyle = computed(() => ({ fontSize: (BASE_PX * zoom.value).toFixed(2) + 'px' }))

/* ---------------- 列宽（px 基准换算成 em，随字号缩放） ---------------- */
const widthMap: Record<string, number> = {
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
  类别: 80,
  商品SAP编码: 130,
  店号区域ID: 120,
  '店名/区域': 180,
  销量: 80,
  过账日期: 130,
  合同价: 100,
  事业部名称: 140,
  事业部: 100,
  业务日期: 130,
  企业名称: 200,
  厂家名称: 200,
  单位: 70,
  生产批号: 100,
  生产日期: 110,
  销售数量: 90,
  供应商名称: 200,
}

function colStyle(name: string) {
  const px = widthMap[name] || 140
  return { minWidth: (px / BASE_PX).toFixed(2) + 'em' }
}

/** 数值列右对齐 + 等宽数字，便于纵向比对 */
const NUMERIC = new Set(['数量', '金额', '单价', '销量', '合同价', '销售数量'])
function cellClass(name: string) {
  return NUMERIC.has(name) ? 'mt-num' : ''
}

function display(v: any) {
  return v === null || v === undefined || v === '' ? '—' : String(v)
}

/* ---------------- 双指捏合缩放（单指仍走原生滑动） ---------------- */
const viewportEl = ref<HTMLElement | null>(null)
let startDist = 0
let startZoom = 1

function touchDist(t: TouchList) {
  const a = t[0]
  const b = t[1]
  // 只在两指触控时调用；这里显式判空，满足 strict 下的 noUncheckedIndexedAccess
  if (!a || !b) return 0
  return Math.hypot(b.clientX - a.clientX, b.clientY - a.clientY)
}

function onTouchStart(e: TouchEvent) {
  if (e.touches.length === 2) {
    startDist = touchDist(e.touches)
    startZoom = zoom.value
  }
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length === 2 && startDist > 0) {
    // 交由组件自己处理捏合（容器已 touch-action: pan-x pan-y，浏览器不会再做页面缩放）
    e.preventDefault()
    zoom.value = clampZoom(startZoom * (touchDist(e.touches) / startDist))
  }
}

function onTouchEnd(e: TouchEvent) {
  if (e.touches.length < 2) startDist = 0
}

onMounted(() => {
  const el = viewportEl.value
  if (!el) return
  el.addEventListener('touchstart', onTouchStart, { passive: true })
  el.addEventListener('touchmove', onTouchMove, { passive: false })
  el.addEventListener('touchend', onTouchEnd, { passive: true })
  el.addEventListener('touchcancel', onTouchEnd, { passive: true })
})

onBeforeUnmount(() => {
  const el = viewportEl.value
  if (!el) return
  el.removeEventListener('touchstart', onTouchStart)
  el.removeEventListener('touchmove', onTouchMove)
  el.removeEventListener('touchend', onTouchEnd)
  el.removeEventListener('touchcancel', onTouchEnd)
})

/* ---------------- 排序 ---------------- */
const sortField = ref('')
const sortDir = ref<'asc' | 'desc'>('asc')

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

/* ---------------- 卡片视图字段 ---------------- */
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
</script>

<style scoped>
.mobile-table {
  display: none; /* 仅手机端显示，见下方媒体查询 */
  padding: 4px 0 8px;
}

/* ---------- 顶部：概览 + 视图切换 ---------- */
.mt-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.mt-summary {
  font-size: 13px;
  color: #606266;
}

.mt-summary b {
  color: #1a4fc0;
  font-weight: 600;
}

.mt-sep {
  margin: 0 6px;
  color: #dcdfe6;
}

.mt-seg {
  display: flex;
  flex-shrink: 0;
  padding: 3px;
  background: #f0f2f5;
  border-radius: 999px;
}

.mt-seg button {
  border: none;
  background: transparent;
  color: #606266;
  font-size: 13px;
  padding: 6px 14px;
  min-height: 32px;
  border-radius: 999px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mt-seg button.on {
  background: #1a4fc0;
  color: #fff;
  font-weight: 600;
}

/* ---------- 排序行 ---------- */
.mt-sort {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.mt-sort-label {
  font-size: 13px;
  color: #606266;
  font-weight: 500;
  flex-shrink: 0;
}

.mt-sort-field {
  flex: 1;
  min-width: 0;
}

.mt-dir {
  flex-shrink: 0;
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid #dcdfe6;
  background: #fff;
  color: #606266;
  font-size: 13px;
  border-radius: 6px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mt-dir:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ---------- 表格舞台 ---------- */
.mt-stage {
  position: relative;
}

.mt-viewport {
  /* 双轴自由滑动：横向看全列、纵向看全行 */
  overflow: auto;
  -webkit-overflow-scrolling: touch;
  /* 保留原生拖动平移，同时关掉该区域内的浏览器双指缩放（改由组件接管） */
  touch-action: pan-x pan-y;
  max-height: 62vh;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 12px;
}

.mt-grid {
  border-collapse: separate;
  border-spacing: 0;
  width: max-content;
  /* 字号由 gridStyle 绑定驱动；下方 padding / 列宽全用 em，整体等比缩放不变形 */
  font-size: 13px;
  color: #303133;
}

.mt-grid th,
.mt-grid td {
  /* em 内边距：随字号一起放大缩小，避免拥挤或变形 */
  padding: 0.55em 0.75em;
  white-space: nowrap;
  text-align: center;
  border-bottom: 1px solid #ebeef5;
}

.mt-grid thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #f5f7fa;
  color: #303133;
  font-weight: 600;
  border-bottom: 1px solid #e4e7ed;
}

/* 首列（行号）横向吸附：横向滑动时始终看得见行号 */
.mt-pin {
  position: sticky;
  left: 0;
  z-index: 1;
  background: #fff;
  color: #909399;
  font-variant-numeric: tabular-nums;
}

.mt-grid thead th.mt-pin {
  z-index: 3;
  background: #f5f7fa;
  color: #606266;
}

.mt-idx {
  font-size: 0.85em;
}

.mt-grid tbody tr:nth-child(even) td {
  background: #fafbfc;
}

.mt-grid tbody tr:nth-child(even) td.mt-pin {
  background: #fafbfc;
}

.mt-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

/* ---------- 缩放控件 ---------- */
.mt-zoom {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid #e4e7ed;
  border-radius: 999px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}

.mt-zoom button {
  min-width: 34px;
  min-height: 34px;
  border: none;
  background: transparent;
  color: #606266;
  font-size: 16px;
  line-height: 1;
  border-radius: 999px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mt-zoom button:active {
  background: #f0f5ff;
}

.mt-zoom-val {
  min-width: 46px !important;
  font-size: 12px !important;
  color: #1a4fc0 !important;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.mt-tip {
  margin: 8px 2px 0;
  font-size: 11px;
  color: #a8abb2;
}

/* ---------- 加载 / 空态 ---------- */
.mt-mask {
  padding: 40px 12px;
  text-align: center;
  color: #909399;
  font-size: 13px;
}

/* ---------- 卡片视图 ---------- */
.mt-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mt-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.mt-c-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a4fc0;
  padding-bottom: 8px;
  margin-bottom: 4px;
  border-bottom: 1px solid #ebeef5;
  word-break: break-word;
}

.mt-c-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 5px 0;
  font-size: 13px;
  border-bottom: 1px dashed #f2f4f7;
}

.mt-c-row:last-child {
  border-bottom: none;
}

/* 不再写死 92px：键名过长时也不会挤压变形 */
.mt-c-key {
  flex: 0 0 auto;
  min-width: 72px;
  max-width: 44%;
  color: #909399;
  font-weight: 500;
  word-break: break-word;
}

.mt-c-val {
  flex: 1;
  min-width: 0;
  color: #303133;
  text-align: right;
  word-break: break-word;
}

@media (max-width: 768px) {
  .mobile-table {
    display: block;
  }
}
</style>
