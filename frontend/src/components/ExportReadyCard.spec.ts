import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExportBundle, Operation } from '@/api/exports'
import ExportReadyCard from './ExportReadyCard.vue'

const bundle: ExportBundle = {
  operation_id: 42,
  delivery_id: 'DELIVERY-1',
  archive_name: 'DELIVERY-1.htp.tar.gz',
  archive_size: 8192,
  sha256: 'a'.repeat(64),
  download_url: '/api/exports/42/download',
}

const operation: Operation = {
  id: 42,
  delivery_id: 'DELIVERY-1',
  type: 'EXPORT',
  status: 'COMPLETED',
  actor_username: 'operator',
  comment: null,
  started_at: null,
  finished_at: null,
  error_code: null,
  error_message: null,
  cancel_requested: false,
  bundle: { filename: bundle.archive_name, size_bytes: bundle.archive_size, sha256: bundle.sha256 },
  progress: {
    total_artifacts: 2,
    completed_artifacts: 2,
    running_artifacts: 0,
    successful_artifacts: 2,
    failed_artifacts: 0,
    skipped_artifacts: 0,
    conflict_artifacts: 0,
    progress_current: 2,
    progress_total: 2,
    current_phase: 'COMPLETED',
    running_artifact_ids: [],
  },
  artifacts: [],
}

describe('ExportReadyCard', () => {
  it('disables transfer actions until bundle metadata exists', () => {
    const wrapper = mount(ExportReadyCard, {
      props: {
        bundle: null,
        operation,
        downloadError: null,
        printHandoffBusy: false,
      },
    })

    expect(wrapper.findAll('.download-actions button').every((button) => button.attributes('disabled') !== undefined)).toBe(true)
  })

  it('emits explicit transfer actions for a ready bundle', async () => {
    const wrapper = mount(ExportReadyCard, {
      props: {
        bundle,
        operation,
        downloadError: null,
        printHandoffBusy: false,
      },
    })

    const buttons = wrapper.findAll('.download-actions button')
    await buttons[0]!.trigger('click')
    await buttons[1]!.trigger('click')
    await buttons[2]!.trigger('click')
    await buttons[3]!.trigger('click')
    await wrapper.get('button.text-button').trigger('click')

    expect(wrapper.emitted('downloadBundle')).toHaveLength(1)
    expect(wrapper.emitted('downloadSidecar')).toHaveLength(1)
    expect(wrapper.emitted('downloadHandoff')).toHaveLength(1)
    expect(wrapper.emitted('printHandoff')).toHaveLength(1)
    expect(wrapper.emitted('reset')).toHaveLength(1)
    expect(wrapper.text()).toContain('DELIVERY-1.htp.tar.gz')
    expect(wrapper.text()).toContain('Проверено artifacts: 2 / 2')
  })

  it('surfaces download errors and disables print while handoff is prepared', () => {
    const wrapper = mount(ExportReadyCard, {
      props: {
        bundle,
        operation,
        downloadError: 'download failed',
        printHandoffBusy: true,
      },
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('download failed')
    expect(wrapper.findAll('.download-actions button')[3]!.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Подготовка…')
  })
})
