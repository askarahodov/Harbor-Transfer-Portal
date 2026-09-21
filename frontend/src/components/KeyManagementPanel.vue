<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'

import { apiClient } from '@/api/client'
import { useRuntimeStore } from '@/stores/runtime'

type Contour = 'SOURCE' | 'TARGET'

type SigningKeyStatus = {
  configured: boolean
  fingerprint: string | null
}

type TrustedKeyStatus = {
  fingerprint: string
  enabled: boolean
}

type KeySettings = {
  contour: Contour
  signing_key: SigningKeyStatus | null
  trusted_keys: TrustedKeyStatus[]
}

const props = defineProps<{ contour: Contour }>()
const runtime = useRuntimeStore()
const effectiveContour = computed<Contour>(() => runtime.contour ?? props.contour)

const keySettings = ref<KeySettings | null>(null)
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const error = ref('')
let loadGeneration = 0

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const detailMessage = value.response?.data?.detail?.message
    if (typeof detailMessage === 'string') return detailMessage
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return fallback
}

async function loadKeys(): Promise<void> {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  try {
    const response = await apiClient.get<KeySettings>('/settings/keys')
    if (generation !== loadGeneration) return
    if (response.data.contour !== effectiveContour.value) {
      keySettings.value = null
      error.value = 'Runtime mode изменился. Key controls обновляются.'
      return
    }
    keySettings.value = response.data
  } catch (reason) {
    if (generation !== loadGeneration) return
    keySettings.value = null
    error.value = safeError('Не удалось загрузить key settings.', reason)
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function requireMode(expected: Contour): boolean {
  if (effectiveContour.value === expected && keySettings.value?.contour === expected) return true
  keySettings.value = null
  error.value = 'Runtime mode изменился. Повторите действие после обновления key settings.'
  void loadKeys()
  return false
}

async function generateSigningIdentity(): Promise<void> {
  if (!requireMode('SOURCE') || keySettings.value?.signing_key?.configured) return
  if (
    !window.confirm(
      'Создать SOURCE signing identity? Private key будет создан и сохранён только на этом сервере.',
    )
  ) {
    return
  }

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    await apiClient.post('/settings/keys/signing/generate')
    await loadKeys()
    message.value = 'SOURCE signing identity создана. Скачайте public key для TARGET trust set.'
  } catch (reason) {
    error.value = safeError('Не удалось создать SOURCE signing identity.', reason)
  } finally {
    busy.value = false
  }
}

async function downloadSigningPublicKey(): Promise<void> {
  if (!requireMode('SOURCE') || !keySettings.value?.signing_key?.configured) return

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const response = await apiClient.get<Blob>('/settings/keys/signing/public', {
      responseType: 'blob',
    })
    const href = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = 'source-signing-public.pem'
    anchor.click()
    URL.revokeObjectURL(href)
    message.value = 'Public key подготовлен для переноса в TARGET.'
  } catch (reason) {
    error.value = safeError('Не удалось скачать SOURCE public key.', reason)
  } finally {
    busy.value = false
  }
}

