<template>
  <el-drawer
    v-model="visible"
    title="更多操作"
    direction="b"
    size="auto"
    :close-on-click-modal="true"
    class="mobile-more-sheet"
  >
    <div class="more-sheet-body">
      <button v-if="showHome" class="more-item" type="button" @click="emit('home')">
        <el-icon :size="20" color="#3b6bd6"><HomeFilled /></el-icon>
        <span>返回主界面</span>
      </button>
      <button class="more-item" type="button" @click="emit('import')">
        <el-icon :size="20" color="#67c23a"><Upload /></el-icon>
        <span>导入数据</span>
      </button>
      <button class="more-item" type="button" @click="emit('export')">
        <el-icon :size="20" color="#409eff"><Download /></el-icon>
        <span>导出查询结果</span>
      </button>
      <button v-if="showAdmin" class="more-item" type="button" @click="emit('admin')">
        <el-icon :size="20" color="#e6a23c"><Setting /></el-icon>
        <span>后台管理</span>
      </button>
      <button class="more-item danger" type="button" @click="emit('clear')">
        <el-icon :size="20" color="#f56c6c"><Delete /></el-icon>
        <span>清空数据库数据</span>
      </button>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { Upload, Download, Delete, Setting, HomeFilled } from '@element-plus/icons-vue'

defineProps<{
  /** 是否展示「返回主界面」 */
  showHome?: boolean
  /** 是否展示「后台管理」（仅管理员） */
  showAdmin?: boolean
}>()

const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{
  (e: 'import'): void
  (e: 'export'): void
  (e: 'clear'): void
  (e: 'admin'): void
  (e: 'home'): void
}>()
</script>

<style scoped>
.more-sheet-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 0 calc(12px + env(safe-area-inset-bottom, 0px));
}

.more-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 54px;
  padding: 14px;
  background: #f7f8fa;
  border: 1px solid transparent;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.more-item:active {
  background: #eef1f6;
}

.more-item.danger {
  color: #f56c6c;
  background: #fdf6f6;
}

.more-item.danger:active {
  background: #fbeaea;
}
</style>
