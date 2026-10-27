import { KeyRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { Button, Field, Input } from '../components/ui'
import { OrbMark } from '../components/Layout'
import { api } from '../lib/api'

export default function Login() {
  const navigate = useNavigate()
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.login(token.trim())
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
        <Field label="Token de acesso" hint="PEACE_API_TOKEN, em ~/peace-secrets/peace.env no servidor.">
          <Input type="password" autoComplete="current-password" autoFocus required value={token} onChange={(e) => setToken(e.target.value)} />
        </Field>
        {error && (
          <p role="alert" className="text-[13px] text-danger">
            {error}
          </p>
        )}
        <Button variant="primary" type="submit" disabled={busy || !token.trim()}>
          <KeyRound className="size-4" /> {busy ? 'A entrar…' : 'Entrar'}
        </Button>
      </form>
    </main>
  )
}
