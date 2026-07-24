const API_BASE = import.meta.env.VITE_API_BASE || ''
import { logger } from '@/utils/logger'

async function request<T>(
  path: string,
  params?: Record<string, any>,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin)
  const init: RequestInit = { method }

  if (method === 'GET' && params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      url.searchParams.set(key, String(value))
    })
  } else if (method !== 'GET' && params) {
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(params)
  }

  const started = performance.now()
  try {
    const res = await fetch(url.toString(), init)
    if (!res.ok) {
      const text = await res.text().catch(() => '请求失败')
      logger.warn(`API ${res.status} ${path}: ${text}`, 'request')
      throw new Error(text)
    }
    const cost = (performance.now() - started).toFixed(0)
    logger.info(`API ${path} -> ${res.status} (${cost}ms)`, 'request')
    return res.json() as Promise<T>
  } catch (e: any) {
    logger.error(`API 请求失败 ${path}: ${e?.message || e}`, 'request')
    throw e
  }
}

export interface RowResponse {
  total: number
  page: number
  page_size: number
  rows: Record<string, any>[]
  next_cursor?: number
}

export interface ClickStatsResponse {
  total: number
  date_range: { min: string | null; max: string | null }
  daily: { day: string; count: number }[]
}

export interface DbMeta {
  key: string
  label: string
  columns: { name: string; type: string }[]
  date_columns: string[]
  row_count: number
  field_map: Record<string, string>
  filter_map: Record<string, string>
  region_levels: string[]
  region_label: string
  supports_stats: boolean
}

export function fetchDbs() {
  return request<DbMeta[]>('/api/dbs')
}

export function fetchRows(
  dbKey: string,
  options: {
    page?: number
    page_size?: number
    search?: string
    sort_by?: string
    sort_dir?: string
    date_from?: string
    date_to?: string
    product_codes?: string
    cities?: string
    provinces?: string
  } = {},
) {
  return request<RowResponse>(`/api/${dbKey}/rows`, options)
}

export function createRow(dbKey: string, body: Record<string, any>) {
  return request<{ id: number; message: string }>(`/api/${dbKey}/rows`, body, 'POST')
}

export function upsertByDate(
  dbKey: string,
  date: string,
  data: Record<string, any>,
) {
  return request<{ id: number; deleted: number; message: string }>(
    `/api/${dbKey}/rows/upsert_by_date`,
    { date, data },
    'POST',
  )
}

export function clearData(dbKey: string) {
  return request<{ message: string; db_key: string }>(
    `/api/${dbKey}/rows/clear`,
    { confirm: true },
    'POST',
  )
}

export function clearDataByMonthRange(
  dbKey: string,
  monthFrom: string,
  monthTo: string,
) {
  return request<{ message: string; db_key: string; deleted: number }>(
    `/api/${dbKey}/rows/clear`,
    { month_from: monthFrom, month_to: monthTo },
    'POST',
  )
}

export interface ImportExcelResult {
  filename: string
  success: boolean
  inserted: number
  deleted: number
  dates: string[]
  excel_rows: number
  overwrite_existing: boolean
  message: string
  error?: string | null
}

export interface ImportExcelBatchResponse {
  success: boolean
  total_files: number
  success_count: number
  failed_count: number
  total_inserted: number
  total_deleted: number
  overwrite_existing: boolean
  message: string
  file_results: ImportExcelResult[]
}

export function importExcelBatch(
  dbKey: string,
  files: File[],
  overwriteExisting = true,
): Promise<ImportExcelBatchResponse> {
  const formData = new FormData()
  files.forEach((file) => formData.append('files', file))
  formData.append(
    'overwrite_existing',
    overwriteExisting ? 'true' : 'false',
  )
  const url = `${API_BASE}/api/${dbKey}/rows/import_excel_batch`
  return fetch(url, { method: 'POST', body: formData }).then(async (res) => {
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      throw new Error(data.detail || data.message || '批量导入失败')
    }
    return data as ImportExcelBatchResponse
  })
}

