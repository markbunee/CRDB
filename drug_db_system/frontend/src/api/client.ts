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
  signal?: AbortSignal,
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
  if (signal) init.signal = signal
  init.headers = headers

  const started = performance.now()
  try {
    const res = await fetch(url.toString(), init)
    if (!res.ok) {
      const text = await res.text().catch(() => '请求失败')
      // 429：查询/导出闸门拒绝（服务器繁忙），后端 detail 已是友好文案；
      // 其它错误也尽量抽取 detail，避免把原始 JSON 直接甩给用户。
      let detail = text
      try {
        const parsed = JSON.parse(text)
        if (parsed?.detail) detail = parsed.detail
      } catch {
        /* 非 JSON，用原文 */
      }
      handleAuthError(res.status, text)
      logger.warn(`API ${res.status} ${path}: ${detail}`, 'request')
      const err: any = new Error(detail)
      err.status = res.status
      throw err
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

// 三个公司视图都会拉取库元数据（含 row_count 角标），登录后基本不变。
// 模块级缓存一次，避免每个视图挂载都重复请求；导入后调用 invalidateDbsCache() 刷新。
let _dbsCache: Promise<DbMeta[]> | null = null

export function fetchDbs() {
  if (!_dbsCache) {
    _dbsCache = request<DbMeta[]>('/api/dbs')
    // 失败的请求不要永久占住缓存：一旦 rejected 立即允许下次重取
    _dbsCache.catch(() => {
      _dbsCache = null
    })
  }
  return _dbsCache
}

export function invalidateDbsCache() {
  _dbsCache = null
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
  signal?: AbortSignal,
) {
  return request<RowResponse>(`/api/${dbKey}/rows`, options, 'GET', signal)
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

// 「数据最新月份」在导入 / 清空之前不会变化，而多个统计子页（门店数 / 盒数 /
// 销售趋势 / 动销率）每次挂载都会各自拉一次，切换统计功能时反复请求同一接口。
// 这里做模块级缓存：同一库只请求一次；导入 / 清空后由视图调用
// invalidateStatsLatestRangeCache() 失效，保证默认区间仍取到最新数据。
const _latestRangeCache = new Map<string, Promise<StatsLatestRange>>()

export function fetchStatsLatestRange(dbKey: string) {
  let p = _latestRangeCache.get(dbKey)
  if (!p) {
    p = request<StatsLatestRange>(`/api/${dbKey}/stats/latest_range`)
    // 失败不要永久占住缓存：一旦 rejected 立即允许下次重取
    p.catch(() => {
      _latestRangeCache.delete(dbKey)
    })
    _latestRangeCache.set(dbKey, p)
  }
  return p
}

export function invalidateStatsLatestRangeCache() {
  _latestRangeCache.clear()
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

/** 动销率 月度：单月一行 */
export interface TurnoverMonthlyMonth {
  /** YYYY-MM */
  month: string
  /** 该月采用的库存快照日期 YYYY-MM-DD（各月统一取最新一次快照：库存无历史版本） */
  inv_date: string | null
  /** 当月实销门店数（去重） */
  sales_stores: number
  /** 当月库存门店数（去重，按 inv_date 快照） */
  inventory_stores: number
  /** 动销率 % = 实销门店数 ÷ 库存门店数 × 100 */
  turnover_rate: number | null
}

export interface TurnoverMonthlyOverall {
  sales_stores: number
  inventory_stores: number
  turnover_rate: number | null
  inv_date: string | null
}

export interface TurnoverMonthlyResponse {
  date_from: string | null
  date_to: string | null
  months: TurnoverMonthlyMonth[]
  overall: TurnoverMonthlyOverall | null
  by_city: TurnoverCityRow[]
  inv_dates?: string[]
  note?: string
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

/** 动销率 按月趋势 */
export function fetchInventoryTurnoverMonthly(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    cities?: string
    provinces?: string
    products?: string
    /** 门店类型：all=全部；chain=连锁(直营)；franchise=加盟 */
    store_type?: 'all' | 'chain' | 'franchise'
  } = {},
) {
  return request<TurnoverMonthlyResponse>(`/api/${dbKey}/inventory/turnover/monthly`, options)
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

// ---------- 门店数统计（通用维度） ----------

/**
 * province = 省份维度：省份拆表、行=省内城市、列=品类（原省份模式的「省 → 市」展开视图，
 * 已与地域级别开关解耦为独立维度）。仅支持省份的库（如大参林）可选。
 */
export type StoreCountDimension = 'city' | 'product' | 'time' | 'province'
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
  /** 注意：门店数是去重计数（COUNT DISTINCT 门店），不可加，
   *  故后端不产出 total / col_totals / grand_total / footer，界面也不显示合计。 */
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
  /** 行合计（该行各列之和） */
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
  /** 列合计（每列纵向求和），键为 col_keys */
  col_totals?: Record<string, number>
  /** 整表总计 */
  grand_total?: number
  /** 表尾「合计」行：形状与数据行完全一致（含同期/上月合计），前端复用同一套渲染 */
  footer?: BoxCountCellRow
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

// ---------- 导出 / 下载（CSV） ----------
// 后端已把所有导出改为 CSV（比 xlsx 快 5~20 倍、内存恒定）：
// 多张子表拼成一个文件，用首列「分组」区分原来的 sheet 名。
// 导出接口受「导出闸门」保护：并发达上限或同一账号连点过快会返回 429（detail 里有提示）。
// 前端不再有同步导出：一律走异步导出任务（下载中心），取文件用 fetchBlobByPath()。

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

/** 按路径直接拉取文件 Blob（异步导出任务「下载中心」取文件用，带令牌 + 429 文案解析） */
export function fetchBlobByPath(path: string): Promise<Blob> {
  return _fetchBlob(path)
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

// ---------- 实销金额口径完整性（缺开票价会静默拉低金额） ----------

export interface AmountCoverageItem {
  code: string
  name: string
  /** 该品类在区间内的盒数（全部未计入金额） */
  qty: number
}

export interface AmountCoverageResponse {
  db_key: string
  date_from: string | null
  date_to: string | null
  /** 已配置开票价的品类数 */
  priced_codes: number
  /** 区间总盒数 */
  total_qty: number
  /** 未配置开票价、因而未计入金额的盒数 */
  unpriced_qty: number
  /** unpriced_qty / total_qty 的百分比 */
  unpriced_share: number
  /** 缺价品类明细（按盒数降序） */
  items: AmountCoverageItem[]
}

/**
 * 查「实销金额」口径完整性：区间内哪些品类有销量却没配开票价。
 * 这些品类被按 0 计入金额，会让金额静默偏低。
 */
export function fetchAmountCoverage(dbKey: string, dateFrom?: string, dateTo?: string) {
  return request<AmountCoverageResponse>(`/api/${dbKey}/stats/amount_coverage`, {
    date_from: dateFrom,
    date_to: dateTo,
  })
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

// ============================================================
// 数字看板（大参林）
// ============================================================
export interface DashboardOption {
  provinces: string[]
  cities: string[]
  products: { code: string; name: string }[]
  month_range: { min: string | null; max: string | null }
  supports_store_type: boolean
  /** 是否开放库存管理 / 动销率 / 库存情况查询（本期仅大参林） */
  supports_inventory?: boolean
}

export interface DashboardKpi {
  boxes: number
  yoy_boxes: number
  yoy_pct: number | null
  amount: number | null
  yoy_amount: number | null
  yoy_amount_pct: number | null
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
  current_amount: number[]
  previous_amount: number[]
  growth_amount: (number | null)[]
}

export interface BreakdownItem {
  key: string | number
  name: string
  boxes: number
  amount: number
  stores: number
  /** 仅城市维度返回，标识该城市所属省份 */
  province?: string | null
}

export interface StoreTypeSplit {
  st: string
  boxes: number
  amount: number
  stores: number
}

export interface TopStore {
  store_code: number
  store_name: string
  store_type: string
  boxes: number
  amount: number
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
  /** 门店类型：与后端一致，仅这三个取值（all=全部，chain=连锁直营，franchise=加盟） */
  store_type?: 'all' | 'chain' | 'franchise'
}

export function getDashboardKpi(dbKey: string, params: DashboardFilters) {
  return request<DashboardKpi>(`/api/${dbKey}/dashboard/kpi`, params)
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

export interface DashboardSummary {
  options: DashboardOption
  kpi: DashboardKpi
  trend: DashboardTrend
  breakdown: {
    province: BreakdownItem[]
    city: BreakdownItem[]
    product: BreakdownItem[]
  }
  stores: DashboardStores | null
  /** 首屏未传日期时，后端实际采用的默认区间 */
  applied_range: { date_from: string; date_to: string } | null
}

/** 一次请求拿回看板全部数据（KPI + 趋势 + 省/市/品种拆解 + 门店板块）。 */
export function getDashboardSummary(
  dbKey: string,
  params: DashboardFilters & {
    province_top_n?: number
    city_top_n?: number
    product_top_n?: number
    store_top_n?: number
    include_stores?: boolean
  },
) {
  return request<DashboardSummary>(`/api/${dbKey}/dashboard/summary`, params)
}

// ---------- 数字看板：会话级「静态固定显示」缓存 ----------
// 看板是「读多写少的分析数据」：同一筛选组合的结果在会话内是稳定的。
// 这里把结果缓存在模块作用域（跨组件挂载 / 路由切换常驻），于是：
//   关闭再打开看板、切到别的公司页再切回，都直接返回已有结果，
//   既不再重复请求，也不再重跑后端的千万级聚合 —— 即「静态固定显示」。
// 数据真正发生变化（导入 / 清空 / 开票价映射变更）时，由调用方
// invalidateDashboardCache() 主动失效，保证不长期显示旧数据。
const _dashboardSummaryCache = new Map<string, DashboardSummary>()

/** 归一化缓存键：忽略空值，避免「未传」与「传空串」被判成两次不同筛选。 */
function _dashboardCacheKey(dbKey: string, params: Record<string, any>): string {
  const parts = Object.keys(params)
    .filter((k) => {
      const v = params[k]
      return v !== undefined && v !== null && v !== ''
    })
    .sort()
    .map((k) => `${k}=${params[k]}`)
  return `${dbKey}|${parts.join('&')}`
}

/** 同步读取已缓存的首屏结果：命中时可立即「静态」呈现，无需 loading。 */
export function peekDashboardSummary(dbKey: string, params: Record<string, any>) {
  return _dashboardSummaryCache.get(_dashboardCacheKey(dbKey, params))
}

/** 带会话缓存的看板首屏：命中直接返回，未命中请求后写入缓存。 */
export function getDashboardSummaryCached(
  dbKey: string,
  params: Parameters<typeof getDashboardSummary>[1],
) {
  const key = _dashboardCacheKey(dbKey, params)
  const hit = _dashboardSummaryCache.get(key)
  if (hit) return Promise.resolve(hit)
  return getDashboardSummary(dbKey, params).then((res) => {
    _dashboardSummaryCache.set(key, res)
    // 首屏未传日期时后端回填了默认区间：把同一份结果也登记到「已回填区间」的键下。
    // 否则下次以具体日期请求会命中不到，关闭重开看板又会重跑一遍（即用户说的「每次都刷新」）。
    if (res.applied_range) {
      _dashboardSummaryCache.set(
        _dashboardCacheKey(dbKey, {
          ...params,
          date_from: res.applied_range.date_from,
          date_to: res.applied_range.date_to,
        }),
        res,
      )
    }
    return res
  })
}

/** 看板数据发生变化（导入 / 清空 / 开票价映射变更）后调用，清空会话缓存。 */
export function invalidateDashboardCache() {
  _dashboardSummaryCache.clear()
}

export interface DashboardProductDetail {
  code: string
  name: string | null
  months: string[]
  boxes: number[]
  amount: number[]
  stores: number[]
  yoy_pct: (number | null)[]
  mom_pct: (number | null)[]
  total_boxes: number
  total_amount: number
  total_stores: number
}

/** 单品种逐月明细：销量 / 金额 / 门店数 / 同比 / 环比。 */
export function getDashboardProductDetail(
  dbKey: string,
  code: string,
  params: DashboardFilters,
) {
  return request<DashboardProductDetail>(`/api/${dbKey}/dashboard/product-detail`, {
    code,
    ...params,
  })
}

// ============================================================
// AI 问数
// ============================================================

/** 确认卡里单个槽位的状态：绿=已确定 / 黄=待确认（含 AI 建议） / 红=必填缺失 */
export type AiSlotState = 'resolved' | 'ambiguous' | 'missing'

export interface AiSlotOption {
  v: string
  label: string
  level?: string
  hot?: boolean
}

export interface AiSlot {
  key: string
  label: string
  state: AiSlotState
  value: any
  display?: string | null
  note?: string | null
  options: AiSlotOption[]
  multi?: boolean
  sub?: {
    key: string
    label: string
    value: any
    type: string
    options?: AiSlotOption[]
  }[]
}

export interface AiCard {
  session_id: string
  question_id?: number
  question: string
  capability: string | null
  capability_label?: string
  db_key: string
  data_range: { min_date: string | null; max_date: string | null }
  confidence: number
  core: AiSlot[]
  advanced: AiSlot[]
  missing: string[]
  ready: boolean
}

export interface AiAnswer {
  mode: 'answer'
  question_id?: number
  session_id: string
  ok: boolean
  capability?: string | null
  capability_label?: string
  answer?: string
  caveats?: string[]
  data?: Record<string, any>
  cost_ms?: number
  error?: string
}

export interface AiAskResponse {
  mode: 'card' | 'answer'
  card?: AiCard
  question_id?: number
  session_id?: string
  ok?: boolean
  answer?: string
  caveats?: string[]
  data?: Record<string, any>
  capability_label?: string
  cost_ms?: number
  error?: string
}

/**
 * AI 提问。返回两种形态：
 *  - mode='card'   ：必填槽有缺失/歧义 → 渲染确认卡，让用户补齐（问全）
 *  - mode='answer' ：槽位齐了，已直接执行
 */
export function askAi(dbKey: string, text: string, sessionId?: string) {
  return request<AiAskResponse>(`/api/${dbKey}/ai/ask`, { text, session_id: sessionId }, 'POST')
}

/** 确认卡提交 */
export function confirmAi(
  dbKey: string,
  payload: { session_id: string; question_id?: number; answers: Record<string, any>; edited_from?: string },
) {
  return request<AiAskResponse>(`/api/${dbKey}/ai/confirm`, payload, 'POST')
}

/** 用户放弃（重要的负面信号，后端会记埋点） */
export function abandonAi(dbKey: string, payload: { session_id: string; question_id?: number }) {
  return request<{ ok: boolean }>(`/api/${dbKey}/ai/abandon`, payload, 'POST')
}

/** 赞 / 踩 */
export function feedbackAi(
  dbKey: string,
  payload: { question_id: number; rating: number; comment?: string },
) {
  return request<{ ok: boolean }>(`/api/${dbKey}/ai/feedback`, payload, 'POST')
}

// ---------- 模型配置（每个登录用户各管各的） ----------

export interface AiProvider {
  /** 供应商 key（我们自己的标识，如 deepseek / custom） */
  key: string
  /** pi-ai 的 provider 模块名（后端透传给桥梁用，前端不必关心） */
  pi: string
  label: string
  base_url: string
  docs_url?: string
  key_hint?: string
  models: string[]
  default_model: string
  note?: string
}

export interface AiModelConfig {
  id: number
  name: string
  provider: string
  provider_label: string
  model: string
  base_url: string
  /** 只回显尾部几位，如 sk-****abcd；明文永不出后端 */
  api_key_masked: string
  has_key: boolean
  is_active: boolean
  updated_at: string
}

export interface AiModelValidateResult {
  valid: boolean
  message: string
  models?: string[]
  base_url?: string
  http_status?: number
}

/** 可选供应商目录（后端下发，加厂商不用改前端） */
export function fetchAiProviders() {
  return request<{ providers: AiProvider[] }>('/api/ai-models/providers')
}

/** 我保存的模型配置列表 */
export function fetchAiModels() {
  return request<{ items: AiModelConfig[] }>('/api/ai-models')
}

/** 新建或更新一套配置（id 为空即新建；api_key 留空表示保留原密钥） */
export function saveAiModel(payload: {
  id?: number
  name: string
  provider: string
  model: string
  api_key?: string
  base_url?: string
}) {
  return request<{ ok: boolean; item: AiModelConfig }>('/api/ai-models', payload, 'POST')
}

/** 切换当前启用的一套 */
export function activateAiModel(configId: number) {
  return request<{ ok: boolean }>(`/api/ai-models/${configId}/activate`, {}, 'POST')
}

export function deleteAiModel(configId: number) {
  return request<{ ok: boolean }>(`/api/ai-models/${configId}`, undefined, 'DELETE')
}

/** 保存前先测连通性 */
export function validateAiModel(payload: {
  provider: string
  model?: string
  api_key: string
  base_url?: string
}) {
  return request<AiModelValidateResult>('/api/ai-models/validate', payload, 'POST')
}
