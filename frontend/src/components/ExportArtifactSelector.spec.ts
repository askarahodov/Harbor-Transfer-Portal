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
  it('keeps version search disabled until repository is selected', () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [], selectedRepository: null },
    })
    expect(wrapper.get('input[type="search"][aria-label="Версия / tag"]').attributes('disabled')).toBeDefined()
  })

  it('selects an exact reference and adds immutable artifact metadata', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 1 },
    })
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('1.0.0')
    expect(rows[0]!.text()).toContain('Container image')
    await rows[0]!.get('input[type="checkbox"]').setValue(true)
    expect(wrapper.text()).toContain('Выбрано: 1')
    expect(wrapper.text()).toContain('4.00 КиБ')
    await wrapper.get('button.artifact-selector__add').trigger('click')
    expect(wrapper.emitted('add')?.[0]).toEqual([artifact, '1.0.0'])
  })

  it('forwards server-backed version search', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 1 },
    })
    await wrapper.get('input[type="search"][aria-label="Версия / tag"]').setValue('1.0')
    expect(wrapper.emitted('update:search')?.at(-1)).toEqual(['1.0'])
  })

  it('shows unsupported OCI references only as diagnostics', () => {
    const unknown = { ...artifact, kind: 'unknown-oci' as const, references: ['release-2026.09'] }
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [unknown], selectedRepository: 'apps/demo', total: 1 },
    })
    expect(wrapper.text()).toContain('release-2026.09')
    expect(wrapper.text()).toContain('Неподдерживаемые OCI artifacts')
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
  })
  it('renders versions as a persistent full-width table instead of a combobox popup', () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 2 },
    })
    expect(wrapper.find('input[role="combobox"][aria-label="Версия / tag"]').exists()).toBe(false)
    expect(wrapper.get('table.artifact-selector__table').isVisible()).toBe(true)
    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
  })

  it('adds multiple checked references in one explicit action', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { ...baseProps, artifacts: [artifact], selectedRepository: 'apps/demo', total: 2 },
    })
    const checks = wrapper.findAll('tbody input[type="checkbox"]')
    await checks[0]!.setValue(true)
    await checks[1]!.setValue(true)
    await wrapper.get('button.artifact-selector__add').trigger('click')
    expect(wrapper.emitted('add')).toEqual([
      [artifact, '1.0.0'],
      [artifact, 'latest'],
    ])
  })

})
