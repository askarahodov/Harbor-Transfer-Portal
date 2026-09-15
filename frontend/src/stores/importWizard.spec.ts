import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as importsApi from '@/api/imports'
import type { ImportDestinationPlan, ImportPreview, Operation } from '@/api/imports'

import { useImportWizardStore } from './importWizard'

const SOURCE_DIGEST = `sha256:${'a'.repeat(64)}`
const TARGET_DIGEST = `sha256:${'b'.repeat(64)}`
const PLAN_ID = 'e'.repeat(64)

function operation(status: Operation['status'], id = 51): Operation {
  return {
    id,
    delivery_id: null,
    type: 'IMPORT',
    status,
    actor_username: 'operator',
    comment: null,
    started_at: '2026-09-14T05:00:00Z',
    finished_at: ['COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED'].includes(status)
      ? '2026-09-14T05:05:00Z'
      : null,
    error_code: status === 'REJECTED' ? 'bundle_signature_invalid' : null,
    error_message: status === 'REJECTED' ? 'Bundle signature verification failed' : null,
    cancel_requested: false,
    bundle: {
      filename: `delivery-${id}.htp.tar.gz`,
      size_bytes: 8192,
      sha256: 'c'.repeat(64),
    },
    progress: {
      total_artifacts: 1,
      completed_artifacts: ['COMPLETED', 'FAILED'].includes(status) ? 1 : 0,
      running_artifacts: status === 'IMPORTING' ? 1 : 0,
      successful_artifacts: status === 'COMPLETED' ? 1 : 0,
      failed_artifacts: status === 'FAILED' ? 1 : 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: ['COMPLETED', 'FAILED'].includes(status) ? 1 : 0,
      progress_total: 1,
      current_phase: status,
      running_artifact_ids: status === 'IMPORTING' ? [1] : [],
    },
    artifacts: [
      {
        id: 1,
        artifact_type: 'container-image',
        repository: 'project/app',
        name: null,
        reference: '1.0.0',
        version: null,
        source_digest: SOURCE_DIGEST,
        target_digest: status === 'COMPLETED' ? SOURCE_DIGEST : null,
        status: status === 'COMPLETED' ? 'VERIFIED' : status === 'FAILED' ? 'FAILED' : 'RUNNING',
        error_code: status === 'FAILED' ? 'skopeo_digest_mismatch' : null,
        error_message: status === 'FAILED' ? 'TARGET digest mismatch' : null,
        size_bytes: 4096,
        started_at: '2026-09-14T05:01:00Z',
        finished_at: ['COMPLETED', 'FAILED'].includes(status) ? '2026-09-14T05:05:00Z' : null,
      },
    ],
  }
}

function preview(classification: ImportPreview['artifacts'][number]['classification'] = 'NEW'): ImportPreview {
  return {
    operation_id: 51,
    status: 'READY',
    source_delivery_id: 'DELIVERY-20260914-IMPORT01',
    bundle_sha256: 'c'.repeat(64),
    bundle_size_bytes: 8192,
    signing_key_fingerprint: 'd'.repeat(64),
    verified_at: '2026-09-14T05:02:00Z',
    bundle_filename: 'delivery.htp.tar.gz',
    intake_mode: 'upload',
    source_harbor: 'harbor.source.local',
    source_portal_version: '1.0.0',
    source_created_at: '2026-09-14T04:00:00Z',
    source_created_by: 'source-operator',
    source_comment: 'offline delivery',
    checksum_verified: true,
    signature_verified: true,
    schema_verified: true,
    overwrite_allowed: true,
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        repository: 'project/app',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: SOURCE_DIGEST,
        target_digest: classification === 'CONFLICT' ? TARGET_DIGEST : null,
        payload_size: 4096,
        classification,
        error_code: null,
        message: null,
      },
    ],
  }
}

function destinationPlan(
  classification: ImportPreview['artifacts'][number]['classification'] = 'NEW',
  planId = PLAN_ID,
): ImportDestinationPlan {
  return {
    operation_id: 51,
    bundle_sha256: 'c'.repeat(64),
    plan_id: planId,
    created_at: '2026-09-14T05:02:30Z',
    valid: ['NEW', 'SAME', 'CONFLICT'].includes(classification),
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        source_repository: 'project/app',
        source_project: 'project',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: SOURCE_DIGEST,
        payload_size: 4096,
        target_project: 'target',
        target_repository: 'target/app',
        final_reference: 'harbor.target.local/target/app:1.0.0',
        project_exists: true,
        write_allowed: true,
        target_digest: classification === 'CONFLICT' ? TARGET_DIGEST : null,
        classification,
        error_code: null,
        message: null,
      },
    ],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  sessionStorage.clear()
})

