<script setup lang="ts">
import axios from 'axios'
import { KeyRound, PlugZap, Save, ShieldAlert, Trash2, Upload } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'

import {
  clearHarborCa,
  fetchHarborSettings,
  installHarborCa,
  rotateHarborCredential,
  testHarborConnection,
  updateHarborSettings,
  type HarborSettings,
} from '@/api/settings'
import ContourBadge from '@/components/ContourBadge.vue'

const config = ref<HarborSettings | null>(null)
const url = ref('')
const username = ref('')
const verifyTls = ref(true)
const credential = ref('')
const caFileName = ref('')
const caPem = ref('')

const loading = ref(true)
const saving = ref(false)
const rotating = ref(false)
const caLoading = ref(false)
const testing = ref(false)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)

function errorText(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const message = error.response?.data?.error?.message
    if (typeof message === 'string' && message.length > 0) {
      return message
    }
  }
  return fallback
}

function applyConfig(value: HarborSettings): void {
  config.value = value
  url.value = value.url ?? ''
  username.value = value.username ?? ''
  verifyTls.value = value.verify_tls
}

function resetMessages(): void {
  errorMessage.value = null
  successMessage.value = null
}

async function load(): Promise<void> {
  loading.value = true
  resetMessages()
  try {
    applyConfig(await fetchHarborSettings())
  } catch (error) {
    errorMessage.value = errorText(error, 'Не удалось загрузить настройки локального Harbor.')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  saving.value = true
  resetMessages()
  try {
    applyConfig(
      await updateHarborSettings({
        url: url.value.trim() || null,
        username: username.value.trim() || null,
        verify_tls: verifyTls.value,
      }),
    )
    successMessage.value = 'Настройки подключения сохранены.'
  } catch (error) {
    errorMessage.value = errorText(error, 'Не удалось сохранить настройки Harbor.')
  } finally {
    saving.value = false
  }
}

async function rotateCredential(): Promise<void> {
  if (!credential.value) {
    errorMessage.value = 'Введите новый пароль или token.'
    return
  }
  rotating.value = true
  resetMessages()
  try {
    applyConfig(await rotateHarborCredential(credential.value))
    credential.value = ''
    successMessage.value = 'Учётные данные Harbor обновлены.'
  } catch (error) {
    errorMessage.value = errorText(error, 'Не удалось обновить учётные данные Harbor.')
  } finally {
    rotating.value = false
  }
}

async function selectCa(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    caFileName.value = ''
    caPem.value = ''
    return
  }
  caFileName.value = file.name
  caPem.value = await file.text()
}

async function uploadCa(): Promise<void> {
  if (!caPem.value) {
    errorMessage.value = 'Выберите PEM/CRT файл доверенного CA.'
    return
  }
  caLoading.value = true
  resetMessages()
  try {
    applyConfig(await installHarborCa(caPem.value))
    caFileName.value = ''
    caPem.value = ''
    successMessage.value = 'Доверенный CA Harbor установлен.'
  } catch (error) {
    errorMessage.value = errorText(error, 'Не удалось установить CA Harbor.')
  } finally {
    caLoading.value = false
  }
}

async function clearCa(): Promise<void> {
  caLoading.value = true
  resetMessages()
  try {
    applyConfig(await clearHarborCa())
    successMessage.value = 'Runtime CA Harbor удалён.'
  } catch (error) {
    errorMessage.value = errorText(error, 'Не удалось удалить CA Harbor.')
  } finally {
    caLoading.value = false
  }
}

