<template>
  <div class="store-ability-stats">
    <!-- 筛选区 -->
    <div class="filter-section">
      <div class="filter-grid">
        <div class="filter-group">
          <label class="filter-label">日期范围</label>
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
        </div>

        <div class="filter-group">
          <label class="filter-label">门店名称（可留空，默认全部；多个用英文逗号分隔）</label>
          <el-input
            v-model="storeKeyword"
            placeholder="例: 花都花山,海明路"
            clearable
            size="default"
            style="width: 260px"
            @keyup.enter="handleQuery"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">品类 / 商品编码（可留空，默认全部；多个用英文逗号分隔）</label>
          <el-input
            v-model="products"
            placeholder="例: 1058746,1086127"
            clearable
            size="default"
            style="width: 260px"
            @keyup.enter="handleQuery"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">展示门店数</label>
          <el-select v-model="topN" size="default" style="width: 120px">
            <el-option label="前 50 家" :value="50" />
            <el-option label="前 100 家" :value="100" />
            <el-option label="前 200 家" :value="200" />
          </el-select>
        </div>

        <div class="filter-group filter-actions">
          <el-button type="primary" :loading="loading" @click="handleQuery">
            <el-icon><Search /></el-icon> 统计
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
          <el-button type="success" :loading="exporting" @click="handleExport">
            <el-icon><Download /></el-icon> 导出Excel
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>
          范围：{{ city }} · 按「门店名称」统计实销盒数 ·
          门店与品类均支持英文逗号分隔多个查询，留空表示全部 ·
          门店为模糊包含匹配 ·
          点击门店名称可查看该店按月产出趋势 ·
          品类占比 = 该品类盒数 ÷ 该门店实销总数 ·
          末位品类为该店销量倒数第一（品类数 ≤ 3 时会与前列重复）
        </span>
      </div>
      <div class="single-day-tip" v-if="isSingleDay">
        <el-icon><InfoFilled /></el-icon>
        <span>当前为单日查询（首尾日期相同）</span>
      </div>
    </div>

    <!-- 结果区 -->
    <div class="results-section" v-loading="loading" element-loading-text="统计中...">
      <template v-if="stores.length > 0">
        <!-- 汇总卡片 -->
        <div class="summary-cards">
          <div class="sum-card">
            <div class="sum-label">动销门店总数</div>
            <div class="sum-value">{{ fmtQty(summary.store_count) }}</div>
            <div class="sum-sub">{{ city }}全量</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">实销总盒数</div>
            <div class="sum-value">{{ fmtQty(summary.total_qty) }}</div>
            <div class="sum-sub">{{ city }}全量</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">店均盒数</div>
            <div class="sum-value">{{ fmtQty(avgQty) }}</div>
            <div class="sum-sub">总盒数 ÷ 门店数</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">本次展示</div>
            <div class="sum-value">{{ fmtQty(summary.returned) }}</div>
            <div class="sum-sub">按实销盒数降序前 {{ topN }} 家</div>
          </div>
        </div>

        <!-- 明细表 -->
        <div class="table-card">
          <el-table
            :data="stores"
            border
            stripe
            size="small"
            height="100%"
            class="ability-table"
          >
            <el-table-column prop="rank" label="排名" width="64" align="center">
              <template #default="{ row }">
                <span class="rank-badge" :class="rankClass(row.rank)">{{ row.rank }}</span>
              </template>
            </el-table-column>

            <el-table-column
              prop="store_name"
              label="门店名称"
              min-width="210"
              show-overflow-tooltip
              fixed
            >
              <template #default="{ row }">
                <span
                  class="store-link"
                  title="点击查看该店月度产出趋势"
                  @click="openTrend(row.store_name)"
                >{{ row.store_name }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="qty" label="实销总数" width="110" align="right">
              <template #default="{ row }">
                <span class="qty-strong">{{ fmtQty(row.qty) }}</span>
              </template>
            </el-table-column>

            <el-table-column prop="cat_cnt" label="品类数" width="78" align="center" />

            <el-table-column
              v-for="i in 3"
              :key="`top-${i}`"
              :label="`最佳品类${i}`"
              min-width="190"
            >
              <template #default="{ row }">
                <CategoryCell :item="catAt(row, i - 1)" />
              </template>
            </el-table-column>

            <el-table-column label="末位品类" min-width="190">
              <template #default="{ row }">
                <CategoryCell :item="row.last_category" tone="last" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><DataAnalysis /></el-icon>
        <p>点击「统计」按钮查看{{ city }}门店能力分析</p>
      </div>
    </div>

    <!-- 单门店月度产出趋势弹窗（点击表格门店名称打开） -->
    <StoreTrendDialog
      v-model="trendVisible"
      :db-key="dbKey"
      :store-name="trendStore"
      :date-from="dateRange?.[0] || ''"
      :date-to="dateRange?.[1] || ''"
      :products="products"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, RefreshRight, InfoFilled, DataAnalysis, Download } from '@element-plus/icons-vue'