describe('import wizard store', () => {
  it('uploads a raw bundle, persists operation id and opens verified preview with import fail-closed', async () => {
    const uploadSpy = vi.spyOn(importsApi, 'uploadImportBundle').mockResolvedValue({
      operation_id: 51,
      status: 'UPLOADED',
      intake_mode: 'upload',
    })
    vi.spyOn(importsApi, 'getOperation').mockResolvedValue(operation('READY'))
    vi.spyOn(importsApi, 'getImportPreview').mockResolvedValue(preview())
    const store = useImportWizardStore()
    const file = new File(['bundle'], 'transfer.htp.tar.gz', { type: 'application/gzip' })

    expect(await store.upload(file)).toBe(true)

    expect(uploadSpy).toHaveBeenCalledWith(file, expect.any(Function))
    expect(store.selectedFile).toEqual({ name: 'transfer.htp.tar.gz', size: 6 })
    expect(sessionStorage.getItem('htp.import.operation-id')).toBe('51')
    expect(store.step).toBe(2)
    expect(store.preview?.signature_verified).toBe(true)
    expect(store.preview?.source_harbor).toBe('harbor.source.local')
    expect(store.sourceProjects).toEqual(['project'])
    expect(store.mappingDirty).toBe(true)
    expect(store.confirmedPlanReady).toBe(false)
    expect(store.canExecuteDefault).toBe(false)
  })

  it('discovers claimed incoming bundles and exposes them for explicit selection', async () => {
    vi.spyOn(importsApi, 'discoverImportBundles').mockResolvedValue({
      operations: [
        { operation_id: 61, status: 'DISCOVERED', intake_mode: 'incoming' },
        { operation_id: 62, status: 'DISCOVERED', intake_mode: 'incoming' },
      ],
    })
    vi.spyOn(importsApi, 'getOperation').mockImplementation(async (id) => operation('DISCOVERED', id))
    vi.useFakeTimers()
    const store = useImportWizardStore()

    await store.discover()

    expect(store.discovered.map((item) => item.intake.operation_id)).toEqual([61, 62])
    expect(store.operation).toBeNull()
    await store.selectOperation(62)
    expect(store.operation?.id).toBe(62)
    expect(sessionStorage.getItem('htp.import.operation-id')).toBe('62')
    store.stopPolling()
  })

  it('keeps the last confirmed plan on validation failure but blocks a dirty mapping', async () => {
    const planSpy = vi
      .spyOn(importsApi, 'buildImportDestinationPlan')
      .mockResolvedValueOnce(destinationPlan())
      .mockRejectedValueOnce(new Error('TARGET validation unavailable'))
    const store = useImportWizardStore()
    store.operation = operation('READY')
    store.preview = preview()
    store.setDefaultProject('container-image', 'target')

    expect(await store.validateDestinationPlan()).toBe(true)
    expect(store.confirmedPlanReady).toBe(true)
    expect(store.destinationPlan?.plan_id).toBe(PLAN_ID)

    store.setSourceProjectMapping('project', 'other-target')
    expect(store.mappingDirty).toBe(true)
    expect(store.destinationPlan?.plan_id).toBe(PLAN_ID)
    expect(store.canExecuteDefault).toBe(false)

    expect(await store.validateDestinationPlan()).toBe(false)
    expect(planSpy).toHaveBeenCalledTimes(2)
    expect(store.destinationPlan?.plan_id).toBe(PLAN_ID)
    expect(store.mappingDirty).toBe(true)
    expect(store.error?.message).toContain('destination mapping')
  })

  it('blocks conflict by default and executes only with confirmed exact plan id plus overwrite approval', async () => {
    sessionStorage.setItem('htp.import.operation-id', '51')
    vi.spyOn(importsApi, 'getOperation')
      .mockResolvedValueOnce(operation('READY'))
      .mockResolvedValueOnce(operation('IMPORTING'))
    vi.spyOn(importsApi, 'getImportPreview').mockResolvedValue(preview('CONFLICT'))
    vi.spyOn(importsApi, 'buildImportDestinationPlan').mockResolvedValue(
      destinationPlan('CONFLICT'),
    )
    const executeSpy = vi.spyOn(importsApi, 'executeImport').mockResolvedValue({
      operation_id: 51,
      status: 'IMPORTING',
    })
    vi.useFakeTimers()
    const store = useImportWizardStore()

    await store.initialize()
    store.setDefaultProject('container-image', 'target')
    expect(await store.validateDestinationPlan()).toBe(true)
    expect(store.conflicts).toHaveLength(1)
    expect(store.canExecuteDefault).toBe(false)
    expect(await store.execute(false)).toBe(false)
    expect(executeSpy).not.toHaveBeenCalled()

    store.overwriteConfirmed = true
    expect(store.canExecuteOverwrite).toBe(true)
    expect(await store.execute(true)).toBe(true)
    expect(executeSpy).toHaveBeenCalledWith(51, true, PLAN_ID)
    expect(store.step).toBe(3)
    store.stopPolling()
  })

  it('restores active import after reload and loads destination-aware receipt on partial failure', async () => {
    sessionStorage.setItem('htp.import.operation-id', '51')
    vi.spyOn(importsApi, 'getOperation')
      .mockResolvedValueOnce(operation('IMPORTING'))
      .mockResolvedValueOnce(operation('FAILED'))
    vi.spyOn(importsApi, 'getImportReceipt').mockResolvedValue({
      operation_id: 51,
      source_delivery_id: 'DELIVERY-20260914-IMPORT01',
      bundle_sha256: 'c'.repeat(64),
      actor_username: 'operator',
      started_at: '2026-09-14T05:03:00Z',
      finished_at: '2026-09-14T05:05:00Z',
      overwrite_conflicts: false,
      destination_plan_id: PLAN_ID,
      result: 'FAILED',
      artifacts: [],
    })
    vi.useFakeTimers()
    const store = useImportWizardStore()

    await store.initialize()
    expect(store.step).toBe(3)
    expect(store.operation?.status).toBe('IMPORTING')
    store.stopPolling()

    await store.refreshOperation(51)
    expect(store.operation?.status).toBe('FAILED')
    expect(store.receipt?.result).toBe('FAILED')
    expect(store.receipt?.destination_plan_id).toBe(PLAN_ID)
  })
})
