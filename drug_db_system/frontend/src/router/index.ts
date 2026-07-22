import { createRouter, createWebHistory } from 'vue-router'
import DashenlinView from '../views/DashenlinView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/dashenlin',
    },
    {
      path: '/dashenlin',
      name: 'dashenlin',
      component: DashenlinView,
    },
  ],
})

export default router
