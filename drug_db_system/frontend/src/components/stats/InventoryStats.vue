<template>
  <div class="inventory-stats">
    <div class="filter-section">
      <div class="filter-grid">
        <div class="filter-group">
          <label class="filter-label">库存截至日期</label>
          <el-select
            v-model="date"
            clearable
            placeholder="留空 = 最新日期"
            size="default"
            style="width: 200px"
          >
            <el-option
              v-for="d in latest?.dates || []"
              :key="d"
              :label="d"
              :value="d"
            />
          </el-select>
        </div>

        <div class="filter-group">
          <label class="filter-label">效期货判定（月）</label>
          <el-input-number
            v-model="expiryMonths"
            :min="0"
            :max="60"
            :controls="false"
            size="default"
            style="width: 120px"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">城市</label>
          <KeywordSelect
            v-model="cities"
            :db-key="dbKey"
            source="inventory"
            field="city"
            placeholder="输入城市关键词"
            width="200px"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">省份</label>
          <KeywordSelect
            v-model="provinces"
            :db-key="dbKey"
            source="inventory"
            field="province"
            placeholder="输入省份关键词"
            width="200px"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">品类（商品编码）</label>
          <KeywordSelect
            v-model="products"
            :db-key="dbKey"
            source="inventory"
            field="product"
            placeholder="输入编码或名称关键词"
            width="280px"
          />
        </div>

        <div class="filter-group">
          <label class="filter-label">门店名称</label>
          <KeywordSelect
            v-model="stores"
            :db-key="dbKey"
            source="inventory"
            field="store"
            placeholder="输入门店名关键词"
            width="240px"
          />
        </div>

        <div class="filter-group filter-actions">
          <el-button type="primary" :loading="loading" @click="handleQuery(1)">
            <el-icon><Search /></el-icon> 查询
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
        </div>
      </div>

      <div class="hint-bar">
        <el-icon><InfoFilled /></el-icon>
        <span>
          库存为「当天快照」，每次导入都是完整最新文件、全量覆盖 ·
          效期货 = 有效期至 ≤ 截至日期 + {{ expiryMonths }} 个月 ·
          已过期 = 有效期至 &lt; 截至日期 ·
          当前库存日期 {{ latest?.date || '—' }}，共 {{ fmt(latest?.rows) }} 行
        </span>
      </div>
    </div>

    <div class="results-section" v-loading="loading" element-loading-text="查询中...">
      <template v-if="result">
        <div class="summary-cards">
          <div class="sum-card">
            <div class="sum-label">门店数</div>
            <div class="sum-value">{{ fmt(result.summary.stores) }}</div>
            <div class="sum-sub">非重复门店</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">库存数量</div>
            <div class="sum-value">{{ fmt(result.summary.qty) }}</div>
            <div class="sum-sub">合计盒数</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">品类数量</div>
            <div class="sum-value">{{ fmt(result.summary.cats) }}</div>
            <div class="sum-sub">非重复商品编码</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">效期货数量</div>
            <div class="sum-value expiry-value">{{ fmt(result.summary.expiry_qty) }}</div>
            <div class="sum-sub">≤ {{ result.expiry_threshold || '—' }}</div>
          </div>
          <div class="sum-card">
            <div class="sum-label">已过期数量</div>
            <div class="sum-value expired-value">{{ fmt(result.summary.expired_qty) }}</div>
            <div class="sum-sub">&lt; {{ result.date || '—' }}</div>
          </div>
        </div>

        <el-table :data="result.rows" border size="small" max-height="440">
          <el-table-column prop="store_code" label="门店编码" width="140" />
          <el-table-column prop="store_name" label="门店名称" min-width="200" show-overflow-tooltip />
          <el-table-column prop="city" label="城市" width="110" />
          <el-table-column prop="qty" label="库存数量" width="120" align="right" sortable />
          <el-table-column prop="cat_cnt" label="品类数量" width="110" align="right" sortable />
          <el-table-column prop="expiry_qty" label="效期货数量" width="120" align="right" sortable />
          <el-table-column prop="expired_qty" label="已过期数量" width="120" align="right" sortable />
        </el-table>

        <div class="pager">
          <el-pagination
            background
            layout="total, prev, pager, next, sizes"
            :total="result.total"
            :current-page="result.page"
            :page-size="result.page_size"
            :page-sizes="[20, 50, 100, 200]"
            @current-change="handleQuery"
            @size-change="(s: number) => handleQuery(1, s)"
          />
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state">
        <el-icon class="empty-icon"><Box /></el-icon>
        <p>点击「查询」按钮查看门店库存情况</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 库存情况查询：按门店看 库存数量 / 品类数量 / 效期货数量 / 已过期数量，
 * 按「库存截至日期」筛选；筛选输入统一支持模糊搜索 + 候选下拉（来源为库存表）。
 */
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  RefreshRight,
  InfoFilled,
  Box,
} from '@element-plus/icons-vue'
import {
  fetchInventoryQuery,
  fetchInventoryLatest,
  type InventoryLatest,
  type InventoryQueryResponse,
} from '@/api/client'
import { logger } from '@/utils/logger'
import KeywordSelect from '../KeywordSelect.vue'

