import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as harborProjectsApi from '@/api/harborProjects'
import * as importsApi from '@/api/imports'
import type { ImportDestinationPlan, ImportPreview, Operation } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'

import HarborProjectCreationPanel from './HarborProjectCreationPanel.vue'

const DIGEST = `sha256:${'a'.repeat(64)}`

function operation(): Operation {
  return {
    id: 91,
    delivery_id: null,
    type: 'IMPORT',
    status: 'READY',
    actor_username: 'operator',
    comment: null,
    started_at: null,
    finished_at: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    bundle: null,
    progress: {
      total_artifacts: 1,
      completed_artifacts: 0,
      running_artifacts: 0,
      successful_artifacts: 0,
      failed_artifacts: 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: 0,
      progress_total: 1,
      current_phase: 'READY',
      running_artifact_ids: [],
    },
    artifacts: [],
  }
}

function preview(): ImportPreview {
  return {
    operation_id: 91,
    status: 'READY',
    source_delivery_id: 'DELIVERY-PROJECT-CREATE',
    bundle_sha256: 'c'.repeat(64),
    bundle_size_bytes: 4096,
    signing_key_fingerprint: 'd'.repeat(64),
    verified_at: '2026-09-15T10:00:00Z',
    checksum_verified: true,
    signature_verified: true,
    schema_verified: true,
    overwrite_allowed: false,
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        repository: 'source/app',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: DIGEST,
        target_digest: null,
        payload_size: 4096,
        classification: 'NEW',
        error_code: null,
        message: null,
      },
    ],
  }
}

function destinationPlan(projectExists: boolean): ImportDestinationPlan {
  return {
    operation_id: 91,
    source_delivery_id: 'DELIVERY-PROJECT-CREATE',
    actor_username: 'operator',
    bundle_sha256: 'c'.repeat(64),
    plan_id: 'e'.repeat(64),
    plan_hash: 'f'.repeat(64),
    mapping_policy_revision: 1,
    created_at: '2026-09-15T10:01:00Z',
    valid: projectExists,
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        source_repository: 'source/app',
        source_project: 'source',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: DIGEST,
        payload_size: 4096,
        target_project: 'docker-prod',
        target_repository: 'docker-prod/app',
        final_reference: 'harbor.target.local/docker-prod/app:1.0.0',
        project_exists: projectExists,
        write_allowed: projectExists,
        target_digest: null,
        classification: projectExists ? 'NEW' : 'ERROR',
        error_code: projectExists ? null : 'import_destination_project_missing',
        message: projectExists ? null : 'missing project',
      },
    ],
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('HarborProjectCreationPanel', () => {
  it('requires exact admin confirmation, creates private project and revalidates plan', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'admin', role: 'admin', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()
    wizard.destinationPlan = destinationPlan(false)
    wizard.mappingDirty = false

    const createSpy = vi.spyOn(harborProjectsApi, 'createHarborProject').mockResolvedValue({
      name: 'docker-prod',
      public: false,
      created: true,
    })
    const planSpy = vi
      .spyOn(importsApi, 'buildImportDestinationPlan')
      .mockResolvedValue(destinationPlan(true))

    const wrapper = mount(HarborProjectCreationPanel)
    const button = wrapper.get('button.project-create__button')
    expect(button.attributes('disabled')).toBeDefined()

    await wrapper.get('input[aria-label="Подтверждение создания project docker-prod"]').setValue('docker')
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('input[aria-label="Подтверждение создания project docker-prod"]').setValue('docker-prod')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await flushPromises()

    expect(createSpy).toHaveBeenCalledWith({
      name: 'docker-prod',
      public: false,
      operation_id: 91,
    })
    expect(planSpy).toHaveBeenCalledTimes(1)
    expect(wizard.destinationPlan?.valid).toBe(true)
    expect(wizard.operation?.status).toBe('READY')
  })

  it('shows operator escalation guidance and never exposes create action', () => {
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'operator', role: 'operator', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()
    wizard.destinationPlan = destinationPlan(false)
    wizard.mappingDirty = false

    const wrapper = mount(HarborProjectCreationPanel)

    expect(wrapper.text()).toContain('У оператора нет права создавать Harbor projects')
    expect(wrapper.text()).toContain('docker-prod')
    expect(wrapper.find('button.project-create__button').exists()).toBe(false)
  })

  it('hides stale missing-project actions after mapping changes', () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'admin', role: 'admin', is_active: true }
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()
    wizard.destinationPlan = destinationPlan(false)
    wizard.mappingDirty = true

    const wrapper = mount(HarborProjectCreationPanel)
    expect(wrapper.find('.project-create').exists()).toBe(false)
  })
})
