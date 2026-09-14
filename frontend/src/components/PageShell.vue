<script setup lang="ts">
import { LogOut } from 'lucide-vue-next'
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { navigationForRole } from '@/navigation'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

import ContourBadge from './ContourBadge.vue'

const router = useRouter()
const auth = useAuthStore()
const runtime = useRuntimeStore()
const navigation = computed(() =>
  navigationForRole(auth.user?.role, runtime.contour ?? undefined),
)

async function logout(): Promise<void> {
  auth.logout()
  await router.push('/login')
}
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
        <div class="topbar__identity">
          <strong>Harbor Transfer Portal</strong>
          <span v-if="runtime.version" class="release-version">v{{ runtime.version }}</span>
        </div>
        <div class="topbar__session">
          <span v-if="auth.user" class="current-user">
            {{ auth.user.username }} · {{ auth.user.role }}
          </span>
          <ContourBadge :contour="runtime.contour" />
          <button class="logout-button" type="button" aria-label="Выйти из портала" @click="logout">
            <LogOut :size="18" aria-hidden="true" />
            <span>Выйти</span>
          </button>
        </div>
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
.topbar__identity { display: flex; align-items: baseline; gap: var(--space-2); }
.release-version { color: var(--color-steel); font-size: 12px; font-variant-numeric: tabular-nums; }
.topbar__session { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; justify-content: flex-end; }
.current-user { color: var(--color-steel); font-size: 14px; }
.logout-button { min-height: 40px; display: inline-flex; align-items: center; gap: var(--space-2); border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-3); background: white; color: var(--color-deep-harbor); cursor: pointer; }
.logout-button:hover, .logout-button:focus-visible { border-color: var(--color-bridge-blue); }
.content { width: min(100%, var(--layout-content-max)); margin: 0 auto; padding: var(--space-8) var(--space-6); }
@media (max-width: 760px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { padding: var(--space-3) var(--space-4); }
  .brand { justify-content: center; }
  .nav-list { grid-auto-flow: column; grid-auto-columns: minmax(64px, 1fr); margin-top: var(--space-3); overflow-x: auto; }
  .nav-link { justify-content: center; padding: var(--space-2); }
  .nav-link span { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .topbar { padding: var(--space-3) var(--space-4); flex-wrap: wrap; }
  .topbar__session { width: 100%; justify-content: flex-start; }
  .content { padding: var(--space-6) var(--space-4); }
}
</style>
