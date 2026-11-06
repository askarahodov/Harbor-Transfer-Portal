<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import {
  apiErrorInfo, inspectExactNexusImage, listNexusRepositories, listNexusTags,
  type ExactNexusInspection,
} from '@/api/exports'

const props = defineProps<{
  profileId: string
  nexusRepository?: string | null
}>()
const emit = defineEmits<{
  add: [repository: string, reference: string, digest: string]
}>()

const query = ref('')
const tagQuery = ref('')
const knownImage = ref('')
const knownTag = ref('')
const knownError = ref<string | null>(null)
const validImageName = /^[a-z0-9]+(?:[._-][a-z0-9]+)*(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)*$/
const validTagOrDigest = /^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|sha256:[a-f0-9]{64})$/
const repositories = ref<string[]>([])
const tags = ref<string[]>([])
const selectedRepository = ref('')
const selectedTag = ref('')
const inspection = ref<ExactNexusInspection | null>(null)
const error = ref<string | null>(null)
const tagError = ref<string | null>(null)
const inspectionError = ref<string | null>(null)
const cursor = ref<string | null>(null)
const tagCursor = ref<string | null>(null)
const repoBusy = ref(false)
const tagsBusy = ref(false)
const inspectBusy = ref(false)
const pagesRead = ref(0)
const tagPagesRead = ref(0)
let generation = 0
let tagGeneration = 0
let inspectionGeneration = 0
let searchTimer: ReturnType<typeof setTimeout> | null = null
let tagTimer: ReturnType<typeof setTimeout> | null = null
const seenCursors = new Set<string>()
const seenTagCursors = new Set<string>()

function resetTag(): void {
  tagGeneration += 1
  inspectionGeneration += 1
  tags.value = []
  tagQuery.value = ''
  selectedTag.value = ''
  inspection.value = null
  inspectionError.value = null
  tagError.value = null
  tagCursor.value = null
  tagPagesRead.value = 0
  seenTagCursors.clear()
  tagsBusy.value = false
  inspectBusy.value = false
}
function resetCatalog(): void {
  generation += 1
  repositories.value = []
  cursor.value = null
  error.value = null
  pagesRead.value = 0
  seenCursors.clear()
  repoBusy.value = false
  selectedRepository.value = ''
  resetTag()
}
async function readCatalog(next = false): Promise<void> {
  const id = props.profileId
  const search = query.value.trim()
  if (!id || repoBusy.value) return
  if (!next) resetCatalog()
  const current = ++generation
  const pageCursor = next ? cursor.value : null
  if (next && (!pageCursor || seenCursors.has(pageCursor))) {
    cursor.value = null
    error.value = 'Получена повторяющаяся страница Nexus. Перечисление остановлено.'
    return
  }
  if (pageCursor) seenCursors.add(pageCursor)
  repoBusy.value = true
  error.value = null
  try {
    const data = await listNexusRepositories(id, search, pageCursor ?? undefined)
    if (current !== generation || id !== props.profileId || search !== query.value.trim()) return
    if (!data.available) {
      cursor.value = null
      error.value = data.message ?? 'Каталог Nexus недоступен для этой учётной записи.'
      return
    }
    pagesRead.value += 1
    repositories.value = [...new Set([...(next ? repositories.value : []), ...data.items])].sort()
    if (data.next_cursor && seenCursors.has(data.next_cursor)) {
      cursor.value = null
      error.value = 'Nexus вернул повторяющийся курсор. Перечисление остановлено.'
    } else {
      cursor.value = data.next_cursor ?? null
    }
  } catch (reason) {
    if (current === generation) {
      cursor.value = null
      error.value = apiErrorInfo(reason, 'Ошибка загрузки каталога Nexus.').message
    }
  } finally {
    if (current === generation) repoBusy.value = false
  }
}
/** Find matches on later server pages without forcing one click per page.
 * A hard eight-page budget per action bounds latency and Nexus load.
 */
async function searchAcrossPages(next = false): Promise<void> {
  const profile = props.profileId
  const search = query.value.trim()
  await readCatalog(next)
  if (!search) return
  for (let page = 1; page < 8; page += 1) {
    if (profile !== props.profileId || search !== query.value.trim()
        || repoBusy.value || !cursor.value || error.value
        || repositories.value.length >= 20) return
    await readCatalog(true)
  }
}

