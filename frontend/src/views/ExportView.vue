<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileArchive,
  RefreshCw,
  XCircle,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { apiClient } from '@/api/client'
import {
  apiErrorInfo,
  createExportDownloadTicket,
  downloadExportHandoff,
  getExportHandoffRecord,
  inspectExactNexusImage,
  getNexusInventory,
  listNexusHelmCharts,
  listNexusRepositories,
  listNexusTags,
  type ExactNexusInspection,
  type NexusRepository,
  type NexusHelmChartVersion,
  type ArtifactStatus,
  type OperationStatus,
} from '@/api/exports'
import ExportArtifactSelector from '@/components/ExportArtifactSelector.vue'
import ExportReadyCard from '@/components/ExportReadyCard.vue'
import HarborProfileSelector from '@/components/HarborProfileSelector.vue'
import SearchCombobox from '@/components/SearchCombobox.vue'
import WizardStepper from '@/components/WizardStepper.vue'
import { formatBytes, shortDigest as formatShortDigest } from '@/presentation/format'
import { useAuthStore } from '@/stores/auth'
import { useExportWizardStore } from '@/stores/exportWizard'
import { useRuntimeStore } from '@/stores/runtime'

const wizard = useExportWizardStore()
const runtime = useRuntimeStore()
const auth = useAuthStore()
const exactRepository = ref('')
const exactReference = ref('')
const exactResult = ref<ExactNexusInspection | null>(null)
const exactError = ref<string | null>(null)
const exactBusy = ref(false)
const nexusRepositories = ref<string[]>([])
const nexusInventory = ref<NexusRepository[]>([])
const nexusInventoryAvailable = ref(false)
const nexusInventoryMessage = ref<string | null>(null)
const nexusInventoryBusy = ref(false)
const nexusHelmSelection = ref<string | null>(null)
const nexusHelmVersions = ref<NexusHelmChartVersion[]>([])
const nexusHelmMessage = ref<string | null>(null)
const nexusHelmBusy = ref(false)
let nexusHelmGeneration = 0

async function browseHelmRepository(repository: string): Promise<void> {
  const profileId = wizard.selectedHarborProfileId
  if (!wizard.isNexus || !profileId) return
  const generation = ++nexusHelmGeneration
  nexusHelmSelection.value = repository
  nexusHelmVersions.value = []
  nexusHelmMessage.value = null
  nexusHelmBusy.value = true
  try {
    const result = await listNexusHelmCharts(profileId, repository)
    if (generation !== nexusHelmGeneration || wizard.selectedHarborProfileId !== profileId) return
    nexusHelmVersions.value = result.items
    nexusHelmMessage.value = result.available
      ? (result.items.length ? null : 'В доступном Helm index.yaml нет подходящих charts.')
      : (result.message ?? 'Нет доступа к Helm index.yaml.')
  } catch (reason) {
    if (generation === nexusHelmGeneration) {
      nexusHelmMessage.value = apiErrorInfo(reason, 'Не удалось прочитать Helm index.yaml.').message
    }
  } finally {
    if (generation === nexusHelmGeneration) nexusHelmBusy.value = false
  }
}

let nexusInventoryGeneration = 0

async function loadNexusInventory(): Promise<void> {
  const profileId = wizard.selectedHarborProfileId
  if (!wizard.isNexus || !profileId) return
  const generation = ++nexusInventoryGeneration
  nexusInventoryBusy.value = true
  nexusInventoryAvailable.value = false
  nexusInventory.value = []
  nexusInventoryMessage.value = null
  try {
    const result = await getNexusInventory(profileId)
    if (generation !== nexusInventoryGeneration || wizard.selectedHarborProfileId !== profileId) return
    nexusInventory.value = result.items
    nexusInventoryAvailable.value = result.available
    nexusInventoryMessage.value = result.message ?? (result.available
      ? (result.items.length ? null : 'Доступных Docker/Helm репозиториев не обнаружено. Список может быть ограничен правами.')
      : 'Список Nexus недоступен. Уточните URL Nexus UI и права сервисной учётной записи.')
  } catch (reason) {
    if (generation === nexusInventoryGeneration && wizard.selectedHarborProfileId === profileId) {
      nexusInventoryMessage.value = apiErrorInfo(reason, 'Не удалось получить список репозиториев Nexus.').message
    }
  } finally {
    if (generation === nexusInventoryGeneration) nexusInventoryBusy.value = false
  }
}

