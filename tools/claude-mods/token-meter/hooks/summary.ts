// Pure helpers of token-meter: the JSONL line shape, file naming and the
// /tokens summary. No `$` here, so the summary is testable on its own.

/** One model request as token-meter records it: counts only, no content. */
export type RequestLine = {
  kind: 'request'
  ts: string
  session: string
  agentId: string
  agentType?: string
  agentLabel?: string
  model: string
  turnId: string
  step: number
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  durationMs: number
  stopReason: string | null
  noUsage?: true
}

/** One subagent start, so a summary can name agents that made no request yet. */
export type SpawnLine = {
  kind: 'spawn'
  ts: string
  session: string
  agentId: string
  agentType: string
  agentLabel: string
  model: string
}

export type MeterLine = RequestLine | SpawnLine

/** A session id as a file name: anything outside [A-Za-z0-9_-] becomes `_`. */
export const safeName = (session: string): string =>
  session.replace(/[^A-Za-z0-9_-]/g, '_') || 'unknown'

/** Part 0 is `<session>.jsonl`, part n is `<session>.<n>.jsonl`. */
export const partFile = (dir: string, session: string, part: number): string =>
  `${dir.replace(/\/+$/, '')}/${safeName(session)}${part === 0 ? '' : `.${part}`}.jsonl`

/** The part number a directory entry holds for the session, or undefined. */
export const partOf = (name: string, session: string): number | undefined => {
  const base = safeName(session)
  if (name === `${base}.jsonl`) return 0
  const m = /^(.*)\.(\d+)\.jsonl$/.exec(name)
  return m && m[1] === base ? Number(m[2]) : undefined
}

/** Parses JSONL text; lines that do not parse are skipped. */
export const parseLines = (text: string): MeterLine[] => {
  const out: MeterLine[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (line === '') continue
    try {
      const v = JSON.parse(line) as MeterLine
      if (v && (v.kind === 'request' || v.kind === 'spawn')) out.push(v)
    } catch {
      // a torn line (crash mid-write) is skipped, the rest still counts
    }
  }
  return out
}

type Sums = {
  label: string
  requests: number
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
  durationMs: number
}

const empty = (label: string): Sums => ({
  label,
  requests: 0,
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  durationMs: 0,
})

const add = (s: Sums, l: RequestLine): void => {
  s.requests += 1
  s.input += l.input
  s.output += l.output
  s.cacheRead += l.cacheRead
  s.cacheWrite += l.cacheWrite
  s.durationMs += l.durationMs
}

const fmt = (n: number): string => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')

/** The /tokens text: one row per agent (main first), then the total. */
export const summarize = (session: string, lines: readonly MeterLine[]): string => {
  const byAgent = new Map<string, Sums>()
  const labels = new Map<string, string>()
  for (const l of lines) {
    if (l.kind === 'spawn') labels.set(l.agentId, `${l.agentType}: ${l.agentLabel}`)
  }
  const total = empty('total')
  for (const l of lines) {
    if (l.kind !== 'request' || l.session !== session) continue
    const label =
      l.agentId === 'main'
        ? 'main'
        : labels.get(l.agentId) ??
          (l.agentType ? `${l.agentType}${l.agentLabel ? `: ${l.agentLabel}` : ''}` : l.agentId)
    const s = byAgent.get(l.agentId) ?? empty(label)
    byAgent.set(l.agentId, s)
    add(s, l)
    add(total, l)
  }
  if (total.requests === 0) return `token-meter: no requests recorded for session ${session}.`

  const rows = [...byAgent.entries()]
    .sort(([a], [b]) => (a === 'main' ? -1 : b === 'main' ? 1 : 0))
    .map(([, s]) => s)
  const head = ['agent', 'requests', 'input', 'output', 'cache read', 'cache write', 'time s']
  const cells = (s: Sums): string[] => [
    s.label.length > 40 ? `${s.label.slice(0, 39)}~` : s.label,
    fmt(s.requests),
    fmt(s.input),
    fmt(s.output),
    fmt(s.cacheRead),
    fmt(s.cacheWrite),
    (s.durationMs / 1000).toFixed(1),
  ]
  const table = [head, ...rows.map(cells), cells(total)]
  const widths = head.map((_, i) => Math.max(...table.map(r => (r[i] ?? '').length)))
  const render = (r: string[]): string =>
    r.map((c, i) => (i === 0 ? c.padEnd(widths[i] ?? 0) : c.padStart(widths[i] ?? 0))).join('  ')
  return [
    `token-meter: session ${session}`,
    render(head),
    ...rows.map(s => render(cells(s))),
    render(cells(total)),
  ].join('\n')
}
