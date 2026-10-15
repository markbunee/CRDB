/** 金额显示单位 */
export type AmountUnit = 'yuan' | 'wan'

/**
 * 格式化金额。
 * - yuan：原样输出，保留 2 位小数，千分位。
 * - wan：除以 10000，保留 1 位小数，加「万」后缀。
 */
export function formatAmount(n: number | string | null | undefined, unit: AmountUnit): string {
  if (n == null || n === '') return '—'
  const v = typeof n === 'string' ? Number(n) : n
  if (!Number.isFinite(v)) return '—'
  if (unit === 'wan') {
    return `${(v / 10000).toLocaleString('zh-CN', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    })}万`
  }
  return v.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
}

/** 金额单位显示标签 */
export function amountUnitLabel(unit: AmountUnit): string {
  return unit === 'wan' ? '万' : '元'
}
