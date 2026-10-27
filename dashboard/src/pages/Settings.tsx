import { LogOut, Moon, Sun } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { SOURCE_NAME, SyncButton, SyncHealthList } from '../components/sync'
import NotificationDiagnostics from '../components/NotificationDiagnostics'
import { Badge, Button, Card, Checkbox, ErrorNote, Field, Input, PageHeader, Segmented, Select, Skeleton, cx, useToast } from '../components/ui'
import { DEFAULT_ACCENT, accentVariants, applyAccent, contrast, isHex, useTheme } from '../lib/theme'
import { OrbMark } from '../components/Layout'
import { api, type Appearance, type Integrations, type NotifyPrefs, type PlanPrefs, type VoicePrefs } from '../lib/api'
import { useApi, useNow } from '../lib/hooks'

const hhmm = (t: string) => t.slice(0, 5)
const PRESETS = [
  { hex: '#ffb547', name: 'Dourado' },
  { hex: '#ff8a3d', name: 'Âmbar' },
  { hex: '#6fd3ff', name: 'Ciano' },
  { hex: '#a78bfa', name: 'Violeta' },
  { hex: '#4ade80', name: 'Verde' },
  { hex: '#f472b6', name: 'Rosa' },
]
const rgbOf = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))

const MODIFIERS = ['SUPER', 'SUPER + SHIFT', 'SUPER + ALT', 'CTRL + ALT']
const KEYS = [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789']

/** Supertonic's ids (F1…F5) as people say them. */
const voiceName = (v: string) => (/^F\d$/.test(v) ? `Voz ${v.slice(1)}` : v)

/** Push-to-talk key, spoken replies and voice; the PC's agent applies them within 30 s. */
function VoiceForm() {
  const toast = useToast()
  const prefs = useApi(() => api.voicePrefs(), [], 0)
  const status = useApi(() => api.assistantStatus(), [], 15_000)
  const [form, setForm] = useState<VoicePrefs | null>(null)
  const [testing, setTesting] = useState(false)
  const test = useApi(() => api.voiceTestStatus(), [], testing ? 500 : 0)
  useEffect(() => {
    if (test.data?.state === 'done' || test.data?.state === 'error') setTesting(false)
  }, [test.data])
  useEffect(() => {
    if (prefs.data) setForm((f) => f ?? prefs.data!) // seed once; a focus reload keeps unsaved edits
  }, [prefs.data])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form) return <Skeleton className="h-32" />
  const parts = form.ptt.split(' + ')
  const key = parts.at(-1)!
  const mods = parts.slice(0, -1).join(' + ')
  const voices = status.data?.voice.voices ?? []
  const connected = !!status.data?.desktop.connected
  const audio = status.data?.voice.audio

  async function testAudio(action: 'meter' | 'stt' | 'tts', voice?: string) {
    try {
      const result = await api.voiceTest(action, voice ? { ...form!, voice } : form!)
      test.setData(result)
      setTesting(true)
    } catch (err) {
      toast(`Não foi possível testar: ${(err as Error).message}`, 'error')
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.saveVoicePrefs(form!)
      toast('Voz guardada: o PC aplica dentro de 30 s', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-4">
      <p className="text-[13px] text-muted">
        {connected
          ? `Agente do PC ligado${status.data?.voice.available ? ` · ${status.data.voice.name}` : audio?.stt_available ? ' · serviço de fala ligado; ativação por voz indisponível' : ' · VoiceStudio indisponível'}.`
          : 'O agente do PC está desligado: as alterações aplicam-se quando voltar.'}
      </p>
      {audio?.error && <p role="status" className="text-sm text-muted">{audio.error}</p>}
      {connected && <p className="text-sm text-muted">Atalho: {audio?.ptt_bound ? 'registado no Hyprland' : 'sem confirmação de registo no Hyprland'}.</p>}
      {(['input_device', 'output_device'] as const).map((device) => {
        const inputs = device === 'input_device'
        const devices = (inputs ? audio?.inputs : audio?.outputs) ?? []
        return <Field key={device} label={inputs ? 'Microfone' : 'Saída de áudio'} hint={!devices.length ? 'A lista é consultada no PC; nenhum dispositivo disponível agora.' : undefined}>
          <Select aria-label={inputs ? 'Microfone' : 'Saída de áudio'} value={form[device] ?? ''} onChange={(e) => setForm({ ...form, [device]: e.target.value })}>
            <option value="">Predefinido do sistema</option>
            {form[device] && !devices.some((d) => d.id === form[device]) && <option value={form[device]}>{form[device]} (indisponível)</option>}
            {devices.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </Select>
        </Field>
      })}
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Manter premido para falar">
          <Select aria-label="Teclas modificadoras" value={mods} onChange={(e) => setForm({ ...form, ptt: `${e.target.value} + ${key}` })} className="w-40">
            {MODIFIERS.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </Select>
        </Field>
        <span className="pb-2 text-muted">+</span>
        <Field label="Tecla">
          <Select aria-label="Tecla" value={key} onChange={(e) => setForm({ ...form, ptt: `${mods} + ${e.target.value}` })} className="w-20">
            {KEYS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </Select>
        </Field>
      </div>
      <Checkbox checked={form.speak} onChange={(speak) => setForm({ ...form, speak })} label="Responder em voz alta (senão, só por notificação)" />
      <Field label="Voz" hint={voices.length ? undefined : 'A lista vem do PC quando o agente e o VoiceStudio estão ligados'}>
        <Select aria-label="Voz" value={form.voice} disabled={!voices.length} onChange={(e) => setForm({ ...form, voice: e.target.value })} className="w-40">
          {voices.map((v) => (
            <option key={v} value={v}>{voiceName(v)}</option>
          ))}
        </Select>
      </Field>
      <Field label="Língua da resposta falada" hint="Percebe sempre o que dizes em português; no ecrã a resposta fica em português">
        <Select aria-label="Língua da resposta falada" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value as 'pt' | 'en' })} className="w-40">
          <option value="en">Inglês (soa melhor)</option>
          <option value="pt">Português</option>
        </Select>
      </Field>
      {voices.length > 0 && (
        <div className="grid gap-1.5">
          <p className="text-sm text-muted">Ouvir cada voz nas colunas do PC (não muda a escolha; uma de cada vez):</p>
          <div className="flex flex-wrap gap-2">
            {voices.map((v) => (
              <Button key={v} size="sm" variant={form.voice === v ? 'primary' : 'secondary'} aria-label={`Ouvir a voz ${voiceName(v)}`}
                disabled={!connected || testing || !audio?.playback_available} onClick={() => void testAudio('tts', v)}>
                ▶ {voiceName(v)}
              </Button>
            ))}
          </div>
        </div>
      )}
      <div className="grid gap-2">
        <p className="text-sm text-muted">Os testes de entrada captam 5 segundos apenas após clicares. O teste de transcrição não envia comandos ao assistente. O áudio é apagado no fim.</p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={!connected || testing} onClick={() => void testAudio('meter')}>Testar microfone (5 s)</Button>
          <Button type="button" disabled={!connected || testing || !audio?.stt_available} onClick={() => void testAudio('stt')}>Testar transcrição (5 s)</Button>
          <Button type="button" disabled={!connected || testing || !audio?.stt_available || !audio?.playback_available} onClick={() => void testAudio('tts')}>Ouvir voz de teste</Button>
        </div>
        <label className="grid gap-1 text-sm">Nível de entrada<meter aria-label="Nível de entrada do microfone" min={0} max={1} value={test.data?.level ?? 0} className="w-full" /></label>
        {test.data && <p role="status" className="text-sm text-muted">{test.data.message}</p>}
        {test.error && <ErrorNote error={test.error} onRetry={test.reload} />}
        <p className="text-sm text-muted">O reconhecimento usa português (pt). A voz pt não garante sotaque europeu; compara as vozes por ouvido.</p>
        {audio?.latency && Object.keys(audio.latency).length > 0 && <p className="text-sm text-muted">Última execução: {Object.entries(audio.latency).map(([stage, ms]) => `${stage.replace('_ms', '')}: ${ms} ms`).join(' · ')}.</p>}
      </div>
      <div>
        <Button variant="primary" type="submit">
          Guardar voz
        </Button>
      </div>
    </form>
  )
}

/** Which sources PEACE syncs, and their logins. Passwords are write-only: empty keeps the saved one. */
function IntegrationsForm({ onSaved }: { onSaved: () => void }) {
  const toast = useToast()
  const prefs = useApi(() => api.integrations(), [], 0)
  type Row = { enabled: boolean; username: string; password: string }
  const [form, setForm] = useState<Record<keyof Integrations, Row> | null>(null)
  useEffect(() => {
    if (prefs.data && !form) {
      const row = (s: keyof Integrations) => ({ enabled: prefs.data![s].enabled, username: prefs.data![s].username, password: '' })
      setForm({ inforestudante: row('inforestudante'), moodle: row('moodle') })
    }
  }, [prefs.data, form])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form || !prefs.data) return <Skeleton className="h-32" />

  const set = (s: keyof Integrations, patch: Partial<Row>) => setForm({ ...form, [s]: { ...form[s], ...patch } })
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      prefs.setData(await api.saveIntegrations(form!))
      setForm({ inforestudante: { ...form!.inforestudante, password: '' }, moodle: { ...form!.moodle, password: '' } })
      toast('Integrações guardadas', 'ok')
      onSaved()
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-5">
      {(['inforestudante', 'moodle'] as const).map((s) => (
        <fieldset key={s} className="grid gap-3">
          <label className="flex items-center gap-2 text-[13px] font-medium">
            <Checkbox checked={form[s].enabled} onChange={(enabled) => set(s, { enabled })} label={`Ligar ${SOURCE_NAME[s]}`} />
            {SOURCE_NAME[s]}
          </label>
          <div className="flex flex-wrap gap-3">
            <Field label="Utilizador" className="sm:w-60">
              <Input value={form[s].username} maxLength={200} autoComplete="off" disabled={!form[s].enabled} onChange={(e) => set(s, { username: e.target.value })} />
            </Field>
            <Field label="Palavra-passe" hint={prefs.data![s].has_password ? 'Guardada. Deixa vazio para manter.' : 'Ainda não definida.'} className="sm:w-60">
              <Input type="password" value={form[s].password} maxLength={200} autoComplete="new-password" disabled={!form[s].enabled} onChange={(e) => set(s, { password: e.target.value })} />
            </Field>
          </div>
        </fieldset>
      ))}
      <div>
        <Button variant="primary" type="submit">
          Guardar integrações
        </Button>
      </div>
    </form>
  )
}

