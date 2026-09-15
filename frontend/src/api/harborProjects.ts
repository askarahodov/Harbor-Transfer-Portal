import { apiClient } from '@/api/client'

export type HarborProjectCreateRequest = {
  name: string
  public?: boolean
  operation_id?: number | null
}

export type HarborProjectCreateResult = {
  name: string
  public: boolean
  created: boolean
}

export async function createHarborProject(
  payload: HarborProjectCreateRequest,
): Promise<HarborProjectCreateResult> {
  const response = await apiClient.post<HarborProjectCreateResult>('/harbor/projects', payload)
  return response.data
}
