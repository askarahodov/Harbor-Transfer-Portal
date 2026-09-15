import type { AxiosProgressEvent } from 'axios'

import { apiClient } from '@/api/client'
import {
  apiErrorInfo,
  cancelOperation,
  getOperation,
  type ApiErrorInfo,
  type ArtifactStatus,
  type Operation,
  type OperationStatus,
} from '@/api/exports'

export { apiErrorInfo, cancelOperation, getOperation }
export type { ApiErrorInfo, ArtifactStatus, Operation, OperationStatus }

export type ImportIntakeMode = 'upload' | 'incoming'
export type ImportPreviewState = 'NEW' | 'SAME' | 'CONFLICT' | 'UNKNOWN' | 'ERROR'

export type ImportIntake = {
  operation_id: number
  status: OperationStatus
  intake_mode: ImportIntakeMode
}

export type ImportDiscovery = {
  operations: ImportIntake[]
}

export type ImportArtifactPreview = {
  index: number
  artifact_type: string
  repository: string
  name: string | null
  reference: string | null
  version: string | null
  expected_digest: string | null
  target_digest: string | null
  payload_size: number
  classification: ImportPreviewState
  error_code: string | null
  message: string | null
}

export type ImportPreview = {
  operation_id: number
  status: OperationStatus
  source_delivery_id: string
  bundle_sha256: string
  bundle_size_bytes: number
  signing_key_fingerprint: string
  verified_at: string
  bundle_filename: string | null
  intake_mode: ImportIntakeMode | null
  source_harbor: string | null
  source_portal_version: string | null
  source_created_at: string | null
  source_created_by: string | null
  source_comment: string | null
  checksum_verified: boolean
  signature_verified: boolean
  schema_verified: boolean
  overwrite_allowed: boolean
  artifacts: ImportArtifactPreview[]
}

export type ImportArtifactDestinationOverride = {
  index: number
  target_project: string
}

export type ImportDestinationPlanRequest = {
  container_image_project: string | null
  helm_chart_project: string | null
  project_mappings: Record<string, string>
  artifact_overrides: ImportArtifactDestinationOverride[]
}

export type ImportDestinationArtifactPlan = {
  index: number
  artifact_type: string
  source_repository: string
  source_project: string
  name: string | null
  reference: string | null
  version: string | null
  expected_digest: string | null
  payload_size: number
  target_project: string | null
  target_repository: string | null
  final_reference: string | null
  project_exists: boolean
  write_allowed: boolean
  target_digest: string | null
  classification: ImportPreviewState
  error_code: string | null
  message: string | null
}

export type ImportDestinationPlan = {
  operation_id: number
  source_delivery_id: string
  actor_username: string
  bundle_sha256: string
  plan_id: string
  plan_hash: string
  created_at: string
  valid: boolean
  artifacts: ImportDestinationArtifactPlan[]
}

export type ImportReceiptArtifact = {
  index: number
  artifact_type: string
  repository: string
  name: string | null
  reference: string | null
  version: string | null
  expected_digest: string | null
  target_digest: string | null
  target_repository: string | null
  final_reference: string | null
  status: ArtifactStatus
  error_code: string | null
  error_message: string | null
}

export type ImportReceipt = {
  operation_id: number
  source_delivery_id: string
  bundle_sha256: string
  actor_username: string
  started_at: string
  finished_at: string
  overwrite_conflicts: boolean
  destination_plan_id: string | null
  destination_plan_hash: string | null
  result: string
  artifacts: ImportReceiptArtifact[]
}

export async function uploadImportBundle(
  file: File,
  onProgress?: (loaded: number, total: number | null) => void,
): Promise<ImportIntake> {
  const response = await apiClient.post<ImportIntake>('/imports/upload', file, {
    headers: {
      'Content-Type': 'application/gzip',
    },
    timeout: 0,
    onUploadProgress: (event: AxiosProgressEvent) => {
      onProgress?.(event.loaded, event.total ?? null)
    },
  })
  return response.data
}

export async function discoverImportBundles(): Promise<ImportDiscovery> {
  const response = await apiClient.post<ImportDiscovery>('/imports/discover')
  return response.data
}

export async function getImportPreview(operationId: number): Promise<ImportPreview> {
  const response = await apiClient.get<ImportPreview>(`/imports/${operationId}/preview`)
  return response.data
}

export async function buildImportDestinationPlan(
  operationId: number,
  mapping: ImportDestinationPlanRequest,
): Promise<ImportDestinationPlan> {
  const response = await apiClient.put<ImportDestinationPlan>(
    `/imports/${operationId}/destination-plan`,
    mapping,
  )
  return response.data
}

export async function executeImport(
  operationId: number,
  overwriteConflicts: boolean,
  destinationPlanId: string,
): Promise<{ operation_id: number; status: OperationStatus }> {
  const response = await apiClient.post<{ operation_id: number; status: OperationStatus }>(
    `/imports/${operationId}/execute`,
    {
      overwrite_conflicts: overwriteConflicts,
      destination_plan_id: destinationPlanId,
    },
  )
  return response.data
}

export async function getImportReceipt(operationId: number): Promise<ImportReceipt> {
  const response = await apiClient.get<ImportReceipt>(`/imports/${operationId}/receipt`)
  return response.data
}
