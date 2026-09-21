import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import * as importsApi from '@/api/imports'
import type { ImportPreview, Operation } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useRuntimeStore } from '@/stores/runtime'

import ImportView from './ImportView.vue'

let pinia = createPinia()

function operation(status: Operation['status']): Operation {
  return {
    id: 51,
    delivery_id: null,
    type: 'IMPORT',
    status,
    actor_username: 'operator',
    comment: null,
    started_at: '2026-09-14T05:00:00Z',
    finished_at: null,
    error_code: null,
    error_message: null,
    cancel_requested: false,
    bundle: {
      filename: 'delivery.htp.tar.gz',
      size_bytes: 8192,
      sha256: 'c'.repeat(64),
    },
    progress: {
      total_artifacts: 1,
      completed_artifacts: 0,
      running_artifacts: 0,
      successful_artifacts: 0,
      failed_artifacts: 0,
      skipped_artifacts: 0,
      conflict_artifacts: 1,
      progress_current: 0,
      progress_total: 1,
      current_phase: status,
      running_artifact_ids: [],
    },
    artifacts: [],
  }
}

function conflictPreview(): ImportPreview {
  return {
    operation_id: 51,
    status: 'READY',
    source_delivery_id: 'DELIVERY-20260914-IMPORT01',
    bundle_sha256: 'c'.repeat(64),
    bundle_size_bytes: 8192,
    signing_key_fingerprint: 'd'.repeat(64),
    verified_at: '2026-09-14T05:02:00Z',
    bundle_filename: 'delivery.htp.tar.gz',
    intake_mode: 'incoming',
    source_harbor: 'harbor.source.local',
    source_portal_version: '1.0.0',
    source_created_at: '2026-09-14T04:00:00Z',
    source_created_by: 'source-operator',
    source_comment: 'critical offline delivery',
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
        expected_digest: `sha256:${'a'.repeat(64)}`,
        target_digest: `sha256:${'b'.repeat(64)}`,
        payload_size: 4096,
        classification: 'CONFLICT',
        error_code: null,
        message: null,
      },
    ],
  }
}

