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

const candidateKey = ref('')
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
const candidate = computed(() => choices.value.find((item) => item.key === candidateKey.value) ?? null)
const unsupported = computed(() => props.artifacts.filter((artifact) => artifact.kind === 'unknown-oci'))

watch(() => props.selectedRepository, () => { candidateKey.value = '' })
watch(() => props.search, () => {
  if (candidateKey.value) candidateKey.value = ''
})

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return 'OCI'
}

function shortDigest(digest: string | null): string {
  return formatShortDigest(digest, { maxLength: 24, headLength: 18, tailLength: 8 })
}

function selectCandidate(value: string): void {
  candidateKey.value = value
}

function addCandidate(): void {
  if (!candidate.value) return
  emit('add', candidate.value.artifact, candidate.value.reference)
  candidateKey.value = ''
  emit('update:search', '')
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
    <div v-else class="artifact-selector__versions" role="listbox" aria-label="Доступные версии">
      <button
        v-for="choice in choices"
        :key="choice.key"
        type="button"
        role="option"
        :aria-selected="candidateKey === choice.key"
        :class="['artifact-selector__version', { 'artifact-selector__version--selected': candidateKey === choice.key }]"
        @click="selectCandidate(choice.key)"
      >
        <strong>{{ choice.reference }}</strong>
        <span>{{ kindLabel(choice.artifact.kind) }}</span>
        <code :title="choice.artifact.digest">{{ shortDigest(choice.artifact.digest) }}</code>
        <span>{{ formatBytes(choice.artifact.size) }}</span>
      </button>
    </div>

    <div v-if="candidate" class="artifact-selector__candidate" aria-live="polite">
      <div>
        <strong>{{ candidate.reference }}</strong>
        <span>{{ kindLabel(candidate.artifact.kind) }} · {{ formatBytes(candidate.artifact.size) }}</span>
        <code :title="candidate.artifact.digest">{{ shortDigest(candidate.artifact.digest) }}</code>
      </div>
      <button type="button" class="artifact-selector__add" @click="addCandidate">Добавить</button>
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
      </span>
    </div>
  </div>
</template>

<style scoped>
.artifact-selector { display: grid; gap: var(--space-3); min-width: 0; }
.artifact-selector__search { display: grid; gap: var(--space-2); font-weight: 700; }
.artifact-selector__search input { width: 100%; min-height: 44px; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; }
.artifact-selector__versions { display: grid; gap: var(--space-2); }
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