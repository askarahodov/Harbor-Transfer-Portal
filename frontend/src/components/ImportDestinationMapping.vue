<script setup lang="ts">
import { CheckCircle2, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { listHarborProjects } from '@/api/exports'
import type { ImportDestinationArtifactPlan, ImportPreviewState } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'

const auth = useAuthStore()
const wizard = useImportWizardStore()

const projects = ref<string[]>([])
const projectsLoading = ref(false)
const projectsError = ref<string | null>(null)

const canEdit = computed(
  () => auth.canStartTransfers && wizard.operation?.status === 'READY',
)
const hasImages = computed(
  () => wizard.preview?.artifacts.some((item) => item.artifact_type === 'container-image') ?? false,
)
const hasCharts = computed(
  () => wizard.preview?.artifacts.some((item) => item.artifact_type === 'helm-chart') ?? false,
)
const planByIndex = computed(
  () => new Map(wizard.destinationPlan?.artifacts.map((item) => [item.index, item]) ?? []),
)

const stateLabels: Record<ImportPreviewState, string> = {
  NEW: 'готов к импорту',
  SAME: 'тот же digest — skip',
  CONFLICT: 'конфликт digest/version',
  UNKNOWN: 'состояние не доказано',
  ERROR: 'ошибка validation',
}

function projectFromEvent(event: Event): string | null {
  const value = (event.target as HTMLSelectElement).value.trim()
  return value || null
}

function overrideProject(index: number): string {
  return wizard.mappingDraft.artifact_overrides.find((item) => item.index === index)?.target_project ?? ''
}

function planned(index: number): ImportDestinationArtifactPlan | undefined {
  return planByIndex.value.get(index)
}

function sourceLabel(item: { repository: string; reference: string | null; name: string | null; version: string | null }): string {
  if (item.reference) return `${item.repository}:${item.reference}`
  if (item.name && item.version) return `${item.repository}/${item.name}:${item.version}`
  return item.repository
}

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return kind
}

async function loadProjects(): Promise<void> {
  projectsLoading.value = true
  projectsError.value = null
  try {
    const names = new Set<string>()
    let page = 1
    let total = Number.POSITIVE_INFINITY
    while (names.size < total) {
      const response = await listHarborProjects(page, 100, '')
      for (const project of response.items) names.add(project.name)
      total = response.pagination.total
      if (response.items.length === 0) break
      page += 1
    }
    projects.value = [...names].sort((left, right) => left.localeCompare(right))
  } catch {
    projectsError.value = 'Не удалось получить список TARGET Harbor projects.'
  } finally {
    projectsLoading.value = false
  }
}

onMounted(() => {
  void loadProjects()
})
</script>

