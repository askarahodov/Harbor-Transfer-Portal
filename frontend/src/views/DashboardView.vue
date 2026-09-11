<script setup lang="ts">
import StatePlaceholder from '@/components/StatePlaceholder.vue'
</script>

<template>
  <section aria-labelledby="dashboard-title">
    <h1 id="dashboard-title">Главная</h1>
    <StatePlaceholder kind="empty" title="Операций пока нет" description="Контурная главная и реальные операции будут подключены отдельной задачей." />
  </section>
</template>
