<script setup lang="ts">
type WizardStep = {
  id: number
  label: string
}

defineProps<{
  steps: readonly WizardStep[]
  currentStep: number
  ariaLabel: string
}>()
</script>

<template>
  <ol
    class="wizard-stepper"
    :aria-label="ariaLabel"
    :style="{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }"
  >
    <li
      v-for="item in steps"
      :key="item.id"
      :class="[
        'wizard-stepper__item',
        {
          'wizard-stepper__item--active': currentStep === item.id,
          'wizard-stepper__item--done': currentStep > item.id,
        },
      ]"
      :aria-current="currentStep === item.id ? 'step' : undefined"
    >
      <span class="wizard-stepper__number">{{ item.id }}</span>
      <span>{{ item.label }}</span>
    </li>
  </ol>
</template>

<style scoped>
.wizard-stepper {
  display: grid;
  gap: var(--space-2);
  padding: 0;
  margin: 0;
  list-style: none;
}
.wizard-stepper__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 48px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  background: var(--color-surface);
}
.wizard-stepper__item--active {
  border-color: var(--color-action);
  color: var(--color-text);
  box-shadow: var(--shadow-sm);
}
.wizard-stepper__item--done {
  border-color: var(--color-success-text);
  color: var(--color-success-text);
  background: var(--color-success-surface);
}
.wizard-stepper__number {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 50%;
  background: var(--color-surface-subtle);
  font-weight: 800;
}
@media (max-width: 640px) {
  .wizard-stepper {
    grid-template-columns: 1fr 1fr !important;
  }
}
@media (max-width: 420px) {
  .wizard-stepper {
    grid-template-columns: 1fr !important;
  }
}
</style>
