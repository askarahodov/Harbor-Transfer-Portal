import { flushPromises, mount } from '@vue/test-utils'
import type { AxiosResponse } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import KeyManagementPanel from './KeyManagementPanel.vue'

function response<T>(data: T): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: { headers: {} } as AxiosResponse<T>['config'],
  }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('KeyManagementPanel', () => {
  it('shows only SOURCE signing status and never renders private key material', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'SOURCE',
        source_signing: {
          configured: true,
          fingerprint: `sha256:${'a'.repeat(64)}`,
        },
        trusted_keys: [],
      }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'SOURCE' } })
    await flushPromises()

    expect(wrapper.text()).toContain('SOURCE signing identity')
    expect(wrapper.text()).toContain(`sha256:${'a'.repeat(64)}`)
    expect(wrapper.text()).toContain('Private key никогда не отображается')
    expect(wrapper.find('#source-signing-key').exists()).toBe(true)
    expect(wrapper.find('#trusted-key').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('BEGIN PRIVATE KEY')
  })

  it('shows TARGET overlap trust states with explicit management actions', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue(
      response({
        contour: 'TARGET',
        source_signing: null,
        trusted_keys: [
          { fingerprint: `sha256:${'1'.repeat(64)}`, enabled: true },
          { fingerprint: `sha256:${'2'.repeat(64)}`, enabled: false },
        ],
      }),
    )

    const wrapper = mount(KeyManagementPanel, { props: { contour: 'TARGET' } })
    await flushPromises()

    expect(wrapper.text()).toContain(`sha256:${'1'.repeat(64)}`)
    expect(wrapper.text()).toContain(`sha256:${'2'.repeat(64)}`)
    expect(wrapper.text()).toContain('enabled')
    expect(wrapper.text()).toContain('disabled')
    expect(wrapper.text()).toContain('Отключить')
    expect(wrapper.text()).toContain('Включить')
    expect(wrapper.text()).toContain('Удалить')
    expect(wrapper.text()).toContain('Заменить существующий key')
    expect(wrapper.find('#source-signing-key').exists()).toBe(false)
  })
})
