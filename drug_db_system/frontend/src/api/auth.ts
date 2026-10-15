/**
 * 认证与成员管理接口封装。
 * 除 login / apply 外，其余接口都需要在 client.ts 注入的 Bearer 令牌。
 */
import { request } from './client'
import type { AuthUser } from '@/utils/auth'

export interface LoginResult {
  token: string
  user: AuthUser
}

export interface MemberOverview {
  total: number
  admin_count: number
  member_count: number
  pending_applications: number
  users: AuthUser[]
}

export interface Application {
  id: number
  display_name: string
  phone: string
  username: string
  note: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string | null
  reviewed_at: string | null
  reviewed_by: string | null
  reject_reason: string
}

// ---------- 登录 / 登出 / 当前用户 ----------

/** 登录：手机号 + 密码（密码为申请账号时用户自己设置的） */
export function login(phone: string, password: string) {
  return request<LoginResult>('/api/auth/login', { phone, password }, 'POST')
}

export function logout() {
  return request<{ message: string }>('/api/auth/logout', undefined, 'POST')
}

export function fetchMe() {
  return request<AuthUser>('/api/auth/me')
}

export function changePassword(old_password: string, new_password: string) {
  return request<{ message: string }>(
    '/api/auth/change-password',
    { old_password, new_password },
    'POST',
  )
}

// ---------- 新成员申请（无需登录）----------

/** 申请账号：姓名 + 手机号 + 自设密码（密码由用户自己设置，管理员看不到） */
export function applyMember(payload: {
  display_name: string
  phone: string
  password: string
  username?: string
  note?: string
}) {
  return request<{ message: string; application: Application }>(
    '/api/auth/apply',
    payload,
    'POST',
  )
}

// ---------- 后台管理（仅管理员）----------

export function fetchMembers() {
  return request<MemberOverview>('/api/auth/members')
}

export function fetchApplications(status = '') {
  return request<{ applications: Application[] }>(
    '/api/auth/applications',
    status ? { status } : undefined,
  )
}

/** 同意申请：密码沿用申请人自设的哈希，后端不返回任何明文 */
export function approveApplication(id: number, payload: { username?: string } = {}) {
  return request<{ message: string; user: AuthUser }>(
    `/api/auth/applications/${id}/approve`,
    payload,
    'POST',
  )
}

export function rejectApplication(id: number, reason = '') {
  return request<{ message: string }>(
    `/api/auth/applications/${id}/reject`,
    { reason },
    'POST',
  )
}

/** 删除成员账号：管理员改不了密码，只能删除让其用手机号重新申请并自设密码 */
export function deleteMember(uid: number) {
  return request<{ message: string; username: string }>(
    `/api/auth/members/${uid}`,
    undefined,
    'DELETE',
  )
}

export function setMemberStatus(uid: number, status: 'active' | 'disabled') {
  return request<{ message: string; username: string; status: string }>(
    `/api/auth/members/${uid}/status`,
    { status },
    'POST',
  )
}

/** 配置成员的数据权限（import 导入/上传、export 导出） */
export function setMemberPermissions(uid: number, permissions: string[]) {
  return request<{ message: string; username: string; permissions: string[] }>(
    `/api/auth/members/${uid}/permissions`,
    { permissions },
    'POST',
  )
}
