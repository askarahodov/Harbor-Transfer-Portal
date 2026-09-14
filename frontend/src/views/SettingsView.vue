<script setup lang="ts">
import axios from 'axios'
import { onMounted, ref } from 'vue'

import { apiClient } from '@/api/client'

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
  operation_max_concurrent: number
  operation_max_concurrent_active: number
  restart_required_fields: string[]
}

type TransferSettingsPatch = Partial<Pick<
  TransferSettings,
  | 'import_allow_overwrite'
  | 'import_max_upload_bytes'
  | 'bundle_max_archive_bytes'
  | 'bundle_max_extracted_bytes'
  | 'bundle_max_member_count'
  | 'operation_max_concurrent'
>>

type ConnectionTest = {
  ok: boolean
  code: string
  message: string
  version: string | null
}

const GIB = 1024 ** 3

const settings = ref<HarborSettings | null>(null)
const transferSettings = ref<TransferSettings | null>(null)
const url = ref('')
const username = ref('')
const verifyTls = ref(true)
const credential = ref('')
const overwriteAllowed = ref(false)
const uploadGiB = ref(50)
const archiveGiB = ref(50)
const extractedGiB = ref(100)
const memberCount = ref(100_000)
const operationConcurrency = ref(2)
const loading = ref(true)
const saving = ref(false)
const transferSaving = ref(false)
const rotating = ref(false)
const testing = ref(false)
const caBusy = ref(false)
const message = ref('')
const error = ref('')

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return fallback
}

function applySettings(value: HarborSettings): void {
  settings.value = value
  url.value = value.url ?? ''
  username.value = value.username ?? ''
  verifyTls.value = value.verify_tls
}

function bytesToGiB(value: number): number {
  return value / GIB
}

function gibToBytes(value: number): number {
  return Math.round(value * GIB)
}

function applyTransferSettings(value: TransferSettings): void {
  transferSettings.value = value
  overwriteAllowed.value = value.import_allow_overwrite
  uploadGiB.value = bytesToGiB(value.import_max_upload_bytes)
  archiveGiB.value = bytesToGiB(value.bundle_max_archive_bytes)
  extractedGiB.value = bytesToGiB(value.bundle_max_extracted_bytes)
  memberCount.value = value.bundle_max_member_count
  operationConcurrency.value = value.operation_max_concurrent
}

