<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const busy = ref(false)
const errorMessage = ref('')
const canSubmit = computed(
  () => !busy.value && currentPassword.value.length > 0 &&
    newPassword.value.length >= 12 && newPassword.value === confirmPassword.value,
)

async function submit(): Promise<void> {
  errorMessage.value = ''
  if (!canSubmit.value) {
    errorMessage.value = 'Новый пароль должен содержать не менее 12 символов и совпадать с подтверждением.'
    return
  }
  busy.value = true
  try {
    if (await auth.changePassword(currentPassword.value, newPassword.value)) {
      await router.replace('/login')
    } else {
      errorMessage.value = 'Не удалось сменить пароль. Проверьте текущий пароль и попробуйте снова.'
    }
  } finally {
    busy.value = false
  }
}

async function signOut(): Promise<void> {
  auth.logout()
  await router.replace('/login')
}
</script>

<template>
  <main class="change-page">
    <section class="change-card" aria-labelledby="change-title">
      <p class="eyebrow">Harbor Transfer Portal</p>
      <h1 id="change-title">Обязательная смена пароля</h1>
      <p>Это временная учётная запись. До смены пароля остальные разделы портала недоступны.</p>
      <form class="change-form" @submit.prevent="submit">
        <label for="current-password">Текущий пароль</label>
        <input id="current-password" v-model="currentPassword" type="password" autocomplete="current-password" required />
        <label for="new-password">Новый пароль (не менее 12 символов)</label>
        <input id="new-password" v-model="newPassword" type="password" autocomplete="new-password" minlength="12" required />
        <label for="confirm-password">Подтверждение нового пароля</label>
        <input id="confirm-password" v-model="confirmPassword" type="password" autocomplete="new-password" required />
        <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
        <button type="submit" :disabled="!canSubmit">Сменить пароль и войти заново</button>
      </form>
      <button class="signout" type="button" @click="signOut">Выйти</button>
    </section>
  </main>
</template>

<style scoped>
.change-page { min-height: 100vh; display: grid; place-items: center; padding: var(--space-6); background: var(--color-surface-subtle); }
.change-card { width: min(100%, 460px); padding: var(--space-8); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-md); }
.change-card h1 { margin: var(--space-2) 0 var(--space-4); color: var(--color-text); }
.change-card p { color: var(--color-text-muted); }
.eyebrow { font-size: 12px; font-weight: 700; text-transform: uppercase; color: var(--color-action); }
.change-form { display: grid; gap: var(--space-3); margin-top: var(--space-6); }
.change-form label { font-weight: 600; }
.change-form input { min-height: 44px; box-sizing: border-box; width: 100%; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); }
.change-form button { min-height: 44px; border: 0; border-radius: var(--radius-md); background: var(--color-action-surface); color: var(--color-on-accent); font-weight: 700; cursor: pointer; }
.change-form button:disabled { opacity: .55; cursor: not-allowed; }
.form-error { color: var(--color-danger-text) !important; }
.signout { border: none; margin-top: var(--space-5); background: transparent; color: var(--color-action); cursor: pointer; text-decoration: underline; }
</style>
