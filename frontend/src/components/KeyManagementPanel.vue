<script setup lang="ts">
import axios from 'axios'
import { onMounted, ref } from 'vue'

import { apiClient } from '@/api/client'

type Contour = 'SOURCE' | 'TARGET'
type SigningStatus = { configured: boolean; fingerprint: string | null }
type TrustedKey = { fingerprint: string; enabled: boolean }
type KeyStatus = {
  contour: Contour
  source_signing: SigningStatus | null
  trusted_keys: TrustedKey[]
}

const props = defineProps<{ contour: Contour }>()
const status = ref<KeyStatus | null>(null)
const sourceFile = ref<File | null>(null)
const trustedFile = ref<File | null>(null)
const replacementFile = ref<File | null>(null)
const replacementFingerprint = ref('')
const busy = ref(false)
const error = ref('')
const message = ref('')
const MAX_KEY_BYTES = 64 * 1024

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return fallback
}

function pickFile(event: Event, target: 'source' | 'trusted' | 'replacement'): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  if (target === 'source') sourceFile.value = file
  if (target === 'trusted') trustedFile.value = file
  if (target === 'replacement') replacementFile.value = file
}

async function readBounded(file: File): Promise<string> {
  if (file.size <= 0 || file.size > MAX_KEY_BYTES) {
    throw new Error('Размер key file должен быть от 1 байта до 64 KiB.')
  }
  return file.text()
}

async function load(): Promise<void> {
  error.value = ''
  try {
    status.value = (await apiClient.get<KeyStatus>('/settings/keys')).data
  } catch (reason) {
    error.value = safeError('Не удалось загрузить состояние signing/trust keys.', reason)
  }
}

async function installSource(): Promise<void> {
  if (!sourceFile.value) {
    error.value = 'Выберите PEM Ed25519 private key.'
    return
  }
  if (!window.confirm('Ротировать SOURCE signing identity? Старые bundle потребуют старый public key на TARGET.')) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const privateKeyPem = await readBounded(sourceFile.value)
    await apiClient.put('/settings/keys/source-signing', {
      private_key_pem: privateKeyPem,
      confirm: true,
    })
    sourceFile.value = null
    await load()
    message.value = 'SOURCE signing identity установлена. Private key не возвращается порталом.'
  } catch (reason) {
    error.value = reason instanceof Error && !axios.isAxiosError(reason)
      ? reason.message
      : safeError('Не удалось установить SOURCE signing key.', reason)
  } finally {
    busy.value = false
  }
}

async function addTrusted(): Promise<void> {
  if (!trustedFile.value) {
    error.value = 'Выберите PEM Ed25519 public key.'
    return
  }
  if (!window.confirm('Добавить SOURCE public key в TARGET trust set?')) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const publicKeyPem = await readBounded(trustedFile.value)
    await apiClient.post('/settings/keys/trusted', { public_key_pem: publicKeyPem, confirm: true })
    trustedFile.value = null
    await load()
    message.value = 'Trusted SOURCE public key добавлен.'
  } catch (reason) {
    error.value = reason instanceof Error && !axios.isAxiosError(reason)
      ? reason.message
      : safeError('Не удалось добавить trusted key.', reason)
  } finally {
    busy.value = false
  }
}

async function setEnabled(item: TrustedKey, enabled: boolean): Promise<void> {
  const action = enabled ? 'включить' : 'отключить'
  if (!window.confirm(`${action} trust для ${item.fingerprint}?`)) return
  busy.value = true
  error.value = ''
  try {
    await apiClient.post(
      `/settings/keys/trusted/${encodeURIComponent(item.fingerprint)}/${enabled ? 'enable' : 'disable'}`,
      { confirm: true },
    )
    await load()
  } catch (reason) {
    error.value = safeError(`Не удалось ${action} trusted key.`, reason)
  } finally {
    busy.value = false
  }
}

async function removeTrusted(item: TrustedKey): Promise<void> {
  if (!window.confirm(`Удалить trusted key ${item.fingerprint}? Bundle, подписанные им, перестанут проходить verification.`)) return
  busy.value = true
  error.value = ''
  try {
    await apiClient.post(`/settings/keys/trusted/${encodeURIComponent(item.fingerprint)}/remove`, { confirm: true })
    await load()
    message.value = 'Trusted key удалён.'
  } catch (reason) {
    error.value = safeError('Не удалось удалить trusted key.', reason)
  } finally {
    busy.value = false
  }
}

