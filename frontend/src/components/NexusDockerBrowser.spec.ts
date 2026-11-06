import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'
import NexusDockerBrowser from './NexusDockerBrowser.vue'

const DIGEST = 'sha256:' + 'a'.repeat(64)

describe('NexusDockerBrowser', () => {
  beforeEach(() => {
    vi.spyOn(exportsApi, 'listNexusRepositories').mockResolvedValue({
      available: true, items: ['alpine', 'bitnami/redis'],
      message: null, truncated: false, next_cursor: null,
    })
    vi.spyOn(exportsApi, 'listNexusTags').mockResolvedValue({
      available: true, items: ['3.20', 'latest'],
      message: null, truncated: false, next_cursor: null,
    })
    vi.spyOn(exportsApi, 'inspectExactNexusImage').mockResolvedValue({
      repository: 'bitnami/redis', reference: '3.20', digest: DIGEST,
      media_type: null,
    })
  })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('provides an image → tag → inspected digest workflow for one Nexus connector', async () => {
    const wrapper = mount(NexusDockerBrowser, {
      props: { profileId: 'proxy-id', nexusRepository: 'docker-proxy' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('docker-proxy')
    await wrapper.findAll('[aria-label="Docker-образы"] button')
      .find(button => button.text() === 'bitnami/redis')!.trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '', undefined)
    await wrapper.findAll('[aria-label="Теги Docker"] button')
      .find(button => button.text() === '3.20')!.trigger('click')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage)
      .toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '3.20')
    await wrapper.findAll('button').find(button => button.text() === 'Добавить проверенный образ')!
      .trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual(['bitnami/redis', '3.20', DIGEST])
  })

  it('verifies baseimage directly on docker-proxy when Nexus browse and tags are forbidden', async () => {
    vi.mocked(exportsApi.listNexusRepositories).mockResolvedValue({
      available: false, items: [], message: 'Каталог Docker v2 закрыт',
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: false, items: [], message: 'Нет прав для перечисления тегов',
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.inspectExactNexusImage).mockResolvedValue({
      repository: 'baseimage', reference: '1.0.0', digest: DIGEST, media_type: null,
    })
    const wrapper = mount(NexusDockerBrowser, {
      props: { profileId: 'proxy-id', nexusRepository: 'docker-proxy' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('docker-proxy / v2 / baseimage')
    await wrapper.get('input[aria-label="Имя известного Docker image"]').setValue('baseimage')
    await wrapper.get('input[aria-label="Точный tag Docker image"]').setValue('1.0.0')
    await wrapper.get('form.nexus-browser__direct-form').trigger('submit')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage).toHaveBeenCalledWith(
      'proxy-id', 'baseimage', '1.0.0',
    )
    expect(exportsApi.listNexusTags).not.toHaveBeenCalled()
    await wrapper.findAll('button').find(button => button.text() === 'Добавить проверенный образ')!
      .trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual(['baseimage', '1.0.0', DIGEST])
    wrapper.unmount()
  })

  it('shows tags for a known proxy image without depending on the Docker catalog', async () => {
    vi.mocked(exportsApi.listNexusRepositories).mockResolvedValue({
      available: false, items: [], message: 'Browse запрещён',
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['1.0.0', 'latest'], message: null,
      truncated: false, next_cursor: null,
    })
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('input[aria-label="Имя известного Docker image"]').setValue('baseimage')
    await wrapper.findAll('button').find(button => button.text() === 'Показать теги этого образа')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith(
      'proxy-id', 'baseimage', '', undefined,
    )
    expect(wrapper.get('[aria-label="Теги Docker"]').text()).toContain('1.0.0')
    wrapper.unmount()
  })

  it('rejects invalid image names and does not offer unchecked selection', async () => {
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('input[aria-label="Имя известного Docker image"]')
      .setValue('docker-proxy/v2/../baseimage')
    await wrapper.get('input[aria-label="Точный tag Docker image"]').setValue('latest')
    await wrapper.get('form.nexus-browser__direct-form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Укажите точное имя Docker image')
    expect(exportsApi.inspectExactNexusImage).not.toHaveBeenCalled()
    expect(wrapper.emitted('add')).toBeUndefined()
    wrapper.unmount()
  })

  it('follows later pages without treating an empty first page as empty catalogue', async () => {
    vi.mocked(exportsApi.listNexusRepositories).mockImplementation(async (_id, _search, cursor) =>
      cursor ? {
        available: true, items: ['bitnami/redis'], message: null,
        truncated: false, next_cursor: null,
      } : {
        available: true, items: [], message: null,
        truncated: true, next_cursor: 'next',
      },
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    expect(wrapper.text()).toContain('На прочитанных страницах совпадений нет')
    await wrapper.findAll('button').find(button => button.text() === 'Следующая страница образов')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusRepositories).toHaveBeenCalledWith('proxy-id', '', 'next')
    expect(wrapper.text()).toContain('bitnami/redis')
  })

  it('automatically searches later proxy pages with a bounded query', async () => {
    vi.useFakeTimers()
    const seen: Array<string | undefined> = []
    vi.mocked(exportsApi.listNexusRepositories).mockImplementation(
      async (_id, search, cursor) => {
        seen.push(cursor)
        if (!search) {
          return { available: true, items: ['alpine'], message: null,
            truncated: false, next_cursor: null }
        }
        if (!cursor) {
          return { available: true, items: [], message: null,
            truncated: true, next_cursor: 'page-2' }
        }
        if (cursor === 'page-2') {
          return { available: true, items: [], message: null,
            truncated: true, next_cursor: 'page-3' }
        }
        return { available: true, items: ['bitnami/redis'], message: null,
          truncated: false, next_cursor: null }
      },
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('input[aria-label="Поиск Docker-образов Nexus"]').setValue('bitnami')
    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()
    expect(seen).toEqual([undefined, undefined, 'page-2', 'page-3'])
    expect(wrapper.text()).toContain('bitnami/redis')
    expect(wrapper.text()).toContain('прочитано страниц: 3')
    wrapper.unmount()
  })

  it('does not leak or show old profile results after profile change', async () => {
    let resolveOld!: (value: Awaited<ReturnType<typeof exportsApi.listNexusRepositories>>) => void
    vi.mocked(exportsApi.listNexusRepositories)
      .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValue({
        available: true, items: ['new/image'], message: null,
        truncated: false, next_cursor: null,
      })
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'old' } })
    await wrapper.setProps({ profileId: 'new' })
    await flushPromises()
    resolveOld({
      available: true, items: ['private/old'], message: null,
      truncated: false, next_cursor: null,
    })
    await flushPromises()
    expect(wrapper.text()).toContain('new/image')
    expect(wrapper.text()).not.toContain('private/old')
    expect(exportsApi.listNexusRepositories).toHaveBeenCalledWith('new', '', undefined)
  })

  it('fails closed on unavailable browse and never offers unchecked add', async () => {
    vi.mocked(exportsApi.listNexusRepositories).mockResolvedValue({
      available: false, items: [], message: 'Нет прав на Nexus Browse',
      truncated: false, next_cursor: null,
    })
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'restricted' } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Нет прав')
    expect(wrapper.text()).toContain('ручной repository:tag')
    expect(wrapper.emitted('add')).toBeUndefined()
    expect(exportsApi.inspectExactNexusImage).not.toHaveBeenCalled()
  })
})