export function fetchClickStats(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    product_codes?: string
    cities?: string
    provinces?: string
  } = {},
) {
  return request<ClickStatsResponse>(`/api/${dbKey}/stats/click`, options)
}

// ---------- 门店数统计（通用三维度） ----------

export type StoreCountDimension = 'city' | 'product' | 'time'
export type StoreCountRegion = 'city' | 'province'

export interface StoreCountCellRow {
  row_key: string
  cells: Record<string, number>
}

export interface StoreCountTable {
  title: string
  split_value: string | null
  row_header: string
  col_header: string
  row_keys: string[]
  col_keys: string[]
  rows: StoreCountCellRow[]
}

export interface StoreCountResponse {
  tables: StoreCountTable[]
  dimension: StoreCountDimension
  region_level: StoreCountRegion
  merge_months: boolean
  merge_cities: boolean
  merge_products: boolean
  total_raw_rows: number
}

export function fetchStoreCount(
  dbKey: string,
  options: {
    dimension?: StoreCountDimension
    region_level?: StoreCountRegion
    date_from?: string
    date_to?: string
    merge_months?: boolean
    cities?: string
    provinces?: string
    products?: string
    merge_cities?: boolean
    merge_products?: boolean
  } = {},
) {
  return request<StoreCountResponse>(`/api/${dbKey}/stats/store_count`, options)
}

// ---------- 实销盒数统计（通用三维度） ----------

export interface BoxCountCellRow {
  row_key: string
  cells: Record<string, number>
  total?: number
  yoy_total?: number
  yoy_pct?: number | null
  mom_total?: number
  mom_pct?: number | null
}

export interface BoxCountTable {
  title: string
  split_value: string | null
  row_header: string
  col_header: string
  row_keys: string[]
  col_keys: string[]
  rows: BoxCountCellRow[]
}

export interface BoxCountResponse {
  tables: BoxCountTable[]
  dimension: StoreCountDimension
  region_level: StoreCountRegion
  merge_months: boolean
  merge_cities: boolean
  merge_products: boolean
  total_raw_rows: number
  calc_yoy_mom?: boolean
  yoy_range?: { date_from: string; date_to: string }
  mom_range?: { date_from: string; date_to: string }
}

export function fetchBoxCount(
  dbKey: string,
  options: {
    dimension?: StoreCountDimension
    region_level?: StoreCountRegion
    date_from?: string
    date_to?: string
    merge_months?: boolean
    cities?: string
    provinces?: string
    products?: string
    merge_cities?: boolean
    merge_products?: boolean
    calc_yoy_mom?: boolean
  } = {},
) {
  return request<BoxCountResponse>(`/api/${dbKey}/stats/box_count`, options)
}

// ---------- 统计结果导出（xlsx） ----------

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

async function _fetchBlob(path: string, params?: Record<string, any>): Promise<Blob> {
  const url = new URL(API_BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString())
  if (!res.ok) {
    const text = await res.text().catch(() => '导出失败')
    logger.warn(`API ${res.status} ${path}: ${text}`, 'request')
    throw new Error(text)
  }
  return res.blob()
}

export function exportStoreCount(
  dbKey: string,
  options: {
    dimension?: StoreCountDimension
    region_level?: StoreCountRegion
    date_from?: string
    date_to?: string
    merge_months?: boolean
    cities?: string
    provinces?: string
    products?: string
    merge_cities?: boolean
    merge_products?: boolean
  } = {},
) {
  return _fetchBlob(`/api/${dbKey}/stats/store_count/export`, options)
}

export function exportBoxCount(
  dbKey: string,
  options: {
    dimension?: StoreCountDimension
    region_level?: StoreCountRegion
    date_from?: string
    date_to?: string
    merge_months?: boolean
    cities?: string
    provinces?: string
    products?: string
    merge_cities?: boolean
    merge_products?: boolean
    calc_yoy_mom?: boolean
  } = {},
) {
  return _fetchBlob(`/api/${dbKey}/stats/box_count/export`, options)
}