const nexusTags = ref<string[]>([])
const nexusCatalogMessage = ref<string | null>(null)
const nexusTagsMessage = ref<string | null>(null)
const nexusBrowseBusy = ref(false)
const nexusTagsBusy = ref(false)
let nexusRepoTimer: ReturnType<typeof setTimeout> | null = null
let nexusTagTimer: ReturnType<typeof setTimeout> | null = null
let nexusGeneration = 0

async function searchNexusRepositories(): Promise<void> {
  const profileId = wizard.selectedHarborProfileId
  if (!wizard.isNexus || !profileId) return
  const generation = ++nexusGeneration
  nexusBrowseBusy.value = true
  try {
    const result = await listNexusRepositories(profileId, exactRepository.value.trim())
    if (generation !== nexusGeneration || wizard.selectedHarborProfileId !== profileId) return
    nexusRepositories.value = result.items
    nexusCatalogMessage.value = result.available
      ? (result.message ?? (result.truncated
        ? 'Результаты неполные; для полного поиска настройте Nexus REST Search API.'
        : null))
      : (result.message ?? 'Перечисление Nexus недоступно; введите точный repository.')
  } catch (reason) {
    if (generation === nexusGeneration) {
      nexusCatalogMessage.value = apiErrorInfo(reason, 'Не удалось получить каталог Nexus.').message
      nexusRepositories.value = []
    }
  } finally {
    if (generation === nexusGeneration) nexusBrowseBusy.value = false
  }
}

let nexusTagsGeneration = 0
async function searchNexusTags(): Promise<void> {
  const profileId = wizard.selectedHarborProfileId
  const repository = exactRepository.value.trim()
  if (!wizard.isNexus || !profileId || !repository) return
  const generation = ++nexusTagsGeneration
  nexusTagsBusy.value = true
  try {
    const result = await listNexusTags(profileId, repository, exactReference.value.trim())
    if (generation !== nexusTagsGeneration || wizard.selectedHarborProfileId !== profileId) return
    nexusTags.value = result.items
    nexusTagsMessage.value = result.available
      ? (result.message ?? (result.truncated
        ? 'Результаты тегов неполные; уточните название или настройте REST Search API.'
        : null))
      : (result.message ?? 'Теги недоступны; введите точный tag.')
  } catch (reason) {
    if (generation === nexusTagsGeneration) {
      nexusTagsMessage.value = apiErrorInfo(reason, 'Не удалось получить теги Nexus.').message
      nexusTags.value = []
    }
  } finally {
    if (generation === nexusTagsGeneration) nexusTagsBusy.value = false
  }
}


async function inspectNexusReference(): Promise<void> {
  const profileId = wizard.selectedHarborProfileId
  if (!wizard.isNexus || !profileId || exactBusy.value) return
  exactBusy.value = true
  exactResult.value = null
  exactError.value = null
  try {
    const inspected = await inspectExactNexusImage(
      profileId, exactRepository.value.trim(), exactReference.value.trim(),
    )
    if (wizard.selectedHarborProfileId !== profileId) return
    exactResult.value = inspected
  } catch (reason) {
    if (wizard.selectedHarborProfileId === profileId) {
      exactError.value = apiErrorInfo(reason, 'Не удалось проверить точный Docker reference.').message
    }
  } finally {
    exactBusy.value = false
  }
}

watch([exactRepository, exactReference], () => {
  exactResult.value = null
  exactError.value = null
})
watch(exactRepository, () => {
  nexusTags.value = []
  nexusTagsMessage.value = null
  if (nexusRepoTimer !== null) clearTimeout(nexusRepoTimer)
  if (nexusTagTimer !== null) clearTimeout(nexusTagTimer)
  nexusRepoTimer = setTimeout(() => { void searchNexusRepositories() }, 400)
})
watch(exactReference, () => {
  if (nexusTagTimer !== null) clearTimeout(nexusTagTimer)
  nexusTagTimer = setTimeout(() => { void searchNexusTags() }, 400)
})

watch(() => wizard.selectedHarborProfileId, () => {
  exactResult.value = null
  exactError.value = null
  nexusGeneration += 1
  nexusTagsGeneration += 1
  nexusInventoryGeneration += 1
  nexusHelmGeneration += 1
  nexusHelmSelection.value = null
  nexusHelmVersions.value = []
  nexusHelmMessage.value = null
  nexusInventory.value = []
  nexusInventoryAvailable.value = false
  nexusInventoryMessage.value = null
  nexusRepositories.value = []
  nexusTags.value = []
  nexusCatalogMessage.value = null
  nexusTagsMessage.value = null
  if (wizard.isNexus) {
    void loadNexusInventory()
    void searchNexusRepositories()
  }
})

