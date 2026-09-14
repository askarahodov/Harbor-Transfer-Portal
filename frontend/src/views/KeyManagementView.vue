<script setup lang="ts">
import axios from 'axios'
import { onMounted, ref } from 'vue'

import { apiClient } from '@/api/client'

type SigningIdentity = {
  configured: boolean
  key_id: string | null
  fingerprint: string | null
}

type TrustedKey = {
  key_id: string
  fingerprint: string
  enabled: boolean
}

type KeyManagement = {
  contour: 'SOURCE' | 'TARGET'
  signing: SigningIdentity | null
  trusted_keys: TrustedKey[]
}

const state = ref<KeyManagement | null>(null)
const loading = ref(true)
const busy = ref(false)
const busyKeyId = ref<string | null>(null)
const signingPrivatePem = ref('')
const trustedPublicPem = ref('')
const replacements = ref<Record<string, string>>({})
const message = ref('')
const error = ref('')

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return fallback
}

async function loadKeys(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await apiClient.get<KeyManagement>('/settings/keys')
    state.value = response.data
  } catch (reason) {
    error.value = safeError('Не удалось загрузить состояние ключей.', reason)
  } finally {
    loading.value = false
  }
}

async function readFile(event: Event): Promise<string> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return ''
  try {
    return await file.text()
  } finally {
    input.value = ''
  }
}

async function selectSigningKey(event: Event): Promise<void> {
  signingPrivatePem.value = await readFile(event)
}

async function installSigningKey(): Promise<void> {
  if (!signingPrivatePem.value) {
    error.value = 'Выберите PEM-файл Ed25519 private key.'
    return
  }
  const rotating = state.value?.signing?.configured === true
  if (rotating && !window.confirm('Ротировать действующий SOURCE signing key?')) return

  busy.value = true
  error.value = ''
  message.value = ''
  const privateKeyPem = signingPrivatePem.value
  try {
    const response = await apiClient.put<KeyManagement>('/settings/keys/signing', {
      private_key_pem: privateKeyPem,
      confirm_rotation: rotating,
    })
    state.value = response.data
    message.value = rotating
      ? 'SOURCE signing key ротирован.'
      : 'SOURCE signing key установлен.'
  } catch (reason) {
    error.value = safeError('Не удалось установить SOURCE signing key.', reason)
  } finally {
    signingPrivatePem.value = ''
    busy.value = false
  }
}

async function selectTrustedKey(event: Event): Promise<void> {
  trustedPublicPem.value = await readFile(event)
}

async function addTrustedKey(): Promise<void> {
  if (!trustedPublicPem.value) {
    error.value = 'Выберите PEM-файл Ed25519 public key.'
    return
  }
  if (!window.confirm('Добавить этот SOURCE public key в доверенный набор TARGET?')) return

  busy.value = true
  error.value = ''
  message.value = ''
  const publicKeyPem = trustedPublicPem.value
  try {
    const response = await apiClient.post<KeyManagement>('/settings/keys/trusted', {
      public_key_pem: publicKeyPem,
      confirm: true,
    })
    state.value = response.data
    message.value = 'Trusted SOURCE public key добавлен.'
  } catch (reason) {
    error.value = safeError('Не удалось добавить trusted public key.', reason)
  } finally {
    trustedPublicPem.value = ''
    busy.value = false
  }
}

async function selectReplacement(keyId: string, event: Event): Promise<void> {
  replacements.value[keyId] = await readFile(event)
}

async function replaceTrustedKey(key: TrustedKey): Promise<void> {
  const publicKeyPem = replacements.value[key.key_id]
  if (!publicKeyPem) {
    error.value = 'Выберите новый PEM Ed25519 public key для замены.'
    return
  }
  if (!window.confirm(`Заменить trusted key ${key.fingerprint}?`)) return

  busyKeyId.value = key.key_id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.put<KeyManagement>(
      `/settings/keys/trusted/${key.key_id}`,
      { public_key_pem: publicKeyPem, confirm: true },
    )
    state.value = response.data
    delete replacements.value[key.key_id]
    message.value = 'Trusted SOURCE public key заменён.'
  } catch (reason) {
    error.value = safeError('Не удалось заменить trusted public key.', reason)
  } finally {
    busyKeyId.value = null
  }
}

async function setTrustedKeyEnabled(key: TrustedKey, enabled: boolean): Promise<void> {
  const action = enabled ? 'включить' : 'отключить'
  if (!window.confirm(`${action} trusted key ${key.fingerprint}?`)) return

  busyKeyId.value = key.key_id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.patch<KeyManagement>(
      `/settings/keys/trusted/${key.key_id}`,
      { enabled, confirm: true },
    )
    state.value = response.data
    message.value = enabled ? 'Trusted key включён.' : 'Trusted key отключён.'
  } catch (reason) {
    error.value = safeError('Не удалось изменить состояние trusted key.', reason)
  } finally {
    busyKeyId.value = null
  }
}

async function removeTrustedKey(key: TrustedKey): Promise<void> {
  if (!window.confirm(`Удалить trusted key ${key.fingerprint}? Это действие необратимо.`)) return

  busyKeyId.value = key.key_id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.delete<KeyManagement>(
      `/settings/keys/trusted/${key.key_id}?confirm=true`,
    )
    state.value = response.data
    message.value = 'Trusted key удалён.'
  } catch (reason) {
    error.value = safeError('Не удалось удалить trusted key.', reason)
  } finally {
    busyKeyId.value = null
  }
}

onMounted(loadKeys)
</script>

