<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'

import { apiClient } from '@/api/client'
import HarborProfilesPanel from '@/components/HarborProfilesPanel.vue'
import KeyManagementPanel from '@/components/KeyManagementPanel.vue'
import { useRuntimeStore } from '@/stores/runtime'

type HarborSettings = {
  contour: 'SOURCE' | 'TARGET'
  url: string | null
  username: string | null
  verify_tls: boolean
  credential_configured: boolean
  custom_ca_configured: boolean
}

type TransferSettings = {
  import_allow_overwrite: boolean
  import_max_upload_bytes: number
  bundle_max_archive_bytes: number
  bundle_max_extracted_bytes: number
  bundle_max_member_count: number
  operation_disk_reserve_bytes: number
  operation_max_concurrent: number
  effective_operation_max_concurrent: number
  restart_required_fields: string[]
  destination_mapping_revision: number
  destination_container_image_project: string | null
  destination_helm_chart_project: string | null
  destination_project_mappings: Record<string, string>
}

type ConnectionTest = {
  ok: boolean
  code: string
  message: string
  version: string | null
}

type KeyReadiness = {
  contour: 'SOURCE' | 'TARGET'
  signing_key: { configured: boolean; fingerprint: string | null } | null
  trusted_keys: Array<{ fingerprint: string; enabled: boolean }>
}

const MIB = 1024 ** 2
const runtime = useRuntimeStore()
const settings = ref<HarborSettings | null>(null)
const transferSettings = ref<TransferSettings | null>(null)
const url = ref('')
const username = ref('')
const verifyTls = ref(true)
const credential = ref('')
const allowOverwrite = ref(false)
const uploadMiB = ref(0)
const archiveMiB = ref(0)
const extractedMiB = ref(0)
const memberCount = ref(0)
const diskReserveMiB = ref(0)
const maxConcurrent = ref(0)
const destinationImageProject = ref('')
const destinationHelmProject = ref('')
const destinationMappingsText = ref('')
const loading = ref(true)
const saving = ref(false)
const transferSaving = ref(false)
const rotating = ref(false)
const testing = ref(false)
const caBusy = ref(false)
const message = ref('')
const error = ref('')
const readinessLoading = ref(false)
const readinessHarbor = ref<boolean | null>(null)
const readinessKeys = ref<KeyReadiness | null>(null)
let readinessGeneration = 0

const effectiveContour = computed(
  () => runtime.contour ?? settings.value?.contour ?? null,
)
const enabledTrustedKeys = computed(
  () => readinessKeys.value?.trusted_keys.filter((item) => item.enabled).length ?? 0,
)
const identityReady = computed(
  () => readinessKeys.value?.signing_key?.configured === true,
)
const firstRunReady = computed(() => {
  if (!settings.value || readinessHarbor.value !== true || !readinessKeys.value) return false
  return effectiveContour.value === 'SOURCE'
    ? identityReady.value
    : effectiveContour.value === 'TARGET' && enabledTrustedKeys.value > 0
})

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  if (value instanceof Error && value.message) return value.message
  return fallback
}

function formatProjectMappings(value: Record<string, string>): string {
  return Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([source, target]) => `${source}=${target}`)
    .join('\n')
}

function parseProjectMappings(value: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const [index, raw] of value.split(/\r?\n/).entries()) {
    const line = raw.trim()
    if (!line) continue
    const separator = line.indexOf('=')
    if (separator <= 0 || separator !== line.lastIndexOf('=') || separator === line.length - 1) {
      throw new Error(`Mapping строка ${index + 1}: используйте формат source-project=target-project.`)
    }
    const source = line.slice(0, separator).trim()
    const target = line.slice(separator + 1).trim()
    if (!source || !target) {
      throw new Error(`Mapping строка ${index + 1}: SOURCE и TARGET project обязательны.`)
    }
    if (Object.prototype.hasOwnProperty.call(result, source)) {
      throw new Error(`Mapping строка ${index + 1}: SOURCE project ${source} указан повторно.`)
    }
    result[source] = target
  }
  return result
}

function applySettings(value: HarborSettings): void {
  settings.value = value
  url.value = value.url ?? ''
  username.value = value.username ?? ''
  verifyTls.value = value.verify_tls
}

