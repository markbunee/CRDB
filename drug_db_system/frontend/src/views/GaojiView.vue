<template>
  <!-- 高济：无数字看板、无省份维度 -->
  <StoreDataView
    db-key="gaoji"
    :show-dashboard="false"
    :show-provinces="false"
    product-label="商品编码"
    city-label="城市"
    product-placeholder="1347215,1347216"
    city-placeholder="潮州,广州"
    export-file-name="高济_查询结果.csv"
  />
</template>

<script setup lang="ts">
import StoreDataView from '@/components/StoreDataView.vue'
</script>
