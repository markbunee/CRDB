<template>
  <div class="placeholder-page">
    <el-result icon="info" :title="title" sub-title="该功能模块正在开发中，敬请期待。">
      <template #extra>
        <el-button type="primary" @click="goHome">返回主界面</el-button>
      </template>
    </el-result>

    <div class="placeholder-desc card">
      <h3>功能说明</h3>
      <p>{{ desc }}</p>
      <ul>
        <li>当前版本已开放「实销查看」模块，可在主界面点击进入。</li>
        <li>本模块上线后将支持数据的查询、导入、统计与导出。</li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const title = computed(() => (route.meta?.title as string) || '功能开发中')
const desc = computed(() => (route.meta?.desc as string) || '该模块正在建设中。')

function goHome() {
  router.push('/home')
}
</script>

<style scoped>
.placeholder-page {
  max-width: 900px;
  margin: 0 auto;
}

.card {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.06);
  padding: 20px 28px;
}

.placeholder-desc h3 {
  margin: 0 0 12px;
  font-size: 16px;
  color: #303133;
}

.placeholder-desc p {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.7;
  color: #606266;
}

.placeholder-desc ul {
  margin: 0;
  padding-left: 20px;
  color: #909399;
  font-size: 13px;
  line-height: 1.9;
}
</style>
