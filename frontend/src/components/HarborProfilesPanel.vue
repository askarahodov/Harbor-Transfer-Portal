<script setup lang="ts">
import axios from 'axios'
import { computed, nextTick, onMounted, ref } from 'vue'

import { apiClient } from '@/api/client'

type HarborProfile = {
  id: string
  registry_type: 'harbor' | 'nexus'
  nexus_format?: 'docker' | 'helm'
  nexus_search_url?: string | null
  nexus_search_repository?: string | null
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
const testingRegistryId = ref<string | null>(null)
const message = ref('')
const error = ref('')
const feedbackLocation = ref<'list' | 'editor'>('list')
const editorForm = ref<HTMLFormElement | null>(null)

const name = ref('')
const url = ref('')
const username = ref('')
const verifyTls = ref(true)
const registryType = ref<'harbor' | 'nexus'>('harbor')
const nexusFormat = ref<'docker' | 'helm'>('docker')
const nexusSearchUrl = ref('')
const nexusSearchRepository = ref('')
const credential = ref('')
const caPem = ref('')

const activeProfile = computed(() => profiles.value.find((item) => item.is_active) ?? null)
const enabledProfiles = computed(() => profiles.value.filter((item) => item.enabled))

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const data = value.response?.data
    const apiMessage = data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) {
      const messages = data.detail
        .map((item: { msg?: unknown }) => item.msg)
        .filter((message: unknown): message is string => typeof message === 'string')
      if (messages.length) return `Проверьте поля формы: ${messages.join('; ')}`
    }
    if (value.response?.status === 403) {
      return 'У вас нет прав администратора для изменения профилей.'
    }
  }
  return value instanceof Error && value.message ? value.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const response = await apiClient.get<ProfilesResponse>('/settings/harbor/profiles')
    profiles.value = response.data.items.map((profile) => ({
      ...profile,
      registry_type: profile.registry_type === 'nexus' ? 'nexus' : 'harbor',
      nexus_format: profile.nexus_format === 'helm' ? 'helm' : 'docker',
    }))
    selectedId.value = response.data.items.find((item) => item.is_active)?.id ?? ''
  } catch (reason) {
    error.value = safeError('Не удалось загрузить профили реестров.', reason)
  } finally {
    loading.value = false
  }
}

async function activate(): Promise<void> {
  if (!selectedId.value || selectedId.value === activeProfile.value?.id) return
  feedbackLocation.value = 'list'
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.put<HarborProfile>(
      `/settings/harbor/profiles/${encodeURIComponent(selectedId.value)}/activate`,
    )
    await load()
    message.value = `Legacy fallback: ${response.data.name}.`
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
  caPem.value = ''
  verifyTls.value = true
  registryType.value = 'harbor'
  nexusFormat.value = 'docker'
  nexusSearchUrl.value = ''
  nexusSearchRepository.value = ''
}

function validateUrlForRegistry(url: string, registryType: 'harbor' | 'nexus'): string | null {
  if (!url.trim()) return 'URL обязателен'
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return 'URL должен начинаться с http:// или https://'
    }
    if (registryType === 'nexus') {
      if (!parsed.hostname) return 'Неверный hostname'
    } else {
      if (!parsed.hostname) return 'Неверный hostname'
    }
    return null
  } catch {
    return 'Неверный формат URL'
  }
}

function editProfile(profile: HarborProfile): void {
  if (profile.is_default) return
  feedbackLocation.value = 'editor'
  editingId.value = profile.id
  name.value = profile.name
  url.value = profile.url
  username.value = profile.username ?? ''
  credential.value = ''
  caPem.value = ''
  verifyTls.value = profile.verify_tls
  registryType.value = profile.registry_type ?? 'harbor'
  nexusFormat.value = profile.nexus_format ?? 'docker'
  nexusSearchUrl.value = profile.nexus_search_url ?? ''
  nexusSearchRepository.value = profile.nexus_search_repository ?? ''
  error.value = ''
  message.value = ''
  void nextTick(() => {
    editorForm.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    editorForm.value?.querySelector<HTMLInputElement>('input[name="profile-name"]')
      ?.focus({ preventScroll: true })
  })
}

