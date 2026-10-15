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
          <div v-if="isAmount" class="amount-unit-toggle">
            <span class="unit-label">金额单位</span>
            <el-radio-group v-model="amountUnit" size="small">
              <el-radio-button value="yuan">元</el-radio-button>
              <el-radio-button value="wan">万</el-radio-button>
            </el-radio-group>
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

    <!-- 金额口径完整性：未配置开票价的品类按 0 计入，金额会「静默偏低」 -->
    <el-alert
      v-if="isAmount && amountCoverage && amountCoverage.unpriced_qty > 0"
      class="amount-coverage-alert"
      type="warning"
      show-icon
      :closable="false"
      :title="`实销金额偏低：${amountCoverage.unpriced_share}% 的盒数没有开票价，未计入金额`"
    >
      <div class="coverage-text">
        区间 {{ amountCoverage.date_from }} ~ {{ amountCoverage.date_to }} 共
        {{ amountCoverage.total_qty.toLocaleString('zh-CN') }} 盒，其中
        <strong>{{ amountCoverage.unpriced_qty.toLocaleString('zh-CN') }} 盒</strong>
        （{{ amountCoverage.unpriced_share }}%，{{ amountCoverage.items.length }} 个品类）
        因没有开票价被按 0 计入：
        <br />
        <span class="coverage-codes">
          {{ amountCoverage.items
            .map((i) => `${i.code} ${i.name}（${i.qty.toLocaleString('zh-CN')} 盒）`)
            .join('、') }}
        </span>
        <br />
        请点右上角「品类映射 → 管理」补充开票价，然后重新统计。
      </div>
    </el-alert>

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
                    <!-- 行合计列：始终显示（不再只在同比环比时才有） -->
                    <th class="total-header">{{ isAmount ? `合计(${amountUnitLabel(amountUnit)})` : '合计(盒)' }}</th>
                    <template v-if="calcYoyMom">
                      <th class="yoy-header">{{ isAmount ? `同期合计(${amountUnitLabel(amountUnit)})` : '同期合计(盒)' }}</th>
                      <th class="yoy-header">同比(%)</th>
                      <th class="mom-header">{{ isAmount ? `上月合计(${amountUnitLabel(amountUnit)})` : '上月合计(盒)' }}</th>
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
                    <!-- 行合计：始终显示 -->
                    <td class="total-cell">{{ fmtCell(row.total) }}</td>
                    <template v-if="calcYoyMom">
                      <td class="total-cell">{{ fmtCell(row.yoy_total) }}</td>
                      <td class="pct-cell" :class="pctClass(row.yoy_pct)">{{ fmtPct(row.yoy_pct) }}</td>
                      <td class="total-cell">{{ fmtCell(row.mom_total) }}</td>
                      <td class="pct-cell" :class="pctClass(row.mom_pct)">{{ fmtPct(row.mom_pct) }}</td>
                    </template>
                  </tr>
                  <!-- 表尾合计行 = 列合计（形状与数据行一致，复用同一套单元格渲染） -->
                  <tr v-if="table.footer" class="summary-row">
                    <td class="row-cell total-cell">{{ table.footer.row_key }}</td>
                    <td
                      v-for="c in table.col_keys"
                      :key="c"
                      class="total-cell"
                    >
                      {{ fmtCell(table.footer.cells[c]) }}
                    </td>
                    <td class="total-cell">{{ fmtCell(table.footer.total) }}</td>
                    <template v-if="calcYoyMom">
                      <td class="total-cell">{{ fmtCell(table.footer.yoy_total) }}</td>
                      <td class="pct-cell" :class="pctClass(table.footer.yoy_pct)">{{ fmtPct(table.footer.yoy_pct) }}</td>
                      <td class="total-cell">{{ fmtCell(table.footer.mom_total) }}</td>
                      <td class="pct-cell" :class="pctClass(table.footer.mom_pct)">{{ fmtPct(table.footer.mom_pct) }}</td>
                    </template>
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
  MapLocation,
  Goods,
  Calendar,
  Download,
} from '@element-plus/icons-vue'
import {
  fetchBoxCount,
  DOWNLOAD_DISABLED,
  type BoxCountTable,
  type StoreCountDimension,
  type StoreCountRegion,
  type DbMeta,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import {
  fetchStatsLatestRange,
  fetchAmountCoverage,
  type AmountCoverageResponse,
} from '@/api/client'
import { requireAdmin, requirePermission } from '@/utils/auth'
import { logger } from '@/utils/logger'
import { submitExport } from '@/composables/downloadQueue'
import { getDbConfig } from '@/utils/dbConfig'
import { loadMapPref, saveMapPref } from '@/utils/mapPref'
import { formatAmount, amountUnitLabel, type AmountUnit } from '@/utils/amount'
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

/**
 * 「省份维度」= 原省份模式那套 省 → 市 展开布局，已单拎为独立维度；
 * 只有支持省份的库（如大参林）才出现该标签页。
 */
const dimensionTabs = computed(() =>
  hasProvince.value
    ? [...dimensions, { key: 'province' as StoreCountDimension, name: '省份维度', desc: '按省份拆分 · 省内城市×品类', icon: MapLocation }]
    : dimensions,
)

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

const tables = ref<BoxCountTable[]>([])

// 拆分表可能多达上百张（城市维度约 192 张），一次性铺满 DOM 是主要卡顿源；
// 默认只渲染前 N 张，其余点击展开（不影响导出与后端计算，仅控制渲染量）。
const TABLES_INITIAL = 30
const showAllTables = ref(false)
const visibleTables = computed(() =>
  showAllTables.value ? tables.value : tables.value.slice(0, TABLES_INITIAL),
)
const loading = ref(false)
const calcYoyMom = ref(false)
const yoyRange = ref<{ date_from: string; date_to: string } | null>(null)
const momRange = ref<{ date_from: string; date_to: string } | null>(null)
// 金额显示单位（仅在 mode === 'amount' 时生效）
const amountUnit = ref<AmountUnit>('wan')

// ---------- 品类映射（四个统计页共用同一份开关偏好） ----------
const mapNames = ref(loadMapPref())
const mapManagerVisible = ref(false)

// 切换开关：持久化偏好；已有结果时自动按新口径重查
watch(mapNames, (v) => {
  saveMapPref(v)
  if (tables.value.length > 0) handleQuery()
})

function onMapSaved() {
  checkAmountCoverage()
  if (tables.value.length > 0) handleQuery()
}

// ---------- 金额口径完整性检查 ----------
// 「实销金额」= 盒数 × 开票价，**未配置开票价的品类按 0 计入** ——
// 金额会静默偏低且表面看不出任何异常（实测：某区间 18% 的盒数被漏计）。
// 这里按**所选区间**向后端要准确结果（只扫一次事实表，约 200ms），
// 只有真的缺价才告警；不用「全表缺价清单」是为了避免长期误报稀释信号。
const amountCoverage = ref<AmountCoverageResponse | null>(null)

async function checkAmountCoverage() {
  amountCoverage.value = null
  if (!isAmount.value) return
  try {
    amountCoverage.value = await fetchAmountCoverage(
      props.dbKey,
      dateRange.value?.[0] || undefined,
      dateRange.value?.[1] || undefined,
    )
  } catch (e: any) {
    logger.warn('金额口径检查失败: ' + (e?.message || e), 'BoxCountStats')
  }
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
  checkAmountCoverage()
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

/**
 * 实时口径说明：与后端 compute_layout 的角色分配一致，避免文案与实际布局不符。
 * 地域级别（城市 / 省份）只是把「地域轴」换掉，拆表与行列角色由 dimension 决定，
 * 因此省份级别与城市级别的表格结构完全相同：
 * - 城市维度   → 地域拆表、行=品类、列=月份
 * - 品类维度   → 品类拆表、行=地域、列=月份
 * - 时间维度   → 月份拆表、行=地域、列=品类
 * - 省份维度   → 省份拆表、行=省内城市、列=品类（月份恒合并，独立于地域级别开关）
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
  if (isAmount.value) {
    parts.push(`金额=盒数×开票价（${amountUnitLabel(amountUnit.value)}，未配置开票价的品类不计入）`)
  }
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
  // 折叠开关现在按「轴」生效（与拆表方式无关），因此切换拆表方式时**保留**用户的选择，
  // 不再像以前那样清空——以前清空正是因为开关含义会随维度漂移。
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
  // 口径检查与统计并行，不阻塞主结果
  void checkAmountCoverage()
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
      // 必须传：后端默认 metric=boxes，漏传会让「实销金额」页拿回盒数（两者看起来一样）
      metric: props.mode,
      store_type: storeType.value,
    })
    tables.value = res.tables
    showAllTables.value = false   // 新结果默认回到「只渲染前 N 张」
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
  // 文件名由后端按口径生成（含 _yoy_mom / sales_amount 前缀），前端不用再拼
  submitExport({
    dbKey: props.dbKey,
    type: 'box_count',
    name: isAmount.value ? '实销金额统计' : '实销盒数统计',
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
      calc_yoy_mom: calcYoyMom.value,
      map_names: mapNames.value,
      store_type: storeType.value,
      metric: props.mode,
    },
  })
}

/** 单元格展示：盒数原样；金额按当前单位格式化 */
function fmtCell(val: number | null | undefined): string {
  if (isAmount.value) return formatAmount(val, amountUnit.value)
  return String(val ?? 0)
}

function countClass(val: number | null | undefined): string {
  const raw = val ?? 0
  const v = isAmount.value && amountUnit.value === 'wan' ? raw / 10000 : raw
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

/* 金额口径告警：不必撑开滚动区，固定高度即可 */
.amount-coverage-alert {
  flex: none;
  margin: 0 0 10px;
}

.coverage-text {
  font-size: 12px;
  line-height: 1.75;
}

.coverage-codes {
  color: #b45309;
  font-weight: 600;
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

/* 表尾「合计」行：与数据行明显区分（上边框 + 底色 + 加粗） */
.summary-row td {
  border-top: 2px solid #d0e3ff;
  background: #eef5ff;
  font-weight: 700;
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

.amount-unit-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
}
.amount-unit-toggle .unit-label {
  font-size: 12px;
  color: #909399;
}
</style>
