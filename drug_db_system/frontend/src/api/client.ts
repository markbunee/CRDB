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
  supports_store_ability?: boolean
  store_ability_city?: string
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
    /** 品类编码映射为中文名（无映射保持编码） */
    map_names?: boolean
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
    /** 品类编码映射为中文名（无映射保持编码） */
    map_names?: boolean
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
    map_names?: boolean
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
    map_names?: boolean
  } = {},
) {
  return _fetchBlob(`/api/${dbKey}/stats/box_count/export`, options)
}

// ---------- 门店能力分析（大参林 · 广州） ----------

/** 单个品类在门店内的表现：盒数 + 占该门店实销总数的比例 */
export interface StoreAbilityCategory {
  product_code: string | number | null
  product_name: string | null
  qty: number
  /** 占该门店实销总数的比例，0~1；门店总盒数为 0 时为 null */
  share: number | null
}

export interface StoreAbilityStore {
  rank: number
  store_name: string
  /** 该门店实销总盒数 */
  qty: number
  /** 该门店有销量的品类数 */
  cat_cnt: number
  /** 销量前 3 的品类，不足 3 个时数组变短 */
  top_categories: StoreAbilityCategory[]
  /** 末位品类（倒数第一）；品类数 <= 3 时会与 top_categories 中的某一项相同 */
  last_category: StoreAbilityCategory | null
}

export interface StoreAbilityResponse {
  db_key: string
  city: string
  date_from: string | null
  date_to: string | null
  summary: {
    /** 广州范围内动销门店总数 */
    store_count: number
    /** 广州范围内实销总盒数 */
    total_qty: number
    /** 实际返回的门店数（<= top_n） */
    returned: number
    top_n: number
  }
  stores: StoreAbilityStore[]
}

/** 广州范围内「数据最新月份」的区间，用于默认填充时间范围 */
export interface StoreAbilityLatestRange {
  date_from: string | null
  date_to: string | null
  max_date: string | null
}

export function fetchStoreAbilityLatestRange(dbKey: string) {
  return request<StoreAbilityLatestRange>(
    `/api/${dbKey}/stats/store_ability/latest_range`,
  )
}

/** 单门店按天产出趋势：一天一个点 */
export interface StoreAbilityTrendPoint {
  /** YYYY-MM-DD */
  date: string
  /** 当日实销盒数（无销量补 0） */
  qty: number
}

export interface StoreAbilityTrendResponse {
  db_key: string
  store_name: string
  date_from: string
  date_to: string
  days: StoreAbilityTrendPoint[]
}

export function fetchStoreAbilityTrend(
  dbKey: string,
  options: {
    store_name: string
    date_from: string
    date_to: string
    /** 品类（商品编码），英文逗号分隔；留空 = 全部品类（与主表口径一致） */
    products?: string
  },
) {
  return request<StoreAbilityTrendResponse>(
    `/api/${dbKey}/stats/store_ability/store_trend`,
    options,
  )
}

export function fetchStoreAbility(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    /** 品类（商品编码），英文逗号分隔；留空 = 全部品类 */
    products?: string
    /** 门店名称关键词，英文逗号分隔，模糊匹配；留空 = 全部门店 */
    stores?: string
    top_n?: number
    /** 品类显示映射表中文名（无映射回落商品名称） */
    map_names?: boolean
  } = {},
) {
  return request<StoreAbilityResponse>(`/api/${dbKey}/stats/store_ability`, options)
}

export function exportStoreAbility(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    products?: string
    stores?: string
    top_n?: number
    map_names?: boolean
  } = {},
) {
  return _fetchBlob(`/api/${dbKey}/stats/store_ability/export`, options)
}

// ---------- 商品编码 → 品类中文名 映射管理 ----------

export interface ProductMapItem {
  code: string
  name: string
}

export interface ProductMapResponse {
  db_key: string
  count: number
  items: ProductMapItem[]
}

export function fetchProductMap(dbKey: string) {
  return request<ProductMapResponse>(`/api/product-map/${dbKey}`)
}

/** 全量保存映射（后端整体替换该库的映射表） */
export function saveProductMap(dbKey: string, items: ProductMapItem[]) {
  return request<ProductMapResponse>(`/api/product-map/${dbKey}`, { items }, 'POST')
}

export function deleteProductCode(dbKey: string, code: string) {
  return request<{ db_key: string; deleted: string }>(
    `/api/product-map/${dbKey}/${encodeURIComponent(code)}`,
    undefined,
    'DELETE',
  )
}
