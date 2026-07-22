<template>
  <div class="dashenlin-page">
    <!-- 公司标签 -->
    <div class="company-tabs">
      <div
        v-for="db in dbs"
        :key="db.key"
        class="tab"
        :class="{ active: db.key === activeDb }"
        @click="switchDb(db.key)"
      >
        <span class="tab-label">{{ db.label }}</span>
        <span class="tab-badge">{{ db.row_count }}</span>
      </div>
    </div>

    <!-- 操作栏：按钮 -->
    <div class="toolbar card">
      <div class="actions">
        <el-button type="success" :icon="Upload" @click="openImportDialog">导入Excel</el-button>
        <el-button :icon="Download" @click="handleExport" :loading="exporting">导出查询结果</el-button>
        <el-button type="warning" :icon="DataLine" @click="statsVisible = true">统计</el-button>
      </div>
      <div class="stats">共 {{ total }} 条 · 第 {{ page }}/{{ totalPages }} 页</div>
    </div>

    <!-- 筛选面板 -->
    <div class="filter-panel card">
      <div class="filter-row">
        <div class="filter-item">
          <label>日期范围</label>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            size="default"
            style="width: 240px"
          />
        </div>
        <div class="filter-item">
          <label>商品编码</label>
          <el-input
            v-model="productCodes"
            placeholder="例: 1058746,1086127"
            clearable
            size="default"
            style="width: 200px"
            @keyup.enter="handleQuery"
            @clear="handleQuery"
          />
        </div>
        <div class="filter-item">
          <label>城市</label>
          <el-input
            v-model="cities"
            placeholder="例: 广州,深圳"
            clearable
            size="default"
            style="width: 160px"
            @keyup.enter="handleQuery"
            @clear="handleQuery"
          />
        </div>
        <div class="filter-item">
          <label>省份</label>
          <el-input
            v-model="provinces"
            placeholder="例: 广东,广西"
            clearable
            size="default"
            style="width: 160px"
            @keyup.enter="handleQuery"
            @clear="handleQuery"
          />
        </div>
        <div class="filter-actions">
          <el-button type="primary" size="default" @click="handleQuery">
            <el-icon><Search /></el-icon> 查询
          </el-button>
          <el-button size="default" @click="resetFilters">
            <el-icon><RefreshRight /></el-icon> 重置
          </el-button>
        </div>
      </div>
    </div>

    <!-- 数据表格 -->
    <div class="card table-card">
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        @sort-change="onSortChange"
      />
    </div>

    <!-- 分页 -->
    <div class="pagination-bar card">
      <div class="page-size">
        <span>每页</span>
        <el-select v-model="pageSize" size="small" @change="handleSizeChange">
          <el-option label="20" :value="20" />
          <el-option label="50" :value="50" />
          <el-option label="100" :value="100" />
        </el-select>
        <span>条</span>
      </div>
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next"
        background
        @current-change="handlePageChange"
      />
    </div>

    <!-- 导入 Excel 弹窗 -->
    <el-dialog
      v-model="importDialogVisible"
      title="导入 Excel 数据"
      width="520px"
      destroy-on-close
    >
      <div class="import-body">
        <el-upload
          ref="uploadRef"
          class="upload-area"
          drag
          :auto-upload="false"
          :limit="1"
          accept=".xlsx,.xls"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="upload-text">
            <p class="upload-title">将 Excel 文件拖到此处，或<em>点击上传</em></p>
            <p class="upload-hint">支持 .xlsx / .xls 格式</p>
          </div>
        </el-upload>

        <div class="overwrite-notice">
          <el-icon><InfoFilled /></el-icon>
          <span>导入数据将自动按日期覆盖同日期旧数据，无需额外设置。</span>
        </div>

        <div v-if="importResult" class="import-result">
          <div class="result-line"><strong>Excel 行数：</strong>{{ importResult.excel_rows }}</div>
          <div class="result-line"><strong>覆盖日期：</strong>{{ importResult.dates.join('、') }}</div>
          <div class="result-line"><strong>删除旧记录：</strong>{{ importResult.deleted }} 条</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="importDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">
          开始导入
        </el-button>
      </template>
    </el-dialog>

    <!-- 统计面板 -->
    <StatisticsPanel v-model="statsVisible" :db-key="dbKey" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Upload, Download, RefreshRight, UploadFilled, InfoFilled, DataLine } from '@element-plus/icons-vue'
