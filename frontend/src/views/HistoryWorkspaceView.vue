<script setup lang="ts">
import ImportReceiptDestinations from '@/components/ImportReceiptDestinations.vue'
import { useHistoryStore } from '@/stores/history'

import HistoryView from './HistoryView.vue'

const history = useHistoryStore()
</script>

<template>
  <HistoryView />
  <Teleport v-if="history.receipt" defer to=".receipt-card">
    <ImportReceiptDestinations :receipt="history.receipt" />
  </Teleport>
</template>
