import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import * as importsApi from '@/api/imports'
import type { ImportDestinationPlan, ImportPreview, Operation } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'

import ImportDestinationMapping from './ImportDestinationMapping.vue'

const SOURCE_DIGEST = `sha256:${'a'.repeat(64)}`

function operation(): Operation {
  return {
    id: 91,
    delivery_id: null,
    type: 'IMPORT',
    status: 'READY',
    actor_username: 'operator',
    comment: null,
    started_at: '2026-09-15T08:00:00Z',
    finished_at: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    bundle: {
      filename: 'mixed.htp.tar.gz',
      size_bytes: 8192,
      sha256: 'c'.repeat(64),
    },
    progress: {
      total_artifacts: 2,
      completed_artifacts: 0,
      running_artifacts: 0,
      successful_artifacts: 0,
      failed_artifacts: 0,
      skipped_artifacts: 0,
      conflict_artifacts: 0,
      progress_current: 0,
      progress_total: 2,
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
    source_delivery_id: 'DELIVERY-20260915-MIXED01',
    bundle_sha256: 'c'.repeat(64),
    bundle_size_bytes: 8192,
    signing_key_fingerprint: 'd'.repeat(64),
    verified_at: '2026-09-15T08:01:00Z',
    bundle_filename: 'mixed.htp.tar.gz',
    intake_mode: 'upload',
    source_harbor: 'harbor.source.local',
    source_portal_version: '1.0.0',
    source_created_at: '2026-09-15T07:55:00Z',
    source_created_by: 'source-operator',
    source_comment: null,
    checksum_verified: true,
    signature_verified: true,
    schema_verified: true,
    overwrite_allowed: true,
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        repository: 'source-app/service',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: SOURCE_DIGEST,
        target_digest: null,
        payload_size: 4096,
        classification: 'NEW',
        error_code: null,
        message: null,
      },
      {
        index: 1,
        artifact_type: 'helm-chart',
        repository: 'source-charts/platform',
        name: 'portal',
        reference: null,
        version: '2.3.4',
        expected_digest: SOURCE_DIGEST,
        target_digest: null,
        payload_size: 4096,
        classification: 'NEW',
        error_code: null,
        message: null,
      },
    ],
  }
}

function plan(): ImportDestinationPlan {
  return {
    operation_id: 91,
    source_delivery_id: 'DELIVERY-20260915-MIXED01',
    actor_username: 'operator',
    bundle_sha256: 'c'.repeat(64),
    plan_id: 'e'.repeat(64),
    plan_hash: 'f'.repeat(64),
    mapping_policy_revision: 7,
    created_at: '2026-09-15T08:02:00Z',
    valid: true,
    artifacts: [
      {
        index: 0,
        artifact_type: 'container-image',
        source_repository: 'source-app/service',
        source_project: 'source-app',
        name: null,
        reference: '1.0.0',
        version: null,
        expected_digest: SOURCE_DIGEST,
        payload_size: 4096,
        target_project: 'mapped-app',
        target_repository: 'mapped-app/service',
        final_reference: 'harbor.target.local/mapped-app/service:1.0.0',
        project_exists: true,
        write_allowed: true,
        target_digest: null,
        classification: 'NEW',
        error_code: null,
        message: null,
      },
      {
        index: 1,
        artifact_type: 'helm-chart',
        source_repository: 'source-charts/platform',
        source_project: 'source-charts',
        name: 'portal',
        reference: null,
        version: '2.3.4',
        expected_digest: SOURCE_DIGEST,
        payload_size: 4096,
        target_project: 'override-charts',
        target_repository: 'override-charts/platform',
        final_reference: 'oci://harbor.target.local/override-charts/platform/portal:2.3.4',
        project_exists: true,
        write_allowed: true,
        target_digest: null,
        classification: 'NEW',
        error_code: null,
        message: null,
      },
    ],
  }
}

function mockProjects(): void {
  vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
    pagination: { page: 1, page_size: 100, total: 4 },
    items: [
      { name: 'docker-default', public: false },
      { name: 'helm-default', public: false },
      { name: 'mapped-app', public: false },
      { name: 'override-charts', public: false },
    ],
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
  setActivePinia(createPinia())
})

