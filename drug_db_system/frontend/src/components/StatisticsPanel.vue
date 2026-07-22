<template>
  <el-drawer
    v-model="drawerVisible"
    title="统计分析"
    direction="rtl"
    size="86%"
    :with-header="true"
    class="stats-drawer"
  >
    <div class="stats-layout">
      <!-- 左侧功能菜单 -->
      <aside class="stats-sidebar">
        <div class="sidebar-title">功能菜单</div>
        <ul class="menu-list">
          <li
            v-for="item in menuItems"
            :key="item.key"
            class="menu-item"
            :class="{ active: activeFunc === item.key }"
            @click="activeFunc = item.key"
          >
            <el-icon class="menu-icon"><component :is="item.icon" /></el-icon>
            <div class="menu-text">
              <div class="menu-name">{{ item.name }}</div>
              <div class="menu-desc">{{ item.desc }}</div>
            </div>
          </li>
        </ul>
      </aside>

      <!-- 右侧内容区 -->
      <section class="stats-content">
        <StoreCityStats
          v-if="activeFunc === 'store_city'"
          :db-key="dbKey"
        />
      </section>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Shop, TrendCharts } from '@element-plus/icons-vue'
import StoreCityStats from './stats/StoreCityStats.vue'

const props = defineProps<{
  modelValue: boolean
  dbKey: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
}>()

const drawerVisible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const menuItems = [
  {
    key: 'store_city',
    name: '实销门店数统计',
    desc: '城市维度 · 月度/区间',
    icon: Shop,
  },
  {
    key: 'placeholder_trend',
    name: '销售趋势分析',
    desc: '敬请期待',
    icon: TrendCharts,
  },
]

const activeFunc = ref('store_city')
</script>

<style scoped>
.stats-layout {
  display: flex;
  height: 100%;
  gap: 0;
}

/* ---------- 左侧功能栏（窄） ---------- */
.stats-sidebar {
  width: 200px;
  flex-shrink: 0;
  background: #fafbfc;
  border-right: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.sidebar-title {
  padding: 16px 18px 10px;
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.menu-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 8px;
}

.menu-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid transparent;
}

.menu-item:hover {
  background: #fff;
  border-color: #e4e7ed;
}

.menu-item.active {
  background: #fff;
  border-color: #3b6bd6;
  box-shadow: 0 0 0 1px #3b6bd6 inset;
}

.menu-icon {
  font-size: 18px;
  color: #909399;
  margin-top: 2px;
  flex-shrink: 0;
}

.menu-item.active .menu-icon {
  color: #3b6bd6;
}

.menu-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.menu-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  line-height: 1.3;
}

.menu-item.active .menu-name {
  color: #3b6bd6;
}

.menu-desc {
  font-size: 11px;
  color: #c0c4cc;
  line-height: 1.3;
}

/* ---------- 右侧内容区（宽） ---------- */
.stats-content {
  flex: 1;
  min-width: 0;
  padding: 20px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