import DataTable from '@/components/DataTable.vue'
import StatisticsPanel from '@/components/StatisticsPanel.vue'
import {
  fetchDbs,
  fetchRows,
  type DbMeta,
  type RowResponse,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'

const route = useRoute()
const router = useRouter()

const dbKey = 'dashenlin'
const activeDb = ref(dbKey)
const API_BASE = import.meta.env.VITE_API_BASE || ''

const dbs = ref<DbMeta[]>([])
const columns = ref<{ name: string; type: string }[]>([])
const rows = ref<Record<string, any>[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const sortBy = ref('')
const sortDir = ref<'asc' | 'desc'>('asc')

// 筛选字段
const { start: defaultStart, end: defaultEnd } = getCurrentMonthRange()
const dateRange = ref<[string, string]>([defaultStart, defaultEnd])
const productCodes = ref('')
const cities = ref('')
const provinces = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

// 导入
const importDialogVisible = ref(false)
const importing = ref(false)
const exporting = ref(false)
const uploadFile = ref<File | null>(null)
const uploadRef = ref<any>(null)
const importResult = ref<any>(null)

// 统计面板
const statsVisible = ref(false)

// ---------- 数据加载 ----------

async function loadDbs() {
  try {
    dbs.value = await fetchDbs()
    const meta = dbs.value.find((d) => d.key === dbKey)
    if (meta) columns.value = meta.columns
  } catch (e: any) {
    logger.error('加载库元数据失败: ' + e.message, 'DashenlinView')
  }
}

async function loadRows() {
  loading.value = true
  try {
    const date_from = dateRange.value?.[0] || ''
    const date_to = dateRange.value?.[1] || ''
    const res: RowResponse = await fetchRows(dbKey, {
      page: page.value,
      page_size: pageSize.value,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
      date_from: date_from || undefined,
      date_to: date_to || undefined,
      product_codes: productCodes.value || undefined,
      cities: cities.value || undefined,
      provinces: provinces.value || undefined,
    })
    rows.value = res.rows
    total.value = res.total
  } catch (e: any) {
    ElMessage.error('加载数据失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

// ---------- 事件处理 ----------

function handleQuery() {
  page.value = 1
  loadRows()
}

function resetFilters() {
  dateRange.value = [defaultStart, defaultEnd]
  productCodes.value = ''
  cities.value = ''
  provinces.value = ''
  page.value = 1
  loadRows()
}

function handlePageChange(p: number) {
  page.value = p
  loadRows()
}

function handleSizeChange(size: number) {
  pageSize.value = size
  page.value = 1
  loadRows()
}

function onSortChange({ prop, order }: { prop: string; order: 'ascending' | 'descending' | null }) {
  sortBy.value = prop
  sortDir.value = order === 'descending' ? 'desc' : 'asc'
  loadRows()
}

function switchDb(key: string) {
  if (key === dbKey) return
  router.push(`/${key}`)
}

// ---------- 导出 ----------

async function handleExport() {
  exporting.value = true
  try {
    // 构建查询参数
    const params = new URLSearchParams()
    const date_from = dateRange.value?.[0]
    const date_to = dateRange.value?.[1]
    if (date_from) params.set('date_from', date_from)
    if (date_to) params.set('date_to', date_to)
    if (productCodes.value) params.set('product_codes', productCodes.value)
    if (cities.value) params.set('cities', cities.value)
    if (provinces.value) params.set('provinces', provinces.value)
    if (sortBy.value) params.set('sort_by', sortBy.value)
    if (sortDir.value) params.set('sort_dir', sortDir.value)

    const url = `${API_BASE}/api/${dbKey}/export/excel?${params.toString()}`
    const response = await fetch(url)
    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || '导出失败')
    }
    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = downloadUrl
    a.download = `大参林_查询结果.xlsx`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(downloadUrl)
    ElMessage.success('导出完成')
  } catch (e: any) {
    ElMessage.error('导出失败: ' + e.message)
  } finally {
    exporting.value = false
  }
}

// ---------- 导入 ----------

function openImportDialog() {
  importResult.value = null
  importDialogVisible.value = true
}

function handleFileChange(file: any) {
  uploadFile.value = file.raw || null
}

function handleFileRemove() {
  uploadFile.value = null
}

async function submitImport() {
  if (!uploadFile.value) {
    ElMessage.warning('请先选择 Excel 文件')
    return
  }
  importing.value = true
  importResult.value = null
  try {
    const formData = new FormData()
    formData.append('file', uploadFile.value)
    const url = `${API_BASE}/api/${dbKey}/rows/import_excel`
    const res = await fetch(url, { method: 'POST', body: formData })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || '导入失败')
    }
    const data = await res.json()
    importResult.value = data
    ElMessage.success(data.message || '导入完成')
    uploadRef.value?.clearFiles()
    uploadFile.value = null
    // 刷新数据
    page.value = 1
    loadRows()
  } catch (e: any) {
    ElMessage.error('导入失败: ' + e.message)
  } finally {
    importing.value = false
  }
}

// ---------- 初始化 ----------

onMounted(() => {
  loadDbs().then(() => loadRows())
})
</script>

<style scoped>
.dashenlin-page {
  max-width: 1600px;
  margin: 0 auto;
}

.card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  padding: 16px 20px;
}