describe('ImportDestinationMapping', () => {
  it('keeps common destination selection simple and previews TARGET refs before validation', async () => {
    mockProjects()
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'operator', role: 'operator', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()

    const wrapper = mount(ImportDestinationMapping)
    await flushPromises()

    expect(wrapper.get('#mapping-title').text()).toContain('Куда будут импортированы артефакты')
    expect(wrapper.findAll('details')).toHaveLength(2)
    expect(wrapper.findAll('details').every((item) => item.attributes('open') === undefined)).toBe(true)

    const selects = wrapper.findAll('select')
    await selects[0]!.setValue('docker-default')
    await selects[1]!.setValue('helm-default')

    expect(wrapper.text()).toContain('docker-default/service:1.0.0')
    expect(wrapper.text()).toContain('helm-default/platform/portal:2.3.4')
    expect(wrapper.text()).toContain('TARGET ещё не проверен')
    expect(wrapper.get('button.button--primary').text()).toBe('Проверить TARGET')
    expect(wrapper.text()).toContain('Import не запускается')
  })

  it('preserves advanced mapping priority and confirms the remote TARGET state', async () => {
    mockProjects()
    const planSpy = vi.spyOn(importsApi, 'buildImportDestinationPlan').mockResolvedValue(plan())
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'operator', role: 'operator', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()

    const wrapper = mount(ImportDestinationMapping)
    await flushPromises()

    const selects = wrapper.findAll('select')
    expect(selects).toHaveLength(6)
    await selects[0]!.setValue('docker-default')
    await selects[1]!.setValue('helm-default')
    await selects[2]!.setValue('mapped-app')
    await selects[5]!.setValue('override-charts')

    expect(wrapper.text()).toContain('mapped-app/service:1.0.0')
    expect(wrapper.text()).toContain('override-charts/platform/portal:2.3.4')
    expect(wrapper.text()).toContain('Правило SOURCE project "source-app"')
    expect(wrapper.text()).toContain('Индивидуальное исключение')

    await wrapper.get('button.button--primary').trigger('click')
    await flushPromises()

    expect(planSpy).toHaveBeenCalledWith(91, {
      harbor_profile_id: null,
      container_image_project: 'docker-default',
      helm_chart_project: 'helm-default',
      project_mappings: { 'source-app': 'mapped-app' },
      artifact_overrides: [{ index: 1, target_project: 'override-charts' }],
    })
    expect(wizard.confirmedPlanReady).toBe(true)
    expect(wrapper.text()).toContain('TARGET проверен')
    expect(wrapper.text()).toContain('NEW · будет импортирован')
    expect(wrapper.text()).toContain('harbor.target.local/mapped-app/service:1.0.0')
    expect(wrapper.text()).toContain('oci://harbor.target.local/override-charts/platform/portal:2.3.4')
    expect(wrapper.text()).not.toContain('mapping policy rev')
  })

  it('explains why changed destinations require revalidation', async () => {
    mockProjects()
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'operator', role: 'operator', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()
    wizard.destinationPlan = plan()
    wizard.mappingDirty = false

    const wrapper = mount(ImportDestinationMapping)
    await flushPromises()

    const selects = wrapper.findAll('select')
    await selects[0]!.setValue('docker-default')

    expect(wrapper.text()).toContain('Настройки назначения изменились')
    expect(wrapper.text()).toContain('Перед Import нужно ещё раз проверить TARGET')
    expect(wrapper.text()).toContain('ничего в нём не изменялось')
  })

  it('keeps destination controls read-only for viewer', async () => {
    mockProjects()
    const auth = useAuthStore()
    auth.user = { id: 2, username: 'viewer', role: 'viewer', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.operation = operation()
    wizard.preview = preview()
    wizard.destinationPlan = plan()
    wizard.mappingDirty = false

    const wrapper = mount(ImportDestinationMapping)
    await flushPromises()

    expect(wrapper.text()).toContain('Режим просмотра')
    expect(wrapper.findAll('select').every((field) => field.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.find('button.button--primary').exists()).toBe(false)
    expect(wrapper.text()).toContain('harbor.target.local/mapped-app/service:1.0.0')
  })
})
