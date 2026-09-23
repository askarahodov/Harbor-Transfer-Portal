import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  apiErrorInfo,
  listHarborProfiles,
  type ApiErrorInfo,
  type HarborProfileOption,
} from '@/api/exports'

const HARBOR_PROFILE_STORAGE_KEY = 'htp.harbor.profile-id'

function storageOrNull(): Storage | null {
  return typeof window === 'undefined' ? null : window.sessionStorage
}

function savedProfileId(): string | null {
  const value = storageOrNull()?.getItem(HARBOR_PROFILE_STORAGE_KEY)?.trim()
  return value || null
}

function saveProfileId(profileId: string): void {
  storageOrNull()?.setItem(HARBOR_PROFILE_STORAGE_KEY, profileId)
}

export const useHarborProfilesStore = defineStore('harbor-profiles', () => {
  const profiles = ref<HarborProfileOption[]>([])
  const selectedId = ref<string | null>(savedProfileId())
  const loading = ref(false)
  const error = ref<ApiErrorInfo | null>(null)

  const selectedProfile = computed(
    () => profiles.value.find((profile) => profile.id === selectedId.value) ?? null,
  )
  const hasProfiles = computed(() => profiles.value.length > 0)

  function chooseFallback(): HarborProfileOption | null {
    if (selectedId.value) {
      const saved = profiles.value.find((profile) => profile.id === selectedId.value)
      if (saved) return saved
    }
    return profiles.value.find((profile) => profile.is_default) ?? profiles.value[0] ?? null
  }

  async function load(force = false): Promise<boolean> {
    if (profiles.value.length > 0 && !force) return true
    loading.value = true
    error.value = null
    try {
      const response = await listHarborProfiles()
      profiles.value = response.items
      const fallback = chooseFallback()
      if (!fallback) {
        selectedId.value = null
        error.value = {
          code: 'harbor_profile_required',
          message: 'Нет доступных Harbor profiles. Обратитесь к администратору.',
        }
        return false
      }
      selectedId.value = fallback.id
      saveProfileId(fallback.id)
      return true
    } catch (requestError) {
      profiles.value = []
      selectedId.value = null
      error.value = apiErrorInfo(requestError, 'Не удалось загрузить доступные Harbor profiles.')
      return false
    } finally {
      loading.value = false
    }
  }

  function select(profileId: string): boolean {
    const profile = profiles.value.find((item) => item.id === profileId)
    if (!profile) return false
    selectedId.value = profile.id
    saveProfileId(profile.id)
    error.value = null
    return true
  }

  return {
    profiles,
    selectedId,
    selectedProfile,
    loading,
    error,
    hasProfiles,
    load,
    select,
  }
})
