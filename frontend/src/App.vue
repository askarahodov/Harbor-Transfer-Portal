<script setup lang="ts">
import PageShell from '@/components/PageShell.vue'
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <component :is="Component" v-if="route.meta.layout === 'bare'" />
    <PageShell v-else>
      <component :is="Component" />
    </PageShell>
  </RouterView>
</template>
