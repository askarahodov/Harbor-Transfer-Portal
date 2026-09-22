import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { HarborArtifact } from '@/api/exports'
import ExportArtifactSelector from './ExportArtifactSelector.vue'

const artifact: HarborArtifact = {
  kind: 'container-image',
  project: 'team',
  repository: 'apps/demo',
  references: ['1.0.0', 'latest'],
  digest: `sha256:${'a'.repeat(64)}`,
  size: 4096,
  pushed_at: null,
  media_type: null,
  artifact_type: null,
}

const baseProps = {
  search: '',
  busy: false,
  page: 1,
  total: 0,
  referencesFor: (item: HarborArtifact) => item.references,
}

describe('ExportArtifactSelector', () => {
  it('keeps version combobox disabled until repository is selected', () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [], selectedRepository: null },
    })
    expect(wrapper.get('input[role="combobox"][aria-label="Версия / tag"]').attributes('disabled')).toBeDefined()
  })

  it('selects an exact reference and adds immutable artifact metadata', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 1 },
    })
    const input = wrapper.get('input[role="combobox"][aria-label="Версия / tag"]')
    await input.trigger('focus')
    const options = wrapper.findAll('[role="option"]')
    expect(options).toHaveLength(2)
    expect(options[0]!.text()).toContain('1.0.0')
    expect(options[0]!.text()).toContain('Container image')
    await options[0]!.trigger('mousedown')
    expect(wrapper.text()).toContain('1.0.0')
    expect(wrapper.text()).toContain('4.00 КиБ')
    await wrapper.get('button.artifact-selector__add').trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual([artifact, '1.0.0'])
  })

  it('forwards server-backed version search', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 1 },
    })
    await wrapper.get('input[role="combobox"][aria-label="Версия / tag"]').setValue('1.0')
    expect(wrapper.emitted('update:search')?.at(-1)).toEqual(['1.0'])
  })

  it('shows unsupported OCI references only as diagnostics', () => {
    const unknown = { ...artifact, kind: 'unknown-oci' as const, references: ['release-2026.09'] }
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [unknown], selectedRepository: 'apps/demo', total: 1 },
    })
    expect(wrapper.text()).toContain('release-2026.09')
    expect(wrapper.text()).toContain('Неподдерживаемые OCI artifacts')
    expect(wrapper.findAll('[role="option"]')).toHaveLength(0)
  })
})
