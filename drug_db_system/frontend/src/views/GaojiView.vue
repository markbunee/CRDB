<template>
  <div class="gaoji-page">
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

    <!-- 操作栏 -->
    <div class="toolbar card">
      <div class="actions">
        <el-button type="success" :icon="Upload" @click="openImportDialog">导入Excel</el-button>
        <el-button :icon="Download" @click="handleExport" :loading="exporting">导出查询结果</el-button>
        <el-button type="warning" :icon="DataLine" @click="statsVisible = true">统计</el-button>
        <el-dropdown trigger="click" @command="onSettingCommand">
          <el-button :icon="Setting">设置</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="clear" style="color: #f56c6c;">
                <el-icon><Delete /></el-icon> 清空数据库数据
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
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
            placeholder="例: 1347215,1347216"
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
            placeholder="例: 潮州,广州"
            clearable
            size="default"
            style="width: 180px"
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
      width="640px"
      destroy-on-close
    >
      <div class="import-body">
        <el-upload
          ref="uploadRef"
          class="upload-area"
          drag
          multiple
          :auto-upload="false"
          accept=".xlsx,.xls"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
        >
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="upload-text">
            <p class="upload-title">将 Excel 文件拖到此处，或<em>点击上传</em></p>
            <p class="upload-hint">支持 .xlsx / .xls 格式，可一次选择多个文件，后台会排队依次处理</p>
          </div>
        </el-upload>

        <div class="overwrite-control">
          <span class="overwrite-label">覆盖相同日期旧数据</span>
          <el-switch v-model="overwriteExisting" />
        </div>

        <div v-if="overwriteExisting" class="overwrite-warning">
          <el-icon><WarningFilled /></el-icon>
          <span>
            <strong>注意：</strong>开启覆盖后，若 Excel 中的日期与数据库中已存在的数据来源不同，
            同日期旧数据仍会被删除并替换为当前 Excel 内容。多个文件包含相同日期时，后处理的文件会覆盖先处理的文件。
            建议确认数据来源一致后再开启覆盖。
          </span>
        </div>
        <div v-else class="overwrite-notice">
          <el-icon><InfoFilled /></el-icon>
          <span>未开启覆盖：新数据将直接追加写入，已存在的同日期旧数据不会被替换。</span>
        </div>

        <div v-if="importResult" class="import-result">
          <el-alert
            :title="importResult.message"
            :type="importResult.success ? 'success' : 'warning'"
            :closable="false"
            show-icon
          />
          <div v-if="importResult.file_results?.length" class="file-result-list">
            <div
              v-for="(item, idx) in importResult.file_results"
              :key="idx"
              class="file-result-item"
            >
              <el-icon :size="14" :color="item.success ? '#67c23a' : '#f56c6c'">
                <CircleCheck v-if="item.success" />
                <CircleClose v-else />
              </el-icon>
              <span class="file-name" :title="item.filename">{{ item.filename }}</span>
              <span class="file-summary">
                {{ item.success
                  ? `写入 ${item.inserted} 条 / 删除 ${item.deleted} 条`
                  : `失败：${item.error}`
                }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="importDialogVisible = false">关闭</el-button>
        <el-button type="primary" :loading="importing" @click="submitImport">
          开始导入（{{ uploadFiles.length }} 个文件）
        </el-button>
      </template>
    </el-dialog>

    <!-- 统计面板 -->
    <StatisticsPanel v-model="statsVisible" :db-key="dbKey" />

    <!-- 清空数据确认弹窗 -->
    <el-dialog
      v-model="clearDialogVisible"
      title="删除数据库数据"
      width="520px"
      :close-on-click-modal="false"
      @closed="closeClearDialog"
    >
      <div class="clear-warning">
        <el-icon class="warning-icon"><WarningFilled /></el-icon>
        <div class="warning-text">
          <p><strong>危险操作：请选择删除范围。</strong></p>
          <p>此操作不可恢复，请确保已经备份需要保留的数据。</p>
        </div>
      </div>

      <div class="clear-mode">
        <el-radio-group v-model="clearMode">
          <el-radio label="month_range">按月份范围删除</el-radio>
          <el-radio label="all">清空全部数据</el-radio>
        </el-radio-group>
      </div>

      <div v-if="clearMode === 'month_range'" class="clear-month-range">
        <span class="range-label">选择月份范围：</span>
        <el-date-picker
          v-model="clearMonthRange"
          type="monthrange"
          value-format="YYYY-MM-DD"
          format="YYYY-MM"
          start-placeholder="开始月份"
          end-placeholder="结束月份"
          style="width: 260px"
        />
        <p class="range-hint">将删除「月度」列落在该范围内的所有数据</p>
      </div>

      <p class="countdown-text">
        {{ countdown > 0 ? `请仔细阅读以上提示，${countdown} 秒后可确认。` : '倒计时结束，请点击下方按钮确认。' }}
      </p>
      <template #footer>
        <el-button @click="closeClearDialog">取消</el-button>
        <el-button
          type="danger"
          :disabled="countdown > 0 || (clearMode === 'month_range' && !clearMonthRange)"
          :loading="clearing"
          @click="confirmClear"
        >
          {{ countdown > 0 ? `确认删除（${countdown}）` : '确认删除' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Upload, Download, RefreshRight, UploadFilled, InfoFilled, WarningFilled, DataLine, Setting, Delete, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import DataTable from '@/components/DataTable.vue'
import StatisticsPanel from '@/components/StatisticsPanel.vue'
import {
  fetchDbs,
  fetchRows,
  clearData,
  clearDataByMonthRange,
  importExcelBatch,
  type DbMeta,
  type RowResponse,
  type ImportExcelBatchResponse,
} from '@/api/client'
import { getCurrentMonthRange } from '@/utils/date'
import { logger } from '@/utils/logger'

const router = useRouter()

const dbKey = 'gaoji'
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

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

// 导入
const importDialogVisible = ref(false)
const importing = ref(false)
const exporting = ref(false)
const uploadFiles = ref<File[]>([])
const uploadRef = ref<any>(null)
const importResult = ref<ImportExcelBatchResponse | null>(null)
const overwriteExisting = ref(true)

// 统计面板
const statsVisible = ref(false)

// 清空数据
const clearDialogVisible = ref(false)
const clearing = ref(false)
const countdown = ref(10)
const clearMode = ref<'month_range' | 'all'>('month_range')
const clearMonthRange = ref<[string, string] | null>(null)
let countdownTimer: ReturnType<typeof setInterval> | null = null

// ---------- 数据加载 ----------

async function loadDbs() {
  try {
    dbs.value = await fetchDbs()
    const meta = dbs.value.find((d) => d.key === dbKey)
    if (meta) columns.value = meta.columns
  } catch (e: any) {
    logger.error('加载库元数据失败: ' + e.message, 'GaojiView')
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
    const params = new URLSearchParams()
    const date_from = dateRange.value?.[0]
    const date_to = dateRange.value?.[1]
    if (date_from) params.set('date_from', date_from)
    if (date_to) params.set('date_to', date_to)
    if (productCodes.value) params.set('product_codes', productCodes.value)
    if (cities.value) params.set('cities', cities.value)
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
    a.download = `高济_查询结果.xlsx`
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
  overwriteExisting.value = true
  uploadFiles.value = []
  importDialogVisible.value = true
}

function handleFileChange(file: any) {
  const raw = file.raw
  if (!raw) return
  if (!uploadFiles.value.some((f) => f.name === raw.name && f.size === raw.size)) {
    uploadFiles.value.push(raw)
  }
}

function handleFileRemove(file: any) {
  const raw = file.raw
  if (!raw) return
  uploadFiles.value = uploadFiles.value.filter(
    (f) => !(f.name === raw.name && f.size === raw.size),
  )
}

async function submitImport() {
  if (uploadFiles.value.length === 0) {
    ElMessage.warning('请先选择 Excel 文件')
    return
  }
  importing.value = true
  importResult.value = null
  try {
    const data = await importExcelBatch(dbKey, uploadFiles.value, overwriteExisting.value)
    importResult.value = data
    if (data.success) {
      ElMessage.success(data.message || '批量导入完成')
    } else {
      ElMessage.warning(data.message || '部分文件导入失败')
    }
    uploadRef.value?.clearFiles()
    uploadFiles.value = []
    page.value = 1
    loadRows()
    loadDbs()
  } catch (e: any) {
    ElMessage.error('导入失败: ' + e.message)
  } finally {
    importing.value = false
  }
}

// ---------- 设置 / 清空数据 ----------

function onSettingCommand(command: string) {
  if (command === 'clear') {
    openClearDialog()
  }
}

function openClearDialog() {
  clearDialogVisible.value = true
  clearMode.value = 'month_range'
  clearMonthRange.value = null
  countdown.value = 10
  if (countdownTimer) clearInterval(countdownTimer)
  countdownTimer = setInterval(() => {
    if (countdown.value > 0) {
      countdown.value--
    } else if (countdownTimer) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }, 1000)
}

function closeClearDialog() {
  clearDialogVisible.value = false
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

async function confirmClear() {
  if (countdown.value > 0) return
  clearing.value = true
  try {
    if (clearMode.value === 'month_range') {
      if (!clearMonthRange.value || !clearMonthRange.value[0] || !clearMonthRange.value[1]) {
        ElMessage.warning('请选择月份范围')
        return
      }
      const [monthFrom, monthTo] = clearMonthRange.value
      const res = await clearDataByMonthRange(dbKey, monthFrom, monthTo)
      ElMessage.success(res.message || `已删除 ${monthFrom} 至 ${monthTo} 的数据`)
    } else {
      await clearData(dbKey)
      ElMessage.success('数据库已清空')
    }
    closeClearDialog()
    page.value = 1
    loadRows()
    loadDbs()
  } catch (e: any) {
    ElMessage.error('删除失败: ' + e.message)
  } finally {
    clearing.value = false
  }
}

// ---------- 初始化 ----------

onMounted(() => {
  loadDbs().then(() => loadRows())
})
</script>

<style scoped>
.gaoji-page {
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

.overwrite-control {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 14px;
  color: #303133;
}

.overwrite-label {
  font-weight: 500;
}

.overwrite-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 16px;
  background: #fff7ed;
  border: 1px solid #f5c7a3;
  border-radius: 6px;
  font-size: 13px;
  color: #b45309;
  line-height: 1.5;
}

.overwrite-warning .el-icon {
  margin-top: 2px;
  flex-shrink: 0;
  color: #f59e0b;
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

::deep(.el-input__wrapper) {
  border-radius: 6px;
}

/* 清空数据弹窗 */
.clear-warning {
  display: flex;
  gap: 12px;
  padding: 12px 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
}

.clear-warning .warning-icon {
  font-size: 24px;
  color: #ef4444;
  flex-shrink: 0;
  margin-top: 2px;
}

.clear-warning .warning-text {
  font-size: 14px;
  color: #7f1d1d;
  line-height: 1.6;
}

.clear-warning .warning-text p {
  margin: 0 0 6px;
}

.clear-warning .warning-text p:last-child {
  margin-bottom: 0;
}

.countdown-text {
  margin-top: 12px;
  color: #dc2626;
  font-weight: 500;
  font-size: 13px;
}

.file-result-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #ffffff;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.4;
}

.file-result-item .file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #374151;
}

.file-result-item .file-summary {
  color: #6b7280;
  white-space: nowrap;
}

.clear-mode {
  margin-top: 16px;
}

.clear-month-range {
  margin-top: 12px;
  padding: 12px 16px;
  background: #f9fafb;
  border-radius: 6px;
}

.clear-month-range .range-label {
  font-size: 14px;
  color: #374151;
  margin-right: 8px;
}

.clear-month-range .range-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: #6b7280;
}
</style>
