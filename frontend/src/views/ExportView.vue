<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Box,
  CheckCircle2,
  Download,
  FileArchive,
  PackageCheck,
  RefreshCw,
  Search,
  ShipWheel,
  XCircle,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  apiErrorInfo,
  createExportDownloadTicket,
  type ArtifactStatus,
  type OperationStatus,
} from '@/api/exports'
import { useExportWizardStore } from '@/stores/exportWizard'
import { useRuntimeStore } from '@/stores/runtime'

const wizard = useExportWizardStore()
const runtime = useRuntimeStore()
const downloadError = ref<string | null>(null)
const now = ref(Date.now())
let clock: ReturnType<typeof setInterval> | null = null

const steps = [
  { id: 1, label: 'Выбор' },
  { id: 2, label: 'Проверка' },
  { id: 3, label: 'Выполнение' },
  { id: 4, label: 'Готово' },
] as const

const phaseLabels: Record<OperationStatus, string> = {
  CREATED: 'Создана',
  VALIDATING: 'Проверка SOURCE',
  RUNNING: 'Выгрузка артефактов',
  PACKAGING: 'Сборка bundle',
  VERIFYING: 'Проверка bundle',
  UPLOADED: 'Загружено',
  DISCOVERED: 'Обнаружено',
  READY: 'Готово к импорту',
  IMPORTING: 'Импорт',
  VERIFYING_TARGET: 'Проверка TARGET',
  COMPLETED: 'Завершено',
  FAILED: 'Ошибка',
  REJECTED: 'Отклонено',
  CANCELLED: 'Отменено',
}

const artifactStatusLabels: Record<ArtifactStatus, string> = {
  PENDING: 'Ожидает',
  RUNNING: 'Выполняется',
  IMPORTED: 'Импортирован',
  SKIPPED: 'Пропущен',
  CONFLICT: 'Конфликт',
  FAILED: 'Ошибка',
  VERIFIED: 'Проверен',
}

const elapsed = computed(() => {
  const started = wizard.operation?.started_at
  if (!started) return '—'
  const finished = wizard.operation?.finished_at
  const end = finished ? new Date(finished).getTime() : now.value
  const seconds = Math.max(0, Math.floor((end - new Date(started).getTime()) / 1000))
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return minutes > 0 ? `${minutes} мин ${rest} с` : `${rest} с`
})

