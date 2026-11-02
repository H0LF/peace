import { KeyRound } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { Button, Field, Input } from '../components/ui'
import { OrbMark } from '../components/Layout'
import { api } from '../lib/api'

/** Why Google sign-in came back here (/entrar?erro=…). */
const GOOGLE_ERRORS: Record<string, string> = {
  conta: 'Esta PEACE pertence a outra conta Google. Entra com a conta que usaste da primeira vez.',
  cancelado: 'Entrada com Google cancelada.',
  ligacao: 'Não foi possível falar com a Google. Verifica a ligação à internet ou usa o token de acesso.',
  endereco: 'A entrada com Google só funciona neste PC (endereço 127.0.0.1 ou localhost).',
  indisponivel: 'Esta instalação ainda não tem a entrada com Google. Usa o token de acesso.',
  pedido: 'A entrada com Google não foi confirmada. Tenta outra vez.',
  primeiro: 'Na primeira vez, entra com o link que a instalação abriu (ou com o token) e liga a tua conta Google em Definições › Sessão.',
  google: 'A Google recusou o pedido. Tenta outra vez.',
}

export default function Login() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [token, setToken] = useState('')
  const [error, setError] = useState(GOOGLE_ERRORS[params.get('erro') ?? ''] ?? '')
  const [busy, setBusy] = useState(false)
  const [google, setGoogle] = useState<boolean | null>(null)
  const [useToken, setUseToken] = useState(false)

  useEffect(() => {
    api.google().then((g) => setGoogle(g.available), () => setGoogle(false))
  }, [])

  // The installer opens /entrar#token=…: the fragment never reaches the server or its logs, and is wiped at once.
  useEffect(() => {
    const given = new URLSearchParams(window.location.hash.slice(1)).get('token')
    if (!given) return
    history.replaceState(null, '', window.location.pathname)
    void enter(given)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: FormEvent) {
    e.preventDefault()
    await enter(token)
  }
  async function enter(value: string) {
    setBusy(true)
    setError('')
    try {
      await api.login(value.trim())
      navigate('/', { replace: true })
    } catch {
      setError('Token inválido.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <form onSubmit={submit} className="animate-rise grid w-full max-w-sm gap-5 rounded-2xl border border-line bg-surface p-7 shadow-2xl">
        <div className="grid justify-items-center gap-3 text-center">
          <OrbMark className="size-20" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Entrar no PEACE</h1>
            <p className="mt-1 text-[13px] text-muted">Só é preciso uma vez neste navegador.</p>
          </div>
        </div>
        {google && (
          <a
            href="/auth/google/start"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-accent-ink hover:brightness-110"
          >
            <GoogleMark /> Entrar com Google
          </a>
        )}
        {error && (
          <p role="alert" className="text-[13px] text-danger">
            {error}
          </p>
        )}
        {google === false || useToken ? (
          <>
            <Field label="Token de acesso" hint="Mostrado no fim da instalação (PEACE_API_TOKEN no ficheiro peace.env da PEACE).">
              <Input type="password" autoComplete="current-password" autoFocus required value={token} onChange={(e) => setToken(e.target.value)} />
            </Field>
            <Button variant={google ? 'secondary' : 'primary'} type="submit" disabled={busy || !token.trim()}>
              <KeyRound className="size-4" /> {busy ? 'A entrar…' : 'Entrar com token'}
            </Button>
          </>
        ) : (
          google && (
            <button type="button" onClick={() => setUseToken(true)} className="justify-self-center text-xs text-muted hover:text-accent hover:underline">
              Sem internet? Usar token de acesso
            </button>
          )
        )}
      </form>
    </main>
  )
}

/** Google's "G" in its brand colours, as Google's sign-in branding asks. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="size-4 rounded-full bg-white p-0.5">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.2C12.5 13.6 17.8 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z" />
      <path fill="#FBBC05" d="M10.6 28.5c-.5-1.4-.8-2.9-.8-4.5s.3-3.1.8-4.5l-7.9-6.2C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.2z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.8-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.4 2.3-6.2 0-11.5-4.1-13.4-9.8l-7.9 6.2C6.6 42.6 14.6 48 24 48z" />
    </svg>
  )
}