function applyTransferSettings(value: TransferSettings): void {
  transferSettings.value = value
  allowOverwrite.value = value.import_allow_overwrite
  uploadMiB.value = value.import_max_upload_bytes / MIB
  archiveMiB.value = value.bundle_max_archive_bytes / MIB
  extractedMiB.value = value.bundle_max_extracted_bytes / MIB
  memberCount.value = value.bundle_max_member_count
  diskReserveMiB.value = value.operation_disk_reserve_bytes / MIB
  maxConcurrent.value = value.operation_max_concurrent
  destinationImageProject.value = value.destination_container_image_project ?? ''
  destinationHelmProject.value = value.destination_helm_chart_project ?? ''
  destinationMappingsText.value = formatProjectMappings(value.destination_project_mappings)
}

async function loadSettings(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [harbor, transfer] = await Promise.all([
      apiClient.get<HarborSettings>('/settings/harbor'),
      apiClient.get<TransferSettings>('/settings/transfer'),
    ])
    applySettings(harbor.data)
    applyTransferSettings(transfer.data)
  } catch (reason) {
    error.value = safeError('Не удалось загрузить настройки.', reason)
  } finally {
    loading.value = false
  }
}

async function saveSettings(): Promise<void> {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.patch<HarborSettings>('/settings/harbor', {
      url: url.value.trim() || null,
      username: username.value.trim() || null,
      verify_tls: verifyTls.value,
    })
    applySettings(response.data)
    message.value = 'Настройки Harbor сохранены.'
  } catch (reason) {
    error.value = safeError('Не удалось сохранить настройки Harbor.', reason)
  } finally {
    saving.value = false
  }
}

async function saveTransferSettings(): Promise<void> {
  transferSaving.value = true
  error.value = ''
  message.value = ''
  try {
    const projectMappings = parseProjectMappings(destinationMappingsText.value)
    const response = await apiClient.patch<TransferSettings>('/settings/transfer', {
      import_allow_overwrite: allowOverwrite.value,
      import_max_upload_bytes: Math.round(uploadMiB.value * MIB),
      bundle_max_archive_bytes: Math.round(archiveMiB.value * MIB),
      bundle_max_extracted_bytes: Math.round(extractedMiB.value * MIB),
      bundle_max_member_count: memberCount.value,
      operation_disk_reserve_bytes: Math.round(diskReserveMiB.value * MIB),
      operation_max_concurrent: maxConcurrent.value,
      destination_container_image_project: destinationImageProject.value.trim() || null,
      destination_helm_chart_project: destinationHelmProject.value.trim() || null,
      destination_project_mappings: projectMappings,
    })
    applyTransferSettings(response.data)
    message.value = response.data.restart_required_fields.length
      ? 'Политики сохранены. Изменение параллелизма вступит в силу после перезапуска backend.'
      : `Политики переноса сохранены и применены. Mapping policy revision: ${response.data.destination_mapping_revision}.`
  } catch (reason) {
    error.value = safeError('Не удалось сохранить политики переноса.', reason)
  } finally {
    transferSaving.value = false
  }
}

async function rotateCredential(): Promise<void> {
  if (!credential.value) {
    error.value = 'Введите новый пароль или токен Harbor.'
    return
  }
  rotating.value = true
  error.value = ''
  message.value = ''
  try {
    await apiClient.put('/settings/harbor/credential', { secret: credential.value })
    credential.value = ''
    if (settings.value) settings.value.credential_configured = true
    message.value = 'Credential Harbor обновлён. Значение не сохраняется в форме.'
  } catch (reason) {
    error.value = safeError('Не удалось обновить credential Harbor.', reason)
  } finally {
    rotating.value = false
  }
}

async function uploadCa(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  caBusy.value = true
  error.value = ''
  message.value = ''
  try {
    const certificatePem = await file.text()
    await apiClient.put('/settings/harbor/ca', { certificate_pem: certificatePem })
    if (settings.value) settings.value.custom_ca_configured = true
    message.value = 'Пользовательский CA установлен.'
  } catch (reason) {
    error.value = safeError('Не удалось установить CA.', reason)
  } finally {
    caBusy.value = false
    input.value = ''
  }
}

