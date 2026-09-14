import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  apiErrorInfo,
  cancelOperation,
  discoverImportBundles,
  executeImport,
  getImportPreview,
  getImportReceipt,
  getOperation,
  uploadImportBundle,
  type ApiErrorInfo,
  type ImportIntake,
  type ImportPreview,
  type ImportReceipt,
  type Operation,
  type OperationStatus,
} from '@/api/imports'

const OPERATION_STORAGE_KEY = 'htp.import.operation-id'
const POLL_INTERVAL_MS = 1500
const VERIFYING_STATES = new Set<OperationStatus>(['UPLOADED', 'DISCOVERED', 'VERIFYING'])
const IMPORTING_STATES = new Set<OperationStatus>(['IMPORTING', 'VERIFYING_TARGET'])
const TERMINAL_STATES = new Set<OperationStatus>(['COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED'])

export type ImportWizardStep = 1 | 2 | 3

export type UploadSelection = {
  name: string
  size: number
}

export type UploadProgress = {
  loaded: number
  total: number | null
}

export type DiscoveredImport = {
  intake: ImportIntake
  operation: Operation
}

function storageOrNull(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function saveOperationId(operationId: number | null): void {
  if (operationId === null) {
    storageOrNull()?.removeItem(OPERATION_STORAGE_KEY)
  } else {
    storageOrNull()?.setItem(OPERATION_STORAGE_KEY, String(operationId))
  }
}

function savedOperationId(): number | null {
  const raw = storageOrNull()?.getItem(OPERATION_STORAGE_KEY)
  if (!raw) return null
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : null
}

export const useImportWizardStore = defineStore('import-wizard', () => {
  const step = ref<ImportWizardStep>(1)
  const selectedFile = ref<UploadSelection | null>(null)
  const uploadProgress = ref<UploadProgress | null>(null)
  const discovered = ref<DiscoveredImport[]>([])
  const operation = ref<Operation | null>(null)
  const preview = ref<ImportPreview | null>(null)
  const receipt = ref<ImportReceipt | null>(null)
  const overwriteConfirmed = ref(false)
  const busy = ref<string | null>(null)
  const error = ref<ApiErrorInfo | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pollRequestActive = false

  const conflicts = computed(
    () => preview.value?.artifacts.filter((item) => item.classification === 'CONFLICT') ?? [],
  )
  const unresolved = computed(
    () =>
      preview.value?.artifacts.filter(
        (item) => item.classification === 'UNKNOWN' || item.classification === 'ERROR',
      ) ?? [],
  )
  const canExecuteDefault = computed(
    () => preview.value !== null && conflicts.value.length === 0 && unresolved.value.length === 0,
  )
  const canExecuteOverwrite = computed(
    () =>
      preview.value !== null &&
      preview.value.overwrite_allowed &&
      conflicts.value.length > 0 &&
      unresolved.value.length === 0 &&
      overwriteConfirmed.value,
  )
  const canCancel = computed(
    () =>
      operation.value !== null &&
      (VERIFYING_STATES.has(operation.value.status) || IMPORTING_STATES.has(operation.value.status)) &&
      !operation.value.cancel_requested,
  )

  function clearError(): void {
    error.value = null
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  function startPolling(): void {
    stopPolling()
    pollTimer = setInterval(() => {
      void refreshOperation()
    }, POLL_INTERVAL_MS)
  }

  async function loadPreview(operationId: number): Promise<void> {
    try {
      preview.value = await getImportPreview(operationId)
      overwriteConfirmed.value = false
      step.value = 2
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Verified preview пакета пока недоступен.')
    }
  }

  async function loadReceipt(operationId: number): Promise<void> {
    try {
      receipt.value = await getImportReceipt(operationId)
    } catch (requestError) {
      const info = apiErrorInfo(requestError, 'Receipt пока недоступен.')
      if (info.status !== 409) {
        error.value = info
      }
    }
  }

  async function applyOperation(current: Operation): Promise<void> {
    if (current.type !== 'IMPORT') {
      saveOperationId(null)
      throw new Error('Saved operation is not an import operation')
    }
    operation.value = current

    if (VERIFYING_STATES.has(current.status)) {
      step.value = 1
      return
    }
    if (current.status === 'READY') {
      stopPolling()
      await loadPreview(current.id)
      return
    }
    if (IMPORTING_STATES.has(current.status)) {
      step.value = 3
      return
    }
    if (TERMINAL_STATES.has(current.status)) {
      stopPolling()
      if (current.status === 'REJECTED') {
        step.value = 1
        error.value = {
          code: current.error_code ?? 'import_rejected',
          message: current.error_message ?? 'Пакет отклонён проверкой и не может быть импортирован.',
        }
        return
      }
      step.value = 3
      if (current.status === 'COMPLETED' || current.status === 'FAILED') {
        await loadReceipt(current.id)
      }
    }
  }

  async function refreshOperation(operationId = operation.value?.id ?? savedOperationId()): Promise<void> {
    if (!operationId || pollRequestActive) return
    pollRequestActive = true
    try {
      const current = await getOperation(operationId)
      await applyOperation(current)
    } catch (requestError) {
      stopPolling()
      error.value = apiErrorInfo(
        requestError,
        'Не удалось получить актуальное состояние import-операции.',
      )
    } finally {
      pollRequestActive = false
    }
  }

  async function selectOperation(operationId: number): Promise<void> {
    saveOperationId(operationId)
    preview.value = null
    receipt.value = null
    clearError()
    await refreshOperation(operationId)
    if (
      operation.value &&
      (VERIFYING_STATES.has(operation.value.status) || IMPORTING_STATES.has(operation.value.status))
    ) {
      startPolling()
    }
  }

  async function upload(file: File): Promise<boolean> {
    selectedFile.value = { name: file.name, size: file.size }
    uploadProgress.value = { loaded: 0, total: file.size }
    busy.value = 'upload'
    clearError()
    preview.value = null
    receipt.value = null
    try {
      const intake = await uploadImportBundle(file, (loaded, total) => {
        uploadProgress.value = { loaded, total: total ?? file.size }
      })
      await selectOperation(intake.operation_id)
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось принять bundle через браузер.')
      return false
    } finally {
      busy.value = null
    }
  }

  async function discover(): Promise<void> {
    busy.value = 'discover'
    clearError()
    try {
      const response = await discoverImportBundles()
      const resolved = await Promise.all(
        response.operations.map(async (intake) => ({
          intake,
          operation: await getOperation(intake.operation_id),
        })),
      )
      discovered.value = resolved.filter((item) => item.operation.type === 'IMPORT')
      if (discovered.value.length === 1) {
        await selectOperation(discovered.value[0].intake.operation_id)
      }
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Не удалось обнаружить готовые bundles в incoming directory.',
      )
    } finally {
      busy.value = null
    }
  }

  async function execute(overwriteConflicts: boolean): Promise<boolean> {
    if (!preview.value || !operation.value) return false
    if (unresolved.value.length > 0) {
      error.value = {
        code: 'import_preview_unresolved',
        message: 'UNKNOWN/ERROR запрещают изменение TARGET Harbor.',
      }
      return false
    }
    if (conflicts.value.length > 0 && !overwriteConflicts) {
      error.value = {
        code: 'import_conflict_blocked',
        message: 'CONFLICT заблокирован безопасной политикой по умолчанию.',
      }
      return false
    }
    if (
      overwriteConflicts &&
      (!preview.value.overwrite_allowed || !overwriteConfirmed.value)
    ) {
      error.value = {
        code: 'import_overwrite_not_confirmed',
        message: 'Overwrite требует разрешения backend и явного подтверждения конфликтов.',
      }
      return false
    }

    busy.value = 'execute'
    clearError()
    try {
      await executeImport(operation.value.id, overwriteConflicts)
      step.value = 3
      await refreshOperation(operation.value.id)
      if (operation.value && IMPORTING_STATES.has(operation.value.status)) {
        startPolling()
      }
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось запустить import в TARGET Harbor.')
      return false
    } finally {
      busy.value = null
    }
  }

  async function cancel(): Promise<void> {
    if (!operation.value || !canCancel.value) return
    busy.value = 'cancel'
    clearError()
    try {
      const current = await cancelOperation(operation.value.id)
      await applyOperation(current)
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось отменить import-операцию.')
    } finally {
      busy.value = null
    }
  }

  async function initialize(): Promise<void> {
    const operationId = savedOperationId()
    if (!operationId) return
    busy.value = 'initialize'
    clearError()
    try {
      await selectOperation(operationId)
    } finally {
      busy.value = null
    }
  }

  function reset(): void {
    stopPolling()
    saveOperationId(null)
    step.value = 1
    selectedFile.value = null
    uploadProgress.value = null
    discovered.value = []
    operation.value = null
    preview.value = null
    receipt.value = null
    overwriteConfirmed.value = false
    busy.value = null
    error.value = null
  }

  return {
    step,
    selectedFile,
    uploadProgress,
    discovered,
    operation,
    preview,
    receipt,
    overwriteConfirmed,
    busy,
    error,
    conflicts,
    unresolved,
    canExecuteDefault,
    canExecuteOverwrite,
    canCancel,
    upload,
    discover,
    selectOperation,
    execute,
    cancel,
    refreshOperation,
    initialize,
    reset,
    startPolling,
    stopPolling,
    clearError,
  }
})
