<template>
  <div class="product-map-panel">
    <div class="panel-toolbar">
      <div class="toolbar-text">
        <div class="panel-title">品类映射表（商品编码 → 中文名 / 开票价）</div>
        <div class="panel-hint">
          无映射的编码保持原编码显示；「实销金额」= 盒数 × 开票价，未配置价格的品类不计入金额。
          保存后立即对当前库的统计、候选搜索与导出生效，无需重启服务。
        </div>
      </div>
      <div class="toolbar-actions">
        <el-button size="small" :disabled="loading" @click="load">放弃修改</el-button>
        <el-button size="small" type="primary" plain @click="addRow">
          <el-icon><Plus /></el-icon> 新增一行
        </el-button>
        <el-button size="small" type="primary" :loading="saving" @click="handleSave">
          保存
        </el-button>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      border
      size="small"
      class="map-table"
      empty-text="暂无映射，点击右上角新增"
    >
      <el-table-column type="index" label="#" width="56" align="center" />
      <el-table-column label="商品编码" width="200">
        <template #default="{ row }">
          <el-input v-model="row.code" size="small" placeholder="如 8106225" />
        </template>
      </el-table-column>
      <el-table-column label="品类中文名" min-width="220">
        <template #default="{ row }">
          <el-input v-model="row.name" size="small" placeholder="如 易善复" />
        </template>
      </el-table-column>
      <el-table-column label="开票价（元）" width="190">
        <template #default="{ row }">
          <el-input-number
            v-model="row.price"
            size="small"
            :min="0"
            :precision="2"
            :controls="false"
            placeholder="选填，用于实销金额"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" align="center">
        <template #default="{ $index }">
          <el-button link type="danger" size="small" @click="rows.splice($index, 1)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
/**
 * 品类映射表（统计侧边栏功能页）：
 * 维护 商品编码 → 品类中文名 / 开票价 的映射，整体保存（后端全量替换）。
 * 开票价供「实销金额」统计使用（SUM(数量 × 开票价)）。
 */
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import {
  fetchProductMap,
  saveProductMap,
  type ProductMapItem,
} from '@/api/client'
import { logger } from '@/utils/logger'

const props = defineProps<{ dbKey: string }>()

const loading = ref(false)
const saving = ref(false)
/** 可编辑行；code / name 为空的行保存时会被后端清洗掉 */
const rows = ref<ProductMapItem[]>([])

function addRow() {
  rows.value.push({ code: '', name: '', price: null })
}

async function load() {
  loading.value = true
  try {
    const res = await fetchProductMap(props.dbKey)
    rows.value = res.items.map((i) => ({ ...i, price: i.price ?? null }))
  } catch (e: any) {
    ElMessage.error('加载映射失败: ' + e.message)
    logger.error('品类映射加载失败: ' + e.message, 'ProductMapPanel')
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  // 编码去重（保留最后一条）
  const valid = rows.value.filter((r) => r.code.trim() && r.name.trim())
  const map = new Map<string, ProductMapItem>()
  for (const r of valid) {
    map.set(r.code.trim(), {
      code: r.code.trim(),
      name: r.name.trim(),
      price: r.price ?? null,
    })
  }
  const items = Array.from(map.values())

  saving.value = true
  try {
    await saveProductMap(props.dbKey, items)
    ElMessage.success(`已保存 ${items.length} 条映射`)
  } catch (e: any) {
    ElMessage.error('保存失败: ' + e.message)
    logger.error('品类映射保存失败: ' + e.message, 'ProductMapPanel')
  } finally {
    saving.value = false
  }
}

onMounted(load)

// 切库后加载对应库的映射
watch(() => props.dbKey, load)
</script>

<style scoped>
.product-map-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}

.panel-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.toolbar-text {
  min-width: 0;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 4px;
}

.panel-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.toolbar-actions {
  flex-shrink: 0;
  display: flex;
  gap: 8px;
}

.map-table {
  flex: 1;
}
</style>
