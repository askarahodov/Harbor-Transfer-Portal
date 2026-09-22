<script setup lang="ts">
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileArchive,
  FolderSearch,
  RefreshCw,
  ShieldCheck,
  Upload,
  XCircle,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  apiErrorInfo,
  verifyPhysicalHandoff,
  type ArtifactStatus,
  type MediaHandoffVerification,
  type OperationStatus,
} from '@/api/imports'
import HarborProjectCreationPanel from '@/components/HarborProjectCreationPanel.vue'
import ImportVerificationCard from '@/components/ImportVerificationCard.vue'
import ImportDestinationMapping from '@/components/ImportDestinationMapping.vue'
import ImportReceiptDestinations from '@/components/ImportReceiptDestinations.vue'
import StatePlaceholder from '@/components/StatePlaceholder.vue'
import WizardStepper from '@/components/WizardStepper.vue'
import {
  formatBytes,
  formatDateTimeMedium as formatDate,
  shortDigest as formatShortDigest,
} from '@/presentation/format'
import { useAuthStore } from '@/stores/auth'
import { useImportWizardStore } from '@/stores/importWizard'
import { useRuntimeStore } from '@/stores/runtime'

const wizard = useImportWizardStore()
const runtime = useRuntimeStore()
const auth = useAuthStore()
const fileInput = ref<HTMLInputElement | null>(null)
const handoffInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const browserFiles = ref<{ name: string; size: number }[]>([])
const browserSelectionError = ref('')
const handoffBusy = ref(false)
const handoffState = ref<'IDLE' | 'VERIFIED' | 'MISMATCH' | 'UNTRUSTED'>('IDLE')
const handoffResult = ref<MediaHandoffVerification | null>(null)
const handoffMessage = ref('')

const steps = [
  { id: 1, label: 'Приём и проверка' },
  { id: 2, label: 'Preview и конфликты' },
  { id: 3, label: 'Импорт и результат' },
] as const

const phaseLabels: Record<OperationStatus, string> = {
  CREATED: 'Создана',
  VALIDATING: 'Проверка',
  RUNNING: 'Выполнение',
  PACKAGING: 'Сборка',
  VERIFYING: 'Криптографическая проверка пакета',
  UPLOADED: 'Загружено',
  DISCOVERED: 'Обнаружено на носителе',
  READY: 'Пакет проверен — готов к решению',
  IMPORTING: 'Импорт в TARGET Harbor',
  VERIFYING_TARGET: 'Проверка результата в TARGET',
  COMPLETED: 'Завершено',
  FAILED: 'Завершено с ошибками',
  REJECTED: 'Пакет отклонён',
  CANCELLED: 'Отменено',
}

const artifactStatusLabels: Record<ArtifactStatus, string> = {
  PENDING: 'Ожидает',
  RUNNING: 'Импортируется',
  IMPORTED: 'Импортирован',
  SKIPPED: 'Пропущен',
  CONFLICT: 'Конфликт',
  FAILED: 'Ошибка',
  VERIFIED: 'Импортирован и проверен',
}

const activeFilename = computed(
  () => wizard.selectedFile?.name ?? wizard.operation?.bundle?.filename ?? wizard.preview?.bundle_filename ?? '—',
)
const activeSize = computed(
  () => wizard.selectedFile?.size ?? wizard.operation?.bundle?.size_bytes ?? wizard.preview?.bundle_size_bytes ?? null,
)
const uploadPercent = computed(() => {
  const progress = wizard.uploadProgress
  if (!progress || !progress.total || progress.total <= 0) return null
  return Math.min(100, Math.round((progress.loaded / progress.total) * 100))
})
const operationPercent = computed(() => {
  const progress = wizard.operation?.progress
  if (!progress || progress.progress_total <= 0) return null
  return Math.round((progress.progress_current / progress.progress_total) * 100)
})
const largeBundleGuidance = computed(() =>
  ['import_upload_too_large', 'operation_insufficient_disk'].includes(wizard.error?.code ?? ''),
)
const canOfferOverwrite = computed(
  () => Boolean(wizard.preview?.overwrite_allowed && auth.canStartTransfers),
)

