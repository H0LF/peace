import type { OrbState } from './Orb'

const SAYS: Record<Exclude<OrbState, 'idle'>, string> = { listening: 'A ouvir-te…', thinking: 'A pensar…', speaking: 'A responder…' }

/** While you talk to PEACE: what it is doing, with bars that move (the agent reports the state, not the mic's volume). */
export function VoiceCue({ state }: { state: OrbState }) {
  if (state === 'idle') return null
  return (
    <p role="status" data-voice={state} className="voice-cue animate-rise inline-flex items-center gap-2.5 rounded-full border border-accent/50 bg-accent-soft px-4 py-1.5 text-[13px] font-medium text-accent">
      <span aria-hidden className="voice-bars">{[0, 1, 2, 3, 4].map((i) => <i key={i} style={{ animationDelay: `${i * -0.17}s` }} />)}</span>
      {SAYS[state]}
    </p>
  )
}
