import { describe, expect, it } from 'vitest'

import { apiErrorInfo } from './exports'

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
