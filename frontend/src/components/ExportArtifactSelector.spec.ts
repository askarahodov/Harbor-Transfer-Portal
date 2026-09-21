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

describe('ExportArtifactSelector', () => {
  it('keeps version search disabled until repository is selected', () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { artifacts: [], selectedRepository: null, search: '', busy: false, page: 1, total: 0, referencesFor: () => [], isSelected: () => false },
    })
    expect(wrapper.get('input[aria-label="Фильтр версии, tag или digest"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Выберите проект и репозиторий')
  })

  it('emits exact reference selection and search', async () => {
    const wrapper = mount(ExportArtifactSelector, {
      props: { artifacts: [artifact], selectedRepository: 'apps/demo', search: '', busy: false, page: 1, total: 1, referencesFor: (item) => item.references, isSelected: () => false },
    })
    const input = wrapper.get('input[aria-label="Фильтр версии, tag или digest"]')
    await input.setValue('1.0')
    expect(wrapper.emitted('update:search')?.at(-1)).toEqual(['1.0'])
    const choices = wrapper.findAll('input[type="checkbox"]')
    expect(choices).toHaveLength(2)
    await choices[0]!.setValue(true)
    expect(wrapper.emitted('toggle')?.[0]).toEqual([artifact, '1.0.0'])
  })

  it('shows unsupported OCI references only as diagnostics', () => {
    const unknown = { ...artifact, kind: 'unknown-oci' as const, references: ['release-2026.09'] }
    const wrapper = mount(ExportArtifactSelector, {
      props: { artifacts: [unknown], selectedRepository: 'apps/demo', search: '', busy: false, page: 1, total: 1, referencesFor: (item) => item.references, isSelected: () => false },
    })
    expect(wrapper.text()).toContain('release-2026.09')
    expect(wrapper.text()).toContain('Не поддерживается export v1')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0)
  })
})
