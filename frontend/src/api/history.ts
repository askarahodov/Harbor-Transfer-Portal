import { apiClient } from '@/api/client'
import {
  apiErrorInfo,
  cancelOperation,
  createExportDownloadTicket,
  type ApiErrorInfo,
  type Operation as BaseOperation,
  type OperationBundle,
  type OperationStatus,
  type OperationType,
} from '@/api/exports'
import { getImportReceipt, type ImportReceipt } from '@/api/imports'

export { apiErrorInfo, cancelOperation, createExportDownloadTicket, getImportReceipt }
export type { ApiErrorInfo, ImportReceipt, OperationStatus, OperationType }

export type Operation = BaseOperation & {
  retry_of_operation_id: number | null
  failure_policy: string | null
}

export type OperationReportFormat = 'csv' | 'pdf'

export type OperationSummary = {
  id: number
  delivery_id: string | null
  type: OperationType
  status: OperationStatus
  actor_username: string
  comment: string | null
  retry_of_operation_id: number | null
  failure_policy: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_code: string | null
  error_message: string | null
  total_artifacts: number
  successful_artifacts: number
  failed_artifacts: number
  skipped_artifacts: number
  conflict_artifacts: number
  bundle: OperationBundle | null
}

export type OperationHistoryPage = {
  items: OperationSummary[]
  total: number
  limit: number
  offset: number
}

export type HistoryFilters = {
  type: OperationType | ''
  status: OperationStatus | ''
  actor: string
  search: string
  createdFrom: string
  createdTo: string
}

function isoOrUndefined(value: string): string | undefined {
  if (!value) return undefined
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString()
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export async function listOperationHistory(
  filters: HistoryFilters,
  limit: number,
  offset: number,
): Promise<OperationHistoryPage> {
  const response = await apiClient.get<OperationHistoryPage>('/operations', {
    params: {
      limit,
      offset,
      ...(filters.type ? { type: filters.type } : {}),
      ...(filters.status ? { status: filters.status } : {}),
      ...(filters.actor.trim() ? { actor: filters.actor.trim() } : {}),
      ...(filters.search.trim() ? { search: filters.search.trim() } : {}),
      ...(isoOrUndefined(filters.createdFrom)
        ? { created_from: isoOrUndefined(filters.createdFrom) }
        : {}),
      ...(isoOrUndefined(filters.createdTo) ? { created_to: isoOrUndefined(filters.createdTo) } : {}),
    },
  })
  return response.data
}

export async function getOperation(operationId: number): Promise<Operation> {
  const response = await apiClient.get<Operation>(`/operations/${operationId}`)
  return response.data
}

export async function downloadOperationReport(
  operationId: number,
  format: OperationReportFormat,
): Promise<void> {
  const response = await apiClient.get<Blob>(`/operations/${operationId}/report.${format}`, {
    responseType: 'blob',
  })
  saveBlob(response.data, `operation-${operationId}.${format}`)
}

export async function downloadImportReceiptFile(operationId: number): Promise<void> {
  const response = await apiClient.get<Blob>(`/imports/${operationId}/receipt/download`, {
    responseType: 'blob',
  })
  saveBlob(response.data, `import-receipt-${operationId}.json`)
}