async function saveProfile(): Promise<void> {
  feedbackLocation.value = 'editor'
  error.value = ''
  message.value = ''
  if (!name.value.trim() || !url.value.trim()) {
    error.value = 'Для нового профиля обязательны имя и URL.'
    return
  }
  const urlError = validateUrlForRegistry(url.value, registryType.value)
  if (urlError) {
    error.value = urlError
    return
  }
  if (registryType.value === 'nexus' && nexusFormat.value === 'helm') {
    const parsed = new URL(url.value)
    if (parsed.protocol !== 'https:' || !parsed.hostname ||
        (parsed.pathname !== '/' && parsed.pathname !== '') || parsed.search || parsed.hash) {
      error.value = 'Helm HTTP требует HTTPS URL корня Nexus UI без пути.'
      return
    }
    if (!nexusSearchRepository.value.trim()) {
      error.value = 'Укажите точное имя Helm HTTP repository.'
      return
    }
  } else if (registryType.value === 'nexus' &&
             (nexusSearchUrl.value || nexusSearchRepository.value)) {
    if (nexusSearchRepository.value.trim() && !nexusSearchUrl.value.trim()) {
      error.value = 'Для привязки Docker repository требуется Nexus UI HTTPS URL.'
      return
    }
    if (!nexusSearchUrl.value.startsWith('https://') ||
        validateUrlForRegistry(nexusSearchUrl.value, 'nexus')) {
      error.value = 'Для Nexus REST Search требуется HTTPS URL корня Nexus UI без пути.'
      return
    }
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
      registry_type: registryType.value,
      nexus_format: registryType.value === 'nexus' ? nexusFormat.value : 'docker',
      ...((
        nexusSearchUrl.value.trim() ||
        nexusSearchRepository.value.trim() ||
        (editingId.value && profiles.value.some((profile) =>
          profile.id === editingId.value && (profile.nexus_search_url || profile.nexus_search_repository)
        ))
      ) ? {
        nexus_search_url: registryType.value === 'nexus' && nexusFormat.value === 'docker'
          ? nexusSearchUrl.value.trim() || null : null,
        nexus_search_repository: registryType.value === 'nexus'
          ? nexusSearchRepository.value.trim() || null : null,
      } : {}),
    }
    const response = editingId.value
      ? await apiClient.patch<HarborProfile>(
          `/settings/harbor/profiles/${encodeURIComponent(editingId.value)}`,
          payload,
        )
      : await apiClient.post<HarborProfile>('/settings/harbor/profiles', {
          ...payload,
          enabled: true,
        })
    if (credential.value) {
      await apiClient.put(
        `/settings/harbor/profiles/${encodeURIComponent(response.data.id)}/credential`,
        { secret: credential.value },
      )
    }
    if (caPem.value.trim()) {
      await apiClient.put(
        `/settings/harbor/profiles/${encodeURIComponent(response.data.id)}/ca`,
        { certificate_pem: caPem.value.trim() },
      )
    }
    const wasEditing = editingId.value !== null
    resetEditor()
    await load()
    feedbackLocation.value = 'editor'
    emit('changed')
    message.value = wasEditing
      ? 'Профиль обновлён.'
      : 'Профиль создан. Проверьте подключение; для transfer он выбирается в Export/Import workflow.'
  } catch (reason) {
    error.value = safeError(
      editingId.value ? 'Не удалось обновить профиль.' : 'Не удалось создать профиль.',
      reason,
    )
  } finally {
    savingProfile.value = false
  }
}

async function testProfile(profile: HarborProfile): Promise<void> {
  // The Harbor REST check is never valid for a Nexus Docker connector,
  // regardless of how this action is invoked.
  if (profile.registry_type === 'nexus') {
    feedbackLocation.value = 'list'
    error.value = 'Для Nexus используйте проверку Docker Registry v2.'
    message.value = ''
    return
  }
  feedbackLocation.value = 'list'
  testingId.value = profile.id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.post<ConnectionTest>(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}/test`,
      {},
      { timeout: 15000 }
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

async function testRegistry(profile: HarborProfile): Promise<void> {
  feedbackLocation.value = 'list'
  testingRegistryId.value = profile.id
  error.value = ''
  message.value = ''
  try {
    const response = await apiClient.post<ConnectionTest>(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}/test-registry`,
      {},
      { timeout: 60000 }
    )
    if (response.data.ok) {
      message.value = `${profile.name}: ${response.data.message}${response.data.version ? ` · ${response.data.version}` : ''}.`
    } else {
      error.value = `${profile.name}: ${response.data.message}`
    }
  } catch (reason) {
    error.value = safeError(`Не удалось проверить ${profile.name}.`, reason)
  } finally {
    testingRegistryId.value = null
  }
}

