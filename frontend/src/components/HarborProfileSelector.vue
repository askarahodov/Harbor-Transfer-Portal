<script setup lang="ts">
import { computed } from 'vue'

import type { HarborProfileOption } from '@/api/exports'

const props = withDefaults(
  defineProps<{
    id: string
    label: string
    profiles: HarborProfileOption[]
    modelValue: string | null
    disabled?: boolean
    locked?: boolean
    hint?: string
  }>(),
  {
    disabled: false,
    locked: false,
    hint: '',
  },
)

const emit = defineEmits<{
  'update:modelValue': [profileId: string]
}>()

const selectedProfile = computed(
  () => props.profiles.find((profile) => profile.id === props.modelValue) ?? null,
)

function host(value: string): string {
  try {
    return new URL(value).host
  } catch {
    return value
  }
}

function change(event: Event): void {
  const target = event.target as HTMLSelectElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <div class="profile-selector">
    <label :for="id">{{ label }}</label>
    <select
      :id="id"
      :value="modelValue ?? ''"
      :disabled="disabled || locked || profiles.length === 0"
      @change="change"
    >
      <option v-if="profiles.length === 0" value="" disabled>Нет доступных profiles</option>
      <option v-for="profile in profiles" :key="profile.id" :value="profile.id">
        {{ profile.name }} · {{ host(profile.url) }}
      </option>
    </select>
    <small v-if="selectedProfile">
      <template v-if="locked">
        Закреплён за operation: <strong>{{ selectedProfile.name }}</strong> · {{ host(selectedProfile.url) }}
      </template>
      <template v-else-if="hint">
        {{ hint }}
      </template>
      <template v-else>
        {{ selectedProfile.name }} · {{ selectedProfile.url }}
      </template>
    </small>
  </div>
</template>

<style scoped>
.profile-selector { display: grid; gap: var(--space-2); max-width: 760px; }
.profile-selector label { color: var(--color-text); font-weight: 700; }
.profile-selector select {
  min-height: 42px;
  width: 100%;
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-md);
  padding: 0 var(--space-3);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}
.profile-selector select:disabled { cursor: not-allowed; opacity: .7; }
.profile-selector small { color: var(--color-text-muted); }
.profile-selector strong { color: var(--color-text); }
</style>
