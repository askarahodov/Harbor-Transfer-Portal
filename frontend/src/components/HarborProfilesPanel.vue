<script setup lang="ts">
import axios from 'axios'
import { onMounted, reactive, ref } from 'vue'

import {
  createHarborProfile,
  deleteHarborProfile,
  installHarborProfileCa,
  listHarborProfiles,
  removeHarborProfileCa,
  rotateHarborProfileCredential,
  testHarborProfile,
  updateHarborProfile,
  type HarborProfile,
} from '@/api/harborProfiles'

type Draft = {
  name: string
  url: string
  username: string
  verify_tls: boolean
  enabled: boolean
  credential: string
  testMessage: string
}

const profiles = ref<HarborProfile[]>([])
const drafts = reactive<Record<string, Draft>>({})
const loading = ref(true)
const busy = ref<string | null>(null)
const message = ref('')
const error = ref('')

const createName = ref('')
const createUrl = ref('')
const createUsername = ref('')
const createVerifyTls = ref(true)

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const detail = value.response?.data?.detail
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
      return detail.message
    }
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  if (value instanceof Error && value.message) return value.message
  return fallback
}

function syncDraft(profile: HarborProfile): void {
  drafts[profile.id] = {
    name: profile.name,
    url: profile.url ?? '',
    username: profile.username ?? '',
    verify_tls: profile.verify_tls,
    enabled: profile.enabled,
    credential: '',
    testMessage: '',
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    profiles.value = await listHarborProfiles()
    for (const profile of profiles.value) syncDraft(profile)
  } catch (reason) {
    error.value = safeError('Не удалось загрузить Harbor profiles.', reason)
  } finally {
    loading.value = false
  }
}

async function createProfile(): Promise<void> {
  if (!createName.value.trim() || !createUrl.value.trim()) {
    error.value = 'Укажите имя и URL нового Harbor profile.'
    return
  }
  busy.value = 'create'
  error.value = ''
  message.value = ''
  try {
    await createHarborProfile({
      name: createName.value.trim(),
      url: createUrl.value.trim(),
      username: createUsername.value.trim() || null,
      verify_tls: createVerifyTls.value,
    })
    createName.value = ''
    createUrl.value = ''
    createUsername.value = ''
    createVerifyTls.value = true
    await load()
    message.value = 'Harbor profile создан.'
  } catch (reason) {
    error.value = safeError('Не удалось создать Harbor profile.', reason)
  } finally {
    busy.value = null
  }
}

async function saveProfile(profile: HarborProfile): Promise<void> {
  if (profile.legacy_default) return
  const draft = drafts[profile.id]
  if (!draft) return
  busy.value = `save:${profile.id}`
  error.value = ''
  message.value = ''
  try {
    await updateHarborProfile(profile.id, {
      name: draft.name.trim(),
      url: draft.url.trim(),
      username: draft.username.trim() || null,
      verify_tls: draft.verify_tls,
      enabled: draft.enabled,
    })
    await load()
    message.value = `Profile «${draft.name.trim()}» сохранён.`
  } catch (reason) {
    error.value = safeError('Не удалось сохранить Harbor profile.', reason)
  } finally {
    busy.value = null
  }
}

async function testProfile(profile: HarborProfile): Promise<void> {
  busy.value = `test:${profile.id}`
  error.value = ''
  try {
    const result = await testHarborProfile(profile.id)
    const draft = drafts[profile.id]
    if (draft) {
      draft.testMessage = result.ok
        ? `Подключение успешно${result.version ? ` · Harbor ${result.version}` : ''}.`
        : result.message
    }
  } catch (reason) {
    error.value = safeError('Не удалось проверить Harbor profile.', reason)
  } finally {
    busy.value = null
  }
}

