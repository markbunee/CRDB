<template>
  <div class="app">
    <header class="app-header">
      <div class="header-content">
        <div class="header-left">
          <h1 class="title">医药销售数据库管理系统</h1>
          <p class="subtitle">大参林 · 高济 · 海王 — 三库数据查询与维护</p>
        </div>
        <div v-if="isLoggedIn()" class="header-right">
          <span class="user-chip">
            {{ user?.display_name || user?.username }}
            <el-tag size="small" :type="isAdmin() ? 'danger' : 'info'" effect="dark">
              {{ isAdmin() ? '管理员' : '成员' }}
            </el-tag>
          </span>
          <el-button size="small" @click="onLogout">退出登录</el-button>
        </div>
      </div>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { clearSession, currentUser, isAdmin, isLoggedIn } from '@/utils/auth'
import { logout } from '@/api/auth'

const router = useRouter()
const user = computed(() => currentUser())

async function onLogout() {
  try {
    await logout()
  } catch {
    /* 服务端无状态登出，忽略失败 */
  }
  clearSession()
  ElMessage.success('已退出登录')
  router.replace('/login')
}
</script>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-header {
  width: 100%;
  background: linear-gradient(135deg, #1a4fc0 0%, #3b6bd6 100%);
  color: #fff;
  padding: 20px 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.header-content {
  max-width: 1600px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.title {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 6px;
  letter-spacing: 0.5px;
}

.subtitle {
  font-size: 13px;
  opacity: 0.85;
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.app-main {
  flex: 1;
  width: 100%;
  padding: 20px 24px 40px;
}

@media (max-width: 768px) {
  .header-content {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
