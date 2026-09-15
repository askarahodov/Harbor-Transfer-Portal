import type { Pinia } from 'pinia'
import {
  createRouter,
  createWebHistory,
  type Router,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'

import { useAuthStore, type UserRole } from '@/stores/auth'
import { useRuntimeStore, type PortalContour } from '@/stores/runtime'

const transferRoles: UserRole[] = ['admin', 'operator']
const adminRoles: UserRole[] = ['admin']
const sourceContours: PortalContour[] = ['SOURCE']
const targetContours: PortalContour[] = ['TARGET']

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
    meta: { roles: transferRoles, contours: sourceContours },
  },
  {
    path: '/import',
    name: 'import',
    component: () => import('@/views/ImportWorkspaceView.vue'),
    meta: { roles: transferRoles, contours: targetContours },
  },
  { path: '/history', name: 'history', component: () => import('@/views/HistoryView.vue') },
  {
    path: '/users',
    name: 'users',
    component: () => import('@/views/UsersView.vue'),
    meta: { roles: adminRoles },
  },
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

    const contours = Array.isArray(to.meta.contours)
      ? (to.meta.contours as PortalContour[])
      : null
    if (contours) {
      const runtime = useRuntimeStore(pinia)
      if (!runtime.contour) {
        await runtime.loadRuntime()
      }
      if (!runtime.contour || !contours.includes(runtime.contour)) {
        return { name: 'dashboard' }
      }
    }

    return true
  })
}

export const router = createAppRouter()
