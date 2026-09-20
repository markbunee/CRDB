<template>
  <div class="admin-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">后台管理</h2>
        <p class="page-desc">成员管理、入会申请审批（仅管理员可见）</p>
      </div>
      <div class="head-actions">
        <el-button @click="loadAll">刷新</el-button>
        <el-button @click="goBack">返回数据页</el-button>
      </div>
    </div>

    <!-- ---------- 成员概况 ---------- -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value">{{ overview.total }}</div>
          <div class="stat-label">成员总数</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value admin">{{ overview.admin_count }}</div>
          <div class="stat-label">管理员</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value member">{{ overview.member_count }}</div>
          <div class="stat-label">普通成员</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value pending">{{ overview.pending_applications }}</div>
          <div class="stat-label">待审申请</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ---------- 成员列表 ---------- -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="section-title">成员列表</div>
      </template>
      <el-table :data="overview.users" v-loading="loadingMembers" border stripe size="small">
        <el-table-column prop="display_name" label="姓名" min-width="100" />
        <el-table-column prop="username" label="登录账号" min-width="120" />
        <el-table-column prop="phone" label="联系电话" min-width="120" />
        <el-table-column label="角色" width="90">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small">
              {{ row.role === 'admin' ? '管理员' : '成员' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'warning'" size="small">
              {{ row.status === 'active' ? '启用' : '已禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="数据权限" min-width="180">
          <template #default="{ row }">
            <template v-if="row.role === 'admin'">
              <el-tag type="danger" size="small">全部</el-tag>
            </template>
            <template v-else>
              <el-tag v-if="row.permissions?.includes('import')" type="success" size="small" style="margin-right:4px">导入</el-tag>
              <el-tag v-if="row.permissions?.includes('export')" type="warning" size="small" style="margin-right:4px">导出</el-tag>
              <span v-if="!row.permissions || row.permissions.length === 0" class="done-text">无</span>
              <el-button link type="primary" size="small" @click="openPerm(row)">编辑</el-button>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="最新上线时间" min-width="170">
          <template #default="{ row }">
            {{ formatTime(row.last_login_at) }}
          </template>
        </el-table-column>
        <el-table-column label="密码" width="150">
          <template #default="{ row }">
            <span class="pwd-mask">{{ row.password_mask || '••••••' }}</span>
            <el-button
              link
              type="primary"
              size="small"
              :disabled="row.role === 'admin'"
              @click="openReset(row)"
            >
              重置
            </el-button>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              :type="row.status === 'active' ? 'danger' : 'success'"
              size="small"
              :disabled="row.role === 'admin'"
              @click="toggleStatus(row)"
            >
              {{ row.status === 'active' ? '禁用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="pwd-note"
        title="密码安全说明"
        description="密码一律 bcrypt 加盐哈希存储，系统无法反推明文，因此只显示掩码。管理员可「重置」生成新密码，新密码仅在弹出框中显示这一次，请妥善转告成员并提醒其尽快修改。"
      />
    </el-card>

    <!-- ---------- 入会申请 ---------- -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="section-title">入会申请</div>
      </template>
      <el-table :data="applications" v-loading="loadingApps" border stripe size="small">
        <el-table-column prop="display_name" label="姓名" min-width="100" />
        <el-table-column prop="phone" label="联系电话" min-width="120" />
        <el-table-column prop="username" label="期望账号" min-width="120">
          <template #default="{ row }">{{ row.username || '—' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
        <el-table-column label="申请时间" min-width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : 'danger'"
              size="small"
            >
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button link type="success" size="small" @click="openApprove(row)">同意</el-button>
              <el-button link type="danger" size="small" @click="onReject(row)">拒绝</el-button>
            </template>
            <span v-else class="done-text">{{ row.reviewed_by || '—' }} 已处理</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ---------- 同意申请 ---------- -->
    <el-dialog v-model="approveVisible" title="同意申请" width="440px">
      <el-form label-width="110px">
        <el-form-item label="姓名">
          <span>{{ currentApp?.display_name }}</span>
        </el-form-item>
        <el-form-item label="联系电话">
          <span>{{ currentApp?.phone }}</span>
        </el-form-item>
        <el-form-item label="登录账号">
          <el-input v-model="approveForm.username" placeholder="不填则自动生成" />
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="approveForm.password" placeholder="不填则由系统生成随机强密码" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button type="primary" :loading="approving" @click="onApprove">确认同意</el-button>
      </template>
    </el-dialog>

    <!-- ---------- 密码回显（仅一次）---------- -->
    <el-dialog v-model="pwdVisible" title="请妥善保存密码" width="440px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="该密码仅显示这一次"
        description="关闭后无法再次查看，请立即复制并转告成员，提醒其登录后尽快修改。"
      />
      <div class="pwd-box">
        <div class="pwd-row">
          <span class="pwd-label">账号</span>
          <span class="pwd-value">{{ pwdResult.username }}</span>
        </div>
        <div class="pwd-row">
          <span class="pwd-label">密码</span>
          <span class="pwd-value strong">{{ pwdResult.password }}</span>
          <el-button size="small" @click="copyPwd">复制</el-button>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" @click="pwdVisible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <!-- ---------- 拒绝申请 ---------- -->
    <el-dialog v-model="rejectVisible" title="拒绝申请" width="420px">
      <el-form label-width="90px">
        <el-form-item label="拒绝原因">
          <el-input v-model="rejectReason" type="textarea" :rows="3" placeholder="选填，会记录到申请中" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rejectVisible = false">取消</el-button>
        <el-button type="danger" :loading="rejecting" @click="confirmReject">确认拒绝</el-button>
      </template>
    </el-dialog>

    <!-- ---------- 成员权限配置 ---------- -->
    <el-dialog v-model="permVisible" title="配置成员数据权限" width="440px">
      <div v-if="permForm.username" class="perm-user">成员：{{ permForm.username }}</div>
      <el-form label-width="92px">
        <el-form-item label="数据权限">
          <el-checkbox-group v-model="permForm.permissions">
            <el-checkbox label="import">导入 / 上传（含库存）</el-checkbox>
            <el-checkbox label="export">导出数据</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="权限说明"
        description="勾选后该成员即可使用前端对应的上传 / 导出按钮；未勾选则点击时提示无权限。管理员默认拥有全部权限。"
      />
      <template #footer>
        <el-button @click="permVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingPerm" @click="savePerm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveApplication,
  fetchApplications,
  fetchMembers,
  rejectApplication,
  resetMemberPassword,
  setMemberPermissions,
  setMemberStatus,
  type Application,
} from '@/api/auth'
import type { AuthUser } from '@/utils/auth'

const router = useRouter()

const overview = reactive({ total: 0, admin_count: 0, member_count: 0, pending_applications: 0, users: [] as AuthUser[] })
const applications = ref<Application[]>([])
const loadingMembers = ref(false)
const loadingApps = ref(false)

const approveVisible = ref(false)
const rejectVisible = ref(false)
const pwdVisible = ref(false)
const permVisible = ref(false)
const approving = ref(false)
const rejecting = ref(false)
const savingPerm = ref(false)
const currentApp = ref<Application | null>(null)
const rejectReason = ref('')
const approveForm = reactive({ username: '', password: '' })
const pwdResult = reactive({ username: '', password: '' })
const permForm = reactive({ uid: 0, username: '', permissions: [] as string[] })

function formatTime(v: string | null): string {
  if (!v) return '从未登录'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function statusText(s: string): string {
  return s === 'pending' ? '待审核' : s === 'approved' ? '已同意' : '已拒绝'
}

async function loadMembers() {
  loadingMembers.value = true
  try {
    const data = await fetchMembers()
    overview.total = data.total
    overview.admin_count = data.admin_count
    overview.member_count = data.member_count
    overview.pending_applications = data.pending_applications
    overview.users = data.users
  } catch (e: any) {
    ElMessage.error(e?.message || '加载成员失败')
  } finally {
    loadingMembers.value = false
  }
}

async function loadApps() {
  loadingApps.value = true
  try {
    const data = await fetchApplications()
    applications.value = data.applications
  } catch (e: any) {
    ElMessage.error(e?.message || '加载申请失败')
  } finally {
    loadingApps.value = false
  }
}

function loadAll() {
  loadMembers()
  loadApps()
}

function goBack() {
  router.push('/dashenlin')
}

// ---------- 审批 ----------
function openApprove(row: Application) {
  currentApp.value = row
  approveForm.username = row.username || ''
  approveForm.password = ''
  approveVisible.value = true
}

async function onApprove() {
  if (!currentApp.value) return
  approving.value = true
  try {
    const res = await approveApplication(currentApp.value.id, {
      username: approveForm.username.trim() || undefined,
      password: approveForm.password || undefined,
    })
    approveVisible.value = false
    pwdResult.username = res.user.username
    pwdResult.password = res.initial_password
    pwdVisible.value = true
    ElMessage.success('已同意申请，账号创建成功')
    loadAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '审批失败')
  } finally {
    approving.value = false
  }
}

function onReject(row: Application) {
  currentApp.value = row
  rejectReason.value = ''
  rejectVisible.value = true
}

async function confirmReject() {
  if (!currentApp.value) return
  rejecting.value = true
  try {
    await rejectApplication(currentApp.value.id, rejectReason.value)
    rejectVisible.value = false
    ElMessage.success('已拒绝该申请')
    loadAll()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    rejecting.value = false
  }
}

// ---------- 成员管理 ----------
function openReset(row: AuthUser) {
  ElMessageBox.prompt(
    `将为「${row.display_name}」重置密码。留空则由系统生成随机强密码。`,
    '重置密码',
    {
      confirmButtonText: '确认重置',
      cancelButtonText: '取消',
      inputPlaceholder: '留空则自动生成',
      inputType: 'password',
    },
  )
    .then(async ({ value }) => {
      const res = await resetMemberPassword(row.id, value?.trim() || undefined)
      pwdResult.username = res.username
      pwdResult.password = res.new_password
      pwdVisible.value = true
      ElMessage.success('密码已重置')
    })
    .catch(() => {
      /* 用户取消 */
    })
}

async function toggleStatus(row: AuthUser) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    await setMemberStatus(row.id, next)
    ElMessage.success(next === 'active' ? '已启用该成员' : '已禁用该成员')
    loadMembers()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  }
}

async function copyPwd() {
  try {
    await navigator.clipboard.writeText(pwdResult.password)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败，请手动记录')
  }
}

// ---------- 成员权限 ----------
function openPerm(row: AuthUser) {
  permForm.uid = row.id
  permForm.username = row.username
  permForm.permissions = [...(row.permissions || [])]
  permVisible.value = true
}

async function savePerm() {
  savingPerm.value = true
  try {
    await setMemberPermissions(permForm.uid, permForm.permissions)
    permVisible.value = false
    ElMessage.success('权限已更新')
    loadMembers()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    savingPerm.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.admin-page {
  max-width: 1400px;
  margin: 0 auto;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #1a4fc0;
}

.page-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: #909399;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.stat-row {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
  padding: 8px 0;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
}

.stat-value.admin {
  color: #f56c6c;
}
.stat-value.member {
  color: #409eff;
}
.stat-value.pending {
  color: #e6a23c;
}

.stat-label {
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}

.section-card {
  margin-bottom: 16px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
}

.pwd-mask {
  margin-right: 6px;
  color: #a8abb2;
  letter-spacing: 2px;
}

.pwd-note {
  margin-top: 12px;
}

.done-text {
  font-size: 12px;
  color: #a8abb2;
}

.pwd-box {
  margin-top: 14px;
}

.perm-user {
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.pwd-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.pwd-label {
  width: 44px;
  color: #909399;
  font-size: 13px;
}

.pwd-value {
  font-family: Consolas, Monaco, monospace;
  font-size: 15px;
  color: #303133;
}

.pwd-value.strong {
  font-weight: 700;
  color: #e6a23c;
}

@media (max-width: 768px) {
  .page-head {
    flex-direction: column;
  }
}
</style>
