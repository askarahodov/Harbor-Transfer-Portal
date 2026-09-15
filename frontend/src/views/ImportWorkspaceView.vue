<script setup lang="ts">
import ImportDestinationMapping from '@/components/ImportDestinationMapping.vue'
import ImportReceiptDestinations from '@/components/ImportReceiptDestinations.vue'
import { useImportWizardStore } from '@/stores/importWizard'

import ImportView from './ImportView.vue'

const wizard = useImportWizardStore()
</script>

<template>
  <ImportView />
  <Teleport
    v-if="wizard.step === 2 && wizard.preview"
    defer
    to=".panel[aria-labelledby='preview-title']"
  >
    <ImportDestinationMapping class="import-destination-mapping-slot" />
  </Teleport>
  <Teleport
    v-if="wizard.step === 3 && wizard.receipt"
    defer
    to=".receipt-card"
  >
    <ImportReceiptDestinations :receipt="wizard.receipt" />
  </Teleport>
</template>

<style>
.panel[aria-labelledby='preview-title'] > .import-destination-mapping-slot {
  order: 1;
}
.panel[aria-labelledby='preview-title'] > .classification-summary,
.panel[aria-labelledby='preview-title'] > .table-wrap {
  display: none;
}
.panel[aria-labelledby='preview-title'] > .notice--danger,
.panel[aria-labelledby='preview-title'] > .conflict-box,
.panel[aria-labelledby='preview-title'] > .actions {
  order: 2;
}
</style>
