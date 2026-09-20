<template>
  <div class="store-city-stats">
    <!-- 维度切换 -->
    <div class="dimension-tabs">
      <div
        v-for="d in dimensions"
        :key="d.key"
        class="dim-tab"
        :class="{ active: dimension === d.key }"
        @click="switchDimension(d.key)"
      >
        <el-icon class="dim-icon"><component :is="d.icon" /></el-icon>
        <div class="dim-text">
          <div class="dim-name">{{ d.name }}</div>
          <div class="dim-desc">{{ d.desc }}</div>
        </div>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="filter-section">
      <div class="filter-grid">
        <div class="filter-group">
          <label class="filter-label">日期范围</label>
          <div class="filter-inline">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              size="default"
              style="width: 280px"
            />
            <el-checkbox v-model="mergeMonths" class="merge-checkbox">
              {{ mergeMonthsLabel }}
            </el-checkbox>
            <el-checkbox v-model="calcYoyMom" class="merge-checkbox yoy-checkbox">
              求同比环比
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">地域（{{ hasProvince ? '城市/省份 二选一' : (dbConfig?.region_label || '城市') }}）</label>
          <div class="filter-inline">
            <el-radio-group
              v-model="regionLevel"
              size="small"
              class="region-switch"
              @change="onRegionChange"
            >
              <el-radio-button label="city">{{ dbConfig?.region_label || '城市' }}</el-radio-button>
              <el-radio-button v-if="hasProvince" label="province">省份</el-radio-button>
            </el-radio-group>
            <KeywordSelect
              v-if="regionLevel === 'city'"
              v-model="cities"
              :db-key="dbKey"
              field="city"
              placeholder="输入城市关键词，如 广州"
              width="220px"
            />
            <KeywordSelect
              v-else
              v-model="provinces"
              :db-key="dbKey"
              field="province"
              placeholder="输入省份关键词，如 广东"
              width="220px"
            />
            <el-checkbox
              v-model="mergeCities"
              class="merge-checkbox"
            >
              合并{{ regionLabel }}
            </el-checkbox>
            <el-checkbox
              v-if="provinceExpanded"
              v-model="mergeProvinceCities"
              class="merge-checkbox"
            >
              合并省份内的城市
            </el-checkbox>
          </div>
        </div>

        <div class="filter-group">
          <label class="filter-label">品类（商品编码）</label>
          <div class="filter-inline">
            <KeywordSelect
              v-model="products"
              :db-key="dbKey"
              field="product"
              placeholder="输入编码或名称关键词"
              width="280px"
            />
            <el-checkbox
              v-model="mergeProducts"
              class="merge-checkbox"
            >
              合并品类
            </el-checkbox>
          </div>
        </div>

        <div v-if="supportsStoreType" class="filter-group">
          <label class="filter-label">门店类型</label>
          <div class="filter-inline">
            <el-radio-group v-model="storeType" size="small" @change="onStoreTypeChange">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="chain">连锁</el-radio-button>
              <el-radio-button value="franchise">加盟</el-radio-button>
            </el-radio-group>
          </div>
        </div>

        <div class="filter-group filter-actions">
          <div class="map-toggle">
            <el-switch v-model="mapNames" size="small" />
            <span class="map-toggle-label">品类映射</span>
            <el-button link type="primary" size="small" @click="mapManagerVisible = true">
              管理
            </el-button>
          </div>
          <el-button type="primary" :loading="loading" @click="handleQuery">
            <el-icon><Search /></el-icon> 统计
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
          <el-button type="success" :loading="exporting" @click="handleExport">
            <el-icon><Download /></el-icon> 导出CSV
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>{{ hintText }}</span>
      </div>

      <!-- 同比环比说明条 -->
      <div v-if="calcYoyMom && yoyRange" class="yoy-mom-info">
        <div class="yoy-mom-item">
          <span class="yoy-label">当前期</span>
          <span class="yoy-value">{{ dateRange?.[0] }} ~ {{ dateRange?.[1] }}</span>
        </div>
        <div class="yoy-mom-item">
          <span class="yoy-label yoy-color">同比期</span>
          <span class="yoy-value">{{ yoyRange.date_from }} ~ {{ yoyRange.date_to }}</span>
        </div>
        <div class="yoy-mom-item">
          <span class="yoy-label mom-color">环比期</span>
          <span class="yoy-value">{{ momRange!.date_from }} ~ {{ momRange!.date_to }}</span>
        </div>
      </div>
    </div>

    <!-- 结果区 -->
    <div class="results-section" v-loading="loading" element-loading-text="统计中...">
      <template v-if="tables.length > 0">
        <div class="results-summary">
          共 {{ tables.length }} 张表 · {{ totalRows }} 行
        </div>
        <div class="tables-scroll-area">
          <div
            v-for="(table, idx) in tables"
            :key="idx"
            class="stat-table-card"
          >
            <div class="table-header">
              <span class="table-title">{{ table.title }}</span>
              <span class="table-meta">
                {{ table.row_keys.length }} {{ table.row_header }} × {{ table.col_keys.length }} {{ table.col_header }}
              </span>
            </div>
            <div class="table-body">
              <table class="pivot-table">
                <thead>
                  <tr>
                    <th class="corner-header">{{ table.row_header }}</th>
                    <th
                      v-for="c in table.col_keys"
                      :key="c"
                      class="time-header"
                    >
                      {{ c }}
                    </th>
                    <template v-if="calcYoyMom">
                      <th class="total-header">{{ isAmount ? '合计(万元)' : '合计(盒)' }}</th>
                      <th class="yoy-header">{{ isAmount ? '同期合计(万元)' : '同期合计(盒)' }}</th>
                      <th class="yoy-header">同比(%)</th>
                      <th class="mom-header">{{ isAmount ? '上月合计(万元)' : '上月合计(盒)' }}</th>
                      <th class="mom-header">环比(%)</th>
                    </template>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in table.rows" :key="row.row_key">
                    <td class="row-cell">{{ row.row_key }}</td>
                    <td
                      v-for="c in table.col_keys"
                      :key="c"
                      class="count-cell"
                      :class="countClass(row.cells[c])"
                    >
                      {{ fmtCell(row.cells[c]) }}
                    </td>
                    <template v-if="calcYoyMom">
                      <td class="total-cell">{{ fmtCell(row.total) }}</td>
                      <td class="total-cell">{{ fmtCell(row.yoy_total) }}</td>
                      <td class="pct-cell" :class="pctClass(row.yoy_pct)">{{ fmtPct(row.yoy_pct) }}</td>
                      <td class="total-cell">{{ fmtCell(row.mom_total) }}</td>
                      <td class="pct-cell" :class="pctClass(row.mom_pct)">{{ fmtPct(row.mom_pct) }}</td>
                    </template>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><Box /></el-icon>
        <p>点击「统计」按钮查看实销{{ isAmount ? '金额' : '盒数' }}统计结果</p>
      </div>
    </div>

    <!-- 品类映射管理弹窗 -->
    <ProductMapManager v-model="mapManagerVisible" :db-key="dbKey" @saved="onMapSaved" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  RefreshRight,
  InfoFilled,
  Box,
  Location,
  Goods,
  Calendar,
  Download,
} from '@element-plus/icons-vue'
import {
  fetchBoxCount,
  exportBoxCount,
  downloadBlob,
  DOWNLOAD_DISABLED,
  type BoxCountTable,
  type StoreCountDimension,
  type StoreCountRegion,
  type DbMeta,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { fetchStatsLatestRange } from '@/api/client'
import { requireAdmin, requirePermission } from '@/utils/auth'
import { logger } from '@/utils/logger'
import { getDbConfig } from '@/utils/dbConfig'
import { loadMapPref, saveMapPref } from '@/utils/mapPref'
import ProductMapManager from './ProductMapManager.vue'
import KeywordSelect from '../KeywordSelect.vue'

const props = withDefaults(
  defineProps<{
    dbKey: string
    /** boxes=实销盒数；amount=实销金额（盒数×开票价，以万元、1 位小数展示） */
    mode?: 'boxes' | 'amount'
  }>(),
  { mode: 'boxes' },
)

const isAmount = computed(() => props.mode === 'amount')

// 默认区间：优先对齐「数据最新月份」（数据常滞后于系统当月，用系统当月会查出空区间，
// 同比/环比随之失真）。取不到时回退系统当月。
const { start: monthStart, end: monthEnd } = getCurrentMonthRange()
const defaultStart = ref(monthStart)
const defaultEnd = ref(monthEnd)

const dimensions = [
  { key: 'city' as StoreCountDimension, name: '城市维度', desc: '按城市/省份拆分 · 品类×月份', icon: Location },
  { key: 'product' as StoreCountDimension, name: '品类维度', desc: '按品类拆分 · 地域×月份', icon: Goods },
  { key: 'time' as StoreCountDimension, name: '时间维度', desc: '按月份拆分 · 地域×品类', icon: Calendar },
]

const dimension = ref<StoreCountDimension>('city')
const regionLevel = ref<StoreCountRegion>('city')
const dateRange = ref<[string, string]>([defaultStart.value, defaultEnd.value])
const mergeMonths = ref(false)
const cities = ref('')
const provinces = ref('')
const products = ref('')
const mergeCities = ref(false)
const mergeProducts = ref(false)
const mergeProvinceCities = ref(false)

const tables = ref<BoxCountTable[]>([])
const loading = ref(false)
const exporting = ref(false)
const calcYoyMom = ref(false)
const yoyRange = ref<{ date_from: string; date_to: string } | null>(null)
const momRange = ref<{ date_from: string; date_to: string } | null>(null)

// ---------- 品类映射（四个统计页共用同一份开关偏好） ----------
const mapNames = ref(loadMapPref())
const mapManagerVisible = ref(false)

// 切换开关：持久化偏好；已有结果时自动按新口径重查
watch(mapNames, (v) => {
  saveMapPref(v)
  if (tables.value.length > 0) handleQuery()
})

function onMapSaved() {
  if (tables.value.length > 0) handleQuery()
}

const regionLabel = computed(() =>
  regionLevel.value === 'province' && hasProvince.value ? '省份' : (dbConfig.value?.region_label || '城市'),
)

const dbConfig = ref<DbMeta | null>(null)
const hasProvince = computed(() => dbConfig.value?.region_levels?.includes('province') ?? true)
const supportsStoreType = computed(() => !!dbConfig.value?.supports_store_type)

onMounted(async () => {
  dbConfig.value = await getDbConfig(props.dbKey)
  await applyLatestRange()
})

/** 把默认区间对齐到库里「数据最新月份」 */
async function applyLatestRange() {
  try {
    const r = await fetchStatsLatestRange(props.dbKey)
    if (r?.date_from && r?.date_to) {
      defaultStart.value = r.date_from
      defaultEnd.value = r.date_to
      dateRange.value = [r.date_from, r.date_to]
    }
  } catch (e: any) {
    logger.warn('取数据最新月份失败，回退系统当月: ' + e.message, 'BoxCountStats')
  }
}

watch(() => props.dbKey, applyLatestRange)

const mergeMonthsLabel = computed(() =>
  dimension.value === 'time' ? '合并为单表' : '合并月范围',
)

/** 省份模式：后端按省份展开成多张子表，表内 行=城市、列=品类（月份按整段区间合并） */
const provinceExpanded = computed(
  () => regionLevel.value === 'province' && hasProvince.value,
)

const hintText = computed(() => {
  const d = dimension.value
  const parts: string[] = []
  if (d === 'city') {
    parts.push(mergeCities.value ? `全部${regionLabel.value}合并为一张表` : `每个${regionLabel.value}单独一张表`)
  } else if (d === 'product') {
    parts.push(mergeProducts.value ? '全部品类合并为一张表' : '每个品类单独一张表')
  } else {
    parts.push(mergeMonths.value ? '全部月份合并为一张表' : '每月单独一张表')
  }
  if (d === 'time') {
    parts.push(`行=${regionLabel.value}，列=品类`)
  } else if (d === 'city') {
    parts.push(`行=品类，列=${mergeMonths.value ? '月份区间' : '月份'}`)
  } else {
    parts.push(`行=${regionLabel.value}，列=${mergeMonths.value ? '月份区间' : '月份'}`)
  }
  const mergeHints: string[] = []
  if (mergeMonths.value) mergeHints.push('时间')
  if (mergeCities.value) mergeHints.push(regionLabel.value)
  if (mergeProducts.value) mergeHints.push('品类')
  if (mergeHints.length) {
    parts.push(`已合并：${mergeHints.join('、')}`)
  }
  if (isAmount.value) {
    parts.push('金额=盒数×开票价，以万元展示（未配置开票价的品类不计入）')
  }
  if (provinceExpanded.value) {
    if (mergeProvinceCities.value) {
      parts.push('每省份内城市已合并为整省合计（一省一行）')
    } else {
      parts.push('省份展开：每个省份一张子表，表内 行=城市、列=品类（月份按区间合并）')
    }
  }
  return '当前模式：' + parts.join('，')
})

const totalRows = computed(() =>
  tables.value.reduce((sum, t) => sum + t.row_keys.length, 0),
)

function switchDimension(d: StoreCountDimension) {
  if (dimension.value === d) return
  dimension.value = d
  mergeCities.value = false
  mergeProducts.value = false
  mergeMonths.value = false
  tables.value = []
}

function onRegionChange() {
  if (regionLevel.value === 'city') {
    provinces.value = ''
  } else {
    cities.value = ''
  }
  tables.value = []
}

function onStoreTypeChange() {
  if (tables.value.length > 0) handleQuery()
}

async function handleQuery() {
  loading.value = true
  try {
    const res = await fetchBoxCount(props.dbKey, {
      dimension: dimension.value,
      region_level: regionLevel.value,
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      merge_months: mergeMonths.value,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
      products: products.value || undefined,
      merge_cities: mergeCities.value,
      merge_products: mergeProducts.value,
      merge_province_cities: mergeProvinceCities.value,
      calc_yoy_mom: calcYoyMom.value,
      map_names: mapNames.value,
      store_type: storeType.value,
    })
    tables.value = res.tables
    yoyRange.value = res.yoy_range || null
    momRange.value = res.mom_range || null
    if (res.tables.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
    }
  } catch (e: any) {
    ElMessage.error('统计失败: ' + e.message)
    logger.error('实销盒数统计失败: ' + e.message, 'BoxCountStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dimension.value = 'city'
  regionLevel.value = 'city'
  dateRange.value = [defaultStart.value, defaultEnd.value]
  mergeMonths.value = false
  cities.value = ''
  provinces.value = ''
  products.value = ''
  mergeCities.value = false
  mergeProducts.value = false
  mergeProvinceCities.value = false
  calcYoyMom.value = false
  yoyRange.value = null
  momRange.value = null
  storeType.value = 'all'
  tables.value = []
}

async function handleExport() {
  if (DOWNLOAD_DISABLED) return
  if (!requirePermission('export', '导出统计数据')) return
  if (tables.value.length === 0) {
    ElMessage.warning('请先执行统计后再导出')
    return
  }
  exporting.value = true
  try {
    const blob = await exportBoxCount(props.dbKey, {
      dimension: dimension.value,
      region_level: regionLevel.value,
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      merge_months: mergeMonths.value,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
      products: products.value || undefined,
      merge_cities: mergeCities.value,
      merge_products: mergeProducts.value,
      merge_province_cities: mergeProvinceCities.value,
      calc_yoy_mom: calcYoyMom.value,
      map_names: mapNames.value,
      store_type: storeType.value,
      metric: props.mode,
    })
    const suffix = calcYoyMom.value ? '_yoy_mom' : ''
    const prefix = isAmount.value ? 'sales_amount' : 'box_count'
    const filename = `${prefix}_${dimension.value}_${regionLevel.value}${suffix}.csv`
    downloadBlob(blob, filename)
    ElMessage.success('导出完成')
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e.message)
    logger.error('盒数导出失败: ' + e.message, 'BoxCountStats')
  } finally {
    exporting.value = false
  }
}

/** 单元格展示：盒数原样；金额为元，÷10000 转万元并保留 1 位小数 */
function fmtCell(val: number | null | undefined): string {
  const v = val ?? 0
  return isAmount.value ? (v / 10000).toFixed(1) : String(v)
}

function countClass(val: number | null | undefined): string {
  const v = isAmount.value ? (val ?? 0) / 10000 : (val ?? 0)
  if (!v) return 'count-zero'
  const low = isAmount.value ? 1 : 100
  const mid = isAmount.value ? 10 : 1000
  if (v < low) return 'count-low'
  if (v < mid) return 'count-mid'
  return 'count-high'
}

function fmtPct(val: number | null | undefined): string {
  // 基期为 0（去年同期 / 上月无销量）时无法计算百分比，显示斜杠
  if (val === null || val === undefined) return '/'
  const sign = val > 0 ? '+' : ''
  return sign + val.toFixed(2) + '%'
}

function pctClass(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'pct-na'
  if (val > 0) return 'pct-up'
  if (val < 0) return 'pct-down'
  return 'pct-flat'
}
</script>

<style scoped>
.store-city-stats {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 16px;
}

.dimension-tabs {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.dim-tab {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.dim-tab:hover {
  border-color: #c0c4cc;
}

.dim-tab.active {
  border-color: #3b6bd6;
  box-shadow: 0 0 0 1px #3b6bd6 inset;
  background: linear-gradient(135deg, #f0f5ff 0%, #ffffff 100%);
}

.dim-icon {
  font-size: 22px;
  color: #909399;
  flex-shrink: 0;
}

.dim-tab.active .dim-icon {
  color: #3b6bd6;
}

.dim-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.dim-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.dim-tab.active .dim-name {
  color: #3b6bd6;
}

.dim-desc {
  font-size: 11px;
  color: #909399;
  line-height: 1.3;
}

.filter-section {
  background: #fff;
  border-radius: 8px;
  padding: 18px 22px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  flex-shrink: 0;
}

.filter-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 18px 24px;
  align-items: flex-end;
}

.filter-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-label {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  letter-spacing: 0.3px;
}

.filter-inline {
  display: flex;
  align-items: center;
  gap: 12px;
}

.region-switch {
  flex-shrink: 0;
}

.merge-checkbox {
  white-space: nowrap;
  font-size: 13px;
}

.filter-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 8px;
  margin-left: auto;
}

.map-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 2px;
  white-space: nowrap;
}

.map-toggle-label {
  font-size: 12px;
  color: #606266;
}

.hint-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 12px;
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 12px;
  color: #3b6bd6;
}

.results-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.results-summary {
  font-size: 13px;
  color: #909399;
  margin-bottom: 10px;
  flex-shrink: 0;
}

.tables-scroll-area {
  flex: 1;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
}

.stat-table-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  flex-shrink: 0;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  background: linear-gradient(135deg, #f0f5ff 0%, #e8f0fe 100%);
  border-bottom: 1px solid #e4e7ed;
}

.table-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.table-meta {
  font-size: 12px;
  color: #909399;
}

.table-body {
  overflow: auto;
  max-height: 420px;
}

.pivot-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.pivot-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.pivot-table th {
  background: #f5f7fa;
  color: #303133;
  font-weight: 600;
  padding: 10px 16px;
  border-bottom: 2px solid #e4e7ed;
  white-space: nowrap;
  text-align: center;
}

.corner-header {
  text-align: left !important;
  min-width: 100px;
}

.time-header {
  min-width: 90px;
}

.pivot-table td {
  padding: 9px 16px;
  border-bottom: 1px solid #ebeef5;
  text-align: center;
}

.pivot-table tbody tr:hover td {
  background: #f0f5ff;
}

.row-cell {
  text-align: left !important;
  font-weight: 500;
  color: #303133;
  position: sticky;
  left: 0;
  background: #fff;
  z-index: 0;
}

.pivot-table tbody tr:hover .row-cell {
  background: #f0f5ff;
}

.count-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

.count-zero {
  color: #c0c4cc;
}

.count-low {
  color: #e6a23c;
}

.count-mid {
  color: #409eff;
}

.count-high {
  color: #67c23a;
  font-weight: 700;
}

.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  gap: 12px;
}

