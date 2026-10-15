<template>
  <div class="home-page">
    <!-- 欢迎横幅 -->
    <div class="welcome card">
      <div class="welcome-left">
        <h2 class="welcome-title">欢迎使用医药销售数据库管理系统</h2>
        <p class="welcome-sub">
          请选择以下功能模块开始使用 — 当前登录：
          <strong>{{ user?.display_name || user?.username }}</strong>
        </p>
      </div>
      <el-tag v-if="isAdmin()" type="danger" effect="dark" size="large">管理员</el-tag>
      <el-tag v-else type="info" effect="dark" size="large">成员</el-tag>
    </div>

    <!-- 功能模块卡片 -->
    <div class="modules">
      <div
        v-for="m in modules"
        :key="m.key"
        class="module-card"
        :class="{ disabled: !m.enabled }"
        @click="onEnter(m)"
      >
        <div class="module-icon" :style="{ background: m.color }">
          <el-icon :size="34"><component :is="m.icon" /></el-icon>
        </div>
        <div class="module-body">
          <div class="module-head">
            <span class="module-name">{{ m.name }}</span>
            <el-tag v-if="!m.enabled" type="info" size="small" effect="plain">敬请期待</el-tag>
            <el-tag v-else type="success" size="small" effect="plain">已开放</el-tag>
          </div>
          <p class="module-desc">{{ m.desc }}</p>
        </div>
        <div class="module-enter">
          <el-icon :size="18"><ArrowRight /></el-icon>
        </div>
      </div>
    </div>

    <!-- AI 数据分析助手（主界面） -->
    <section class="ai-section card">
      <div class="ai-section-head">
        <div class="ai-section-title">
          <el-icon><MagicStick /></el-icon>
          <span>AI 数据分析助手</span>
          <el-tag type="warning" size="small" effect="light">内测预览</el-tag>
        </div>
        <p class="ai-section-sub">
          用自然语言问数；左侧挑一个案例，即可看到「提问 → 分析 → 结论」的完整交互演示。
        </p>
      </div>
      <AiAssistantPanel />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight,
  Goods,
  Shop,
  OfficeBuilding,
  MagicStick,
} from '@element-plus/icons-vue'
import { currentUser, isAdmin } from '@/utils/auth'
import AiAssistantPanel from '@/components/AiAssistantPanel.vue'

const router = useRouter()
const user = computed(() => currentUser())

interface Module {
  key: string
  name: string
  desc: string
  icon: any
  color: string
  enabled: boolean
  to: string
}

const modules: Module[] = [
  {
    key: 'sales',
    name: '实销查看',
    desc: '查询各门店实际销售数据，支持按日期、商品、城市等维度筛选与导出。',
    icon: Goods,
    color: 'linear-gradient(135deg,#3b6bd6,#1a4fc0)',
    enabled: true,
    to: '/dashenlin',
  },
  {
    key: 'store',
    name: '门店购进',
    desc: '查看门店向总部/供应商的购进数据，掌握门店进货情况。',
    icon: Shop,
    color: 'linear-gradient(135deg,#13c2c2,#08979c)',
    enabled: false,
    to: '/store-purchase',
  },
  {
    key: 'hq',
    name: '总部购进',
    desc: '查看总部统一采购数据，统计各品类与供应商的购进规模。',
    icon: OfficeBuilding,
    color: 'linear-gradient(135deg,#722ed1,#531dab)',
    enabled: false,
    to: '/hq-purchase',
  },
]

function onEnter(m: Module) {
  if (!m.enabled) {
    ElMessage.info(`「${m.name}」功能正在开发中，敬请期待`)
    return
  }
  router.push(m.to)
}
</script>

<style scoped>
.home-page {
  max-width: 1200px;
  margin: 0 auto;
}

.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
}

.welcome {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 24px 28px;
  margin-bottom: 24px;
  background: linear-gradient(135deg, #f5f8ff 0%, #eef3ff 100%);
}

.welcome-title {
  margin: 0 0 8px;
  font-size: 22px;
  font-weight: 600;
  color: #1a4fc0;
}

.welcome-sub {
  margin: 0;
  font-size: 14px;
  color: #606266;
}

.welcome-sub strong {
  color: #303133;
}

.modules {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

.ai-section {
  padding: 22px 24px 24px;
}

.ai-section-head {
  margin-bottom: 16px;
}

.ai-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 600;
  color: #1a1a2e;
}

.ai-section-title .el-icon {
  font-size: 20px;
  color: #7c4dff;
}

.ai-section-sub {
  margin: 8px 0 0;
  font-size: 13px;
  color: #909399;
}

.module-card {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 24px 22px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.04);
}

.module-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 22px rgba(26, 79, 192, 0.14);
  border-color: #a0c1ff;
}

.module-card.disabled {
  cursor: not-allowed;
  opacity: 0.72;
}

.module-card.disabled:hover {
  transform: none;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.04);
  border-color: #ebeef5;
}

.module-icon {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

.module-body {
  flex: 1;
  min-width: 0;
}

.module-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.module-name {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.module-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #909399;
}

.module-enter {
  flex-shrink: 0;
  align-self: center;
  color: #c0c4cc;
  transition: all 0.2s ease;
}

.module-card:hover .module-enter {
  color: #3b6bd6;
  transform: translateX(3px);
}

@media (max-width: 860px) {
  .modules {
    grid-template-columns: 1fr;
  }
  .welcome {
    flex-direction: column;
    align-items: flex-start;
  }
}

/* ---------- 手机端（≤768px） ---------- */
@media (max-width: 768px) {
  .welcome {
    padding: 16px 18px;
    margin-bottom: 16px;
    gap: 10px;
  }

  .welcome-title {
    font-size: 18px;
  }

  .welcome-sub {
    font-size: 13px;
  }

  .modules {
    gap: 12px;
  }

  .module-card {
    padding: 14px;
    gap: 12px;
    border-radius: 12px;
  }

  /* 触摸态用背景反馈替代桌面端的位移悬浮 */
  .module-card:active {
    background: #f7f9fc;
  }

  .module-icon {
    width: 48px;
    height: 48px;
    border-radius: 13px;
  }

  .module-icon :deep(.el-icon) {
    font-size: 26px;
  }

  .module-name {
    font-size: 15px;
  }

  .module-desc {
    font-size: 12px;
    line-height: 1.6;
  }
}
</style>
