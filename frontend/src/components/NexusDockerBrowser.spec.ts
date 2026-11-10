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
    await wrapper.get('button[aria-label="Выбрать Docker image bitnami/redis"]')
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '', undefined)
    await wrapper.get('button.nexus-browser__tag-link[aria-pressed="false"]')
      .trigger('click')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage)
      .toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '3.20')
    await wrapper.findAll('button').find(button => button.text() === 'Добавить проверенный образ')!
      .trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual(['bitnami/redis', '3.20', DIGEST])
  })

  it('uses a compact searchable image selector to open a Harbor-style tag table', async () => {
    const wrapper = mount(NexusDockerBrowser, {
      props: { profileId: 'proxy-id', nexusRepository: 'docker-proxy' },
    })
    await flushPromises()
    const selector = wrapper.get('select[aria-label="Выбрать Docker image Nexus"]')
    expect(selector.findAll('option').map(option => option.text())).toContain('bitnami/redis')
    expect(wrapper.get('.nexus-browser__catalog').element.tagName).toBe('DETAILS')
    expect(wrapper.get('.nexus-browser__catalog').attributes('open')).toBeUndefined()
    await selector.setValue('bitnami/redis')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '', undefined)
    expect(wrapper.get('table.nexus-browser__table').text()).toContain('3.20')
    await wrapper.get('input[aria-label="Выбрать 3.20"]').setValue(true)
    await wrapper.findAll('button').find(button => button.text() === 'Добавить выбранное (1)')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage).toHaveBeenCalledWith(
      'proxy-id', 'bitnami/redis', '3.20',
    )
    expect(wrapper.emitted('add')?.[0]).toEqual(['bitnami/redis', '3.20', DIGEST])
    wrapper.unmount()
  })

  it('retains the selected image and checked tags while searching for another image', async () => {
    vi.useFakeTimers()
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['v1', 'v2'], message: null,
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.listNexusRepositories).mockImplementation(async (_id, search) => ({
      available: true,
      items: search === 'bitnami' ? ['bitnami/redis'] : ['alpine', 'bitnami/redis'],
      message: null, truncated: false, next_cursor: null,
    }))
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('select[aria-label="Выбрать Docker image Nexus"]').setValue('alpine')
    await flushPromises()
    await wrapper.get('input[aria-label="Выбрать v1"]').setValue(true)
    expect(wrapper.text()).toContain('Выбрано: 1')
    await wrapper.get('input[aria-label="Поиск Docker-образов Nexus"]').setValue('bitnami')
    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()
    const selector = wrapper.get('select[aria-label="Выбрать Docker image Nexus"]')
    expect((selector.element as HTMLSelectElement).value).toBe('alpine')
    expect(selector.findAll('option').map(option => option.text())).toContain('alpine · выбран')
    expect(wrapper.get('table.nexus-browser__table').text()).toContain('v1')
    expect(wrapper.text()).toContain('Выбрано: 1')
    await selector.setValue('bitnami/redis')
    await flushPromises()
    expect(wrapper.text()).not.toContain('Выбрано: 1')
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith('proxy-id', 'bitnami/redis', '', undefined)
    wrapper.unmount()
  })

  it('retries denied tags/list for an already selected known image without losing healthy choices', async () => {
    let attempt = 0
    vi.mocked(exportsApi.listNexusTags).mockImplementation(async () => {
      attempt += 1
      return attempt === 1
        ? { available: false, items: [], message: 'Нет прав на теги',
            truncated: false, next_cursor: null }
        : { available: true, items: ['v1'], message: null,
            truncated: false, next_cursor: null }
    })
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('select[aria-label="Выбрать Docker image Nexus"]').setValue('alpine')
    await flushPromises()
    expect(wrapper.text()).toContain('Нет прав на теги')
    await wrapper.get('input[aria-label="Имя известного Docker image"]').setValue('alpine')
    await wrapper.findAll('button').find(button => button.text() === 'Показать теги этого образа')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledTimes(2)
    expect(wrapper.get('table.nexus-browser__table').text()).toContain('v1')
    await wrapper.get('input[aria-label="Выбрать v1"]').setValue(true)
    await wrapper.findAll('button').find(button => button.text() === 'Показать теги этого образа')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Выбрано: 1')
    wrapper.unmount()
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
    expect(wrapper.get('[aria-label="Таблица версий Nexus"]').text()).toContain('1.0.0')
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

  it('browses hierarchical Docker image namespaces and verifies a selected leaf tag', async () => {
    vi.mocked(exportsApi.listNexusRepositories).mockResolvedValue({
      available: true,
      items: ['baseimage', 'team/worker', 'team/platform/api'],
      message: null, truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['v1', 'v2'], message: null,
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.inspectExactNexusImage).mockResolvedValue({
      repository: 'team/platform/api', reference: 'v2', digest: DIGEST, media_type: null,
    })
    const wrapper = mount(NexusDockerBrowser, {
      props: { profileId: 'proxy-id', nexusRepository: 'docker-proxy' },
    })
    await flushPromises()
    expect(wrapper.get('[role="tree"]').text()).toContain('baseimage')
    expect(wrapper.get('[role="tree"]').text()).toContain('team/platform/api')
    expect(wrapper.findAll('[role="treeitem"]')).toHaveLength(5)
    await wrapper.get('button[aria-label="Свернуть team"]').trigger('click')
    expect(wrapper.findAll('[role="treeitem"]')).toHaveLength(2)
    await wrapper.get('button[aria-label="Раскрыть team"]').trigger('click')
    await wrapper.get('button[aria-label="Выбрать Docker image team/platform/api"]')
      .trigger('click')
    await flushPromises()
    expect(exportsApi.listNexusTags).toHaveBeenCalledWith(
      'proxy-id', 'team/platform/api', '', undefined,
    )
    await wrapper.findAll('button.nexus-browser__tag-link')
      .find(button => button.text() === 'v2')!.trigger('click')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage).toHaveBeenCalledWith(
      'proxy-id', 'team/platform/api', 'v2',
    )
    await wrapper.findAll('button').find(button => button.text() === 'Добавить проверенный образ')!
      .trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual(['team/platform/api', 'v2', DIGEST])
    wrapper.unmount()
  })

  it('matches Harbor table layout and stages several Nexus tags for bounded verified export', async () => {
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['v1', 'v2', 'v3'], message: null,
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.inspectExactNexusImage).mockImplementation(
      async (_id, repository, reference) => ({
        repository, reference, digest: DIGEST, media_type: null,
      }),
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('button[aria-label="Выбрать Docker image bitnami/redis"]').trigger('click')
    await flushPromises()
    const table = wrapper.get('table.nexus-browser__table')
    expect(table.text()).toContain('Версия / tag')
    expect(table.text()).toContain('Digest')
    expect(table.text()).toContain('Размер')
    expect(table.text()).toContain('Добавлен')
    expect(table.text()).toContain('Не проверен')
    await wrapper.get('input[aria-label="Выбрать v1"]').setValue(true)
    await wrapper.get('input[aria-label="Выбрать v3"]').setValue(true)
    expect(wrapper.text()).toContain('Выбрано: 2')
    await wrapper.findAll('button').find(button => button.text() === 'Добавить выбранное (2)')!
      .trigger('click')
    await flushPromises()
    expect(exportsApi.inspectExactNexusImage).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('add')).toEqual([
      ['bitnami/redis', 'v1', DIGEST],
      ['bitnami/redis', 'v3', DIGEST],
    ])
    expect(table.text()).toContain('sha256:')
    expect(wrapper.text()).toContain('Отметьте нужные версии')
    wrapper.unmount()
  })

  it('keeps multi-selection atomic if one Nexus tag inspection fails', async () => {
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['v1', 'v2'], message: null,
      truncated: false, next_cursor: null,
    })
    vi.mocked(exportsApi.inspectExactNexusImage).mockImplementation(
      async (_id, repository, reference) => {
        if (reference === 'v2') throw new Error('upstream 403')
        return { repository, reference, digest: DIGEST, media_type: null }
      },
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('button[aria-label="Выбрать Docker image alpine"]').trigger('click')
    await flushPromises()
    await wrapper.get('input[aria-label="Выбрать все видимые версии"]').setValue(true)
    await wrapper.findAll('button').find(button => button.text() === 'Добавить выбранное (2)')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.emitted('add')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('Ничего не добавлено')
    expect(wrapper.get('[role="alert"]').text()).not.toContain('403')
    wrapper.unmount()
  })

  it('drops in-flight batch verification when Nexus profile changes, without stale emits', async () => {
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: ['v1'], message: null,
      truncated: false, next_cursor: null,
    })
    let resolveInspect!: (value: exportsApi.ExactNexusInspection) => void
    vi.mocked(exportsApi.inspectExactNexusImage).mockImplementation(
      () => new Promise(resolve => { resolveInspect = resolve }),
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'old-nexus' } })
    await flushPromises()
    await wrapper.get('button[aria-label="Выбрать Docker image alpine"]').trigger('click')
    await flushPromises()
    await wrapper.get('input[aria-label="Выбрать v1"]').setValue(true)
    await wrapper.findAll('button')
      .find(button => button.text() === 'Добавить выбранное (1)')!.trigger('click')
    await wrapper.setProps({ profileId: 'new-nexus' })
    resolveInspect({
      repository: 'alpine', reference: 'v1', digest: DIGEST, media_type: null,
    })
    await flushPromises()
    expect(wrapper.emitted('add')).toBeUndefined()
    expect(wrapper.text()).not.toContain('Проверка digest…')
    wrapper.unmount()
  })

  it('paginates 100 tags into compact 25-row tables while retaining selected choices', async () => {
    vi.mocked(exportsApi.listNexusTags).mockResolvedValue({
      available: true, items: Array.from({ length: 100 }, (_, i) => 'v' + String(i).padStart(3, '0')),
      message: null, truncated: false, next_cursor: null,
    })
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('button[aria-label="Выбрать Docker image alpine"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('table.nexus-browser__table tbody tr')).toHaveLength(25)
    await wrapper.get('input[aria-label="Выбрать v000"]').setValue(true)
    await wrapper.findAll('.nexus-browser__pagination button')
      .find(button => button.text() === 'Далее')!.trigger('click')
    expect(wrapper.findAll('table.nexus-browser__table tbody tr')).toHaveLength(25)
    expect(wrapper.text()).toContain('Страница 2')
    await wrapper.get('input[aria-label="Выбрать v025"]').setValue(true)
    expect(wrapper.text()).toContain('Выбрано: 2')
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

  it('continues searching beyond the first populated catalogue page', async () => {
    vi.useFakeTimers()
    const firstPage = Array.from({ length: 25 }, (_, index) => `unrelated/image-${index}`)
    vi.mocked(exportsApi.listNexusRepositories).mockImplementation(
      async (_id, search, cursor) => {
        if (!search) {
          return { available: true, items: ['alpine'], message: null,
            truncated: false, next_cursor: null }
        }
        if (!cursor) {
          return { available: true, items: firstPage, message: null,
            truncated: true, next_cursor: 'page-2' }
        }
        return { available: true, items: ['team/redis'], message: null,
          truncated: false, next_cursor: null }
      },
    )
    const wrapper = mount(NexusDockerBrowser, { props: { profileId: 'proxy-id' } })
    await flushPromises()
    await wrapper.get('input[aria-label="Поиск Docker-образов Nexus"]').setValue('redis')
    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()
    expect(exportsApi.listNexusRepositories).toHaveBeenCalledWith('proxy-id', 'redis', 'page-2')
    expect(wrapper.text()).toContain('team/redis')
    expect(wrapper.text()).toContain('прочитано страниц: 2')
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
    expect(wrapper.text()).toContain('Точный Docker image:tag, если дерево недоступно')
    expect(wrapper.emitted('add')).toBeUndefined()
    expect(exportsApi.inspectExactNexusImage).not.toHaveBeenCalled()
  })
})
