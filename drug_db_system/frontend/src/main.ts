import './assets/main.css'

import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
// 用 es/locale/lang 入口：该路径自带 .d.ts 类型声明，
// dist/locale/*.mjs 无类型会导致 TS7016（隐式 any）。
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import App from './App.vue'
import router from './router'
import { logger } from '@/utils/logger'

const app = createApp(App)

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(ElementPlus, { locale: zhCn })
app.use(router)

app.mount('#app')
logger.info('前端应用已挂载', 'main')

