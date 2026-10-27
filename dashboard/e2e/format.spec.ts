import { expect, test } from '@playwright/test'
import { countdown, dayHeading, duration, urgency } from '../src/lib/format'

// Pure formatting checks (no browser needed).
const now = new Date('2026-10-26T14:00:00')
const at = (min: number) => new Date(now.getTime() + min * 60_000).toISOString()

test('countdown rounds before splitting hours and minutes', () => {
  expect(countdown(at(119.6), now)).toBe('em 2 h')
  expect(countdown(at(95), now)).toBe('em 1 h 35 min')
  expect(countdown(at(45), now)).toBe('em 45 min')
  expect(countdown(at(-120), now)).toBe('há 2 h')
  expect(countdown(at(60 * 50), now)).toBe('em 2 d 2 h')
  expect(countdown(at(60 * 24 * 14), now)).toBe('em 14 dias')
})

test('urgency bands', () => {
  expect([at(-1), at(60), at(60 * 48), at(60 * 24 * 5), at(60 * 24 * 9)].map((d) => urgency(d, now))).toEqual([
    'overdue',
    'critical',
    'soon',
    'week',
    'later',
  ])
})

test('durations and day headings', () => {
  expect([duration(50), duration(60), duration(90)]).toEqual(['50 min', '1 h', '1 h 30'])
  expect(dayHeading(new Date('2026-10-27T10:00:00'), now)).toEqual(['Amanhã', '27/10'])
  expect(dayHeading(new Date('2026-10-28T10:00:00'), now)[1]).toBeNull()
})
