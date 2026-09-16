<script setup lang="ts">
import axios from 'axios'
import { onMounted, reactive, ref } from 'vue'

import {
  createUser,
  listUsers,
  updateUser,
  type ManagedUser,
  type UpdateUserPayload,
} from '@/api/users'
import type { UserRole } from '@/stores/auth'

const users = ref<ManagedUser[]>([])
const loading = ref(true)
const creating = ref(false)
const busyUserId = ref<number | null>(null)
const error = ref('')
const message = ref('')

const newUsername = ref('')
const newPassword = ref('')
const newRole = ref<UserRole>('viewer')
const roleDrafts = reactive<Record<number, UserRole>>({})
const activeDrafts = reactive<Record<number, boolean>>({})
const passwordDrafts = reactive<Record<number, string>>({})

function safeError(fallback: string, value: unknown): string {
  if (axios.isAxiosError(value)) {
    const apiMessage = value.response?.data?.error?.message
    if (typeof apiMessage === 'string') return apiMessage
  }
  return fallback
}

function formatDate(value: string | null): string {
  if (!value) return 'Никогда'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('ru-RU')
}

function syncDraft(user: ManagedUser): void {
  roleDrafts[user.id] = user.role
  activeDrafts[user.id] = user.is_active
  passwordDrafts[user.id] = ''
}

function replaceUser(updated: ManagedUser): void {
  const index = users.value.findIndex((item) => item.id === updated.id)
  if (index >= 0) users.value[index] = updated
  else users.value.push(updated)
  users.value.sort((left, right) => left.username.localeCompare(right.username))
  syncDraft(updated)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    users.value = await listUsers()
    for (const user of users.value) syncDraft(user)
  } catch (reason) {
    error.value = safeError('Не удалось загрузить пользователей.', reason)
  } finally {
    loading.value = false
  }
}

async function submitNewUser(): Promise<void> {
  const username = newUsername.value.trim()
  if (!username) {
    error.value = 'Введите имя пользователя.'
    return
  }
  if (newPassword.value.length < 12) {
    error.value = 'Начальный пароль должен содержать не менее 12 символов.'
    return
  }

  creating.value = true
  error.value = ''
  message.value = ''
  try {
    const created = await createUser({
      username,
      password: newPassword.value,
      role: newRole.value,
    })
    replaceUser(created)
    newUsername.value = ''
    newPassword.value = ''
    newRole.value = 'viewer'
    message.value = `Пользователь ${created.username} создан.`
  } catch (reason) {
    error.value = safeError('Не удалось создать пользователя.', reason)
  } finally {
    creating.value = false
  }
}

async function saveAccess(user: ManagedUser): Promise<void> {
  const role = roleDrafts[user.id]
  const isActive = activeDrafts[user.id]
  const payload: UpdateUserPayload = {}
  if (role !== user.role) payload.role = role
  if (isActive !== user.is_active) payload.is_active = isActive

  if (Object.keys(payload).length === 0) {
    message.value = `Для ${user.username} нет изменений.`
    error.value = ''
    return
  }

  const action = payload.is_active === false ? 'отключить пользователя' : 'изменить роль/статус'
  if (!window.confirm(`Подтвердите: ${action} ${user.username}.`)) {
    syncDraft(user)
    return
  }

  busyUserId.value = user.id
  error.value = ''
  message.value = ''
  try {
    const updated = await updateUser(user.id, payload)
    replaceUser(updated)
    message.value = `Доступ пользователя ${updated.username} обновлён.`
  } catch (reason) {
    syncDraft(user)
    error.value = safeError('Не удалось изменить доступ пользователя.', reason)
  } finally {
    busyUserId.value = null
  }
}

