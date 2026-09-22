<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { HarborArtifact } from '@/api/exports'
import StatePlaceholder from '@/components/StatePlaceholder.vue'
import { formatBytes, shortDigest as formatShortDigest } from '@/presentation/format'

const props = defineProps<{
  artifacts: HarborArtifact[]
  selectedRepository: string | null
  search: string
  busy: boolean
  page: number
  total: number
  referencesFor: (artifact: HarborArtifact) => string[]
}>()

const emit = defineEmits<{
  'update:search': [value: string]
  add: [artifact: HarborArtifact, reference: string]
  page: [page: number]
}>()

type VersionChoice = {
  key: string
  artifact: HarborArtifact
  reference: string
}

const candidateKeys = ref<string[]>([])
const choices = computed<VersionChoice[]>(() =>
  props.artifacts.flatMap((artifact) =>
    artifact.kind === 'unknown-oci'
      ? []
      : props.referencesFor(artifact).map((reference) => ({
          key: [artifact.kind, artifact.project, artifact.repository, artifact.digest, reference].join('|'),
          artifact,
          reference,
        })),
  ),
)
const candidates = computed(() => choices.value.filter((item) => candidateKeys.value.includes(item.key)))
const unsupported = computed(() => props.artifacts.filter((artifact) => artifact.kind === 'unknown-oci'))

watch(() => props.selectedRepository, () => { candidateKeys.value = [] })
watch(() => props.search, () => {
  if (candidateKeys.value.length) candidateKeys.value = []
})

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return 'OCI'
}

function shortDigest(digest: string | null): string {
  return formatShortDigest(digest, { maxLength: 24, headLength: 18, tailLength: 8 })
}

function toggleCandidate(value: string): void {
  candidateKeys.value = candidateKeys.value.includes(value)
    ? candidateKeys.value.filter((item) => item !== value)
    : [...candidateKeys.value, value]
}

function addCandidates(): void {
  for (const candidate of candidates.value) {
    emit('add', candidate.artifact, candidate.reference)
  }
  candidateKeys.value = []
}
</script>

