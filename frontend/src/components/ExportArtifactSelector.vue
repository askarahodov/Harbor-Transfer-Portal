<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { HarborArtifact } from '@/api/exports'
import SearchCombobox, { type SearchComboboxOption } from '@/components/SearchCombobox.vue'
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
const options = computed<SearchComboboxOption[]>(() =>
  choices.value.map(({ key, artifact, reference }) => ({
    value: key,
    label: reference,
    description: `${kindLabel(artifact.kind)} · ${shortDigest(artifact.digest)} · ${formatBytes(artifact.size)}`,
  })),
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
    <SearchCombobox
      label="Версия / tag"
      :model-value="candidateKey"
      :search="search"
      :options="options"
      placeholder="Найти version, tag или digest"
      :disabled="!selectedRepository"
      :loading="busy"
      :page="page"
      :total="total"
      @update:search="emit('update:search', $event)"
      @select="selectCandidate"
      @previous="emit('page', page - 1)"
      @next="emit('page', page + 1)"
    />

    <div v-if="candidate" class="artifact-selector__candidate" aria-live="polite">
      <div>
        <strong>{{ candidate.reference }}</strong>
        <span>{{ kindLabel(candidate.artifact.kind) }} · {{ formatBytes(candidate.artifact.size) }}</span>
        <code :title="candidate.artifact.digest">{{ shortDigest(candidate.artifact.digest) }}</code>
      </div>
      <button type="button" class="artifact-selector__add" @click="addCandidate">Добавить</button>
    </div>

    <div v-if="unsupported.length" class="artifact-selector__unsupported" role="status">
      <strong>Неподдерживаемые OCI artifacts</strong>
      <span v-for="artifact in unsupported" :key="artifact.digest">
        {{ shortDigest(artifact.digest) }}
        <template v-if="referencesFor(artifact).length"> · {{ referencesFor(artifact).join(', ') }}</template>
      </span>
    </div>
  </div>
</template>

<style scoped>
.artifact-selector { min-width: 0; }
.artifact-selector__candidate { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-top: var(--space-2); padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.artifact-selector__candidate > div { display: flex; min-width: 0; align-items: baseline; flex-wrap: wrap; gap: var(--space-2); }
.artifact-selector__candidate span { color: var(--color-text-muted); font-size: 12px; }
.artifact-selector__candidate code { overflow-wrap: anywhere; font-size: 12px; }
.artifact-selector__add { min-height: 36px; padding: 0 var(--space-3); border: 0; border-radius: var(--radius-md); background: var(--color-action-surface); color: var(--color-on-accent); border: 1px solid var(--color-action); font-weight: 700; cursor: pointer; }
.artifact-selector__unsupported { display: grid; gap: var(--space-1); margin-top: var(--space-2); color: var(--color-text-muted); font-size: 12px; }
@media (max-width: 760px) { .artifact-selector__candidate { align-items: stretch; flex-direction: column; } }
</style>
