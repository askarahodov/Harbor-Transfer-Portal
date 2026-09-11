import type { Pinia } from 'pinia'
import {
  createRouter,
  createWebHistory,
  type Router,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'

import { useAuthStore, type UserRole } from '@/stores/auth'

const transferRoles: UserRole[] = ['admin', 'operator']
const adminRoles: UserRole[] = ['admin']

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, layout: 'bare' },
  },
  { path: '/', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
  {
    path: '/export',
    name: 'export',
    component: () => import('@/views/ExportView.vue'),
    meta: { roles: transferRoles },
  },
  {
    path: '/import',
    name: 'import',
    component: () => import('@/views/ImportView.vue'),
    meta: { roles: transferRoles },
  },
  { path: '/history', name: 'history', component: () => import('@/views/HistoryView.vue') },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { roles: adminRoles },
  },
]

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({ history, routes })
}

export function installAuthGuards(appRouter: Router, pinia: Pinia): void {
  appRouter.beforeEach(async (to) => {
    const auth = useAuthStore(pinia)
    if (!auth.initialized) {
      await auth.bootstrapSession()
    }

    if (to.meta.public === true) {
      if (to.name === 'login' && auth.isAuthenticated) {
        return { name: 'dashboard' }
      }
      return true
    }

    if (!auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    const roles = Array.isArray(to.meta.roles) ? (to.meta.roles as UserRole[]) : null
    if (roles && !roles.includes(auth.user!.role)) {
      return { name: 'dashboard' }
    }

    return true
  })
}

export const router = createAppRouter()
