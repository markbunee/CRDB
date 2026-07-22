/**
 * 前端日志工具：console 输出 + 批量上报到后端 /api/log/frontend
 * 后端将日志写入 frontend/log.txt
 */

type LogLevel = 'info' | 'warn' | 'error'

interface LogEntry {
  level: LogLevel
  message: string
  context?: string
}

const API_BASE = import.meta.env.VITE_API_BASE || ''
const BUFFER: LogEntry[] = []
const FLUSH_INTERVAL = 2000
const FLUSH_MAX = 20
let flushTimer: ReturnType<typeof setTimeout> | null = null

function scheduleFlush() {
  if (flushTimer) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    flush()
  }, FLUSH_INTERVAL)
}

async function flush() {
  if (BUFFER.length === 0) return
  const batch = BUFFER.splice(0, BUFFER.length)
  try {
    await fetch(`${API_BASE}/api/log/frontend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        level: batch[batch.length - 1].level,
        message: batch.map((e) => e.message).join('\n'),
        context: batch[0].context,
      }),
    })
  } catch {
    // 后端不可用，静默丢弃，避免日志循环
  }
}

function push(level: LogLevel, message: string, context?: string) {
  // 控制台输出
  const fn = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log
  fn(`[frontend] ${message}`)

  BUFFER.push({ level, message, context })
  if (BUFFER.length >= FLUSH_MAX) {
    flush()
  } else {
    scheduleFlush()
  }
}

export const logger = {
  info(message: string, context?: string) {
    push('info', message, context)
  },
  warn(message: string, context?: string) {
    push('warn', message, context)
  },
  error(message: string, context?: string) {
    push('error', message, context)
  },
}

/** 页面卸载前同步上报剩余日志 */
export function flushLogs() {
  flush()
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushLogs)
  window.addEventListener('error', (event) => {
    push('error', `${event.message} @ ${event.filename}:${event.lineno}`, 'globalError')
  })
  window.addEventListener('unhandledrejection', (event) => {
    push('error', `未处理的 Promise 拒绝: ${event.reason}`, 'unhandledrejection')
  })
}