async function removeCa(): Promise<void> {
  caBusy.value = true
  error.value = ''
  message.value = ''
  try {
    await apiClient.delete('/settings/harbor/ca')
    await loadSettings()
    message.value = 'Managed CA удалён; применяется deployment fallback, если он настроен.'
  } catch (reason) {
    error.value = safeError('Не удалось удалить managed CA.', reason)
  } finally {
    caBusy.value = false
  }
}

async function loadReadiness(): Promise<void> {
  const generation = ++readinessGeneration
  readinessLoading.value = true
  try {
    const [harborResult, keysResult] = await Promise.allSettled([
      apiClient.get<{ connected: boolean }>('/harbor/connection'),
      apiClient.get<KeyReadiness>('/settings/keys'),
    ])
    if (generation !== readinessGeneration) return
    readinessHarbor.value =
      harborResult.status === 'fulfilled' ? harborResult.value.data.connected : false
    readinessKeys.value =
      keysResult.status === 'fulfilled' ? keysResult.value.data : null
  } finally {
    if (generation === readinessGeneration) readinessLoading.value = false
  }
}

async function testConnection(): Promise<void> {
  testing.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.post<ConnectionTest>('/settings/harbor/test')
    readinessHarbor.value = response.data.ok
    if (response.data.ok) {
      message.value = `Подключение успешно${response.data.version ? ` · Harbor ${response.data.version}` : ''}.`
    } else {
      error.value = response.data.message
    }
  } catch (reason) {
    error.value = safeError('Не удалось выполнить проверку подключения.', reason)
  } finally {
    testing.value = false
  }
}

watch(
  () => runtime.contour,
  (next, previous) => {
    if (next && next !== previous) {
      readinessKeys.value = null
      readinessHarbor.value = null
      void loadSettings()
      void loadReadiness()
    }
  },
)

onMounted(() => {
  void loadSettings()
  void loadReadiness()
})
</script>

