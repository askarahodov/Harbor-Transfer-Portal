import { computed, ref } from 'vue'
import { defineStore, storeToRefs } from 'pinia'

import {
  apiErrorInfo,
  buildImportDestinationPlan,
  cancelOperation,
  discoverImportBundles,
  executeImport,
  getImportPreview,
  getImportReceipt,
  getOperation,
  uploadImportBundle,
  type ApiErrorInfo,
  type BrowserPhysicalHandoffFiles,
  type ImportDestinationPlan,
  type ImportDestinationPlanRequest,
  type ImportIntake,
  type ImportPreview,
  type ImportReceipt,
  type Operation,
  type OperationStatus,
} from '@/api/imports'
import { useHarborProfilesStore } from '@/stores/harborProfiles'

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

function emptyMappingRequest(): ImportDestinationPlanRequest {
  return {
    harbor_profile_id: null,
    container_image_project: null,
    helm_chart_project: null,
    project_mappings: {},
    artifact_overrides: [],
  }
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
  const harborProfilesStore = useHarborProfilesStore()
  const {
    profiles: harborProfiles,
    selectedId: selectedHarborProfileId,
    selectedProfile: selectedHarborProfile,
    loading: harborProfilesLoading,
    error: harborProfilesError,
  } = storeToRefs(harborProfilesStore)
  const step = ref<ImportWizardStep>(1)
  const selectedFile = ref<UploadSelection | null>(null)
  const uploadProgress = ref<UploadProgress | null>(null)
  const discovered = ref<DiscoveredImport[]>([])
  const operation = ref<Operation | null>(null)
  const preview = ref<ImportPreview | null>(null)
  const destinationPlan = ref<ImportDestinationPlan | null>(null)
  const mappingDraft = ref<ImportDestinationPlanRequest>(emptyMappingRequest())
  const mappingDirty = ref(true)
  const receipt = ref<ImportReceipt | null>(null)
  const overwriteConfirmed = ref(false)
  const busy = ref<string | null>(null)
  const error = ref<ApiErrorInfo | null>(null)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let pollRequestActive = false

  const sourceProjects = computed(() => {
    const projects = new Set<string>()
    for (const artifact of preview.value?.artifacts ?? []) {
      const [project] = artifact.repository.split('/', 1)
      if (project) projects.add(project)
    }
    return [...projects].sort((left, right) => left.localeCompare(right))
  })

  const effectiveArtifacts = computed(() => {
    if (!destinationPlan.value || !preview.value) return []
    const plannedByIndex = new Map(
      destinationPlan.value.artifacts.map((artifact) => [artifact.index, artifact]),
    )
    return preview.value.artifacts.map((artifact) => {
      const planned = plannedByIndex.get(artifact.index)
      if (!planned) return artifact
      return {
        ...artifact,
        classification: planned.classification,
        target_digest: planned.target_digest,
        error_code: planned.error_code,
        message: planned.message,
        target_project: planned.target_project,
        target_repository: planned.target_repository,
        final_reference: planned.final_reference,
        project_exists: planned.project_exists,
        write_allowed: planned.write_allowed,
      }
    })
  })

  const conflicts = computed(
    () => effectiveArtifacts.value.filter((item) => item.classification === 'CONFLICT'),
  )
  const unresolved = computed(
    () =>
      effectiveArtifacts.value.filter(
        (item) => item.classification === 'UNKNOWN' || item.classification === 'ERROR',
      ),
  )
  const confirmedPlanReady = computed(
    () => destinationPlan.value !== null && destinationPlan.value.valid && !mappingDirty.value,
  )
  const canExecuteDefault = computed(
    () => confirmedPlanReady.value && conflicts.value.length === 0 && unresolved.value.length === 0,
  )
  const canExecuteOverwrite = computed(
    () =>
      confirmedPlanReady.value &&
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
  const harborProfileLocked = computed(() => operation.value !== null)

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

  function resetMapping(): void {
    mappingDraft.value = emptyMappingRequest()
    destinationPlan.value = null
    mappingDirty.value = true
    overwriteConfirmed.value = false
  }

  function markMappingDirty(): void {
    mappingDirty.value = true
    overwriteConfirmed.value = false
  }

  function setDefaultProject(
    kind: 'container-image' | 'helm-chart',
    targetProject: string | null,
  ): void {
    mappingDraft.value = {
      ...mappingDraft.value,
      [kind === 'container-image' ? 'container_image_project' : 'helm_chart_project']:
        targetProject || null,
    }
    markMappingDirty()
  }

  function setSourceProjectMapping(sourceProject: string, targetProject: string | null): void {
    const mappings = { ...mappingDraft.value.project_mappings }
    if (targetProject) mappings[sourceProject] = targetProject
    else delete mappings[sourceProject]
    mappingDraft.value = { ...mappingDraft.value, project_mappings: mappings }
    markMappingDirty()
  }

  function setArtifactOverride(index: number, targetProject: string | null): void {
    const overrides = mappingDraft.value.artifact_overrides.filter((item) => item.index !== index)
    if (targetProject) overrides.push({ index, target_project: targetProject })
    overrides.sort((left, right) => left.index - right.index)
    mappingDraft.value = { ...mappingDraft.value, artifact_overrides: overrides }
    markMappingDirty()
  }

  async function validateDestinationPlan(): Promise<boolean> {
    if (!operation.value || !preview.value || operation.value.status !== 'READY') return false
    busy.value = 'destination-plan'
    clearError()
    try {
      const profileId = operation.value.harbor_profile_id ?? selectedHarborProfileId.value
      if (!profileId) {
        error.value = {
          code: 'harbor_profile_required',
          message: 'Harbor profile не закреплён за import operation.',
        }
        return false
      }
      const plan = await buildImportDestinationPlan(operation.value.id, {
        ...mappingDraft.value,
        harbor_profile_id: profileId,
      })
      destinationPlan.value = plan
      mappingDirty.value = false
      overwriteConfirmed.value = false
      return plan.valid
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Не удалось проверить TARGET Harbor. Проверьте доступность Harbor, проекты назначения и права записи.',
      )
      return false
    } finally {
      busy.value = null
    }
  }

  async function loadPreview(operationId: number): Promise<void> {
    try {
      const loaded = await getImportPreview(operationId)
      if (preview.value?.operation_id !== loaded.operation_id) {
        resetMapping()
      }
      preview.value = loaded
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
    if (current.harbor_profile_id) {
      harborProfilesStore.select(current.harbor_profile_id)
      mappingDraft.value = {
        ...mappingDraft.value,
        harbor_profile_id: current.harbor_profile_id,
      }
    }

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
    resetMapping()
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
    resetMapping()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) {
        error.value = {
          code: 'harbor_profile_required',
          message: 'Выберите TARGET Harbor profile перед загрузкой.',
        }
        return false
      }
      const intake = await uploadImportBundle(
        file,
        (loaded, total) => {
          uploadProgress.value = { loaded, total: total ?? file.size }
        },
        undefined,
        profileId,
      )
      await selectOperation(intake.operation_id)
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(requestError, 'Не удалось принять bundle через браузер.')
      return false
    } finally {
      busy.value = null
    }
  }

  async function uploadPhysicalHandoff(files: BrowserPhysicalHandoffFiles): Promise<boolean> {
    const { bundle, sidecar, handoff } = files
    const deliveryId = bundle.name.replace(/\.htp\.tar\.gz$/, '')
    if (
      !bundle.name.endsWith('.htp.tar.gz') ||
      sidecar.name !== `${bundle.name}.sha256` ||
      handoff.name !== `${deliveryId}.htp-handoff.json`
    ) {
      error.value = {
        code: 'handoff_browser_files_mismatch',
        message: 'Выберите bundle, matching .sha256 и .htp-handoff.json одного Delivery ID.',
      }
      return false
    }

    selectedFile.value = { name: bundle.name, size: bundle.size }
    uploadProgress.value = { loaded: 0, total: bundle.size }
    busy.value = 'upload'
    clearError()
    preview.value = null
    receipt.value = null
    resetMapping()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) {
        error.value = {
          code: 'harbor_profile_required',
          message: 'Выберите TARGET Harbor profile перед загрузкой.',
        }
        return false
      }
      const intake = await uploadImportBundle(
        bundle,
        (loaded, total) => {
          uploadProgress.value = { loaded, total: total ?? bundle.size }
        },
        { sidecar, handoff },
        profileId,
      )
      await selectOperation(intake.operation_id)
      return true
    } catch (requestError) {
      error.value = apiErrorInfo(
        requestError,
        'Не удалось проверить и принять физическую поставку через браузер.',
      )
      return false
    } finally {
      busy.value = null
    }
  }

  async function discover(): Promise<void> {
    busy.value = 'discover'
    clearError()
    try {
      const profileId = selectedHarborProfileId.value
      if (!profileId) {
        error.value = {
          code: 'harbor_profile_required',
          message: 'Выберите TARGET Harbor profile перед discovery.',
        }
        return
      }
      const response = await discoverImportBundles(profileId)
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
    if (!destinationPlan.value || mappingDirty.value || !destinationPlan.value.valid) {
      error.value = {
        code: 'import_destination_plan_required',
        message: 'После изменения назначения сначала нажмите «Проверить TARGET».',
      }
      return false
    }
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
      await executeImport(operation.value.id, overwriteConflicts, destinationPlan.value.plan_id)
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

  async function loadHarborProfiles(): Promise<boolean> {
    const loaded = await harborProfilesStore.load()
    if (!loaded && harborProfilesError.value) {
      error.value = harborProfilesError.value
    }
    return loaded
  }

  function selectHarborProfile(profileId: string): void {
    if (harborProfileLocked.value || profileId === selectedHarborProfileId.value) return
    if (!harborProfilesStore.select(profileId)) return
    selectedFile.value = null
    uploadProgress.value = null
    discovered.value = []
    preview.value = null
    receipt.value = null
    resetMapping()
    mappingDraft.value = {
      ...mappingDraft.value,
      harbor_profile_id: profileId,
    }
    clearError()
  }

  async function initialize(): Promise<void> {
    busy.value = 'initialize'
    clearError()
    try {
      const profilesReady = await loadHarborProfiles()
      if (!profilesReady) return
      if (selectedHarborProfileId.value) {
        mappingDraft.value = {
          ...mappingDraft.value,
          harbor_profile_id: selectedHarborProfileId.value,
        }
      }
      const operationId = savedOperationId()
      if (operationId) {
        await selectOperation(operationId)
      }
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
    resetMapping()
    busy.value = null
    error.value = null
  }

  return {
    step,
    harborProfiles,
    selectedHarborProfileId,
    selectedHarborProfile,
    harborProfilesLoading,
    harborProfileLocked,
    selectedFile,
    uploadProgress,
    discovered,
    operation,
    preview,
    destinationPlan,
    mappingDraft,
    mappingDirty,
    receipt,
    overwriteConfirmed,
    busy,
    error,
    sourceProjects,
    effectiveArtifacts,
    conflicts,
    unresolved,
    confirmedPlanReady,
    canExecuteDefault,
    canExecuteOverwrite,
    canCancel,
    loadHarborProfiles,
    selectHarborProfile,
    upload,
    uploadPhysicalHandoff,
    discover,
    selectOperation,
    validateDestinationPlan,
    setDefaultProject,
    setSourceProjectMapping,
    setArtifactOverride,
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