.empty-icon {
  font-size: 48px;
}

.empty-state p {
  font-size: 14px;
}

/* ---------- 同比环比 ---------- */
.yoy-checkbox {
  color: #e6a23c;
  font-weight: 600;
}

.yoy-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: #e6a23c;
  border-color: #e6a23c;
}

.yoy-checkbox :deep(.el-checkbox__input.is-checked + .el-checkbox__label) {
  color: #e6a23c;
}

.yoy-mom-info {
  display: flex;
  gap: 20px;
  margin-top: 10px;
  padding: 10px 16px;
  background: linear-gradient(90deg, #fdf6ec 0%, #f0f9ff 50%, #fef0f0 100%);
  border-radius: 6px;
  flex-wrap: wrap;
}

.yoy-mom-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.yoy-label {
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: #3b6bd6;
  color: #fff;
}

.yoy-label.yoy-color {
  background: #e6a23c;
}

.yoy-label.mom-color {
  background: #f56c6c;
}

.yoy-value {
  color: #606266;
  font-variant-numeric: tabular-nums;
}

.total-header {
  background: #ecf5ff !important;
  color: #3b6bd6 !important;
  border-left: 2px solid #d0e3ff;
}

.yoy-header {
  background: #fdf6ec !important;
  color: #e6a23c !important;
  border-left: 1px solid #faecd8;
}

.mom-header {
  background: #fef0f0 !important;
  color: #f56c6c !important;
  border-left: 1px solid #fde2e2;
}

.total-cell {
  font-weight: 600;
  color: #3b6bd6;
  background: #f8fbff;
  font-variant-numeric: tabular-nums;
}

.pct-cell {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.pct-up {
  color: #f56c6c;
}

.pct-down {
  color: #67c23a;
}

.pct-flat {
  color: #909399;
}

.pct-na {
  color: #c0c4cc;
}
</style>