async function replaceTrusted(): Promise<void> {
  if (!replacementFingerprint.value || !replacementFile.value) {
    error.value = 'Выберите существующий fingerprint и новый public key.'
    return
  }
  if (!window.confirm('Заменить trusted key? Новый key будет установлен до удаления старого.')) return
  busy.value = true
  error.value = ''
  try {
    const publicKeyPem = await readBounded(replacementFile.value)
    await apiClient.post(
      `/settings/keys/trusted/${encodeURIComponent(replacementFingerprint.value)}/replace`,
      { public_key_pem: publicKeyPem, confirm: true },
    )
    replacementFile.value = null
    replacementFingerprint.value = ''
    await load()
    message.value = 'Trusted key заменён.'
  } catch (reason) {
    error.value = reason instanceof Error && !axios.isAxiosError(reason)
      ? reason.message
      : safeError('Не удалось заменить trusted key.', reason)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="key-panel" aria-labelledby="key-management-title">
    <div>
      <h2 id="key-management-title">Signing и trust keys</h2>
      <p class="muted">Private key никогда не отображается и не экспортируется через web API.</p>
    </div>

    <template v-if="props.contour === 'SOURCE'">
      <p class="status">
        SOURCE signing identity:
        <strong>{{ status?.source_signing?.configured ? 'настроена' : 'не настроена' }}</strong>
      </p>
      <code v-if="status?.source_signing?.fingerprint">{{ status.source_signing.fingerprint }}</code>
      <label for="source-signing-key">PEM Ed25519 private key, максимум 64 KiB</label>
      <input id="source-signing-key" type="file" accept=".pem,text/plain" :disabled="busy" @change="pickFile($event, 'source')" />
      <button type="button" :disabled="busy || !sourceFile" @click="installSource">
        Установить / ротировать signing identity
      </button>
    </template>

    <template v-else>
      <label for="trusted-key">Новый PEM Ed25519 public key, максимум 64 KiB</label>
      <input id="trusted-key" type="file" accept=".pem,text/plain" :disabled="busy" @change="pickFile($event, 'trusted')" />
      <button type="button" :disabled="busy || !trustedFile" @click="addTrusted">Добавить trusted key</button>

      <div v-if="status?.trusted_keys.length" class="trust-list">
        <article v-for="item in status.trusted_keys" :key="item.fingerprint" class="trust-item">
          <code>{{ item.fingerprint }}</code>
          <strong>{{ item.enabled ? 'enabled' : 'disabled' }}</strong>
          <div class="actions">
            <button v-if="item.enabled" type="button" class="secondary" :disabled="busy" @click="setEnabled(item, false)">Отключить</button>
            <button v-else type="button" class="secondary" :disabled="busy" @click="setEnabled(item, true)">Включить</button>
            <button type="button" class="danger" :disabled="busy" @click="removeTrusted(item)">Удалить</button>
          </div>
        </article>
      </div>
      <p v-else class="muted">Trusted SOURCE keys пока не настроены.</p>

      <div v-if="status?.trusted_keys.length" class="replace-box">
        <label for="replace-fingerprint">Заменить существующий key</label>
        <select id="replace-fingerprint" v-model="replacementFingerprint" :disabled="busy">
          <option value="">Выберите fingerprint</option>
          <option v-for="item in status.trusted_keys" :key="item.fingerprint" :value="item.fingerprint">
            {{ item.fingerprint }}
          </option>
        </select>
        <input type="file" accept=".pem,text/plain" :disabled="busy" @change="pickFile($event, 'replacement')" />
        <button type="button" :disabled="busy || !replacementFingerprint || !replacementFile" @click="replaceTrusted">Заменить key</button>
      </div>
    </template>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.key-panel { grid-column: 1 / -1; display: grid; gap: var(--space-3); padding: var(--space-5); border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: white; }
.key-panel h2 { margin: 0; }
.muted, .status { margin: 0; color: var(--color-steel); }
code { overflow-wrap: anywhere; }
button { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-bridge-blue); color: white; font: inherit; cursor: pointer; }
button:disabled { opacity: .6; cursor: wait; }
button.secondary { background: white; color: var(--color-deep-harbor); border: 1px solid var(--color-mist); }
button.danger { background: #b91c1c; }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.trust-list { display: grid; gap: var(--space-2); }
.trust-item, .replace-box { display: grid; gap: var(--space-2); padding: var(--space-3); border: 1px solid var(--color-mist); border-radius: var(--radius-md); }
select { min-height: 42px; border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-2); }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid #15803d; }
.error { border: 1px solid #b91c1c; }
</style>
