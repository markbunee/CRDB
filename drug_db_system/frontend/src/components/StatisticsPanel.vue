<template>
  <el-drawer
    v-model="drawerVisible"
    title="统计分析"
    direction="rtl"
    :size="isMobile ? '100%' : '86%'"
    :with-header="true"
    class="stats-drawer"
    @closed="showMenu = true"
  >
    <div class="stats-layout" :class="{ 'is-mobile': isMobile }">
      <!-- 左侧功能菜单（手机端作为独立菜单页整屏展示） -->
      <aside class="stats-sidebar" v-show="!isMobile || showMenu">
        <div class="sidebar-title">
          {{ isMobile ? '选择统计维度' : '功能菜单' }}
          <span v-if="isMobile" class="sidebar-count">共 {{ menuItems.length }} 项</span>
        </div>
        <ul class="menu-list">
          <li
            v-for="item in menuItems"
            :key="item.key"
            class="menu-item"
            :class="{ active: activeFunc === item.key }"
            @click="selectFunc(item.key)"
          >
            <el-icon class="menu-icon"><component :is="item.icon" /></el-icon>
            <div class="menu-text">
              <div class="menu-name">{{ item.name }}</div>
              <div class="menu-desc">{{ item.desc }}</div>
            </div>
            <el-icon v-if="isMobile" class="menu-arrow"><ArrowRight /></el-icon>
          </li>
        </ul>
      </aside>

      <!-- 右侧内容区 -->
      <section class="stats-content" v-show="!isMobile || !showMenu">
        <!-- 手机端：返回菜单 + 当前功能名 -->
        <div v-if="isMobile" class="mobile-bar">
          <button class="mobile-back" type="button" @click="showMenu = true">
            <el-icon><ArrowLeft /></el-icon>
            <span>{{ currentItem?.name }}</span>
          </button>
        </div>

        <StoreCityStats
          v-if="activeFunc === 'store_city'"
          :db-key="dbKey"
        />
        <BoxCountStats
          v-else-if="activeFunc === 'box_count'"
          :db-key="dbKey"
        />
        <SalesTrendStats
          v-else-if="activeFunc === 'trend'"
          :db-key="dbKey"
        />
        <StoreAbilityStats
          v-else-if="activeFunc === 'store_ability'"
          :db-key="dbKey"
        />
        <BoxCountStats
          v-else-if="activeFunc === 'amount'"
          :db-key="dbKey"
          mode="amount"
        />
        <ProductMapPanel
          v-else-if="activeFunc === 'product_map'"
          :db-key="dbKey"
        />
        <TurnoverInventory
          v-else-if="activeFunc === 'turnover_inventory'"
          :db-key="dbKey"
        />
      </section>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import {
  Shop,
  Box,
  TrendCharts,
  Trophy,
  Money,
  Notebook,
  DataAnalysis,
  ArrowRight,
  ArrowLeft,
} from '@element-plus/icons-vue'
import StoreCityStats from './stats/StoreCityStats.vue'
import BoxCountStats from './stats/BoxCountStats.vue'
import SalesTrendStats from './stats/SalesTrendStats.vue'
import StoreAbilityStats from './stats/StoreAbilityStats.vue'
import ProductMapPanel from './stats/ProductMapPanel.vue'
import TurnoverInventory from './stats/TurnoverInventory.vue'
import { getDbConfig } from '@/utils/dbConfig'

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

/** 门店能力分析是否对当前库开放（本期仅大参林，由后端 meta 下发开关） */
const abilityEnabled = ref(false)
/** 库存管理 / 动销率 / 库存情况查询 是否对当前库开放（本期仅大参林） */
const inventoryEnabled = ref(false)

async function loadAbilityFlag() {
  const cfg = await getDbConfig(props.dbKey)
  abilityEnabled.value = cfg?.supports_store_ability ?? false
  inventoryEnabled.value = cfg?.supports_inventory ?? false
}

