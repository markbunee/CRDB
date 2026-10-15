import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import DashenlinView from '../views/DashenlinView.vue'
import HaiwangView from '../views/HaiwangView.vue'
import GaojiView from '../views/GaojiView.vue'
import LoginView from '../views/LoginView.vue'
import AdminView from '../views/AdminView.vue'
import HomeView from '../views/HomeView.vue'
import ModulePlaceholderView from '../views/ModulePlaceholderView.vue'
import { isAdmin, isLoggedIn } from '@/utils/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/home',
    },
    {
      path: '/home',
      name: 'home',
      component: HomeView,
      meta: { title: '主界面' },
    },
    {
      path: '/store-purchase',
      name: 'store-purchase',
      component: ModulePlaceholderView,
      meta: {
        title: '门店购进',
        desc: '查看门店向总部/供应商的购进数据，掌握门店进货情况。',
      },
    },
    {
      path: '/hq-purchase',
      name: 'hq-purchase',
      component: ModulePlaceholderView,
      meta: {
        title: '总部购进',
        desc: '查看总部统一采购数据，统计各品类与供应商的购进规模。',
      },
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
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
    {
      path: '/admin',
      name: 'admin',
      component: AdminView,
    },
  ],
})

// ---------- 登录 / 权限守卫 ----------
router.beforeEach((to) => {
  // 登录页：未登录可访问；已登录则直接回数据页
  if (to.path === '/login') {
    return isLoggedIn() ? { path: '/home' } : true
  }
  // 未登录：跳转登录页并记录原目标，登录后自动回跳
  if (!isLoggedIn()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 后台管理：仅管理员（后端同样有 require_admin 校验）
  if (to.path === '/admin' && !isAdmin()) {
    ElMessage.warning('无权限：后台管理仅管理员可用')
    return { path: '/home' }
  }
  return true
})

export default router
