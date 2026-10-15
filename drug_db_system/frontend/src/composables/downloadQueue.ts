/**
 * 下载中心（异步导出任务）
 * --------------------------------------------------
 * 所有「导出 / 下载」统一从这里发起：
 * 1) 调 POST /api/{dbKey}/export/async 提交任务，立刻返回 job_id（后端后台慢慢生成，不阻塞）；
 * 2) 任务进入本地下载中心列表，轮询 GET /api/export/jobs 同步状态；
 * 3) 状态变为 done 时弹「前往下载中心」通知，用户在中心点「下载」取文件（不自动下载）；
 * 4) 服务端文件与任务 1 小时后自动清理，轮询发现记录消失即标记 expired。
 *
 * 设计为模块级单例：跨组件（实销查看、统计面板）共享同一个下载中心。
 */
import { ref } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import { DOWNLOAD_DISABLED, fetchBlobByPath, request } from '@/api/client'
import { logger } from '@/utils/logger'

export type ExportType = 'rows' | 'store_count' | 'box_count' | 'store_ability'

export type DownloadStatus = 'queued' | 'running' | 'done' | 'failed' | 'expired'

export interface DownloadTask {
  id: string
  /** 后端任务 id；提交成功后才有 */
  jobId?: string
  /** 展示名，如「查询结果 / 门店数统计」 */
  name: string
  dbKey: string
  type: ExportType
  status: DownloadStatus
  /** 下载文件名（完成后由后端返回） */
  filename?: string
  size?: number
  rows?: number
  error?: string
  createdAt: number
  finishedAt?: number
}

/** 后端 /api/export/jobs 返回的单个任务 */
interface ServerJob {
  job_id: string
  db_key: string
  type: string
  name: string
  status: 'queued' | 'running' | 'done' | 'failed'
  filename?: string | null
  size?: number
  rows?: number
  error?: string | null
  created_at?: number
  finished_at?: number | null
}

export interface SubmitExportOptions {
  dbKey: string
  type: ExportType
  /** 与旧同步接口同名的查询参数（snake_case） */
  params?: Record<string, any>
  name?: string
}

// ---------- 模块级单例状态 ----------
const tasks = ref<DownloadTask[]>([])
const POLL_INTERVAL_MS = 3000
let timer: ReturnType<typeof setInterval> | null = null
let seq = 0

