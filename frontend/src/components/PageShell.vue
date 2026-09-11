<script setup lang="ts">
import { History, LogIn, PackageOpen, Settings, Upload } from '@lucide/vue-next'
import { storeToRefs } from 'pinia'

import { useRuntimeStore } from '@/stores/runtime'

import ContourBadge from './ContourBadge.vue'

const runtime = useRuntimeStore()
const { contour } = storeToRefs(runtime)

const navigation = [
  { to: '/', label: 'Главная', icon: PackageOpen },
  { to: '/export', label: 'Отправка', icon: Upload },
  { to: '/import', label: 'Приём', icon: LogIn },
  { to: '/history', label: 'История', icon: History },
  { to: '/settings', label: 'Настройки', icon: Settings },
]
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Основная навигация">
      <RouterLink class="brand" to="/" aria-label="Harbor Transfer Portal — главная">
        <span class="brand__mark">HT</span>
        <span>Harbor Transfer Portal</span>
      </RouterLink>
      <nav class="nav-list">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to" class="nav-link">
          <component :is="item.icon" :size="20" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <strong>Harbor Transfer Portal</strong>
        <ContourBadge :contour="contour" />
      </header>
      <main class="content">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; display: grid; grid-template-columns: var(--layout-sidebar) minmax(0, 1fr); }
.sidebar { background: var(--color-deep-harbor); color: white; padding: var(--space-6) var(--space-4); }
.brand { display: flex; align-items: center; gap: var(--space-3); min-height: 44px; color: white; font-weight: 700; text-decoration: none; }
.brand__mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: var(--radius-md); background: var(--color-bridge-blue); font-size: 12px; }
.nav-list { display: grid; gap: var(--space-2); margin-top: var(--space-8); }
.nav-link { display: flex; align-items: center; gap: var(--space-3); min-height: 44px; padding: 0 var(--space-3); border-radius: var(--radius-md); color: rgba(255,255,255,.76); text-decoration: none; }
.nav-link:hover, .nav-link:focus-visible, .nav-link.router-link-exact-active { background: rgba(37,99,235,.2); color: white; }
.workspace { min-width: 0; }
.topbar { min-height: var(--layout-header); display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: 0 var(--space-6); border-bottom: 1px solid var(--color-mist); background: var(--color-cloud-white); }
.content { width: min(100%, var(--layout-content-max)); margin: 0 auto; padding: var(--space-8) var(--space-6); }
@media (max-width: 760px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { padding: var(--space-3) var(--space-4); }
  .brand { justify-content: center; }
  .nav-list { grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top: var(--space-3); overflow-x: auto; }
  .nav-link { justify-content: center; padding: var(--space-2); }
  .nav-link span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .topbar { padding: var(--space-3) var(--space-4); flex-wrap: wrap; }
  .content { padding: var(--space-6) var(--space-4); }
}
</style>