const downloadError = ref<string | null>(null)
const printHandoffBusy = ref(false)
const signingRecoveryBusy = ref(false)
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

const longRunning = computed(() => {
  const operation = wizard.operation
  if (!operation?.started_at || !['CREATED', 'VALIDATING', 'RUNNING', 'PACKAGING', 'VERIFYING'].includes(operation.status)) {
    return false
  }
  return now.value - new Date(operation.started_at).getTime() > 5 * 60_000
})

const progressPercent = computed(() => {
  const progress = wizard.operation?.progress
  if (!progress || progress.progress_total <= 0) return null
  return Math.round((progress.progress_current / progress.progress_total) * 100)
})
const projectOptions = computed(() =>
  wizard.projects.map((project) => ({
    value: project.name,
    label: project.name,
    description: project.public ? 'public' : 'private',
  })),
)
const repositoryOptions = computed(() =>
  wizard.repositories.map((repository) => ({
    value: repository.name,
    label: repository.name,
    description: `${repository.artifact_count ?? '—'} artifacts`,
  })),
)


function shortDigest(digest: string | null): string {
  return formatShortDigest(digest, { maxLength: 24, headLength: 18, tailLength: 8 })
}

function kindLabel(kind: string): string {
  if (kind === 'container-image') return 'Container image'
  if (kind === 'helm-chart') return 'Helm chart'
  return 'OCI (не поддерживается)'
}


async function generateIdentityAndContinueExport(): Promise<void> {
  if (auth.user?.role !== 'admin' || signingRecoveryBusy.value) return
  if (
    !window.confirm(
      'Создать SOURCE signing identity и продолжить экспорт? Private key останется только на этом сервере.',
    )
  ) {
    return
  }

  signingRecoveryBusy.value = true
  try {
    await apiClient.post('/settings/keys/signing/generate')
    wizard.clearError()
    await wizard.start()
  } catch (reason) {
    const info = apiErrorInfo(reason, 'Не удалось создать SOURCE signing identity.')
    if (info.code === 'signing_key_already_configured') {
      wizard.clearError()
      await wizard.start()
    } else {
      wizard.error = info
    }
  } finally {
    signingRecoveryBusy.value = false
  }
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

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}

async function printHandoffRecord(): Promise<void> {
  if (!wizard.operation || printHandoffBusy.value) return
  const popup = window.open('', '_blank')
  if (!popup) {
    downloadError.value = 'Браузер заблокировал окно печати handoff.'
    return
  }
  popup.opener = null
  printHandoffBusy.value = true
  downloadError.value = null
  try {
    const record = await getExportHandoffRecord(wizard.operation.id)
    const rows = record.payload.files
      .map(
        (item) =>
          `<tr><td>${escapeHtml(item.role)}</td><td>${escapeHtml(item.name)}</td><td>${item.size_bytes}</td><td><code>${escapeHtml(item.sha256)}</code></td></tr>`,
      )
      .join('')
    popup.document.write(`<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Handoff ${escapeHtml(record.payload.delivery_id)}</title>
<style>
body{font-family:Arial,sans-serif;margin:32px;color:CanvasText}
h1{font-size:22px}
dl{display:grid;grid-template-columns:180px 1fr;gap:8px 16px}
dt{font-weight:700}
dd{margin:0}
table{width:100%;border-collapse:collapse;margin-top:24px}
th,td{border:1px solid ButtonBorder;padding:8px;text-align:left;vertical-align:top}
code{word-break:break-all;font-size:11px}
.signatures{margin-top:40px;display:grid;grid-template-columns:1fr 1fr;gap:48px}
.line{border-bottom:1px solid CanvasText;height:32px}
@media print{button{display:none}}
</style>
</head>
<body>
<h1>Harbor Transfer Portal — ведомость физической передачи</h1>
<dl>
<dt>Delivery ID</dt><dd>${escapeHtml(record.payload.delivery_id)}</dd>
<dt>SOURCE signer</dt><dd><code>${escapeHtml(record.payload.signing_key_fingerprint)}</code></dd>
<dt>Создано UTC</dt><dd>${escapeHtml(record.payload.created_at)}</dd>
<dt>Создал</dt><dd>${escapeHtml(record.payload.created_by)}</dd>
<dt>Schema</dt><dd>${escapeHtml(record.payload.schema_version)}</dd>
</dl>
<table>
<thead><tr><th>Role</th><th>Файл</th><th>Размер, bytes</th><th>SHA-256</th></tr></thead>
<tbody>${rows}</tbody>
</table>
<div class="signatures">
<div><div class="line"></div><p>SOURCE передал / дата</p></div>
<div><div class="line"></div><p>TARGET принял / дата</p></div>
</div>
<button onclick="window.print()">Печать</button>
</body>
</html>`)
    popup.document.close()
    popup.focus()
  } catch (error) {
    popup.close()
    downloadError.value = apiErrorInfo(
      error,
      'Не удалось подготовить печатную handoff-ведомость.',
    ).message
  } finally {
    printHandoffBusy.value = false
  }
}

