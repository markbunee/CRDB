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

// ---------- 门店数统计 ----------

export interface StoreCountRow {
  city: string
  counts: Record<string, number>
}

export interface StoreCountTable {
  title: string
  product_code: string | null
  time_columns: string[]
  cities: string[]
  rows: StoreCountRow[]
}

export interface StoreCountResponse {
  tables: StoreCountTable[]
  merge_months: boolean
  merge_products: boolean
  total_raw_rows: number
}

export function fetchStoreCityStats(
  dbKey: string,
  options: {
    date_from?: string
    date_to?: string
    merge_months?: boolean
    cities?: string
    product_codes?: string
    merge_products?: boolean
  } = {},
) {
  return request<StoreCountResponse>(`/api/${dbKey}/stats/store_city`, options)
}
