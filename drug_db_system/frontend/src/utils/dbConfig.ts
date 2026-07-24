/**
 * 数据库配置缓存工具：避免 stats 组件重复请求 /api/dbs
 */
import { fetchDbs, type DbMeta } from '@/api/client'

let _cache: DbMeta[] | null = null

export async function getDbConfig(dbKey: string): Promise<DbMeta | null> {
  if (!_cache) {
    try {
      _cache = await fetchDbs()
    } catch {
      return null
    }
  }
  return _cache.find((d) => d.key === dbKey) || null
}

export function clearDbConfigCache() {
  _cache = null
}
