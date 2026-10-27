// pt-PT formatting. Dates are shown in the browser's local time (the desktop runs in Europe/Lisbon).

const LOCALE = 'pt-PT'
const timeFmt = new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit' })
const longDayFmt = new Intl.DateTimeFormat(LOCALE, { weekday: 'long', day: 'numeric', month: 'long' })
const shortDayFmt = new Intl.DateTimeFormat(LOCALE, { weekday: 'short', day: 'numeric', month: 'short' })
const dayMonthFmt = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short' })
const monthFmt = new Intl.DateTimeFormat(LOCALE, { month: 'long', year: 'numeric' })
// pt-PT "short" weekdays are full words ("segunda"); grids need three letters.
const WEEKDAYS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
const MIN = 60_000
const HOUR = 60 * MIN
const DAY = 24 * HOUR

export const time = (d: string | Date) => timeFmt.format(new Date(d))
export const longDay = (d: Date) => cap(longDayFmt.format(d))
export const shortDay = (d: string | Date) => shortDayFmt.format(new Date(d)).replace('.', '')
export const dayMonth = (d: string | Date) => dayMonthFmt.format(new Date(d)).replace('.', '')
export const monthTitle = (d: Date) => cap(monthFmt.format(d))
export const weekday = (d: Date) => WEEKDAYS[d.getDay()]
export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}
export function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes())
}
export function startOfWeek(d: Date): Date {
  const day = startOfDay(d)
  return addDays(day, -((day.getDay() + 6) % 7))
}
export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}
/** Local calendar date as YYYY-MM-DD (what the API's `date` parameters expect). */
export function dayKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function dayLabel(d: Date, now = new Date()): string {
  const diff = Math.round((startOfDay(d).getTime() - startOfDay(now).getTime()) / DAY)
  if (diff === 0) return 'Hoje'
  if (diff === 1) return 'Amanhã'
  if (diff === -1) return 'Ontem'
  return cap(shortDay(d))
}

/** Group heading: ["Amanhã", "27/10"] or ["Quarta, 28/10", null] (the date is already in it). */
export function dayHeading(d: Date, now = new Date()): [string, string | null] {
  const label = dayLabel(d, now)
  return ['Hoje', 'Amanhã', 'Ontem'].includes(label) ? [label, dayMonth(d)] : [label, null]
}

export function duration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m}` : `${h} h`
}

/** "em 2 d 3 h", "em 45 min", "há 5 h". */
export function countdown(iso: string, now = new Date()): string {
  const diff = new Date(iso).getTime() - now.getTime()
  const abs = Math.abs(diff)
  let span: string
  if (abs < HOUR) span = `${Math.max(1, Math.round(abs / MIN))} min`
  else if (abs < 2 * DAY) {
    const total = Math.round(abs / MIN) // round first, so 1 h 59.6 min is "2 h", never "1 h 60 min"
    const h = Math.floor(total / 60)
    const m = total % 60
    span = abs < 10 * HOUR && m ? `${h} h ${m} min` : `${h} h`
  } else if (abs < 7 * DAY) {
    const d = Math.floor(abs / DAY)
    const h = Math.floor((abs % DAY) / HOUR)
    span = h ? `${d} d ${h} h` : `${d} d`
  } else span = plural(Math.round(abs / DAY), 'dia', 'dias')
  return diff >= 0 ? `em ${span}` : `há ${span}`
}

export function ago(iso: string, now = new Date()): string {
  const diff = now.getTime() - new Date(iso).getTime()
  if (diff < MIN) return 'agora mesmo'
  return countdown(iso, now)
}

export type Urgency = 'overdue' | 'critical' | 'soon' | 'week' | 'later'

export function urgency(iso: string | null, now = new Date()): Urgency {
  if (!iso) return 'later'
  const diff = new Date(iso).getTime() - now.getTime()
  if (diff < 0) return 'overdue'
  if (diff < DAY) return 'critical'
  if (diff < 3 * DAY) return 'soon'
  if (diff < 7 * DAY) return 'week'
  return 'later'
}

export function greeting(now = new Date()): string {
  const h = now.getHours()
  return h < 6 ? 'Boa noite' : h < 12 ? 'Bom dia' : h < 20 ? 'Boa tarde' : 'Boa noite'
}

/** ISO string to the value a <input type="datetime-local"> expects. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${dayKey(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
/** datetime-local value (local time) to an ISO string with offset, as the API requires. */
export const fromLocalInput = (value: string) => (value ? new Date(value).toISOString() : null)

/** Stable, readable colour per course. */
export const courseHue = (id: number) => (id * 137) % 360