const menuItems = computed(() => {
  const items: {
    key: string
    name: string
    desc: string
    icon: typeof Shop
  }[] = [
    {
      key: 'store_city',
      name: '实销门店数统计',
      desc: '城市维度 · 月度/区间',
      icon: Shop,
    },
    {
      key: 'box_count',
      name: '实销盒数统计',
      desc: '城市维度 · 月度/区间 · SUM(数量)',
      icon: Box,
    },
    {
      key: 'trend',
      name: '销售趋势分析',
      desc: '折线图 · 柱状图 · 可视化',
      icon: TrendCharts,
    },
  ]
  items.push({
    key: 'amount',
    name: '实销金额统计',
    desc: '盒数×开票价 · 万元展示',
    icon: Money,
  })
  items.push({
    key: 'product_map',
    name: '品类映射表',
    desc: '编码→中文名 · 开票价维护',
    icon: Notebook,
  })
  if (inventoryEnabled.value) {
    items.push({
      key: 'turnover_inventory',
      name: '动销率库存情况',
      desc: '实销门店数 ÷ 库存门店数 · 库存量/效期货',
      icon: DataAnalysis,
    })
  }
  if (abilityEnabled.value) {
    items.push({
      key: 'store_ability',
      name: '门店能力分析',
      desc: '广州 · 门店产出排行 · 品类能力',
      icon: Trophy,
    })
  }
  return items
})

const activeFunc = ref('store_city')

/** 手机端：先展示功能菜单，点选后再进入对应统计内容 */
const showMenu = ref(true)
const currentItem = computed(() => menuItems.value.find((i) => i.key === activeFunc.value))

/** 与 main.css 保持一致的移动端断点 */
const MOBILE_QUERY = '(max-width: 768px)'
const isMobile = ref(false)
let mq: MediaQueryList | null = null

function syncMobile() {
  isMobile.value = !!mq?.matches
}

function selectFunc(key: string) {
  activeFunc.value = key
  if (isMobile.value) showMenu.value = false
}

onMounted(() => {
  mq = window.matchMedia(MOBILE_QUERY)
  mq.addEventListener('change', syncMobile)
  syncMobile()
  loadAbilityFlag()
})

onUnmounted(() => {
  mq?.removeEventListener('change', syncMobile)
  mq = null
})

// 切换库后重新判断开关；若当前停留在新库不支持的功能上则回落到第一项
watch(
  () => props.dbKey,
  async () => {
    await loadAbilityFlag()
    if (!menuItems.value.some((i) => i.key === activeFunc.value)) {
      activeFunc.value = 'store_city'
    }
  },
)
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
  display: none;
}

.menu-arrow {
  margin-left: auto;
  align-self: center;
  font-size: 16px;
  color: #c0c4cc;
  flex-shrink: 0;
}

/* ---------- 右侧内容区（宽） ---------- */
.stats-content {
  flex: 1;
  min-width: 0;
  padding: 20px 20px 24px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

/* 手机端返回条 */
.mobile-bar {
  flex-shrink: 0;
  margin-bottom: 12px;
}

.mobile-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 8px 14px 8px 10px;
  background: #f1f6ff;
  border: 1px solid #d6e4ff;
  border-radius: 999px;
  color: #1a4fc0;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.mobile-back span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ============================================================
   手机端（≤768px）：侧栏改为整屏菜单页，点选后进入内容
   ============================================================ */
@media (max-width: 768px) {
  .stats-layout {
    display: block;
    height: 100%;
  }

  .stats-sidebar {
    width: 100%;
    height: 100%;
    background: #fff;
    border-right: none;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .sidebar-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 2px 12px;
    font-size: 12px;
    color: #909399;
    text-transform: none;
    letter-spacing: 0;
  }

  .sidebar-count {
    font-size: 11px;
    color: #909399;
    font-weight: 400;
  }

  .menu-list {
    padding: 0;
    gap: 10px;
  }

  .menu-item {
    align-items: center;
    gap: 12px;
    padding: 14px;
    background: #fff;
    border: 1px solid #ebeef5;
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
  }

  .menu-item:active {
    background: #f5f7fa;
  }

  .menu-item.active {
    background: #f1f6ff;
    border-color: #3b6bd6;
  }

  .menu-icon {
    font-size: 22px;
    margin-top: 0;
    flex-shrink: 0;
  }

  .menu-name {
    font-size: 14px;
  }

  .menu-desc {
    font-size: 11px;
    color: #909399;
  }

  .stats-content {
    height: 100%;
    padding: 4px 0 calc(20px + env(safe-area-inset-bottom, 0px));
    overflow-x: hidden;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
