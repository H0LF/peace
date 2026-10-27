// Typed client for the PEACE API. Same origin: the session cookie travels automatically.

export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED'
export type EventKind = 'class' | 'exam' | 'appointment'
export type SessionStatus = 'planned' | 'done' | 'skipped'

export interface CourseRef {
  id: number
  name: string
  code: string | null
}
export interface Course extends CourseRef {
  url: string | null
  source: string
}
export interface Task {
  id: number
  title: string
  description: string
  kind: 'task' | 'assignment'
  course: CourseRef | null
  status: TaskStatus
  priority: number
  due_at: string | null
  estimated_minutes: number | null
  progress: number
  snoozed_until: string | null
  postponed_count: number
  completed_at: string | null
  source: string
  url: string | null
  obsidian_note: string | null
  created_at: string
  updated_at: string
}
export interface CalEvent {
  id: number
  title: string
  kind: EventKind
  course: CourseRef | null
  starts_at: string
  ends_at: string
  location: string | null
  source: string
  url: string | null
}
export interface StudySession {
  id: number
  task: { id: number; title: string } | null
  event?: { id: number; title: string } | null
  course_id: number | null
  starts_at: string
  ends_at: string
  status: SessionStatus
  reason: string
  progress_note?: string | null
  materials: StudyMaterial[]
}
/** A file to study for a task, chosen by content similarity, with a short pt-PT reason. */
export interface StudyMaterial {
  id: number
  title: string
  filename: string | null
  vault_path: string | null
  url: string | null
  score: number
  why: string
}
export interface Change {
  id: number
  created_at: string
  source: string
  entity: string
  entity_id: number | null
  kind: 'created' | 'updated' | 'removed'
  summary: string
  detail: Record<string, unknown>
  seen_at: string | null
}
export interface Deadline {
  type: 'task' | 'assignment' | 'exam'
  id: number
  title: string
  course: CourseRef | null
  due_at: string
  status: TaskStatus | null
  overdue: boolean
}
export interface Recommendation {
  task_id: number | null
  event_id?: number | null
  title: string
  minutes: number
  reasons: string[]
  text: string
  materials?: StudyMaterial[]
  course?: CourseRef | null
  due_at?: string | null
  starts_at?: string | null
  objective?: string
  warnings?: string[]
  available_minutes?: number
  readiness?: null
  assessment_weight?: null
  scheduled_minutes?: number
}
export interface SyncHealth {
  source: string
  last_status: 'running' | 'success' | 'failed' | 'auth_failed' | null
  last_run_at: string | null
  last_error: string | null
  last_success_at: string | null
  stale: boolean
  needs_login: boolean
  next_scheduled_at: string
}
export interface SyncRun {
  id: number
  source: string
  trigger: string
  status: string
  started_at: string
  finished_at: string | null
  error: string | null
  stats: { items?: Record<string, number>; changes?: Record<string, number> }
}
export interface AuditEntry {
  id: number
  created_at: string
  actor: string
  action: string
  detail: Record<string, unknown>
}
export interface Today {
  date: string
  now: string
  timezone: string
  events: CalEvent[]
  sessions: StudySession[]
  overdue: Task[]
  due_today: Task[]
  completed_today: Task[]
  priorities: { task: Task; points: number; reasons: string[] }[]
  recommendation: Recommendation | null
  upcoming: Deadline[]
  changes: Change[]
  sync: SyncHealth[]
}
export interface PlanPrefs {
  study_start: string
  study_end: string
  max_daily_minutes: number
  block_minutes: number
  break_minutes: number
  max_task_minutes_per_day: number
  default_task_minutes: number
  default_exam_minutes: number
}
export interface Material {
  id: number
  course: CourseRef | null
  section: string
  title: string
  kind: 'file' | 'link'
  filename: string | null
  size: number | null
  mimetype: string | null
  modified_at: string | null
  url: string | null
  vault_path: string | null
  created_at: string
}
export type GraphKind = 'course' | 'material' | 'concept' | 'task' | 'exam'
export interface GraphNode {
  id: string
  label: string
  kind: GraphKind
  course_id: number | null
  community: number | null
  degree: number
  vault_path: string | null
  note: string
}
export interface GraphLink {
  source: string
  target: string
  relation: 'contains' | 'mentions' | 'similar' | 'has_task' | 'has_exam' | 'study_with'
  inferred: boolean
  weight: number
  why: string | null
}
export type AssistantState = 'idle' | 'listening' | 'thinking' | 'speaking'
export interface AssistantItem {
  title: string
  detail?: string
  vault_path?: string | null
  url?: string | null
}
export interface AssistantReply {
  reply: string
  intent: string
  items: AssistantItem[]
  to: string | null
  via: 'model' | 'rules' | 'confirmed'
  model: string | null
  job: DesktopJob | null
}
export interface DesktopStepResult {
  tool: string
  ok: boolean
  verified: boolean
  code: string | null
  message: string
}
export interface DesktopJob {
  id: string
  title: string
  state: 'confirm' | 'pending' | 'running' | 'done' | 'partial' | 'failed' | 'cancelled' | 'expired'
  summary: string | null
  results: DesktopStepResult[]
  steps: number
}
export interface AssistantMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  source: 'text' | 'voice'
  created_at: string
  items: AssistantItem[]
  to: string | null
}
export interface AssistantStatus {
  state: AssistantState
  model: { name: string; reachable: boolean }
  desktop: { connected: boolean; seen_at: string | null; actions?: { categories?: string[]; hyprland?: boolean; browser?: boolean } }
  voice: { available: boolean; name: string | null; shortcut: string | null; voices: string[]; audio?: {
    inputs?: { id: string; name: string }[]; outputs?: { id: string; name: string }[];
    error?: string | null; ptt_bound?: boolean; latency?: Record<string, number>
    capture_available?: boolean; playback_available?: boolean; stt_available?: boolean
  } }
  focus: { active: boolean; until: string | null }
}
export interface SourceLogin {
  enabled: boolean
  url: string
  username: string
  has_password: boolean
}
export type Integrations = Record<'inforestudante' | 'moodle', SourceLogin>
export interface Models {
  installed: string[]
  using: string | null
  chosen: string
  preferred: string[]
}
export interface VoicePrefs {
  ptt: string
  speak: boolean
  voice: string
  language: 'pt' | 'en'
  input_device: string
  output_device: string
}
export interface VoiceTest {
  id: string
  state: 'pending' | 'running' | 'done' | 'error'
  message: string
  level: number
}
export interface Appearance {
  accent: string
  name: string
}
export interface NotifyPrefs {
  enabled: boolean
  quiet_start: string
  quiet_end: string
  briefing_at: string
  deadlines: boolean
  sessions: boolean
  changes: boolean
  missed: boolean
  integrations: boolean
  max_per_hour: number
  min_gap_minutes: number
}
export interface PeaceNotification {
  id: number
  kind: string
  title: string
  body: string
  url: string
  actions: string[]
  urgent: boolean
  created_at: string
  deliver_after: string
  expires_at: string | null
  delivered_at: string | null
}
export interface NotificationDiagnostics {
  enabled: boolean
  quiet_hours: boolean
  focus_active: boolean
  desktop_connected: boolean
  delivery_connected: boolean
  pending: number
  visual_confirmation: 'pending_user'
  delivery: {
    seen_at: string
    notify_send?: boolean
    session_bus?: boolean
    daemon_available?: boolean
    daemon?: string | null
    result?: 'accepted' | 'error'
    notification_id?: number
    native_id?: number
  } | null
}
export interface TaskInput {
  title: string
  description?: string
  kind?: 'task' | 'assignment'
  course_id?: number | null
  priority?: number
  due_at?: string | null
  estimated_minutes?: number | null
  status?: TaskStatus
  progress?: number
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (r.status === 401 && !path.startsWith('/auth/login')) {
    window.dispatchEvent(new Event('peace:unauthorized'))
  }
  if (!r.ok) {
    let detail = r.statusText
    try {
      const body = await r.json()
      detail = typeof body.detail === 'string' ? body.detail : (body.detail?.[0]?.msg ?? detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, detail)
  }
  return (r.status === 204 ? undefined : await r.json()) as T
}

const body = (data: unknown): RequestInit => ({ body: JSON.stringify(data) })
const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v))
  const out = s.toString()
  return out ? `?${out}` : ''
}