.company-tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 18px;
  background: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #606266;
  transition: all 0.2s;
}

.tab:hover {
  color: #3b6bd6;
  border-color: #a0c1ff;
}

.tab.active {
  color: #fff;
  background: #3b6bd6;
  border-color: #3b6bd6;
}

.tab.active .tab-badge {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: 11px;
  font-size: 12px;
  background: #ebeef5;
  color: #909399;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.actions {
  display: flex;
  gap: 10px;
}

.stats {
  margin-left: auto;
  font-size: 13px;
  color: #909399;
  white-space: nowrap;
}

/* 筛选面板 */
.filter-panel {
  margin-bottom: 16px;
  padding: 14px 20px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.filter-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.filter-item label {
  white-space: nowrap;
  font-weight: 500;
}

.filter-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.table-card {
  padding: 0;
  overflow: hidden;
  margin-bottom: 16px;
}

.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.page-size {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}

.page-size :deep(.el-select) {
  width: 70px;
}

/* 导入弹窗 */
.import-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.upload-area {
  width: 100%;
}

.upload-icon {
  font-size: 40px;
  color: #3b6bd6;
  margin-bottom: 8px;
}

.upload-text {
  text-align: center;
}

.upload-title {
  font-size: 14px;
  color: #606266;
  margin: 0 0 4px;
}

.upload-title em {
  color: #3b6bd6;
  font-style: normal;
  cursor: pointer;
}

.upload-hint {
  font-size: 12px;
  color: #909399;
  margin: 0;
}

.overwrite-notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 16px;
  background: #f0f9ff;
  border: 1px solid #b3d8ff;
  border-radius: 6px;
  font-size: 13px;
  color: #3b6bd6;
  line-height: 1.5;
}

.overwrite-notice .el-icon {
  margin-top: 2px;
  flex-shrink: 0;
}

.import-result {
  padding: 12px 16px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  font-size: 13px;
  color: #166534;
}

.result-line {
  line-height: 1.6;
}

:deep(.el-input__wrapper) {
  border-radius: 6px;
}
</style>