<template>
  <section class="keys" aria-labelledby="keys-title">
    <header class="keys__header">
      <div>
        <h1 id="keys-title">Ключи переноса</h1>
        <p>Signing identity SOURCE и доверенные публичные ключи TARGET.</p>
      </div>
      <strong class="contour" aria-label="Текущий контур">{{ state?.contour ?? '—' }}</strong>
    </header>

    <p v-if="loading">Загрузка состояния ключей…</p>

    <section v-else-if="state?.contour === 'SOURCE' && state.signing" class="card">
      <h2>SOURCE signing identity</h2>
      <p class="status">
        Статус: {{ state.signing.configured ? 'настроен' : 'не настроен' }}
      </p>
      <dl v-if="state.signing.configured" class="identity">
        <div>
          <dt>Fingerprint</dt>
          <dd data-testid="signing-fingerprint">{{ state.signing.fingerprint }}</dd>
        </div>
        <div>
          <dt>Key ID</dt>
          <dd>{{ state.signing.key_id }}</dd>
        </div>
      </dl>
      <p class="warning" role="alert">
        Private key никогда не возвращается через API. Выбранный файл передаётся только для
        установки или ротации и очищается из формы после запроса.
      </p>
      <label for="source-signing-key">Ed25519 private key, PEM PKCS#8</label>
      <input
        id="source-signing-key"
        type="file"
        accept=".pem,text/plain"
        :disabled="busy"
        @change="selectSigningKey"
      />
      <button type="button" :disabled="busy || !signingPrivatePem" @click="installSigningKey">
        {{
          busy
            ? 'Сохранение…'
            : state.signing.configured
              ? 'Ротировать signing key'
              : 'Установить signing key'
        }}
      </button>
    </section>

    <template v-else-if="state?.contour === 'TARGET'">
      <section class="card">
        <h2>Добавить доверенный SOURCE key</h2>
        <p class="status">
          TARGET принимает только Ed25519 public key. Private key здесь отклоняется backend.
        </p>
        <label for="target-trusted-key">Ed25519 public key, PEM</label>
        <input
          id="target-trusted-key"
          type="file"
          accept=".pem,text/plain"
          :disabled="busy"
          @change="selectTrustedKey"
        />
        <button type="button" :disabled="busy || !trustedPublicPem" @click="addTrustedKey">
          {{ busy ? 'Добавление…' : 'Добавить trusted key' }}
        </button>
      </section>

      <section class="card card--wide" aria-labelledby="trusted-list-title">
        <div>
          <h2 id="trusted-list-title">Trusted SOURCE keys</h2>
          <p class="status">
            Enabled keys используются verifier. Для overlap rotation сначала добавьте новый key,
            затем отключите или удалите старый.
          </p>
        </div>
        <p v-if="!state.trusted_keys.length" class="empty">Доверенные ключи не настроены.</p>
        <ul v-else class="key-list">
          <li v-for="key in state.trusted_keys" :key="key.key_id" class="key-row">
            <div class="key-row__identity">
              <strong>{{ key.enabled ? 'Enabled' : 'Disabled' }}</strong>
              <code>{{ key.fingerprint }}</code>
              <span class="status">Key ID: {{ key.key_id }}</span>
            </div>
            <div class="key-row__replace">
              <label :for="`replace-${key.key_id}`">Новый public key для замены</label>
              <input
                :id="`replace-${key.key_id}`"
                type="file"
                accept=".pem,text/plain"
                :disabled="busyKeyId === key.key_id"
                @change="selectReplacement(key.key_id, $event)"
              />
              <button
                type="button"
                class="secondary"
                :disabled="busyKeyId === key.key_id || !replacements[key.key_id]"
                @click="replaceTrustedKey(key)"
              >
                Заменить
              </button>
            </div>
            <div class="actions">
              <button
                type="button"
                class="secondary"
                :disabled="busyKeyId === key.key_id"
                @click="setTrustedKeyEnabled(key, !key.enabled)"
              >
                {{ key.enabled ? 'Отключить' : 'Включить' }}
              </button>
              <button
                type="button"
                class="danger"
                :disabled="busyKeyId === key.key_id"
                @click="removeTrustedKey(key)"
              >
                Удалить
              </button>
            </div>
          </li>
        </ul>
      </section>
    </template>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.keys { display: grid; gap: var(--space-6); }
.keys__header { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
.keys__header h1 { margin: 0 0 var(--space-2); }
.keys__header p, .status { margin: 0; color: var(--color-steel); }
.contour { border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: var(--space-2) var(--space-3); }
.card { display: grid; gap: var(--space-3); padding: var(--space-5); border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: white; }
.card--wide { width: 100%; box-sizing: border-box; }
.card h2 { margin: 0; }
.card button { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-bridge-blue); color: white; font: inherit; cursor: pointer; }
.card button:disabled { opacity: .6; cursor: wait; }
.card button.secondary { background: white; color: var(--color-deep-harbor); border: 1px solid var(--color-mist); }
.card button.danger { background: #b91c1c; }
.identity { display: grid; gap: var(--space-2); margin: 0; }
.identity div { display: grid; gap: var(--space-1); }
.identity dt { color: var(--color-steel); }
.identity dd { margin: 0; overflow-wrap: anywhere; font-family: monospace; }
.warning { margin: 0; padding: var(--space-3); border: 1px solid #b45309; border-radius: var(--radius-md); }
.empty { margin: 0; color: var(--color-steel); }
.key-list { display: grid; gap: var(--space-4); padding: 0; margin: 0; list-style: none; }
.key-row { display: grid; gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-mist); border-radius: var(--radius-md); }
.key-row__identity, .key-row__replace { display: grid; gap: var(--space-2); }
.key-row code { overflow-wrap: anywhere; }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid #15803d; }
.error { border: 1px solid #b91c1c; }
@media (max-width: 640px) { .keys__header { flex-direction: column; } }
</style>