export const api = {
  login: (token: string) => request<void>('/auth/login', { method: 'POST', ...body({ token }) }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  session: () => request<void>('/auth/session'),

  today: () => request<Today>('/api/v1/today'),
  deadlines: (days: number) => request<Deadline[]>(`/api/v1/deadlines${qs({ days })}`),
  recommendation: () => request<Recommendation | null>('/api/v1/recommendation'),

  courses: () => request<Course[]>('/api/v1/courses'),
  materials: (course_id?: number) => request<Material[]>(`/api/v1/materials${qs({ course_id })}`),
  knowledgeGraph: () => request<{ nodes: GraphNode[]; links: GraphLink[] }>('/api/v1/knowledge/graph'),
  course: (id: number) => request<Course>(`/api/v1/courses/${id}`),

  tasks: (params: { status?: TaskStatus[]; course_id?: number; q?: string; due_after?: string; due_before?: string; limit?: number } = {}) => {
    const { status, ...rest } = params
    const statuses = (status ?? []).map((s) => `status=${s}`).join('&')
    const query = qs(rest)
    return request<Task[]>(`/api/v1/tasks${query}${statuses ? (query ? '&' : '?') + statuses : ''}`)
  },
  createTask: (data: TaskInput) => request<Task>('/api/v1/tasks', { method: 'POST', ...body(data) }),
  updateTask: (id: number, data: Partial<TaskInput>) => request<Task>(`/api/v1/tasks/${id}`, { method: 'PATCH', ...body(data) }),
  postponeTask: (id: number, until: string) => request<Task>(`/api/v1/tasks/${id}/postpone`, { method: 'POST', ...body({ until }) }),
  deleteTask: (id: number) => request<void>(`/api/v1/tasks/${id}`, { method: 'DELETE' }),

  events: (start: string, end: string, course_id?: number) => request<CalEvent[]>(`/api/v1/events${qs({ start, end, course_id })}`),
  createEvent: (data: { title: string; kind: EventKind; starts_at: string; ends_at: string; course_id?: number | null; location?: string | null }) =>
    request<CalEvent>('/api/v1/events', { method: 'POST', ...body(data) }),

  sessions: (start: string, end: string) => request<StudySession[]>(`/api/v1/sessions${qs({ start, end })}`),
  createSession: (data: { task_id: number | null; event_id?: number | null; starts_at: string; ends_at: string }) =>
    request<StudySession>('/api/v1/sessions', { method: 'POST', ...body(data) }),
  updateSession: (id: number, data: { status?: SessionStatus; starts_at?: string; ends_at?: string }) =>
    request<StudySession>(`/api/v1/sessions/${id}`, { method: 'PATCH', ...body(data) }),
  deleteSession: (id: number) => request<void>(`/api/v1/sessions/${id}`, { method: 'DELETE' }),
  planDay: (date?: string) => request<StudySession[]>(`/api/v1/plan/day${qs({ date })}`, { method: 'POST' }),
  planWeek: (start?: string) => request<StudySession[]>(`/api/v1/plan/week${qs({ start })}`, { method: 'POST' }),

  changes: (unseen = false) => request<Change[]>(`/api/v1/changes${qs({ unseen, limit: 100 })}`),
  markSeen: (ids?: number[]) => request<void>('/api/v1/changes/seen', { method: 'POST', ...body(ids ? { ids } : {}) }),
  audit: () => request<AuditEntry[]>('/api/v1/audit?limit=100'),

  syncStatus: () => request<SyncHealth[]>('/api/v1/sync/status'),
  syncRuns: () => request<SyncRun[]>('/api/v1/sync/runs?limit=30'),
  sync: () => request<{ started: string[]; already_running: string[] }>('/api/v1/sync', { method: 'POST' }),

  planner: () => request<PlanPrefs>('/api/v1/preferences/planner'),
  savePlanner: (p: PlanPrefs) => request<PlanPrefs>('/api/v1/preferences/planner', { method: 'PUT', ...body(p) }),
  integrations: () => request<Integrations>('/api/v1/preferences/integrations'),
  saveIntegrations: (p: Record<keyof Integrations, { enabled: boolean; url: string; username: string; password: string }>) =>
    request<Integrations>('/api/v1/preferences/integrations', { method: 'PUT', ...body(p) }),
  models: () => request<Models>('/api/v1/assistant/models'),
  chooseModel: (model: string) => request<void>('/api/v1/preferences/assistant', { method: 'PUT', ...body({ model }) }),
  voicePrefs: () => request<VoicePrefs>('/api/v1/preferences/voice'),
  saveVoicePrefs: (p: VoicePrefs) => request<VoicePrefs>('/api/v1/preferences/voice', { method: 'PUT', ...body(p) }),
  voiceTest: (action: 'meter' | 'stt' | 'tts', settings: VoicePrefs) => request<VoiceTest>('/api/v1/voice/test', { method: 'POST', ...body({ action, settings }) }),
  voiceTestStatus: () => request<VoiceTest | null>('/api/v1/voice/test'),
  appearance: () => request<Appearance>('/api/v1/preferences/appearance'),
  saveAppearance: (p: Appearance) => request<Appearance>('/api/v1/preferences/appearance', { method: 'PUT', ...body(p) }),
  notifyPrefs: () => request<NotifyPrefs>('/api/v1/preferences/notifications'),
  saveNotifyPrefs: (p: NotifyPrefs) => request<NotifyPrefs>('/api/v1/preferences/notifications', { method: 'PUT', ...body(p) }),
  notifications: () => request<PeaceNotification[]>('/api/v1/notifications?limit=100'),
  notificationDiagnostics: () => request<NotificationDiagnostics>('/api/v1/notifications/diagnostics'),
  testNotification: () => request<PeaceNotification>('/api/v1/notifications/test', { method: 'POST' }),

  ask: (text: string) => request<AssistantReply>('/api/v1/assistant/ask', { method: 'POST', ...body({ text }) }),
  assistantMessages: () => request<AssistantMessage[]>('/api/v1/assistant/messages?limit=20'),
  assistantStatus: () => request<AssistantStatus>('/api/v1/assistant/status'),
  desktopJob: (id: string) => request<DesktopJob>(`/api/v1/desktop/jobs/${encodeURIComponent(id)}`),
  cancelDesktopJob: (id: string) => request<DesktopJob>(`/api/v1/desktop/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  focusOn: (minutes: number) => request<AssistantStatus['focus']>('/api/v1/focus', { method: 'POST', ...body({ minutes }) }),
  focusOff: () => request<AssistantStatus['focus']>('/api/v1/focus', { method: 'DELETE' }),
}
