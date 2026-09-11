import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ContourBadge from './ContourBadge.vue'

describe('ContourBadge', () => {
  it.each(['SOURCE', 'TARGET'] as const)('renders %s contour as text', (contour) => {
    const wrapper = mount(ContourBadge, { props: { contour } })
    expect(wrapper.text()).toContain(`Контур: ${contour}`)
  })

  it('does not invent a contour when runtime config is absent', () => {
    const wrapper = mount(ContourBadge, { props: { contour: null } })
    expect(wrapper.text()).toContain('не определён')
  })
})
