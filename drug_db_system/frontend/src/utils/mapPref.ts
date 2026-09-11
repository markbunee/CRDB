/**
 * 「品类映射」开关的本地偏好：四个统计页共用同一份开关状态（localStorage 持久化）。
 */
const KEY = 'product_map_names'

export function loadMapPref(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export function saveMapPref(v: boolean): void {
  try {
    localStorage.setItem(KEY, v ? '1' : '0')
  } catch {
    /* 隐私模式等场景下写不进就算了，不影响使用 */
  }
}