async function installSigningKey(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !requireMode('SOURCE')) return
  const rotating = keySettings.value?.signing_key?.configured === true
  const confirmed = window.confirm(
    rotating
      ? 'Ротировать SOURCE signing private key? Новые bundle будут подписываться новым ключом.'
      : 'Установить SOURCE signing private key? Private key нельзя будет скачать обратно через Portal.',
  )
  if (!confirmed) {
    input.value = ''
    return
  }

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const pem = await file.text()
    await apiClient.put('/settings/keys/signing', { pem })
    await loadKeys()
    message.value = rotating
      ? 'SOURCE signing key ротирован. Проверьте overlap trust на TARGET.'
      : 'SOURCE signing key установлен.'
  } catch (reason) {
    error.value = safeError('Не удалось установить SOURCE signing key.', reason)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function addTrustedKey(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !requireMode('TARGET')) return
  if (!window.confirm('Добавить этот Ed25519 public key в TARGET trust set?')) {
    input.value = ''
    return
  }

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const pem = await file.text()
    await apiClient.post('/settings/keys/trusted', { pem, confirm: true })
    await loadKeys()
    message.value = 'Trusted SOURCE public key добавлен.'
  } catch (reason) {
    error.value = safeError('Не удалось добавить trusted public key.', reason)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function replaceTrustedKey(key: TrustedKeyStatus, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !requireMode('TARGET')) return
  if (!window.confirm(`Заменить trusted key ${key.fingerprint} новым public key?`)) {
    input.value = ''
    return
  }

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    const pem = await file.text()
    await apiClient.put(`/settings/keys/trusted/${encodeURIComponent(key.fingerprint)}`, {
      pem,
      confirm: true,
    })
    await loadKeys()
    message.value = 'Trusted key атомарно заменён в существующем trust slot.'
  } catch (reason) {
    error.value = safeError('Не удалось заменить trusted public key.', reason)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function setTrustedState(key: TrustedKeyStatus, enabled: boolean): Promise<void> {
  if (!requireMode('TARGET')) return
  const verb = enabled ? 'включить' : 'отключить'
  if (!window.confirm(`${verb[0]?.toUpperCase()}${verb.slice(1)} trust для ${key.fingerprint}?`)) return

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    await apiClient.patch(`/settings/keys/trusted/${encodeURIComponent(key.fingerprint)}`, {
      enabled,
      confirm: true,
    })
    await loadKeys()
    message.value = enabled ? 'Trusted key включён.' : 'Trusted key отключён.'
  } catch (reason) {
    error.value = safeError('Не удалось изменить состояние trusted key.', reason)
  } finally {
    busy.value = false
  }
}

async function removeTrustedKey(key: TrustedKeyStatus): Promise<void> {
  if (!requireMode('TARGET')) return
  if (!window.confirm(`Удалить trusted key ${key.fingerprint}? Это действие нельзя отменить.`)) return

  busy.value = true
  message.value = ''
  error.value = ''
  try {
    await apiClient.delete(`/settings/keys/trusted/${encodeURIComponent(key.fingerprint)}`, {
      params: { confirm: true },
    })
    await loadKeys()
    message.value = 'Trusted key удалён.'
  } catch (reason) {
    error.value = safeError('Не удалось удалить trusted key.', reason)
  } finally {
    busy.value = false
  }
}

watch(effectiveContour, () => {
  keySettings.value = null
  message.value = ''
  error.value = ''
  void loadKeys()
})

onMounted(loadKeys)
</script>

