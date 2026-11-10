import axios, { AxiosHeaders } from 'axios'

const ACCESS_TOKEN_STORAGE_KEY = 'htp.access-token'

type UnauthorizedHandler = (() => void) | null
let unauthorizedHandler: UnauthorizedHandler = null

function sessionStorageOrNull(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

export function readAccessToken(): string | null {
  return sessionStorageOrNull()?.getItem(ACCESS_TOKEN_STORAGE_KEY) ?? null
}

export function storeAccessToken(token: string): void {
  sessionStorageOrNull()?.setItem(ACCESS_TOKEN_STORAGE_KEY, token)
}

export function clearAccessToken(): void {
  sessionStorageOrNull()?.removeItem(ACCESS_TOKEN_STORAGE_KEY)
}

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 15_000,
  headers: {
    Accept: 'application/json',
  },
})

apiClient.interceptors.request.use((config) => {
  const token = readAccessToken()
  if (token) {
    const headers = AxiosHeaders.from(config.headers)
    headers.set('Authorization', `Bearer ${token}`)
    config.headers = headers
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    // Never resurrect a logged-out session or overwrite a newer token when
    // a delayed refresh response belongs to a previous authenticated request.
    const newToken = response.headers['x-new-access-token']
    const sentAuthorization = AxiosHeaders.from(response.config.headers).get('Authorization')
    const currentToken = readAccessToken()
    if (
      typeof newToken === 'string' &&
      currentToken &&
      sentAuthorization === `Bearer ${currentToken}`
    ) {
      storeAccessToken(newToken)
    }
    return response
  },
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      // In-flight requests can outlive a renewal or a subsequent login.
      // Only a 401 for the *current* bearer may invalidate the session.
      // A late 401 from an older token (or tokenless login) must not log
      // out an already refreshed or newly authenticated user.
      const currentToken = readAccessToken()
      const sentAuthorization = error.config
        ? AxiosHeaders.from(error.config.headers).get('Authorization')
        : null
      if (currentToken && sentAuthorization === `Bearer ${currentToken}`) {
        clearAccessToken()
        unauthorizedHandler?.()
      }
    }
    return Promise.reject(error)
  },
)