const progressPercent = computed(() => {
  const progress = wizard.operation?.progress
  if (!progress || progress.progress_total <= 0) return null
  return Math.round((progress.progress_current / progress.progress_total) * 100)
})

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return 'размер неизвестен'
  const units = ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  const digits = index === 0 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${units[index]}`
}

function shortDigest(digest: string | null): string {
  if (!digest) return '—'
  return digest.length > 24 ? `${digest.slice(0, 18)}…${digest.slice(-8)}` : digest
}

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return 'OCI (не поддерживается)'
}

async function searchProjects(): Promise<void> {
  await wizard.loadProjects(1)
}

async function searchRepositories(): Promise<void> {
  await wizard.loadRepositories(1)
}

async function searchArtifacts(): Promise<void> {
  await wizard.loadArtifacts(1)
}

async function downloadBundle(): Promise<void> {
  if (!wizard.operation || !wizard.bundle) return
  downloadError.value = null
  try {
    const ticket = await createExportDownloadTicket(wizard.operation.id)
    window.location.assign(ticket.download_url)
  } catch (error) {
    downloadError.value = apiErrorInfo(
      error,
      'Не удалось подготовить безопасное скачивание bundle.',
    ).message
  }
}

function downloadSidecar(): void {
  if (!wizard.bundle) return
  downloadError.value = null
  const text = `${wizard.bundle.sha256}  ${wizard.bundle.archive_name}\n`
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `${wizard.bundle.archive_name}.sha256`
  anchor.click()
  URL.revokeObjectURL(href)
}

onMounted(async () => {
  if (!runtime.contour) {
    await runtime.loadRuntime()
  }
  if (runtime.contour === 'SOURCE') {
    await wizard.initialize()
  }
  clock = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  wizard.stopPolling()
  if (clock !== null) clearInterval(clock)
})
</script>

<template>
  <section class="export-page" aria-labelledby="export-title">
    <header class="page-heading">
      <div>
        <p class="eyebrow">SOURCE · офлайн-передача</p>
        <h1 id="export-title">Отправка артефактов</h1>
        <p class="lead">
          Выберите точные версии из локального Harbor, проверьте digest и создайте подписанный
          Offline Bundle v1 для физического переноса в TARGET.
        </p>
      </div>
      <div v-if="wizard.connection" class="connection-chip" aria-label="Состояние локального Harbor">
        <CheckCircle2 :size="18" aria-hidden="true" />
        Harbor {{ wizard.connection.version || 'подключён' }}
      </div>
    </header>

    <div v-if="runtime.contour !== 'SOURCE'" class="notice notice--danger" role="alert">
      <XCircle :size="22" aria-hidden="true" />
      <div>
        <strong>Экспорт доступен только в контуре SOURCE.</strong>
        <p>На TARGET используйте workflow приёма. Сервер также отклонит попытку запуска экспорта.</p>
      </div>
    </div>

    <template v-else>
      <ol class="stepper" aria-label="Этапы экспорта">
        <li
          v-for="item in steps"
          :key="item.id"
          :class="['stepper__item', { 'stepper__item--active': wizard.step === item.id, 'stepper__item--done': wizard.step > item.id }]"
          :aria-current="wizard.step === item.id ? 'step' : undefined"
        >
          <span class="stepper__number">{{ item.id }}</span>
          <span>{{ item.label }}</span>
        </li>
      </ol>

      <div v-if="wizard.error" class="notice notice--danger" role="alert">
        <AlertTriangle :size="22" aria-hidden="true" />
        <div>
          <strong>{{ wizard.error.message }}</strong>
          <p class="error-code">Код: {{ wizard.error.code }}</p>
        </div>
      </div>

      <section v-if="wizard.step === 1" class="wizard-card" aria-labelledby="selection-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Шаг 1 из 4</p>
            <h2 id="selection-title">Выберите артефакты</h2>
          </div>
          <div class="selection-summary" aria-live="polite">
            <strong>{{ wizard.selectedCount }}</strong> выбрано · {{ formatBytes(wizard.selectedKnownBytes) }}
            <span v-if="wizard.selectedUnknownSizeCount">
              · без размера: {{ wizard.selectedUnknownSizeCount }}
            </span>
          </div>
        </div>

        <div class="browser-grid">
          <section class="browser-panel" aria-labelledby="projects-title">
            <h3 id="projects-title">1. Проект</h3>
            <form class="search-row" @submit.prevent="searchProjects">
              <label class="sr-only" for="project-search">Поиск проекта</label>
              <input id="project-search" v-model="wizard.projectSearch" type="search" placeholder="Найти проект" maxlength="256" />
              <button class="icon-button" type="submit" aria-label="Искать проект">
                <Search :size="18" aria-hidden="true" />
              </button>
            </form>
            <div v-if="wizard.busy === 'projects'" class="muted">Загрузка…</div>
            <div v-else-if="wizard.projects.length === 0" class="empty-inline">Проекты не найдены.</div>
            <button
              v-for="project in wizard.projects"
              :key="project.name"
              type="button"
              :class="['browser-item', { 'browser-item--selected': wizard.selectedProject === project.name }]"
              @click="wizard.chooseProject(project.name)"
            >
              <span>{{ project.name }}</span>
              <span class="item-meta">{{ project.public ? 'public' : 'private' }}</span>
            </button>
            <div v-if="wizard.projectTotal > 25" class="pagination" aria-label="Страницы проектов">
              <button type="button" :disabled="wizard.projectPage <= 1" @click="wizard.loadProjects(wizard.projectPage - 1)">Назад</button>
              <span>{{ wizard.projectPage }} / {{ Math.ceil(wizard.projectTotal / 25) }}</span>
              <button type="button" :disabled="wizard.projectPage * 25 >= wizard.projectTotal" @click="wizard.loadProjects(wizard.projectPage + 1)">Далее</button>
            </div>
          </section>

          <section class="browser-panel" aria-labelledby="repositories-title">
            <h3 id="repositories-title">2. Репозиторий</h3>
            <form class="search-row" @submit.prevent="searchRepositories">
              <label class="sr-only" for="repository-search">Поиск репозитория</label>
              <input id="repository-search" v-model="wizard.repositorySearch" type="search" placeholder="Найти репозиторий" maxlength="256" :disabled="!wizard.selectedProject" />
              <button class="icon-button" type="submit" aria-label="Искать репозиторий" :disabled="!wizard.selectedProject">
                <Search :size="18" aria-hidden="true" />
              </button>
            </form>
            <div v-if="!wizard.selectedProject" class="empty-inline">Сначала выберите проект.</div>
            <div v-else-if="wizard.busy === 'repositories'" class="muted">Загрузка…</div>
            <div v-else-if="wizard.repositories.length === 0" class="empty-inline">Репозитории не найдены.</div>
            <button
              v-for="repository in wizard.repositories"
              :key="repository.name"
              type="button"
              :class="['browser-item', { 'browser-item--selected': wizard.selectedRepository === repository.name }]"
              @click="wizard.chooseRepository(repository.name)"
            >
              <span>{{ repository.name }}</span>
              <span class="item-meta">{{ repository.artifact_count ?? '—' }} artifacts</span>
            </button>
            <div v-if="wizard.repositoryTotal > 25" class="pagination" aria-label="Страницы репозиториев">
              <button type="button" :disabled="wizard.repositoryPage <= 1" @click="wizard.loadRepositories(wizard.repositoryPage - 1)">Назад</button>
              <span>{{ wizard.repositoryPage }} / {{ Math.ceil(wizard.repositoryTotal / 25) }}</span>
              <button type="button" :disabled="wizard.repositoryPage * 25 >= wizard.repositoryTotal" @click="wizard.loadRepositories(wizard.repositoryPage + 1)">Далее</button>
            </div>
          </section>
        </div>

        <section class="artifact-panel" aria-labelledby="artifacts-title">
          <div class="artifact-panel__heading">
            <div>
              <h3 id="artifacts-title">3. Точные tag / version</h3>
              <p>Выбор хранится при поиске и переходе между страницами.</p>
            </div>
            <form class="search-row search-row--wide" @submit.prevent="searchArtifacts">
              <label class="sr-only" for="artifact-search">Поиск tag, version или digest</label>
              <input id="artifact-search" v-model="wizard.artifactSearch" type="search" placeholder="Tag, version или digest" maxlength="256" :disabled="!wizard.selectedRepository" />
              <button class="icon-button" type="submit" aria-label="Искать артефакт" :disabled="!wizard.selectedRepository">
                <Search :size="18" aria-hidden="true" />
              </button>
            </form>
          </div>

          <div v-if="!wizard.selectedRepository" class="empty-state">Выберите проект и репозиторий, чтобы увидеть версии.</div>
          <div v-else-if="wizard.busy === 'artifacts'" class="empty-state">Загрузка артефактов…</div>
          <div v-else-if="wizard.artifacts.length === 0" class="empty-state">По этому фильтру артефакты не найдены.</div>
          <div v-else class="artifact-list">
            <article v-for="artifact in wizard.artifacts" :key="artifact.digest" class="artifact-card">
              <div class="artifact-card__main">
                <div class="kind-icon" aria-hidden="true">
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
                Не поддерживается export v1
              </div>
              <div v-else-if="wizard.referencesFor(artifact).length === 0" class="unsupported">
                Нет явной версии/tag — выбрать нельзя
              </div>
              <div v-else class="reference-list">
                <label v-for="reference in wizard.referencesFor(artifact)" :key="reference" class="reference-choice">
                  <input
                    type="checkbox"
                    :checked="wizard.isSelected(artifact, reference)"
                    @change="wizard.toggleArtifact(artifact, reference)"
                  />
                  <span>{{ reference }}</span>
                </label>
              </div>
            </article>
          </div>
          <div v-if="wizard.artifactTotal > 25" class="pagination" aria-label="Страницы артефактов">
            <button type="button" :disabled="wizard.artifactPage <= 1" @click="wizard.loadArtifacts(wizard.artifactPage - 1)">Назад</button>
            <span>{{ wizard.artifactPage }} / {{ Math.ceil(wizard.artifactTotal / 25) }}</span>
            <button type="button" :disabled="wizard.artifactPage * 25 >= wizard.artifactTotal" @click="wizard.loadArtifacts(wizard.artifactPage + 1)">Далее</button>
          </div>
        </section>

        <div class="actions actions--end">
          <button class="primary-button" type="button" :disabled="wizard.selectedCount === 0 || wizard.busy === 'preview'" @click="wizard.preparePreview">
            Проверить выбранное
            <ArrowRight :size="18" aria-hidden="true" />
          </button>
        </div>
      </section>

      <section v-else-if="wizard.step === 2" class="wizard-card" aria-labelledby="preview-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Шаг 2 из 4</p>
            <h2 id="preview-title">Параметры и финальная проверка</h2>
          </div>
          <span class="selection-summary">{{ wizard.preview?.artifacts.length ?? 0 }} artifacts</span>
        </div>

        <div class="identity-grid">
          <div class="info-box">
            <span>Контур</span>
            <strong>SOURCE</strong>
          </div>
          <div class="info-box">
            <span>Локальный Harbor</span>
            <strong>{{ wizard.connection?.version || 'подключён' }}</strong>
            <small v-if="wizard.connection?.auth_mode">Auth: {{ wizard.connection.auth_mode }}</small>
          </div>
          <div class="info-box">
            <span>Оценка payload</span>
            <strong>{{ formatBytes(wizard.preview?.estimated_payload_bytes) }}</strong>
            <small v-if="wizard.selectedUnknownSizeCount">Есть {{ wizard.selectedUnknownSizeCount }} artifact без известного размера</small>
          </div>
        </div>

        <label class="field" for="export-comment">
          <span>Комментарий оператора <small>необязательно</small></span>
          <textarea id="export-comment" v-model="wizard.comment" maxlength="2000" rows="3" placeholder="Например: перенос релиза 2026.09" />
          <small>{{ wizard.comment.length }} / 2000</small>
        </label>

        <div class="preview-table-wrap">
          <table class="preview-table">
            <thead>
              <tr><th>Тип</th><th>Артефакт</th><th>Reference</th><th>Digest SOURCE</th><th>Размер</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in wizard.preview?.artifacts" :key="`${item.project}/${item.repository}:${item.reference}`">
                <td>{{ kindLabel(item.kind) }}</td>
                <td>{{ item.project }}/{{ item.repository }}</td>
                <td><strong>{{ item.reference }}</strong></td>
                <td class="digest" :title="item.source_digest">{{ shortDigest(item.source_digest) }}</td>
                <td>{{ formatBytes(item.size_bytes) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="notice notice--info">
          <FileArchive :size="22" aria-hidden="true" />
          <div>
            <strong>После создания bundle сеть между контурами не используется.</strong>
            <p>Скачайте archive и `.sha256`, затем перенесите оба файла на разрешённом физическом носителе в TARGET.</p>
          </div>
        </div>

        <div class="actions">
          <button class="secondary-button" type="button" @click="wizard.backToSelection">
            <ArrowLeft :size="18" aria-hidden="true" /> Назад
          </button>
          <button class="primary-button" type="button" :disabled="wizard.busy === 'start'" @click="wizard.start">
            Запустить экспорт <ArrowRight :size="18" aria-hidden="true" />
          </button>
        </div>
      </section>

      <section v-else-if="wizard.step === 3" class="wizard-card" aria-labelledby="progress-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Шаг 3 из 4</p>
            <h2 id="progress-title">Выполнение export-операции</h2>
          </div>
          <button class="secondary-button secondary-button--compact" type="button" :disabled="wizard.busy !== null" @click="wizard.refreshOperation()">
            <RefreshCw :size="17" aria-hidden="true" /> Обновить
          </button>
        </div>

        <div v-if="wizard.operation" class="operation-summary">
          <div class="info-box">
            <span>Delivery</span>
            <strong>{{ wizard.operation.delivery_id || 'создаётся' }}</strong>
          </div>
          <div class="info-box">
            <span>Фаза</span>
            <strong>{{ phaseLabels[wizard.operation.status] }}</strong>
          </div>
          <div class="info-box">
            <span>Выполнено</span>
            <strong>{{ wizard.operation.progress.completed_artifacts }} / {{ wizard.operation.progress.total_artifacts }}</strong>
          </div>
          <div class="info-box">
            <span>Прошло</span>
            <strong>{{ elapsed }}</strong>
            <small>ETA не вычисляется</small>
          </div>
        </div>

        <div v-if="wizard.operation && progressPercent !== null" class="progress-block">
          <div class="progress-label"><span>Грубый прогресс по обработанным artifacts</span><strong>{{ progressPercent }}%</strong></div>
          <progress :value="wizard.operation.progress.progress_current" :max="wizard.operation.progress.progress_total">
            {{ progressPercent }}%
          </progress>
        </div>

        <div v-if="wizard.operation" class="operation-artifacts">
          <article v-for="artifact in wizard.operation.artifacts" :key="artifact.id" class="operation-artifact">
            <div>
              <strong>{{ artifact.repository }}</strong>
              <p>{{ artifact.reference || artifact.version || artifact.name || '—' }}</p>
            </div>
            <div class="artifact-result">
              <span :class="['status-pill', `status-pill--${artifact.status.toLowerCase()}`]">{{ artifactStatusLabels[artifact.status] }}</span>
              <small v-if="artifact.error_message">{{ artifact.error_message }}</small>
            </div>
          </article>
        </div>

        <div v-if="wizard.isTerminalFailure && wizard.operation" class="notice notice--danger" role="alert">
          <AlertTriangle :size="22" aria-hidden="true" />
          <div>
            <strong>{{ wizard.operation.status === 'CANCELLED' ? 'Экспорт отменён.' : 'Экспорт завершился ошибкой.' }}</strong>
            <p v-if="wizard.operation.error_message">{{ wizard.operation.error_message }}</p>
            <p v-if="wizard.operation.error_code" class="error-code">Код: {{ wizard.operation.error_code }}</p>
            <p>Частичный archive не считается готовым и не предлагается для переноса.</p>
            <RouterLink to="/history">Перейти к истории операций</RouterLink>
          </div>
        </div>

        <div class="actions">
          <button v-if="wizard.canCancel" class="danger-button" type="button" :disabled="wizard.busy === 'cancel'" @click="wizard.cancel">
            Отменить операцию
          </button>
          <button v-if="wizard.isTerminalFailure" class="secondary-button" type="button" @click="wizard.reset">
            Начать новый экспорт
          </button>
        </div>
      </section>

      <section v-else class="wizard-card ready-card" aria-labelledby="ready-title">
        <div class="ready-icon" aria-hidden="true"><PackageCheck :size="36" /></div>
        <p class="eyebrow">Шаг 4 из 4</p>
        <h2 id="ready-title">Bundle готов к физическому переносу</h2>
        <p class="lead">Операция достигла `COMPLETED`; backend зафиксировал filename, размер и SHA-256 готового archive.</p>

        <div v-if="wizard.bundle" class="ready-grid">
          <div class="info-box"><span>Delivery ID</span><strong>{{ wizard.bundle.delivery_id }}</strong></div>
          <div class="info-box"><span>Файл</span><strong>{{ wizard.bundle.archive_name }}</strong></div>
          <div class="info-box"><span>Размер</span><strong>{{ formatBytes(wizard.bundle.archive_size) }}</strong></div>
          <div class="info-box info-box--wide"><span>SHA-256</span><code>{{ wizard.bundle.sha256 }}</code></div>
        </div>

        <div v-if="wizard.operation" class="ready-summary">
          <strong>Проверено artifacts: {{ wizard.operation.progress.successful_artifacts }} / {{ wizard.operation.progress.total_artifacts }}</strong>
          <span>Все готовые export artifacts имеют terminal verified state.</span>
        </div>

        <div class="notice notice--warning">
          <AlertTriangle :size="22" aria-hidden="true" />
          <div>
            <strong>На носитель нужно скопировать два файла.</strong>
            <p>Перенесите сам `.htp.tar.gz` и соответствующий `.sha256`. TARGET использует sidecar как readiness/integrity metadata; SHA-256 не заменяет цифровую подпись bundle.</p>
          </div>
        </div>

        <p v-if="downloadError" class="download-error" role="alert">{{ downloadError }}</p>
        <div class="download-actions">
          <button class="primary-button" type="button" :disabled="!wizard.bundle" @click="downloadBundle">
            <Download :size="19" aria-hidden="true" /> Скачать bundle
          </button>
          <button class="secondary-button" type="button" :disabled="!wizard.bundle" @click="downloadSidecar">
            <Download :size="19" aria-hidden="true" /> Скачать `.sha256`
          </button>
        </div>

        <div class="next-steps">
          <h3>Что дальше</h3>
          <ol>
            <li>Сверьте, что оба файла имеют одинаковое базовое имя.</li>
            <li>Скопируйте их на разрешённый физический носитель по вашей организационной процедуре.</li>
            <li>В TARGET откройте workflow «Приём» и загрузите/обнаружьте bundle. Не распаковывайте archive вручную.</li>
          </ol>
        </div>

        <button class="text-button" type="button" @click="wizard.reset">Создать ещё один export</button>
      </section>
    </template>
  </section>
</template>

<style scoped>
.export-page { display: grid; gap: var(--space-6); color: var(--color-deep-harbor); }
.page-heading, .section-heading, .artifact-panel__heading, .actions, .progress-label { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: var(--space-2); font-size: clamp(28px, 4vw, 40px); }
h2 { margin-bottom: 0; font-size: 24px; }
h3 { margin-bottom: var(--space-2); font-size: 16px; }
.eyebrow { margin-bottom: var(--space-2); color: var(--color-bridge-blue); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.lead { max-width: 760px; margin-bottom: 0; color: var(--color-steel); line-height: 1.6; }
.muted, .item-meta, small { color: var(--color-steel); }
.connection-chip { display: inline-flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-full); background: var(--color-mint); color: #065f46; white-space: nowrap; }
.stepper { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-2); padding: 0; margin: 0; list-style: none; }
.stepper__item { display: flex; align-items: center; gap: var(--space-2); min-height: 48px; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-mist); border-radius: var(--radius-md); color: var(--color-steel); background: white; }
.stepper__item--active { border-color: var(--color-bridge-blue); color: var(--color-deep-harbor); box-shadow: var(--shadow-sm); }
.stepper__item--done { border-color: var(--color-transfer-green); color: #065f46; background: var(--color-mint); }
.stepper__number { display: grid; place-items: center; width: 26px; height: 26px; border-radius: 50%; background: var(--color-fog-gray); font-weight: 800; }
.wizard-card { display: grid; gap: var(--space-6); padding: var(--space-6); border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: var(--color-cloud-white); box-shadow: var(--shadow-sm); }
.selection-summary { padding: var(--space-2) var(--space-3); border-radius: var(--radius-md); background: var(--color-sky); color: #1e40af; }
.browser-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
.browser-panel, .artifact-panel { display: grid; align-content: start; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: var(--color-fog-gray); }
.artifact-panel { gap: var(--space-4); }
.search-row { display: grid; grid-template-columns: minmax(0, 1fr) 42px; gap: var(--space-2); }
.search-row--wide { width: min(360px, 100%); }
input[type='search'], textarea { width: 100%; border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: white; color: var(--color-deep-harbor); font: inherit; }
input[type='search'] { min-height: 42px; padding: 0 var(--space-3); }
textarea { padding: var(--space-3); resize: vertical; }
input:focus-visible, textarea:focus-visible, button:focus-visible, a:focus-visible { outline: 3px solid rgba(37, 99, 235, .25); outline-offset: 2px; }
.icon-button { min-width: 42px; min-height: 42px; display: grid; place-items: center; border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: white; cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .55; }
.browser-item { min-height: 44px; display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid transparent; border-radius: var(--radius-md); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.browser-item:hover, .browser-item:focus-visible { background: white; }
.browser-item--selected { border-color: var(--color-bridge-blue); background: var(--color-sky); }
.empty-inline, .empty-state { padding: var(--space-4); color: var(--color-steel); text-align: center; }
.artifact-list, .operation-artifacts { display: grid; gap: var(--space-3); }
.artifact-card, .operation-artifact { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); padding: var(--space-4); border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: white; }
.artifact-card__main { display: flex; align-items: flex-start; gap: var(--space-3); min-width: 0; }
.kind-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: var(--radius-md); background: var(--color-sky); color: var(--color-bridge-blue); }
.digest { margin: var(--space-1) 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
.reference-list { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-2); }
.reference-choice { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 38px; padding: var(--space-1) var(--space-3); border: 1px solid var(--color-mist); border-radius: var(--radius-full); background: var(--color-fog-gray); cursor: pointer; }
.reference-choice:has(input:checked) { border-color: var(--color-bridge-blue); background: var(--color-sky); }
.unsupported { max-width: 240px; color: var(--color-steel); font-size: 13px; text-align: right; }
.pagination { display: flex; align-items: center; justify-content: center; gap: var(--space-3); padding-top: var(--space-2); }
.pagination button, .text-button { border: 0; background: transparent; color: var(--color-bridge-blue); cursor: pointer; text-decoration: underline; }
.identity-grid, .operation-summary, .ready-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-3); }
.operation-summary { grid-template-columns: repeat(4, 1fr); }
.info-box { display: grid; gap: var(--space-1); padding: var(--space-4); border: 1px solid var(--color-mist); border-radius: var(--radius-md); background: var(--color-fog-gray); min-width: 0; }
.info-box > span { color: var(--color-steel); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.info-box strong, .info-box code { overflow-wrap: anywhere; }
.info-box--wide { grid-column: 1 / -1; }
.field { display: grid; gap: var(--space-2); }
.field > span { font-weight: 700; }
.preview-table-wrap { overflow-x: auto; }
.preview-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.preview-table th, .preview-table td { padding: var(--space-3); border-bottom: 1px solid var(--color-mist); text-align: left; vertical-align: top; }
.preview-table th { color: var(--color-steel); font-size: 12px; text-transform: uppercase; }
.notice { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); }
.notice p { margin: var(--space-1) 0 0; line-height: 1.5; }
.notice--info { background: var(--color-sky); color: #1e3a8a; }
.notice--warning { background: var(--color-sand); color: #78350f; }
.notice--danger { background: var(--color-rose); color: #991b1b; }
.error-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.actions { align-items: center; }
.actions--end { justify-content: flex-end; }
.primary-button, .secondary-button, .danger-button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); border-radius: var(--radius-md); padding: 0 var(--space-4); font: inherit; font-weight: 700; cursor: pointer; }
.primary-button { border: 1px solid var(--color-bridge-blue); background: var(--color-bridge-blue); color: white; }
.secondary-button { border: 1px solid var(--color-mist); background: white; color: var(--color-deep-harbor); }
.secondary-button--compact { min-height: 38px; padding-inline: var(--space-3); }
.danger-button { border: 1px solid var(--color-stop-red); background: var(--color-stop-red); color: white; }
.progress-block { display: grid; gap: var(--space-2); }
progress { width: 100%; height: 12px; accent-color: var(--color-bridge-blue); }
.operation-artifact p { margin: var(--space-1) 0 0; color: var(--color-steel); }
.artifact-result { display: grid; justify-items: end; gap: var(--space-1); text-align: right; }
.status-pill { display: inline-flex; padding: var(--space-1) var(--space-2); border-radius: var(--radius-full); background: var(--color-fog-gray); font-size: 12px; font-weight: 800; }
.status-pill--running { background: var(--color-sky); color: #1e40af; }
.status-pill--verified, .status-pill--imported { background: var(--color-mint); color: #065f46; }
.status-pill--failed, .status-pill--conflict { background: var(--color-rose); color: #991b1b; }
.ready-card { justify-items: center; text-align: center; }
.ready-card .lead { max-width: 720px; }
.ready-icon { display: grid; place-items: center; width: 72px; height: 72px; border-radius: 50%; background: var(--color-mint); color: #047857; }
.ready-grid { width: 100%; text-align: left; }
.ready-summary { width: 100%; display: flex; justify-content: space-between; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); background: var(--color-mint); color: #065f46; text-align: left; }
.download-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: var(--space-3); }
.download-error { color: #991b1b; }
.next-steps { width: min(720px, 100%); text-align: left; }
.next-steps li { margin-bottom: var(--space-2); line-height: 1.5; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@media (max-width: 860px) {
  .browser-grid, .identity-grid, .operation-summary, .ready-grid { grid-template-columns: 1fr 1fr; }
  .page-heading, .section-heading, .artifact-panel__heading { align-items: stretch; flex-direction: column; }
  .search-row--wide { width: 100%; }
  .artifact-card, .operation-artifact { align-items: flex-start; flex-direction: column; }
  .reference-list { justify-content: flex-start; }
  .artifact-result { justify-items: start; text-align: left; }
}
@media (max-width: 620px) {
  .stepper { grid-template-columns: 1fr 1fr; }
  .browser-grid, .identity-grid, .operation-summary, .ready-grid { grid-template-columns: 1fr; }
  .wizard-card { padding: var(--space-4); }
  .actions, .ready-summary { align-items: stretch; flex-direction: column; }
  .primary-button, .secondary-button, .danger-button { width: 100%; }
}
</style>
