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
      <button class="more-item" type="button" @click="emit('import')">
        <el-icon :size="20" color="#67c23a"><Upload /></el-icon>
        <span>导入数据</span>
      </button>
      <button class="more-item" type="button" @click="emit('export')">
        <el-icon :size="20" color="#409eff"><Download /></el-icon>
        <span>导出查询结果</span>
      </button>
      <button class="more-item danger" type="button" @click="emit('clear')">
        <el-icon :size="20" color="#f56c6c"><Delete /></el-icon>
        <span>清空数据库数据</span>
      </button>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { Upload, Download, Delete } from '@element-plus/icons-vue'

const visible = defineModel<boolean>('visible', { required: true })
const emit = defineEmits<{
  (e: 'import'): void
  (e: 'export'): void
  (e: 'clear'): void
}>()
</script>

<style scoped>
.more-sheet-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px 12px;
}

.more-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 12px;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  font-size: 14px;
  color: #303133;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.more-item:active {
  background: #f5f7fa;
}

.more-item.danger {
  color: #f56c6c;
}
</style>