async function testConnection(): Promise<void> {
  testing.value = true
  resetMessages()
  try {
    const result = await testHarborConnection()
    const version = result.version ? ` ${result.version}` : ''
    successMessage.value = `Подключение к Harbor успешно${version}.`
  } catch (error) {
    errorMessage.value = errorText(error, 'Проверка подключения к Harbor завершилась ошибкой.')
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="settings-page" aria-labelledby="settings-title">
    <header class="settings-heading">
      <div>
        <p class="eyebrow">Администрирование</p>
        <h1 id="settings-title">Локальный Harbor</h1>
        <p class="settings-intro">
          Эта установка работает только с Harbor своего изолированного контура. Данные другого
          контура здесь не настраиваются и не хранятся.
        </p>
      </div>
      <ContourBadge v-if="config" :contour="config.contour" />
    </header>

    <p v-if="loading" class="status-card" aria-live="polite">Загрузка настроек…</p>
    <p v-else-if="!config" class="status-card status-card--error" role="alert">
      {{ errorMessage ?? 'Настройки недоступны.' }}
      <button type="button" class="text-button" @click="load">Повторить</button>
    </p>

    <template v-else>
      <p v-if="errorMessage" class="status-card status-card--error" role="alert">
        {{ errorMessage }}
      </p>
      <p v-if="successMessage" class="status-card status-card--success" role="status">
        {{ successMessage }}
      </p>

      <form class="settings-card" @submit.prevent="save">
        <div class="card-heading">
          <div>
            <h2>Подключение</h2>
            <p>URL, service account и политика TLS для локального Harbor.</p>
          </div>
          <button class="primary-button" type="submit" :disabled="saving">
            <Save :size="17" aria-hidden="true" />
            {{ saving ? 'Сохранение…' : 'Сохранить' }}
          </button>
        </div>

        <div class="form-grid">
          <label>
            <span>URL Harbor</span>
            <input v-model="url" type="url" placeholder="https://harbor.local" maxlength="2048" />
          </label>
          <label>
            <span>Имя пользователя / service account</span>
            <input v-model="username" type="text" autocomplete="off" maxlength="256" />
          </label>
        </div>

        <label class="check-row">
          <input v-model="verifyTls" type="checkbox" />
          <span>Проверять TLS-сертификат Harbor</span>
        </label>
        <div v-if="!verifyTls" class="warning-card" role="alert">
          <ShieldAlert :size="20" aria-hidden="true" />
          <span>
            TLS verification отключена явно. Используйте это только как временное исключение;
            предпочтительно установить доверенный CA.
          </span>
        </div>

        <button class="secondary-button" type="button" :disabled="testing" @click="testConnection">
          <PlugZap :size="17" aria-hidden="true" />
          {{ testing ? 'Проверка…' : 'Проверить подключение' }}
        </button>
      </form>

      <section class="settings-card" aria-labelledby="credential-title">
        <div class="card-heading">
          <div>
            <h2 id="credential-title">Учётные данные</h2>
            <p>
              Текущее значение никогда не возвращается в браузер. Статус:
              <strong>{{ config.credential_configured ? 'настроено' : 'не настроено' }}</strong>.
            </p>
          </div>
        </div>
        <div class="inline-action">
          <label class="grow-field">
            <span>Новый пароль или token</span>
            <input
              v-model="credential"
              type="password"
              autocomplete="new-password"
              maxlength="4096"
              placeholder="Введите только для ротации"
            />
          </label>
          <button class="secondary-button" type="button" :disabled="rotating" @click="rotateCredential">
            <KeyRound :size="17" aria-hidden="true" />
            {{ rotating ? 'Обновление…' : 'Ротировать credential' }}
          </button>
        </div>
      </section>

      <section class="settings-card" aria-labelledby="ca-title">
        <div class="card-heading">
          <div>
            <h2 id="ca-title">Доверенный CA</h2>
            <p>
              {{ config.custom_ca_configured ? 'CA настроен' : 'Дополнительный CA не настроен' }}
              <template v-if="config.custom_ca_source">({{ config.custom_ca_source }})</template>.
            </p>
          </div>
          <button
            v-if="config.custom_ca_source === 'runtime'"
            class="danger-button"
            type="button"
            :disabled="caLoading"
            @click="clearCa"
          >
            <Trash2 :size="17" aria-hidden="true" />
            Удалить runtime CA
          </button>
        </div>

        <div class="inline-action">
          <label class="grow-field">
            <span>PEM/CRT файл</span>
            <input type="file" accept=".pem,.crt,application/x-pem-file" @change="selectCa" />
            <small v-if="caFileName">Выбран: {{ caFileName }}</small>
          </label>
          <button class="secondary-button" type="button" :disabled="caLoading" @click="uploadCa">
            <Upload :size="17" aria-hidden="true" />
            {{ caLoading ? 'Сохранение…' : 'Установить CA' }}
          </button>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.settings-page { display: grid; gap: var(--space-5); max-width: 1040px; margin: 0 auto; }
.settings-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-5); }
.settings-heading h1 { margin: var(--space-2) 0; color: var(--color-deep-harbor); }
.settings-intro { max-width: 720px; margin: 0; color: var(--color-steel); }
.eyebrow { margin: 0; color: var(--color-bridge-blue); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.settings-card, .status-card { border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: var(--color-cloud-white); padding: var(--space-6); box-shadow: var(--shadow-sm); }
.settings-card { display: grid; gap: var(--space-5); }
.card-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); }
.card-heading h2 { margin: 0 0 var(--space-2); color: var(--color-deep-harbor); }
.card-heading p { margin: 0; color: var(--color-steel); }
.form-grid { display: grid; grid-template-columns: 1.4fr 1fr; gap: var(--space-4); }
.form-grid label, .grow-field { display: grid; gap: var(--space-2); color: var(--color-deep-harbor); font-weight: 600; }
input[type='text'], input[type='url'], input[type='password'], input[type='file'] { width: 100%; min-height: 42px; box-sizing: border-box; border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-3); background: white; color: var(--color-deep-harbor); }
input[type='file'] { padding-top: 9px; }
.check-row { display: flex; align-items: center; gap: var(--space-2); font-weight: 600; color: var(--color-deep-harbor); }
.check-row input { width: 18px; height: 18px; }
.warning-card { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border-radius: var(--radius-md); background: color-mix(in srgb, var(--color-alert-amber) 14%, white); color: var(--color-deep-harbor); }
.inline-action { display: flex; align-items: end; gap: var(--space-4); }
.grow-field { flex: 1; }
.grow-field small { color: var(--color-steel); font-weight: 400; }
.primary-button, .secondary-button, .danger-button, .text-button { min-height: 40px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); border-radius: var(--radius-md); padding: 0 var(--space-4); font-weight: 700; cursor: pointer; }
.primary-button { border: 0; background: var(--color-bridge-blue); color: white; }
.secondary-button { border: 1px solid var(--color-mist); background: white; color: var(--color-deep-harbor); }
.danger-button { border: 1px solid var(--color-stop-red); background: white; color: var(--color-stop-red); }
.text-button { min-height: 30px; margin-left: var(--space-2); border: 0; background: transparent; color: var(--color-bridge-blue); }
button:disabled { cursor: not-allowed; opacity: .55; }
.status-card { margin: 0; }
.status-card--error { border-color: color-mix(in srgb, var(--color-stop-red) 45%, var(--color-mist)); color: var(--color-stop-red); }
.status-card--success { border-color: color-mix(in srgb, var(--color-transfer-green) 45%, var(--color-mist)); color: var(--color-deep-harbor); }
input:focus-visible, button:focus-visible { outline: 3px solid color-mix(in srgb, var(--color-bridge-blue) 30%, transparent); outline-offset: 2px; }
@media (max-width: 760px) {
  .settings-heading, .card-heading, .inline-action { flex-direction: column; align-items: stretch; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