async function toggleProfileEnabled(profile: HarborProfile): Promise<void> {
  if (profile.is_default || (profile.is_active && profile.enabled)) return
  feedbackLocation.value = 'list'
  busy.value = true
  error.value = ''
  message.value = ''
  const nextEnabled = !profile.enabled
  try {
    await apiClient.patch<HarborProfile>(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}`,
      { enabled: nextEnabled },
    )
    await load()
    emit('changed')
    message.value = nextEnabled
      ? `Профиль ${profile.name} включён и снова доступен для новых transfer workflows.`
      : `Профиль ${profile.name} отключён и больше не предлагается для новых transfer workflows.`
  } catch (reason) {
    error.value = safeError(
      `Не удалось ${nextEnabled ? 'включить' : 'отключить'} ${profile.name}.`,
      reason,
    )
  } finally {
    busy.value = false
  }
}

async function removeProfileCa(profile: HarborProfile): Promise<void> {
  if (profile.is_default || !profile.custom_ca_configured) return
  if (!window.confirm(`Удалить custom CA для ${profile.name}? Новые подключения будут использовать системное trust store.`)) {
    return
  }
  feedbackLocation.value = 'list'
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    await apiClient.delete(
      `/settings/harbor/profiles/${encodeURIComponent(profile.id)}/ca`,
    )
    await load()
    emit('changed')
    message.value = `Custom CA для ${profile.name} удалён.`
  } catch (reason) {
    error.value = safeError(`Не удалось удалить custom CA для ${profile.name}.`, reason)
  } finally {
    busy.value = false
  }
}

async function deleteProfile(profile: HarborProfile): Promise<void> {
  if (profile.is_default || profile.is_active) return
  feedbackLocation.value = 'list'
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
        <h2 id="harbor-profiles-title">Профили реестров Harbor / Nexus</h2>
        <p class="status">
          Portal хранит профили Harbor и Nexus. SOURCE/ TARGET выбирают реестр внутри workflow; selector ниже задаёт legacy fallback для старых клиентов.
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
            {{ profile.registry_type === 'nexus' ? 'Nexus · Docker v2' : 'Harbor' }} · {{ profile.name }} · {{ profile.url }}
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

      <p v-if="feedbackLocation === 'list' && message" class="success" role="status">{{ message }}</p>
      <p v-if="feedbackLocation === 'list' && error" class="error" role="alert">{{ error }}</p>

      <div class="profile-list">
        <article v-for="profile in profiles" :key="profile.id" class="profile-row">
          <div>
            <div class="profile-title">
              <strong>{{ profile.name }}</strong>
              <span class="registry-kind">{{ profile.registry_type === 'nexus'
                ? profile.nexus_format === 'helm' ? 'Nexus · Helm HTTP' : 'Nexus · Docker v2'
                : 'Harbor' }}</span>
              <span v-if="profile.is_active" class="inline-active">legacy fallback</span>
              <span
                class="state-badge"
                :class="profile.enabled ? 'state-badge--enabled' : 'state-badge--disabled'"
              >
                {{ profile.enabled ? 'enabled' : 'disabled' }}
              </span>
            </div>
            <p>{{ profile.url }}</p>
            <p v-if="profile.is_active && !profile.is_default" class="profile-hint">
              Это текущий legacy fallback. Чтобы отключить или удалить профиль, сначала
              выберите другой fallback в списке выше.
            </p>
            <p v-if="profile.is_default" class="profile-hint">
              Default Harbor защищён от удаления и редактируется в разделе ниже.
            </p>
            <small>
              {{ profile.username || 'без username' }} · TLS {{ profile.verify_tls ? 'on' : 'off' }}
              · credential {{ profile.credential_configured ? 'есть' : 'нет' }}
              · custom CA {{ profile.custom_ca_configured ? 'есть' : 'нет' }}
            </small>
          </div>
          <div class="row-actions">
            <button
              v-if="profile.registry_type !== 'nexus'"
              type="button"
              class="secondary"
              :disabled="testingId === profile.id || !profile.enabled"
              :title="profile.enabled ? undefined : 'Сначала включите profile'"
              @click="testProfile(profile)"
            >
              {{ testingId === profile.id ? 'Проверка…' : 'Проверить (Harbor API)' }}
            </button>
            <button
              type="button"
              class="secondary"
              :disabled="testingRegistryId === profile.id || !profile.enabled"
              @click="testRegistry(profile)"
            >
              <span v-if="testingRegistryId === profile.id" class="spinner-inline"></span>
              {{ testingRegistryId === profile.id ? 'Проверка реестра…' : profile.registry_type === 'nexus'
                  ? profile.nexus_format === 'helm' ? 'Проверить Helm HTTP index.yaml' : 'Проверить Docker Registry v2'
                  : 'Проверить реестр' }}
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
              class="secondary"
              :disabled="busy || (profile.is_active && profile.enabled)"
              :title="profile.is_active && profile.enabled ? 'Сначала выберите другой legacy fallback' : undefined"
              @click="toggleProfileEnabled(profile)"
            >
              {{ profile.enabled ? 'Отключить' : 'Включить' }}
            </button>
            <button
              v-if="!profile.is_default && profile.custom_ca_configured"
              type="button"
              class="secondary danger"
              :disabled="busy"
              @click="removeProfileCa(profile)"
            >
              Удалить CA
            </button>
            <button
              v-if="!profile.is_default"
              type="button"
              class="secondary danger"
              :disabled="busy || profile.is_active"
              :title="profile.is_active ? 'Сначала выберите другой legacy fallback' : undefined"
              @click="deleteProfile(profile)"
            >
              Удалить profile
            </button>
          </div>
        </article>
      </div>

      <form ref="editorForm" class="create-form" novalidate @submit.prevent="saveProfile">
        <div class="editor-heading">
          <h3>{{ editingId ? 'Изменить профиль реестра' : 'Добавить реестр' }}</h3>
          <button v-if="editingId" type="button" class="secondary" @click="resetEditor">
            Отмена
          </button>
        </div>
        <div class="form-grid">
          <label>
            Имя профиля
            <input v-model="name" name="profile-name" type="text" maxlength="128" placeholder="Harbor DC-2" />
          </label>
          <label>
            {{ registryType === 'nexus' && nexusFormat === 'docker'
                ? 'Docker v2 connector URL (не Nexus UI)'
                : registryType === 'nexus' ? 'Nexus UI HTTPS origin (Helm HTTP)' : 'URL' }}
            <input v-model="url" type="url" :placeholder="registryType === 'nexus' && nexusFormat === 'docker'
              ? 'https://docker-connector.example.internal:5000'
              : registryType === 'nexus' ? 'https://nexus-ui.example.internal'
                : 'https://harbor-dc2.local'" />
          </label>
          <label>
            Тип реестра
            <select v-model="registryType">
              <option value="harbor">Harbor</option>
              <option value="nexus">Sonatype Nexus</option>
            </select>
          </label>
          <label v-if="registryType === 'nexus'">
            Формат Nexus repository
            <select v-model="nexusFormat" aria-label="Формат Nexus repository">
              <option value="docker">Docker Registry v2</option>
              <option value="helm">Helm HTTP (index.yaml, .tgz)</option>
            </select>
          </label>
          <p v-if="registryType === 'nexus' && nexusFormat === 'helm'" class="profile-hint">
            Для каждого Helm HTTP repository создайте отдельный профиль. URL — HTTPS origin
            Nexus UI/API, а имя repository указывается ниже. Проверка читает только
            index.yaml; Helm OCI и Docker Registry v2 для этого профиля не используются.
          </p>
          <p v-if="registryType === 'nexus' && nexusFormat === 'docker'" class="profile-hint">Nexus Browse: docker-proxy / v2 / baseimage означает Docker repository docker-proxy и image baseimage. В поле URL укажите корневой адрес HTTPS Docker v2 connector именно docker-proxy (проверка /v2/). Для каждого Docker repository hosted/proxy/group нужен отдельный профиль. Nexus UI HTTPS URL — отдельный необязательный адрес для административного списка и Helm HTTP; для проверки известного Docker image:tag он не требуется. Имя Docker repository (ниже) можно задать docker-proxy. Не используйте Helm OCI. Не отключайте TLS для обхода ошибок.</p>
          <label v-if="registryType === 'nexus' && nexusFormat === 'docker'">
            Nexus UI HTTPS URL (необязательно)
            <input v-model="nexusSearchUrl" type="url" placeholder="https://nexus-ui.example.internal" />
          </label>
          <label v-if="registryType === 'nexus'">
            {{ nexusFormat === 'helm'
              ? 'Имя Helm HTTP repository (обязательно)'
              : 'Имя Docker repository, обслуживаемого этим connector (необязательно)' }}
            <input v-model="nexusSearchRepository" type="text" maxlength="128"
              :placeholder="nexusFormat === 'helm' ? 'helm-hosted' : 'docker-hosted'" />
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
        <label class="ca-field" for="harbor-profile-ca">
          Custom CA (PEM)
          <textarea
            id="harbor-profile-ca"
            v-model="caPem"
            rows="6"
            spellcheck="false"
            placeholder="-----BEGIN CERTIFICATE-----"
          />
          <small>
            Необязательно. Существующий CA никогда не читается обратно: оставьте поле пустым,
            чтобы не менять его. Для удаления используйте действие в строке profile.
          </small>
        </label>
        <button type="submit" :disabled="savingProfile">
          {{ savingProfile ? 'Сохранение…' : editingId ? 'Сохранить изменения' : 'Добавить профиль' }}
        </button>
        <p v-if="feedbackLocation === 'editor' && message" class="success" role="status">{{ message }}</p>
        <p v-if="feedbackLocation === 'editor' && error" class="error" role="alert">{{ error }}</p>
      </form>
    </template>
  </section>
</template>

<style scoped>
.profiles-card { display: grid; gap: var(--space-4); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); grid-column: 1 / -1; }
.profiles-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.profiles-heading h2, .create-form h3 { margin: 0; }
.status, .profile-row p { margin: 0; color: var(--color-text-muted); }
.profile-title { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.active-badge, .inline-active, .state-badge { border-radius: var(--radius-full); padding: var(--space-1) var(--space-2); font-size: 12px; font-weight: 700; }
.active-badge, .inline-active, .state-badge--enabled { background: var(--color-success-surface); color: var(--color-success-text); }
.state-badge--disabled { background: var(--color-surface-subtle); color: var(--color-text-muted); }
.selector-row { display: grid; grid-template-columns: auto minmax(240px, 1fr) auto; gap: var(--space-3); align-items: end; }
.selector-row label { align-self: center; }
select, input, textarea { border: 1px solid var(--color-border-control); border-radius: var(--radius-md); font: inherit; background: var(--color-surface); color: var(--color-text); }
select, input { min-height: 42px; padding: 0 var(--space-3); }
textarea { width: 100%; padding: var(--space-3); resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.profile-list { display: grid; gap: var(--space-2); }
.profile-row { display: flex; justify-content: space-between; gap: var(--space-4); align-items: center; padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.profile-row small { color: var(--color-text-muted); }
.profile-row .profile-hint { margin-top: var(--space-2); font-size: 13px; color: var(--color-text-muted); }
.row-actions { display: flex; gap: var(--space-2); }
.create-form { display: grid; gap: var(--space-3); padding-top: var(--space-4); border-top: 1px solid var(--color-border); }
.editor-heading { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3); }
.form-grid label, .ca-field { display: grid; gap: var(--space-2); }
.ca-field small { color: var(--color-text-muted); }
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

.spinner-inline {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin-right: var(--space-2);
  vertical-align: middle;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
