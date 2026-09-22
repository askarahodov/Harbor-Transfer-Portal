import { afterEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'

import { apiErrorInfo, getHarborConnection, listHarborProfiles, listHarborProjects } from './exports'

afterEach(() => vi.restoreAllMocks())

describe('apiErrorInfo', () => {
  it('reads the normalized backend error envelope', () => {
    const error = {
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          error: {
            code: 'operation_terminal',
            message: 'Операция уже завершена',
          },
        },
      },
    }

    expect(apiErrorInfo(error, 'fallback')).toEqual({
      code: 'operation_terminal',
      message: 'Операция уже завершена',
      status: 409,
    })
  })

  it('keeps compatibility with structured FastAPI detail payloads', () => {
    const error = {
      isAxiosError: true,
      response: {
        status: 409,
        data: {
          detail: {
            code: 'runtime_mode_busy',
            message: 'busy',
          },
        },
      },
    }

    expect(apiErrorInfo(error, 'fallback')).toEqual({
      code: 'runtime_mode_busy',
      message: 'busy',
      status: 409,
    })
  })
})


describe('profile-aware Harbor API', () => {
  it('loads safe selectable profiles', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { items: [{ id: 'profile-a', name: 'A', url: 'https://a.local', is_default: false }] },
    })

    await listHarborProfiles()

    expect(get).toHaveBeenCalledWith('/harbor/profiles')
  })

  it('passes selected profile through browse and connection queries', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({
        data: { pagination: { page: 1, page_size: 25, total: 0 }, items: [] },
      })
      .mockResolvedValueOnce({
        data: { connected: true, version: '2.14', auth_mode: 'db_auth' },
      })

    await listHarborProjects(1, 25, 'app', 'profile-a')
    await getHarborConnection('profile-a')

    expect(get).toHaveBeenNthCalledWith(1, '/harbor/projects', {
      params: {
        page: 1,
        page_size: 25,
        profile_id: 'profile-a',
        search: 'app',
      },
    })
    expect(get).toHaveBeenNthCalledWith(2, '/harbor/connection', {
      params: { profile_id: 'profile-a' },
    })
  })
})
