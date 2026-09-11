import { describe, expect, it } from 'vitest'

import { navigationForRole } from './navigation'

describe('role-aware navigation', () => {
  it('keeps viewer navigation read-only', () => {
    expect(navigationForRole('viewer').map((item) => item.to)).toEqual(['/', '/history'])
  })

  it('allows operator transfer workflows but not settings', () => {
    expect(navigationForRole('operator').map((item) => item.to)).toEqual([
      '/',
      '/export',
      '/import',
      '/history',
    ])
  })

  it('allows admin navigation to all current sections', () => {
    expect(navigationForRole('admin').map((item) => item.to)).toEqual([
      '/',
      '/export',
      '/import',
      '/history',
      '/settings',
    ])
  })
})