function shortDigest(value: string | null | undefined): string {
  return formatShortDigest(value, { maxLength: 28, headLength: 18, tailLength: 8 })
}

function artifactLabel(item: { repository: string; reference?: string | null; name?: string | null; version?: string | null }): string {
  if (item.reference) return `${item.repository}:${item.reference}`
  if (item.name && item.version) return `${item.repository}/${item.name}:${item.version}`
  return item.repository
}

function chooseFile(): void {
  fileInput.value?.click()
}

function chooseHandoff(): void {
  handoffInput.value?.click()
}

function groupedFingerprint(value: string | null | undefined): string {
  if (!value) return '—'
  const prefix = value.startsWith('sha256:') ? 'sha256:' : ''
  const body = prefix ? value.slice(prefix.length) : value
  const grouped = body.match(/.{1,8}/g)?.join(' ') ?? body
  return `${prefix}${grouped}`
}

async function verifyHandoffFile(file: File | undefined): Promise<void> {
  if (!file) return
  handoffBusy.value = true
  handoffState.value = 'IDLE'
  handoffResult.value = null
  handoffMessage.value = ''
  try {
    const result = await verifyPhysicalHandoff(file)
    handoffResult.value = result
    handoffState.value = 'VERIFIED'
    handoffMessage.value = 'Подпись и фактический состав transfer media подтверждены.'
  } catch (error) {
    const info = apiErrorInfo(error, 'Не удалось проверить signed handoff.')
    handoffMessage.value = info.message
    handoffState.value =
      info.code === 'handoff_signer_untrusted' ? 'UNTRUSTED' : 'MISMATCH'
  } finally {
    handoffBusy.value = false
  }
}

async function onHandoffChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  await verifyHandoffFile(input.files?.[0])
  input.value = ''
}

async function submitBrowserFiles(files: FileList | File[] | undefined): Promise<void> {
  if (!files) return
  const selected = Array.from(files)
  browserFiles.value = selected.map((file) => ({ name: file.name, size: file.size }))
  browserSelectionError.value = ''

  const bundle = selected.find((file) => file.name.endsWith('.htp.tar.gz'))
  const sidecar = selected.find((file) => file.name.endsWith('.htp.tar.gz.sha256'))
  const handoff = selected.find((file) => file.name.endsWith('.htp-handoff.json'))
  if (selected.length !== 3 || !bundle || !sidecar || !handoff) {
    browserSelectionError.value =
      'Выберите ровно три файла одной доставки: .htp.tar.gz, matching .sha256 и .htp-handoff.json.'
    return
  }

  await wizard.uploadPhysicalHandoff({ bundle, sidecar, handoff })
}

async function onFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  await submitBrowserFiles(input.files ?? undefined)
  input.value = ''
}

async function onDrop(event: DragEvent): Promise<void> {
  dragging.value = false
  await submitBrowserFiles(event.dataTransfer?.files ?? undefined)
}

function downloadReceipt(): void {
  if (!wizard.receipt) return
  const blob = new Blob([`${JSON.stringify(wizard.receipt, null, 2)}\n`], {
    type: 'application/json;charset=utf-8',
  })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = `import-${wizard.receipt.operation_id}-receipt.json`
  anchor.click()
  URL.revokeObjectURL(href)
}

onMounted(async () => {
  if (!runtime.contour) {
    await runtime.loadRuntime()
  }
  if (runtime.contour === 'TARGET') {
    await wizard.initialize()
  }
})

onBeforeUnmount(() => {
  wizard.stopPolling()
})
</script>