/** Accent colour (live preview while editing) and the name used in the greeting. */
function AppearanceForm() {
  const toast = useToast()
  const prefs = useApi(() => api.appearance(), [], 0)
  const [form, setForm] = useState<Appearance | null>(null)
  const [hexText, setHexText] = useState('')
  const saved = useRef<string>(undefined)
  useEffect(() => {
    // Seed once: useApi reloads on window focus, which must not wipe unsaved edits.
    if (prefs.data && !saved.current) {
      setForm(prefs.data)
      setHexText(prefs.data.accent)
      saved.current = prefs.data.accent
    }
  }, [prefs.data])
  // Leaving the page without saving puts the saved accent back (the preview is live).
  useEffect(() => () => void (saved.current && applyAccent(saved.current)), [])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form) return <Skeleton className="h-40" />

  const pick = (hex: string) => {
    const h = hex.toLowerCase()
    setForm({ ...form, accent: h })
    setHexText(h)
    applyAccent(h, false)
  }
  const v = accentVariants(form.accent)
  const [r, g, b] = rgbOf(form.accent)
  const channel = (i: number) => (e: { target: { value: string } }) => {
    const c = rgbOf(form.accent)
    c[i] = Math.max(0, Math.min(255, Number(e.target.value) || 0))
    pick('#' + c.map((x) => x.toString(16).padStart(2, '0')).join(''))
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.saveAppearance(form!)
      saved.current = form!.accent
      applyAccent(form!.accent)
      toast('Aparência guardada', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-5">
      <Field label="O teu nome" hint="Para a saudação no Início">
        <Input value={form.name} maxLength={40} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Ex.: Ana" className="sm:w-72" />
      </Field>
      <fieldset className="grid gap-3">
        <legend className="mb-2 text-[13px] font-medium">Cor de destaque</legend>
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-xs text-muted">
            Escolher
            <input type="color" aria-label="Cor de destaque" value={form.accent} onChange={(e) => pick(e.target.value)} className="h-9 w-14 cursor-pointer rounded-lg border border-line bg-surface-2 p-1" />
          </label>
          <label className="grid gap-1 text-xs text-muted">
            HEX
            <Input
              aria-label="Cor em HEX"
              value={hexText}
              maxLength={7}
              onChange={(e) => {
                setHexText(e.target.value)
                if (isHex(e.target.value)) pick(e.target.value)
              }}
              className="w-28 font-mono"
            />
          </label>
          {(['R', 'G', 'B'] as const).map((c, i) => (
            <label key={c} className="grid gap-1 text-xs text-muted">
              {c}
              <Input aria-label={`Componente ${c}`} type="number" min={0} max={255} value={[r, g, b][i]} onChange={channel(i)} className="w-20" />
            </label>
          ))}
        </div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Cores sugeridas">
          {PRESETS.map((p) => (
            <button
              key={p.hex}
              type="button"
              aria-pressed={form.accent === p.hex}
              onClick={() => pick(p.hex)}
              className={cx('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs', form.accent === p.hex ? 'border-accent text-text' : 'border-line text-muted hover:text-text')}
            >
              <span aria-hidden className="size-3 rounded-full" style={{ background: p.hex }} />
              {p.name}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-line bg-surface-2/50 p-4">
        <OrbMark className="size-10" />
        <span className="inline-flex h-9 items-center rounded-lg bg-accent px-4 text-[13px] font-medium text-accent-ink">Botão principal</span>
        <span className="text-[13px] font-medium text-accent">Texto em destaque</span>
        <p className="w-full text-xs text-muted">
          Contraste do destaque: {contrast(v.dark, '#12100d').toFixed(1)}:1 no tema escuro, {contrast(v.light, '#ffffff').toFixed(1)}:1 no claro (mínimo 4,5:1).
          {(v.dark !== form.accent || v.light !== form.accent) && ' A cor é ajustada automaticamente onde for preciso para continuar legível.'}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" type="submit">
          Guardar aparência
        </Button>
        <Button type="button" onClick={() => pick(DEFAULT_ACCENT)}>
          Repor dourado
        </Button>
      </div>
    </form>
  )
}

function PlannerForm() {
  const toast = useToast()
  const prefs = useApi(() => api.planner(), [], 0)
  const [form, setForm] = useState<PlanPrefs | null>(null)
  useEffect(() => {
    if (prefs.data) setForm({ ...prefs.data, study_start: hhmm(prefs.data.study_start), study_end: hhmm(prefs.data.study_end) })
  }, [prefs.data])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form) return <Skeleton className="h-40" />

  const num = (k: keyof PlanPrefs) => (e: { target: { value: string } }) => setForm({ ...form, [k]: Number(e.target.value) })
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.savePlanner(form!)
      toast('Preferências guardadas. Replaneia para as aplicar.', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Início do dia de estudo">
          <Input type="time" required value={form.study_start} onChange={(e) => setForm({ ...form, study_start: e.target.value })} />
        </Field>
        <Field label="Fim do dia de estudo">
          <Input type="time" required value={form.study_end} onChange={(e) => setForm({ ...form, study_end: e.target.value })} />
        </Field>
        <Field label="Máximo de estudo por dia (min)" hint="Inclui sessões já feitas">
          <Input type="number" min={15} max={720} step={15} value={form.max_daily_minutes} onChange={num('max_daily_minutes')} />
        </Field>
        <Field label="Duração de cada sessão (min)">
          <Input type="number" min={15} max={180} step={5} value={form.block_minutes} onChange={num('block_minutes')} />
        </Field>
        <Field label="Pausa entre sessões (min)">
          <Input type="number" min={0} max={60} step={5} value={form.break_minutes} onChange={num('break_minutes')} />
        </Field>
        <Field label="Máximo por tarefa por dia (min)">
          <Input type="number" min={15} max={480} step={15} value={form.max_task_minutes_per_day} onChange={num('max_task_minutes_per_day')} />
        </Field>
        <Field label="Estimativa por omissão (min)" hint="Para tarefas sem duração">
          <Input type="number" min={15} max={600} step={15} value={form.default_task_minutes} onChange={num('default_task_minutes')} />
        </Field>
        <Field label="Preparação inicial por avaliação (min)" hint="Orçamento de planeamento; não mede a tua preparação">
          <Input type="number" min={15} max={600} step={15} value={form.default_exam_minutes} onChange={num('default_exam_minutes')} />
        </Field>
      </div>
      <div>
        <Button variant="primary" type="submit">
          Guardar preferências
        </Button>
      </div>
    </form>
  )
}

const CATEGORIES: { key: keyof NotifyPrefs; label: string }[] = [
  { key: 'deadlines', label: 'Prazos e avaliações (24 h e 2 h antes)' },
  { key: 'sessions', label: 'Início das sessões de estudo' },
  { key: 'changes', label: 'Novidades nos sites' },
  { key: 'missed', label: 'Estudo que ficou por fazer' },
  { key: 'integrations', label: 'Problemas de sincronização' },
]

function NotificationsForm() {
  const toast = useToast()
  const prefs = useApi(() => api.notifyPrefs(), [], 0)
  const [form, setForm] = useState<NotifyPrefs | null>(null)
  useEffect(() => {
    if (prefs.data)
      setForm({ ...prefs.data, quiet_start: hhmm(prefs.data.quiet_start), quiet_end: hhmm(prefs.data.quiet_end), briefing_at: hhmm(prefs.data.briefing_at) })
  }, [prefs.data])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form) return <Skeleton className="h-40" />
  const time = (k: 'quiet_start' | 'quiet_end' | 'briefing_at') => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.saveNotifyPrefs(form!)
      toast('Notificações guardadas', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-5">
      <label className="flex items-center gap-3 text-[13px]">
        <Checkbox checked={form.enabled} label="Notificações ativas" onChange={(v) => setForm({ ...form, enabled: v })} />
        Notificações no ambiente de trabalho (silenciosas)
      </label>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Silêncio a partir de">
          <Input type="time" required value={form.quiet_start} onChange={time('quiet_start')} />
        </Field>
        <Field label="Silêncio até">
          <Input type="time" required value={form.quiet_end} onChange={time('quiet_end')} />
        </Field>
        <Field label="Resumo do dia a partir de" hint="Aparece quando ligas o PC">
          <Input type="time" required value={form.briefing_at} onChange={time('briefing_at')} />
        </Field>
        <Field label="Avisos secundários por hora" hint="Novidades, estudo por fazer, falhas de sincronização">
          <Input type="number" min={1} max={30} value={form.max_per_hour} onChange={(e) => setForm({ ...form, max_per_hour: Number(e.target.value) })} />
        </Field>
        <Field label="Intervalo mínimo entre eles (min)" hint="Prazos, avaliações e sessões nunca esperam">
          <Input type="number" min={0} max={120} value={form.min_gap_minutes} onChange={(e) => setForm({ ...form, min_gap_minutes: Number(e.target.value) })} />
        </Field>
      </div>
      <fieldset className="grid gap-2.5">
        <legend className="mb-2 text-xs font-medium text-muted">Avisar sobre</legend>
        {CATEGORIES.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-3 text-[13px]">
            <Checkbox checked={Boolean(form[key])} label={label} onChange={(v) => setForm({ ...form, [key]: v })} />
            {label}
          </label>
        ))}
      </fieldset>
      <div>
        <Button variant="primary" type="submit">
          Guardar notificações
        </Button>
      </div>
    </form>
  )
}

