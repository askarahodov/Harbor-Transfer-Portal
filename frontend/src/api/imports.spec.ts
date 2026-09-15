import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import {
  buildImportDestinationPlan,
  executeImport,
  type ImportDestinationPlan,
  type ImportDestinationPlanRequest,
} from './imports'

const PLAN_ID = 'e'.repeat(64)

function destinationPlan(): ImportDestinationPlan {
  return {
    operation_id: 42,
    bundle_sha256: 'c'.repeat(64),
    plan_id: PLAN_ID,
    created_at: '2026-09-15T08:00:00Z',
    valid: true,
    artifacts: [],
  }
}

afterEach(() => vi.restoreAllMocks())

describe('import destination API contract', () => {
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
