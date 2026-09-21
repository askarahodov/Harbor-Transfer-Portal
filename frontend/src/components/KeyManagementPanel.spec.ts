import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { useRuntimeStore } from '@/stores/runtime'

import KeyManagementPanel from './KeyManagementPanel.vue'

const fingerprint = `sha256:${'a'.repeat(64)}`

function response<T>(data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status === 201 ? 'Created' : 'OK',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
} {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('KeyManagementPanel', () => {
  it('shows only SOURCE signing fingerprint and never private material', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'SOURCE',
        signing_key: { configured: true, fingerprint },
        pending_signing_key: { configured: false, fingerprint: null },
        trusted_keys: [],
      }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()

    expect(wrapper.text()).toContain(fingerprint)
    expect(wrapper.text()).toContain('Private key остаётся только server-side')
    expect(wrapper.text()).toContain('Скачать active trust package')
    expect(wrapper.text()).toContain('Скачать public key')
    expect(wrapper.text()).toContain('Подготовить rotation')
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    expect(wrapper.find('#target-trusted-key').exists()).toBe(false)
  })

  it('generates SOURCE identity server-side under explicit admin confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: false, fingerprint: null },
          pending_signing_key: { configured: false, fingerprint: null },
          trusted_keys: [],
        }),
      )
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint },
          pending_signing_key: { configured: false, fingerprint: null },
          trusted_keys: [],
        }),
      )
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(
      response({ action: 'generated', fingerprint }, 201),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()

    const generate = wrapper.findAll('button').find((item) =>
      item.text().includes('Создать signing identity'),
    )
    if (!generate) throw new Error('Generate signing identity button not found')
    await generate.trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(post).toHaveBeenCalledWith('/settings/keys/signing/generate')
    expect(wrapper.text()).toContain(fingerprint)
    expect(wrapper.text()).toContain('Скачать public key')
  })

  it('prepares and activates SOURCE rotation without direct private-key replacement', async () => {
    const pendingFingerprint = `sha256:${'b'.repeat(64)}`
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint },
          pending_signing_key: { configured: false, fingerprint: null },
          trusted_keys: [],
        }),
      )
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint },
          pending_signing_key: { configured: true, fingerprint: pendingFingerprint },
          trusted_keys: [],
        }),
      )
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint: pendingFingerprint },
          pending_signing_key: { configured: false, fingerprint: null },
          trusted_keys: [],
        }),
      )
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce(response({ action: 'prepared', fingerprint: pendingFingerprint }, 201))
      .mockResolvedValueOnce(response({ action: 'activated', fingerprint: pendingFingerprint }))

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()

    const prepare = wrapper.findAll('button').find((item) =>
      item.text().includes('Подготовить rotation'),
    )
    if (!prepare) throw new Error('Prepare rotation button not found')
    await prepare.trigger('click')
    await flushPromises()

    expect(post).toHaveBeenNthCalledWith(1, '/settings/keys/signing/rotation/prepare')
    expect(wrapper.text()).toContain(pendingFingerprint)
    expect(wrapper.text()).toContain('Активировать pending key')
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)

    const activate = wrapper.findAll('button').find((item) =>
      item.text().includes('Активировать pending key'),
    )
    if (!activate) throw new Error('Activate pending key button not found')
    await activate.trigger('click')
    await flushPromises()

    expect(post).toHaveBeenNthCalledWith(2, '/settings/keys/signing/rotation/activate', {
      expected_fingerprint: pendingFingerprint,
    })
    expect(wrapper.text()).toContain(pendingFingerprint)
    expect(wrapper.text()).toContain('Подготовить rotation')
  })

  it('uploads SOURCE key file only after explicit confirmation', async () => {
    const pem = 'synthetic-source-key-fixture'
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'SOURCE',
        signing_key: { configured: false, fingerprint: null },
        trusted_keys: [],
      }),
    )
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ action: 'installed', fingerprint }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()
    const input = wrapper.get('#source-signing-key')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [{ text: () => Promise.resolve(pem) }],
    })
    await input.trigger('change')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(put).toHaveBeenCalledWith('/settings/keys/signing', { pem })
    expect(wrapper.text()).not.toContain(pem)
  })

  it('manages TARGET trust state with confirmations', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get').mockImplementation((url) => {
      if (String(url).includes('/impact')) {
        return Promise.resolve(
          response({
            fingerprint,
            enabled: true,
            enabled_key_count: 2,
            historical_import_count: 7,
            blocking_operation_ids: [],
            can_retire: true,
          }),
        )
      }
      return Promise.resolve(
        response({
          contour: 'TARGET',
          signing_key: null,
          pending_signing_key: null,
          trusted_keys: [{ fingerprint, enabled: true }],
        }),
      )
    })
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({ action: 'disabled', fingerprint }),
    )
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue(
      response({ action: 'removed', fingerprint }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'TARGET' } })
    await flushPromises()
    expect(wrapper.text()).toContain(fingerprint)
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)

    await wrapper.get('.key-row button.secondary').trigger('click')
    await flushPromises()
    expect(patch).toHaveBeenCalledWith(
      `/settings/keys/trusted/${encodeURIComponent(fingerprint)}`,
      { enabled: false, confirm: true },
    )

    await wrapper.get('.key-row button.danger').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith(
      `/settings/keys/trusted/${encodeURIComponent(fingerprint)}`,
      { params: { confirm: true } },
    )
    expect(window.confirm).toHaveBeenCalledTimes(2)
  })

  it('imports a SOURCE trust package into TARGET under explicit confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce(
        response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
      )
      .mockResolvedValueOnce(
        response({
          contour: 'TARGET',
          signing_key: null,
          trusted_keys: [{ fingerprint, enabled: true }],
        }),
      )
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(
      response({ action: 'added', fingerprint }, 201),
    )
    const payload = new Uint8Array([31, 139, 8, 0]).buffer

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'TARGET' } })
    await flushPromises()
    expect(wrapper.text()).toContain('SOURCE trust не настроен')

    const input = wrapper.get('#target-trust-package')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [{ arrayBuffer: () => Promise.resolve(payload) }],
    })
    await input.trigger('change')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledOnce()
    expect(post).toHaveBeenCalledWith(
      '/settings/keys/trusted/package',
      payload,
      {
        params: { confirm: true },
        headers: { 'Content-Type': 'application/gzip' },
      },
    )
    expect(wrapper.text()).toContain('SOURCE trust настроен')
    expect(wrapper.text()).toContain('SOURCE identity импортирована')
  })

  it('confirms and uploads a TARGET public key file', async () => {
    const pem = 'synthetic-target-public-key-fixture'
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
    )
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue(
      response({ action: 'added', fingerprint }, 201),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'TARGET' } })
    await flushPromises()
    const input = wrapper.get('#target-trusted-key')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [{ text: () => Promise.resolve(pem) }],
    })
    await input.trigger('change')
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/settings/keys/trusted', { pem, confirm: true })
    expect(wrapper.text()).not.toContain(pem)
  })

  it('replaces a TARGET public key only after confirmation', async () => {
    const replacementPem = 'synthetic-replacement-public-key-fixture'
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'TARGET',
        signing_key: null,
        trusted_keys: [{ fingerprint, enabled: true }],
      }),
    )
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({ action: 'replaced', fingerprint: `sha256:${'b'.repeat(64)}` }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'TARGET' } })
    await flushPromises()
    const input = wrapper.get('input[aria-label="Заменить trusted public key"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true,
      value: [{ text: () => Promise.resolve(replacementPem) }],
    })
    await input.trigger('change')
    await flushPromises()

    expect(put).toHaveBeenCalledWith(
      `/settings/keys/trusted/${encodeURIComponent(fingerprint)}`,
      { pem: replacementPem, confirm: true },
    )
    expect(wrapper.text()).not.toContain(replacementPem)
  })

  it('hides stale SOURCE controls and reloads after live runtime mode changes', async () => {
    const get = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint },
          pending_signing_key: { configured: false, fingerprint: null },
          trusted_keys: [],
        }),
      )
      .mockResolvedValueOnce(
        response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
      )

    const runtime = useRuntimeStore()
    runtime.setContour('SOURCE')
    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    expect(wrapper.text()).toContain('Подготовить rotation')

    runtime.setContour('TARGET')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    await flushPromises()

    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
  })

  it('ignores a stale key-settings response from the previous runtime mode', async () => {
    const staleSource = deferred<AxiosResponse>()
    vi.spyOn(apiClient, 'get')
      .mockReturnValueOnce(staleSource.promise)
      .mockResolvedValueOnce(
        response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
      )

    const runtime = useRuntimeStore()
    runtime.setContour('SOURCE')
    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    runtime.setContour('TARGET')
    await flushPromises()
    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)

    staleSource.resolve(
      response({
        contour: 'SOURCE',
        signing_key: { configured: true, fingerprint },
        pending_signing_key: { configured: false, fingerprint: null },
        trusted_keys: [],
      }),
    )
    await flushPromises()

    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(fingerprint)
  })
})
