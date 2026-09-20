<template>
  <nav class="mobile-nav">
    <button class="nav-item" type="button" @click="$emit('query')">
      <el-icon :size="22"><Search /></el-icon>
      <span>查询</span>
    </button>
    <button class="nav-item" type="button" @click="$emit('filter')">
      <span class="nav-icon-wrap">
        <el-icon :size="22"><Filter /></el-icon>
        <span v-if="filterActive" class="nav-badge"></span>
      </span>
      <span>筛选</span>
    </button>
    <button class="nav-item" type="button" @click="$emit('stats')">
      <el-icon :size="22"><DataLine /></el-icon>
      <span>统计</span>
    </button>
    <button v-if="showDashboard" class="nav-item" type="button" @click="$emit('dashboard')">
      <el-icon :size="22"><TrendCharts /></el-icon>
      <span>看板</span>
    </button>
    <button class="nav-item" type="button" @click="$emit('more')">
      <el-icon :size="22"><Menu /></el-icon>
      <span>更多</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
import { Search, Filter, DataLine, TrendCharts, Menu } from '@element-plus/icons-vue'

defineProps<{ showDashboard?: boolean; filterActive?: boolean }>()
defineEmits<{
  (e: 'query'): void
  (e: 'filter'): void
  (e: 'stats'): void
  (e: 'dashboard'): void
  (e: 'more'): void
}>()
</script>

<style scoped>
.mobile-nav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 2000;
  display: none; /* 仅手机端显示，见媒体查询 */
  height: 56px;
  padding-bottom: env(safe-area-inset-bottom, 0);
  background: #fff;
  border-top: 1px solid #e4e7ed;
  box-shadow: 0 -2px 12px rgba(0, 0, 0, 0.08);
}

.nav-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  height: 100%;
  border: none;
  background: transparent;
  color: #606266;
  font-size: 11px;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.15s;
}

.nav-item:active {
  background: #f0f5ff;
}

.nav-item span {
  line-height: 1;
}

.nav-icon-wrap {
  position: relative;
  display: inline-flex;
  line-height: 1;
}

.nav-badge {
  position: absolute;
  top: -2px;
  right: -4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f56c6c;
  border: 1.5px solid #fff;
}

@media (max-width: 768px) {
  .mobile-nav {
    display: flex;
  }
}
</style>
