const API_BASE = import.meta.env.VITE_API_BASE || ''
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'
import { clearSession, getToken, isLoggedIn } from '@/utils/auth'

/**
 * 部署开关：打包时传入 VITE_DISABLE_DOWNLOAD=true 即关闭所有「导出/下载」。
 * 按钮保留，点击直接返回、不做任何事（由各调用方 `if (DOWNLOAD_DISABLED) return` 控制）。
 */
export const DOWNLOAD_DISABLED = import.meta.env.VITE_DISABLE_DOWNLOAD === 'true'

/** 统一处理鉴权相关错误码：401 踢回登录页，403 弹「无权限」提示 */
function handleAuthError(status: number, text: string) {
  let detail = text
  try {
    const parsed = JSON.parse(text)
    if (parsed?.detail) detail = parsed.detail
  } catch {
    /* 非 JSON 响应，用原文 */
  }
  if (status === 401) {
    // 登录接口本身返回 401 是「账号或密码错误」，不该踢回登录页
    if (isLoggedIn()) {
      clearSession()
      ElMessage.error('登录已失效，请重新登录')
      window.location.assign('/login')
    }
  } else if (status === 403) {
    ElMessage.warning(detail || '无权限：该操作仅管理员可用')
  }
}

/** 带令牌的请求头（导出等场景也要带上，否则会被后端 401 拦掉） */
function authHeaders(): Record<string, string> {
  const tk = getToken()
  return tk ? { Authorization: `Bearer ${tk}` } : {}
}

export async function request<T>(
  path: string,
  params?: Record<string, any>,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin)
  const init: RequestInit = { method }
  const headers: Record<string, string> = { ...authHeaders() }

  if (method === 'GET' && params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      url.searchParams.set(key, String(value))
    })
  } else if (method !== 'GET' && params) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(params)
  }
  init.headers = headers

  const started = performance.now()
  try {
    const res = await fetch(url.toString(), init)
    if (!res.ok) {
      const text = await res.text().catch(() => '请求失败')
      handleAuthError(res.status, text)
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
  /** 是否开放库存管理 / 动销率 / 库存情况查询（本期仅大参林） */
  supports_inventory?: boolean
  /** 是否支持按连锁/加盟拆分（含 大区/营运区 列，如大参林） */
  supports_store_type?: boolean
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
  // 上传走 FormData，不能复用 request()，但必须手动带上令牌
  return fetch(url, { method: 'POST', body: formData, headers: authHeaders() }).then(
    async (res) => {
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        handleAuthError(res.status, JSON.stringify(data))
        throw new Error(data.detail || data.message || '批量导入失败')
      }
      return data as ImportExcelBatchResponse
    },
  )
}

/** 统计通用：库里「数据最新月份」的区间（数据常滞后于系统当月，供统计页面默认填充） */
export interface StatsLatestRange {
  date_from: string | null
  date_to: string | null
  max_date: string | null
}

