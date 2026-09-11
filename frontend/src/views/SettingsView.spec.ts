import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearHarborCa,
  fetchHarborSettings,
  installHarborCa,
  rotateHarborCredential,
  testHarborConnection,
  updateHarborSettings,
  type HarborSettings,
} from '@/api/settings'

import SettingsView from './SettingsView.vue'

vi.mock('@/api/settings', () => ({
  fetchHarborSettings: vi.fn(),
  updateHarborSettings: vi.fn(),
  rotateHarborCredential: vi.fn(),
  installHarborCa: vi.fn(),
  clearHarborCa: vi.fn(),
  testHarborConnection: vi.fn(),
}))

const configured: HarborSettings = {
  contour: 'SOURCE',
  url: 'https://harbor.local/',
  username: 'robot$portal',
  verify_tls: true,
  credential_configured: true,
  custom_ca_configured: false,
  custom_ca_source: null,
}

async function mountSettings() {
  const wrapper = mount(SettingsView)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchHarborSettings).mockResolvedValue({ ...configured })
  vi.mocked(updateHarborSettings).mockResolvedValue({ ...configured })
  vi.mocked(rotateHarborCredential).mockResolvedValue({ ...configured })
  vi.mocked(installHarborCa).mockResolvedValue({
    ...configured,
    custom_ca_configured: true,
    custom_ca_source: 'runtime',
  })
  vi.mocked(clearHarborCa).mockResolvedValue({ ...configured })
  vi.mocked(testHarborConnection).mockResolvedValue({
    connected: true,
    version: '2.13.0',
    auth_mode: 'db_auth',
  })
})

describe('settings view', () => {
  it('loads safe config but never prefills the Harbor credential', async () => {
    const wrapper = await mountSettings()

    expect(wrapper.text()).toContain('Статус: настроено')
    expect(wrapper.get('input[type="password"]').element.value).toBe('')
    expect(wrapper.text()).not.toContain('bootstrap-secret')
  })

  it('saves non-secret fields separately and shows explicit TLS warning', async () => {
    const wrapper = await mountSettings()
    const tls = wrapper.get('input[type="checkbox"]')
    await tls.setValue(false)

    expect(wrapper.text()).toContain('TLS verification отключена явно')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(updateHarborSettings).toHaveBeenCalledWith({
      url: 'https://harbor.local/',
      username: 'robot$portal',
      verify_tls: false,
    })
  })

  it('rotates credential only through the dedicated action and clears the input', async () => {
    const wrapper = await mountSettings()
    const password = wrapper.get('input[type="password"]')
    await password.setValue('new-runtime-secret')
    const rotate = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Ротировать credential'))
    expect(rotate).toBeDefined()

    await rotate!.trigger('click')
    await flushPromises()

    expect(rotateHarborCredential).toHaveBeenCalledWith('new-runtime-secret')
    expect(password.element.value).toBe('')
  })

  it('shows sanitized connection check result', async () => {
    const wrapper = await mountSettings()
    const testButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('Проверить подключение'))
    expect(testButton).toBeDefined()

    await testButton!.trigger('click')
    await flushPromises()

    expect(testHarborConnection).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('Подключение к Harbor успешно 2.13.0.')
  })
})
