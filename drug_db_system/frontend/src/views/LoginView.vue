<template>
  <div class="login-page">
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
            <el-form-item label="登录账号">
              <el-input
                v-model="form.username"
                placeholder="请输入登录账号"
                :prefix-icon="User"
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
            description="填写姓名与联系电话提交申请，管理员在后台同意后即可登录（初始密码由管理员设定或系统生成）。"
            class="apply-tip"
          />
          <el-form label-position="top" @submit.prevent="onApply">
            <el-form-item label="姓名" required>
              <el-input v-model="applyForm.display_name" placeholder="请输入真实姓名" size="large" />
            </el-form-item>
            <el-form-item label="联系电话" required>
              <el-input v-model="applyForm.phone" placeholder="请输入联系电话" size="large" />
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
import { Lock, User } from '@element-plus/icons-vue'
import { applyMember, login } from '@/api/auth'
import { setSession } from '@/utils/auth'

const router = useRouter()
const route = useRoute()

const activeTab = ref('login')
const loading = ref(false)
const applyLoading = ref(false)

const form = reactive({ username: '', password: '' })
const applyForm = reactive({
  display_name: '',
  phone: '',
  username: '',
  note: '',
})

async function onLogin() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入账号和密码')
    return
  }
  loading.value = true
  try {
    const res = await login(form.username.trim(), form.password)
    setSession(res.token, res.user)
    ElMessage.success(`欢迎，${res.user.display_name || res.user.username}`)
    const redirect = (route.query.redirect as string) || '/dashenlin'
    router.replace(redirect)
    if (res.user.must_change_password) {
      ElMessage.warning('您当前使用的是初始密码，建议尽快修改')
    }
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
  applyLoading.value = true
  try {
    await applyMember({
      display_name: applyForm.display_name.trim(),
      phone: applyForm.phone.trim(),
      username: applyForm.username.trim(),
      note: applyForm.note.trim(),
    })
    ElMessage.success('申请已提交，请等待管理员审核')
    applyForm.display_name = ''
    applyForm.phone = ''
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
</style>
