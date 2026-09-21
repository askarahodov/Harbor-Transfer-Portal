<script setup lang="ts">
import { ChevronDown, Search } from 'lucide-vue-next'
import { computed, nextTick, ref } from 'vue'

export type SearchComboboxOption = {
  value: string
  label: string
  description?: string
}

const props = withDefaults(
  defineProps<{
    label: string
    modelValue: string
    search: string
    options: SearchComboboxOption[]
    placeholder?: string
    disabled?: boolean
    loading?: boolean
    page?: number
    total?: number
    pageSize?: number
  }>(),
  { placeholder: 'Поиск…', disabled: false, loading: false, page: 1, total: 0, pageSize: 25 },
)

const emit = defineEmits<{
  'update:search': [value: string]
  select: [value: string]
  previous: []
  next: []
}>()

const open = ref(false)
const activeIndex = ref(-1)
const input = ref<HTMLInputElement | null>(null)
const listboxId = `combobox-${Math.random().toString(36).slice(2)}`
const hasPrevious = computed(() => props.page > 1)
const hasNext = computed(() => props.page * props.pageSize < props.total)
const activeId = computed(() => activeIndex.value >= 0 ? `${listboxId}-${activeIndex.value}` : undefined)

function show(): void {
  if (props.disabled) return
  open.value = true
  const selected = props.options.findIndex((item) => item.value === props.modelValue)
  activeIndex.value = selected >= 0 ? selected : props.options.length > 0 ? 0 : -1
}

function choose(index: number): void {
  const option = props.options[index]
  if (!option) return
  emit('select', option.value)
  open.value = false
  activeIndex.value = index
}

function onInput(event: Event): void {
  emit('update:search', (event.target as HTMLInputElement).value)
  open.value = true
  activeIndex.value = 0
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    open.value = false
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) show()
    if (props.options.length === 0) return
    const delta = event.key === 'ArrowDown' ? 1 : -1
    activeIndex.value = (activeIndex.value + delta + props.options.length) % props.options.length
    return
  }
  if (event.key === 'Enter' && open.value && activeIndex.value >= 0) {
    event.preventDefault()
    choose(activeIndex.value)
  }
}

async function toggle(): Promise<void> {
  open.value = !open.value
  if (open.value) {
    show()
    await nextTick()
    input.value?.focus()
  }
}
</script>

<template>
  <div class="search-combobox">
    <label class="search-combobox__label">{{ label }}</label>
    <div class="search-combobox__control">
      <Search :size="16" aria-hidden="true" />
      <input
        ref="input"
        role="combobox"
        type="search"
        :value="search"
        :placeholder="modelValue || placeholder"
        :disabled="disabled"
        :aria-label="label"
        :aria-expanded="open"
        :aria-controls="listboxId"
        :aria-activedescendant="activeId"
        autocomplete="off"
        @focus="show"
        @input="onInput"
        @keydown="onKeydown"
      />
      <button type="button" :disabled="disabled" :aria-label="`Открыть: ${label}`" @click="toggle">
        <ChevronDown :size="17" aria-hidden="true" />
      </button>
    </div>

    <div v-if="open" class="search-combobox__popover">
      <div v-if="loading" class="search-combobox__state" role="status">Загрузка…</div>
      <div v-else-if="options.length === 0" class="search-combobox__state" role="status">Ничего не найдено</div>
      <ul v-else :id="listboxId" role="listbox" :aria-label="label">
        <li
          v-for="(option, index) in options"
          :id="`${listboxId}-${index}`"
          :key="option.value"
          role="option"
          :aria-selected="option.value === modelValue"
          :class="{ 'search-combobox__option--active': index === activeIndex }"
          @mousedown.prevent="choose(index)"
        >
          <strong>{{ option.label }}</strong>
          <span v-if="option.description">{{ option.description }}</span>
        </li>
      </ul>
      <div v-if="total > pageSize" class="search-combobox__pagination" aria-label="Страницы результатов">
        <button type="button" :disabled="!hasPrevious" @click="emit('previous')">‹</button>
        <span>{{ page }} / {{ Math.ceil(total / pageSize) }}</span>
        <button type="button" :disabled="!hasNext" @click="emit('next')">›</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-combobox { position: relative; min-width: 0; }
.search-combobox__label { display: block; margin-bottom: var(--space-1); font-size: 12px; font-weight: 700; color: var(--color-text-muted); }
.search-combobox__control { display: flex; align-items: center; gap: var(--space-2); min-height: 40px; padding-left: var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); }
.search-combobox__control:focus-within { outline: 2px solid var(--color-focus-ring); outline-offset: 1px; }
.search-combobox__control input { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--color-text); }
.search-combobox__control button, .search-combobox__pagination button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; }
.search-combobox__control button { align-self: stretch; padding: 0 var(--space-3); }
.search-combobox__control button:disabled, .search-combobox__pagination button:disabled { cursor: default; opacity: .45; }
.search-combobox__popover { position: absolute; z-index: 20; top: calc(100% + var(--space-1)); left: 0; right: 0; overflow: hidden; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); box-shadow: var(--shadow-md); }
.search-combobox__popover ul { max-height: 260px; margin: 0; padding: var(--space-1); overflow: auto; list-style: none; }
.search-combobox__popover li { display: flex; flex-direction: column; gap: 2px; padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); cursor: pointer; }
.search-combobox__popover li span, .search-combobox__state { font-size: 12px; color: var(--color-text-muted); }
.search-combobox__option--active { background: var(--color-background); }
.search-combobox__state { padding: var(--space-3); }
.search-combobox__pagination { display: flex; align-items: center; justify-content: center; gap: var(--space-2); padding: var(--space-2); border-top: 1px solid var(--color-border); font-size: 12px; color: var(--color-text-muted); }
</style>