export function fetchStatsLatestRange(dbKey: string) {
  return request<StatsLatestRange>(`/api/${dbKey}/stats/latest_range`)
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

// ---------- 筛选候选值（模糊查询，供统计各子功能下拉使用） ----------

export type FilterOptionField = 'city' | 'province' | 'product' | 'store'

export interface FilterOption {
  value: string
  label: string
}

export interface FilterOptionResponse {
  field: string
  /** 命中总数；items 会按 limit 截断 */
  total: number
  items: FilterOption[]
}

/**
 * 输入关键词 → 返回候选值（城市 / 省份 / 商品编码 / 门店名称）。
 * keyword 为空时返回前 limit 个，用于首次展开下拉时给一批默认值。
 * 后端对去重结果做了 TTL 缓存，逐次输入不会重复扫描大表。
 */
export function fetchFilterOptions(
  dbKey: string,
  field: FilterOptionField,
  keyword = '',
  limit = 30,
  source: 'sales' | 'inventory' = 'sales',
) {
  return request<FilterOptionResponse>(`/api/${dbKey}/filter_options`, {
    field,
    keyword,
    limit,
    source,
  })
}

// ---------- 门店库存（当天快照，导入即全量覆盖） ----------

export interface InventoryLatest {
  /** 最新库存截至日期 YYYY-MM-DD */
  date: string | null
  /** 可选日期列表（倒序，最多 30 个） */
  dates: string[]
  rows: number
  stores: number
  cats: number
}

export interface InventorySummary {
  stores: number
  cats: number
  qty: number
  expiry_qty: number
  expired_qty: number
}

export interface InventoryRow {
  store_code: string
  store_name: string | null
  city: string | null
  qty: number
  cat_cnt: number
  expiry_qty: number
  expired_qty: number
}

export interface InventoryQueryResponse {
  date: string | null
  expiry_months: number
  expiry_threshold: string | null
  summary: InventorySummary
  total: number
  page: number
  page_size: number
  rows: InventoryRow[]
}

export interface TurnoverCityRow {
  city: string
  sales_stores: number
  inventory_stores: number
  turnover_rate: number | null
}

export interface TurnoverResponse {
  date_from: string | null
  date_to: string | null
  inv_date: string | null
  /** 实销门店数（非重复计数） */
  sales_stores: number
  /** 库存门店数（非重复计数） */
  inventory_stores: number
  /** 动销率 % = 实销门店数 ÷ 库存门店数 × 100；库存门店数为 0 时为 null */
  turnover_rate: number | null
  by_city: TurnoverCityRow[]
}

export interface InventoryImportResult {
  db_key: string
  inserted: number
  date: string | null
  message: string
}

export function fetchInventoryLatest(dbKey: string) {
  return request<InventoryLatest>(`/api/${dbKey}/inventory/latest`)
}

export function fetchInventoryQuery(
  dbKey: string,
  options: {
    date?: string
    cities?: string
    provinces?: string
    products?: string
    stores?: string
    /** 门店类型：all=全部；chain=连锁(直营)；franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
    expiry_months?: number
    page?: number
    page_size?: number
    sort_by?: string
    sort_dir?: string
  } = {},
) {
  return request<InventoryQueryResponse>(`/api/${dbKey}/inventory/query`, options)
}

/** 动销率 = 实销门店数(非重复) ÷ 库存门店数(非重复) */
export function fetchInventoryTurnover(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    inv_date?: string
    cities?: string
    provinces?: string
    products?: string
    /** 门店类型：all=全部；chain=连锁(直营)；franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
  } = {},
) {
  return request<TurnoverResponse>(`/api/${dbKey}/inventory/turnover`, options)
}

/** 导入库存 Excel（全量覆盖）：库存是当天快照，每次更新都是完整最新文件 */
export function importInventory(
  dbKey: string,
  file: File,
): Promise<InventoryImportResult> {
  const formData = new FormData()
  formData.append('file', file)
  const url = `${API_BASE}/api/${dbKey}/inventory/import`
  // 上传走 FormData，不能复用 request()，但必须手动带上令牌
  return fetch(url, {
    method: 'POST',
    body: formData,
    headers: authHeaders(),
  }).then(async (res) => {
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      handleAuthError(res.status, JSON.stringify(data))
      throw new Error(data.detail || data.message || '库存导入失败')
    }
    return data as InventoryImportResult
  })
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
  /** true=省份展开模式：每个省份一张子表，表内 行=城市、列=品类 */
  province_expanded?: boolean
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
    /** 省份模式：合并省内城市为整省合计（一省一行） */
    merge_province_cities?: boolean
    /** 品类编码映射为中文名（无映射保持编码） */
    map_names?: boolean
    /** 门店类型：all=全部；chain=连锁(直营)；franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
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
  /** boxes=盒数；amount=实销金额（元，前端除以 10000 转万元展示） */
  metric?: 'boxes' | 'amount'
  /** true=省份展开模式：每个省份一张子表，表内 行=城市、列=品类 */
  province_expanded?: boolean
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
    /** 省份模式：合并省内城市为整省合计（一省一行） */
    merge_province_cities?: boolean
    calc_yoy_mom?: boolean
    /** 品类编码映射为中文名（无映射保持编码） */
    map_names?: boolean
    /** boxes=盒数（默认）；amount=实销金额 = SUM(数量×开票价)，单位元 */
    metric?: 'boxes' | 'amount'
    /** 门店类型：all=全部；chain=连锁(直营)；franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
  } = {},
) {
  return request<BoxCountResponse>(`/api/${dbKey}/stats/box_count`, options)
}

// ---------- 统计结果导出（CSV） ----------
// 后端已把所有导出改为 CSV（比 xlsx 快 5~20 倍、内存恒定）：
// 多张子表拼成一个文件，用首列「分组」区分原来的 sheet 名。
// 导出接口受「导出闸门」保护：并发达上限或同一账号连点过快会返回 429（detail 里有提示）。

export function downloadBlob(blob: Blob, filename: string) {
  if (DOWNLOAD_DISABLED) return
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
  const res = await fetch(url.toString(), { headers: authHeaders() })
  if (!res.ok) {
    const text = await res.text().catch(() => '导出失败')
    // 后端 429（导出闸门：并发满 / 连点过快）返回 {"detail": "..."}，
    // 直接抛原始 JSON 用户看不懂，这里解析出提示文案。
    let msg = text || `导出失败（HTTP ${res.status}）`
    try {
      msg = JSON.parse(text)?.detail || msg
    } catch {
      /* 非 JSON，原样使用 */
    }
    handleAuthError(res.status, msg)
    logger.warn(`API ${res.status} ${path}: ${msg}`, 'request')
    throw new Error(msg)
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
    /** 与查询口径一致：boxes=盒数 | amount=实销金额（导出文件名随指标变化） */
    metric?: 'boxes' | 'amount'
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
    /** 门店类型：all=全部，chain=连锁(直营)，franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
    /** 城市，英文逗号分隔；留空=全部（兼容 市/省市 两种写法） */
    cities?: string
    /** 省份，英文逗号分隔；留空=全部 */
    provinces?: string
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
    /** 门店类型：all=全部，chain=连锁(直营)，franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
    /** 城市，英文逗号分隔；留空=全部（兼容 市/省市 两种写法） */
    cities?: string
    /** 省份，英文逗号分隔；留空=全部 */
    provinces?: string
  } = {},
) {
  return _fetchBlob(`/api/${dbKey}/stats/store_ability/export`, options)
}

// ---------- 商品编码 → 品类中文名 映射管理 ----------

export interface ProductMapItem {
  code: string
  name: string
  /** 开票价（元）；未配置为 null。用于「实销金额」= 盒数 × 开票价 */
  price?: number | null
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

// ============================================================
// 数字看板（大参林）
// ============================================================
export interface DashboardOption {
  provinces: string[]
  cities: string[]
  products: { code: string; name: string }[]
  month_range: { min: string | null; max: string | null }
  supports_store_type: boolean
}

export interface DashboardKpi {
  boxes: number
  yoy_boxes: number
  yoy_pct: number | null
  rows: number
  stores: number
  provinces: number | null
  cities: number | null
}

export interface DashboardTrend {
  months: string[]
  current: number[]
  previous: number[]
  growth: (number | null)[]
}

export interface BreakdownItem {
  key: string | number
  name: string
  boxes: number
  stores: number
}

export interface StoreTypeSplit {
  st: string
  boxes: number
  stores: number
}

export interface TopStore {
  store_code: number
  store_name: string
  store_type: string
  boxes: number
}

export interface DashboardStores {
  store_type_split: StoreTypeSplit[]
  top_stores: TopStore[]
  top_products: { code: string; name: string; boxes: number }[]
}

export interface DashboardFilters {
  date_from?: string
  date_to?: string
  provinces?: string
  cities?: string
  products?: string
  store_type?: string
}

export function getDashboardOptions(dbKey: string) {
  return request<DashboardOption>(`/api/${dbKey}/dashboard/options`)
}

export function getDashboardKpi(dbKey: string, params: DashboardFilters) {
  return request<DashboardKpi>(`/api/${dbKey}/dashboard/kpi`, params)
}

export function getDashboardTrend(dbKey: string, params: DashboardFilters) {
  return request<DashboardTrend>(`/api/${dbKey}/dashboard/trend`, params)
}

export function getDashboardBreakdown(
  dbKey: string,
  dim: 'province' | 'city' | 'product',
  topN: number,
  params: DashboardFilters,
) {
  return request<{ dim: string; items: BreakdownItem[] }>(
    `/api/${dbKey}/dashboard/breakdown`,
    { dim, top_n: topN, ...params },
  )
}

export function getDashboardStores(dbKey: string, topN: number, params: DashboardFilters) {
  return request<DashboardStores>(`/api/${dbKey}/dashboard/stores`, { top_n: topN, ...params })
}

export interface DashboardSummary {
  options: DashboardOption
  kpi: DashboardKpi
  trend: DashboardTrend
  breakdown: {
    province: BreakdownItem[]
    city: BreakdownItem[]
    product: BreakdownItem[]
  }
  stores: DashboardStores
}

/** 一次请求拿回看板全部数据（KPI + 趋势 + 省/市/品种拆解 + 门店板块）。 */
export function getDashboardSummary(
  dbKey: string,
  params: DashboardFilters & {
    province_top_n?: number
    city_top_n?: number
    product_top_n?: number
    store_top_n?: number
  },
) {
  return request<DashboardSummary>(`/api/${dbKey}/dashboard/summary`, params)
}
