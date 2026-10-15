<template>
  <!-- 大参林：有数字看板 + 省份维度 -->
  <StoreDataView
    db-key="dashenlin"
    :show-dashboard="true"
    :show-provinces="true"
    product-label="商品编码"
    city-label="城市"
    product-placeholder="1058746,1086127"
    city-placeholder="广州,深圳"
    export-file-name="大参林_查询结果.csv"
  />
</template>

<script setup lang="ts">
import StoreDataView from '@/components/StoreDataView.vue'
</script>