async function readTags(next = false): Promise<void> {
  const id = props.profileId
  const repository = selectedRepository.value
  const search = tagQuery.value.trim()
  if (!id || !repository || tagsBusy.value) return
  if (!next) {
    tagGeneration += 1
    tags.value = []
    tagCursor.value = null
    seenTagCursors.clear()
    tagPagesRead.value = 0
  }
  const current = ++tagGeneration
  const pageCursor = next ? tagCursor.value : null
  if (next && (!pageCursor || seenTagCursors.has(pageCursor))) {
    tagCursor.value = null
    tagError.value = 'Повторяющийся курсор версий. Перечисление остановлено.'
    return
  }
  if (pageCursor) seenTagCursors.add(pageCursor)
  tagsBusy.value = true
  tagError.value = null
  try {
    const data = await listNexusTags(id, repository, search, pageCursor ?? undefined)
    if (current !== tagGeneration || id !== props.profileId
        || repository !== selectedRepository.value || search !== tagQuery.value.trim()) return
    if (!data.available) {
      tagCursor.value = null
      tagError.value = data.message ?? 'Нет доступа к списку версий; используйте точный tag.'
      return
    }
    tagPagesRead.value += 1
    tags.value = [...new Set([...(next ? tags.value : []), ...data.items])].sort()
    if (data.next_cursor && seenTagCursors.has(data.next_cursor)) {
      tagCursor.value = null
      tagError.value = 'Nexus вернул повторяющийся курсор версий.'
    } else {
      tagCursor.value = data.next_cursor ?? null
    }
  } catch (reason) {
    if (current === tagGeneration) {
      tagCursor.value = null
      tagError.value = apiErrorInfo(reason, 'Ошибка чтения тегов Nexus.').message
    }
  } finally {
    if (current === tagGeneration) tagsBusy.value = false
  }
}
function chooseImage(name: string): void {
  selectedRepository.value = name
  resetTag()
  void readTags()
}

function openKnownImage(): void {
  const name = knownImage.value.trim()
  knownError.value = null
  if (!validImageName.test(name)) {
    knownError.value = 'Укажите имя Docker image без префикса v2/ и без имени Nexus repository.'
    return
  }
  // The catalog can be forbidden on Nexus proxy, while tags/list may still work.
  chooseImage(name)
}

async function inspectKnownImage(): Promise<void> {
  const name = knownImage.value.trim()
  const tag = knownTag.value.trim()
  knownError.value = null
  if (!validImageName.test(name)) {
    knownError.value = 'Укажите точное имя Docker image, например baseimage или team/app.'
    return
  }
  if (!validTagOrDigest.test(tag)) {
    knownError.value = 'Укажите точный Docker tag или sha256 digest выбранного образа.'
    return
  }
  if (selectedRepository.value !== name) {
    selectedRepository.value = name
    resetTag()
  }
  // Exact inspect uses the configured Docker v2 connector and Skopeo; it does
  // not require permissions for /v2/_catalog, Nexus REST Search or tags/list.
  await chooseTag(tag)
}
async function chooseTag(tag: string): Promise<void> {
  const id = props.profileId
  const repository = selectedRepository.value
  selectedTag.value = tag
  inspection.value = null
  inspectionError.value = null
  const current = ++inspectionGeneration
  inspectBusy.value = true
  try {
    const value = await inspectExactNexusImage(id, repository, tag)
    if (current === inspectionGeneration && id === props.profileId
        && repository === selectedRepository.value && tag === selectedTag.value) {
      inspection.value = value
    }
  } catch (reason) {
    if (current === inspectionGeneration) {
      inspectionError.value = apiErrorInfo(
        reason, 'Не удалось проверить Docker tag на выбранном connector.',
      ).message
    }
  } finally {
    if (current === inspectionGeneration) inspectBusy.value = false
  }
}
function scheduleSearch(): void {
  if (searchTimer) clearTimeout(searchTimer)
  resetCatalog()
  searchTimer = setTimeout(() => { void searchAcrossPages() }, 350)
}
function scheduleTagSearch(): void {
  if (tagTimer) clearTimeout(tagTimer)
  tagGeneration += 1
  tags.value = []
  tagCursor.value = null
  tagTimer = setTimeout(() => { void readTags() }, 350)
}
watch([knownImage, knownTag], () => {
  inspectionGeneration += 1
  inspection.value = null
  inspectionError.value = null
})
watch(() => props.profileId, () => {
  if (searchTimer) clearTimeout(searchTimer)
  if (tagTimer) clearTimeout(tagTimer)
  knownImage.value = ''
  knownTag.value = ''
  knownError.value = null
  query.value = ''
  resetCatalog()
  if (props.profileId) void readCatalog()
}, { immediate: true })
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
  if (tagTimer) clearTimeout(tagTimer)
  resetCatalog()
})
</script>

