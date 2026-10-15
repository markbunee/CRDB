<template>
  <div class="login-page">
    <!-- 手机端：品牌区放在蓝色渐变上，白色表单面板从下方浮起（对齐移动端设计稿 01） -->
    <div class="login-brand">
      <div class="brand-logo">
        <el-icon :size="34"><Box /></el-icon>
      </div>
      <div class="brand-title">医药销售数据库管理系统</div>
      <div class="brand-sub">大参林 · 高济 · 海王 — 三库数据查询与维护</div>
    </div>

    <el-card class="login-card" shadow="always">
      <template #header>
        <div class="card-header">
          <div class="card-title">医药销售数据库管理系统</div>
          <div class="card-sub">大参林 · 高济 · 海王 — 三库数据查询与维护</div>
        </div>
      </template>

      <el-tabs v-model="activeTab">
        <!-- ---------- 登录 ---------- -->
        <el-tab-pane label="账号登录" name="login">
          <el-form label-position="top" @submit.prevent="onLogin">
            <el-form-item label="手机号">
              <el-input
                v-model="form.phone"
                placeholder="请输入手机号"
                :prefix-icon="Iphone"
                size="large"
                autocomplete="username"
                @keyup.enter="onLogin"
              />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="form.password"
                type="password"
                placeholder="请输入密码"
                :prefix-icon="Lock"
                size="large"
                show-password
                autocomplete="current-password"
                @keyup.enter="onLogin"
              />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              class="submit-btn"
              :loading="loading"
              @click="onLogin"
            >
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <!-- ---------- 申请账号 ---------- -->
        <el-tab-pane label="申请账号" name="apply">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="新成员需提交申请"
            description="填写姓名、联系电话并自行设置密码后提交申请；管理员在后台同意后，即可用「手机号 + 密码」登录。密码由您自己设置，管理员无法查看。"
            class="apply-tip"
          />
          <el-form label-position="top" @submit.prevent="onApply">
            <el-form-item label="姓名" required>
              <el-input v-model="applyForm.display_name" placeholder="请输入真实姓名" size="large" />
            </el-form-item>
            <el-form-item label="联系电话" required>
              <el-input v-model="applyForm.phone" placeholder="请输入联系电话" size="large" />
            </el-form-item>
            <el-form-item label="设置密码" required>
              <el-input
                v-model="applyForm.password"
                type="password"
                placeholder="至少 8 位，需同时包含字母和数字"
                size="large"
                show-password
                autocomplete="new-password"
              />
            </el-form-item>
            <el-form-item label="确认密码" required>
              <el-input
                v-model="applyForm.password2"
                type="password"
                placeholder="请再次输入密码"
                size="large"
                show-password
                autocomplete="new-password"
                @keyup.enter="onApply"
              />
            </el-form-item>
            <el-form-item label="期望登录账号（选填）">
              <el-input v-model="applyForm.username" placeholder="不填则由管理员分配" size="large" />
            </el-form-item>
            <el-form-item label="备注（选填）">
              <el-input v-model="applyForm.note" type="textarea" :rows="2" placeholder="可说明申请用途" />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              class="submit-btn"
              :loading="applyLoading"
              @click="onApply"
            >
              提交申请
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <div class="login-foot">登录即代表您已获授权访问本系统数据</div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, Iphone, Box } from '@element-plus/icons-vue'
import { applyMember, login } from '@/api/auth'
import { setSession } from '@/utils/auth'

const router = useRouter()
const route = useRoute()

const activeTab = ref('login')
const loading = ref(false)
const applyLoading = ref(false)

const form = reactive({ phone: '', password: '' })
const applyForm = reactive({
  display_name: '',
  phone: '',
  password: '',
  password2: '',
  username: '',
  note: '',
})