<template>
  <section class="mapping-panel" aria-labelledby="mapping-title">
    <div class="mapping-panel__header">
      <div>
        <p class="eyebrow">Destination plan</p>
        <h3 id="mapping-title">Куда импортировать артефакты</h3>
        <p>
          Приоритет правил: override конкретного артефакта → mapping SOURCE project → default project типа.
          Пустые значения дополняются admin-managed defaults TARGET-контура; явные значения этой операции имеют приоритет.
          Harbor изменится только после подтверждённого plan.
        </p>
      </div>
      <button
        class="mapping-panel__refresh"
        type="button"
        :disabled="projectsLoading"
        aria-label="Обновить список TARGET Harbor projects"
        @click="loadProjects"
      >
        <RefreshCw :size="18" aria-hidden="true" />
      </button>
    </div>

    <div v-if="projectsError" class="mapping-message mapping-message--danger" role="alert">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>{{ projectsError }}</span>
    </div>
    <div v-else-if="projectsLoading" class="mapping-message" aria-live="polite">
      <RefreshCw :size="18" aria-hidden="true" />
      <span>Загрузка TARGET Harbor projects…</span>
    </div>
    <div v-else-if="projects.length === 0" class="mapping-message mapping-message--danger" role="alert">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>В TARGET Harbor не найдено доступных projects. Import остаётся заблокирован.</span>
    </div>

    <div v-if="!auth.canStartTransfers" class="mapping-message">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>Viewer видит подтверждённый destination plan только для чтения.</span>
    </div>

    <div class="mapping-grid">
      <label v-if="hasImages" class="mapping-field">
        <span>Default project · Container Images</span>
        <select
          :value="wizard.mappingDraft.container_image_project ?? ''"
          :disabled="!canEdit || projectsLoading"
          @change="wizard.setDefaultProject('container-image', projectFromEvent($event))"
        >
          <option value="">Admin default / не задан</option>
          <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
        </select>
      </label>
      <label v-if="hasCharts" class="mapping-field">
        <span>Default project · Helm Charts</span>
        <select
          :value="wizard.mappingDraft.helm_chart_project ?? ''"
          :disabled="!canEdit || projectsLoading"
          @change="wizard.setDefaultProject('helm-chart', projectFromEvent($event))"
        >
          <option value="">Admin default / не задан</option>
          <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
        </select>
      </label>
    </div>

    <div v-if="wizard.sourceProjects.length > 0" class="mapping-section">
      <h4>SOURCE project → TARGET project</h4>
      <div class="mapping-grid">
        <label v-for="sourceProject in wizard.sourceProjects" :key="sourceProject" class="mapping-field">
          <span>{{ sourceProject }}</span>
          <select
            :value="wizard.mappingDraft.project_mappings[sourceProject] ?? ''"
            :disabled="!canEdit || projectsLoading"
            @change="wizard.setSourceProjectMapping(sourceProject, projectFromEvent($event))"
          >
            <option value="">Admin mapping / default типа</option>
            <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
          </select>
        </label>
      </div>
    </div>

    <div class="mapping-section">
      <h4>Per-artifact override и итоговый TARGET reference</h4>
      <div class="mapping-table-wrap">
        <table class="mapping-table">
          <thead>
            <tr>
              <th>SOURCE</th>
              <th>Тип</th>
              <th>Override project</th>
              <th>TARGET reference</th>
              <th>Validation</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in wizard.preview?.artifacts ?? []" :key="item.index">
              <td>{{ sourceLabel(item) }}</td>
              <td>{{ kindLabel(item.artifact_type) }}</td>
              <td>
                <select
                  :value="overrideProject(item.index)"
                  :disabled="!canEdit || projectsLoading"
                  :aria-label="`Override project для ${sourceLabel(item)}`"
                  @change="wizard.setArtifactOverride(item.index, projectFromEvent($event))"
                >
                  <option value="">По общим правилам</option>
                  <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
                </select>
              </td>
              <td class="target-reference">
                {{ planned(item.index)?.final_reference ?? 'будет вычислен после проверки' }}
              </td>
              <td>
                <template v-if="planned(item.index)">
                  <span :class="['plan-state', `plan-state--${planned(item.index)!.classification.toLowerCase()}`]">
                    {{ stateLabels[planned(item.index)!.classification] }}
                  </span>
                  <small v-if="planned(item.index)!.error_code">
                    {{ planned(item.index)!.error_code }}<span v-if="planned(item.index)!.message"> · {{ planned(item.index)!.message }}</span>
                  </small>
                  <small v-else>
                    project {{ planned(item.index)!.project_exists ? 'exists' : 'missing' }} · push {{ planned(item.index)!.write_allowed ? 'allowed' : 'denied' }}
                  </small>
                </template>
                <span v-else class="muted">не проверено</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="wizard.mappingDirty" class="mapping-message mapping-message--warning" role="status">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>
        Mapping изменён. Import заблокирован до новой проверки.
        <template v-if="wizard.destinationPlan">Показанные TARGET references относятся к последнему confirmed plan.</template>
      </span>
    </div>
    <div v-else-if="wizard.destinationPlan?.valid" class="mapping-message mapping-message--success" role="status">
      <CheckCircle2 :size="18" aria-hidden="true" />
      <span>
        Destination plan подтверждён · {{ wizard.destinationPlan.plan_id.slice(0, 12) }}… · mapping policy rev {{ wizard.destinationPlan.mapping_policy_revision }}.
        Эта revision зафиксирована в plan и не меняется при последующей правке global defaults.
      </span>
    </div>
    <div v-else-if="wizard.destinationPlan" class="mapping-message mapping-message--danger" role="alert">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>Plan невалиден. Исправьте mapping/access/conflicts inspection до Import.</span>
    </div>

    <div v-if="auth.canStartTransfers" class="mapping-actions">
      <button
        class="button button--primary"
        type="button"
        :disabled="!canEdit || projectsLoading || wizard.busy !== null"
        @click="wizard.validateDestinationPlan()"
      >
        Проверить и подтвердить destination plan
      </button>
    </div>
  </section>
</template>

<style scoped>
.mapping-panel { margin: var(--space-6) 0; padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.mapping-panel__header { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
.mapping-panel__header h3, .mapping-section h4 { margin: 0; color: var(--color-text); }
.mapping-panel__header p:last-child { margin-bottom: 0; color: var(--color-text-muted); }
.mapping-panel__refresh { display: inline-grid; place-items: center; min-width: 40px; min-height: 40px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); cursor: pointer; }
.mapping-panel__refresh:disabled { cursor: wait; opacity: .6; }
.mapping-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); margin-top: var(--space-5); }
.mapping-field { display: grid; gap: var(--space-2); color: var(--color-text); font-weight: 700; }
.mapping-field select, .mapping-table select { min-height: 40px; width: 100%; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); padding: 0 var(--space-3); background: var(--color-surface); color: var(--color-text); font: inherit; font-weight: 400; }
.mapping-field select:disabled, .mapping-table select:disabled { background: var(--color-surface-subtle); color: var(--color-text-muted); }
.mapping-section { margin-top: var(--space-6); }
.mapping-table-wrap { margin-top: var(--space-3); overflow-x: auto; }
.mapping-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.mapping-table th, .mapping-table td { padding: var(--space-3); border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: top; }
.mapping-table th { color: var(--color-text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
.target-reference { min-width: 260px; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.plan-state { display: block; font-weight: 700; }
.plan-state--new, .plan-state--same { color: var(--color-success-text); }
.plan-state--conflict, .plan-state--unknown { color: var(--color-warning-text); }
.plan-state--error { color: var(--color-danger-text); }
.mapping-table small { display: block; margin-top: var(--space-1); color: var(--color-text-muted); }
.mapping-message { display: flex; align-items: flex-start; gap: var(--space-2); margin-top: var(--space-4); padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface-subtle); color: var(--color-text-muted); }
.mapping-message--success { background: var(--color-success-surface); color: var(--color-success-text); }
.mapping-message--warning { background: var(--color-warning-surface); color: var(--color-warning-text); }
.mapping-message--danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
.mapping-actions { display: flex; justify-content: flex-end; margin-top: var(--space-5); }
.muted { color: var(--color-text-muted); }
@media (max-width: 760px) {
  .mapping-grid { grid-template-columns: 1fr; }
  .mapping-panel { padding: var(--space-4); }
}
</style>