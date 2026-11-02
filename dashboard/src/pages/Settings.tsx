import { ExternalLink, LogOut, Moon, Sun } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { SOURCE_NAME, SyncButton, SyncHealthList } from '../components/sync'
import Diagnostics from '../components/Diagnostics'
import NotificationDiagnostics from '../components/NotificationDiagnostics'
import { Button, Card, Checkbox, ErrorNote, Field, Input, PageHeader, Segmented, Select, Skeleton, cx, useToast } from '../components/ui'
import { DEFAULT_ACCENT, accentVariants, applyAccent, contrast, isHex, useTheme } from '../lib/theme'
import { OrbMark } from '../components/Layout'
import { setVaultName } from '../components/materials'
import { useHolo } from '../holo/HoloHands'
import { PINCH_IN, PINCH_OUT } from '../holo/gestures'
import { api, type Appearance, type IntegrationsForm as IntegrationsValues, type NotifyPrefs, type PhonePrefs, type PlanPrefs, type ReviewRules, type VoicePrefs } from '../lib/api'
import { useApi, useNow } from '../lib/hooks'
import { ago } from '../lib/format'

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

const EXAMPLE_URL = {
  moodle: 'O Moodle da tua universidade, ex.: https://moodle.universidade.pt',
  inforestudante: 'O portal Nónio (inforestudante), ex.: https://inforestudante.universidade.pt/nonio',
  email: 'O webmail da universidade, ex.: https://webmail.universidade.pt/owa',
}
const SOURCE_LABEL: Record<string, string> = { ...SOURCE_NAME, email: 'Email da universidade' }

