<script setup lang="ts">
import { onMounted } from 'vue'

import PageShell from '@/components/PageShell.vue'
import { useRuntimeStore } from '@/stores/runtime'

const runtime = useRuntimeStore()

onMounted(() => {
  void runtime.loadRuntime()
})
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <component :is="Component" v-if="route.meta.layout === 'bare'" />
    <PageShell v-else>
      <component :is="Component" />
    </PageShell>
  </RouterView>
</template>
