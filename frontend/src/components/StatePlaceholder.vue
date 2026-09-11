<script setup lang="ts">
import { AlertTriangle, Inbox, LoaderCircle } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    kind: 'empty' | 'loading' | 'error'
    title: string
    description?: string
  }>(),
  { description: '' },
)

const icons = {
  empty: Inbox,
  loading: LoaderCircle,
  error: AlertTriangle,
}
</script>

<template>
  <div class="state-placeholder" role="status" :aria-live="props.kind === 'error' ? 'assertive' : 'polite'">
    <component :is="icons[props.kind]" :class="{ 'is-spinning': props.kind === 'loading' }" :size="32" aria-hidden="true" />
    <strong>{{ props.title }}</strong>
    <p v-if="props.description">{{ props.description }}</p>
  </div>
</template>

<style scoped>
.state-placeholder {
  display: grid;
  justify-items: center;
  gap: var(--space-3);
  padding: var(--space-8);
  border: 1px dashed var(--color-mist);
  border-radius: var(--radius-lg);
  background: var(--color-cloud-white);
  color: var(--color-steel);
  text-align: center;
}
.state-placeholder strong { color: var(--color-deep-harbor); }
.state-placeholder p { max-width: 52ch; margin: 0; }
.is-spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .is-spinning { animation: none; } }
</style>
