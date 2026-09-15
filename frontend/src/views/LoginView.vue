<script setup lang="ts">
import { Eye, EyeOff, LogIn } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ContourBadge from '@/components/ContourBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

const auth = useAuthStore()
const runtime = useRuntimeStore()
const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const showPassword = ref(false)
const errorMessage = ref<string | null>(null)

const canSubmit = computed(
  () => username.value.trim().length > 0 && password.value.length > 0 && !auth.loading,
)

function redirectAfterLogin(): string {
  const requested = route.query.redirect
  if (
    typeof requested === 'string' &&
    requested.startsWith('/') &&
    !requested.startsWith('//') &&
    requested !== '/login'
  ) {
    return requested
  }
  return '/'
}

async function submit(): Promise<void> {
  errorMessage.value = null
  if (!username.value.trim() || !password.value) {
    errorMessage.value = 'Введите имя пользователя и пароль.'
    return
  }

  const authenticated = await auth.login(username.value, password.value)
  if (authenticated) {
    await router.replace(redirectAfterLogin())
    return
  }

  errorMessage.value =
    auth.loginErrorCode === 'invalid_credentials'
      ? 'Неверное имя пользователя или пароль.'
      : 'Не удалось выполнить вход. Проверьте доступность портала и повторите попытку.'
}
</script>

<template>
  <main class="login-page">
    <section class="login-card" aria-labelledby="login-title">
      <div class="login-card__heading">
        <div>
          <p class="eyebrow">Harbor Transfer Portal</p>
          <h1 id="login-title">Вход в портал</h1>
        </div>
        <ContourBadge :contour="runtime.contour" />
      </div>

      <p class="login-card__hint">
        Используйте локальную учётную запись этого изолированного контура.
      </p>

      <form class="login-form" novalidate @submit.prevent="submit">
        <label for="username">Имя пользователя</label>
        <input
          id="username"
          v-model="username"
          name="username"
          type="text"
          autocomplete="username"
          maxlength="128"
          :disabled="auth.loading"
          autofocus
        />

        <label for="password">Пароль</label>
        <div class="password-field">
          <input
            id="password"
            v-model="password"
            name="password"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            maxlength="4096"
            :disabled="auth.loading"
          />
          <button
            class="password-toggle"
            type="button"
            :aria-pressed="showPassword"
            :aria-label="showPassword ? 'Скрыть пароль' : 'Показать пароль'"
            :disabled="auth.loading"
            @click="showPassword = !showPassword"
          >
            <EyeOff v-if="showPassword" :size="18" aria-hidden="true" />
            <Eye v-else :size="18" aria-hidden="true" />
          </button>
        </div>

        <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>

        <button class="submit-button" type="submit" :disabled="!canSubmit">
          <LogIn :size="18" aria-hidden="true" />
          <span>{{ auth.loading ? 'Выполняется вход…' : 'Войти' }}</span>
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page { min-height: 100vh; display: grid; place-items: center; padding: var(--space-6); background: var(--color-fog-gray); }
.login-card { width: min(100%, 460px); padding: var(--space-8); border: 1px solid var(--color-mist); border-radius: var(--radius-lg); background: var(--color-cloud-white); box-shadow: var(--shadow-md); }
.login-card__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
.login-card h1 { margin: var(--space-2) 0 0; color: var(--color-deep-harbor); }
.eyebrow { margin: 0; color: var(--color-bridge-blue); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.login-card__hint { margin: var(--space-4) 0 var(--space-6); color: var(--color-steel); }
.login-form { display: grid; gap: var(--space-3); }
.login-form label { font-weight: 600; color: var(--color-deep-harbor); }
.login-form input { width: 100%; min-height: 44px; box-sizing: border-box; border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-3); background: white; color: var(--color-deep-harbor); }
.login-form input:focus-visible, .password-toggle:focus-visible, .submit-button:focus-visible { outline: 3px solid color-mix(in srgb, var(--color-bridge-blue) 30%, transparent); outline-offset: 2px; }
.password-field { position: relative; }
.password-field input { padding-right: 48px; }
.password-toggle { position: absolute; top: 2px; right: 2px; width: 40px; height: 40px; display: grid; place-items: center; border: 0; background: transparent; color: var(--color-steel); cursor: pointer; }
.form-error { margin: 0; color: var(--color-danger-text); }
.submit-button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); margin-top: var(--space-2); border: 0; border-radius: var(--radius-md); background: var(--color-bridge-blue); color: white; font-weight: 700; cursor: pointer; }
.submit-button:disabled, .password-toggle:disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 520px) {
  .login-page { padding: var(--space-4); }
  .login-card { padding: var(--space-6); }
  .login-card__heading { flex-direction: column; }
}
</style>
