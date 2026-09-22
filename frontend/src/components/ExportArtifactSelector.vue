<script setup lang="ts">
import { Box, Search, ShipWheel } from 'lucide-vue-next'

import type { HarborArtifact } from '@/api/exports'
import StatePlaceholder from './StatePlaceholder.vue'
import { formatBytes, shortDigest as formatShortDigest } from '@/presentation/format'

defineProps<{
  artifacts: HarborArtifact[]
  selectedRepository: string | null
  search: string
  busy: boolean
  page: number
  total: number
  referencesFor: (artifact: HarborArtifact) => string[]
  isSelected: (artifact: HarborArtifact, reference: string) => boolean
}>()

const emit = defineEmits<{
  'update:search': [value: string]
  toggle: [artifact: HarborArtifact, reference: string]
  page: [page: number]
}>()

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return 'OCI (не поддерживается)'
}

function shortDigest(digest: string | null): string {
  return formatShortDigest(digest, { maxLength: 24, headLength: 18, tailLength: 8 })
}
</script>

<template>
  <div class="artifact-selector">
    <label class="artifact-selector__search">
      <span>Версия / tag</span>
      <span class="artifact-selector__input">
        <Search :size="17" aria-hidden="true" />
        <input
          :value="search"
          type="search"
          placeholder="Фильтр version, tag или digest"
          maxlength="256"
          :disabled="!selectedRepository"
          aria-label="Фильтр версии, tag или digest"
          @input="emit('update:search', ($event.target as HTMLInputElement).value)"
        />
      </span>
    </label>

    <section class="artifact-selector__panel" aria-labelledby="artifacts-title">
      <div class="artifact-selector__heading">
        <h3 id="artifacts-title">Доступные версии</h3>
        <p>Digest остаётся источником точной идентичности; tag используется как удобное имя.</p>
      </div>
      <StatePlaceholder v-if="!selectedRepository" compact kind="empty" title="Выберите проект и репозиторий" />
      <StatePlaceholder v-else-if="busy" compact kind="loading" title="Загрузка версий" />
      <StatePlaceholder v-else-if="artifacts.length === 0" compact kind="empty" title="Версии не найдены" />
      <div v-else class="artifact-selector__list">
        <article v-for="artifact in artifacts" :key="artifact.digest" class="artifact-selector__card">
          <div class="artifact-selector__main">
            <div class="artifact-selector__kind" aria-hidden="true">
              <Box v-if="artifact.kind === 'container-image'" :size="20" />
              <ShipWheel v-else :size="20" />
            </div>
            <div>
              <strong>{{ kindLabel(artifact.kind) }}</strong>
              <p class="digest" :title="artifact.digest">{{ shortDigest(artifact.digest) }}</p>
              <p class="muted">{{ formatBytes(artifact.size) }}</p>
            </div>
          </div>
          <div v-if="artifact.kind === 'unknown-oci'" class="unsupported">
            <div>Не поддерживается export v1</div>
            <div v-if="referencesFor(artifact).length" class="reference-list" aria-label="References неподдерживаемого OCI">
              <code v-for="reference in referencesFor(artifact)" :key="reference">{{ reference }}</code>
            </div>
          </div>
          <div v-else-if="referencesFor(artifact).length === 0" class="unsupported">Нет явной версии/tag</div>
          <div v-else class="reference-list">
            <label v-for="reference in referencesFor(artifact)" :key="reference" class="reference-choice">
              <input type="checkbox" :checked="isSelected(artifact, reference)" @change="emit('toggle', artifact, reference)" />
              <span>{{ reference }}</span>
            </label>
          </div>
        </article>
      </div>
      <div v-if="total > 25" class="artifact-selector__pagination" aria-label="Страницы артефактов">
        <button type="button" :disabled="page <= 1" @click="emit('page', page - 1)">Назад</button>
        <span>{{ page }} / {{ Math.ceil(total / 25) }}</span>
        <button type="button" :disabled="page * 25 >= total" @click="emit('page', page + 1)">Далее</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.artifact-selector { min-width: 0; }
.artifact-selector__search { display: block; max-width: 440px; margin-bottom: var(--space-3); }
.artifact-selector__search > span:first-child { display: block; margin-bottom: var(--space-1); font-size: 12px; font-weight: 700; color: var(--color-text-muted); }
.artifact-selector__input { display: flex; align-items: center; gap: var(--space-2); min-height: 40px; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); }
.artifact-selector__input:focus-within { outline: 2px solid var(--color-focus-ring); outline-offset: 1px; }
.artifact-selector__input input { width: 100%; border: 0; outline: 0; background: transparent; color: var(--color-text); }
.artifact-selector__panel { border-top: 1px solid var(--color-border); padding-top: var(--space-3); }
.artifact-selector__heading { margin-bottom: var(--space-3); }
.artifact-selector__heading h3, .artifact-selector__heading p { margin: 0; }
.artifact-selector__heading p { margin-top: var(--space-1); color: var(--color-text-muted); font-size: 13px; }
.artifact-selector__list { display: grid; gap: var(--space-2); }
.artifact-selector__card { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(220px, 2fr); gap: var(--space-3); align-items: center; padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.artifact-selector__main { display: flex; gap: var(--space-2); align-items: center; min-width: 0; }
.artifact-selector__main p { margin: 2px 0 0; }
.digest { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
.muted { color: var(--color-text-muted); }
.reference-list { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-2); }
.reference-choice { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 38px; padding: var(--space-1) var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-full); background: var(--color-surface-subtle); cursor: pointer; }
.reference-choice:has(input:checked) { border-color: var(--color-action); background: var(--color-info-surface); }
.unsupported { max-width: 240px; color: var(--color-text-muted); font-size: 13px; text-align: right; }
.artifact-selector__kind { display: grid; place-items: center; flex: 0 0 36px; width: 36px; height: 36px; border-radius: var(--radius-md); background: var(--color-background); }
.artifact-selector__pagination { display: flex; justify-content: center; align-items: center; gap: var(--space-3); margin-top: var(--space-3); }
.artifact-selector__pagination button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; }
@media (max-width: 760px) { .artifact-selector__card { grid-template-columns: 1fr; } .reference-list { justify-content: flex-start; } .unsupported { max-width: none; text-align: left; } }
</style>
