import { apiClient } from '@/api/client'
import {
  apiErrorInfo,
  createExportDownloadTicket,
  getOperation,
  type ApiErrorInfo,
  type Operation,
  type OperationBundle,
  type OperationStatus,
  type OperationType,
} from '@/api/exports'
import { getImportReceipt, type ImportReceipt } from '@/api/imports'

export { apiErrorInfo, createExportDownloadTicket, getImportReceipt, getOperation }
export type { ApiErrorInfo, ImportReceipt, Operation, OperationStatus, OperationType }

export type OperationSummary = {
  id: number
  delivery_id: string | null
  type: OperationType
  status: OperationStatus
  actor_username: string
  comment: string | null
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