async function rotateCredential(profile: HarborProfile): Promise<void> {
  const draft = drafts[profile.id]
  if (!draft?.credential) {
    error.value = 'Введите новый credential.'
    return
  }
  busy.value = `credential:${profile.id}`
  error.value = ''
  message.value = ''
  try {
    await rotateHarborProfileCredential(profile.id, draft.credential)
    draft.credential = ''
    await load()
    message.value = `Credential profile «${profile.name}» обновлён.`
  } catch (reason) {
    error.value = safeError('Не удалось обновить credential.', reason)
  } finally {
    busy.value = null
  }
}

async function uploadCa(profile: HarborProfile, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = `ca:${profile.id}`
  error.value = ''
  message.value = ''
  try {
    await installHarborProfileCa(profile.id, await file.text())
    await load()
    message.value = `CA profile «${profile.name}» обновлён.`
  } catch (reason) {
    error.value = safeError('Не удалось установить CA.', reason)
  } finally {
    input.value = ''
    busy.value = null
  }
}

async function removeCa(profile: HarborProfile): Promise<void> {
  busy.value = `ca:${profile.id}`
  error.value = ''
  message.value = ''
  try {
    await removeHarborProfileCa(profile.id)
    await load()
    message.value = `Managed CA profile «${profile.name}» удалён.`
  } catch (reason) {
    error.value = safeError('Не удалось удалить CA.', reason)
  } finally {
    busy.value = null
  }
}