import CategoryCell from './CategoryCell.vue'
import StoreTrendDialog from './StoreTrendDialog.vue'
import {
  fetchStoreAbility,
  exportStoreAbility,
  fetchStoreAbilityLatestRange,
  downloadBlob,
  type StoreAbilityResponse,
  type StoreAbilityStore,
  type StoreAbilityCategory,
  type StoreAbilityLatestRange,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'

const props = defineProps<{
  dbKey: string
}>()

const { start: defaultStart, end: defaultEnd } = getCurrentMonthRange()

/** 门店能力分析当前固定城市（后端同步下发，此处仅用于展示） */
const city = ref('广州')

/** 数据最新月份区间；拉取成功后接管默认时间范围，失败则用系统当月兜底 */
const latest = ref<StoreAbilityLatestRange | null>(null)

/** 当前应使用的默认区间：优先「数据最新月份」，其次系统当月 */
function defaultRange(): [string, string] {
  if (latest.value?.date_from && latest.value?.date_to) {
    return [latest.value.date_from, latest.value.date_to]
  }
  return [defaultStart, defaultEnd]
}

const dateRange = ref<[string, string]>(defaultRange())
/** 门店名称关键词（英文逗号分隔）；留空 = 全部门店。注意结果列表已占用 stores 名 */
const storeKeyword = ref('')
const products = ref('')
const topN = ref(100)

// 进入页面即拉取最新月份，把默认时间范围对齐到「数据最新月份」而非系统当月，
// 避免数据滞后时默认区间查出来是空的。
onMounted(async () => {
  try {
    latest.value = await fetchStoreAbilityLatestRange(props.dbKey)
    dateRange.value = defaultRange()
  } catch (e: any) {
    logger.warn('获取最新月份失败，回退到系统当月: ' + e.message, 'StoreAbilityStats')
  }
})

const loading = ref(false)
const exporting = ref(false)

// ---------- 门店月度趋势弹窗 ----------
const trendVisible = ref(false)
const trendStore = ref('')

/** 点击表格里的门店名：沿用当前页面的时间范围与品类筛选查看该店按月产出 */
function openTrend(storeName: string) {
  trendStore.value = storeName
  trendVisible.value = true
}

const summary = ref<StoreAbilityResponse['summary']>({
  store_count: 0,
  total_qty: 0,
  returned: 0,
  top_n: 100,
})
const stores = ref<StoreAbilityStore[]>([])

const isSingleDay = computed(
  () => !!dateRange.value?.[0] && dateRange.value[0] === dateRange.value?.[1],
)

const avgQty = computed(() => {
  const { store_count, total_qty } = summary.value
  if (!store_count) return 0
  return Math.round(total_qty / store_count)
})

/** 取第 idx 个最佳品类（0-based），不足则返回 null */
function catAt(row: StoreAbilityStore, idx: number): StoreAbilityCategory | null {
  return row.top_categories?.[idx] ?? null
}

function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN')
}

function rankClass(rank: number): string {
  if (rank === 1) return 'rank-1'
  if (rank === 2) return 'rank-2'
  if (rank === 3) return 'rank-3'
  return 'rank-normal'
}

async function handleQuery() {
  loading.value = true
  try {
    const res = await fetchStoreAbility(props.dbKey, {
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      stores: storeKeyword.value || undefined,
      products: products.value || undefined,
      top_n: topN.value,
    })
    summary.value = res.summary
    stores.value = res.stores
    if (res.city) city.value = res.city
    if (res.stores.length === 0) {
      ElMessage.info('查询结果为空，请调整筛选条件')
    }
  } catch (e: any) {
    ElMessage.error('统计失败: ' + e.message)
    logger.error('门店能力分析失败: ' + e.message, 'StoreAbilityStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  dateRange.value = defaultRange()
  storeKeyword.value = ''
  products.value = ''
  topN.value = 100
  stores.value = []
  summary.value = { store_count: 0, total_qty: 0, returned: 0, top_n: 100 }
}

async function handleExport() {
  if (stores.value.length === 0) {
    ElMessage.warning('请先执行统计后再导出')
    return
  }
  exporting.value = true
  try {
    const blob = await exportStoreAbility(props.dbKey, {
      date_from: dateRange.value?.[0] || undefined,
      date_to: dateRange.value?.[1] || undefined,
      stores: storeKeyword.value || undefined,
      products: products.value || undefined,
      top_n: topN.value,
    })
    const from = dateRange.value?.[0] || 'all'
    const to = dateRange.value?.[1] || 'all'
    downloadBlob(blob, `门店能力分析_${city.value}_${from}_${to}.xlsx`)
    ElMessage.success('导出完成')
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e.message)
    logger.error('门店能力分析导出失败: ' + e.message, 'StoreAbilityStats')
  } finally {
    exporting.value = false
  }
}
</script>

<style scoped>
.store-ability-stats {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 14px;
}

.filter-section {
  background: #fff;
  border-radius: 8px;
  padding: 16px 22px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  flex-shrink: 0;
}

.filter-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
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

.filter-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 8px;
  margin-left: auto;
}

.hint-bar,
.single-day-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 12px;
  padding: 8px 12px;
  background: #f0f9ff;
  border-radius: 6px;
  font-size: 12px;
  color: #3b6bd6;
  line-height: 1.5;
}

.single-day-tip {
  margin-top: 8px;
  background: #fff7ed;
  color: #b45309;
}

.results-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  gap: 14px;
}

/* ---------- 汇总卡片 ---------- */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  flex-shrink: 0;
}

.sum-card {
  background: #fff;
  border-radius: 8px;
  padding: 14px 18px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.sum-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}

.sum-value {
  font-size: 22px;
  font-weight: 700;
  color: #303133;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.sum-sub {
  margin-top: 4px;
  font-size: 11px;
  color: #c0c4cc;
}

/* ---------- 表格 ---------- */
.table-card {
  flex: 1;
  min-height: 0;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  overflow: hidden;
  padding: 4px;
}

.ability-table {
  width: 100%;
}

.qty-strong {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.store-link {
  color: #3b6bd6;
  cursor: pointer;
}

.store-link:hover {
  text-decoration: underline;
}

.rank-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 22px;
  padding: 0 6px;
  border-radius: 11px;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.rank-normal {
  background: #f4f4f5;
  color: #909399;
}

.rank-3 {
  background: #fdf6ec;
  color: #e6a23c;
}

.rank-2 {
  background: #f0f9ff;
  color: #409eff;
}

.rank-1 {
  background: #fef0f0;
  color: #f56c6c;
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