<template>
  <section class="settings" aria-labelledby="settings-title">
    <header class="settings__header">
      <div>
        <h1 id="settings-title">Настройки</h1>
        <p>Локальный Harbor и безопасные политики переноса этого изолированного контура.</p>
      </div>
      <strong class="contour" aria-label="Текущий контур">{{ effectiveContour ?? '—' }}</strong>
    </header>

    <p v-if="loading">Загрузка настроек…</p>
    <div v-else-if="settings && transferSettings" class="settings__grid">
      <section class="card card--wide readiness-card" aria-labelledby="first-run-readiness-title">
        <div class="readiness-heading">
          <div>
            <h2 id="first-run-readiness-title">First-run readiness</h2>
            <p class="status">Проверка минимальных условий для {{ effectiveContour ?? settings.contour }} workflow.</p>
          </div>
          <button type="button" class="secondary" :disabled="readinessLoading" @click="loadReadiness">
            {{ readinessLoading ? 'Проверка…' : 'Обновить readiness' }}
          </button>
        </div>
        <ul class="readiness-list">
          <li :class="{ ready: readinessHarbor === true }">
            Harbor: {{ readinessHarbor === true ? 'доступен' : readinessHarbor === false ? 'не готов' : 'проверяется' }}
          </li>
          <template v-if="effectiveContour === 'SOURCE'">
            <li :class="{ ready: identityReady }">
              Signing identity: {{ identityReady ? 'готова' : 'не настроена' }}
            </li>
            <li :class="{ ready: identityReady }">
              Trust package: {{ identityReady ? 'можно скачать' : 'создайте identity' }}
            </li>
          </template>
          <template v-else>
            <li :class="{ ready: enabledTrustedKeys > 0 }">
              SOURCE trust: {{ enabledTrustedKeys > 0 ? enabledTrustedKeys + ' active key(s)' : 'не настроен' }}
            </li>
            <li :class="{ ready: firstRunReady }">
              Import readiness: {{ firstRunReady ? 'готов' : 'требуется Harbor + SOURCE trust' }}
            </li>
          </template>
        </ul>
      </section>

      <HarborProfilesPanel class="card--wide" />

      <form class="card" @submit.prevent="saveSettings">
        <h2>Подключение</h2>
        <label for="harbor-url">URL локального Harbor</label>
        <input id="harbor-url" v-model="url" type="url" placeholder="https://harbor.local" autocomplete="url" />

        <label for="harbor-username">Service account / пользователь</label>
        <input id="harbor-username" v-model="username" type="text" autocomplete="username" />

        <label class="checkbox-row" for="harbor-verify-tls">
          <input id="harbor-verify-tls" v-model="verifyTls" type="checkbox" />
          Проверять TLS-сертификат Harbor
        </label>
        <p v-if="!verifyTls" class="warning" role="alert">
          Проверка TLS отключена явно. Используйте это только как временную диагностическую меру; предпочтителен пользовательский CA.
        </p>

        <div class="actions">
          <button type="submit" :disabled="saving">{{ saving ? 'Сохранение…' : 'Сохранить' }}</button>
          <button type="button" class="secondary" :disabled="testing" @click="testConnection">
            {{ testing ? 'Проверка…' : 'Проверить подключение' }}
          </button>
        </div>
      </form>

      <section class="card" aria-labelledby="credential-title">
        <h2 id="credential-title">Credential</h2>
        <p class="status">Текущее значение: {{ settings.credential_configured ? 'настроено' : 'не настроено' }}</p>
        <label for="harbor-credential">Новый пароль или токен</label>
        <input
          id="harbor-credential"
          v-model="credential"
          type="password"
          autocomplete="new-password"
          placeholder="Значение никогда не подставляется обратно"
        />
        <button type="button" :disabled="rotating" @click="rotateCredential">
          {{ rotating ? 'Обновление…' : 'Установить / ротировать credential' }}
        </button>
      </section>

      <section class="card" aria-labelledby="ca-title">
        <h2 id="ca-title">Пользовательский CA</h2>
        <p class="status">Статус: {{ settings.custom_ca_configured ? 'настроен' : 'не настроен' }}</p>
        <label for="harbor-ca">PEM/CRT CA bundle</label>
        <input id="harbor-ca" type="file" accept=".pem,.crt,.cer,text/plain" :disabled="caBusy" @change="uploadCa" />
        <button v-if="settings.custom_ca_configured" type="button" class="secondary" :disabled="caBusy" @click="removeCa">
          Удалить managed CA
        </button>
      </section>

      <KeyManagementPanel :contour="effectiveContour ?? settings.contour" @changed="loadReadiness" />

      <form class="card card--wide transfer-form" @submit.prevent="saveTransferSettings">
        <div>
          <h2>Политики переноса</h2>
          <p class="status">Изменения валидируются backend и записываются в audit.</p>
        </div>

        <label class="checkbox-row" for="transfer-overwrite">
          <input id="transfer-overwrite" v-model="allowOverwrite" type="checkbox" />
          Разрешить явный overwrite конфликтов на TARGET
        </label>
        <p v-if="allowOverwrite" class="warning" role="alert">
          Overwrite остаётся отдельным подтверждаемым действием оператора; эта настройка только разрешает его server-side.
        </p>

        <div class="policy-grid">
          <label for="transfer-upload-mib">
            Browser upload, MiB
            <input id="transfer-upload-mib" v-model.number="uploadMiB" type="number" min="1" step="1" />
          </label>
          <label for="transfer-archive-mib">
            Максимальный Bundle archive, MiB
            <input id="transfer-archive-mib" v-model.number="archiveMiB" type="number" min="1" step="1" />
          </label>
          <label for="transfer-extracted-mib">
            Максимум после распаковки, MiB
            <input id="transfer-extracted-mib" v-model.number="extractedMiB" type="number" min="1" step="1" />
          </label>
          <label for="transfer-members">
            Максимум archive members
            <input id="transfer-members" v-model.number="memberCount" type="number" min="4" max="1000000" step="1" />
          </label>
          <label for="transfer-disk-reserve-mib">
            Обязательный disk reserve, MiB
            <input id="transfer-disk-reserve-mib" v-model.number="diskReserveMiB" type="number" min="0" step="1" />
          </label>
          <label for="transfer-concurrency">
            Параллельные операции
            <input id="transfer-concurrency" v-model.number="maxConcurrent" type="number" min="1" max="32" step="1" />
          </label>
        </div>

        <section class="mapping-policy" aria-labelledby="mapping-policy-title">
          <div>
            <h3 id="mapping-policy-title">TARGET mapping defaults</h3>
            <p class="status">
              Revision {{ transferSettings.destination_mapping_revision }}. Новые destination plans фиксируют эту revision;
              уже подтверждённые plans не изменяются при последующей правке defaults.
            </p>
          </div>
          <div class="policy-grid">
            <label for="destination-image-project">
              Default project · Container Images
              <input
                id="destination-image-project"
                v-model="destinationImageProject"
                type="text"
                placeholder="например, docker-prod"
                autocomplete="off"
              />
            </label>
            <label for="destination-helm-project">
              Default project · Helm Charts
              <input
                id="destination-helm-project"
                v-model="destinationHelmProject"
                type="text"
                placeholder="например, helm-prod"
                autocomplete="off"
              />
            </label>
          </div>
          <label for="destination-project-mappings">
            SOURCE project → TARGET project
            <textarea
              id="destination-project-mappings"
              v-model="destinationMappingsText"
              rows="5"
              placeholder="source-team=target-team\nsource-charts=helm-prod"
              spellcheck="false"
            />
          </label>
          <p class="status">
            По одной паре <code>source-project=target-project</code> на строку. В конкретном Import явный SOURCE mapping
            или per-artifact override имеет приоритет над global defaults. Пустые defaults не создают скрытого mapping:
            unmapped artifact остаётся fail-closed.
          </p>
        </section>

        <p class="status">
          Активный параллелизм: {{ transferSettings.effective_operation_max_concurrent }}.
          Изменение этого поля применяется после restart backend; остальные показанные policy values — runtime-effective.
        </p>
        <p v-if="transferSettings.restart_required_fields.length" class="warning" role="alert">
          Требуется restart backend для: {{ transferSettings.restart_required_fields.join(', ') }}.
        </p>
        <button type="submit" :disabled="transferSaving">
          {{ transferSaving ? 'Сохранение…' : 'Сохранить политики' }}
        </button>
      </form>
    </div>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.settings { display: grid; gap: var(--space-6); }
