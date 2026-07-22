<template>
  <div class="app">
    <header class="app-header">
      <div class="header-content">
        <h1 class="title">医药销售数据库管理系统</h1>
        <p class="subtitle">大参林 · 高济 · 海王 — 三库数据查询与维护</p>
      </div>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
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

.app-main {
  flex: 1;
  width: 100%;
  padding: 20px 24px 40px;
}
</style>
