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

export type BrowserPhysicalHandoffFiles = {
  bundle: File
  sidecar: File
  handoff: File
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error ?? new Error('Не удалось прочитать metadata file'))
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const separator = result.indexOf(',')
      if (separator < 0) {
        reject(new Error('Не удалось закодировать metadata file'))
        return
      }
      resolve(result.slice(separator + 1))
    }
    reader.readAsDataURL(file)
  })
}

export type ImportIntakeMode = 'upload' | 'incoming'
export type ImportPreviewState = 'NEW' | 'SAME' | 'CONFLICT' | 'UNKNOWN' | 'ERROR'

export type MediaHandoffVerification = {
  delivery_id: string
  signing_key_fingerprint: string
  bundle_sha256: string
  bundle_size_bytes: number
  created_at: string
  created_by: string
  verified: boolean
}

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
  harbor_profile_id?: string | null
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
  mapping_policy_revision: number
  created_at: string
  valid: boolean
  artifacts: ImportDestinationArtifactPlan[]
}

export type ImportRetry = {
  operation_id: number
  retry_of_operation_id: number
  status: OperationStatus
  failure_policy: 'continue-on-error'
  destination_plan: ImportDestinationPlan
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
  skip_conflicts: boolean
  destination_plan_id: string | null
  destination_plan_hash?: string | null
  retry_of_operation_id?: number | null
  failure_policy?: string | null
  result: string
  artifacts: ImportReceiptArtifact[]
}

export async function verifyPhysicalHandoff(
  file: File,
): Promise<MediaHandoffVerification> {
  const response = await apiClient.post<MediaHandoffVerification>(
    '/imports/handoff/verify',
    file,
    {
      headers: {
        'Content-Type': 'application/json',
      },
    },
  )
  return response.data
}

export async function uploadImportBundle(
  file: File,
  onProgress?: (loaded: number, total: number | null) => void,
  physicalHandoff?: Pick<BrowserPhysicalHandoffFiles, 'sidecar' | 'handoff'>,
  profileId?: string,
): Promise<ImportIntake> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/gzip',
  }
  if (physicalHandoff) {
    headers['X-HTP-Bundle-Filename'] = file.name
    headers['X-HTP-Sidecar-Base64'] = await fileToBase64(physicalHandoff.sidecar)
    headers['X-HTP-Handoff-Base64'] = await fileToBase64(physicalHandoff.handoff)
  }
  const response = await apiClient.post<ImportIntake>('/imports/upload', file, {
    headers,
    params: profileId ? { profile_id: profileId } : undefined,
    timeout: 0,
    onUploadProgress: (event: AxiosProgressEvent) => {
      onProgress?.(event.loaded, event.total ?? null)
    },
  })
  return response.data
}

export async function discoverImportBundles(profileId?: string): Promise<ImportDiscovery> {
  const response = await apiClient.post<ImportDiscovery>('/imports/discover', undefined, {
    params: profileId ? { profile_id: profileId } : undefined,
  })
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

export async function prepareImportRetry(
  operationId: number,
  destinationPlanId: string,
): Promise<ImportRetry> {
  const response = await apiClient.post<ImportRetry>(`/imports/${operationId}/retry`, {
    destination_plan_id: destinationPlanId,
  })
  return response.data
}

export async function executeImport(
  operationId: number,
  overwriteConflicts: boolean,
  destinationPlanId: string,
  skipConflicts = false,
): Promise<{ operation_id: number; status: OperationStatus }> {
  const response = await apiClient.post<{ operation_id: number; status: OperationStatus }>(
    `/imports/${operationId}/execute`,
    {
      overwrite_conflicts: overwriteConflicts,
      skip_conflicts: skipConflicts,
      destination_plan_id: destinationPlanId,
    },
  )
  return response.data
}

export async function getImportReceipt(operationId: number): Promise<ImportReceipt> {
  const response = await apiClient.get<ImportReceipt>(`/imports/${operationId}/receipt`)
  return response.data
}
