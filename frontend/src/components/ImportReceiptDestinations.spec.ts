import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ImportReceipt } from '@/api/imports'

import ImportReceiptDestinations from './ImportReceiptDestinations.vue'

function receipt(): ImportReceipt {
  return {
    operation_id: 77,
    source_delivery_id: 'SOURCE-77',
    bundle_sha256: 'c'.repeat(64),
    actor_username: 'operator',
    started_at: '2026-09-15T08:00:00Z',
    finished_at: '2026-09-15T08:02:00Z',
    overwrite_conflicts: false,
    destination_plan_id: 'e'.repeat(64),
    destination_plan_hash: 'f'.repeat(64),
    result: 'COMPLETED',
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        repository: 'source/app',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: `sha256:${'a'.repeat(64)}`,
        target_digest: `sha256:${'a'.repeat(64)}`,
        target_repository: 'target/app',
        final_reference: 'harbor.target.local/target/app:1.0.0',
        status: 'VERIFIED',
        error_code: null,
        error_message: null,
      },
      {
        index: 1,
        artifact_type: 'helm-chart',
        repository: 'source/charts',
        name: 'portal',
        reference: null,
        version: '2.0.0',
        expected_digest: `sha256:${'b'.repeat(64)}`,
        target_digest: null,
        target_repository: 'target-charts/charts',
        final_reference: null,
        status: 'FAILED',
        error_code: 'helm_push_failed',
        error_message: 'push failed',
      },
    ],
  }
}

describe('ImportReceiptDestinations', () => {
  it('shows persisted final reference, repository fallback and destination plan id', () => {
    const wrapper = mount(ImportReceiptDestinations, { props: { receipt: receipt() } })

    expect(wrapper.text()).toContain('Фактические TARGET destinations')
    expect(wrapper.text()).toContain('plan eeeeeeeeeeee…')
    expect(wrapper.text()).toContain('harbor.target.local/target/app:1.0.0')
    expect(wrapper.text()).toContain('target-charts/charts')
    expect(wrapper.text()).toContain('VERIFIED')
    expect(wrapper.text()).toContain('FAILED')
  })
})
