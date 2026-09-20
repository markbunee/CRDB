/**
 * 登录态管理：令牌与当前用户（localStorage 持久化 + 响应式）。
 *
 * 说明：项目未引入 Pinia，这里用模块级 ref 充当轻量 store，
 * 登录/登出后所有引用 currentUser() / isAdmin() 的组件会自动更新。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

export interface AuthUser {
  id: number
  username: string
  display_name: string
  phone: string
  role: 'admin' | 'member'
  /** 被管理员显式授予的细粒度权限：'import'（导入/上传）、'export'（导出） */
  permissions: string[]
  status: string
  must_change_password: boolean
  last_login_at: string | null
  password_mask?: string
}

const TOKEN_KEY = 'crdb_token'
const USER_KEY = 'crdb_user'

function readUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
const user = ref<AuthUser | null>(readUser())

export function getToken(): string {
  return token.value
}

export function currentUser(): AuthUser | null {
  return user.value
}

export function isLoggedIn(): boolean {
  return !!token.value
}

export function isAdmin(): boolean {
  return user.value?.role === 'admin'
}

/** 登录成功后保存令牌与用户信息 */
export function setSession(t: string, u: AuthUser): void {
  token.value = t
  user.value = u
  localStorage.setItem(TOKEN_KEY, t)
  localStorage.setItem(USER_KEY, JSON.stringify(u))
}

/** 登出 / 令牌失效时清理 */
export function clearSession(): void {
  token.value = ''
  user.value = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/**
 * 管理员专属操作的统一拦截：成员点击时弹「无权限」提示并返回 false。
 * 用法：if (!requireAdmin('导入 Excel')) return
 *
 * 注意这只是前端体验层；后端对所有敏感接口都有 require_admin 校验，
 * 即使绕过前端也会被 403 拒绝（纵深防御）。
 */
export function requireAdmin(action = '该操作'): boolean {
  if (isAdmin()) return true
  ElMessage.warning(`无权限：${action}仅管理员可用`)
  return false
}

/** 判断某个能力是否可用（用于按钮显隐/禁用） */
export function hasPermission(perm: string): boolean {
  if (isAdmin()) return true
  return (user.value?.permissions ?? []).includes(perm)
}

export function can(action: 'import' | 'export' | 'delete' | 'admin'): boolean {
  return hasPermission(action)
}

/**
 * 细粒度权限拦截：成员点击时若无该权限弹「无权限」提示并返回 false。
 * 用法：if (!requirePermission('import', '导入库存数据')) return
 */
export function requirePermission(perm: string, action = '该操作'): boolean {
  if (hasPermission(perm)) return true
  ElMessage.warning(`无权限：${action}需要相应权限（请联系管理员开通）`)
  return false
}
