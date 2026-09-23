<script setup lang="ts">
import axios from 'axios'
import { computed, onMounted, ref } from 'vue'

import { apiClient } from '@/api/client'

type HarborProfile = {
  id: string
  name: string
  url: string
  username: string | null
  verify_tls: boolean
  enabled: boolean
  credential_configured: boolean
  custom_ca_configured: boolean
  is_default: boolean
  is_active: boolean
}

type ProfilesResponse = { items: HarborProfile[] }
type ConnectionTest = {
  ok: boolean
  code: string
  message: string
  version: string | null
}

const emit = defineEmits<{
  changed: []
}>()

const profiles = ref<HarborProfile[]>([])
const selectedId = ref('')
const loading = ref(true)
const busy = ref(false)
const savingProfile = ref(false)
const editingId = ref<string | null>(null)
const testingId = ref<string | null>(null)
const message = ref('')
const error = ref('')

const name = ref('')
const url = ref('')
const username = ref('')
const verifyTls = ref(true)
const credential = ref('')

const activeProfile = computed(() => profiles.value.find((item) => item.is_active) ?? null)
const enabledProfiles = computed(() => profiles.value.filter((item) => item.enabled))

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return value instanceof Error && value.message ? value.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await apiClient.get<ProfilesResponse>('/settings/harbor/profiles')
    profiles.value = response.data.items
    selectedId.value = response.data.items.find((item) => item.is_active)?.id ?? ''
  } catch (reason) {
    error.value = safeError('Не удалось загрузить Harbor profiles.', reason)
  } finally {
    loading.value = false
  }
}

async function activate(): Promise<void> {
  if (!selectedId.value || selectedId.value === activeProfile.value?.id) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.put<HarborProfile>(
      `/settings/harbor/profiles/${encodeURIComponent(selectedId.value)}/activate`,
    )
    await load()
    message.value = `Legacy fallback Harbor: ${response.data.name}.`
    emit('changed')
  } catch (reason) {
    error.value = safeError('Не удалось изменить legacy fallback Harbor.', reason)
  } finally {
    busy.value = false
  }
}

function resetEditor(): void {
  editingId.value = null
  name.value = ''
  url.value = ''
  username.value = ''
  credential.value = ''
  verifyTls.value = true
}

function editProfile(profile: HarborProfile): void {
  if (profile.is_default) return
  editingId.value = profile.id
  name.value = profile.name
  url.value = profile.url
  username.value = profile.username ?? ''
  credential.value = ''
  verifyTls.value = profile.verify_tls
  error.value = ''
  message.value = ''
}

async function saveProfile(): Promise<void> {
  if (!name.value.trim() || !url.value.trim()) {
    error.value = 'Для нового профиля обязательны имя и URL.'
    return
  }
  savingProfile.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = {
      name: name.value.trim(),
      url: url.value.trim(),
      username: username.value.trim() || null,
      verify_tls: verifyTls.value,
      enabled: true,
    }
    const response = editingId.value
      ? await apiClient.patch<HarborProfile>(
          `/settings/harbor/profiles/${encodeURIComponent(editingId.value)}`,
          payload,
        )
      : await apiClient.post<HarborProfile>('/settings/harbor/profiles', payload)
    if (credential.value) {
      await apiClient.put(
        `/settings/harbor/profiles/${encodeURIComponent(response.data.id)}/credential`,
        { secret: credential.value },
      )
    }
    const wasEditing = editingId.value !== null
    resetEditor()
    await load()
    selectedId.value = response.data.id
    message.value = wasEditing
      ? 'Harbor profile обновлён.'
      : 'Harbor profile создан. Проверьте подключение; для transfer он выбирается в Export/Import workflow.'
  } catch (reason) {
    error.value = safeError(
      editingId.value ? 'Не удалось обновить Harbor profile.' : 'Не удалось создать Harbor profile.',
      reason,
    )
  } finally {
    savingProfile.value = false
  }
}

