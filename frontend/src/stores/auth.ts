import axios from 'axios'
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  apiClient,
  clearAccessToken,
  readAccessToken,
  setUnauthorizedHandler,
  storeAccessToken,
} from '@/api/client'

export type UserRole = 'admin' | 'operator' | 'viewer'

export type CurrentUser = {
  id: number
  username: string
  role: UserRole
  is_active: boolean
}

type LoginResponse = {
  access_token: string
  token_type: string
}

function isUserRole(value: unknown): value is UserRole {
  return value === 'admin' || value === 'operator' || value === 'viewer'
}

function parseCurrentUser(value: unknown): CurrentUser {
  if (typeof value !== 'object' || value === null) {
    throw new Error('Invalid current user response')
  }
  const candidate = value as Record<string, unknown>
  if (
    typeof candidate.id !== 'number' ||
    typeof candidate.username !== 'string' ||
    !isUserRole(candidate.role) ||
    typeof candidate.is_active !== 'boolean'
  ) {
    throw new Error('Invalid current user response')
  }
  return {
    id: candidate.id,
    username: candidate.username,
    role: candidate.role,
    is_active: candidate.is_active,
  }
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)
  const loading = ref(false)
  const loginErrorCode = ref<string | null>(null)

  const isAuthenticated = computed(() => user.value !== null)
  const canStartTransfers = computed(
    () => user.value?.role === 'admin' || user.value?.role === 'operator',
  )
  const canManageSettings = computed(() => user.value?.role === 'admin')

  function resetSession(): void {
    clearAccessToken()
    user.value = null
    initialized.value = true
  }

  setUnauthorizedHandler(resetSession)

  async function bootstrapSession(): Promise<void> {
    if (initialized.value) return
    if (!readAccessToken()) {
      initialized.value = true
      return
    }

    try {
      const response = await apiClient.get<unknown>('/auth/me')
      user.value = parseCurrentUser(response.data)
    } catch {
      resetSession()
    } finally {
      initialized.value = true
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    loading.value = true
    loginErrorCode.value = null
    clearAccessToken()
    user.value = null

    try {
      const response = await apiClient.post<LoginResponse>('/auth/login', { username, password })
      storeAccessToken(response.data.access_token)
      const currentUserResponse = await apiClient.get<unknown>('/auth/me')
      user.value = parseCurrentUser(currentUserResponse.data)
      initialized.value = true
      return true
    } catch (error: unknown) {
      resetSession()
      loginErrorCode.value =
        axios.isAxiosError(error) && error.response?.status === 401
          ? 'invalid_credentials'
          : 'authentication_unavailable'
      return false
    } finally {
      loading.value = false
    }
  }

  function logout(): void {
    loginErrorCode.value = null
    resetSession()
  }

  return {
    user,
    initialized,
    loading,
    loginErrorCode,
    isAuthenticated,
    canStartTransfers,
    canManageSettings,
    bootstrapSession,
    login,
    logout,
  }
})