async function removeProfile(profile: HarborProfile): Promise<void> {
  if (profile.legacy_default) return
  busy.value = `delete:${profile.id}`
  error.value = ''
  message.value = ''
  try {
    await deleteHarborProfile(profile.id)
    await load()
    message.value = `Profile «${profile.name}» удалён.`
  } catch (reason) {
    error.value = safeError('Не удалось удалить Harbor profile.', reason)
  } finally {
    busy.value = null
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <section class="profiles-card" aria-labelledby="harbor-profiles-title">
    <div class="heading">
      <div>
        <h2 id="harbor-profiles-title">Harbor profiles</h2>
        <p>
          Несколько Harbor можно настроить один раз и выбирать в Export/Import. Выбранный profile
          фиксируется в operation.
        </p>
      </div>
      <button class="secondary" type="button" :disabled="loading" @click="load">
        {{ loading ? 'Загрузка…' : 'Обновить' }}
      </button>
    </div>

    <p v-if="error" class="notice notice--error" role="alert">{{ error }}</p>
    <p v-if="message" class="notice notice--success" role="status">{{ message }}</p>

    <form class="create-grid" @submit.prevent="createProfile">
      <label>
        Имя
        <input v-model="createName" type="text" maxlength="128" placeholder="Production" required />
      </label>
      <label>
        URL
        <input v-model="createUrl" type="url" placeholder="https://harbor.example" required />
      </label>
      <label>
        Service account
        <input v-model="createUsername" type="text" autocomplete="username" />
      </label>
      <label class="checkbox">
        <input v-model="createVerifyTls" type="checkbox" />
        Проверять TLS
      </label>
      <button type="submit" :disabled="busy === 'create'">
        {{ busy === 'create' ? 'Создание…' : 'Добавить profile' }}
      </button>
    </form>

    <div v-if="!loading" class="profile-list">
      <article v-for="profile in profiles" :key="profile.id" class="profile">
        <div class="profile__heading">
          <div>
            <strong>{{ profile.name }}</strong>
            <span v-if="profile.legacy_default" class="badge">Default / compatibility</span>
            <small>{{ profile.url || 'URL не настроен' }}</small>
          </div>
          <span :class="['status', { 'status--off': !profile.enabled }]">
            {{ profile.enabled ? 'enabled' : 'disabled' }}
          </span>
        </div>

        <p v-if="profile.legacy_default" class="hint">
          Этот profile использует существующие поля «Подключение / Credential / CA» ниже и
          сохраняет совместимость с HARBOR_*.
        </p>

        <template v-else-if="drafts[profile.id]">
          <div class="edit-grid">
            <label>
              Имя
              <input v-model="drafts[profile.id].name" type="text" maxlength="128" />
            </label>
            <label>
              URL
              <input v-model="drafts[profile.id].url" type="url" />
            </label>
            <label>
              Service account
              <input v-model="drafts[profile.id].username" type="text" autocomplete="username" />
            </label>
            <label class="checkbox">
              <input v-model="drafts[profile.id].verify_tls" type="checkbox" />
              Проверять TLS
            </label>
            <label class="checkbox">
              <input v-model="drafts[profile.id].enabled" type="checkbox" />
              Profile включён
            </label>
          </div>

          <div class="actions">
            <button type="button" :disabled="busy !== null" @click="saveProfile(profile)">Сохранить</button>
            <button class="secondary" type="button" :disabled="busy !== null" @click="testProfile(profile)">
              Проверить
            </button>
            <button class="danger" type="button" :disabled="busy !== null" @click="removeProfile(profile)">
              Удалить
            </button>
          </div>

          <p v-if="drafts[profile.id].testMessage" class="hint" role="status">
            {{ drafts[profile.id].testMessage }}
          </p>

          <div class="secret-grid">
            <label>
              Новый credential
              <input
                v-model="drafts[profile.id].credential"
                type="password"
                autocomplete="new-password"
                placeholder="Значение не читается обратно"
              />
            </label>
            <button type="button" :disabled="busy !== null" @click="rotateCredential(profile)">
              Ротировать
            </button>
            <span class="hint">Текущее: {{ profile.credential_configured ? 'настроено' : 'не настроено' }}</span>
          </div>

          <div class="ca-row">
            <label>
              Managed CA
              <input
                type="file"
                accept=".pem,.crt,.cer,text/plain"
                :disabled="busy !== null"
                @change="uploadCa(profile, $event)"
              />
            </label>
            <button
              v-if="profile.custom_ca_configured"
              class="secondary"
              type="button"
              :disabled="busy !== null"
              @click="removeCa(profile)"
            >
              Удалить CA
            </button>
            <span class="hint">{{ profile.custom_ca_configured ? 'CA настроен' : 'без managed CA' }}</span>
          </div>
        </template>
      </article>
    </div>
  </section>
</template>

<style scoped>
.profiles-card { display: grid; gap: var(--space-5); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.heading, .profile__heading, .actions, .secret-grid, .ca-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.heading p, .hint, .profile small { margin: var(--space-1) 0 0; color: var(--color-text-muted); }
.create-grid, .edit-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); align-items: end; }
.create-grid label, .edit-grid label, .secret-grid label, .ca-row label { display: grid; gap: var(--space-1); font-weight: 700; }
input { min-height: 40px; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; }
input[type='file'] { padding: var(--space-2); }
.checkbox { display: flex !important; align-items: center; gap: var(--space-2) !important; }
.checkbox input { min-height: auto; }
.profile-list { display: grid; gap: var(--space-3); }
.profile { display: grid; gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.profile__heading > div { display: grid; gap: var(--space-1); }
.badge, .status { display: inline-flex; width: fit-content; padding: 2px var(--space-2); border-radius: var(--radius-full); background: var(--color-success-surface); color: var(--color-success-text); font-size: 12px; font-weight: 800; }
.badge { margin-left: var(--space-2); background: var(--color-info-surface); color: var(--color-info-text); }
.status--off { background: var(--color-surface); color: var(--color-text-muted); }
button { min-height: 40px; padding: 0 var(--space-4); border: 0; border-radius: var(--radius-md); background: var(--color-action); color: var(--color-on-action); font-weight: 800; cursor: pointer; }
button.secondary { border: 1px solid var(--color-border-control); background: var(--color-surface); color: var(--color-text); }
button.danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
button:disabled { cursor: not-allowed; opacity: .55; }
.notice { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.notice--error { background: var(--color-danger-surface); color: var(--color-danger-text); }
.notice--success { background: var(--color-success-surface); color: var(--color-success-text); }
@media (max-width: 980px) {
  .create-grid, .edit-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .create-grid, .edit-grid { grid-template-columns: 1fr; }
  .heading, .profile__heading, .actions, .secret-grid, .ca-row { align-items: stretch; flex-direction: column; }
}
</style>
