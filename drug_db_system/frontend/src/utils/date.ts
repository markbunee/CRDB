/**
 * 日期工具函数
 */

/** 返回当前月份的日期范围：月初 ～ 今天 */
export function getCurrentMonthRange(): { start: string; end: string } {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth() + 1 // 0-based

  const pad = (n: number) => String(n).padStart(2, '0')
  const start = `${year}-${pad(month)}-01`
  const end = `${year}-${pad(month)}-${pad(now.getDate())}`

  return { start, end }
}
