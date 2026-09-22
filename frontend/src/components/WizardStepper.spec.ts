import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import WizardStepper from './WizardStepper.vue'

const steps = [
  { id: 1, label: 'Выбор' },
  { id: 2, label: 'Проверка' },
  { id: 3, label: 'Готово' },
] as const

describe('WizardStepper', () => {
  it('marks current and completed steps with accessible state', () => {
    const wrapper = mount(WizardStepper, {
      props: {
        steps,
        currentStep: 2,
        ariaLabel: 'Этапы проверки',
      },
    })

    const items = wrapper.findAll('li')
    expect(wrapper.get('ol').attributes('aria-label')).toBe('Этапы проверки')
    expect(items).toHaveLength(3)
    expect(items[0]!.classes()).toContain('wizard-stepper__item--done')
    expect(items[1]!.classes()).toContain('wizard-stepper__item--active')
    expect(items[1]!.attributes('aria-current')).toBe('step')
    expect(items[2]!.attributes('aria-current')).toBeUndefined()
  })
})