async function downloadHandoff(): Promise<void> {
  if (!wizard.operation || !wizard.bundle) return
  downloadError.value = null
  try {
    const blob = await downloadExportHandoff(wizard.operation.id)
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = `${wizard.bundle.delivery_id}.htp-handoff.json`
    anchor.click()
    URL.revokeObjectURL(href)
  } catch (error) {
    downloadError.value = apiErrorInfo(
      error,
      'Не удалось скачать signed physical handoff.',
    ).message
  }
}

onMounted(async () => {
  if (!runtime.contour) {
    await runtime.loadRuntime()
  }
  if (runtime.contour === 'SOURCE') {
    await wizard.initialize()
    if (wizard.isNexus) {
      void loadNexusInventory()
      void searchNexusRepositories()
    }
  }
  clock = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  wizard.stopPolling()
  nexusGeneration += 1
  nexusTagsGeneration += 1
  if (nexusRepoTimer !== null) clearTimeout(nexusRepoTimer)
  if (nexusTagTimer !== null) clearTimeout(nexusTagTimer)
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
          Выберите точный Docker-образ Nexus или версию из Harbor, проверьте digest и создайте подписанный
          Offline Bundle для физического переноса в TARGET.
        </p>
      </div>
      <div v-if="wizard.connection && !wizard.isNexus" class="connection-chip" aria-label="Состояние локального Harbor">
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
      <WizardStepper :steps="steps" :current-step="wizard.step" aria-label="Этапы экспорта" />

      <div v-if="wizard.error" class="notice notice--danger" role="alert">
        <AlertTriangle :size="22" aria-hidden="true" />
        <div>
          <strong>{{ wizard.error.message }}</strong>
          <p class="error-code">Код: {{ wizard.error.code }}</p>
          <div v-if="wizard.error.code === 'bundle_signing_key_not_configured'" class="signing-recovery">
            <button
              v-if="auth.user?.role === 'admin'"
              class="secondary-button secondary-button--compact"
              type="button"
              :disabled="signingRecoveryBusy"
              @click="generateIdentityAndContinueExport"
            >
              Создать identity и продолжить экспорт
            </button>
            <p v-else>
              SOURCE signing identity может создать только администратор в настройках Portal.
            </p>
          </div>
        </div>
      </div>

      <HarborProfileSelector
        id="export-harbor-profile"
        label="SOURCE registry profile"
        :profiles="wizard.harborProfiles"
        :model-value="wizard.operation && !wizard.operation.harbor_profile_id ? null : wizard.selectedHarborProfileId"
        :disabled="wizard.harborProfilesLoading || wizard.busy !== null"
        :locked="wizard.harborProfileLocked"
        hint="Выбранный профиль закрепляется за операцией. Для Nexus используется Docker Registry v2 без Harbor API."
        @update:model-value="wizard.selectHarborProfile"
      />

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

        <div v-if="wizard.isNexus" class="notice notice--info" role="status">
          <AlertTriangle :size="22" aria-hidden="true" />
          <div>
            <strong>Nexus · Docker Registry v2</strong>
            <p>Поиск доступен, если ваш Nexus разрешает Docker v2 catalog/tags. Если каталог закрыт, укажите точный repository:tag. Harbor API и Helm OCI не используются.</p>
            <div class="nexus-inventory" aria-label="Репозитории Nexus по сервисной учётной записи">
              <div class="nexus-inventory__heading">
                <strong>Репозитории Nexus: Docker и Helm</strong>
                <button type="button" class="secondary-button" :disabled="nexusInventoryBusy" @click="loadNexusInventory">
                  {{ nexusInventoryBusy ? 'Загрузка…' : 'Обновить список' }}
                </button>
              </div>
              <p class="muted">Список получен с правами сервисного пользователя. Видимость не означает право чтения или записи. Репозитории другого формата не показываются.</p>
              <p v-if="nexusInventoryMessage" role="status" class="muted">{{ nexusInventoryMessage }}</p>
              <ul v-if="nexusInventoryAvailable && nexusInventory.length" class="nexus-inventory__list">
                <li v-for="item in nexusInventory" :key="item.format + ':' + item.name">
                  <strong>{{ item.name }}</strong>
                  <span>{{ item.format === 'docker' ? 'Docker' : 'Helm' }} · {{ item.kind }}</span>
                  <span class="muted">{{ item.format === 'helm'
                    ? 'Helm HTTP (.tgz) — перенос пока не реализован'
                    : 'Укажите Docker image path ниже и проверьте digest' }}</span>
                  <button
                    v-if="item.format === 'helm'"
                    type="button"
                    class="secondary-button"
                    :disabled="nexusHelmBusy"
                    @click="browseHelmRepository(item.name)"
                  >Показать charts</button>
                </li>
              </ul>
              <div v-if="nexusHelmSelection" class="nexus-helm-catalog" role="region" aria-label="Helm charts и версии">
                <strong>Helm: {{ nexusHelmSelection }} (только чтение)</strong>
                <p v-if="nexusHelmMessage" role="status">{{ nexusHelmMessage }}</p>
                <ul v-if="nexusHelmVersions.length">
                  <li v-for="entry in nexusHelmVersions" :key="entry.name + ':' + entry.version">
                    {{ entry.name }} · {{ entry.version }} · sha256:{{ entry.digest.slice(0, 12) }}
                    <button
                      type="button"
                      class="secondary-button"
                      @click="wizard.addExactNexusHelmChart(
                        nexusHelmSelection, entry.name, entry.version, entry.digest
                      )"
                    >Добавить Helm chart</button>
                  </li>
                </ul>
                <p class="muted">Helm HTTP: chart можно добавить в подписанный SOURCE bundle. TARGET требует отдельной проверки прав записи в hosted repository; overwrite запрещён.</p>
              </div>
            </div>
            <form class="nexus-exact-form" @submit.prevent="inspectNexusReference">
              <label>Repository
                <input v-model="exactRepository" type="text" required maxlength="255" placeholder="team/application" autocomplete="off" list="nexus-repositories" />
                <datalist id="nexus-repositories">
                  <option v-for="name in nexusRepositories" :key="name" :value="name" />
                </datalist>
              </label>
              <button class="secondary-button" type="button" :disabled="nexusBrowseBusy" @click="searchNexusRepositories">
                {{ nexusBrowseBusy ? 'Поиск репозиториев…' : 'Найти репозитории' }}
              </button>
              <p v-if="nexusCatalogMessage" class="muted" role="status">{{ nexusCatalogMessage }}</p>
              <div v-if="nexusRepositories.length" class="nexus-suggestions" aria-label="Найденные Nexus репозитории">
                <button v-for="name in nexusRepositories" :key="name" type="button" class="secondary-button" @click="exactRepository = name; exactReference = ''; searchNexusTags()">{{ name }}</button>
              </div>
              <label>Tag или digest
                <input v-model="exactReference" type="text" required maxlength="128" placeholder="1.0.0" autocomplete="off" list="nexus-tags" />
                <datalist id="nexus-tags">
                  <option v-for="tag in nexusTags" :key="tag" :value="tag" />
                </datalist>
              </label>
              <button type="button" class="secondary-button" :disabled="!exactRepository.trim() || nexusTagsBusy" @click="searchNexusTags">
                {{ nexusTagsBusy ? 'Поиск тегов…' : 'Найти теги' }}
              </button>
              <p v-if="nexusTagsMessage" class="muted" role="status">{{ nexusTagsMessage }}</p>
              <div v-if="nexusTags.length" class="nexus-suggestions" aria-label="Найденные Nexus теги">
                <button v-for="tag in nexusTags" :key="tag" type="button" class="secondary-button" @click="exactReference = tag">{{ tag }}</button>
              </div>
              <button class="secondary-button" type="submit" :disabled="exactBusy || !exactRepository.trim() || !exactReference.trim()">Проверить Docker image</button>
            </form>
            <p v-if="exactError" role="alert">{{ exactError }}</p>
            <div v-if="exactResult" role="status">
              <p>Найден {{ exactResult.repository }}:{{ exactResult.reference }} · digest <code>{{ exactResult.digest }}</code></p>
              <button
                type="button"
                class="secondary-button"
                @click="wizard.addExactNexusImage(exactResult.repository, exactResult.reference, exactResult.digest)"
              >Добавить образ в выбор</button>
            </div>
          </div>
        </div>

        <div v-else class="compact-selector" aria-label="Выбор артефакта Harbor">
          <SearchCombobox
            label="Проект Harbor"
            :model-value="wizard.selectedProject ?? ''"
            :search="wizard.projectSearch"
            :options="projectOptions"
            placeholder="Найти проект"
            :loading="wizard.busy === 'projects'"
            :page="wizard.projectPage"
            :total="wizard.projectTotal"
            @update:search="wizard.projectSearch = $event"
            @select="wizard.chooseProject"
            @previous="wizard.loadProjects(wizard.projectPage - 1)"
            @next="wizard.loadProjects(wizard.projectPage + 1)"
          />

          <SearchCombobox
            label="Репозиторий Harbor"
            :model-value="wizard.selectedRepository ?? ''"
            :search="wizard.repositorySearch"
            :options="repositoryOptions"
            placeholder="Найти репозиторий"
            :disabled="!wizard.selectedProject"
            :loading="wizard.busy === 'repositories'"
            :page="wizard.repositoryPage"
            :total="wizard.repositoryTotal"
            @update:search="wizard.repositorySearch = $event"
            @select="wizard.chooseRepository"
            @previous="wizard.loadRepositories(wizard.repositoryPage - 1)"
            @next="wizard.loadRepositories(wizard.repositoryPage + 1)"
          />

          <div class="compact-selector__versions">
            <ExportArtifactSelector
            :artifacts="wizard.artifacts"
            :selected-repository="wizard.selectedRepository"
            :search="wizard.artifactSearch"
            :busy="wizard.busy === 'artifacts'"
            :page="wizard.artifactPage"
            :total="wizard.artifactTotal"
            :references-for="wizard.referencesFor"
            @update:search="wizard.artifactSearch = $event"
            @add="wizard.addArtifact"
            @page="wizard.loadArtifacts"
            />
          </div>
        </div>

        <div class="selection-context" aria-live="polite">
          <span v-if="wizard.selectedProject"><strong>{{ wizard.selectedProject }}</strong></span>
          <span v-if="wizard.selectedRepository">/ {{ wizard.selectedRepository }}</span>
          <span v-if="wizard.selectedRepository" class="muted">· выберите точную версию ниже</span>
          <span v-else class="muted">Сначала выберите проект и репозиторий.</span>
        </div>

        <div v-if="wizard.selectedArtifacts.length" class="selected-artifacts" aria-label="Выбранные артефакты">
          <div
            v-for="artifact in wizard.selectedArtifacts"
            :key="[artifact.kind, artifact.project, artifact.repository, artifact.digest].join('|')"
            class="selected-artifact"
          >
            <div>
              <strong>{{ artifact.project }}/{{ artifact.repository }}:{{ artifact.reference }}</strong>
              <span>{{ kindLabel(artifact.kind) }} · {{ formatBytes(artifact.size_bytes) }}</span>
              <code :title="artifact.digest">{{ shortDigest(artifact.digest) }}</code>
            </div>
            <button
              type="button"
              class="text-button"
              :aria-label="`Удалить ${artifact.reference}`"
              @click="wizard.toggleArtifact(artifact, artifact.reference)"
            >
              Удалить
            </button>
          </div>
        </div>

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
            <span>{{ wizard.isNexus ? 'Nexus · Docker v2' : 'Локальный Harbor' }}</span>
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

        <div
          v-if="wizard.operation && progressPercent !== null"
          class="progress-block"
          role="status"
          aria-live="polite"
          aria-label="Прогресс экспорта"
        >
          <div class="progress-label"><span>Грубый прогресс по обработанным artifacts</span><strong>{{ progressPercent }}%</strong></div>
          <progress :value="wizard.operation.progress.progress_current" :max="wizard.operation.progress.progress_total">
            {{ progressPercent }}%
          </progress>
        </div>

        <div v-if="longRunning && wizard.operation" class="notice notice--info" role="status">
          <AlertTriangle :size="22" aria-hidden="true" />
          <div>
            <strong>Экспорт выполняется дольше обычного.</strong>
            <p>0% здесь означает 0 завершённых артефактов, а не отсутствие переданных байтов. Проверьте Skopeo/Nexus и свободное место на SOURCE. Если операция не меняет статус, сохраните ID {{ wizard.operation.id }} и серверные логи перед отменой; повторный экспорт не запускайте параллельно.</p>
          </div>
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

      <ExportReadyCard
        v-else
        :bundle="wizard.bundle"
        :operation="wizard.operation"
        :download-error="downloadError"
        :print-handoff-busy="printHandoffBusy"
        @download-bundle="downloadBundle"
        @download-sidecar="downloadSidecar"
        @download-handoff="downloadHandoff"
        @print-handoff="printHandoffRecord"
        @reset="wizard.reset"
      />
    </template>
  </section>
</template>

<style scoped>
.nexus-inventory { margin-top: var(--space-3); display: grid; gap: var(--space-2); }
.nexus-inventory__heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); flex-wrap: wrap; }
.nexus-inventory__list { list-style: none; padding: 0; margin: 0; display: grid; gap: var(--space-2); }
.nexus-inventory__list li { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
.nexus-exact-form { display: grid; gap: var(--space-3); margin-top: var(--space-3); }
.nexus-suggestions { display: flex; flex-wrap: wrap; gap: var(--space-2); max-height: 180px; overflow-y: auto; }
.nexus-suggestions button { max-width: 100%; overflow-wrap: anywhere; }
.nexus-exact-form label { display: grid; gap: var(--space-1); }
.nexus-exact-form input { min-height: 42px; width: 100%; padding: var(--space-2); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); }

