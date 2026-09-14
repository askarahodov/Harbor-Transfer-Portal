import { apiClient } from '@/api/client'
import type { UserRole } from '@/stores/auth'

export type ManagedUser = {
  id: number
  username: string
  role: UserRole
  is_active: boolean
  created_at: string
  updated_at: string
  last_login_at: string | null
}

export type CreateUserPayload = {
  username: string
  password: string
  role: UserRole
}

export type UpdateUserPayload = {
  role?: UserRole
  is_active?: boolean
  password?: string
}

export async function listUsers(): Promise<ManagedUser[]> {
  const response = await apiClient.get<ManagedUser[]>('/users')
  return response.data
}

export async function createUser(payload: CreateUserPayload): Promise<ManagedUser> {
  const response = await apiClient.post<ManagedUser>('/users', payload)
  return response.data
}

export async function updateUser(
  userId: number,
  payload: UpdateUserPayload,
): Promise<ManagedUser> {
  const response = await apiClient.patch<ManagedUser>(`/users/${userId}`, payload)
  return response.data
}