<template>
  <div class="artifact-selector">
    <label class="artifact-selector__search">
      <span>Версия / tag</span>
      <input
        type="search"
        :value="search"
        :disabled="!selectedRepository"
        placeholder="Найти version, tag или digest"
        aria-label="Версия / tag"
        @input="emit('update:search', ($event.target as HTMLInputElement).value)"
      />
    </label>

    <component
      :is="StatePlaceholder"
      v-if="!selectedRepository"
      compact
      kind="empty"
      title="Выберите проект и репозиторий"
    />

    <div v-else-if="busy" class="artifact-selector__state" role="status">Загрузка версий…</div>
    <div v-else-if="!choices.length" class="artifact-selector__state" role="status">Версии не найдены</div>
    <div v-else class="artifact-selector__table-wrap">
      <table class="artifact-selector__table">
        <thead>
          <tr>
            <th scope="col"><span class="sr-only">Выбор</span></th>
            <th scope="col">Версия / tag</th>
            <th scope="col">Тип</th>
            <th scope="col">Digest</th>
            <th scope="col">Размер</th>
            <th scope="col">Добавлен</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="choice in choices" :key="choice.key" :class="{ 'artifact-selector__row--selected': candidateKeys.includes(choice.key) }">
            <td>
              <input
                type="checkbox"
                :checked="candidateKeys.includes(choice.key)"
                :aria-label="`Выбрать ${choice.reference}`"
                @change="toggleCandidate(choice.key)"
              />
            </td>
            <td><strong>{{ choice.reference }}</strong></td>
            <td>{{ kindLabel(choice.artifact.kind) }}</td>
            <td><code :title="choice.artifact.digest">{{ shortDigest(choice.artifact.digest) }}</code></td>
            <td>{{ formatBytes(choice.artifact.size) }}</td>
            <td>{{ choice.artifact.pushed_at ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="selectedRepository" class="artifact-selector__actions">
      <span>{{ candidateKeys.length ? `Выбрано: ${candidateKeys.length}` : 'Выберите один или несколько артефактов.' }}</span>
      <button type="button" class="artifact-selector__add" :disabled="!candidateKeys.length" @click="addCandidates">
        Добавить выбранное
      </button>
    </div>

    <div v-if="total > choices.length" class="artifact-selector__pagination" aria-label="Страницы версий">
      <button type="button" :disabled="page <= 1 || busy" @click="emit('page', page - 1)">Назад</button>
      <span>Страница {{ page }}</span>
      <button type="button" :disabled="busy || page * 25 >= total" @click="emit('page', page + 1)">Далее</button>
    </div>

    <div v-if="unsupported.length" class="artifact-selector__unsupported" role="status">
      <strong>Неподдерживаемые OCI artifacts</strong>
      <span>OCI (не поддерживается) · Не поддерживается export v1</span>
      <span v-for="artifact in unsupported" :key="artifact.digest">
        {{ shortDigest(artifact.digest) }}
        <template v-if="referencesFor(artifact).length"> · {{ referencesFor(artifact).join(', ') }}</template>
      </span>
    </div>
  </div>
</template>

<style scoped>
.artifact-selector { display: grid; gap: var(--space-3); min-width: 0; }
.artifact-selector__search { display: grid; gap: var(--space-2); font-weight: 700; }
.artifact-selector__search input { width: 100%; min-height: 44px; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; }
.artifact-selector__table-wrap { overflow-x: auto; border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.artifact-selector__table { width: 100%; min-width: 760px; border-collapse: collapse; font-size: 13px; }
.artifact-selector__table th, .artifact-selector__table td { padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: middle; }
.artifact-selector__table th { color: var(--color-text-muted); font-size: 12px; }
.artifact-selector__table tbody tr:last-child td { border-bottom: 0; }
.artifact-selector__row--selected { background: var(--color-info-surface); }
.artifact-selector__table code { font-size: 12px; overflow-wrap: anywhere; }
.artifact-selector__actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); color: var(--color-text-muted); font-size: 13px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.artifact-selector__version { display: grid; grid-template-columns: minmax(120px,.8fr) minmax(140px,1fr) minmax(220px,1.5fr) minmax(90px,.5fr); align-items: center; gap: var(--space-3); width: 100%; min-height: 48px; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); text-align: left; cursor: pointer; }
.artifact-selector__version:hover, .artifact-selector__version:focus-visible { border-color: var(--color-action); }
.artifact-selector__version--selected { border-color: var(--color-action); background: var(--color-info-surface); }
.artifact-selector__version span { color: var(--color-text-muted); font-size: 12px; }
.artifact-selector__version code { overflow-wrap: anywhere; font-size: 12px; }
.artifact-selector__state { padding: var(--space-4); border: 1px dashed var(--color-border); border-radius: var(--radius-md); color: var(--color-text-muted); text-align: center; }
.artifact-selector__candidate { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.artifact-selector__candidate > div { display: flex; min-width: 0; align-items: baseline; flex-wrap: wrap; gap: var(--space-2); }
.artifact-selector__candidate span { color: var(--color-text-muted); font-size: 12px; }
.artifact-selector__candidate code { overflow-wrap: anywhere; font-size: 12px; }
.artifact-selector__add { min-height: 36px; padding: 0 var(--space-3); border: 1px solid var(--color-action); border-radius: var(--radius-md); background: var(--color-action-surface); color: var(--color-on-accent); font-weight: 700; cursor: pointer; }
.artifact-selector__pagination { display: flex; align-items: center; justify-content: center; gap: var(--space-3); color: var(--color-text-muted); font-size: 12px; }
.artifact-selector__pagination button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; }
.artifact-selector__unsupported { display: grid; gap: var(--space-1); color: var(--color-text-muted); font-size: 12px; }
@media (max-width: 760px) {
  .artifact-selector__version { grid-template-columns: 1fr; gap: var(--space-1); }
  .artifact-selector__candidate { align-items: stretch; flex-direction: column; }
}
</style>