<template>
  <section class="card card--wide" aria-labelledby="keys-title">
    <div>
      <h2 id="keys-title">Signing и trust keys</h2>
      <p class="status">Key material применяется тем же Bundle v1 build/verifier path.</p>
    </div>

    <p v-if="loading">Загрузка key settings…</p>

    <template
      v-else-if="keySettings && keySettings.contour === effectiveContour && effectiveContour === 'SOURCE'"
    >
      <p class="status">
        Signing identity:
        <strong>{{ keySettings.signing_key?.configured ? 'настроена' : 'не настроена' }}</strong>
      </p>
      <p v-if="keySettings.signing_key?.fingerprint" class="fingerprint">
        Fingerprint: <code>{{ keySettings.signing_key.fingerprint }}</code>
      </p>
      <div class="actions">
        <button
          v-if="!keySettings.signing_key?.configured"
          type="button"
          :disabled="busy"
          @click="generateSigningIdentity"
        >
          Создать signing identity
        </button>
        <button
          v-else
          type="button"
          class="secondary"
          :disabled="busy"
          @click="downloadSigningPublicKey"
        >
          Скачать public key
        </button>
      </div>
      <label for="source-signing-key">
        {{ keySettings.signing_key?.configured ? 'Ротация: Ed25519 private key, PEM' : 'Или установить существующий Ed25519 private key, PEM' }}
      </label>
      <input
        id="source-signing-key"
        type="file"
        accept=".pem,text/plain"
        :disabled="busy"
        @change="installSigningKey"
      />
      <p class="warning">
        Private key используется только server-side и никогда не возвращается через normal API/UI.
        Автогенерация создаёт identity от имени текущего admin в audit. При rotation сначала
        обеспечьте overlap trusted public keys на TARGET.
      </p>
    </template>

    <template
      v-else-if="keySettings && keySettings.contour === effectiveContour && effectiveContour === 'TARGET'"
    >
      <label for="target-trusted-key">Добавить Ed25519 public key, PEM</label>
      <input
        id="target-trusted-key"
        type="file"
        accept=".pem,text/plain"
        :disabled="busy"
        @change="addTrustedKey"
      />

      <p v-if="keySettings.trusted_keys.length === 0" class="status">
        Trusted SOURCE public keys пока не настроены.
      </p>
      <ul v-else class="key-list">
        <li v-for="key in keySettings.trusted_keys" :key="key.fingerprint" class="key-row">
          <div>
            <code>{{ key.fingerprint }}</code>
            <span class="status">{{ key.enabled ? 'active' : 'disabled' }}</span>
          </div>
          <div class="actions">
            <label class="replace-action">
              Заменить
              <input
                type="file"
                accept=".pem,text/plain"
                :disabled="busy"
                aria-label="Заменить trusted public key"
                @change="replaceTrustedKey(key, $event)"
              />
            </label>
            <button
              v-if="key.enabled"
              type="button"
              class="secondary"
              :disabled="busy"
              @click="setTrustedState(key, false)"
            >
              Отключить
            </button>
            <button
              v-else
              type="button"
              class="secondary"
              :disabled="busy"
              @click="setTrustedState(key, true)"
            >
              Включить
            </button>
            <button type="button" class="danger" :disabled="busy" @click="removeTrustedKey(key)">
              Удалить
            </button>
          </div>
        </li>
      </ul>
      <p class="status">
        Для плановой rotation сначала добавьте новый public key, выдержите overlap, затем отключите старый.
        «Заменить» делает немедленный atomic cutover одного trust slot без увеличения числа active keys.
      </p>
    </template>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.card { display: grid; gap: var(--space-3); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.card--wide { grid-column: 1 / -1; }
.card h2 { margin: 0; }
.card button, .replace-action { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-action-surface); color: var(--color-on-accent); font: inherit; cursor: pointer; }
.card button:disabled { opacity: .6; cursor: wait; }
.card button.secondary { background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border-control); }
.card button.danger { background: var(--color-surface); color: var(--color-danger-text); border: 1px solid var(--color-danger-text); }
.replace-action { display: inline-flex; align-items: center; }
.replace-action input { width: 1px; height: 1px; overflow: hidden; opacity: 0; position: absolute; }
.status { margin: 0; color: var(--color-text-muted); }
.fingerprint { margin: 0; overflow-wrap: anywhere; }
.warning { margin: 0; padding: var(--space-3); border: 1px solid var(--color-warning-text); border-radius: var(--radius-md); color: var(--color-warning-text); }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid var(--color-success-text); color: var(--color-success-text); }
.error { border: 1px solid var(--color-danger-text); color: var(--color-danger-text); }
.key-list { list-style: none; display: grid; gap: var(--space-3); padding: 0; margin: 0; }
.key-row { display: flex; justify-content: space-between; gap: var(--space-3); align-items: center; border-top: 1px solid var(--color-border); padding-top: var(--space-3); }
.key-row > div:first-child { display: grid; gap: var(--space-1); min-width: 0; overflow-wrap: anywhere; }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
@media (max-width: 720px) { .key-row { align-items: stretch; flex-direction: column; } }
</style>