function Pending({ title, text, phase }: { title: string; text: string; phase: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div>
        <div className="text-[13px] font-medium">{title}</div>
        <div className="text-xs text-muted">{text}</div>
      </div>
      <Badge>{phase}</Badge>
    </div>
  )
}

export default function Settings() {
  const now = useNow(60_000)
  const navigate = useNavigate()
  const health = useApi(() => api.syncStatus(), [])
  const [theme, setTheme] = useTheme()

  return (
    <div className="grid max-w-4xl gap-6">
      <PageHeader title="Definições" />
      <Card title="Integrações" action={<SyncButton size="sm" />}>
        {health.data ? <SyncHealthList health={health.data} now={now} /> : <Skeleton className="h-16" />}
        <p className="mt-4 text-xs text-faint">
          Sincronização automática todos os dias às 02:00 (Europe/Lisbon). As credenciais ficam só neste PC, na base de dados da PEACE; nunca são mostradas de volta.
        </p>
        <div className="mt-6 border-t border-line pt-5" />
        <IntegrationsForm onSaved={health.reload} />
      </Card>
      <Card title="Plano de estudo">
        <PlannerForm />
      </Card>
      <Card title="Notificações">
        <NotificationsForm />
        <NotificationDiagnostics />
      </Card>
      <Card title="Voz">
        <VoiceForm />
      </Card>
      <Card title="Aparência">
        <AppearanceForm />
        <div className="mt-6 border-t border-line pt-5" />
        <Segmented
          label="Tema"
          value={theme}
          onChange={setTheme}
          options={[
            { value: 'dark', label: 'Escuro' },
            { value: 'light', label: 'Claro' },
          ]}
        />
        <span className="ml-3 inline-flex align-middle text-faint">{theme === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}</span>
      </Card>
      <Card title="Ainda por ligar">
        <div className="divide-y divide-line">
          <Pending title="Obsidian" text="Índice das tuas notas do cofre Peace Vault." phase="Mais tarde" />
        </div>
      </Card>
      <Card title="Sessão">
        <Button
          onClick={async () => {
            await api.logout()
            navigate('/entrar', { replace: true })
          }}
        >
          <LogOut className="size-4" /> Terminar sessão
        </Button>
      </Card>
    </div>
  )
}