async function resetPassword(user: ManagedUser): Promise<void> {
  const password = passwordDrafts[user.id] ?? ''
  if (password.length < 12) {
    error.value = 'Новый пароль должен содержать не менее 12 символов.'
    return
  }
  if (!window.confirm(`Подтвердите смену пароля для ${user.username}.`)) return

  busyUserId.value = user.id
  error.value = ''
  message.value = ''
  try {
    const updated = await updateUser(user.id, { password })
    replaceUser(updated)
    message.value = `Пароль пользователя ${updated.username} изменён.`
  } catch (reason) {
    error.value = safeError('Не удалось изменить пароль пользователя.', reason)
  } finally {
    busyUserId.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="users" aria-labelledby="users-title">
    <header class="users__header">
      <div>
        <h1 id="users-title">Пользователи</h1>
        <p>Локальные учётные записи этого экземпляра Portal. Все изменения доступны только admin.</p>
      </div>
      <button type="button" class="secondary" :disabled="loading" @click="load">
        Обновить список
      </button>
    </header>

    <section class="card" aria-labelledby="create-user-title">
      <h2 id="create-user-title">Создать пользователя</h2>
      <form class="create-grid" @submit.prevent="submitNewUser">
        <label>
          Имя пользователя
          <input v-model="newUsername" name="username" autocomplete="off" maxlength="128" />
        </label>
        <label>
          Начальный пароль
          <input
            v-model="newPassword"
            name="password"
            type="password"
            autocomplete="new-password"
            minlength="12"
          />
        </label>
        <label>
          Роль
          <select v-model="newRole" name="role">
            <option value="viewer">viewer</option>
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit" :disabled="creating">
          {{ creating ? 'Создание…' : 'Создать пользователя' }}
        </button>
      </form>
      <p class="hint">Пароль не отображается и не возвращается API после сохранения.</p>
    </section>

    <p v-if="message" class="success" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading">Загрузка пользователей…</p>

    <section v-else class="card" aria-labelledby="user-list-title">
      <div class="section-heading">
        <h2 id="user-list-title">Локальные пользователи</h2>
        <span>{{ users.length }}</span>
      </div>
      <p v-if="users.length === 0" class="empty">Пользователи ещё не созданы.</p>

      <article v-for="user in users" :key="user.id" class="user-row">
        <div class="identity">
          <strong>{{ user.username }}</strong>
          <span>ID {{ user.id }}</span>
          <span>Создан: {{ formatDate(user.created_at) }}</span>
          <span>Последний вход: {{ formatDate(user.last_login_at) }}</span>
        </div>

        <label>
          Роль
          <select v-model="roleDrafts[user.id]" :aria-label="`Роль ${user.username}`">
            <option value="viewer">viewer</option>
            <option value="operator">operator</option>
            <option value="admin">admin</option>
          </select>
        </label>

        <label class="active-toggle">
          <input v-model="activeDrafts[user.id]" type="checkbox" />
          Активен
        </label>

        <button
          type="button"
          :disabled="busyUserId === user.id"
          @click="saveAccess(user)"
        >
          Сохранить доступ
        </button>

        <div class="password-reset">
          <label>
            Новый пароль
            <input
              v-model="passwordDrafts[user.id]"
              type="password"
              autocomplete="new-password"
              minlength="12"
              :aria-label="`Новый пароль ${user.username}`"
            />
          </label>
          <button
            type="button"
            class="secondary"
            :disabled="busyUserId === user.id"
            @click="resetPassword(user)"
          >
            Сменить пароль
          </button>
        </div>
      </article>
    </section>
  </section>
</template>

<style scoped>
.users { display: grid; gap: var(--space-6); }
.users__header { display: flex; justify-content: space-between; gap: var(--space-4); align-items: flex-start; }
.users__header h1, .card h2 { margin: 0; }
.users__header p { margin: var(--space-2) 0 0; color: var(--color-text-muted); }
.card { display: grid; gap: var(--space-4); padding: var(--space-5); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); }
.create-grid { display: grid; grid-template-columns: 1.2fr 1.2fr .8fr auto; gap: var(--space-3); align-items: end; }
label { display: grid; gap: var(--space-2); color: var(--color-text-muted); font-size: 14px; }
input, select, button { min-height: 42px; border-radius: var(--radius-md); font: inherit; }
input, select { border: 1px solid var(--color-border-control); background: var(--color-surface); padding: 0 var(--space-3); color: var(--color-text); }
button { border: 0; padding: 0 var(--space-4); background: var(--color-bridge-blue); color: white; cursor: pointer; }
button.secondary { border: 1px solid var(--color-border-control); background: var(--color-surface); color: var(--color-text); }
button:disabled { opacity: .6; cursor: wait; }
.section-heading { display: flex; justify-content: space-between; align-items: center; }
.user-row { display: grid; grid-template-columns: minmax(180px, 1.2fr) minmax(120px, .6fr) auto auto minmax(260px, 1fr); gap: var(--space-3); align-items: end; padding-top: var(--space-4); border-top: 1px solid var(--color-border); }
.identity { display: grid; gap: var(--space-1); }
.identity span, .hint, .empty { color: var(--color-text-muted); font-size: 13px; }
.active-toggle { display: flex; align-items: center; gap: var(--space-2); min-height: 42px; color: var(--color-text); }
.active-toggle input { min-height: auto; }
.password-reset { display: grid; grid-template-columns: minmax(150px, 1fr) auto; gap: var(--space-2); align-items: end; }
.success, .error { margin: 0; padding: var(--space-3); border-radius: var(--radius-md); }
.success { border: 1px solid var(--color-success-text); color: var(--color-success-text); }
.error { border: 1px solid var(--color-danger-text); color: var(--color-danger-text); }
@media (max-width: 980px) {
  .create-grid, .user-row { grid-template-columns: 1fr 1fr; }
  .identity, .password-reset { grid-column: 1 / -1; }
}
@media (max-width: 640px) {
  .users__header, .create-grid, .user-row, .password-reset { grid-template-columns: 1fr; }
  .users__header { display: grid; }
  .identity, .password-reset { grid-column: auto; }
}
</style>