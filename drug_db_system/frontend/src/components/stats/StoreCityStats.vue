<template>
  <div class="store-city-stats">
    <!-- 维度切换 -->
    <div class="dimension-tabs">
      <div
        v-for="d in dimensionTabs"
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
            <el-checkbox v-model="mergeMonths" class="merge-checkbox" :disabled="dimension === 'province'">
              <span :title="dimension === 'province' ? '省份维度的行/列是 省内城市×品类，月份不参与布局，恒按日期区间合计' : '勾选后不按月份细分，整段区间合并为一个合计'">月份合计</span>
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
              <span :title="`勾选后不按${regionLabel}细分，合并为一个合计`">{{ regionLabel }}合计</span>
            </el-checkbox>
            <el-checkbox
              v-if="dimension === 'province'"
              v-model="mergeProvinceCities"
              class="merge-checkbox"
            >
              <span title="勾选后每省内城市折叠成整省合计（一省一行）">省内城市合计</span>
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
              <span title="勾选后不按品类细分，合并为一个合计">品类合计</span>
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
          <el-button type="success" @click="handleExport">
            <el-icon><Download /></el-icon> 导出CSV
          </el-button>
        </div>
      </div>

      <!-- 提示信息 -->
      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>{{ hintText }}</span>
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
            v-for="(table, idx) in visibleTables"
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
                    <!-- 故意不做「合计」列：门店数是去重计数（同一门店跨品种/跨月会重复），
                         单元格求和偏大且无业务含义（合计由后端 with_totals=False 一并不产出） -->
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
                      {{ row.cells[c] ?? 0 }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <!-- 拆分表惰性渲染：城市维度可能上百张表，一次全渲染会明显卡顿 -->
          <div v-if="tables.length > TABLES_INITIAL" class="tables-more">
            <el-button v-if="!showAllTables" link type="primary" @click="showAllTables = true">
              展开其余 {{ tables.length - TABLES_INITIAL }} 张表
            </el-button>
            <el-button v-else link type="info" @click="showAllTables = false">
              收起，仅显示前 {{ TABLES_INITIAL }} 张
            </el-button>
          </div>
        </div>
      </template>
      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><DataAnalysis /></el-icon>
        <p>点击「统计」按钮查看门店数统计结果</p>
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
  DataAnalysis,
  Location,
  MapLocation,
  Goods,
  Calendar,
  Download,
} from '@element-plus/icons-vue'
import {
  DOWNLOAD_DISABLED,
  fetchStoreCount,
  type StoreCountTable,
  type StoreCountDimension,
  type StoreCountRegion,
  type DbMeta,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { fetchStatsLatestRange } from '@/api/client'
import { requireAdmin, requirePermission } from '@/utils/auth'
import { logger } from '@/utils/logger'
import { submitExport } from '@/composables/downloadQueue'
import { getDbConfig } from '@/utils/dbConfig'
import { loadMapPref, saveMapPref } from '@/utils/mapPref'
import ProductMapManager from './ProductMapManager.vue'
import KeywordSelect from '../KeywordSelect.vue'

const props = defineProps<{
  dbKey: string
}>()

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

/**
 * 「省份维度」= 原省份模式那套 省 → 市 展开布局，已单拎为独立维度；
 * 只有支持省份的库（如大参林）才出现该标签页。
 */
const dimensionTabs = computed(() =>
  hasProvince.value
    ? [...dimensions, { key: 'province' as StoreCountDimension, name: '省份维度', desc: '按省份拆分 · 省内城市×品类', icon: MapLocation }]
    : dimensions,
)

// 筛选字段
const dimension = ref<StoreCountDimension>('city')
const regionLevel = ref<StoreCountRegion>('city')
const dateRange = ref<[string, string]>([defaultStart.value, defaultEnd.value])
const mergeMonths = ref(false)
const cities = ref('')
const provinces = ref('')
const products = ref('')
const mergeCities = ref(false)
const mergeProducts = ref(false)
// 仅在「省份维度」下有意义：把行轴上的省内城市折叠成整省合计
const mergeProvinceCities = ref(false)
// 门店类型筛选（连锁/加盟/全部），仅支持该维度的库（如大参林）显示
const storeType = ref<'all' | 'chain' | 'franchise'>('all')

// 结果
const tables = ref<StoreCountTable[]>([])

// 拆分表可能多达上百张（城市维度约 192 张），一次性铺满 DOM 是主要卡顿源；
// 默认只渲染前 N 张，其余点击展开（不影响后端计算与导出，仅控制渲染量）。
const TABLES_INITIAL = 30
const showAllTables = ref(false)
const visibleTables = computed(() =>
  showAllTables.value ? tables.value : tables.value.slice(0, TABLES_INITIAL),
)
const loading = ref(false)

// ---------- 品类映射（四个统计页共用同一份开关偏好） ----------
const mapNames = ref(loadMapPref())
const mapManagerVisible = ref(false)

// 切换开关：持久化偏好；已有结果时自动按新口径重查，免去手动再点一次
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
    logger.warn('取数据最新月份失败，回退系统当月: ' + e.message, 'StoreCityStats')
  }
}