/** 真正触发浏览器下载（沿用 DOWNLOAD_DISABLED 总开关） */
function triggerDownload(filename: string, blob: Blob) {
  if (DOWNLOAD_DISABLED) return
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 延迟回收，避免部分浏览器点击瞬间 revoke 导致下载为空
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** 滚动定位到页面上的「下载中心」看板 */
function gotoDownloadCenter() {
  const el = document.getElementById('download-center')
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

/** 任务完成提示：点击通知直达下载中心 */
function notifyReady(task: DownloadTask) {
  ElNotification({
    title: '导出完成',
    message: `「${task.name}」已生成，点击前往下载中心下载`,
    type: 'success',
    duration: 0,
    onClick: gotoDownloadCenter,
  })
}

/** 同步后端任务状态到本地列表 */
function applyServerJobs(serverJobs: ServerJob[]) {
  const map = new Map(serverJobs.map((j) => [j.job_id, j]))

  for (const t of tasks.value) {
    if (!t.jobId) continue
    const sj = map.get(t.jobId)

    if (!sj) {
      // 服务端已无此记录：1 小时 TTL 清理 / 手动删除 / 服务重启（内存任务表丢失）
      if (t.status !== 'expired') {
        const wasRunning = t.status === 'queued' || t.status === 'running'
        t.status = 'expired'
        // 还在排队/生成就消失，说明是任务失效而非文件过期，文案要区分开
        t.error = wasRunning
          ? '任务已失效（服务已重启或任务被清理）'
          : '文件已过期（超过 1 小时自动清理）'
      }
      continue
    }

    const prev = t.status
    t.status = sj.status
    t.name = sj.name || t.name
    t.filename = sj.filename || undefined
    t.size = sj.size || 0
    t.rows = sj.rows || 0
    t.error = sj.error || undefined
    t.finishedAt = sj.finished_at ? sj.finished_at * 1000 : undefined

    if (sj.status === 'done' && prev !== 'done') {
      notifyReady(t)
    }
  }
}

/** 拉取一次状态；没有进行中任务就停掉轮询，避免空转 */
async function poll() {
  try {
    const res = await request<{ jobs: ServerJob[] }>('/api/export/jobs')
    applyServerJobs(res.jobs || [])
  } catch (e: any) {
    logger.warn('下载中心轮询失败: ' + (e?.message || e), 'downloadQueue')
  }

  const hasActive = tasks.value.some((t) => t.status === 'queued' || t.status === 'running')
  if (!hasActive && timer) {
    clearInterval(timer)
    timer = null
  }
}

/** 确保轮询在跑（有任务时才跑） */
export function ensurePolling() {
  if (timer) return
  timer = setInterval(poll, POLL_INTERVAL_MS)
  poll() // 立即拉一次，缩短首次反馈
}

/** 提交一次异步导出；失败（429 等）直接提示 */
export async function submitExport(opts: SubmitExportOptions) {
  if (DOWNLOAD_DISABLED) {
    ElMessage.warning('导出功能已关闭')
    return
  }
  const id = `dl_${++seq}_${Date.now()}`
  // 先占个位（乐观插入），提交成功再补 jobId；失败则移除
  const task: DownloadTask = {
    id,
    name: opts.name || '导出任务',
    dbKey: opts.dbKey,
    type: opts.type,
    status: 'queued',
    createdAt: Date.now(),
  }
  tasks.value.unshift(task)

  try {
    const res = await request<{ job_id: string; status: string; name: string }>(
      `/api/${opts.dbKey}/export/async`,
      { type: opts.type, params: opts.params || {}, name: opts.name },
      'POST',
    )
    task.jobId = res.job_id
    task.name = res.name || task.name
    ElMessage.success('已加入下载队列，完成后会通知你')
    ensurePolling()
  } catch (e: any) {
    tasks.value = tasks.value.filter((t) => t.id !== id)
    ElMessage.error(e?.message || '提交导出失败')
    // 不向外抛：调用方都是「点了就不管」的场景，避免未处理的 Promise 拒绝
  }
}

/** 从下载中心取文件（仅 done 状态可用） */
export async function downloadJob(id: string) {
  const t = tasks.value.find((x) => x.id === id)
  if (!t || !t.jobId || t.status !== 'done') return
  try {
    const blob = await fetchBlobByPath(`/api/export/jobs/${t.jobId}/download`)
    triggerDownload(t.filename || `${t.name}.csv`, blob)
  } catch (e: any) {
    ElMessage.error(e?.message || '下载失败')
  }
}

/** 重试失败任务（重新排队，由后端慢慢跑） */
export async function retryExport(id: string) {
  const t = tasks.value.find((x) => x.id === id)
  if (!t || !t.jobId) return
  try {
    await request(`/api/export/jobs/${t.jobId}/retry`, undefined, 'POST')
    t.status = 'queued'
    t.error = undefined
    ElMessage.success('已重新排队')
    ensurePolling()
  } catch (e: any) {
    ElMessage.error(e?.message || '重试失败')
  }
}

/** 移除单个任务（同时删掉服务端记录与文件） */
export async function removeExport(id: string) {
  const t = tasks.value.find((x) => x.id === id)
  if (t?.jobId) {
    try {
      await request(`/api/export/jobs/${t.jobId}`, undefined, 'DELETE')
    } catch (e: any) {
      logger.warn('删除导出任务失败: ' + (e?.message || e), 'downloadQueue')
    }
  }
  tasks.value = tasks.value.filter((x) => x.id !== id)
}

/** 清空已完成 / 失败 / 过期的历史（保留进行中的） */
export async function clearExportHistory() {
  const keep = tasks.value.filter((t) => t.status === 'queued' || t.status === 'running')
  const done = tasks.value.filter((t) => !keep.includes(t))
  for (const t of done) {
    if (!t.jobId) continue
    try {
      await request(`/api/export/jobs/${t.jobId}`, undefined, 'DELETE')
    } catch {
      /* 已过期/已删除的直接忽略 */
    }
  }
  tasks.value = keep
}

export function useDownloadQueue() {
  return {
    tasks,
    submitExport,
    downloadJob,
    retryExport,
    removeExport,
    clearExportHistory,
    ensurePolling,
  }
}
