import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import KeyManagementView from './KeyManagementView.vue'

function response<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

function fileWithText(value: string): File {
  return { text: vi.fn().mockResolvedValue(value) } as unknown as File
}

async function chooseFile(wrapper: ReturnType<typeof mount>, selector: string, value: string) {
  const input = wrapper.get(selector)
  Object.defineProperty(input.element, 'files', {
    configurable: true,
    value: [fileWithText(value)],
  })
  await input.trigger('change')
  await flushPromises()
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('KeyManagementView', () => {
  it('shows only public SOURCE identity and clears private key after rotation', async () => {
    const initial = {
      contour: 'SOURCE' as const,
      signing: {
        configured: true,
        key_id: 'a'.repeat(64),
        fingerprint: `sha256:${'a'.repeat(64)}`,
      },
      trusted_keys: [],
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue(response(initial))
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue(
      response({
        ...initial,
        signing: {
          configured: true,
          key_id: 'b'.repeat(64),
          fingerprint: `sha256:${'b'.repeat(64)}`,
        },
      }),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(KeyManagementView)
    await flushPromises()

    expect(wrapper.text()).toContain(`sha256:${'a'.repeat(64)}`)
    expect(wrapper.text()).toContain('Private key никогда не возвращается через API')

    const privatePem = '-----BEGIN PRIVATE KEY-----\nsecret-fixture\n-----END PRIVATE KEY-----'
    await chooseFile(wrapper, '#source-signing-key', privatePem)
    const rotateButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Ротировать signing key'))
    expect(rotateButton).toBeDefined()
    await rotateButton!.trigger('click')
    await flushPromises()

    expect(put).toHaveBeenCalledWith('/settings/keys/signing', {
      private_key_pem: privatePem,
      confirm_rotation: true,
    })
    expect(wrapper.text()).not.toContain('secret-fixture')
    expect(wrapper.text()).toContain(`sha256:${'b'.repeat(64)}`)
  })

  it('renders TARGET trust state and confirms disable server-side', async () => {
    const keyId = 'c'.repeat(64)
    const initial = {
      contour: 'TARGET' as const,
      signing: null,
      trusted_keys: [
        { key_id: keyId, fingerprint: `sha256:${keyId}`, enabled: true },
      ],
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue(response(initial))
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue(
      response({
        ...initial,
        trusted_keys: [
          { key_id: keyId, fingerprint: `sha256:${keyId}`, enabled: false },
        ],
      }),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(KeyManagementView)
    await flushPromises()

    expect(wrapper.text()).toContain('Enabled')
    expect(wrapper.text()).toContain(`sha256:${keyId}`)
    const disableButton = wrapper
      .findAll('button')
      .find((button) => button.text() === 'Отключить')
    expect(disableButton).toBeDefined()
    await disableButton!.trigger('click')
    await flushPromises()

    expect(patch).toHaveBeenCalledWith(`/settings/keys/trusted/${keyId}`, {
      enabled: false,
      confirm: true,
    })
    expect(wrapper.text()).toContain('Disabled')
  })

  it('requires browser confirmation before adding a TARGET key', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({ contour: 'TARGET' as const, signing: null, trusted_keys: [] }),
    )
    const post = vi.spyOn(apiClient, 'post')
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    const wrapper = mount(KeyManagementView)
    await flushPromises()
    const publicPem = '-----BEGIN PUBLIC KEY-----\npublic-fixture\n-----END PUBLIC KEY-----'
    await chooseFile(wrapper, '#target-trusted-key', publicPem)
    const addButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Добавить trusted key'))
    expect(addButton).toBeDefined()
    await addButton!.trigger('click')

    expect(post).not.toHaveBeenCalled()
  })
})
