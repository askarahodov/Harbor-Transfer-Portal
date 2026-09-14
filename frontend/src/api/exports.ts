import axios from 'axios'

import { apiClient } from '@/api/client'

export type ArtifactKind = 'container-image' | 'helm-chart' | 'unknown-oci'
export type OperationType = 'EXPORT' | 'IMPORT'
export type OperationStatus =
  | 'CREATED'
  | 'VALIDATING'
  | 'RUNNING'
  | 'PACKAGING'
  | 'VERIFYING'
  | 'UPLOADED'
  | 'DISCOVERED'
  | 'READY'
  | 'IMPORTING'
  | 'VERIFYING_TARGET'
  | 'COMPLETED'
  | 'FAILED'
  | 'REJECTED'
  | 'CANCELLED'
export type ArtifactStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'IMPORTED'
  | 'SKIPPED'
  | 'CONFLICT'
  | 'FAILED'
  | 'VERIFIED'

export type ApiErrorInfo = {
  code: string
  message: string
  status?: number
}

export type PageMeta = {
  page: number
  page_size: number
  total: number
}

export type HarborProject = {
  name: string
  public: boolean
}

export type HarborRepository = {
  name: string
  artifact_count: number | null
  pull_count: number | null
}

export type HarborArtifact = {
  kind: ArtifactKind
  project: string
  repository: string
  references: string[]
  digest: string
  size: number | null
  pushed_at: string | null
  media_type: string | null
  artifact_type: string | null
}

export type PageResponse<T> = {
  pagination: PageMeta
  items: T[]
}

export type HarborConnection = {
  connected: boolean
  version: string | null
  auth_mode: string | null
}

export type ExportSelection = {
  kind: Exclude<ArtifactKind, 'unknown-oci'>
  project: string
  repository: string
  reference: string
  digest: string
}

export type ExportSelectionRequest = {
  artifacts: ExportSelection[]
  comment: string | null
}

export type ExportResolvedArtifact = {
  kind: Exclude<ArtifactKind, 'unknown-oci'>
  project: string
  repository: string
  reference: string
  source_digest: string
  size_bytes: number | null
}

export type ExportPreview = {
  artifacts: ExportResolvedArtifact[]
  estimated_payload_bytes: number
}

export type ExportStart = {
  operation_id: number
  delivery_id: string
  status: OperationStatus
}

export type OperationArtifact = {
  id: number
  artifact_type: string
  repository: string
  name: string | null
  reference: string | null
  version: string | null
  source_digest: string | null
  target_digest: string | null
  status: ArtifactStatus
  error_code: string | null
  error_message: string | null
  size_bytes: number | null
  started_at: string | null
  finished_at: string | null
}

export type OperationProgress = {
  total_artifacts: number
  completed_artifacts: number
  running_artifacts: number
  successful_artifacts: number
  failed_artifacts: number
  skipped_artifacts: number
  conflict_artifacts: number
  progress_current: number
  progress_total: number
  current_phase: OperationStatus
  running_artifact_ids: number[]
}

export type OperationBundle = {
  filename: string
  size_bytes: number
  sha256: string
}

export type Operation = {
  id: number
  delivery_id: string | null
  type: OperationType
  status: OperationStatus
  actor_username: string
  comment: string | null
  started_at: string | null
  finished_at: string | null
  error_code: string | null
  error_message: string | null
  cancel_requested: boolean
  bundle: OperationBundle | null
  progress: OperationProgress
  artifacts: OperationArtifact[]
}

export type ExportBundle = {
  operation_id: number
  delivery_id: string
  archive_name: string
  archive_size: number
  sha256: string
  download_url: string
}

export type ExportDownloadTicket = {
  download_url: string
  expires_in_seconds: number
}

function params(page: number, pageSize: number, search: string) {
  return {
    page,
    page_size: pageSize,
    ...(search.trim() ? { search: search.trim() } : {}),
  }
}

export async function listHarborProjects(
  page: number,
  pageSize: number,
  search: string,
): Promise<PageResponse<HarborProject>> {
  const response = await apiClient.get<PageResponse<HarborProject>>('/harbor/projects', {
    params: params(page, pageSize, search),
  })
  return response.data
}

export async function listHarborRepositories(
  project: string,
  page: number,
  pageSize: number,
  search: string,
): Promise<PageResponse<HarborRepository>> {
  const response = await apiClient.get<PageResponse<HarborRepository>>(
    `/harbor/projects/${encodeURIComponent(project)}/repositories`,
    { params: params(page, pageSize, search) },
  )
  return response.data
}

export async function listHarborArtifacts(
  project: string,
  repository: string,
  page: number,
  pageSize: number,
  search: string,
): Promise<PageResponse<HarborArtifact>> {
  const response = await apiClient.get<PageResponse<HarborArtifact>>(
    `/harbor/projects/${encodeURIComponent(project)}/artifacts`,
    {
      params: {
        repository,
        ...params(page, pageSize, search),
      },
    },
  )
  return response.data
}

export async function getHarborConnection(): Promise<HarborConnection> {
  const response = await apiClient.get<HarborConnection>('/harbor/connection')
  return response.data
}

export async function previewExport(payload: ExportSelectionRequest): Promise<ExportPreview> {
  const response = await apiClient.post<ExportPreview>('/exports/preview', payload)
  return response.data
}

export async function startExport(payload: ExportSelectionRequest): Promise<ExportStart> {
  const response = await apiClient.post<ExportStart>('/exports', payload)
  return response.data
}

export async function getOperation(operationId: number): Promise<Operation> {
  const response = await apiClient.get<Operation>(`/operations/${operationId}`)
  return response.data
}

export async function cancelOperation(operationId: number): Promise<Operation> {
  const response = await apiClient.post<Operation>(`/operations/${operationId}/cancel`)
  return response.data
}

export async function getExportBundle(operationId: number): Promise<ExportBundle> {
  const response = await apiClient.get<ExportBundle>(`/exports/${operationId}/bundle`)
  return response.data
}

export async function createExportDownloadTicket(
  operationId: number,
): Promise<ExportDownloadTicket> {
  const response = await apiClient.post<ExportDownloadTicket>(
    `/exports/${operationId}/download-ticket`,
  )
  return response.data
}

export function apiErrorInfo(error: unknown, fallback: string): ApiErrorInfo {
  if (!axios.isAxiosError(error)) {
    return { code: 'unexpected_error', message: fallback }
  }

  const status = error.response?.status
  const detail = error.response?.data?.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const candidate = detail as Record<string, unknown>
    if (typeof candidate.code === 'string' && typeof candidate.message === 'string') {
      return { code: candidate.code, message: candidate.message, status }
    }
  }

  if (status === 403) {
    return { code: 'forbidden', message: 'Недостаточно прав для выполнения операции.', status }
  }
  if (status === 404) {
    return { code: 'not_found', message: 'Запрошенный объект больше не найден.', status }
  }
  if (status === 422) {
    return {
      code: 'validation_failed',
      message: 'Проверьте выбранные артефакты и параметры операции.',
      status,
    }
  }
  if (status === 503) {
    return {
      code: 'service_unavailable',
      message: 'Локальный Harbor или backend временно недоступен.',
      status,
    }
  }
  return { code: 'request_failed', message: fallback, status }
}
