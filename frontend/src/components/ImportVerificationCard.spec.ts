import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ImportPreview, Operation } from '@/api/imports'
import ImportVerificationCard from './ImportVerificationCard.vue'

function operation(status: Operation['status']): Operation {
  return {
    id: 42,
    delivery_id: 'DELIVERY-1',
    type: 'IMPORT',
    status,
    actor_username: 'operator',
    comment: null,
    started_at: null,
    finished_at: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    bundle: { filename: 'bundle.htp.tar.gz', size_bytes: 4096, sha256: 'a'.repeat(64) },
    progress: {
      total_artifacts: 0,
      completed_artifacts: 0,
      running_artifacts: 0,
      successful_artifacts: 0,
      failed_artifacts: 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: 0,
      progress_total: 0,
      current_phase: status,
      running_artifact_ids: [],
    },
    artifacts: [],
  }
}

function preview(): ImportPreview {
  return {
    operation_id: 42,
    status: 'READY',
    source_delivery_id: 'DELIVERY-1',
    bundle_sha256: 'a'.repeat(64),
    bundle_size_bytes: 4096,
    signing_key_fingerprint: 'b'.repeat(64),
    verified_at: '2026-09-22T07:00:00Z',
    bundle_filename: 'bundle.htp.tar.gz',
    intake_mode: 'upload',
    source_harbor: 'harbor.local',
    source_portal_version: '1.0.0',
    source_created_at: null,
    source_created_by: null,
    source_comment: null,
    checksum_verified: true,
    signature_verified: true,
    schema_verified: true,
    overwrite_allowed: false,
    artifacts: [],
  }
}

describe('ImportVerificationCard', () => {
  it('shows live verification state and emits refresh/cancel', async () => {
    const wrapper = mount(ImportVerificationCard, {
      props: {
        operation: operation('VERIFYING'),
        preview: null,
        phaseLabel: 'Криптографическая проверка пакета',
        activeFilename: 'bundle.htp.tar.gz',
        activeSize: 4096,
        canCancel: true,
        busy: false,
      },
    })

    expect(wrapper.get('[role="status"]').attributes('aria-live')).toBe('polite')
    expect(wrapper.text()).toContain('проверяется')
    await wrapper.get('button[aria-label="Обновить состояние операции"]').trigger('click')
    await wrapper.get('button.button--danger').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('projects verified backend flags without doing client-side verification', () => {
    const wrapper = mount(ImportVerificationCard, {
      props: {
        operation: operation('READY'),
        preview: preview(),
        phaseLabel: 'Готово к импорту',
        activeFilename: 'bundle.htp.tar.gz',
        activeSize: 4096,
        canCancel: false,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('подтверждена')
    expect(wrapper.text()).toContain('совместима')
    expect(wrapper.text()).toContain('подпись доверена')
    expect(wrapper.find('button.button--danger').exists()).toBe(false)
  })

  it('shows rejection without exposing a cancel action', () => {
    const wrapper = mount(ImportVerificationCard, {
      props: {
        operation: operation('REJECTED'),
        preview: null,
        phaseLabel: 'Пакет отклонён',
        activeFilename: 'bundle.htp.tar.gz',
        activeSize: 4096,
        canCancel: false,
        busy: false,
      },
    })

    expect(wrapper.text()).toContain('не подтверждена')
    expect(wrapper.find('button.button--danger').exists()).toBe(false)
  })
})