.settings__header { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
.settings__header h1 { margin: 0 0 var(--space-2); }
.settings__header p { margin: 0; color: var(--color-text-muted); }
.contour { border: 1px solid var(--color-border); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); }
.settings__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: var(--space-5); align-items: start; }
.card { display: grid; gap: var(--space-3); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.card--wide { grid-column: 1 / -1; }
.card h2, .card h3 { margin: 0; }
.card input[type='text'], .card input[type='url'], .card input[type='password'], .card input[type='number'], .card textarea { min-height: 42px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); padding: 0 var(--space-3); font: inherit; }
.card textarea { width: 100%; min-height: 120px; padding-block: var(--space-3); resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.card button { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-action-surface); color: var(--color-on-accent); font: inherit; cursor: pointer; }
.card button:disabled { opacity: .6; cursor: wait; }
.card button.secondary { background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border-control); }
.checkbox-row { display: flex; gap: var(--space-2); align-items: center; }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.policy-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3); }
.mapping-policy { display: grid; gap: var(--space-3); margin-top: var(--space-3); padding-top: var(--space-4); border-top: 1px solid var(--color-border); }
.mapping-policy label { display: grid; gap: var(--space-2); }
.status { margin: 0; color: var(--color-text-muted); }
.readiness-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.readiness-list { display: grid; gap: var(--space-2); margin: 0; padding-left: var(--space-5); }
.readiness-list li { color: var(--color-warning-text); }
.readiness-list li.ready { color: var(--color-success-text); }
.warning { margin: 0; padding: var(--space-3); border: 1px solid var(--color-warning-text); border-radius: var(--radius-md); color: var(--color-warning-text); }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid var(--color-success-text); color: var(--color-success-text); }
.error { border: 1px solid var(--color-danger-text); color: var(--color-danger-text); }
@media (max-width: 640px) { .settings__header { flex-direction: column; } }
</style>