async function testProfile(profile: HarborProfile): Promise<void> {
  testingId.value = profile.id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.post<ConnectionTest>(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}/test`,
    )
    if (response.data.ok) {
      message.value = `${profile.name}: подключение успешно${response.data.version ? ` · Harbor ${response.data.version}` : ''}.`
    } else {
      error.value = `${profile.name}: ${response.data.message}`
    }
  } catch (reason) {
    error.value = safeError(`Не удалось проверить ${profile.name}.`, reason)
  } finally {
    testingId.value = null
  }
}

async function deleteProfile(profile: HarborProfile): Promise<void> {
  if (profile.is_default || profile.is_active) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await apiClient.delete(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}`,
    )
    await load()
    message.value = `Профиль ${profile.name} удалён.`
  } catch (reason) {
    error.value = safeError(`Не удалось удалить ${profile.name}.`, reason)
  } finally {
    busy.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="profiles-card" aria-labelledby="harbor-profiles-title">
    <div class="profiles-heading">
      <div>
        <h2 id="harbor-profiles-title">Harbor profiles</h2>
        <p class="status">
          Один Portal может хранить несколько Harbor. Новые Export/Import выбирают profile внутри workflow; selector ниже задаёт только legacy fallback для старых клиентов.
        </p>
      </div>
      <span v-if="activeProfile" class="active-badge">Legacy fallback · {{ activeProfile.name }}</span>
    </div>

    <p v-if="loading" class="status">Загрузка profiles…</p>
    <template v-else>
      <div class="selector-row">
        <label for="active-harbor-profile">Legacy fallback Harbor</label>
        <select id="active-harbor-profile" v-model="selectedId" :disabled="busy">
          <option v-for="profile in enabledProfiles" :key="profile.id" :value="profile.id">
            {{ profile.name }} · {{ profile.url }}
          </option>
        </select>
        <button
          type="button"
          :disabled="busy || !selectedId || selectedId === activeProfile?.id"
          @click="activate"
        >
          {{ busy ? 'Переключение…' : 'Сделать fallback' }}
        </button>
      </div>

      <div class="profile-list">
        <article v-for="profile in profiles" :key="profile.id" class="profile-row">
          <div>
            <strong>{{ profile.name }}</strong>
            <span v-if="profile.is_active" class="inline-active">legacy fallback</span>
            <p>{{ profile.url }}</p>
            <small>
              {{ profile.username || 'без username' }} · TLS {{ profile.verify_tls ? 'on' : 'off' }}
              · credential {{ profile.credential_configured ? 'есть' : 'нет' }}
            </small>
          </div>
          <div class="row-actions">
            <button
              type="button"
              class="secondary"
              :disabled="testingId === profile.id"
              @click="testProfile(profile)"
            >
              {{ testingId === profile.id ? 'Проверка…' : 'Проверить' }}
            </button>
            <button
              v-if="!profile.is_default"
              type="button"
              class="secondary"
              :disabled="busy"
              @click="editProfile(profile)"
            >
              Изменить
            </button>
            <button
              v-if="!profile.is_default"
              type="button"
              class="secondary danger"
              :disabled="busy || profile.is_active"
              @click="deleteProfile(profile)"
            >
              Удалить
            </button>
          </div>
        </article>
      </div>

      <form class="create-form" @submit.prevent="saveProfile">
        <div class="editor-heading">
          <h3>{{ editingId ? 'Изменить Harbor profile' : 'Добавить Harbor' }}</h3>
          <button v-if="editingId" type="button" class="secondary" @click="resetEditor">
            Отмена
          </button>
        </div>
        <div class="form-grid">
          <label>
            Имя профиля
            <input v-model="name" type="text" maxlength="128" placeholder="Harbor DC-2" />
          </label>
          <label>
            URL
            <input v-model="url" type="url" placeholder="https://harbor-dc2.local" />
          </label>
          <label>
            Service account
            <input v-model="username" type="text" autocomplete="username" />
          </label>
          <label>
            Credential
            <input
              v-model="credential"
              type="password"
              autocomplete="new-password"
              :placeholder="editingId ? 'оставьте пустым, чтобы не менять' : 'необязательно при создании'"
            />
          </label>
        </div>
        <label class="checkbox-row">
          <input v-model="verifyTls" type="checkbox" />
          Проверять TLS-сертификат
        </label>
        <button type="submit" :disabled="savingProfile">
          {{ savingProfile ? 'Сохранение…' : editingId ? 'Сохранить изменения' : 'Добавить профиль' }}
        </button>
      </form>
    </template>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.profiles-card { display: grid; gap: var(--space-4); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); grid-column: 1 / -1; }
.profiles-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.profiles-heading h2, .create-form h3 { margin: 0; }
.status, .profile-row p { margin: 0; color: var(--color-text-muted); }
.active-badge, .inline-active { border-radius: var(--radius-full); padding: var(--space-1) var(--space-2); background: var(--color-success-surface); color: var(--color-success-text); font-size: 12px; font-weight: 700; }
.inline-active { margin-left: var(--space-2); }
.selector-row { display: grid; grid-template-columns: auto minmax(240px, 1fr) auto; gap: var(--space-3); align-items: end; }
.selector-row label { align-self: center; }
select, input { min-height: 42px; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); padding: 0 var(--space-3); font: inherit; background: var(--color-surface); color: var(--color-text); }
.profile-list { display: grid; gap: var(--space-2); }
.profile-row { display: flex; justify-content: space-between; gap: var(--space-4); align-items: center; padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.profile-row small { color: var(--color-text-muted); }
.row-actions { display: flex; gap: var(--space-2); }
.create-form { display: grid; gap: var(--space-3); padding-top: var(--space-4); border-top: 1px solid var(--color-border); }
.editor-heading { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3); }
.form-grid label { display: grid; gap: var(--space-2); }
.checkbox-row { display: flex; gap: var(--space-2); align-items: center; }
button { min-height: 42px; border: 0; border-radius: var(--radius-md); padding: 0 var(--space-4); background: var(--color-action-surface); color: var(--color-on-accent); font: inherit; cursor: pointer; }
button.secondary { background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border-control); }
button.danger { color: var(--color-danger-text); }
button:disabled { opacity: .55; cursor: not-allowed; }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid var(--color-success-text); color: var(--color-success-text); }
.error { border: 1px solid var(--color-danger-text); color: var(--color-danger-text); }
@media (max-width: 720px) {
  .profiles-heading, .profile-row { align-items: stretch; flex-direction: column; }
  .selector-row { grid-template-columns: 1fr; }
  .row-actions { flex-wrap: wrap; }
}
</style>
