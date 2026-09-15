import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import type { ImportPreview } from '@/api/imports'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'

import ImportWorkspaceView from './ImportWorkspaceView.vue'

function preview(): ImportPreview {
  return {
    operation_id: 51,
    status: 'READY',
    source_delivery_id: 'DELIVERY-51',
    bundle_sha256: 'c'.repeat(64),
    bundle_size_bytes: 1024,
    signing_key_fingerprint: 'd'.repeat(64),
    verified_at: '2026-09-15T08:00:00Z',
    bundle_filename: 'bundle.htp.tar.gz',
    intake_mode: 'upload',
    source_harbor: 'harbor.source.local',
    source_portal_version: '1.0.0',
    source_created_at: '2026-09-15T07:00:00Z',
    source_created_by: 'operator',
    source_comment: null,
    checksum_verified: true,
    signature_verified: true,
    schema_verified: true,
    overwrite_allowed: false,
    artifacts: [],
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(exportsApi, 'listHarborProjects').mockResolvedValue({
    pagination: { page: 1, page_size: 100, total: 1 },
    items: [{ name: 'target', public: false }],
  })
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ImportWorkspaceView', () => {
  it('teleports destination mapping into preview and hides stale identity classification', async () => {
    const auth = useAuthStore()
    auth.user = { id: 1, username: 'operator', role: 'operator', is_active: true }
    auth.initialized = true
    const wizard = useImportWizardStore()
    wizard.step = 2
    wizard.preview = preview()

    const wrapper = mount(ImportWorkspaceView, {
      attachTo: document.body,
      global: {
        stubs: {
          ImportView: {
            template: `
              <section class="panel" aria-labelledby="preview-title">
                <div class="preview-metadata">metadata</div>
                <div class="classification-summary">summary</div>
                <div class="table-wrap">legacy table</div>
                <div class="actions">actions</div>
              </section>
            `,
          },
        },
      },
    })
    await flushPromises()

    const panel = document.body.querySelector(".panel[aria-labelledby='preview-title']")
    const mapping = panel?.querySelector('.import-destination-mapping-slot') as HTMLElement | null
    const summary = panel?.querySelector('.classification-summary') as HTMLElement | null
    const legacyTable = panel?.querySelector('.table-wrap') as HTMLElement | null
    const actions = panel?.querySelector('.actions') as HTMLElement | null

    expect(mapping).not.toBeNull()
    expect(mapping?.closest('.panel')).toBe(panel)
    expect(getComputedStyle(mapping!).order).toBe('1')
    expect(getComputedStyle(summary!).display).toBe('none')
    expect(getComputedStyle(legacyTable!).display).toBe('none')
    expect(getComputedStyle(actions!).order).toBe('2')

    wrapper.unmount()
  })
})