async function loadSettings(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [harborResponse, transferResponse] = await Promise.all([
      apiClient.get<HarborSettings>('/settings/harbor'),
      apiClient.get<TransferSettings>('/settings/transfer'),
    ])
    applySettings(harborResponse.data)
    applyTransferSettings(transferResponse.data)
  } catch (reason) {
    error.value = safeError('Не удалось загрузить настройки портала.', reason)
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
  const current = transferSettings.value
  if (!current) return

  const uploadBytes = gibToBytes(uploadGiB.value)
  const archiveBytes = gibToBytes(archiveGiB.value)
  const extractedBytes = gibToBytes(extractedGiB.value)
  if (
    !Number.isFinite(uploadBytes)
    || !Number.isFinite(archiveBytes)
    || !Number.isFinite(extractedBytes)
    || !Number.isInteger(memberCount.value)
    || !Number.isInteger(operationConcurrency.value)
  ) {
    error.value = 'Проверьте числовые значения transfer limits.'
    return
  }

  const patch: TransferSettingsPatch = {}
  if (overwriteAllowed.value !== current.import_allow_overwrite) {
    patch.import_allow_overwrite = overwriteAllowed.value
  }
  if (uploadBytes !== current.import_max_upload_bytes) patch.import_max_upload_bytes = uploadBytes
  if (archiveBytes !== current.bundle_max_archive_bytes) patch.bundle_max_archive_bytes = archiveBytes
  if (extractedBytes !== current.bundle_max_extracted_bytes) {
    patch.bundle_max_extracted_bytes = extractedBytes
  }
  if (memberCount.value !== current.bundle_max_member_count) {
    patch.bundle_max_member_count = memberCount.value
  }
  if (operationConcurrency.value !== current.operation_max_concurrent) {
    patch.operation_max_concurrent = operationConcurrency.value
  }

  if (Object.keys(patch).length === 0) {
    error.value = ''
    message.value = 'Transfer policies не изменились.'
    return
  }

  transferSaving.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.patch<TransferSettings>('/settings/transfer', patch)
    applyTransferSettings(response.data)
    message.value = response.data.restart_required_fields.length
      ? 'Transfer policies сохранены. Для отмеченных параметров требуется рестарт backend.'
      : 'Transfer policies применены.'
  } catch (reason) {
    error.value = safeError('Не удалось сохранить transfer policies.', reason)
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

async function testConnection(): Promise<void> {
  testing.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.post<ConnectionTest>('/settings/harbor/test')
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

onMounted(loadSettings)
</script>

<template>
  <section class="settings" aria-labelledby="settings-title">
    <header class="settings__header">
      <div>
        <h1 id="settings-title">Настройки портала</h1>
        <p>Локальный Harbor и безопасные runtime-политики этого изолированного контура.</p>
      </div>
      <strong class="contour" aria-label="Текущий контур">{{ settings?.contour ?? '—' }}</strong>
    </header>

    <p v-if="loading">Загрузка настроек…</p>
    <div v-else-if="settings" class="settings__grid">
      <form class="card" @submit.prevent="saveSettings">
        <h2>Подключение Harbor</h2>
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

      <form v-if="transferSettings" class="card card--wide" @submit.prevent="saveTransferSettings">
        <div class="card-heading">
          <div>
            <h2>Политики переноса</h2>
            <p class="status">Лимиты применяются server-side, а не только в браузере.</p>
          </div>
          <span class="status">Concurrency active: {{ transferSettings.operation_max_concurrent_active }}</span>
        </div>

        <label class="checkbox-row" for="transfer-overwrite">
          <input id="transfer-overwrite" v-model="overwriteAllowed" type="checkbox" />
          Разрешить явный overwrite конфликтующих TARGET artifacts
        </label>
        <p v-if="overwriteAllowed" class="warning" role="alert">
          Глобальная политика разрешает overwrite, но import всё равно требует явного подтверждения оператора.
        </p>

        <div class="policy-grid">
          <label for="transfer-upload-gib">
            Browser upload limit, GiB
            <input id="transfer-upload-gib" v-model.number="uploadGiB" type="number" min="0.001" max="1024" step="0.001" />
          </label>
          <label for="transfer-archive-gib">
            Bundle archive limit, GiB
            <input id="transfer-archive-gib" v-model.number="archiveGiB" type="number" min="0.001" max="1024" step="0.001" />
          </label>
          <label for="transfer-extracted-gib">
            Extracted limit, GiB
            <input id="transfer-extracted-gib" v-model.number="extractedGiB" type="number" min="0.001" max="2048" step="0.001" />
          </label>
          <label for="transfer-member-count">
            Archive member limit
            <input id="transfer-member-count" v-model.number="memberCount" type="number" min="4" max="1000000" step="1" />
          </label>
          <label for="transfer-concurrency">
            Operation concurrency
            <input id="transfer-concurrency" v-model.number="operationConcurrency" type="number" min="1" max="32" step="1" />
          </label>
        </div>

        <p
          v-if="transferSettings.restart_required_fields.includes('operation_max_concurrent')"
          class="warning"
          role="status"
        >
          Новое значение operation concurrency сохранено, но вступит в силу после рестарта backend.
        </p>
        <p class="hint">
          Retention auto-delete здесь не настраивается: lifecycle удаления пока не реализован и не имитируется декоративной настройкой.
        </p>
        <button type="submit" :disabled="transferSaving">
          {{ transferSaving ? 'Сохранение…' : 'Сохранить политики переноса' }}
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
.settings__header p { margin: 0; color: var(--color-steel); }
.contour { border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); }
.settings__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: var(--space-5); align-items: start; }
.card { display: grid; gap: var(--space-3); padding: var(--space-5); border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: white; }
.card--wide { grid-column: 1 / -1; }
.card h2 { margin: 0; }
.card-heading { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
.card input[type='text'], .card input[type='url'], .card input[type='password'], .card input[type='number'] { min-height: 42px; border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-3); font: inherit; }
.card button { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-bridge-blue); color: white; font: inherit; cursor: pointer; }
.card button:disabled { opacity: .6; cursor: wait; }
.card button.secondary { background: white; color: var(--color-deep-harbor); border: 1px solid var(--color-mist); }
.checkbox-row { display: flex; gap: var(--space-2); align-items: center; }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.policy-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: var(--space-3); }
.status, .hint { margin: 0; color: var(--color-steel); }
.hint { font-size: 13px; }
.warning { margin: 0; padding: var(--space-3); border: 1px solid #b45309; border-radius: var(--radius-md); }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid #15803d; }
.error { border: 1px solid #b91c1c; }
@media (max-width: 640px) {
  .settings__header, .card-heading { flex-direction: column; }
}
</style>
