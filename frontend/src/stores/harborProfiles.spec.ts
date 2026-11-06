import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as exportsApi from '@/api/exports'

import { useHarborProfilesStore } from './harborProfiles'

beforeEach(() => {
  setActivePinia(createPinia())
  sessionStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('Harbor profile selection store', () => {
  it('prefers default profile when no session preference exists', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        { id: 'profile-b', name: 'B', url: 'https://b.local', is_default: false },
        { id: 'default', name: 'Default Harbor', url: 'https://default.local', is_default: true },
      ],
    })
    const store = useHarborProfilesStore()

    expect(await store.load()).toBe(true)
    expect(store.selectedId).toBe('default')
    expect(sessionStorage.getItem('htp.harbor.profile-id')).toBe('default')
  })

  it('keeps an available session preference across workflows', async () => {
    sessionStorage.setItem('htp.harbor.profile-id', 'profile-b')
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        { id: 'default', name: 'Default Harbor', url: 'https://default.local', is_default: true },
        { id: 'profile-b', name: 'B', url: 'https://b.local', is_default: false },
      ],
    })
    const store = useHarborProfilesStore()

    await store.load()

    expect(store.selectedId).toBe('profile-b')
    expect(store.selectedProfile?.name).toBe('B')
  })

  it('falls back safely when the saved profile is no longer selectable', async () => {
    sessionStorage.setItem('htp.harbor.profile-id', 'removed-profile')
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        { id: 'profile-b', name: 'B', url: 'https://b.local', is_default: false },
      ],
    })
    const store = useHarborProfilesStore()

    await store.load()

    expect(store.selectedId).toBe('profile-b')
    expect(sessionStorage.getItem('htp.harbor.profile-id')).toBe('profile-b')
  })

  it('normalizes old DTOs to Harbor and preserves explicit Nexus type', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        { id: 'legacy', name: 'Legacy', url: 'https://legacy.example.test', is_default: true },
        {
          id: 'nexus', name: 'Regional Nexus', url: 'https://docker.example.test',
          is_default: false, registry_type: 'nexus',
        },
      ],
    })
    const store = useHarborProfilesStore()
    expect(await store.load()).toBe(true)
    expect(store.selectedProfile?.registry_type).toBe('harbor')
    expect(store.select('nexus')).toBe(true)
    expect(store.selectedProfile?.registry_type).toBe('nexus')
    expect(store.select('legacy')).toBe(true)
    expect(store.selectedProfile?.registry_type).toBe('harbor')
  })

  it('does not infer Nexus from an old DTO with Nexus in its name', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({
      items: [
        { id: 'legacy', name: 'Legacy Nexus migration', url: 'https://nexus.example.test',
          is_default: true },
      ],
    })
    const store = useHarborProfilesStore()
    expect(await store.load()).toBe(true)
    expect(store.selectedProfile?.registry_type).toBe('harbor')
  })

  it('fails closed when there are no selectable Harbor profiles', async () => {
    vi.spyOn(exportsApi, 'listHarborProfiles').mockResolvedValue({ items: [] })
    const store = useHarborProfilesStore()

    expect(await store.load()).toBe(false)
    expect(store.selectedId).toBeNull()
    expect(store.error?.code).toBe('harbor_profile_required')
  })
})
