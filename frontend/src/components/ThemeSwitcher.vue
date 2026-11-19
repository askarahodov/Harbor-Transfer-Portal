<script setup lang="ts">
import { Monitor, Moon, Sun } from 'lucide-vue-next'
import { onBeforeUnmount, onMounted, ref } from 'vue'

type ThemeChoice = 'system' | 'light' | 'dark'

const choices = [
  { value: 'system', label: 'Системная', icon: Monitor },
  { value: 'light', label: 'Светлая', icon: Sun },
  { value: 'dark', label: 'Тёмная', icon: Moon },
] as const

const selected = ref<ThemeChoice>(window.HtpTheme?.get() ?? 'system')

function sync(): void {
  selected.value = window.HtpTheme?.get() ?? 'system'
}

function change(choice: ThemeChoice): void {
  window.HtpTheme?.set(choice)
  selected.value = window.HtpTheme?.get() ?? choice
}

onMounted(() => {
  sync()
  window.addEventListener('htp-theme-change', sync)
})
onBeforeUnmount(() => window.removeEventListener('htp-theme-change', sync))
</script>

<template>
  <div class="theme-switcher" role="group" aria-label="Тема оформления">
    <button
      v-for="choice in choices"
      :key="choice.value"
      type="button"
      class="theme-switcher__button"
      :class="{ 'theme-switcher__button--active': selected === choice.value }"
      :aria-label="`Тема: ${choice.label}`"
      :aria-pressed="selected === choice.value"
      :title="`Тема: ${choice.label}`"
      @click="change(choice.value)"
    >
      <component :is="choice.icon" :size="17" aria-hidden="true" />
      <span class="theme-switcher__label">{{ choice.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.theme-switcher {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  flex: 0 0 auto;
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}
.theme-switcher__button {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
}
.theme-switcher__button:hover {
  border-color: var(--color-border-control);
}
.theme-switcher__button--active {
  border-color: var(--color-action);
  background: var(--color-surface);
  color: var(--color-action);
  font-weight: 700;
  box-shadow: var(--shadow-sm);
}
@media (max-width: 1180px) {
  .theme-switcher__label { display: none; }
  .theme-switcher__button { min-width: 34px; }
}
</style>
