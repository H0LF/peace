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
export interface GoogleCalendar {
  available: boolean
  connected: boolean
  email: string | null
  sessions: boolean
  last_sync: string | null
  error: 'reconnect' | 'google' | null
  readonly_calendars: number
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
export interface PlanSlot {
  id: number | null
  starts_at: string
  ends_at: string
  title?: string | null
}
/** Work a recovery option leaves without enough time, or leaves out on purpose (minutes still to do). */
export interface WorkGap {
  id: number
  title: string
  minutes: number
}
export interface Proposal {
  id: number
  kind: 'replan' | 'recover_spread' | 'recover_extend' | 'recover_drop' | 'mail_cleanup' | 'mail_date' | 'mail_new' | 'requirements' | 'concepts'
  status: 'pending' | 'applied' | 'rejected' | 'superseded' | 'undone'
  title: string
  reason: string
  created_at: string
  decided_at: string | null
  evidence: { source: string; ref: string; quote: string; rule: string; before?: string | null; after?: string | null }[]
  item: { type: 'task' | 'exam'; id: number; title: string; course: string | null; at: string | null } | null
  preview: {
    removed: PlanSlot[]
    created: PlanSlot[]
    error: string | null
    short?: WorkGap[]
    dropped?: WorkGap[]
    messages?: CleanupItem[]
    diff?: RequirementDiff
    concepts?: ProposedConcept[]
  } | null
  result: { removed: PlanSlot[]; created: PlanSlot[]; dropped?: WorkGap[]; moved?: number[] } | null
}
export interface ReviewRules {
  deadline_earlier: boolean
  deadline_later: boolean
  assessment_new: boolean
  assessment_moved: boolean
  assignment_new: boolean
}
export interface FileRef {
  id: number
  name: string
  vault_path?: string | null
}

export interface DeliveryItem {
  id: number
  title: string
  course: CourseRef | null
  due_at: string
  overdue: boolean
  status: TaskStatus
  progress: number
  minutes: number
  estimated: boolean
  what: string[]
  brief: FileRef | null
}

export interface ExamItem {
  id: number
  title: string
  course: CourseRef | null
  starts_at: string
  location: string | null
  minutes: number | null
  why: string
  topics: { topic: string; minutes: number; files: FileRef[] }[]
}

export interface Workload {
  deliveries: DeliveryItem[]
  exams: ExamItem[]
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
  effort_factors?: Record<string, number>
  recovery_study_end: string
  recovery_max_daily_minutes: number
}
export interface TimerState {
  state: 'idle' | 'running' | 'paused'
  task?: { id: number; title: string } | null
  course?: { id: number; name: string } | null
  session_id?: number | null
  focus?: boolean
  since?: string
  running_since?: string | null
  banked_seconds?: number
  elapsed_seconds?: number
  forgotten?: boolean
  logged_minutes?: number
}
export interface StudyLogEntry {
  id: number
  starts_at: string
  ends_at: string | null
  origin: 'timer' | 'manual'
  minutes: number
  session_id: number | null
  task: { id: number; title: string } | null
  course: { id: number; name: string } | null
}
export interface StudyTimeReport {
  period: 'day' | 'week' | 'month'
  start: string
  end: string
  actual_minutes: number
  planned_minutes: number
  courses: { course_id: number | null; name: string; actual_minutes: number; planned_minutes: number }[]
  logs: StudyLogEntry[]
  calibration: { course_id: number; name: string; factor: number; tasks: number; current: number | null }[]
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
  material_id: number | null
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
  model: { name: string; reachable: boolean; paused?: string | null }
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
export interface MailLogin extends SourceLogin {
  clean_junk: boolean
  quota_mb: number | null
}
export type Integrations = Record<'inforestudante' | 'moodle', SourceLogin> & { email: MailLogin }
export type IntegrationsForm = Record<'inforestudante' | 'moodle', { enabled: boolean; url: string; username: string; password: string }> & {
  email: { enabled: boolean; url: string; username: string; password: string; clean_junk: boolean; quota_mb: number | null }
}
export interface MailStatus {
  configured: boolean
  enabled: boolean
  host: string
  last_run: string | null
  last_success: string | null
  error: string | null
  needs_login: boolean
  used_bytes: number | null
  quota_mb: number | null
  clean_junk: boolean
  keep_senders: string[]
  inbox: number
  junk: number
}
export interface MailMessage {
  id: number
  folder: 'inbox' | 'junk'
  sender: string
  sender_name: string
  subject: string
  sent_at: string | null
  size: number
  seen: boolean
  academic: boolean
  bulk: boolean
  notice: boolean
  course: { id: number; name: string } | null
  removed_at: string | null
  removed_by: string | null
  restorable: boolean
  snippet: string
  body?: string | null
  facts: { kind: 'deadline' | 'assessment'; title: string; at: string; timed: boolean; quote: string }[]
}
export type MasteryState = 'untested' | 'weak' | 'developing' | 'mastered'
export interface Mastery {
  state: MasteryState
  mean: number | null
  low: number | null
  high: number | null
  answers: number
  evidence: { source: string; score: number; difficulty: number; grade: 'verified' | 'estimated'; confirmed: boolean; answered_at: string; weight: number }[]
}
export interface ConceptRow {
  id: number
  name: string
  description: string
  origin: 'model' | 'student'
  files: { material_id: number; file: string }[]
  confidence: number | null
  mastery: Mastery
  requires: { link: number; id: number; name: string | null; approved: boolean }[]
}
export interface CourseConcepts {
  course: { id: number; name: string }
  concepts: ConceptRow[]
  has_topics: boolean
  proposing: { state: 'idle' | 'reading' | 'done' | 'error'; error?: string; proposal?: number | null }
  model: { available: boolean; paused: string | null }
}
export type ExamState = 'new' | 'reading' | 'review' | 'confirmed' | 'error'
export interface PastExamRow {
  id: number
  course_id: number
  title: string
  state: ExamState
  error: string | null
  pages: number
  questions: number
  created_at: string
}
export interface ExamProfile {
  exams: number
  questions: number
  kinds: { kind: string; label: string; weight: number; exams: number; questions: number }[]
}
export interface CoursePastExams {
  exams: PastExamRow[]
  profile: ExamProfile
  reader: { available: boolean; paused: string | null }
}
export interface ExamQuestionRow {
  id: number
  number: string
  weight: number | null
  kind: string
  kind_label: string
  text: string
  printed: string
  asks: string
  page: number | null
  concept_id: number | null
  status: 'draft' | 'ok' | 'discarded'
}
export type ExamQuestionIn = Pick<ExamQuestionRow, 'number' | 'weight' | 'kind' | 'text' | 'printed' | 'asks' | 'page' | 'concept_id'>
export interface PastExamDetail extends Omit<PastExamRow, 'questions'> {
  course: { id: number; name: string }
  page_names: string[]
  questions: ExamQuestionRow[]
  concepts: { id: number; name: string }[]
  kinds: { kind: string; label: string }[]
}
export interface QuestionBank {
  ready: number
  past: number
  discarded: number
  has_concepts: boolean
  generating: { state: 'idle' | 'reading' | 'done' | 'error'; error?: string; kept?: number; discarded?: number }
  attempts: { id: number; mode: PracticeMode; started_at: string; finished: boolean; questions: number; answered: number; right: number }[]
  model: { available: boolean; paused: string | null }
}
export type PracticeMode = 'practice' | 'timed' | 'weakness' | 'retake'
export interface PracticeResponse {
  choice?: number
  value?: number
  code?: string
  text?: string
}
export interface PracticeFeedback {
  state: 'pending' | 'grading' | 'waiting' | 'done' | 'error'
  status?: 'passed' | 'failed' | 'error'
  output?: string
  criteria?: { criterion: string; max: number; points: number; comment: string }[]
  feedback?: string
  confirmed?: boolean
}
export interface PracticeQuestion {
  id: number
  qtype: 'mcq' | 'numeric' | 'code' | 'written'
  text: string
  printed: string
  options: string[] | null
  language: 'python' | 'c' | 'sql' | null
  figure: { exam_id: number; page: number } | null
  concept: string | null
  kind_label: string
  /** In a running timed attempt only `response` is filled: no grade before the end. */
  answered: {
    response: PracticeResponse
    score: number | null
    grade: 'verified' | 'estimated'
    feedback: PracticeFeedback | null
    answer: { choice?: number; value?: number; tolerance?: number; solution?: string } | null
    explanation: string
  } | null
}
export interface PracticeAttempt {
  id: number
  mode: PracticeMode
  course: { id: number; name: string }
  started_at: string
  finished: boolean
  ends_at: string | null
  seconds_left: number | null
  questions: PracticeQuestion[]
}
export interface PracticeSummary {
  answered: number
  right: number
  questions: number
  grading: number
  concepts: {
    id: number
    name: string
    right: number
    answered: number
    state: MasteryState
    mean: number | null
    revisit: { material_id: number; file: string }[]
  }[]
}

export interface ProposedConcept {
  name: string
  description: string
  topics: string[]
  files: { material_id: number; file: string }[]
  requires: string[]
}
export type RequirementKind = 'functional' | 'constraint' | 'deliverable' | 'grading'
export type RequirementState = 'unverified' | 'verified' | 'missing'
export interface Requirement {
  id: number
  kind: RequirementKind
  text: string
  state: RequirementState
  verified_by: string | null
  evidence: { material_id: number | null; file: string; page: number | null; quote: string } | null
  origin: 'model' | 'student'
  edited: boolean
}
export interface ProjectCheck {
  id: number
  name: string
  command: string
  requirement_id: number | null
  origin: 'student' | 'model'
  approved: boolean
  last_run_at: string | null
  last_status: 'passed' | 'failed' | 'error' | null
  last_output: string | null
}
export interface Readiness {
  ready: boolean
  due_at: string | null
  counts: Record<RequirementState, number>
  total: number
  missing: string[]
  unverified: string[]
  failing: string[]
  not_run: string[]
  git: { branch?: string; changed?: number; commits?: string[]; error?: string } | null
  summary: string
}
export interface Delivery {
  task: { id: number; title: string; due_at: string | null; course: string | null }
  brief: { id: number; filename: string; title: string } | null
  suggested: number | null
  options: { id: number; filename: string; title: string; doc_kind: string | null }[]
  brief_changed: boolean
  reading: { state: 'idle' | 'reading' | 'done' | 'error'; error?: string; proposal?: number | null }
  model: { available: boolean; paused: string | null }
  requirements: Requirement[]
  checks: ProjectCheck[]
  project: string | null
  sandbox: boolean
  readiness: Readiness
}
export interface RequirementDiff {
  added: { kind: RequirementKind; text: string }[]
  removed: { id: number; text: string }[]
  changed: { id: number; before: string; text: string }[]
}
/** One message listed in a cleanup proposal (its Evidence). */
export interface CleanupItem {
  ref: string
  quote: string
  sender: string
  size: number
  rule: string
}
export interface PhonePrefs {
  enabled: boolean
  server: string
  topic: string
  mode: 'away' | 'always'
}
export interface Models {
  installed: string[]
  using: string | null
  chosen: string
  preferred: string[]
}
export interface Vault {
  name: string
  path: string
}
export interface ObsidianPrefs {
  chosen: string
  vaults: Vault[]
  vault: Vault | null
  pinned: boolean
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
export type HealthState = 'ok' | 'warn' | 'error' | 'off'
export interface Diagnostics {
  checked_at: string
  components: { id: string; name: string; state: HealthState; code: string | null; detail: string }[]
  last_job: { id: string; title: string; state: string; summary: string | null } | null
  history: { at: string; action: string }[]
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
  google: () => request<{ available: boolean }>('/auth/google'),
  googleAccount: () => request<{ available: boolean; email: string | null }>('/auth/google/account'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  session: () => request<void>('/auth/session'),

  today: () => request<Today>('/api/v1/today'),
  workload: () => request<Workload>('/api/v1/workload'),
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
  updateEvent: (id: number, data: Partial<{ title: string; kind: EventKind; starts_at: string; ends_at: string; course_id: number | null; location: string | null }>) =>
    request<CalEvent>(`/api/v1/events/${id}`, { method: 'PATCH', ...body(data) }),
  deleteEvent: (id: number) => request<void>(`/api/v1/events/${id}`, { method: 'DELETE' }),
  googleCalendar: () => request<GoogleCalendar>('/api/v1/google/calendar'),
  saveGoogleCalendar: (sessions: boolean) => request<GoogleCalendar>('/api/v1/google/calendar', { method: 'PUT', ...body({ sessions }) }),
  disconnectGoogleCalendar: (deleteCalendar: boolean) =>
    request<void>(`/api/v1/google/calendar?delete_calendar=${deleteCalendar}`, { method: 'DELETE' }),

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
  timer: () => request<TimerState>('/api/v1/timer'),
  startTimer: (b: { session_id?: number; task_id?: number; course_id?: number; focus?: boolean }) =>
    request<TimerState>('/api/v1/timer/start', { method: 'POST', ...body(b) }),
  pauseTimer: () => request<TimerState>('/api/v1/timer/pause', { method: 'POST' }),
  resumeTimer: () => request<TimerState>('/api/v1/timer/resume', { method: 'POST' }),
  stopTimer: (b: { ended_at?: string; complete_session?: boolean } = {}) => request<TimerState>('/api/v1/timer/stop', { method: 'POST', ...body(b) }),
  studyTime: (period: StudyTimeReport['period'], date?: string) => request<StudyTimeReport>(`/api/v1/study-time${qs({ period, date })}`),
  addStudyLog: (b: { starts_at: string; ends_at: string; task_id?: number }) => request<StudyLogEntry>('/api/v1/study-logs', { method: 'POST', ...body(b) }),
  deleteStudyLog: (id: number) => request<void>(`/api/v1/study-logs/${id}`, { method: 'DELETE' }),
  setCalibration: (course_id: number, factor: number | null) =>
    request<PlanPrefs>('/api/v1/study-time/calibration', { method: 'POST', ...body({ course_id, factor }) }),
  applyProposal: (id: number, keep: number[]) => request<Proposal>(`/api/v1/proposals/${id}/apply`, { method: 'POST', ...body({ keep }) }),
  delivery: (taskId: number) => request<Delivery>(`/api/v1/tasks/${taskId}/delivery`),
  setBrief: (taskId: number, material_id: number | null) =>
    request<{ ok: boolean }>(`/api/v1/tasks/${taskId}/brief`, { method: 'PUT', ...body({ material_id }) }),
  readRequirements: (taskId: number) => request<{ state: string }>(`/api/v1/tasks/${taskId}/requirements/read`, { method: 'POST' }),
  addRequirement: (taskId: number, r: { kind: RequirementKind; text: string }) =>
    request<Requirement>(`/api/v1/tasks/${taskId}/requirements`, { method: 'POST', ...body(r) }),
  updateRequirement: (id: number, r: Partial<Pick<Requirement, 'kind' | 'text' | 'state'>>) =>
    request<Requirement>(`/api/v1/requirements/${id}`, { method: 'PATCH', ...body(r) }),
  deleteRequirement: (id: number) => request<void>(`/api/v1/requirements/${id}`, { method: 'DELETE' }),
  setProject: (taskId: number, path: string | null) =>
    request<{ project: string | null }>(`/api/v1/tasks/${taskId}/project`, { method: 'PUT', ...body({ path }) }),
  addCheck: (taskId: number, c: { name: string; command: string; requirement_id: number | null }) =>
    request<ProjectCheck>(`/api/v1/tasks/${taskId}/checks`, { method: 'POST', ...body(c) }),
  deleteCheck: (id: number) => request<void>(`/api/v1/checks/${id}`, { method: 'DELETE' }),
  runCheck: (id: number) => request<ProjectCheck>(`/api/v1/checks/${id}/run`, { method: 'POST' }),
  exportReadiness: (taskId: number) => request<{ path: string }>(`/api/v1/tasks/${taskId}/readiness/export`, { method: 'POST' }),
  pastExams: (courseId: number) => request<CoursePastExams>(`/api/v1/courses/${courseId}/past-exams`),
  createPastExam: (courseId: number, title: string) => request<PastExamRow>(`/api/v1/courses/${courseId}/past-exams`, { method: 'POST', ...body({ title }) }),
  addPastExamFile: (id: number, file: File) =>
    request<PastExamRow>(`/api/v1/past-exams/${id}/files${qs({ name: file.name })}`, {
      method: 'POST',
      body: file,
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
    }),
  readPastExam: (id: number) => request<{ state: string }>(`/api/v1/past-exams/${id}/read`, { method: 'POST' }),
  pastExam: (id: number) => request<PastExamDetail>(`/api/v1/past-exams/${id}`),
  pastExamPage: (id: number, page: number) => `/api/v1/past-exams/${id}/pages/${page}`,
  addExamQuestion: (id: number, q: ExamQuestionIn) => request<ExamQuestionRow>(`/api/v1/past-exams/${id}/questions`, { method: 'POST', ...body(q) }),
  editExamQuestion: (id: number, q: ExamQuestionIn) => request<ExamQuestionRow>(`/api/v1/exam-questions/${id}`, { method: 'PUT', ...body(q) }),
  deleteExamQuestion: (id: number) => request<void>(`/api/v1/exam-questions/${id}`, { method: 'DELETE' }),
  confirmPastExam: (id: number) => request<PastExamRow>(`/api/v1/past-exams/${id}/confirm`, { method: 'POST' }),
  reopenPastExam: (id: number) => request<PastExamRow>(`/api/v1/past-exams/${id}/reopen`, { method: 'POST' }),
  deletePastExam: (id: number) => request<void>(`/api/v1/past-exams/${id}`, { method: 'DELETE' }),
  questionBank: (courseId: number) => request<QuestionBank>(`/api/v1/courses/${courseId}/practice`),
  generateQuestions: (courseId: number) => request<{ state: string }>(`/api/v1/courses/${courseId}/questions/generate`, { method: 'POST' }),
  startPractice: (courseId: number, mode: PracticeMode = 'practice', minutes?: number) =>
    request<{ id: number }>(`/api/v1/courses/${courseId}/practice`, { method: 'POST', ...body({ count: 10, mode, minutes }) }),
  practice: (id: number) => request<PracticeAttempt>(`/api/v1/practice/${id}`),
  answerPractice: (id: number, a: { question_id: number } & PracticeResponse) =>
    request<PracticeQuestion>(`/api/v1/practice/${id}/answer`, { method: 'POST', ...body(a) }),
  finishPractice: (id: number) => request<PracticeSummary>(`/api/v1/practice/${id}/finish`, { method: 'POST' }),
  gradePractice: (id: number, questionId: number, score: number | null) =>
    request<PracticeQuestion>(`/api/v1/practice/${id}/questions/${questionId}/grade`, { method: 'POST', ...body({ score }) }),
  concepts: (courseId: number) => request<CourseConcepts>(`/api/v1/courses/${courseId}/concepts`),
  proposeConcepts: (courseId: number) => request<{ state: string }>(`/api/v1/courses/${courseId}/concepts/propose`, { method: 'POST' }),
  addConcept: (courseId: number, c: { name: string; description?: string }) =>
    request<{ id: number }>(`/api/v1/courses/${courseId}/concepts`, { method: 'POST', ...body(c) }),
  updateConcept: (id: number, c: { name?: string; description?: string; confidence?: number; clear_confidence?: boolean }) =>
    request<{ id: number }>(`/api/v1/concepts/${id}`, { method: 'PATCH', ...body(c) }),
  deleteConcept: (id: number) => request<void>(`/api/v1/concepts/${id}`, { method: 'DELETE' }),
  linkConcepts: (concept_id: number, requires_id: number) =>
    request<{ id: number }>('/api/v1/concept-links', { method: 'POST', ...body({ concept_id, requires_id }) }),
  unlinkConcepts: (id: number) => request<void>(`/api/v1/concept-links/${id}`, { method: 'DELETE' }),
  applyConcepts: (id: number, skip: number[]) => request<Proposal>(`/api/v1/proposals/${id}/apply`, { method: 'POST', ...body({ skip }) }),
  mailStatus: () => request<MailStatus>('/api/v1/mail/status'),
  syncMail: () => request<MailStatus>('/api/v1/mail/sync', { method: 'POST' }),
  mailMessages: (view: string, q = '') => request<MailMessage[]>(`/api/v1/mail/messages${qs({ view, q: q || undefined })}`),
  mailMessage: (id: number) => request<MailMessage>(`/api/v1/mail/messages/${id}`),
  restoreMail: (id: number) => request<{ ok: boolean }>(`/api/v1/mail/messages/${id}/restore`, { method: 'POST' }),
  saveKeepSenders: (senders: string[]) => request<{ senders: string[] }>('/api/v1/mail/keep', { method: 'PUT', ...body({ senders }) }),
  recover: () => request<{ options: Proposal[]; note: string }>('/api/v1/recovery', { method: 'POST' }),
  proposals: (history = false) => request<Proposal[]>(`/api/v1/proposals${qs({ status: history ? 'history' : 'pending' })}`),
  decideProposal: (id: number, action: 'apply' | 'reject' | 'undo') => request<Proposal>(`/api/v1/proposals/${id}/${action}`, { method: 'POST' }),
  reviewRules: () => request<ReviewRules>('/api/v1/preferences/review'),
  saveReviewRules: (r: ReviewRules) => request<ReviewRules>('/api/v1/preferences/review', { method: 'PUT', ...body(r) }),
  audit: () => request<AuditEntry[]>('/api/v1/audit?limit=100'),

  syncStatus: () => request<SyncHealth[]>('/api/v1/sync/status'),
  syncRuns: () => request<SyncRun[]>('/api/v1/sync/runs?limit=30'),
  sync: () => request<{ started: string[]; already_running: string[] }>('/api/v1/sync', { method: 'POST' }),

  planner: () => request<PlanPrefs>('/api/v1/preferences/planner'),
  savePlanner: (p: PlanPrefs) => request<PlanPrefs>('/api/v1/preferences/planner', { method: 'PUT', ...body(p) }),
  integrations: () => request<Integrations>('/api/v1/preferences/integrations'),
  saveIntegrations: (p: IntegrationsForm) => request<Integrations>('/api/v1/preferences/integrations', { method: 'PUT', ...body(p) }),
  phone: () => request<PhonePrefs>('/api/v1/preferences/phone'),
  savePhone: (p: PhonePrefs) => request<PhonePrefs>('/api/v1/preferences/phone', { method: 'PUT', ...body(p) }),
  testPhone: () => request<void>('/api/v1/phone/test', { method: 'POST' }),
  obsidian: () => request<ObsidianPrefs>('/api/v1/preferences/obsidian'),
  chooseVault: (path: string) => request<ObsidianPrefs>('/api/v1/preferences/obsidian', { method: 'PUT', ...body({ path }) }),
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
  diagnostics: () => request<Diagnostics>('/api/v1/diagnostics'),
  notificationDiagnostics: () => request<NotificationDiagnostics>('/api/v1/notifications/diagnostics'),
  testNotification: () => request<PeaceNotification>('/api/v1/notifications/test', { method: 'POST' }),

  ask: (text: string, source: 'text' | 'voice' = 'text') => request<AssistantReply>('/api/v1/assistant/ask', { method: 'POST', ...body({ text, source }) }),
  hear: (wav: Blob) => request<{ text: string }>('/api/v1/voice/hear', { method: 'POST', body: wav, headers: { 'Content-Type': 'audio/wav' } }),
  /** The spoken reply, or null when spoken replies are off. */
  say: async (text: string) => {
    const r = await fetch('/api/v1/voice/say', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, ...body({ text }) })
    if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => null))?.detail ?? r.statusText)
    return r.status === 204 ? null : r.blob()
  },
  assistantMessages: () => request<AssistantMessage[]>('/api/v1/assistant/messages?limit=20'),
  assistantStatus: () => request<AssistantStatus>('/api/v1/assistant/status'),
  holo: () => request<{ on: boolean }>('/api/v1/holo'),
  setHolo: (on: boolean) => request<{ on: boolean }>('/api/v1/holo', { method: 'PUT', ...body({ on }) }),
  saveHoloRecording: (frames: unknown[]) => request<{ saved: string }>('/api/v1/holo/recording', { method: 'POST', ...body({ frames }) }),
  desktopJob: (id: string) => request<DesktopJob>(`/api/v1/desktop/jobs/${encodeURIComponent(id)}`),
  cancelDesktopJob: (id: string) => request<DesktopJob>(`/api/v1/desktop/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  focusOn: (minutes: number) => request<AssistantStatus['focus']>('/api/v1/focus', { method: 'POST', ...body({ minutes }) }),
  focusOff: () => request<AssistantStatus['focus']>('/api/v1/focus', { method: 'DELETE' }),
}
