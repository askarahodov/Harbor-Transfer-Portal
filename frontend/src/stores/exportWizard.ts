import { computed, onScopeDispose, ref, watch } from 'vue'
import { defineStore, storeToRefs } from 'pinia'

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
import { useHarborProfilesStore } from '@/stores/harborProfiles'

const PAGE_SIZE = 25
const SEARCH_DEBOUNCE_MS = 300
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
  return [selection.kind, selection.project, selection.repository, selection.digest].join('|')
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
  const harborProfilesStore = useHarborProfilesStore()
  const {
    profiles: harborProfiles,
    selectedId: selectedHarborProfileId,
    selectedProfile: selectedHarborProfile,
    loading: harborProfilesLoading,
    error: harborProfilesError,
  } = storeToRefs(harborProfilesStore)
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
  let projectSearchTimer: ReturnType<typeof setTimeout> | null = null
  let repositorySearchTimer: ReturnType<typeof setTimeout> | null = null
  let artifactSearchTimer: ReturnType<typeof setTimeout> | null = null
  let projectRequestGeneration = 0
  let repositoryRequestGeneration = 0
  let artifactRequestGeneration = 0

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
  const harborProfileLocked = computed(
    () => operation.value !== null || preview.value !== null || step.value !== 1,
  )

  function clearError(): void {
    error.value = null
  }

  function clearProjectSearchTimer(): void {
    if (projectSearchTimer !== null) {
      clearTimeout(projectSearchTimer)
      projectSearchTimer = null
    }
  }

  function clearRepositorySearchTimer(): void {
    if (repositorySearchTimer !== null) {
      clearTimeout(repositorySearchTimer)
      repositorySearchTimer = null
    }
  }

  function clearArtifactSearchTimer(): void {
    if (artifactSearchTimer !== null) {
      clearTimeout(artifactSearchTimer)
      artifactSearchTimer = null
    }
  }

  function cancelSearchDebounces(): void {
    clearProjectSearchTimer()
    clearRepositorySearchTimer()
    clearArtifactSearchTimer()
  }

  function scheduleProjectSearch(): void {
    projectPage.value = 1
    projectRequestGeneration += 1
    clearProjectSearchTimer()
    clearError()
    projectSearchTimer = setTimeout(() => {
      projectSearchTimer = null
      void loadProjects(1)
    }, SEARCH_DEBOUNCE_MS)
  }

  function scheduleRepositorySearch(): void {
    repositoryPage.value = 1
    repositoryRequestGeneration += 1
    clearRepositorySearchTimer()
    clearError()
    if (!selectedProject.value) return
    repositorySearchTimer = setTimeout(() => {
      repositorySearchTimer = null
      void loadRepositories(1)
    }, SEARCH_DEBOUNCE_MS)
  }

  function scheduleArtifactSearch(): void {
    artifactPage.value = 1
    artifactRequestGeneration += 1
    clearArtifactSearchTimer()
    clearError()
    if (!selectedProject.value || !selectedRepository.value) return
    artifactSearchTimer = setTimeout(() => {
      artifactSearchTimer = null
      void loadArtifacts(1)
    }, SEARCH_DEBOUNCE_MS)
  }

  async function loadConnection(): Promise<void> {
    const profileId = selectedHarborProfileId.value
    if (!profileId) return
    try {
      connection.value = await getHarborConnection(profileId)
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Не удалось проверить соединение с локальным Harbor.',
      )
    }
  }

  async function loadProjects(page = 1): Promise<void> {
    clearProjectSearchTimer()
    const requestGeneration = ++projectRequestGeneration
    const search = projectSearch.value
    busy.value = 'projects'
    clearError()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) return
      const result = await listHarborProjects(page, PAGE_SIZE, search, profileId)
      if (requestGeneration !== projectRequestGeneration) return
      projects.value = result.items
      projectPage.value = result.pagination.page
      projectTotal.value = result.pagination.total
    } catch (requestError) {
      if (requestGeneration !== projectRequestGeneration) return
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить проекты Harbor.')
    } finally {
      if (requestGeneration === projectRequestGeneration && busy.value === 'projects') {
        busy.value = null
      }
    }
  }

  async function chooseProject(project: string): Promise<void> {
    repositoryRequestGeneration += 1
    artifactRequestGeneration += 1
    selectedProject.value = project
    selectedRepository.value = null
    repositories.value = []
    artifacts.value = []
    repositoryTotal.value = 0
    artifactTotal.value = 0
    repositorySearch.value = ''
    artifactSearch.value = ''
    clearRepositorySearchTimer()
    clearArtifactSearchTimer()
    repositoryPage.value = 1
    artifactPage.value = 1
    await loadRepositories(1)
  }

  async function loadRepositories(page = 1): Promise<void> {
    const project = selectedProject.value
    if (!project) return
    clearRepositorySearchTimer()
    const requestGeneration = ++repositoryRequestGeneration
    const search = repositorySearch.value
    busy.value = 'repositories'
    clearError()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) return
      const result = await listHarborRepositories(project, page, PAGE_SIZE, search, profileId)
      if (
        requestGeneration !== repositoryRequestGeneration ||
        selectedProject.value !== project
      ) {
        return
      }
      repositories.value = result.items
      repositoryPage.value = result.pagination.page
      repositoryTotal.value = result.pagination.total
    } catch (requestError) {
      if (
        requestGeneration !== repositoryRequestGeneration ||
        selectedProject.value !== project
      ) {
        return
      }
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить репозитории Harbor.')
    } finally {
      if (
        requestGeneration === repositoryRequestGeneration &&
        selectedProject.value === project &&
        busy.value === 'repositories'
      ) {
        busy.value = null
      }
    }
  }

  async function chooseRepository(repository: string): Promise<void> {
    artifactRequestGeneration += 1
    selectedRepository.value = repository
    artifacts.value = []
    artifactTotal.value = 0
    artifactSearch.value = ''
    clearArtifactSearchTimer()
    artifactPage.value = 1
    await loadArtifacts(1)
  }

  async function loadArtifacts(page = 1): Promise<void> {
    const project = selectedProject.value
    const repository = selectedRepository.value
    if (!project || !repository) return
    clearArtifactSearchTimer()
    const requestGeneration = ++artifactRequestGeneration
    const search = artifactSearch.value
    busy.value = 'artifacts'
    clearError()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) return
      const result = await listHarborArtifacts(
        project,
        repository,
        page,
        PAGE_SIZE,
        search,
        profileId,
      )
      if (
        requestGeneration !== artifactRequestGeneration ||
        selectedProject.value !== project ||
        selectedRepository.value !== repository
      ) {
        return
      }
      artifacts.value = result.items
      artifactPage.value = result.pagination.page
      artifactTotal.value = result.pagination.total
    } catch (requestError) {
      if (
        requestGeneration !== artifactRequestGeneration ||
        selectedProject.value !== project ||
        selectedRepository.value !== repository
      ) {
        return
      }
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить артефакты Harbor.')
    } finally {
      if (
        requestGeneration === artifactRequestGeneration &&
        selectedProject.value === project &&
        selectedRepository.value === repository &&
        busy.value === 'artifacts'
      ) {
        busy.value = null
      }
    }
  }

  function referencesFor(artifact: HarborArtifact): string[] {
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

  function addArtifact(artifact: HarborArtifact, reference: string): void {
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
    if (selected.value[key]?.reference === reference) return
    selected.value = { ...selected.value, [key]: selection }
    preview.value = null
  }

  function requestPayload() {
    const profileId = selectedHarborProfileId.value
    if (!profileId) {
      throw new Error('Harbor profile is required')
    }
    return {
      artifacts: selectedArtifacts.value.map((artifact) => ({
        kind: artifact.kind,
        project: artifact.project,
        repository: artifact.repository,
        reference: artifact.reference,
        digest: artifact.digest,
      })),
      comment: comment.value.trim() || null,
      harbor_profile_id: profileId,
    }
  }

  async function preparePreview(): Promise<boolean> {
    if (!selectedHarborProfileId.value) {
      error.value = {
        code: 'harbor_profile_required',
        message: 'Выберите Harbor profile перед продолжением.',
      }
      return false
    }
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
      if (current.harbor_profile_id) {
        harborProfilesStore.select(current.harbor_profile_id)
      }
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

  async function loadHarborProfiles(): Promise<boolean> {
    const loaded = await harborProfilesStore.load()
    if (!loaded && harborProfilesError.value) {
      error.value = harborProfilesError.value
    }
    return loaded
  }

  async function selectHarborProfile(profileId: string): Promise<void> {
    if (harborProfileLocked.value || profileId === selectedHarborProfileId.value) return
    if (!harborProfilesStore.select(profileId)) return

    cancelSearchDebounces()
    projectRequestGeneration += 1
    repositoryRequestGeneration += 1
    artifactRequestGeneration += 1
    connection.value = null
    projects.value = []
    repositories.value = []
    artifacts.value = []
    selectedProject.value = null
    selectedRepository.value = null
    projectSearch.value = ''
    repositorySearch.value = ''
    artifactSearch.value = ''
    projectPage.value = 1
    repositoryPage.value = 1
    artifactPage.value = 1
    projectTotal.value = 0
    repositoryTotal.value = 0
    artifactTotal.value = 0
    selected.value = {}
    preview.value = null
    clearError()
    await loadConnection()
    await loadProjects(1)
  }

  async function initialize(): Promise<void> {
    busy.value = 'initialize'
    clearError()
    try {
      const profilesReady = await loadHarborProfiles()
      if (!profilesReady) return
      const resumed = await resumeSavedOperation()
      if (!resumed) {
        await loadConnection()
        await loadProjects(1)
      }
    } finally {
      busy.value = null
    }
  }

  function reset(): void {
    cancelSearchDebounces()
    projectRequestGeneration += 1
    repositoryRequestGeneration += 1
    artifactRequestGeneration += 1
    stopPolling()
    saveOperationId(null)
    step.value = 1
    preview.value = null
    operation.value = null
    bundle.value = null
    comment.value = ''
    error.value = null
    busy.value = null
    selected.value = {}
  }

  watch(projectSearch, scheduleProjectSearch, { flush: 'sync' })
  watch(repositorySearch, scheduleRepositorySearch, { flush: 'sync' })
  watch(artifactSearch, scheduleArtifactSearch, { flush: 'sync' })
  onScopeDispose(cancelSearchDebounces)

  return {
    step,
    connection,
    harborProfiles,
    selectedHarborProfileId,
    selectedHarborProfile,
    harborProfilesLoading,
    harborProfileLocked,
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
    loadHarborProfiles,
    selectHarborProfile,
    loadProjects,
    chooseProject,
    loadRepositories,
    chooseRepository,
    loadArtifacts,
    referencesFor,
    isSelected,
    toggleArtifact,
    addArtifact,
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