beforeEach(() => {
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
    pagination: { page: 1, page_size: 100, total: 2 },
    items: [
      { name: 'docker-prod', public: false },
      { name: 'helm-prod', public: false },
    ],
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('TARGET import wizard view', () => {
  it('mounts destination mapping and keeps import disabled until plan confirmation', async () => {
    sessionStorage.setItem('htp.import.operation-id', '51')
    vi.spyOn(importsApi, 'getOperation').mockResolvedValue(operation('READY'))
    vi.spyOn(importsApi, 'getImportPreview').mockResolvedValue(conflictPreview())
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('TARGET')
    const auth = useAuthStore(pinia)
    auth.initialized = true
    auth.user = { id: 1, username: 'operator', role: 'operator', is_active: true }

    const wrapper = mount(ImportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    expect(wrapper.get('h1').text()).toContain('Приём и импорт Offline Bundle')
    expect(wrapper.text()).toContain('harbor.source.local')
    expect(wrapper.text()).toContain('critical offline delivery')
    expect(wrapper.text()).toContain('CONFLICT — другой digest, заблокирован')
    expect(wrapper.text()).toContain('package verified')
    expect(wrapper.text()).toContain('не означает')
    expect(wrapper.get('#mapping-title').text()).toContain('Куда импортировать артефакты')

    const confirmPlanButton = wrapper.findAll('button').find((item) =>
      item.text().includes('Проверить и подтвердить destination plan'),
    )
    expect(confirmPlanButton).toBeDefined()

    const defaultImportButton = wrapper.findAll('button').find((item) =>
      item.text().includes('Импортировать NEW'),
    )
    expect(defaultImportButton).toBeDefined()
    expect(defaultImportButton?.attributes('disabled')).toBeDefined()
    expect(wrapper.find('.overwrite-confirmation').exists()).toBe(false)
  })

  it('keeps drag-and-drop fully keyboard accessible and exposes empty discovery state', async () => {
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('TARGET')
    const inputClick = vi.spyOn(HTMLInputElement.prototype, 'click')
    const wrapper = mount(ImportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const browserInput = wrapper.get('input[type="file"][multiple]')
    expect(browserInput.attributes('accept')).toContain('.htp.tar.gz')
    expect(browserInput.attributes('accept')).toContain('.sha256')
    expect(browserInput.attributes('accept')).toContain('.htp-handoff.json')
    const dropZone = wrapper.get('.drop-zone')
    expect(dropZone.attributes('tabindex')).toBe('0')
    expect(dropZone.attributes('role')).toBe('button')
    expect(dropZone.attributes('aria-describedby')).toBe('bundle-drop-help')
    expect(wrapper.get('#bundle-drop-help').text()).toContain('.sha256 + .htp-handoff.json')

    await dropZone.trigger('keydown', { key: 'Enter' })
    await dropZone.trigger('keydown', { key: ' ' })
    expect(inputClick).toHaveBeenCalledTimes(2)

    const emptyState = wrapper.get('.state-placeholder')
    expect(emptyState.attributes('role')).toBe('status')
    expect(emptyState.text()).toContain('Готовые пакеты не найдены')
    expect(wrapper.text()).toContain('Обнаружить готовые пакеты')
  })


  it('uploads bundle, sidecar and signed handoff together through browser intake', async () => {
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('TARGET')
    const upload = vi.spyOn(importsApi, 'uploadImportBundle').mockRejectedValue(
      new Error('synthetic stop after browser intake request'),
    )

    const wrapper = mount(ImportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const delivery = 'DELIVERY-20260921-BROWSER01'
    const bundle = new File(['bundle'], `${delivery}.htp.tar.gz`, {
      type: 'application/gzip',
    })
    const sidecar = new File(['checksum'], `${delivery}.htp.tar.gz.sha256`, {
      type: 'text/plain',
    })
    const handoff = new File(['{}'], `${delivery}.htp-handoff.json`, {
      type: 'application/json',
    })
    const input = wrapper.get('input[type="file"][multiple]')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [bundle, sidecar, handoff],
    })
    await input.trigger('change')
    await flushPromises()

    expect(upload).toHaveBeenCalledOnce()
    expect(upload.mock.calls[0]?.[0]).toBe(bundle)
    expect(upload.mock.calls[0]?.[2]).toEqual({ sidecar, handoff })
    expect(wrapper.text()).toContain(bundle.name)
    expect(wrapper.text()).toContain(sidecar.name)
    expect(wrapper.text()).toContain(handoff.name)
  })

  it('requires VERIFIED signed handoff before media discovery', async () => {
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('TARGET')
    vi.spyOn(importsApi, 'verifyPhysicalHandoff').mockResolvedValue({
      delivery_id: 'DELIVERY-20260921-HANDOFF1',
      signing_key_fingerprint: `sha256:${'a'.repeat(64)}`,
      bundle_sha256: 'b'.repeat(64),
      bundle_size_bytes: 8192,
      created_at: '2026-09-21T11:00:00Z',
      created_by: 'source-operator',
      verified: true,
    })
    const discover = vi.spyOn(importsApi, 'discoverImportBundles').mockResolvedValue({
      operations: [],
    })

    const wrapper = mount(ImportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    const discoverButton = wrapper.findAll('button').find((item) =>
      item.text().includes('Обнаружить готовые пакеты'),
    )
    expect(discoverButton).toBeDefined()
    expect(discoverButton?.attributes('disabled')).toBeDefined()

    const inputs = wrapper.findAll('input[type="file"]')
    const handoff = inputs.find((item) => item.attributes('accept')?.includes('.htp-handoff.json'))
    expect(handoff).toBeDefined()
    Object.defineProperty(handoff!.element, 'files', {
      configurable: true,
      value: [new File(['{}'], 'DELIVERY-20260921-HANDOFF1.htp-handoff.json', { type: 'application/json' })],
    })
    await handoff!.trigger('change')
    await flushPromises()

    expect(importsApi.verifyPhysicalHandoff).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('VERIFIED')
    expect(wrapper.text()).toContain('source-operator')
    expect(wrapper.text()).toContain('DELIVERY-20260921-HANDOFF1')
    expect(discoverButton?.attributes('disabled')).toBeUndefined()

    await discoverButton!.trigger('click')
    await flushPromises()
    expect(discover).toHaveBeenCalledOnce()
  })

  it('renders SOURCE fallback and does not restore TARGET operations', async () => {
    sessionStorage.setItem('htp.import.operation-id', '51')
    const getOperation = vi.spyOn(importsApi, 'getOperation')
    const runtime = useRuntimeStore(pinia)
    runtime.setContour('SOURCE')

    const wrapper = mount(ImportView, {
      global: {
        plugins: [pinia],
        stubs: { RouterLink: { template: '<a><slot /></a>' } },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Import workflow доступен только в контуре TARGET')
    expect(getOperation).not.toHaveBeenCalled()
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
  })
})