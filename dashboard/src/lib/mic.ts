/** Browsers only offer the microphone on https or on this computer, hence the check. */
export const micAvailable = () => !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined'

/** Starts recording; the returned function stops it, releases the microphone and gives the recording. */
export async function startRecording(): Promise<() => Promise<Blob>> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const recorder = new MediaRecorder(stream)
  const chunks: Blob[] = []
  recorder.ondataavailable = (e) => chunks.push(e.data)
  recorder.start()
  return () =>
    new Promise((resolve) => {
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        resolve(new Blob(chunks, { type: recorder.mimeType }))
      }
      recorder.stop()
    })
}

const RATE = 16000

/** The speech services take 16-bit PCM WAV, not the compressed audio browsers record; the context resamples to 16 kHz. */
export async function toWav(recording: Blob): Promise<Blob> {
  const audio = await new OfflineAudioContext(1, 1, RATE).decodeAudioData(await recording.arrayBuffer())
  const pcm = audio.getChannelData(0)
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2))
  const text = (at: number, s: string) => [...s].forEach((c, i) => out.setUint8(at + i, c.charCodeAt(0)))
  text(0, 'RIFF')
  out.setUint32(4, 36 + pcm.length * 2, true)
  text(8, 'WAVEfmt ')
  out.setUint32(16, 16, true)
  out.setUint16(20, 1, true) // PCM
  out.setUint16(22, 1, true) // mono
  out.setUint32(24, RATE, true)
  out.setUint32(28, RATE * 2, true)
  out.setUint16(32, 2, true)
  out.setUint16(34, 16, true)
  text(36, 'data')
  out.setUint32(40, pcm.length * 2, true)
  pcm.forEach((v, i) => out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 32767, true))
  return new Blob([out], { type: 'audio/wav' })
}