async function onLogin() {
  if (!form.phone.trim() || !form.password) {
    ElMessage.warning('请输入手机号和密码')
    return
  }
  loading.value = true
  try {
    const res = await login(form.phone.trim(), form.password)
    setSession(res.token, res.user)
    ElMessage.success(`欢迎，${res.user.display_name || res.user.username}`)
    const redirect = (route.query.redirect as string) || '/home'
    router.replace(redirect)
  } catch (e: any) {
    ElMessage.error(e?.message || '登录失败')
  } finally {
    loading.value = false
  }
}

async function onApply() {
  if (!applyForm.display_name.trim()) {
    ElMessage.warning('请填写姓名')
    return
  }
  if (!applyForm.phone.trim()) {
    ElMessage.warning('请填写联系电话')
    return
  }
  if (!applyForm.password) {
    ElMessage.warning('请设置登录密码')
    return
  }
  if (applyForm.password.length < 8) {
    ElMessage.warning('密码至少 8 位，且需同时包含字母和数字')
    return
  }
  if (applyForm.password !== applyForm.password2) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  applyLoading.value = true
  try {
    await applyMember({
      display_name: applyForm.display_name.trim(),
      phone: applyForm.phone.trim(),
      password: applyForm.password,
      username: applyForm.username.trim(),
      note: applyForm.note.trim(),
    })
    ElMessage.success('申请已提交，请等待管理员审核')
    applyForm.display_name = ''
    applyForm.phone = ''
    applyForm.password = ''
    applyForm.password2 = ''
    applyForm.username = ''
    applyForm.note = ''
    activeTab.value = 'login'
  } catch (e: any) {
    ElMessage.error(e?.message || '提交失败')
  } finally {
    applyLoading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a4fc0 0%, #3b6bd6 100%);
  padding: 24px;
}

.login-card {
  width: 100%;
  max-width: 460px;
}

.card-header {
  text-align: center;
}

.card-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a4fc0;
}

.card-sub {
  margin-top: 6px;
  font-size: 13px;
  color: #909399;
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
}

.apply-tip {
  margin-bottom: 16px;
}

.login-foot {
  margin-top: 16px;
  text-align: center;
  font-size: 12px;
  color: #a8abb2;
}

/* 品牌区：仅手机端显示，桌面端由卡片自带头部呈现 */
.login-brand {
  display: none;
}

.brand-logo {
  width: 72px;
  height: 72px;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: rgba(255, 255, 255, 0.18);
}

.brand-title {
  font-size: 19px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.5px;
}

.brand-sub {
  font-size: 12px;
  color: #dce6fa;
}

/* ---------- 手机端（≤768px） ---------- */
@media (max-width: 768px) {
  .login-page {
    flex-direction: column;
    align-items: stretch;
    justify-content: flex-start;
    padding: 0;
    /* 顶部安全区（刘海屏 / 挖孔屏） */
    padding-top: env(safe-area-inset-top, 0px);
  }

  .login-brand {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    padding: 34px 24px 28px;
  }

  /* 白色面板从下方浮起，铺满剩余高度（对齐设计稿） */
  .login-page :deep(.el-card) {
    flex: 1;
    max-width: none;
    border-radius: 26px 26px 0 0;
    border: none;
    box-shadow: 0 -6px 24px rgba(0, 0, 0, 0.12);
  }

  /* 卡片自带头部与品牌区重复，手机端隐藏 */
  .login-page :deep(.el-card__header) {
    display: none;
  }

  .login-page :deep(.el-card__body) {
    padding: 24px 18px calc(24px + env(safe-area-inset-bottom, 0px));
  }

  .card-title {
    font-size: 18px;
  }

  .card-sub {
    font-size: 12px;
  }

  .login-page :deep(.el-form-item__label) {
    padding-bottom: 6px;
  }

  .login-page :deep(.el-tabs__item) {
    font-size: 14px;
  }

  .submit-btn {
    height: 48px;
    margin-top: 4px;
    font-size: 15px;
  }
}
</style>
