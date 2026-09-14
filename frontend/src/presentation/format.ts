export interface DigestFormatOptions {
  maxLength: number
  headLength: number
  tailLength: number
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return 'размер неизвестен'
  const units = ['Б', 'КиБ', 'МиБ', 'ГиБ', 'ТиБ']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  const digits = index === 0 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${units[index]}`
}

export function shortDigest(
  value: string | null | undefined,
  options: DigestFormatOptions,
): string {
  if (!value) return '—'
  if (value.length <= options.maxLength) return value
  return `${value.slice(0, options.headLength)}…${value.slice(-options.tailLength)}`
}

export function formatDateTimeMedium(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(new Date(value))
}

export function formatDateTimeLocale(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('ru-RU')
}