const props = defineProps<{ dbKey: string; storeType?: 'all' | 'chain' | 'franchise' }>()

const date = ref('')
const expiryMonths = ref(6)
const cities = ref('')
const provinces = ref('')
const products = ref('')
const stores = ref('')

const loading = ref(false)
const latest = ref<InventoryLatest | null>(null)
const result = ref<InventoryQueryResponse | null>(null)
const pageSize = ref(20)

async function loadLatest() {
  try {
    latest.value = await fetchInventoryLatest(props.dbKey)
  } catch (e: any) {
    logger.warn('取库存概况失败: ' + e.message, 'InventoryStats')
  }
}

async function handleQuery(page = 1, size?: number) {
  if (size) pageSize.value = size
  loading.value = true
  try {
    result.value = await fetchInventoryQuery(props.dbKey, {
      date: date.value || undefined,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
      products: products.value || undefined,
      stores: stores.value || undefined,
      store_type: props.storeType || 'all',
      expiry_months: expiryMonths.value,
      page,
      page_size: pageSize.value,
    })
  } catch (e: any) {
    ElMessage.error('库存查询失败: ' + e.message)
    logger.error('库存查询失败: ' + e.message, 'InventoryStats')
  } finally {
    loading.value = false
  }
}

function handleReset() {
  date.value = ''
  expiryMonths.value = 6
  cities.value = ''
  provinces.value = ''
  products.value = ''
  stores.value = ''
  result.value = null
}

function fmt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString('zh-CN')
}

onMounted(async () => {
  await loadLatest()
})

watch(
  () => props.dbKey,
  async () => {
    result.value = null
    await loadLatest()
  },
)
watch(
  () => props.storeType,
  () => {
    if (result.value) handleQuery(1)
  },
)

/** 供父组件（动销率库存情况合并视图）在库存导入后刷新 */
defineExpose({
  refresh: async () => {
    await loadLatest()
    await handleQuery(1)
  },
})
</script>

<style scoped>
.inventory-stats {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}
.filter-section {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px 16px;
  background: #fafbfc;
}
.filter-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 14px 20px;
}
.filter-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.filter-label {
  font-size: 12px;
  color: #909399;
}
.filter-actions {
  flex-direction: row;
  align-items: flex-end;
  gap: 8px;
}
.hint-bar {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.results-section {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.summary-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.sum-card {
  flex: 1;
  min-width: 150px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px 14px;
  background: #fff;
}
.sum-label {
  font-size: 12px;
  color: #909399;
}
.sum-value {
  font-size: 22px;
  font-weight: 700;
  margin: 6px 0 4px;
}
.sum-sub {
  font-size: 12px;
  color: #c0c4cc;
}
.expiry-value {
  color: #e6a23c;
}
.expired-value {
  color: #f56c6c;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.empty-state {
  text-align: center;
  color: #c0c4cc;
  padding: 48px 0;
}
.empty-icon {
  font-size: 40px;
}
</style>
