import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import {
  buildImportDestinationPlan,
  executeImport,
  uploadImportBundle,
  type ImportDestinationPlan,
  type ImportDestinationPlanRequest,
} from './imports'

const PLAN_ID = 'e'.repeat(64)
const PLAN_HASH = 'f'.repeat(64)

function destinationPlan(): ImportDestinationPlan {
  return {
    operation_id: 42,
    source_delivery_id: 'DELIVERY-42',
    actor_username: 'operator',
    bundle_sha256: 'c'.repeat(64),
    plan_id: PLAN_ID,
    plan_hash: PLAN_HASH,
    created_at: '2026-09-15T08:00:00Z',
    valid: true,
    artifacts: [],
  }
}

afterEach(() => vi.restoreAllMocks())

describe('import destination API contract', () => {

  it('streams bundle with physical handoff companion headers', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { operation_id: 77, status: 'UPLOADED', intake_mode: 'upload' },
    })
    const bundle = new File(['bundle'], 'DELIVERY-20260921-ABC123.htp.tar.gz', {
      type: 'application/gzip',
    })
    const sidecar = new File(['sha256-line\n'], `${bundle.name}.sha256`, {
      type: 'text/plain',
    })
    const handoff = new File(
      ['{"kind":"harbor-transfer-portal-physical-handoff"}\n'],
      'DELIVERY-20260921-ABC123.htp-handoff.json',
      { type: 'application/json' },
    )

    await uploadImportBundle(bundle, sidecar, handoff)

    expect(post).toHaveBeenCalledOnce()
    const [url, body, config] = post.mock.calls[0]!
    expect(url).toBe('/imports/upload')
    expect(body).toBe(bundle)
    expect(config?.headers).toMatchObject({
      'Content-Type': 'application/gzip',
      'X-HTP-Bundle-Filename': bundle.name,
      'X-HTP-Sidecar-B64': window.btoa('sha256-line\n'),
      'X-HTP-Handoff-B64': window.btoa(
        '{"kind":"harbor-transfer-portal-physical-handoff"}\n',
      ),
    })
  })

  it('sends mapping through the destination-plan PUT endpoint unchanged', async () => {
    const mapping: ImportDestinationPlanRequest = {
      container_image_project: 'docker-target',
      helm_chart_project: 'helm-target',
      project_mappings: { source: 'mapped-target' },
      artifact_overrides: [{ index: 3, target_project: 'override-target' }],
    }
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: destinationPlan() })

    expect(await buildImportDestinationPlan(42, mapping)).toEqual(destinationPlan())
    expect(put).toHaveBeenCalledWith('/imports/42/destination-plan', mapping)
  })

  it('binds execute to the exact confirmed destination plan id', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { operation_id: 42, status: 'IMPORTING' },
    })

    await executeImport(42, true, PLAN_ID)

    expect(post).toHaveBeenCalledWith('/imports/42/execute', {
      overwrite_conflicts: true,
      destination_plan_id: PLAN_ID,
    })
  })
})
