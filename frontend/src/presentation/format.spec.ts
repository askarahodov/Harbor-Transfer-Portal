import { describe, expect, it } from 'vitest'

import {
  formatBytes,
  formatDateTimeLocale,
  formatDateTimeMedium,
  shortDigest,
} from './format'

describe('presentation formatters', () => {
  it('formats binary byte sizes and preserves the unknown placeholder', () => {
    expect(formatBytes(null)).toBe('размер неизвестен')
    expect(formatBytes(0)).toBe('0 Б')
    expect(formatBytes(1024)).toBe('1.00 КиБ')
    expect(formatBytes(12 * 1024)).toBe('12.0 КиБ')
  })

  it('shortens digests using caller-provided presentation bounds', () => {
    const digest = 'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'

    expect(
      shortDigest(digest, { maxLength: 24, headLength: 18, tailLength: 8 }),
    ).toBe(`${digest.slice(0, 18)}…${digest.slice(-8)}`)
    expect(shortDigest('short', { maxLength: 24, headLength: 16, tailLength: 8 })).toBe('short')
    expect(shortDigest(null, { maxLength: 24, headLength: 16, tailLength: 8 })).toBe('—')
  })

  it('keeps the existing date placeholders and invalid-history fallback', () => {
    expect(formatDateTimeMedium(null)).toBe('—')
    expect(formatDateTimeLocale(undefined)).toBe('—')
    expect(formatDateTimeLocale('not-a-date')).toBe('not-a-date')
  })
})
