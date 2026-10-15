<template>
  <!-- 海王：无数字看板、无省份维度；商品/城市列业务名不同 -->
  <StoreDataView
    db-key="haiwang"
    :show-dashboard="false"
    :show-provinces="false"
    product-label="商品SAP编码"
    city-label="事业部名称"
    product-placeholder="10002345,10002346"
    city-placeholder="海王星辰,海王健康"
    export-file-name="海王_查询结果.csv"
  />
</template>

<script setup lang="ts">
import StoreDataView from '@/components/StoreDataView.vue'
</script>
