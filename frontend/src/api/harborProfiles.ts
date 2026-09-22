import { apiClient } from '@/api/client'

export type HarborProfile = {
  id: string
  name: string
  url: string | null
  username: string | null
  verify_tls: boolean
  enabled: boolean
  credential_configured: boolean
  custom_ca_configured: boolean
  legacy_default: boolean
}

export type HarborProfileCreate = {
  name: string
  url: string
  username: string | null
  verify_tls: boolean
}

export type HarborProfilePatch = Partial<{
  name: string
  url: string
  username: string | null
  verify_tls: boolean
  enabled: boolean
}>

export type HarborConnectionTest = {
  ok: boolean
  code: string
  message: string
  version: string | null
}

export async function listHarborProfiles(): Promise<HarborProfile[]> {
  const response = await apiClient.get<{ items: HarborProfile[] }>('/settings/harbor/profiles')
  return response.data.items
}

export async function createHarborProfile(
  payload: HarborProfileCreate,
): Promise<HarborProfile> {
  const response = await apiClient.post<HarborProfile>('/settings/harbor/profiles', payload)
  return response.data
}

export async function updateHarborProfile(
  profileId: string,
  payload: HarborProfilePatch,
): Promise<HarborProfile> {
  const response = await apiClient.patch<HarborProfile>(
    `/settings/harbor/profiles/${encodeURIComponent(profileId)}`,
    payload,
  )
  return response.data
}

export async function deleteHarborProfile(profileId: string): Promise<void> {
  await apiClient.delete(`/settings/harbor/profiles/${encodeURIComponent(profileId)}`)
}

export async function rotateHarborProfileCredential(
  profileId: string,
  secret: string,
): Promise<void> {
  await apiClient.put(
    `/settings/harbor/profiles/${encodeURIComponent(profileId)}/credential`,
    { secret },
  )
}

export async function installHarborProfileCa(
  profileId: string,
  certificatePem: string,
): Promise<void> {
  await apiClient.put(
    `/settings/harbor/profiles/${encodeURIComponent(profileId)}/ca`,
    { certificate_pem: certificatePem },
  )
}

export async function removeHarborProfileCa(profileId: string): Promise<void> {
  await apiClient.delete(
    `/settings/harbor/profiles/${encodeURIComponent(profileId)}/ca`,
  )
}

export async function testHarborProfile(profileId: string): Promise<HarborConnectionTest> {
  const response = await apiClient.post<HarborConnectionTest>(
    `/settings/harbor/profiles/${encodeURIComponent(profileId)}/test`,
  )
  return response.data
}
