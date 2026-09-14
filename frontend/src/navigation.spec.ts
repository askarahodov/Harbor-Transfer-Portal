import { describe, expect, it } from 'vitest'

import { navigationForRole } from './navigation'

describe('role- and contour-aware navigation', () => {
  it('keeps viewer navigation read-only in any contour', () => {
    expect(navigationForRole('viewer', 'SOURCE').map((item) => item.to)).toEqual(['/', '/history'])
    expect(navigationForRole('viewer', 'TARGET').map((item) => item.to)).toEqual(['/', '/history'])
  })

  it('shows only SOURCE export workflow to operator', () => {
    expect(navigationForRole('operator', 'SOURCE').map((item) => item.to)).toEqual([
      '/',
      '/export',
      '/history',
    ])
  })

  it('shows only TARGET import workflow to operator', () => {
    expect(navigationForRole('operator', 'TARGET').map((item) => item.to)).toEqual([
      '/',
      '/import',
      '/history',
    ])
  })

  it('keeps admin management together with contour-specific transfer workflow', () => {
    expect(navigationForRole('admin', 'SOURCE').map((item) => item.to)).toEqual([
      '/',
      '/export',
      '/history',
      '/users',
      '/settings',
    ])
    expect(navigationForRole('admin', 'TARGET').map((item) => item.to)).toEqual([
      '/',
      '/import',
      '/history',
      '/users',
      '/settings',
    ])
  })

  it('does not guess a transfer route before contour is known', () => {
    expect(navigationForRole('operator').map((item) => item.to)).toEqual(['/', '/history'])
  })
})
