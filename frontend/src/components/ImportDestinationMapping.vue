<script setup lang="ts">
import { CheckCircle2, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'

import { listHarborProjects } from '@/api/exports'
import type {
  ImportArtifactPreview,
  ImportDestinationArtifactPlan,
  ImportPreviewState,
} from '@/api/imports'
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
  NEW: 'NEW · будет импортирован',
  SAME: 'SAME · уже есть, будет пропущен',
  CONFLICT: 'CONFLICT · другой digest/version',
  UNKNOWN: 'UNKNOWN · состояние не удалось определить',
  ERROR: 'ERROR · TARGET не прошёл проверку',
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

function repositoryParts(repository: string): { project: string; suffix: string } {
  const separator = repository.indexOf('/')
  if (separator < 0) return { project: repository, suffix: '' }
  return {
    project: repository.slice(0, separator),
    suffix: repository.slice(separator + 1),
  }
}

function defaultProject(item: ImportArtifactPreview): string | null {
  if (item.artifact_type === 'container-image') {
    return wizard.mappingDraft.container_image_project
  }
  if (item.artifact_type === 'helm-chart') {
    return wizard.mappingDraft.helm_chart_project
  }
  return null
}

function targetProject(item: ImportArtifactPreview): string | null {
  const override = wizard.mappingDraft.artifact_overrides.find(
    (candidate) => candidate.index === item.index,
  )?.target_project
  if (override) return override

  const { project } = repositoryParts(item.repository)
  return wizard.mappingDraft.project_mappings[project] ?? defaultProject(item)
}

function targetRule(item: ImportArtifactPreview): string {
  const override = wizard.mappingDraft.artifact_overrides.find(
    (candidate) => candidate.index === item.index,
  )?.target_project
  if (override) return 'Индивидуальное исключение'

  const { project } = repositoryParts(item.repository)
  if (wizard.mappingDraft.project_mappings[project]) return `Правило SOURCE project "${project}"`
  return 'Проект по умолчанию'
}

function localTargetReference(item: ImportArtifactPreview): string | null {
  const project = targetProject(item)
  if (!project) return null

  const { suffix } = repositoryParts(item.repository)
  const repository = project + (suffix ? `/${suffix}` : '')

  if (item.artifact_type === 'container-image' && item.reference) {
    const separator = item.reference.startsWith('sha256:') ? '@' : ':'
    return `${repository}${separator}${item.reference}`
  }
  if (item.artifact_type === 'helm-chart' && item.name && item.version) {
    return `${repository}/${item.name}:${item.version}`
  }
  return repository
}

function displayedTargetReference(item: ImportArtifactPreview): string {
  const confirmed = !wizard.mappingDirty ? planned(item.index)?.final_reference : null
  return confirmed ?? localTargetReference(item) ?? 'Проект назначения будет определён при проверке TARGET'
}

function validationLabel(item: ImportArtifactPreview): string {
  const confirmed = !wizard.mappingDirty ? planned(item.index) : undefined
  if (!confirmed) return 'TARGET ещё не проверен'
  return stateLabels[confirmed.classification]
}

function sourceLabel(item: {
  repository: string
  reference: string | null
  name: string | null
  version: string | null
}): string {
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
      const profileId = wizard.operation?.harbor_profile_id ?? wizard.selectedHarborProfileId
      const response = await listHarborProjects(page, 100, '', profileId ?? undefined)
      for (const project of response.items) names.add(project.name)
      total = response.pagination.total
      if (response.items.length === 0) break
      page += 1
    }
    projects.value = [...names].sort((left, right) => left.localeCompare(right))
  } catch {
    projectsError.value = 'Не удалось получить список проектов TARGET Harbor.'
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
        <p class="eyebrow">Назначение в TARGET Harbor</p>
        <h3 id="mapping-title">Куда будут импортированы артефакты</h3>
        <p>
          Обычно достаточно выбрать проекты назначения ниже. Имена images/charts и их версии
          сохраняются автоматически. Ничего в Harbor не изменится, пока вы отдельно не запустите Import.
        </p>
      </div>
      <button
        class="mapping-panel__refresh"
        type="button"
        :disabled="projectsLoading"
        aria-label="Обновить список проектов TARGET Harbor"
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
      <span>Получаем проекты из TARGET Harbor…</span>
    </div>
    <div v-else-if="projects.length === 0" class="mapping-message mapping-message--danger" role="alert">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>В TARGET Harbor не найдено доступных проектов. Import остаётся заблокирован.</span>
    </div>

    <div v-if="!auth.canStartTransfers" class="mapping-message">
      <ShieldAlert :size="18" aria-hidden="true" />
      <span>Режим просмотра: изменить назначение и запустить проверку может оператор или администратор.</span>
    </div>

    <section class="simple-destination" aria-labelledby="default-destination-title">
      <div>
        <h4 id="default-destination-title">Проекты назначения</h4>
        <p class="section-help">
          Эти значения применяются ко всем артефактам соответствующего типа, если ниже не задано исключение.
        </p>
      </div>

      <div class="mapping-grid">
        <label v-if="hasImages" class="mapping-field">
          <span>Container images → TARGET project</span>
          <select
            :value="wizard.mappingDraft.container_image_project ?? ''"
            :disabled="!canEdit || projectsLoading"
            @change="wizard.setDefaultProject('container-image', projectFromEvent($event))"
          >
            <option value="">Использовать настройку администратора</option>
            <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
          </select>
          <small>
            Например: <code>clever/admin-api:tag</code> → <code>clever/admin-api:tag</code>.
          </small>
        </label>

        <label v-if="hasCharts" class="mapping-field">
          <span>Helm charts → TARGET project</span>
          <select
            :value="wizard.mappingDraft.helm_chart_project ?? ''"
            :disabled="!canEdit || projectsLoading"
            @change="wizard.setDefaultProject('helm-chart', projectFromEvent($event))"
          >
            <option value="">Использовать настройку администратора</option>
            <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
          </select>
          <small>
            Имя chart и version сохраняются; меняется только TARGET project.
          </small>
        </label>
      </div>
    </section>

    <section class="destination-preview" aria-labelledby="destination-preview-title">
      <div>
        <h4 id="destination-preview-title">Что получится</h4>
        <p class="section-help">
          TARGET reference рассчитывается сразу из выбранных правил. Проверка Harbor ниже подтвердит
          существование проекта, право записи и состояние NEW / SAME / CONFLICT.
        </p>
      </div>

      <div class="artifact-routes">
        <article
          v-for="item in wizard.preview?.artifacts ?? []"
          :key="item.index"
          class="artifact-route"
        >
          <div class="artifact-route__source">
            <span class="route-label">SOURCE</span>
            <strong>{{ sourceLabel(item) }}</strong>
            <small>{{ kindLabel(item.artifact_type) }}</small>
          </div>
          <span class="artifact-route__arrow" aria-hidden="true">→</span>
          <div class="artifact-route__target">
            <span class="route-label">TARGET</span>
            <strong>{{ displayedTargetReference(item) }}</strong>
            <small>{{ targetRule(item) }}</small>
          </div>
          <div class="artifact-route__validation">
            <span
              v-if="!wizard.mappingDirty && planned(item.index)"
              :class="['plan-state', `plan-state--${planned(item.index)!.classification.toLowerCase()}`]"
            >
              {{ validationLabel(item) }}
            </span>
            <span v-else class="muted">{{ validationLabel(item) }}</span>
            <small v-if="!wizard.mappingDirty && planned(item.index)?.error_code">
              {{ planned(item.index)!.error_code }}
              <span v-if="planned(item.index)!.message"> · {{ planned(item.index)!.message }}</span>
            </small>
          </div>
        </article>
      </div>
    </section>

    <details v-if="wizard.sourceProjects.length > 0" class="advanced-section">
      <summary>Расширенные правила для целого SOURCE project</summary>
      <p class="section-help">
        Используйте только если весь SOURCE project должен попасть в другой TARGET project.
        Если оставить «Использовать проект по умолчанию», будут применены настройки по типу артефакта выше.
      </p>
      <div class="mapping-grid">
        <label
          v-for="sourceProject in wizard.sourceProjects"
          :key="sourceProject"
          class="mapping-field"
        >
          <span>SOURCE project «{{ sourceProject }}»</span>
          <select
            :value="wizard.mappingDraft.project_mappings[sourceProject] ?? ''"
            :disabled="!canEdit || projectsLoading"
            @change="wizard.setSourceProjectMapping(sourceProject, projectFromEvent($event))"
          >
            <option value="">Использовать проект по умолчанию</option>
            <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
          </select>
        </label>
      </div>
      <p class="advanced-priority">
        Приоритет: индивидуальное исключение → правило SOURCE project → проект по умолчанию.
      </p>
    </details>

    <details class="advanced-section">
      <summary>Индивидуальные исключения для отдельных артефактов</summary>
      <p class="section-help">
        Используйте только если один конкретный image или chart должен попасть в другой TARGET project.
      </p>
      <div class="mapping-table-wrap">
        <table class="mapping-table">
          <thead>
            <tr>
              <th>Артефакт SOURCE</th>
              <th>Исключение TARGET project</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in wizard.preview?.artifacts ?? []" :key="item.index">
              <td>{{ sourceLabel(item) }}</td>
              <td>
                <select
                  :value="overrideProject(item.index)"
                  :disabled="!canEdit || projectsLoading"
                  :aria-label="`Индивидуальный TARGET project для ${sourceLabel(item)}`"
                  @change="wizard.setArtifactOverride(item.index, projectFromEvent($event))"
                >
                  <option value="">Использовать общее правило</option>
                  <option v-for="project in projects" :key="project" :value="project">{{ project }}</option>
                </select>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>

    <div
      v-if="wizard.mappingDirty && wizard.destinationPlan"
      class="mapping-message mapping-message--warning"
      role="status"
    >
      <ShieldAlert :size="18" aria-hidden="true" />
      <div>
        <strong>Настройки назначения изменились.</strong>
        <span>
          Перед Import нужно ещё раз проверить TARGET. Показанные выше адреса уже рассчитаны по новым
          настройкам, но Harbor пока не проверен и ничего в нём не изменялось.
        </span>
      </div>
    </div>
    <div
      v-else-if="wizard.mappingDirty"
      class="mapping-message mapping-message--warning"
      role="status"
    >
      <ShieldAlert :size="18" aria-hidden="true" />
      <div>
        <strong>Назначение ещё не проверено.</strong>
        <span>
          Нажмите «Проверить TARGET»: Portal только прочитает состояние Harbor и не будет импортировать артефакты.
        </span>
      </div>
    </div>
    <div
      v-else-if="wizard.destinationPlan?.valid"
      class="mapping-message mapping-message--success"
      role="status"
    >
      <CheckCircle2 :size="18" aria-hidden="true" />
      <div>
        <strong>TARGET проверен.</strong>
        <span>
          Проекты и права записи подтверждены. Состояния NEW / SAME / CONFLICT актуальны для этой проверки.
        </span>
      </div>
    </div>
    <div
      v-else-if="wizard.destinationPlan"
      class="mapping-message mapping-message--danger"
      role="alert"
    >
      <ShieldAlert :size="18" aria-hidden="true" />
      <div>
        <strong>TARGET не готов к Import.</strong>
        <span>Проверьте отсутствующие проекты, права записи и строки с ERROR / UNKNOWN.</span>
      </div>
    </div>

    <div v-if="auth.canStartTransfers" class="mapping-actions">
      <div class="mapping-actions__help">
        <strong>Проверка безопасна.</strong>
        <span>Portal проверит проекты, права и существующие artifacts. Import не запускается.</span>
      </div>
      <button
        class="button button--primary"
        type="button"
        :disabled="!canEdit || projectsLoading || wizard.busy !== null"
        @click="wizard.validateDestinationPlan()"
      >
        {{ wizard.busy === 'destination-plan' ? 'Проверяем TARGET…' : 'Проверить TARGET' }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.mapping-panel {
  margin: var(--space-6) 0;
  padding: var(--space-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
}
.mapping-panel__header {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: flex-start;
}
.mapping-panel__header h3,
.simple-destination h4,
.destination-preview h4 {
  margin: 0;
  color: var(--color-text);
}
.mapping-panel__header p:last-child {
  max-width: 850px;
  margin-bottom: 0;
  color: var(--color-text-muted);
  line-height: 1.55;
}
.mapping-panel__refresh {
  display: inline-grid;
  place-items: center;
  min-width: 40px;
  min-height: 40px;
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
}
.mapping-panel__refresh:disabled {
  cursor: wait;
  opacity: .6;
}
.simple-destination,
.destination-preview {
  margin-top: var(--space-6);
}
.section-help {
  margin: var(--space-2) 0 0;
  color: var(--color-text-muted);
  line-height: 1.5;
}
.mapping-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-4);
}
.mapping-field {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text);
  font-weight: 700;
}
.mapping-field small {
  color: var(--color-text-muted);
  font-weight: 400;
  line-height: 1.4;
}
.mapping-field select,
.mapping-table select {
  min-height: 40px;
  width: 100%;
  border: 1px solid var(--color-border-control);
  border-radius: var(--radius-md);
  padding: 0 var(--space-3);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-weight: 400;
}
.mapping-field select:disabled,
.mapping-table select:disabled {
  background: var(--color-surface-subtle);
  color: var(--color-text-muted);
}
.artifact-routes {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.artifact-route {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) minmax(180px, .6fr);
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}
.artifact-route__source,
.artifact-route__target,
.artifact-route__validation {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}
.artifact-route strong {
  overflow-wrap: anywhere;
}
.artifact-route__target strong {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}
.artifact-route small {
  color: var(--color-text-muted);
}
.artifact-route__arrow {
  color: var(--color-action);
  font-size: 20px;
  font-weight: 800;
}
.route-label {
  color: var(--color-text-muted);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .06em;
}
.advanced-section {
  margin-top: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.advanced-section summary {
  cursor: pointer;
  color: var(--color-text);
  font-weight: 700;
}
.advanced-priority {
  margin: var(--space-3) 0 0;
  color: var(--color-text-muted);
  font-size: 13px;
}
.mapping-table-wrap {
  margin-top: var(--space-3);
  overflow-x: auto;
}
.mapping-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
.mapping-table th,
.mapping-table td {
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  vertical-align: top;
}
.mapping-table th {
  color: var(--color-text-muted);
  font-size: 12px;
}
.plan-state {
  display: block;
  font-weight: 700;
}
.plan-state--new,
.plan-state--same {
  color: var(--color-success-text);
}
.plan-state--conflict,
.plan-state--unknown {
  color: var(--color-warning-text);
}
.plan-state--error {
  color: var(--color-danger-text);
}
.mapping-message {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-top: var(--space-4);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
  color: var(--color-text-muted);
}
.mapping-message > div {
  display: grid;
  gap: var(--space-1);
}
.mapping-message--success {
  background: var(--color-success-surface);
  color: var(--color-success-text);
}
.mapping-message--warning {
  background: var(--color-warning-surface);
  color: var(--color-warning-text);
}
.mapping-message--danger {
  background: var(--color-danger-surface);
  color: var(--color-danger-text);
}
.mapping-actions {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: center;
  margin-top: var(--space-5);
}
.mapping-actions__help {
  display: grid;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: 13px;
}
.muted {
  color: var(--color-text-muted);
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (max-width: 960px) {
  .artifact-route {
    grid-template-columns: 1fr;
  }
  .artifact-route__arrow {
    transform: rotate(90deg);
  }
}
@media (max-width: 760px) {
  .mapping-grid {
    grid-template-columns: 1fr;
  }
  .mapping-panel {
    padding: var(--space-4);
  }
  .mapping-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
