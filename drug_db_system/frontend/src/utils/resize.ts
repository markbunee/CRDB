/**
 * 窗口 resize 防抖订阅（统一入口）。
 *
 * 背景：手机端浏览器地址栏在滚动时收起 / 展开会**连续触发 resize**。
 * 图表组件若直接绑定 resize，会在滚动过程中被反复 `chart.resize()`，
 * 表现为整屏重绘、滑动卡顿。所以全项目统一走这里，默认 150ms 防抖。
 *
 * 同时它返回取消订阅函数，避免像之前 `SalesTrendStats` 那样
 * 注册后从不移除，造成监听器堆积 + 对已 dispose 的图表调用 resize。
 *
 * @param handler 实际处理函数
 * @param delay 防抖间隔（毫秒），默认 150
 * @returns 取消订阅函数，请在 `onUnmounted` / `onBeforeUnmount` 中调用
 */
export function onWindowResize(handler: () => void, delay = 150): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null

  const listener = () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      handler()
    }, delay)
  }

  window.addEventListener('resize', listener)

  return () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    window.removeEventListener('resize', listener)
  }
}
