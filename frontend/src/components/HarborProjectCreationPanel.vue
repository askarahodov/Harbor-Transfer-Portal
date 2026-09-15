<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Plus } from 'lucide-vue-next'
import { computed, reactive, ref } from 'vue'

import { apiErrorInfo } from '@/api/exports'
import { createHarborProject } from '@/api/harborProjects'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'

const emit = defineEmits<{
  created: [project: string]
}>()

const auth = useAuthStore()
const wizard = useImportWizardStore()
const confirmations = reactive<Record<string, string>>({})
const busyProject = ref<string | null>(null)
const errorMessage = ref<string | null>(null)
const successMessage = ref<string | null>(null)

const missingProjects = computed(() => {
  if (wizard.mappingDirty) return []
  const names = new Set<string>()
  for (const artifact of wizard.destinationPlan?.artifacts ?? []) {
    if (
      artifact.error_code === 'import_destination_project_missing' &&
      artifact.project_exists === false &&
      artifact.target_project
    ) {
      names.add(artifact.target_project)
    }
  }
  return [...names].sort((left, right) => left.localeCompare(right))
})

const isAdmin = computed(() => auth.user?.role === 'admin')
const isOperator = computed(() => auth.user?.role === 'operator')

async function createProject(project: string): Promise<void> {
  if (!isAdmin.value || confirmations[project] !== project || !wizard.operation) return
  busyProject.value = project
  errorMessage.value = null
  successMessage.value = null
  try {
    const result = await createHarborProject({
      name: project,
      public: false,
      operation_id: wizard.operation.id,
    })
    successMessage.value = result.created
      ? `Project ${project} создан в локальном TARGET Harbor.`
      : `Project ${project} уже существует в локальном TARGET Harbor.`
    confirmations[project] = ''
    emit('created', project)
  } catch (error) {
    const info = apiErrorInfo(error, `Не удалось создать TARGET Harbor project ${project}.`)
    errorMessage.value = `${info.code}: ${info.message}`
  } finally {
    busyProject.value = null
  }
}
</script>

<template>
  <section v-if="missingProjects.length > 0" class="project-create" aria-labelledby="project-create-title">
    <div class="project-create__heading">
      <AlertTriangle :size="19" aria-hidden="true" />
      <div>
        <h4 id="project-create-title">В TARGET Harbor отсутствуют projects</h4>
        <p>
          Создание project — отдельное административное действие. Оно не запускает Import;
          после создания destination plan будет проверен заново.
        </p>
      </div>
    </div>

    <div v-if="isAdmin" class="project-create__list">
      <article v-for="project in missingProjects" :key="project" class="project-create__item">
        <div>
          <strong>{{ project }}</strong>
          <small>Будет создан private project в Harbor, настроенном на этом Portal.</small>
        </div>
        <label>
          <span>Для подтверждения введите точное имя project</span>
          <input
            v-model="confirmations[project]"
            :aria-label="`Подтверждение создания project ${project}`"
            :placeholder="project"
            autocomplete="off"
          >
        </label>
        <button
          class="button button--secondary project-create__button"
          type="button"
          :disabled="confirmations[project] !== project || busyProject !== null"
          @click="createProject(project)"
        >
          <Plus :size="17" aria-hidden="true" />
          {{ busyProject === project ? 'Создание…' : `Создать ${project}` }}
        </button>
      </article>
    </div>

    <div v-else-if="isOperator" class="project-create__operator-note">
      <p>
        У оператора нет права создавать Harbor projects. Передайте администратору точные имена:
        <strong>{{ missingProjects.join(', ') }}</strong>.
      </p>
    </div>

    <div v-else class="project-create__operator-note">
      <p>Создание Harbor projects доступно только администратору.</p>
    </div>

    <p v-if="errorMessage" class="project-create__error" role="alert">{{ errorMessage }}</p>
    <p v-if="successMessage" class="project-create__success" role="status">
      <CheckCircle2 :size="17" aria-hidden="true" />
      {{ successMessage }}
    </p>
  </section>
</template>

<style scoped>
.project-create { margin-top: var(--space-4); padding: var(--space-4); border: 1px solid var(--color-alert-amber); border-radius: var(--radius-md); background: var(--color-sand); }
.project-create__heading { display: flex; gap: var(--space-2); align-items: flex-start; color: var(--color-alert-amber); }
.project-create__heading h4, .project-create__heading p { margin: 0; }
.project-create__heading p { margin-top: var(--space-1); color: var(--color-steel); }
.project-create__list { display: grid; gap: var(--space-3); margin-top: var(--space-4); }
.project-create__item { display: grid; grid-template-columns: minmax(180px, .8fr) minmax(240px, 1.3fr) auto; gap: var(--space-3); align-items: end; padding: var(--space-3); border-radius: var(--radius-md); background: white; }
.project-create__item small, .project-create__item label span { display: block; color: var(--color-steel); font-size: 12px; }
.project-create__item label { display: grid; gap: var(--space-1); }
.project-create__item input { min-height: 40px; border: 1px solid var(--color-mist); border-radius: var(--radius-md); padding: 0 var(--space-3); font: inherit; }
.project-create__operator-note { margin-top: var(--space-3); color: var(--color-deep-harbor); }
.project-create__operator-note p { margin: 0; }
.project-create__error { margin: var(--space-3) 0 0; color: var(--color-danger); }
.project-create__success { display: flex; align-items: center; gap: var(--space-2); margin: var(--space-3) 0 0; color: var(--color-transfer-green); }
@media (max-width: 900px) {
  .project-create__item { grid-template-columns: 1fr; align-items: stretch; }
}
</style>
