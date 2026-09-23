import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import type { HarborArtifact, HarborProject, Operation, PageResponse } from '@/api/exports'

import { useExportWizardStore } from './exportWizard'

const DIGEST = `sha256:${'a'.repeat(64)}`

function artifact(reference = '1.0.0'): HarborArtifact {
  return {
    kind: 'container-image',
    project: 'team',
    repository: 'apps/demo',
    references: [reference],
    digest: DIGEST,
    size: 4096,
    pushed_at: null,
    media_type: null,
    artifact_type: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

function operation(status: Operation['status']): Operation {
  return {
    id: 42,
    delivery_id: 'DELIVERY-20260911-ABCDEF',
    type: 'EXPORT',
    status,
    actor_username: 'operator',
    comment: null,
    started_at: '2026-09-11T20:00:00Z',
    finished_at: status === 'COMPLETED' ? '2026-09-11T20:01:00Z' : null,
    error_code: status === 'FAILED' ? 'export_source_changed' : null,
    error_message: status === 'FAILED' ? 'SOURCE artifact digest изменился' : null,
    cancel_requested: false,
    bundle:
      status === 'COMPLETED'
        ? { filename: 'DELIVERY-20260911-ABCDEF.htp.tar.gz', size_bytes: 8192, sha256: 'b'.repeat(64) }
        : null,
    progress: {
      total_artifacts: 1,
      completed_artifacts: status === 'COMPLETED' || status === 'FAILED' ? 1 : 0,
      running_artifacts: status === 'RUNNING' ? 1 : 0,
      successful_artifacts: status === 'COMPLETED' ? 1 : 0,
      failed_artifacts: status === 'FAILED' ? 1 : 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: status === 'COMPLETED' ? 1 : 0,
      progress_total: 1,
      current_phase: status,
      running_artifact_ids: status === 'RUNNING' ? [1] : [],
    },
    artifacts: [
      {
        id: 1,
        artifact_type: 'container-image',
        repository: 'team/apps/demo',
        name: null,
        reference: '1.0.0',
        version: null,
        source_digest: DIGEST,
        target_digest: null,
        status: status === 'COMPLETED' ? 'VERIFIED' : status === 'FAILED' ? 'FAILED' : 'RUNNING',
        error_code: status === 'FAILED' ? 'export_source_changed' : null,
        error_message: status === 'FAILED' ? 'SOURCE artifact digest изменился' : null,
        size_bytes: 4096,
        started_at: '2026-09-11T20:00:01Z',
        finished_at: status === 'RUNNING' ? null : '2026-09-11T20:01:00Z',
      },
    ],
  }
}

function mockBrowse(): void {
  vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
    items: [
      {
        id: 'default',
        name: 'Default Harbor',
        url: 'https://harbor.local',
        is_default: true,
      },
      {
        id: 'profile-b',
        name: 'Harbor B',
        url: 'https://harbor-b.local',
        is_default: false,
      },
    ],
  })
  vi.spyOn(exportsApi, 'getHarborConnection').mockResolvedValue({
    connected: true,
    version: '2.14.0',
    auth_mode: 'db_auth',
  })
  vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [{ name: 'team', public: false }],
  })
  vi.spyOn(exportsApi, 'listHarborRepositories').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [{ name: 'apps/demo', artifact_count: 2, pull_count: 0 }],
  })
  vi.spyOn(exportsApi, 'listHarborArtifacts').mockResolvedValue({
    pagination: { page: 1, page_size: 25, total: 1 },
    items: [artifact()],
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
  mockBrowse()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  sessionStorage.clear()
})