<template>
  <section class="nexus-browser" aria-label="Каталог Docker-образов Nexus">
    <header class="nexus-browser__heading">
      <div>
        <h3>Каталог Docker-образов</h3>
        <p class="muted">
          Nexus repository: <strong>{{ nexusRepository || 'текущий Docker connector' }}</strong>.
          Выберите образ, затем версию; digest проверяется перед добавлением.
        </p>
      </div>
      <button type="button" class="secondary-button" :disabled="repoBusy" @click="searchAcrossPages()">
        Обновить каталог
      </button>
    </header>
    <section class="nexus-browser__direct" aria-label="Проверить известный Docker image Nexus">
      <h4>Образ из Nexus Browse — по точному имени</h4>
      <p class="muted">
        Если в Nexus открыт <code>docker-proxy / v2 / baseimage</code>, здесь нужно ввести
        <strong>baseimage</strong>. <code>docker-proxy</code> — имя Nexus repository,
        а не часть имени образа и не Helm repository. Каталог может быть закрыт,
        но проверка конкретного образа остаётся доступна.
      </p>
      <form class="nexus-browser__direct-form" @submit.prevent="inspectKnownImage">
        <label class="nexus-browser__label">
          Имя Docker image внутри выбранного connector
          <input v-model="knownImage" type="text" autocomplete="off" maxlength="255"
            aria-label="Имя известного Docker image" placeholder="baseimage" />
        </label>
        <label class="nexus-browser__label">
          Точный tag или sha256 digest
          <input v-model="knownTag" type="text" autocomplete="off" maxlength="128"
            aria-label="Точный tag Docker image" placeholder="latest" />
        </label>
        <div class="nexus-browser__direct-actions">
          <button type="button" class="secondary-button" :disabled="tagsBusy || !knownImage.trim()"
            @click="openKnownImage">Показать теги этого образа</button>
          <button type="submit" class="secondary-button" :disabled="inspectBusy || !knownImage.trim() || !knownTag.trim()">
            {{ inspectBusy ? 'Проверяем…' : 'Проверить image и digest' }}
          </button>
        </div>
      </form>
      <p v-if="knownError" role="alert">{{ knownError }}</p>
      <p v-if="selectedRepository === knownImage.trim() && tagError" role="status" class="muted">
        Список тегов недоступен: {{ tagError }}. Если tag известен, введите его выше и проверьте digest напрямую.
      </p>
    </section>
    <details class="nexus-browser__catalog">
      <summary>Необязательный поиск через каталог Nexus Docker v2 / REST Search</summary>
      <p class="muted">Для больших proxy-репозиториев список может быть неполным или закрытым. Поиск не требуется для проверки известного image:tag.</p>
    <label class="nexus-browser__label">
      Найти образ в выбранном Nexus repository
      <input
        v-model="query" type="search" autocomplete="off"
        aria-label="Поиск Docker-образов Nexus" placeholder="Например, bitnami/redis"
        @input="scheduleSearch"
      />
    </label>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else role="status" class="muted">
      {{ repoBusy ? 'Читаем страницу Nexus…' : `Найдено ${repositories.length} образов, прочитано страниц: ${pagesRead}` }}
      {{ cursor ? '· В Nexus есть следующие страницы' : (pagesRead ? '· Доступные страницы просмотрены' : '') }}
    </p>
    <ul v-if="repositories.length" class="nexus-browser__list" aria-label="Docker-образы">
      <li v-for="name in repositories" :key="name">
        <button
          type="button" class="secondary-button"
          :aria-pressed="selectedRepository === name"
          @click="chooseImage(name)"
        >{{ name }}</button>
      </li>
    </ul>
    <button
      v-if="cursor" type="button" class="secondary-button"
      :disabled="repoBusy" @click="searchAcrossPages(true)"
    >{{ repoBusy ? 'Загрузка…' : 'Следующая страница образов' }}</button>
    <p v-if="!repositories.length && !repoBusy && pagesRead && cursor" class="muted">
      На прочитанных страницах совпадений нет. Продолжите поиск по следующим страницам.
    </p>
    </details>
    <section v-if="selectedRepository" class="nexus-browser__versions" aria-label="Версии Docker-образа">
      <h4>Версии: {{ selectedRepository }}</h4>
      <label class="nexus-browser__label">
        Фильтр tag
        <input v-model="tagQuery" type="search" autocomplete="off"
          aria-label="Поиск версий Docker" placeholder="Например, 1.0.0"
          @input="scheduleTagSearch"
        />
      </label>
      <p v-if="tagError" role="alert">{{ tagError }}</p>
      <p v-else class="muted" role="status">
        {{ tagsBusy ? 'Читаем версии…' : `Версий: ${tags.length} · страниц: ${tagPagesRead}` }}
        {{ tagCursor ? '· Есть следующие страницы' : '' }}
      </p>
      <ul v-if="tags.length" class="nexus-browser__list" aria-label="Теги Docker">
        <li v-for="tag in tags" :key="tag">
          <button type="button" class="secondary-button" :aria-pressed="selectedTag === tag"
            @click="chooseTag(tag)">{{ tag }}</button>
        </li>
      </ul>
      <button v-if="tagCursor" type="button" class="secondary-button"
        :disabled="tagsBusy" @click="readTags(true)">Следующая страница тегов</button>
      <p v-if="inspectionError" role="alert">{{ inspectionError }}</p>
      <p v-if="inspectBusy" role="status">Проверяем digest в Nexus…</p>
      <div v-if="inspection" class="nexus-browser__verified" role="status">
        <strong>Проверен: {{ inspection.repository }}:{{ inspection.reference }}</strong>
        <code>{{ inspection.digest }}</code>
        <button type="button" class="secondary-button"
          @click="emit('add', inspection.repository, inspection.reference, inspection.digest)">
          Добавить проверенный образ
        </button>
      </div>
    </section>
    <p class="muted">
      Каталог показывает доступные серверному профилю записи. Если Nexus proxy не
      перечисляет нужный образ, используйте ручной repository:tag ниже.
    </p>
  </section>
</template>

<style scoped>
.nexus-browser { display: grid; gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.nexus-browser__heading { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: var(--space-3); }
.nexus-browser__direct { display: grid; gap: var(--space-3); }
.nexus-browser__direct-form { display: grid; gap: var(--space-3); }
.nexus-browser__direct-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.nexus-browser__catalog { display: grid; gap: var(--space-3); }
.nexus-browser__catalog summary { cursor: pointer; font-weight: 600; margin-bottom: var(--space-3); }
.nexus-browser__direct code { overflow-wrap: anywhere; }
.nexus-browser h3, .nexus-browser h4 { margin: 0; }
.nexus-browser__label { display: grid; gap: var(--space-2); font-weight: 600; }
.nexus-browser input { width: 100%; min-height: 40px; padding: var(--space-2); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); }
.nexus-browser__list { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--space-2); max-height: 360px; overflow-y: auto; }
.nexus-browser__list button { width: 100%; text-align: left; overflow-wrap: anywhere; }
.nexus-browser__versions { display: grid; gap: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--color-border); }
.nexus-browser__verified { display: grid; gap: var(--space-2); overflow-wrap: anywhere; }
.nexus-browser__verified code { overflow-wrap: anywhere; }
</style>