.export-page { display: grid; gap: var(--space-6); color: var(--color-text); }
.page-heading, .section-heading, .artifact-panel__heading, .actions, .progress-label { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: var(--space-2); font-size: clamp(28px, 4vw, 40px); }
h2 { margin-bottom: 0; font-size: 24px; }
h3 { margin-bottom: var(--space-2); font-size: 16px; }
.eyebrow { margin-bottom: var(--space-2); color: var(--color-action); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
.lead { max-width: 760px; margin-bottom: 0; color: var(--color-text-muted); line-height: 1.6; }
.muted, .item-meta, small { color: var(--color-text-muted); }
.connection-chip { display: inline-flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-full); background: var(--color-success-surface); color: var(--color-success-text); white-space: nowrap; }
.wizard-card { display: grid; gap: var(--space-6); padding: var(--space-6); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.selection-summary { padding: var(--space-2) var(--space-3); border-radius: var(--radius-md); background: var(--color-info-surface); color: var(--color-info-text); }
.selected-artifacts { display: grid; gap: var(--space-2); }
.selected-artifact { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.selected-artifact > div { display: flex; min-width: 0; flex-wrap: wrap; align-items: baseline; gap: var(--space-2); }
.selected-artifact span { color: var(--color-text-muted); font-size: 12px; }
.selected-artifact code { overflow-wrap: anywhere; font-size: 12px; }
.browser-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
.compact-selector { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr); gap: var(--space-3); align-items: end; padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.compact-selector__versions { grid-column: 1 / -1; margin-top: var(--space-2); }
.compact-field { display: grid; gap: var(--space-2); min-width: 0; font-weight: 700; }
.compact-pagination { display: flex; align-items: center; justify-content: center; gap: var(--space-1); font-size: 12px; color: var(--color-text-muted); }
.compact-pagination button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; }
.select-shell { position: relative; display: block; }
.select-shell select { width: 100%; min-height: 42px; padding: 0 38px 0 var(--space-3); appearance: none; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; }
.select-shell > svg { position: absolute; right: var(--space-3); top: 50%; transform: translateY(-50%); pointer-events: none; color: var(--color-text-muted); }
.compact-search { display: grid; grid-template-columns: 20px minmax(0, 1fr); align-items: center; gap: var(--space-2); min-height: 42px; padding-left: var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); }
.compact-search input[type='search'] { min-height: 40px; padding-left: 0; border: 0; outline: 0; }
.selection-context { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: calc(var(--space-3) * -1); font-size: 13px; }
.artifact-panel--compact { padding: var(--space-3) var(--space-4); }
.artifact-list--compact { gap: var(--space-2); }
.artifact-card--compact { padding: var(--space-3); }
.browser-panel, .artifact-panel { display: grid; align-content: start; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.artifact-panel { gap: var(--space-4); }
.search-row { display: grid; grid-template-columns: minmax(0, 1fr) 42px; gap: var(--space-2); }
.search-row--wide { width: min(360px, 100%); }
input[type='search'], textarea { width: 100%; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; }
input[type='search'] { min-height: 42px; padding: 0 var(--space-3); }
textarea { padding: var(--space-3); resize: vertical; }
.icon-button { min-width: 42px; min-height: 42px; display: grid; place-items: center; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .55; }
.browser-item { min-height: 44px; display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid transparent; border-radius: var(--radius-md); background: transparent; color: inherit; text-align: left; cursor: pointer; }
.browser-item:hover, .browser-item:focus-visible { background: var(--color-surface); }
.browser-item--selected { border-color: var(--color-action); background: var(--color-info-surface); }
.artifact-list, .operation-artifacts { display: grid; gap: var(--space-3); }
.artifact-card, .operation-artifact { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.artifact-card__main { display: flex; align-items: flex-start; gap: var(--space-3); min-width: 0; }
.kind-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: var(--radius-md); background: var(--color-info-surface); color: var(--color-action); }
.digest { margin: var(--space-1) 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
.reference-list { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-2); }
.reference-choice { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 38px; padding: var(--space-1) var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-full); background: var(--color-surface-subtle); cursor: pointer; }
.reference-choice:has(input:checked) { border-color: var(--color-action); background: var(--color-info-surface); }
.unsupported { max-width: 240px; color: var(--color-text-muted); font-size: 13px; text-align: right; }
.pagination { display: flex; align-items: center; justify-content: center; gap: var(--space-3); padding-top: var(--space-2); }
.pagination button, .text-button { border: 0; background: transparent; color: var(--color-action); cursor: pointer; text-decoration: underline; }
.identity-grid, .operation-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-3); }
.operation-summary { grid-template-columns: repeat(4, 1fr); }
.info-box { display: grid; gap: var(--space-1); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); min-width: 0; }
.info-box > span { color: var(--color-text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.info-box strong, .info-box code { overflow-wrap: anywhere; }
.info-box--wide { grid-column: 1 / -1; }
.field { display: grid; gap: var(--space-2); }
.field > span { font-weight: 700; }
.preview-table-wrap { overflow-x: auto; }
.preview-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.preview-table th, .preview-table td { padding: var(--space-3); border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: top; }
.preview-table th { color: var(--color-text-muted); font-size: 12px; text-transform: uppercase; }
.notice { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); }
.notice p { margin: var(--space-1) 0 0; line-height: 1.5; }
.notice--info { background: var(--color-info-surface); color: var(--color-info-text); }
.notice--warning { background: var(--color-warning-surface); color: var(--color-warning-text); }
.notice--danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
.error-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.signing-recovery { display: grid; justify-items: start; gap: var(--space-2); margin-top: var(--space-3); }
.signing-recovery p { margin: 0; }
.actions { align-items: center; }
.actions--end { justify-content: flex-end; }
.primary-button, .secondary-button, .danger-button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); border-radius: var(--radius-md); padding: 0 var(--space-4); font: inherit; font-weight: 700; cursor: pointer; }
.primary-button { border: 1px solid var(--color-action); background: var(--color-action-surface); color: var(--color-on-accent); }
.secondary-button { border: 1px solid var(--color-border-control); background: var(--color-surface); color: var(--color-text); }
.secondary-button--compact { min-height: 38px; padding-inline: var(--space-3); }
.danger-button { border: 1px solid var(--color-danger-text); background: var(--color-danger-text); color: var(--color-on-accent); }
.progress-block { display: grid; gap: var(--space-2); }
progress { width: 100%; height: 12px; accent-color: var(--color-action); }
.operation-artifact p { margin: var(--space-1) 0 0; color: var(--color-text-muted); }
.artifact-result { display: grid; justify-items: end; gap: var(--space-1); text-align: right; }
.status-pill { display: inline-flex; padding: var(--space-1) var(--space-2); border-radius: var(--radius-full); background: var(--color-surface-subtle); font-size: 12px; font-weight: 800; }
.status-pill--running { background: var(--color-info-surface); color: var(--color-info-text); }
.status-pill--verified, .status-pill--imported { background: var(--color-success-surface); color: var(--color-success-text); }
.status-pill--failed, .status-pill--conflict { background: var(--color-danger-surface); color: var(--color-danger-text); }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@media (max-width: 860px) {
  .browser-grid, .identity-grid, .operation-summary { grid-template-columns: 1fr 1fr; }
  .compact-selector { grid-template-columns: 1fr 1fr; }
  .compact-field--search { grid-column: 1 / -1; }
  .page-heading, .section-heading, .artifact-panel__heading { align-items: stretch; flex-direction: column; }
  .search-row--wide { width: 100%; }
  .artifact-card, .operation-artifact { align-items: flex-start; flex-direction: column; }
  .reference-list { justify-content: flex-start; }
  .artifact-result { justify-items: start; text-align: left; }
}
@media (max-width: 620px) {
  .browser-grid, .identity-grid, .operation-summary, .compact-selector { grid-template-columns: 1fr; }
  .compact-field--search { grid-column: auto; }
  .wizard-card { padding: var(--space-4); }
  .actions { align-items: stretch; flex-direction: column; }
  .primary-button, .secondary-button, .danger-button { width: 100%; }
}
</style>