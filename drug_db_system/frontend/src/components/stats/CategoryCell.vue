<template>
  <div v-if="item" class="cat-cell" :class="tone === 'last' ? 'is-last' : 'is-top'">
    <div class="cat-name" :title="titleText">
      {{ displayName }}
    </div>
    <div class="cat-meta">
      <span class="cat-qty">{{ fmtQty(item.qty) }}</span>
      <span class="cat-share">{{ fmtShare(item.share) }}</span>
    </div>
  </div>
  <span v-else class="cat-empty">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { StoreAbilityCategory } from '@/api/client'

const props = withDefaults(
  defineProps<{
    item: StoreAbilityCategory | null
    /** top = 最佳品类（蓝）；last = 末位品类（橙） */
    tone?: 'top' | 'last'
  }>(),
  { tone: 'top' },
)

const displayName = computed(() => {
  const it = props.item
  if (!it) return ''
  return it.product_name || (it.product_code != null ? String(it.product_code) : '—')
})

const titleText = computed(() => {
  const it = props.item
  if (!it) return ''
  const code = it.product_code != null ? `（编码 ${it.product_code}）` : ''
  return `${displayName.value}${code}`
})

function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN')
}

function fmtShare(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(1)}%`
}
</script>

<style scoped>
.cat-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 3px 0;
  line-height: 1.35;
}

.cat-name {
  font-size: 12px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cat-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.cat-qty {
  color: #606266;
  font-weight: 600;
}

.cat-share {
  padding: 0 5px;
  border-radius: 8px;
  font-weight: 600;
}

.is-top .cat-share {
  background: #ecf5ff;
  color: #409eff;
}

.is-last .cat-share {
  background: #fdf6ec;
  color: #e6a23c;
}

.cat-empty {
  color: #c0c4cc;
}
</style>
