import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  apiErrorInfo,
  cancelOperation,
  getExportBundle,
  getHarborConnection,
  getOperation,
  listHarborArtifacts,
  listHarborProjects,
  listHarborRepositories,
  previewExport,
  startExport,
  type ApiErrorInfo,
  type ExportBundle,
  type ExportPreview,
  type ExportSelection,
  type HarborArtifact,
  type HarborConnection,
  type HarborProject,
  type HarborRepository,
  type Operation,
  type OperationStatus,
} from '@/api/exports'

const PAGE_SIZE = 25
const OPERATION_STORAGE_KEY = 'htp.export.operation-id'
const ACTIVE_EXPORT_STATUSES = new Set<OperationStatus>([
  'CREATED',
  'VALIDATING',
  'RUNNING',
  'PACKAGING',
  'VERIFYING',
])
const TERMINAL_EXPORT_STATUSES = new Set<OperationStatus>(['COMPLETED', 'FAILED', 'CANCELLED'])

export type ExportWizardStep = 1 | 2 | 3 | 4

export type SelectedExportArtifact = ExportSelection & {
  size_bytes: number | null
}

function storageOrNull(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function selectedKey(selection: ExportSelection): string {
  return [selection.kind, selection.project, selection.repository, selection.reference].join('|')
}

function savedOperationId(): number | null {
  const raw = storageOrNull()?.getItem(OPERATION_STORAGE_KEY)
  if (!raw) return null
  const parsed = Number(raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function saveOperationId(operationId: number | null): void {
  if (operationId === null) {
    storageOrNull()?.removeItem(OPERATION_STORAGE_KEY)
  } else {
    storageOrNull()?.setItem(OPERATION_STORAGE_KEY, String(operationId))
  }
}

export const useExportWizardStore = defineStore('export-wizard', () => {
  const step = ref<ExportWizardStep>(1)
  const connection = ref<HarborConnection | null>(null)
  const projects = ref<HarborProject[]>([])
  const repositories = ref<HarborRepository[]>([])
  const artifacts = ref<HarborArtifact[]>([])
  const selectedProject = ref<string | null>(null)
  const selectedRepository = ref<string | null>(null)
  const projectSearch = ref('')
  const repositorySearch = ref('')
  const artifactSearch = ref('')
  const projectPage = ref(1)
  const repositoryPage = ref(1)
  const artifactPage = ref(1)
  const projectTotal = ref(0)
  const repositoryTotal = ref(0)
  const artifactTotal = ref(0)
  const selected = ref<Record<string, SelectedExportArtifact>>({})
  const comment = ref('')
  const preview = ref<ExportPreview | null>(null)
  const operation = ref<Operation | null>(null)
  const bundle = ref<ExportBundle | null>(null)
  const error = ref<ApiErrorInfo | null>(null)
  const busy = ref<string | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pollRequestActive = false

  const selectedArtifacts = computed(() => Object.values(selected.value))
  const selectedCount = computed(() => selectedArtifacts.value.length)
  const selectedKnownBytes = computed(() =>
    selectedArtifacts.value.reduce((sum, item) => sum + (item.size_bytes ?? 0), 0),
  )
  const selectedUnknownSizeCount = computed(
    () => selectedArtifacts.value.filter((item) => item.size_bytes === null).length,
  )
  const canCancel = computed(
    () =>
      operation.value !== null &&
      ACTIVE_EXPORT_STATUSES.has(operation.value.status) &&
      !operation.value.cancel_requested,
  )
  const isTerminalFailure = computed(
    () => operation.value?.status === 'FAILED' || operation.value?.status === 'CANCELLED',
  )
  const failedArtifacts = computed(
    () => operation.value?.artifacts.filter((item) => item.status === 'FAILED') ?? [],
  )

  function clearError(): void {
    error.value = null
  }

  async function loadConnection(): Promise<void> {
    try {
      connection.value = await getHarborConnection()
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Не удалось проверить соединение с локальным Harbor.',
      )
    }
  }

  async function loadProjects(page = 1): Promise<void> {
    busy.value = 'projects'
    clearError()
    try {
      const result = await listHarborProjects(page, PAGE_SIZE, projectSearch.value)
      projects.value = result.items
      projectPage.value = result.pagination.page
      projectTotal.value = result.pagination.total
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить проекты Harbor.')
    } finally {
      busy.value = null
    }
  }

  async function chooseProject(project: string): Promise<void> {
    selectedProject.value = project
    selectedRepository.value = null
    repositories.value = []
    artifacts.value = []
    repositorySearch.value = ''
    artifactSearch.value = ''
    repositoryPage.value = 1
    artifactPage.value = 1
    await loadRepositories(1)
  }

  async function loadRepositories(page = 1): Promise<void> {
    if (!selectedProject.value) return
    busy.value = 'repositories'
    clearError()
    try {
      const result = await listHarborRepositories(
        selectedProject.value,
        page,
        PAGE_SIZE,
        repositorySearch.value,
      )
      repositories.value = result.items
      repositoryPage.value = result.pagination.page
      repositoryTotal.value = result.pagination.total
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить репозитории Harbor.')
    } finally {
      busy.value = null
    }
  }

  async function chooseRepository(repository: string): Promise<void> {
    selectedRepository.value = repository
    artifacts.value = []
    artifactSearch.value = ''
    artifactPage.value = 1
    await loadArtifacts(1)
  }

  async function loadArtifacts(page = 1): Promise<void> {
    if (!selectedProject.value || !selectedRepository.value) return
    busy.value = 'artifacts'
    clearError()
    try {
      const result = await listHarborArtifacts(
        selectedProject.value,
        selectedRepository.value,
        page,
        PAGE_SIZE,
        artifactSearch.value,
      )
      artifacts.value = result.items
      artifactPage.value = result.pagination.page
      artifactTotal.value = result.pagination.total
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить артефакты Harbor.')
    } finally {
      busy.value = null
    }
  }

  function referencesFor(artifact: HarborArtifact): string[] {
    if (artifact.kind === 'unknown-oci') return []
    if (artifact.references.length > 0) return artifact.references
    return artifact.kind === 'container-image' ? [artifact.digest] : []
  }

  function isSelected(artifact: HarborArtifact, reference: string): boolean {
    if (artifact.kind === 'unknown-oci') return false
    return Boolean(
      selected.value[
        selectedKey({
          kind: artifact.kind,
          project: artifact.project,
          repository: artifact.repository,
          reference,
          digest: artifact.digest,
        })
      ],
    )
  }

  function toggleArtifact(artifact: HarborArtifact, reference: string): void {
    if (artifact.kind === 'unknown-oci') return
    const selection: SelectedExportArtifact = {
      kind: artifact.kind,
      project: artifact.project,
      repository: artifact.repository,
      reference,
      digest: artifact.digest,
      size_bytes: artifact.size,
    }
    const key = selectedKey(selection)
    const next = { ...selected.value }
    if (next[key]) {
      delete next[key]
    } else {
      next[key] = selection
    }
    selected.value = next
    preview.value = null
  }

  function requestPayload() {
    return {
      artifacts: selectedArtifacts.value.map((artifact) => ({
        kind: artifact.kind,
        project: artifact.project,
        repository: artifact.repository,
        reference: artifact.reference,
        digest: artifact.digest,
      })),
      comment: comment.value.trim() || null,
    }
  }

  async function preparePreview(): Promise<boolean> {
    if (selectedCount.value === 0) {
      error.value = {
        code: 'selection_required',
        message: 'Выберите хотя бы один точный tag/version перед продолжением.',
      }
      return false
    }
    if (comment.value.length > 2000) {
      error.value = {
        code: 'comment_too_long',
        message: 'Комментарий не должен превышать 2000 символов.',
      }
      return false
    }

    busy.value = 'preview'
    clearError()
    try {
      preview.value = await previewExport(requestPayload())
      step.value = 2
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Backend не смог подтвердить выбранные артефакты.',
      )
      return false
    } finally {
      busy.value = null
    }
  }

  function backToSelection(): void {
    step.value = 1
    clearError()
  }

  async function start(): Promise<boolean> {
    if (!preview.value) return false
    busy.value = 'start'
    clearError()
    try {
      const started = await startExport(requestPayload())
      saveOperationId(started.operation_id)
      step.value = 3
      await refreshOperation(started.operation_id)
      if (operation.value && ACTIVE_EXPORT_STATUSES.has(operation.value.status)) {
        startPolling()
      }
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось запустить SOURCE export.')
      return false
    } finally {
      busy.value = null
    }
  }

  async function loadBundle(operationId: number): Promise<void> {
    try {
      bundle.value = await getExportBundle(operationId)
      step.value = 4
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Операция завершена, но metadata готового bundle недоступна.',
      )
    }
  }

  async function refreshOperation(operationId = operation.value?.id ?? savedOperationId()): Promise<void> {
    if (!operationId || pollRequestActive) return
    pollRequestActive = true
    try {
      const current = await getOperation(operationId)
      if (current.type !== 'EXPORT') {
        saveOperationId(null)
        throw new Error('Saved operation is not an export operation')
      }
      operation.value = current
      if (current.status === 'COMPLETED') {
        stopPolling()
        await loadBundle(current.id)
      } else if (TERMINAL_EXPORT_STATUSES.has(current.status)) {
        stopPolling()
        step.value = 3
      }
    } catch (requestError) {
      stopPolling()
      error.value = apiErrorInfo(
        requestError,
        'Не удалось получить актуальный статус export-операции.',
      )
    } finally {
      pollRequestActive = false
    }
  }

  function startPolling(): void {
    stopPolling()
    pollTimer = setInterval(() => {
      void refreshOperation()
    }, 1500)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function cancel(): Promise<void> {
    if (!operation.value || !canCancel.value) return
    busy.value = 'cancel'
    clearError()
    try {
      operation.value = await cancelOperation(operation.value.id)
      if (TERMINAL_EXPORT_STATUSES.has(operation.value.status)) {
        stopPolling()
      }
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось отменить export-операцию.')
    } finally {
      busy.value = null
    }
  }

  async function resumeSavedOperation(): Promise<boolean> {
    const operationId = savedOperationId()
    if (!operationId) return false
    step.value = 3
    await refreshOperation(operationId)
    if (operation.value && ACTIVE_EXPORT_STATUSES.has(operation.value.status)) {
      startPolling()
    }
    return operation.value !== null
  }

  async function initialize(): Promise<void> {
    busy.value = 'initialize'
    clearError()
    await loadConnection()
    const resumed = await resumeSavedOperation()
    if (!resumed) {
      await loadProjects(1)
    }
    busy.value = null
  }

  function reset(): void {
    stopPolling()
    saveOperationId(null)
    step.value = 1
    preview.value = null
    operation.value = null
    bundle.value = null
    comment.value = ''
    error.value = null
    selected.value = {}
  }

  return {
    step,
    connection,
    projects,
    repositories,
    artifacts,
    selectedProject,
    selectedRepository,
    projectSearch,
    repositorySearch,
    artifactSearch,
    projectPage,
    repositoryPage,
    artifactPage,
    projectTotal,
    repositoryTotal,
    artifactTotal,
    selected,
    comment,
    preview,
    operation,
    bundle,
    error,
    busy,
    selectedArtifacts,
    selectedCount,
    selectedKnownBytes,
    selectedUnknownSizeCount,
    canCancel,
    isTerminalFailure,
    failedArtifacts,
    loadProjects,
    chooseProject,
    loadRepositories,
    chooseRepository,
    loadArtifacts,
    referencesFor,
    isSelected,
    toggleArtifact,
    preparePreview,
    backToSelection,
    start,
    refreshOperation,
    startPolling,
    stopPolling,
    cancel,
    resumeSavedOperation,
    initialize,
    reset,
    clearError,
  }
})