watch(() => props.dbKey, applyLatestRange)

/**
 * 实时口径说明：与后端 compute_layout 的角色分配一致，避免文案与实际布局不符。
 * 地域级别（城市 / 省份）只是把「地域轴」换掉，拆表与行列角色由 dimension 决定，
 * 因此省份级别与城市级别的表格结构完全相同；
 * 「省 → 市 展开」是独立的省份维度（dimension=province），与地域级别开关无关。
 */
const hintText = computed(() => {
  const d = dimension.value
  const rl = regionLabel.value
  let split: string
  let row: string
  let col: string
  if (d === 'province') { split = '省份'; row = `省内${rl}`; col = '品类' }
  else if (d === 'city') { split = rl; row = '品类'; col = '月份' }
  else if (d === 'product') { split = '品类'; row = rl; col = '月份' }
  else { split = '月份'; row = rl; col = '品类' }

  const parts = [`按「${split}」拆表，行=${row}，列=${col}`]
  const folded: string[] = []
  if (mergeCities.value) folded.push(d === 'province' ? '省份' : rl)
  if (d === 'province' && mergeProvinceCities.value) folded.push(`省内${rl}`)
  if (mergeProducts.value) folded.push('品类')
  if (d !== 'province' && mergeMonths.value) folded.push('月份')
  if (folded.length) parts.push(`已折叠为合计：${folded.join('、')}`)
  if (d === 'province') parts.push('月份不参与布局（恒按日期区间合计）')
  return parts.join(' · ')
})

const totalRows = computed(() =>
  tables.value.reduce((sum, t) => sum + t.row_keys.length, 0),
)

function switchDimension(d: StoreCountDimension) {
  if (dimension.value === d) return
  dimension.value = d
  // 省份维度固定「省份拆表 + 省内城市行」，地域筛选按省份给 → 一并把地域级别切到省份
  if (d === 'province') regionLevel.value = 'province'
  // 折叠开关按「轴」生效（与拆表方式无关），切换拆表方式时保留用户选择，不再清空
  tables.value = []
}

function onRegionChange() {
  if (regionLevel.value === 'city') {
    provinces.value = ''
    // 省份维度只在省份级别下成立：切回城市级别时回落到城市维度，避免筛选器与布局错位
    if (dimension.value === 'province') dimension.value = 'city'
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
    const res = await fetchStoreCount(props.dbKey, {
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
      map_names: mapNames.value,
      store_type: storeType.value,
    })
    tables.value = res.tables
    showAllTables.value = false   // 新结果默认回到「只渲染前 N 张」
    if (res.tables.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
    }
  } catch (e: any) {
    ElMessage.error('统计失败: ' + e.message)
    logger.error('门店数统计失败: ' + e.message, 'StoreCityStats')
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
  tables.value = []
}

function handleExport() {
  if (DOWNLOAD_DISABLED) {
    ElMessage.warning('导出功能已关闭')
    return
  }
  if (!requirePermission('export', '导出统计数据')) return
  if (tables.value.length === 0) {
    ElMessage.warning('请先执行统计后再导出')
    return
  }
  submitExport({
    dbKey: props.dbKey,
    type: 'store_count',
    name: '门店数统计',
    params: {
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
      map_names: mapNames.value,
      store_type: storeType.value,
    },
  })
}

function countClass(val: number | undefined): string {
  if (!val || val === 0) return 'count-zero'
  if (val < 10) return 'count-low'
  if (val < 50) return 'count-mid'
  return 'count-high'
}
</script>

<style scoped>
.store-city-stats {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  gap: 12px;
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
  padding: 10px 14px;
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
  display: none;
}

.filter-section {
  background: #fff;
  border-radius: 8px;
  padding: 14px 18px;
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

.tables-more {
  padding: 10px 0 2px;
  text-align: center;
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
</style>