/** Which sources PEACE syncs, their addresses and logins. Passwords are write-only: empty keeps the saved one. */
function IntegrationsForm({ onSaved }: { onSaved: () => void }) {
  const toast = useToast()
  const prefs = useApi(() => api.integrations(), [], 0)
  const [form, setForm] = useState<IntegrationsValues | null>(null)
  useEffect(() => {
    if (prefs.data && !form) {
      const row = (s: 'inforestudante' | 'moodle' | 'email') => ({ enabled: prefs.data![s].enabled, url: prefs.data![s].url, username: prefs.data![s].username, password: '' })
      const { clean_junk, quota_mb } = prefs.data.email
      setForm({ inforestudante: row('inforestudante'), moodle: row('moodle'), email: { ...row('email'), clean_junk, quota_mb } })
    }
  }, [prefs.data, form])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form || !prefs.data) return <Skeleton className="h-32" />

  const set = (s: keyof IntegrationsValues, patch: Partial<IntegrationsValues['email']>) => setForm({ ...form, [s]: { ...form[s], ...patch } })
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      prefs.setData(await api.saveIntegrations(form!))
      setForm({ inforestudante: { ...form!.inforestudante, password: '' }, moodle: { ...form!.moodle, password: '' }, email: { ...form!.email, password: '' } })
      toast('Integrações guardadas', 'ok')
      onSaved()
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-5">
      {(['inforestudante', 'moodle', 'email'] as const).map((s) => (
        <fieldset key={s} className="grid gap-3">
          <label className="flex items-center gap-2 text-[13px] font-medium">
            <Checkbox checked={form[s].enabled} onChange={(enabled) => set(s, { enabled })} label={`Ligar ${SOURCE_LABEL[s]}`} />
            {SOURCE_LABEL[s]}
          </label>
          <Field label="Endereço" hint={EXAMPLE_URL[s]} className="sm:max-w-md">
            <Input type="url" value={form[s].url} maxLength={300} placeholder="https://" onChange={(e) => set(s, { url: e.target.value.trim() })} />
          </Field>
          <div className="flex flex-wrap gap-3">
            <Field label="Utilizador" className="sm:w-60">
              <Input value={form[s].username} maxLength={200} autoComplete="off" disabled={!form[s].enabled} onChange={(e) => set(s, { username: e.target.value })} />
            </Field>
            <Field label="Palavra-passe" hint={prefs.data![s].has_password ? 'Guardada. Deixa vazio para manter.' : 'Ainda não definida.'} className="sm:w-60">
              <Input type="password" value={form[s].password} maxLength={200} autoComplete="new-password" disabled={!form[s].enabled} onChange={(e) => set(s, { password: e.target.value })} />
            </Field>
          </div>
          {s === 'email' && (
            <div className="grid gap-3">
              <p className="text-xs text-muted">Só leitura, exceto a limpeza abaixo. A PEACE nunca envia emails nem marca nada como lido.</p>
              <label className="flex items-center gap-2 text-[13px]">
                <Checkbox checked={form.email.clean_junk} onChange={(clean_junk) => set('email', { clean_junk })} label="Limpar o lixo eletrónico" />
                Mover para Itens Eliminados o Lixo Eletrónico com mais de 14 dias (podes repor durante 30 dias)
              </label>
              <Field label="Limite da caixa (MB)" hint="Opcional. Avisa-te aos 85 %." className="sm:w-60">
                <Input type="number" min={1} step={1} value={form.email.quota_mb ?? ''} disabled={!form.email.enabled}
                  onChange={(e) => set('email', { quota_mb: e.target.value ? Number(e.target.value) : null })} />
              </Field>
            </div>
          )}
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

/** Reminders on the phone through ntfy: a secret topic, the app subscribes to it. */
function PhoneForm() {
  const toast = useToast()
  const prefs = useApi(() => api.phone(), [], 0)
  const [form, setForm] = useState<PhonePrefs | null>(null)
  useEffect(() => {
    if (prefs.data) setForm((f) => f ?? prefs.data!)
  }, [prefs.data])
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!form) return <Skeleton className="h-32" />
  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      setForm(await api.savePhone(form!))
      toast('Telemóvel guardado', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  async function test() {
    try {
      await api.testPhone()
      toast('Mensagem de teste enviada: vê o telemóvel', 'ok')
    } catch (err) {
      toast(`O teste falhou: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-4">
      <ol className="grid list-decimal gap-1 pl-5 text-[13px] text-muted">
        <li>Instala a app <span className="text-text">ntfy</span> no telemóvel (Android ou iPhone).</li>
        <li>
          Na app, carrega em «+» e subscreve o tópico <code className="select-all rounded bg-surface-2 px-1.5 py-0.5 text-text">{form.topic}</code>
          {form.server !== 'https://ntfy.sh' && <> no servidor <code className="rounded bg-surface-2 px-1.5 py-0.5">{form.server}</code></>}.
        </li>
        <li>Liga aqui, guarda e carrega em «Enviar teste».</li>
      </ol>
      <label className="flex items-center gap-2 text-[13px]">
        <Checkbox checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} label="Enviar lembretes para o telemóvel" />
        Enviar lembretes para o telemóvel
      </label>
      <Field label="Quando">
        <Select aria-label="Quando enviar para o telemóvel" value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value as PhonePrefs['mode'] })} className="sm:w-80">
          <option value="away">Só quando o PC está desligado</option>
          <option value="always">Sempre (também no PC)</option>
        </Select>
      </Field>
      <Field label="Servidor ntfy" hint="As mensagens passam por este servidor. O nome do tópico é o único segredo: não o partilhes.">
        <Input type="url" value={form.server} onChange={(e) => setForm({ ...form, server: e.target.value.trim() })} className="sm:max-w-md" />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" type="submit">Guardar telemóvel</Button>
        <Button type="button" onClick={() => void test()}>Enviar teste</Button>
      </div>
    </form>
  )
}

/** The local model behind the assistant: automatic (the best installed) or one the student picks. */
function ModelForm() {
  const toast = useToast()
  const models = useApi(() => api.models(), [], 30_000)
  if (models.error) return <ErrorNote error={models.error} onRetry={models.reload} />
  if (!models.data) return <Skeleton className="h-16" />
  const { installed, using, chosen, preferred } = models.data
  const best = preferred.find((m) => installed.includes(m))
  async function choose(model: string) {
    try {
      await api.chooseModel(model)
      models.reload()
      toast('Modelo guardado', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <div className="grid gap-3">
      <p className="text-[13px] text-muted">
        {using
          ? `Em uso: ${using}.`
          : installed.length
            ? 'O modelo escolhido não está instalado no Ollama. Escolhe outro ou «Automático».'
            : 'Sem modelo: o Ollama não está a correr ou não tem modelos. O assistente responde sem modelo (frases fixas).'}
      </p>
      <Field label="Modelo" hint="Automático usa o melhor modelo instalado que sabe escolher comandos">
        <Select aria-label="Modelo do assistente" value={chosen} onChange={(e) => void choose(e.target.value)} className="sm:w-72">
          <option value="">Automático{best ? ` (${best})` : ''}</option>
          {installed.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </Select>
      </Field>
    </div>
  )
}

const GOOGLE_RESULT: Record<string, [string, 'ok' | 'error']> = {
  ligado: ['Google Calendar ligado: a primeira sincronização começa dentro de momentos.', 'ok'],
  permissao: ['Para sincronizar, a Google tem de autorizar o acesso ao calendário. Tenta outra vez e aceita as permissões.', 'error'],
  erro: ['Não foi possível ligar o Google Calendar. Verifica a internet e tenta outra vez.', 'error'],
}

/** Two-way sync with a "PEACE" calendar in the user's Google account; their other calendars come in read-only. */
function GoogleCalendarForm() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const cal = useApi(() => api.googleCalendar(), [], 30_000)
  const [leaving, setLeaving] = useState(false)
  const [deleteCalendar, setDeleteCalendar] = useState(false)
  useEffect(() => {
    const result = GOOGLE_RESULT[params.get('google') ?? '']
    if (!result) return
    toast(...result)
    setParams({}, { replace: true })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  if (cal.error) return <ErrorNote error={cal.error} onRetry={cal.reload} />
  if (!cal.data) return <Skeleton className="h-16" />
  const c = cal.data
  if (!c.available) return <p className="text-[13px] text-muted">Esta instalação ainda não tem a ligação à Google.</p>
  if (!c.connected)
    return (
      <div className="grid gap-3">
        <p className="text-[13px] text-muted">
          A PEACE cria o calendário «PEACE» na tua conta Google com as aulas, avaliações, prazos e compromissos. O que mudares lá ou aqui
          muda nos dois lados. Os teus outros calendários aparecem aqui só para leitura, para o plano de estudo não os pisar.
        </p>
        <a href="/auth/google/start?calendar=1" className="inline-flex h-9 w-fit items-center rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-ink hover:brightness-110">
          Ligar Google Calendar
        </a>
      </div>
    )
  async function toggleSessions(sessions: boolean) {
    try {
      await api.saveGoogleCalendar(sessions)
      cal.reload()
      toast('Preferência guardada', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  async function disconnect() {
    try {
      await api.disconnectGoogleCalendar(deleteCalendar)
      setLeaving(false)
      cal.reload()
      toast('Google Calendar desligado. Os teus dados na PEACE ficaram.', 'ok')
    } catch (err) {
      toast(`Não foi possível desligar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <div className="grid gap-4">
      <p className="text-[13px] text-muted">
        Ligado a {c.email}. {c.last_sync ? `Última sincronização ${ago(c.last_sync)}.` : 'A primeira sincronização está a decorrer.'}
        {c.readonly_calendars > 0 && ` ${c.readonly_calendars} outro(s) calendário(s) só para leitura.`}
      </p>
      {c.error === 'reconnect' && (
        <p role="alert" className="text-[13px] text-danger">
          A Google deixou de aceitar a ligação.{' '}
          <a href="/auth/google/start?calendar=1" className="font-medium underline">Ligar outra vez</a>
        </p>
      )}
      {c.error === 'google' && <p role="alert" className="text-[13px] text-warn">A última sincronização falhou; a PEACE tenta outra vez sozinha.</p>}
      <label className="flex items-center gap-2 text-[13px]">
        <Checkbox label="Mostrar também as sessões de estudo no Google Calendar" checked={c.sessions} onChange={(v) => void toggleSessions(v)} />
        Mostrar também as sessões de estudo no Google Calendar
      </label>
      {leaving ? (
        <div className="grid gap-3 rounded-lg border border-line p-3">
          <label className="flex items-center gap-2 text-[13px]">
            <Checkbox label="Apagar também o calendário «PEACE» da minha conta Google" checked={deleteCalendar} onChange={setDeleteCalendar} />
            Apagar também o calendário «PEACE» da minha conta Google
          </label>
          <div className="flex gap-2">
            <Button variant="danger" onClick={() => void disconnect()}>Desligar</Button>
            <Button variant="ghost" onClick={() => setLeaving(false)}>Cancelar</Button>
          </div>
        </div>
      ) : (
        <Button variant="ghost" className="w-fit" onClick={() => setLeaving(true)}>Desligar o Google Calendar</Button>
      )}
    </div>
  )
}

const POSE_NAME: Record<string, string> = {
  pinch: 'beliscar', point: 'apontar', two: 'dois dedos', vee: '✌', three: 'polegar, indicador e médio', palm: 'mão aberta', fist: 'punho', other: '—',
}
const GESTURE_NAME: Record<string, string> = {
  select: 'escolher', open: 'abrir (duplo)', dragStart: 'arrastar', dragMove: 'arrastar', dragEnd: 'largar', scroll: 'deslizar',
  zoom: 'zoom', escape: 'fechar', back: 'voltar', reset: 'posição inicial', lost: 'mão fora de vista',
}

/** Live view of what Holo Hands measures, and a 10 s landmark recording to tune it to the student's hand. */
function HoloTuning() {
  const toast = useToast()
  const { on, reading, record } = useHolo()
  const [now, setNow] = useState(reading.current)
  const [left, setLeft] = useState(0)
  useEffect(() => {
    if (!on) return
    const id = setInterval(() => setNow({ ...reading.current }), 100)
    return () => clearInterval(id)
  }, [on, reading])
  if (!on) return <p className="text-[13px] text-muted">Liga o Holo Hands (botão ✋ na barra lateral ou a tecla H) para ver o que a câmara mede.</p>
  // Closeness of thumb and index: 0 = far apart, 1 = touching. The lines mark where a pinch starts and ends.
  const close = (r: number | null) => (r == null ? 0 : Math.max(0, Math.min(1, 1 - r / 0.9)))
  async function go() {
    setLeft(10)
    const id = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    try {
      const frames = await record(10)
      toast(`Gravação guardada (${frames} imagens da mão). Diz ao Claude para afinar o Holo Hands.`, 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    } finally {
      clearInterval(id)
      setLeft(0)
    }
  }
  return (
    <div className="grid gap-4">
      <div>
        <div className="mb-1 flex justify-between text-xs text-muted">
          <span>Polegar e indicador: {now.ratio == null ? 'sem mão' : now.pinched ? 'a beliscar' : 'abertos'}</span>
          <span>Pose: {POSE_NAME[now.pose] ?? now.pose} · Último gesto: {now.gesture ? GESTURE_NAME[now.gesture] ?? now.gesture : '—'}</span>
        </div>
        <div className="relative h-3 overflow-hidden rounded-full bg-surface-2" role="meter" aria-label="Beliscar" aria-valuemin={0} aria-valuemax={1} aria-valuenow={close(now.ratio)}>
          <div className={cx('h-full rounded-full transition-[width] duration-100', now.pinched ? 'bg-accent' : 'bg-muted')} style={{ width: `${close(now.ratio) * 100}%` }} />
          <span aria-hidden title="Começa a beliscar" className="absolute inset-y-0 w-0.5 bg-ok" style={{ left: `${close(PINCH_IN) * 100}%` }} />
          <span aria-hidden title="Larga" className="absolute inset-y-0 w-0.5 bg-warn" style={{ left: `${close(PINCH_OUT) * 100}%` }} />
        </div>
        <p className="mt-1 text-[11px] text-faint">A linha verde é onde o beliscar começa; a laranja, onde larga.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={() => void go()} disabled={left > 0}>
          {left > 0 ? `A gravar… ${left}s` : 'Gravar 10 s para afinar'}
        </Button>
        <span className="text-xs text-faint">Durante a gravação: 3 beliscões lentos, 3 duplos, e mexe a mão devagar. Só se guardam os pontos da mão, nunca imagem.</span>
      </div>
    </div>
  )
}

/** The Google account that signs in to this PEACE; linking one needs this already signed-in browser. */
function GoogleAccount() {
  const account = useApi(() => api.googleAccount(), [], 0)
  if (!account.data?.available) return null
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
      <p className="text-[13px] text-muted">
        {account.data.email ? `Entras com a conta Google ${account.data.email}.` : 'Liga a tua conta Google para entrares sem token.'}
      </p>
      {!account.data.email && (
        <a href="/auth/google/start" className="inline-flex h-9 items-center rounded-lg bg-accent px-3 text-[13px] font-medium text-accent-ink hover:brightness-110">
          Ligar conta Google
        </a>
      )}
    </div>
  )
}

/** The vault PEACE copies notes, materials and the knowledge graph into; Obsidian Sync takes them to other devices. */
function ObsidianForm() {
  const toast = useToast()
  const prefs = useApi(() => api.obsidian(), [], 30_000)
  if (prefs.error) return <ErrorNote error={prefs.error} onRetry={prefs.reload} />
  if (!prefs.data) return <Skeleton className="h-16" />
  const { chosen, vaults, vault, pinned } = prefs.data
  async function choose(path: string) {
    try {
      const saved = await api.chooseVault(path)
      if (saved.vault) setVaultName(saved.vault.name)
      prefs.reload()
      toast('Cofre guardado', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  if (!vaults.length)
    return (
      <p className="text-[13px] text-muted">
        Não encontrei o Obsidian neste PC. Instala-o em{' '}
        <a href="https://obsidian.md" target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">obsidian.md</a>{' '}
        e cria ou abre um cofre: aparece aqui sozinho.
      </p>
    )
  return (
    <div className="grid gap-3">
      <p className="text-[13px] text-muted">
        {vault
          ? `A PEACE guarda no cofre «${vault.name}» os materiais das cadeiras, o diário e o grafo de conhecimento (pasta 07 - Generated).`
          : 'Escolhe o cofre onde a PEACE guarda os materiais, o diário e o grafo de conhecimento.'}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Cofre">
          <Select aria-label="Cofre do Obsidian" value={chosen} disabled={pinned} onChange={(e) => void choose(e.target.value)} className="sm:w-72">
            <option value="">Automático{!chosen && vault ? ` (${vault.name})` : ''}</option>
            {vaults.map((v) => (
              <option key={v.path} value={v.path}>{v.name}</option>
            ))}
          </Select>
        </Field>
        {vault && (
          <a
            href={`obsidian://open?vault=${encodeURIComponent(vault.name)}`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-[13px] font-medium hover:border-accent/50 hover:text-accent"
          >
            <ExternalLink className="size-4" /> Abrir no Obsidian
          </a>
        )}
      </div>
      <p className="text-xs text-faint">
        {pinned && 'Cofre fixado por PEACE_VAULT no ficheiro desktop.env. '}
        Para veres tudo no telemóvel ou noutro PC, liga o Obsidian Sync a este cofre na app do Obsidian. Ao mudar de cofre, a PEACE copia tudo
        para o novo e não apaga nada do anterior.
      </p>
    </div>
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
    if (prefs.data)
      setForm({ ...prefs.data, study_start: hhmm(prefs.data.study_start), study_end: hhmm(prefs.data.study_end), recovery_study_end: hhmm(prefs.data.recovery_study_end) })
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
        <Field label="Para recuperar, estudar no máximo até" hint="Só se escolheres essa opção em Rever">
          <Input type="time" required value={form.recovery_study_end} onChange={(e) => setForm({ ...form, recovery_study_end: e.target.value })} />
        </Field>
        <Field label="Para recuperar, máximo por dia (min)" hint="Só se escolheres essa opção em Rever">
          <Input type="number" min={15} max={720} step={15} value={form.recovery_max_daily_minutes} onChange={num('recovery_max_daily_minutes')} />
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

const REVIEW_RULES: { key: keyof ReviewRules; label: string }[] = [
  { key: 'deadline_earlier', label: 'Um prazo é antecipado' },
  { key: 'deadline_later', label: 'Um prazo é adiado' },
  { key: 'assessment_new', label: 'Aparece uma avaliação nova (até 30 dias)' },
  { key: 'assessment_moved', label: 'Uma avaliação muda de data' },
  { key: 'assignment_new', label: 'Aparece um trabalho novo (até 30 dias)' },
]

function ReviewRulesForm() {
  const toast = useToast()
  const rules = useApi(() => api.reviewRules(), [], 0)
  const [form, setForm] = useState<ReviewRules | null>(null)
  useEffect(() => {
    if (rules.data) setForm(rules.data)
  }, [rules.data])
  if (rules.error) return <ErrorNote error={rules.error} onRetry={rules.reload} />
  if (!form) return <Skeleton className="h-32" />

  async function submit(e: FormEvent) {
    e.preventDefault()
    try {
      await api.saveReviewRules(form!)
      toast('Regras guardadas', 'ok')
    } catch (err) {
      toast(`Não foi possível guardar: ${(err as Error).message}`, 'error')
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-5">
      <fieldset className="grid gap-2.5">
        <legend className="mb-2 text-xs font-medium text-muted">Sugerir um novo plano (em Rever) quando</legend>
        {REVIEW_RULES.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-3 text-[13px]">
            <Checkbox checked={form[key]} label={label} onChange={(v) => setForm({ ...form, [key]: v })} />
            {label}
          </label>
        ))}
      </fieldset>
      <p className="text-xs text-faint">Só aparece uma sugestão quando o plano mudaria mesmo. Os prazos da universidade nunca são alterados.</p>
      <div>
        <Button variant="primary" type="submit">
          Guardar regras
        </Button>
      </div>
    </form>
  )
}

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
      <Card title="Rever">
        <ReviewRulesForm />
      </Card>
      <Card title="Telemóvel">
        <PhoneForm />
      </Card>
      <Card title="Google Calendar">
        <GoogleCalendarForm />
      </Card>
      <Card title="Holo Hands">
        <HoloTuning />
      </Card>
      <Card title="Obsidian">
        <ObsidianForm />
      </Card>
      <Card title="Assistente">
        <ModelForm />
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
      <Card title="Diagnóstico">
        <Diagnostics />
      </Card>
      <Card title="Sessão">
        <GoogleAccount />
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
