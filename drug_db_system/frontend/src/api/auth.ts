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

export function login(username: string, password: string) {
  return request<LoginResult>('/api/auth/login', { username, password }, 'POST')
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

export function applyMember(payload: {
  display_name: string
  phone: string
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

/** 同意申请：返回初始密码（后台只会明文回显这一次） */
export function approveApplication(
  id: number,
  payload: { username?: string; password?: string } = {},
) {
  return request<{ message: string; user: AuthUser; initial_password: string }>(
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

/** 重置成员密码：返回新密码（仅回显一次） */
export function resetMemberPassword(uid: number, password?: string) {
  return request<{ message: string; username: string; new_password: string }>(
    `/api/auth/members/${uid}/reset-password`,
    password ? { password } : {},
    'POST',
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
