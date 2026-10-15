<template>
  <!-- 全局账户水印：固定在视口顶层、穿透点击，覆盖抽屉/弹窗等 body 级元素 -->
  <div v-if="text" class="wm-layer" :style="{ backgroundImage: `url(${bg})` }" />
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{ text: string }>()

const bg = ref('')

function build() {
  const t = props.text?.trim()
  if (!t) {
    bg.value = ''
    return
  }
  const dpr = window.devicePixelRatio || 1
  const w = 320
  const h = 200
  const canvas = document.createElement('canvas')
  canvas.width = Math.floor(w * dpr)
  canvas.height = Math.floor(h * dpr)
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.scale(dpr, dpr)
  ctx.clearRect(0, 0, w, h)
  ctx.font = '14px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'
  ctx.fillStyle = 'rgba(17, 34, 68, 0.08)'
  ctx.textBaseline = 'middle'
  ctx.rotate((-20 * Math.PI) / 180)
  for (let y = -60; y < h + 120; y += 62) {
    for (let x = -60; x < w + 120; x += 210) {
      ctx.fillText(t, x, y)
    }
  }
  bg.value = canvas.toDataURL('image/png')
}

onMounted(build)
watch(() => props.text, build)
</script>

<style scoped>
.wm-layer {
  position: fixed;
  inset: 0;
  z-index: 99999;
  pointer-events: none;
  background-repeat: repeat;
}
</style>
