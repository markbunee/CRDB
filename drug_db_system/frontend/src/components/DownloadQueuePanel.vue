<template>
  <el-card id="download-center" class="download-queue" shadow="never" :body-style="{ padding: '12px 14px' }">
    <div class="dq-header">
      <div class="dq-title">
        <el-icon><Download /></el-icon>
        <span>下载中心</span>
        <el-badge v-if="activeCount" :value="activeCount" type="primary" class="dq-badge" />
      </div>
      <el-button v-if="historyCount" link type="info" size="small" @click="clearExportHistory">
        清空已完成
      </el-button>
    </div>

    <div v-if="tasks.length === 0" class="dq-empty">
      <el-icon><DocumentChecked /></el-icon>
      <span>暂无导出任务</span>
    </div>

    <ul v-else class="dq-list">
      <li v-for="t in tasks" :key="t.id" class="dq-item" :class="t.status">
        <span class="dq-status">
          <el-icon v-if="t.status === 'running'" class="spin"><Loading /></el-icon>
          <el-icon v-else-if="t.status === 'done'" color="#67c23a"><CircleCheckFilled /></el-icon>
          <el-icon v-else-if="t.status === 'failed'" color="#f56c6c"><CircleCloseFilled /></el-icon>
          <el-icon v-else-if="t.status === 'expired'" color="#c0c4cc"><InfoFilled /></el-icon>
          <el-icon v-else color="#909399"><Clock /></el-icon>
        </span>

        <div class="dq-meta">
          <div class="dq-name" :title="t.name">{{ t.name }}</div>
          <div class="dq-file">
            <span v-if="t.status === 'running'">后台生成中，完成后会通知你…</span>
            <span v-else-if="t.status === 'queued'">排队中，等待服务器空闲…</span>
            <span v-else-if="t.status === 'done'" :title="t.filename">
              {{ t.filename || '已生成' }}<template v-if="sizeText(t)">（{{ sizeText(t) }}）</template>
            </span>
            <span v-else :title="t.filename">{{ t.filename || '' }}</span>
          </div>
          <div v-if="t.error" class="dq-error">{{ t.error }}</div>
        </div>

        <div class="dq-actions">
          <el-button
            v-if="t.status === 'done'"
            link
            type="primary"
            size="small"
            :icon="Download"
            @click="downloadJob(t.id)"
          >下载</el-button>
          <el-button
            v-if="t.status === 'failed'"
            link
            type="warning"
            size="small"
            :icon="RefreshRight"
            @click="retryExport(t.id)"
          >重试</el-button>
          <el-button
            link
            type="info"
            size="small"
            :icon="Close"
            @click="removeExport(t.id)"
          />
        </div>
      </li>
    </ul>

    <div v-if="tasks.length" class="dq-tip">文件在服务器上保留 1 小时，过期自动清理</div>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  Download,
  DocumentChecked,
  Loading,
  CircleCheckFilled,
  CircleCloseFilled,
  InfoFilled,
  Clock,
  RefreshRight,
  Close,
} from '@element-plus/icons-vue'
import {
  useDownloadQueue,
  type DownloadTask,
} from '@/composables/downloadQueue'

const { tasks, downloadJob, retryExport, removeExport, clearExportHistory } = useDownloadQueue()

const activeCount = computed(
  () => tasks.value.filter((t) => t.status === 'queued' || t.status === 'running').length,
)
const historyCount = computed(
  () => tasks.value.filter((t) => ['done', 'failed', 'expired'].includes(t.status)).length,
)

function sizeText(t: DownloadTask): string {
  if (!t.size) return ''
  if (t.size < 1024) return `${t.size} B`
  if (t.size < 1024 * 1024) return `${(t.size / 1024).toFixed(1)} KB`
  return `${(t.size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style scoped>
.download-queue {
  margin-bottom: 12px;
}
.dq-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.dq-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.dq-badge {
  margin-left: 2px;
}
.dq-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 18px 0;
  color: #c0c4cc;
  font-size: 12px;
}
.dq-empty .el-icon {
  font-size: 26px;
}
.dq-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.dq-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafbfc;
}
.dq-item.queued,
.dq-item.running {
  border-color: #b3d4ff;
  background: #f3f8ff;
}
.dq-item.done {
  border-color: #c8e6c9;
}
.dq-item.failed {
  border-color: #f5c6c6;
  background: #fef0f0;
}
.dq-item.expired {
  opacity: 0.6;
}
.dq-status .el-icon {
  font-size: 18px;
}
.spin {
  animation: dq-spin 1s linear infinite;
}
@keyframes dq-spin {
  to {
    transform: rotate(360deg);
  }
}
.dq-meta {
  flex: 1;
  min-width: 0;
}
.dq-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dq-file {
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dq-error {
  font-size: 11px;
  color: #f56c6c;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dq-actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 2px;
}
.dq-tip {
  margin-top: 8px;
  font-size: 11px;
  color: #c0c4cc;
}
</style>
