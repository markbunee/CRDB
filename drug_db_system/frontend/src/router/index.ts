import { createRouter, createWebHistory } from 'vue-router'
import DashenlinView from '../views/DashenlinView.vue'
import HaiwangView from '../views/HaiwangView.vue'
import GaojiView from '../views/GaojiView.vue'

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
    {
      path: '/gaoji',
      name: 'gaoji',
      component: GaojiView,
    },
    {
      path: '/haiwang',
      name: 'haiwang',
      component: HaiwangView,
    },
  ],
})

export default router
