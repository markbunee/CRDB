<template>
  <div class="ti-wrap">
    <!-- 功能切换 + 共享库存上传（动销率与库存情况共用同一份库存文件） -->
    <div class="ti-bar">
      <div class="ti-bar-left">
        <el-radio-group v-model="mode" size="small">
          <el-radio-button label="turnover">动销率</el-radio-button>
          <el-radio-button label="inventory">库存情况</el-radio-button>
        </el-radio-group>
        <div v-if="supportsStoreType" class="store-type-switch">
          <span class="store-type-label">门店类型</span>
          <el-radio-group v-model="storeType" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="chain">连锁</el-radio-button>
            <el-radio-button value="franchise">加盟</el-radio-button>
          </el-radio-group>
        </div>
      </div>
      <div class="ti-bar-right">
        <el-upload
          :http-request="handleImport"
          :show-file-list="false"
          accept=".xlsx,.xls,.csv"
        >
          <el-button type="warning" :loading="importing">
            <el-icon><Upload /></el-icon> 更新库存（覆盖）
          </el-button>
        </el-upload>
      </div>
    </div>

    <TurnoverStats v-if="mode === 'turnover'" ref="childRef" :db-key="dbKey" :store-type="storeType" />
    <InventoryStats v-else ref="childRef" :db-key="dbKey" :store-type="storeType" />
  </div>
</template>

<script setup lang="ts">
/**
 * 动销率库存情况（合并视图）：
 * - 动销率 与 库存情况 共用同一份库存文件，这里只放一个「更新库存（覆盖）」上传入口；
 * - 通过功能切换在两种分析视图间切换，切换即重新拉取对应数据。
 */
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { importInventory, type DbMeta } from '@/api/client'
import { getDbConfig } from '@/utils/dbConfig'
import { logger } from '@/utils/logger'
import TurnoverStats from './TurnoverStats.vue'
import InventoryStats from './InventoryStats.vue'

const props = defineProps<{ dbKey: string }>()

const mode = ref<'turnover' | 'inventory'>('turnover')
const importing = ref(false)
// 门店类型筛选（连锁/加盟/全部），仅支持该维度的库（如大参林）显示
const storeType = ref<'all' | 'chain' | 'franchise'>('all')
const dbConfig = ref<DbMeta | null>(null)
const supportsStoreType = computed(() => !!dbConfig.value?.supports_store_type)
// 仅当前激活的子组件会挂载，ref 指向它；上传后调用其 refresh 刷新
const childRef = ref<any>(null)

async function loadConfig() {
  try {
    dbConfig.value = await getDbConfig(props.dbKey)
  } catch (e: any) {
    logger.warn('取库配置失败: ' + e.message, 'TurnoverInventory')
  }
}

onMounted(loadConfig)
watch(() => props.dbKey, () => {
  storeType.value = 'all'
  loadConfig()
})

async function handleImport(opts: any) {
  importing.value = true
  try {
    const res: any = await importInventory(props.dbKey, opts.file as File)
    ElMessage.success(res?.message || '导入成功')
    childRef.value?.refresh?.()
  } catch (e: any) {
    ElMessage.error('导入失败: ' + e.message)
    logger.error('库存导入失败: ' + e.message, 'TurnoverInventory')
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.ti-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.ti-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafbfc;
}
.ti-bar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ti-bar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}
.store-type-switch {
  display: flex;
  align-items: center;
  gap: 8px;
}
.store-type-label {
  font-size: 12px;
  color: #909399;
}
</style>
