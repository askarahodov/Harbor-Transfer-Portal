import { apiClient } from './client'

export interface HarborSettings {
  contour: 'SOURCE' | 'TARGET'
  url: string | null
  username: string | null
  verify_tls: boolean
  credential_configured: boolean
  custom_ca_configured: boolean
  custom_ca_source: 'runtime' | 'bootstrap' | null
}

export interface HarborConnectionTest {
  connected: boolean
  version: string | null
  auth_mode: string | null
}

export async function fetchHarborSettings(): Promise<HarborSettings> {
  const response = await apiClient.get<HarborSettings>('/settings/harbor')
  return response.data
}

export async function updateHarborSettings(payload: {
  url: string | null
  username: string | null
  verify_tls: boolean
}): Promise<HarborSettings> {
  const response = await apiClient.patch<HarborSettings>('/settings/harbor', payload)
  return response.data
}

export async function rotateHarborCredential(secret: string): Promise<HarborSettings> {
  const response = await apiClient.put<HarborSettings>('/settings/harbor/credential', { secret })
  return response.data
}

export async function installHarborCa(certificatePem: string): Promise<HarborSettings> {
  const response = await apiClient.put<HarborSettings>('/settings/harbor/ca', {
    certificate_pem: certificatePem,
  })
  return response.data
}

export async function clearHarborCa(): Promise<HarborSettings> {
  const response = await apiClient.delete<HarborSettings>('/settings/harbor/ca')
  return response.data
}

export async function testHarborConnection(): Promise<HarborConnectionTest> {
  const response = await apiClient.post<HarborConnectionTest>('/settings/harbor/test')
  return response.data
}