describe('export wizard store', () => {
  it('preserves exact selection while browse results change and sends digest to preview', async () => {
    const previewSpy = vi.spyOn(exportsApi, 'previewExport').mockResolvedValue({
      artifacts: [
        {
          kind: 'container-image',
          project: 'team',
          repository: 'apps/demo',
          reference: '1.0.0',
          source_digest: DIGEST,
          size_bytes: 4096,
        },
      ],
      estimated_payload_bytes: 4096,
    })
    const store = useExportWizardStore()

    await store.initialize()
    await store.chooseProject('team')
    await store.chooseRepository('apps/demo')
    store.toggleArtifact(store.artifacts[0]!, '1.0.0')

    vi.mocked(exportsApi.listHarborArtifacts).mockResolvedValueOnce({
      pagination: { page: 2, page_size: 25, total: 26 },
      items: [artifact('2.0.0')],
    })
    await store.loadArtifacts(2)

    expect(store.selectedCount).toBe(1)
    expect(store.selectedArtifacts[0]?.reference).toBe('1.0.0')
    expect(store.selectedKnownBytes).toBe(4096)

    expect(await store.preparePreview()).toBe(true)
    expect(store.step).toBe(2)
    expect(previewSpy).toHaveBeenCalledWith({
      artifacts: [
        {
          kind: 'container-image',
          project: 'team',
          repository: 'apps/demo',
          reference: '1.0.0',
          digest: DIGEST,
        },
      ],
      comment: null,
      harbor_profile_id: 'default',
    })
  })

  it('clears dependent SOURCE selection when Harbor profile changes', async () => {
    const store = useExportWizardStore()
    await store.initialize()
    await store.chooseProject('team')
    await store.chooseRepository('apps/demo')
    store.toggleArtifact(store.artifacts[0]!, '1.0.0')

    const connectionSpy = vi.mocked(exportsApi.getHarborConnection)
    const projectsSpy = vi.mocked(exportsApi.listHarborProjects)
    connectionSpy.mockClear()
    projectsSpy.mockClear()

    await store.selectHarborProfile('profile-b')

    expect(store.selectedHarborProfileId).toBe('profile-b')
    expect(store.selectedProject).toBeNull()
    expect(store.selectedRepository).toBeNull()
    expect(store.selectedCount).toBe(0)
    expect(store.preview).toBeNull()
    expect(connectionSpy).toHaveBeenCalledWith('profile-b')
    expect(projectsSpy).toHaveBeenCalledWith(1, 25, '', 'profile-b')
    expect(sessionStorage.getItem('htp.harbor.profile-id')).toBe('profile-b')
  })

  it('deduplicates aliases that resolve to the same immutable digest', () => {
    const store = useExportWizardStore()
    const aliased = artifact('1.0.0')
    aliased.references = ['1.0.0', 'stable']

    store.toggleArtifact(aliased, '1.0.0')
    expect(store.selectedCount).toBe(1)
    expect(store.isSelected(aliased, 'stable')).toBe(true)

    store.toggleArtifact(aliased, 'stable')
    expect(store.selectedCount).toBe(0)
  })

  it('adds multi-selected aliases idempotently by immutable digest', () => {
    const store = useExportWizardStore()
    const aliased = artifact('1.0.0')
    aliased.references = ['1.0.0', 'stable']

    store.addArtifact(aliased, '1.0.0')
    store.addArtifact(aliased, 'stable')

    expect(store.selectedCount).toBe(1)
    expect(store.selectedArtifacts[0]?.reference).toBe('stable')

    const second = artifact('2.0.0')
    second.digest = `sha256:${'b'.repeat(64)}`
    store.addArtifact(second, '2.0.0')

    expect(store.selectedCount).toBe(2)
    expect(store.selectedArtifacts.map((item) => item.digest)).toEqual([
      DIGEST,
      second.digest,
    ])
  })

  it('keeps unknown OCI references visible while selection remains fail-closed', () => {
    const store = useExportWizardStore()
    const unknown: HarborArtifact = {
      kind: 'unknown-oci',
      project: 'team',
      repository: 'apps/opaque',
      references: ['release-2026.09', 'latest'],
      digest: DIGEST,
      size: 1024,
      pushed_at: null,
      media_type: 'application/vnd.example.unknown',
      artifact_type: 'application/vnd.example.unknown',
    }

    expect(store.referencesFor(unknown)).toEqual(['release-2026.09', 'latest'])
    expect(store.isSelected(unknown, 'release-2026.09')).toBe(false)

    store.toggleArtifact(unknown, 'release-2026.09')

    expect(store.selectedCount).toBe(0)
    expect(store.selectedArtifacts).toEqual([])
  })

  it('debounces all Harbor search inputs and resets pagination before requesting', async () => {
    vi.useFakeTimers()
    const store = useExportWizardStore()
    await store.initialize()
    await store.chooseProject('team')
    await store.chooseRepository('apps/demo')

    const projectsSpy = vi.mocked(exportsApi.listHarborProjects)
    const repositoriesSpy = vi.mocked(exportsApi.listHarborRepositories)
    const artifactsSpy = vi.mocked(exportsApi.listHarborArtifacts)
    projectsSpy.mockClear()
    repositoriesSpy.mockClear()
    artifactsSpy.mockClear()

    store.projectPage = 4
    store.repositoryPage = 3
    store.artifactPage = 2
    store.projectSearch = 'report'
    store.repositorySearch = 'api'
    store.artifactSearch = '1.2.3'

    expect(store.projectPage).toBe(1)
    expect(store.repositoryPage).toBe(1)
    expect(store.artifactPage).toBe(1)
    expect(projectsSpy).not.toHaveBeenCalled()
    expect(repositoriesSpy).not.toHaveBeenCalled()
    expect(artifactsSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(299)
    expect(projectsSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    await Promise.resolve()

    expect(projectsSpy).toHaveBeenCalledTimes(1)
    expect(projectsSpy).toHaveBeenCalledWith(1, 25, 'report', 'default')
    expect(repositoriesSpy).toHaveBeenCalledTimes(1)
    expect(repositoriesSpy).toHaveBeenCalledWith('team', 1, 25, 'api', 'default')
    expect(artifactsSpy).toHaveBeenCalledTimes(1)
    expect(artifactsSpy).toHaveBeenCalledWith('team', 'apps/demo', 1, 25, '1.2.3', 'default')
  })

  it('applies only the latest project response and ignores stale success and error', async () => {
    const oldSuccess = deferred<PageResponse<HarborProject>>()
    const staleFailure = deferred<PageResponse<HarborProject>>()
    const latestSuccess = deferred<PageResponse<HarborProject>>()
    const projectsSpy = vi.mocked(exportsApi.listHarborProjects)
    projectsSpy.mockReset()
    projectsSpy
      .mockImplementationOnce(() => oldSuccess.promise)
      .mockImplementationOnce(() => staleFailure.promise)
      .mockImplementationOnce(() => latestSuccess.promise)

    const store = useExportWizardStore()
    await store.loadHarborProfiles()

    store.projectSearch = 'old'
    const oldRequest = store.loadProjects(1)
    store.projectSearch = 'error'
    const failureRequest = store.loadProjects(1)
    store.projectSearch = 'latest'
    const latestRequest = store.loadProjects(1)

    latestSuccess.resolve({
      pagination: { page: 1, page_size: 25, total: 1 },
      items: [{ name: 'latest-result', public: false }],
    })
    await latestRequest

    oldSuccess.resolve({
      pagination: { page: 1, page_size: 25, total: 1 },
      items: [{ name: 'stale-result', public: true }],
    })
    staleFailure.reject(new Error('stale request failed'))
    await Promise.all([oldRequest, failureRequest])

    expect(store.projects).toEqual([{ name: 'latest-result', public: false }])
    expect(store.projectPage).toBe(1)
    expect(store.projectTotal).toBe(1)
    expect(store.error).toBeNull()
    expect(store.busy).toBeNull()
  })

  it('moves from preview to completed bundle and persists operation id for reload', async () => {
    vi.spyOn(exportsApi, 'previewExport').mockResolvedValue({
      artifacts: [
        {
          kind: 'container-image',
          project: 'team',
          repository: 'apps/demo',
          reference: '1.0.0',
          source_digest: DIGEST,
          size_bytes: 4096,
        },
      ],
      estimated_payload_bytes: 4096,
    })
    vi.spyOn(exportsApi, 'startExport').mockResolvedValue({
      operation_id: 42,
      delivery_id: 'DELIVERY-20260911-ABCDEF',
      status: 'CREATED',
    })
    vi.spyOn(exportsApi, 'getOperation').mockResolvedValue(operation('COMPLETED'))
    vi.spyOn(exportsApi, 'getExportBundle').mockResolvedValue({
      operation_id: 42,
      delivery_id: 'DELIVERY-20260911-ABCDEF',
      archive_name: 'DELIVERY-20260911-ABCDEF.htp.tar.gz',
      archive_size: 8192,
      sha256: 'b'.repeat(64),
      download_url: '/api/exports/42/download',
    })
    const store = useExportWizardStore()

    await store.initialize()
    await store.chooseProject('team')
    await store.chooseRepository('apps/demo')
    store.toggleArtifact(store.artifacts[0]!, '1.0.0')
    await store.preparePreview()
    store.comment = 'release candidate'

    expect(await store.start()).toBe(true)
    expect(store.step).toBe(4)
    expect(store.bundle?.archive_size).toBe(8192)
    expect(sessionStorage.getItem('htp.export.operation-id')).toBe('42')
  })

  it('reopens an active operation from session storage after refresh', async () => {
    sessionStorage.setItem('htp.export.operation-id', '42')
    vi.spyOn(exportsApi, 'getOperation').mockResolvedValue(operation('RUNNING'))
    vi.useFakeTimers()
    const store = useExportWizardStore()

    expect(await store.resumeSavedOperation()).toBe(true)
    expect(store.step).toBe(3)
    expect(store.operation?.status).toBe('RUNNING')
    store.stopPolling()
  })

  it('exposes failure details and cancellation without creating a ready bundle', async () => {
    const cancelSpy = vi
      .spyOn(exportsApi, 'cancelOperation')
      .mockResolvedValue({ ...operation('RUNNING'), status: 'CANCELLED' })
    const store = useExportWizardStore()
    sessionStorage.setItem('htp.export.operation-id', '42')
    vi.spyOn(exportsApi, 'getOperation').mockResolvedValue(operation('RUNNING'))
    await store.resumeSavedOperation()

    expect(store.canCancel).toBe(true)
    await store.cancel()

    expect(cancelSpy).toHaveBeenCalledWith(42)
    expect(store.operation?.status).toBe('CANCELLED')
    expect(store.bundle).toBeNull()
  })
})