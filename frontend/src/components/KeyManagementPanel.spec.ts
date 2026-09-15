import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

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

afterEach(() => {
  vi.restoreAllMocks()
})

describe('KeyManagementPanel', () => {
  it('shows only SOURCE signing fingerprint and never private material', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'SOURCE',
        signing_key: { configured: true, fingerprint },
        trusted_keys: [],
      }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()

    expect(wrapper.text()).toContain(fingerprint)
    expect(wrapper.text()).toContain('Private key используется только server-side')
    expect(wrapper.find('#target-trusted-key').exists()).toBe(false)
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
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'TARGET',
        signing_key: null,
        trusted_keys: [{ fingerprint, enabled: true }],
      }),
    )
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

  it('hides stale SOURCE controls and reloads after runtime contour changes', async () => {
    const get = vi
      .spyOn(apiClient, 'get')
      .mockResolvedValueOnce(
        response({
          contour: 'SOURCE',
          signing_key: { configured: true, fingerprint },
          trusted_keys: [],
        }),
      )
      .mockResolvedValueOnce(
        response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
      )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()
    expect(wrapper.find('#source-signing-key').exists()).toBe(true)

    await wrapper.setProps({ contour: 'TARGET' })
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    await flushPromises()

    expect(get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
  })

  it('ignores a stale key-settings response from the previous runtime contour', async () => {
    const staleSource = deferred<AxiosResponse>()
    vi.spyOn(apiClient, 'get')
      .mockReturnValueOnce(staleSource.promise)
      .mockResolvedValueOnce(
        response({ contour: 'TARGET', signing_key: null, trusted_keys: [] }),
      )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await wrapper.setProps({ contour: 'TARGET' })
    await flushPromises()
    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)

    staleSource.resolve(
      response({
        contour: 'SOURCE',
        signing_key: { configured: true, fingerprint },
        trusted_keys: [],
      }),
    )
    await flushPromises()

    expect(wrapper.find('#target-trusted-key').exists()).toBe(true)
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(fingerprint)
  })
})