<template>
  <section class="import-page" aria-labelledby="import-title">
    <header class="page-header">
      <div>
        <p class="eyebrow">TARGET workflow</p>
        <h1 id="import-title">Приём и импорт Offline Bundle</h1>
        <p class="lead">
          Пакет сначала проходит checksum, schema и Ed25519 signature verification. Изменение TARGET Harbor начинается только после verified preview и явного решения оператора.
        </p>
      </div>
    </header>

    <div v-if="runtime.contour !== 'TARGET'" class="notice notice--danger" role="alert">
      <XCircle :size="20" aria-hidden="true" />
      <div>
        <strong>Import workflow доступен только в контуре TARGET.</strong>
        <p>Эта установка не должна принимать offline bundle как TARGET.</p>
      </div>
    </div>

    <template v-else>
      <WizardStepper :steps="steps" :current-step="wizard.step" aria-label="Этапы импорта" />

      <div v-if="wizard.error" class="notice notice--danger" role="alert">
        <AlertTriangle :size="20" aria-hidden="true" />
        <div>
          <strong>{{ wizard.error.code }}</strong>
          <p>{{ wizard.error.message }}</p>
          <p v-if="largeBundleGuidance" class="notice__hint">
            Для большого пакета скопируйте <code>.htp.tar.gz</code> и его <code>.sha256</code> в настроенный incoming directory или смонтированный transfer media, затем используйте «Обнаружить готовые пакеты».
          </p>
        </div>
      </div>

      <section v-if="wizard.step === 1" class="panel" aria-labelledby="intake-title">
        <div class="panel__header">
          <div>
            <p class="eyebrow">Шаг 1</p>
            <h2 id="intake-title">Приём и криптографическая проверка</h2>
          </div>
          <ShieldCheck :size="28" aria-hidden="true" />
        </div>

        <label class="harbor-profile-select">
          <span>TARGET Harbor profile</span>
          <select
            :value="wizard.selectedHarborProfileId ?? ''"
            :disabled="wizard.operation !== null || wizard.busy === 'initialize' || wizard.harborProfiles.length === 0"
            @change="wizard.chooseHarborProfile(($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="profile in wizard.harborProfiles"
              :key="profile.id"
              :value="profile.id"
              :disabled="!profile.enabled || !profile.url"
            >
              {{ profile.name }} · {{ profile.url || 'не настроен' }}
            </option>
          </select>
          <small>
            Профиль фиксируется при intake и используется для preview, TARGET validation и import.
          </small>
        </label>

        <div class="intake-grid">
          <article class="intake-card">
            <h3>Физическая поставка через браузер</h3>
            <p>Выберите три файла одной доставки. Большой bundle передаётся raw stream, а signed handoff и .sha256 проверяются backend до preview.</p>
            <div
              :class="['drop-zone', { 'drop-zone--active': dragging }]"
              tabindex="0"
              role="button"
              aria-label="Выбрать три файла физической поставки"
              aria-describedby="bundle-drop-help"
              @click="chooseFile"
              @keydown.enter.prevent="chooseFile"
              @keydown.space.prevent="chooseFile"
              @dragenter.prevent="dragging = true"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
            >
              <Upload :size="28" aria-hidden="true" />
              <strong>Перетащите сюда 3 файла одной доставки</strong>
              <span id="bundle-drop-help">.htp.tar.gz + .sha256 + .htp-handoff.json</span>
            </div>
            <input
              ref="fileInput"
              class="visually-hidden"
              type="file"
              multiple
              accept=".gz,.htp.tar.gz,.sha256,.json,.htp-handoff.json,application/gzip,application/json,text/plain"
              @change="onFileChange"
            >
            <div v-if="browserFiles.length" class="browser-file-list">
              <div v-for="item in browserFiles" :key="item.name" class="file-summary">
                <FileArchive :size="20" aria-hidden="true" />
                <div>
                  <strong>{{ item.name }}</strong>
                  <span>{{ formatBytes(item.size) }}</span>
                </div>
              </div>
            </div>
            <p v-if="browserSelectionError" class="handoff-message">{{ browserSelectionError }}</p>
            <div
              v-if="wizard.busy === 'upload' && wizard.uploadProgress"
              class="progress-block"
              role="status"
              aria-live="polite"
              aria-label="Прогресс загрузки bundle"
            >
              <div>Загрузка: {{ uploadPercent === null ? formatBytes(wizard.uploadProgress.loaded) : `${uploadPercent}%` }}</div>
              <progress v-if="uploadPercent !== null" :value="uploadPercent" max="100">{{ uploadPercent }}%</progress>
            </div>
          </article>

          <article class="intake-card">
            <h3>Большой пакет / transfer media</h3>
            <p>Скопируйте archive, финальный <code>.sha256</code> и signed <code>.htp-handoff.json</code> в TARGET. Сначала проверьте handoff, затем запускайте discovery.</p>

            <div class="handoff-box">
              <div class="handoff-box__heading">
                <div>
                  <strong>Signed physical handoff</strong>
                  <small>Проверка выполняется до import и не изменяет Harbor.</small>
                </div>
                <span :class="['handoff-state', `handoff-state--${handoffState.toLowerCase()}`]">
                  {{ handoffState }}
                </span>
              </div>
              <button
                class="button button--secondary"
                type="button"
                :disabled="handoffBusy || wizard.busy !== null"
                @click="chooseHandoff"
              >
                <ShieldCheck :size="18" aria-hidden="true" />
                {{ handoffBusy ? 'Проверка…' : 'Проверить handoff' }}
              </button>
              <input
                ref="handoffInput"
                class="visually-hidden"
                type="file"
                accept=".json,.htp-handoff.json,application/json"
                @change="onHandoffChange"
              >
              <p v-if="handoffMessage" class="handoff-message">{{ handoffMessage }}</p>
              <dl v-if="handoffResult" class="handoff-metadata">
                <div><dt>Delivery</dt><dd>{{ handoffResult.delivery_id }}</dd></div>
                <div><dt>Created by</dt><dd>{{ handoffResult.created_by }}</dd></div>
                <div><dt>UTC</dt><dd>{{ formatDate(handoffResult.created_at) }}</dd></div>
                <div><dt>Signer</dt><dd><code>{{ groupedFingerprint(handoffResult.signing_key_fingerprint) }}</code></dd></div>
                <div><dt>Bundle SHA-256</dt><dd><code>{{ groupedFingerprint(handoffResult.bundle_sha256) }}</code></dd></div>
              </dl>
            </div>

            <button
              class="button button--secondary"
              type="button"
              :disabled="wizard.busy !== null || handoffState !== 'VERIFIED'"
              @click="wizard.discover"
            >
              <FolderSearch :size="18" aria-hidden="true" />
              Обнаружить готовые пакеты
            </button>
            <StatePlaceholder
              v-if="wizard.busy === 'discover'"
              compact
              kind="loading"
              title="Поиск готовых пакетов"
            />
            <StatePlaceholder
              v-else-if="wizard.discovered.length === 0"
              compact
              kind="empty"
              title="Готовые пакеты не найдены"
              description="После поиска здесь появятся только пакеты, которые backend безопасно claim-нул."
            />
            <div v-else class="discovery-list">
              <button
                v-for="item in wizard.discovered"
                :key="item.intake.operation_id"
                class="discovered-item"
                type="button"
                @click="wizard.selectOperation(item.intake.operation_id)"
              >
                <span>{{ item.operation.bundle?.filename ?? `Import #${item.intake.operation_id}` }}</span>
                <small>{{ formatBytes(item.operation.bundle?.size_bytes) }} · {{ phaseLabels[item.operation.status] }}</small>
              </button>
            </div>
          </article>
        </div>

        <ImportVerificationCard
          v-if="wizard.operation"
          :operation="wizard.operation"
          :preview="wizard.preview"
          :phase-label="phaseLabels[wizard.operation.status]"
          :active-filename="activeFilename"
          :active-size="activeSize"
          :can-cancel="wizard.canCancel"
          :busy="wizard.busy !== null"
          @refresh="wizard.refreshOperation()"
          @cancel="wizard.cancel"
        />
      </section>

      <section v-else-if="wizard.step === 2 && wizard.preview" class="panel" aria-labelledby="preview-title">
        <div class="panel__header">
          <div>
            <p class="eyebrow">Шаг 2 · package verified</p>
            <h2 id="preview-title">Preview TARGET и политика конфликтов</h2>
          </div>
          <CheckCircle2 :size="28" aria-hidden="true" />
        </div>

        <div class="notice notice--success">
          <ShieldCheck :size="20" aria-hidden="true" />
          <p>Пакет прошёл checksum, schema и signature verification. Это <strong>не означает</strong>, что артефакты уже импортированы.</p>
        </div>

        <dl class="metadata-grid metadata-grid--wide">
          <div><dt>Delivery ID</dt><dd>{{ wizard.preview.source_delivery_id }}</dd></div>
          <div><dt>SOURCE Harbor</dt><dd>{{ wizard.preview.source_harbor ?? 'нет данных в legacy preview' }}</dd></div>
          <div><dt>Создан</dt><dd>{{ formatDate(wizard.preview.source_created_at) }}</dd></div>
          <div><dt>Автор</dt><dd>{{ wizard.preview.source_created_by ?? '—' }}</dd></div>
          <div><dt>Portal SOURCE</dt><dd>{{ wizard.preview.source_portal_version ?? '—' }}</dd></div>
          <div><dt>Проверен TARGET</dt><dd>{{ formatDate(wizard.preview.verified_at) }}</dd></div>
          <div><dt>Bundle SHA-256</dt><dd :title="wizard.preview.bundle_sha256">{{ shortDigest(wizard.preview.bundle_sha256) }}</dd></div>
          <div><dt>Signing key fingerprint</dt><dd :title="wizard.preview.signing_key_fingerprint">{{ shortDigest(wizard.preview.signing_key_fingerprint) }}</dd></div>
        </dl>
        <div v-if="wizard.preview.source_comment" class="comment-box">
          <strong>Комментарий SOURCE</strong>
          <p>{{ wizard.preview.source_comment }}</p>
        </div>

        <ImportDestinationMapping />
        <HarborProjectCreationPanel />

        <div v-if="wizard.unresolved.length > 0" class="notice notice--danger" role="alert">
          <XCircle :size="20" aria-hidden="true" />
          <div>
            <strong>Import заблокирован.</strong>
            <p>UNKNOWN/ERROR нельзя трактовать как NEW. Сначала устраните проблему TARGET inspection.</p>
          </div>
        </div>

        <div v-if="wizard.conflicts.length > 0" class="conflict-box">
          <div class="notice notice--warning">
            <AlertTriangle :size="20" aria-hidden="true" />
            <div>
              <strong>Обнаружены точные CONFLICT</strong>
              <ul>
                <li v-for="item in wizard.conflicts" :key="item.index">
                  {{ artifactLabel(item) }} — expected {{ shortDigest(item.expected_digest) }}, TARGET {{ shortDigest(item.target_digest) }}
                </li>
              </ul>
            </div>
          </div>
          <p v-if="!canOfferOverwrite">
            Server-side policy не разрешает overwrite. Безопасное действие — остановить import и разрешить конфликт отдельно.
          </p>
          <label v-else class="overwrite-confirmation">
            <input v-model="wizard.overwriteConfirmed" type="checkbox">
            <span>Я подтверждаю перезапись <strong>только перечисленных выше CONFLICT</strong>. SAME останутся skip; UNKNOWN/ERROR по-прежнему блокируют import.</span>
          </label>
        </div>

        <div class="actions">
          <button
            v-if="wizard.conflicts.length === 0"
            class="button button--primary"
            type="button"
            :disabled="!wizard.canExecuteDefault || wizard.busy !== null"
            @click="wizard.execute(false)"
          >
            Импортировать NEW · пропустить SAME
          </button>
          <button
            v-else-if="canOfferOverwrite"
            class="button button--danger"
            type="button"
            :disabled="!wizard.canExecuteOverwrite || wizard.busy !== null"
            @click="wizard.execute(true)"
          >
            Импортировать с подтверждённым overwrite
          </button>
          <button class="button button--secondary" type="button" @click="wizard.reset">
            Выбрать другой пакет
          </button>
        </div>
      </section>

      <section v-else-if="wizard.step === 3 && wizard.operation" class="panel" aria-labelledby="result-title">
        <div class="panel__header">
          <div>
            <p class="eyebrow">Шаг 3 · Import #{{ wizard.operation.id }}</p>
            <h2 id="result-title">{{ phaseLabels[wizard.operation.status] }}</h2>
          </div>
          <RefreshCw v-if="['IMPORTING', 'VERIFYING_TARGET'].includes(wizard.operation.status)" :size="28" aria-hidden="true" />
          <CheckCircle2 v-else-if="wizard.operation.status === 'COMPLETED'" :size="28" aria-hidden="true" />
          <XCircle v-else :size="28" aria-hidden="true" />
        </div>

        <div
          class="progress-block"
          role="status"
          aria-live="polite"
          aria-label="Прогресс импорта"
        >
          <div>
            {{ wizard.operation.progress.progress_current }} / {{ wizard.operation.progress.progress_total || wizard.operation.progress.total_artifacts }}
            · imported/verified {{ wizard.operation.progress.successful_artifacts }}
            · skipped {{ wizard.operation.progress.skipped_artifacts }}
            · conflicts {{ wizard.operation.progress.conflict_artifacts }}
            · failed {{ wizard.operation.progress.failed_artifacts }}
          </div>
          <progress v-if="operationPercent !== null" :value="operationPercent" max="100">{{ operationPercent }}%</progress>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Артефакт</th><th>Результат</th><th>SOURCE digest</th><th>TARGET digest</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in wizard.operation.artifacts" :key="item.id">
                <td>{{ artifactLabel(item) }}</td>
                <td>{{ artifactStatusLabels[item.status] }}<span v-if="item.error_message"> · {{ item.error_message }}</span></td>
                <td :title="item.source_digest ?? undefined">{{ shortDigest(item.source_digest) }}</td>
                <td :title="item.target_digest ?? undefined">{{ shortDigest(item.target_digest) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="wizard.operation.status === 'FAILED'" class="notice notice--danger" role="alert">
          <AlertTriangle :size="20" aria-hidden="true" />
          <div>
            <strong>{{ wizard.operation.error_code ?? 'import_failed' }}</strong>
            <p>{{ wizard.operation.error_message ?? 'Import завершён с ошибками.' }}</p>
            <p>Уже успешно импортированные независимые артефакты не откатываются автоматически. Смотрите per-artifact результат и receipt.</p>
          </div>
        </div>

        <article v-if="wizard.receipt" class="receipt-card">
          <h3>Immutable receipt</h3>
          <dl class="metadata-grid">
            <div><dt>Delivery</dt><dd>{{ wizard.receipt.source_delivery_id }}</dd></div>
            <div><dt>Результат</dt><dd>{{ wizard.receipt.result }}</dd></div>
            <div><dt>Actor</dt><dd>{{ wizard.receipt.actor_username }}</dd></div>
            <div><dt>Завершён</dt><dd>{{ formatDate(wizard.receipt.finished_at) }}</dd></div>
          </dl>
          <ImportReceiptDestinations :receipt="wizard.receipt" />
          <div class="actions">
            <button class="button button--secondary" type="button" @click="downloadReceipt">
              <Download :size="18" aria-hidden="true" />
              Скачать receipt JSON
            </button>
            <RouterLink class="button button--secondary" to="/history">Перейти к истории операций</RouterLink>
          </div>
        </article>

        <div class="actions">
          <button
            v-if="wizard.canCancel"
            class="button button--danger"
            type="button"
            :disabled="wizard.busy !== null"
            @click="wizard.cancel"
          >
            Отменить import
          </button>
          <button
            v-if="['COMPLETED', 'FAILED', 'CANCELLED'].includes(wizard.operation.status)"
            class="button button--primary"
            type="button"
            @click="wizard.reset"
          >
            Принять следующий пакет
          </button>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.import-page { display: grid; gap: var(--space-6); }
.page-header { display: flex; justify-content: space-between; gap: var(--space-4); }
.eyebrow { margin: 0 0 var(--space-1); color: var(--color-action); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
h1, h2, h3, p { margin-top: 0; }
.lead { max-width: 850px; color: var(--color-text-muted); line-height: 1.6; }
.panel { display: grid; gap: var(--space-6); padding: var(--space-6); border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.harbor-profile-select { display: grid; gap: var(--space-2); max-width: 680px; font-weight: 700; }
.harbor-profile-select select { min-height: 42px; width: 100%; padding: 0 var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; font-weight: 400; }
.harbor-profile-select small { color: var(--color-text-muted); font-weight: 400; line-height: 1.4; }
.panel__header { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
.intake-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.intake-card, .receipt-card { padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.drop-zone { display: grid; place-items: center; gap: var(--space-2); min-height: 180px; margin: var(--space-4) 0; padding: var(--space-4); border: 2px dashed var(--color-border-control); border-radius: var(--radius-md); text-align: center; cursor: pointer; }
.drop-zone:hover, .drop-zone:focus-visible, .drop-zone--active { border-color: var(--color-action); background: var(--color-info-surface); }
.browser-file-list { display: grid; gap: var(--space-2); }
.file-summary { display: flex; gap: var(--space-3); align-items: center; padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.file-summary div { display: grid; gap: var(--space-1); }
.discovery-list { display: grid; gap: var(--space-2); margin-top: var(--space-3); }
.discovered-item { display: grid; gap: var(--space-1); text-align: left; padding: var(--space-3); border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); cursor: pointer; }
.discovered-item:hover, .discovered-item:focus-visible { border-color: var(--color-action); }
.metadata-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); margin: 0; }
.metadata-grid--wide { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.metadata-grid div { min-width: 0; padding: var(--space-3); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
dt { color: var(--color-text-muted); font-size: 12px; }
dd { margin: var(--space-1) 0 0; overflow-wrap: anywhere; font-weight: 600; }
.notice { display: flex; gap: var(--space-3); align-items: flex-start; padding: var(--space-4); border-radius: var(--radius-md); }
.notice p { margin-bottom: 0; }
.notice--danger { background: var(--color-danger-surface); color: var(--color-danger-text); }
.notice--warning { background: var(--color-warning-surface); color: var(--color-warning-text); }
.notice--success { background: var(--color-success-surface); color: var(--color-success-text); }
.notice__hint { margin-top: var(--space-2); }
.progress-block { display: grid; gap: var(--space-2); }
progress { width: 100%; height: 12px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: var(--space-3); border-bottom: 1px solid var(--color-border); text-align: left; vertical-align: top; }
th { color: var(--color-text-muted); font-size: 12px; }
.conflict-box { display: grid; gap: var(--space-3); }
.overwrite-confirmation { display: flex; align-items: flex-start; gap: var(--space-3); padding: var(--space-4); border: 1px solid var(--color-danger-text); border-radius: var(--radius-md); }
.comment-box { padding: var(--space-4); border-left: 4px solid var(--color-action); background: var(--color-surface-subtle); }
.actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.button { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2); padding: 0 var(--space-4); border: 1px solid transparent; border-radius: var(--radius-md); font: inherit; font-weight: 600; text-decoration: none; cursor: pointer; }
.button:disabled { cursor: not-allowed; opacity: .55; }
.button--primary { background: var(--color-action-surface); color: var(--color-on-accent); }
.button--secondary { border-color: var(--color-border-control); background: var(--color-surface); color: var(--color-text); }
.button--danger { background: var(--color-danger-text); color: var(--color-on-accent); }
.icon-button { min-width: 40px; min-height: 40px; display: grid; place-items: center; border: 1px solid var(--color-border-control); border-radius: var(--radius-md); background: var(--color-surface); cursor: pointer; }
.handoff-box { display: grid; gap: var(--space-3); margin-bottom: var(--space-3); padding: var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.handoff-box__heading { display: flex; justify-content: space-between; gap: var(--space-3); align-items: flex-start; }
.handoff-box__heading small { display: block; margin-top: var(--space-1); color: var(--color-text-muted); }
.handoff-state { display: inline-flex; padding: var(--space-1) var(--space-2); border-radius: var(--radius-full); background: var(--color-surface-subtle); font-size: 12px; font-weight: 800; }
.handoff-state--verified { background: var(--color-success-surface); color: var(--color-success-text); }
.handoff-state--mismatch, .handoff-state--untrusted { background: var(--color-danger-surface); color: var(--color-danger-text); }
.handoff-message { margin: 0; color: var(--color-text-muted); }
.handoff-metadata { display: grid; gap: var(--space-2); margin: 0; }
.handoff-metadata div { display: grid; gap: var(--space-1); }
.handoff-metadata dt { color: var(--color-text-muted); font-size: 12px; text-transform: uppercase; }
.handoff-metadata dd { margin: 0; overflow-wrap: anywhere; }
.muted { color: var(--color-text-muted); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (max-width: 900px) {
  .intake-grid, .metadata-grid, .metadata-grid--wide { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .intake-grid, .metadata-grid, .metadata-grid--wide { grid-template-columns: 1fr; }
  .panel { padding: var(--space-4); }
}
</style>