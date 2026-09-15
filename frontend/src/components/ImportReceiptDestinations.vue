<script setup lang="ts">
import type { ImportReceipt } from '@/api/imports'
import { shortDigest as formatShortDigest } from '@/presentation/format'

const props = defineProps<{
  receipt: ImportReceipt
}>()

function shortDigest(value: string | null | undefined): string {
  return formatShortDigest(value, { maxLength: 24, headLength: 16, tailLength: 8 })
}
</script>

<template>
  <section class="receipt-destinations" aria-labelledby="receipt-destinations-title">
    <div class="receipt-destinations__header">
      <div>
        <h4 id="receipt-destinations-title">Фактические TARGET destinations</h4>
        <p>Данные взяты из immutable import receipt, а не пересчитываются UI.</p>
      </div>
      <code v-if="props.receipt.destination_plan_id" :title="props.receipt.destination_plan_id">
        plan {{ props.receipt.destination_plan_id.slice(0, 12) }}…
      </code>
    </div>

    <div class="receipt-destinations__table-wrap">
      <table>
        <thead>
          <tr>
            <th>Артефакт</th>
            <th>Фактический TARGET reference</th>
            <th>Статус</th>
            <th>TARGET digest</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in props.receipt.artifacts" :key="item.index">
            <td>{{ item.repository }}</td>
            <td class="receipt-destinations__reference">
              {{ item.final_reference ?? item.target_repository ?? '—' }}
            </td>
            <td>{{ item.status }}</td>
            <td :title="item.target_digest ?? undefined">{{ shortDigest(item.target_digest) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.receipt-destinations { margin-top: var(--space-4); padding-top: var(--space-4); border-top: 1px solid var(--color-mist); }
.receipt-destinations__header { display: flex; justify-content: space-between; gap: var(--space-3); align-items: flex-start; }
.receipt-destinations__header h4 { margin: 0; color: var(--color-deep-harbor); }
.receipt-destinations__header p { margin: var(--space-1) 0 0; color: var(--color-steel); }
.receipt-destinations__header code { color: var(--color-steel); white-space: nowrap; }
.receipt-destinations__table-wrap { margin-top: var(--space-3); overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: var(--space-3); border-bottom: 1px solid var(--color-mist); text-align: left; vertical-align: top; }
th { color: var(--color-steel); font-size: 12px; }
.receipt-destinations__reference { min-width: 260px; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
</style>
