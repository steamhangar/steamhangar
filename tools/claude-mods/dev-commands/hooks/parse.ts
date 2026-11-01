// Pure parsing and formatting for dev-commands: no `$`, easy to test.

/** Pads every column but the last to its widest cell. */
export const table = (rows: readonly (readonly string[])[]): string[] => {
  const widths: number[] = []
  for (const row of rows) row.forEach((cell, i) => { widths[i] = Math.max(widths[i] ?? 0, cell.length) })
  return rows.map(row =>
    row.map((cell, i) => (i === row.length - 1 ? cell : cell.padEnd(widths[i] ?? 0))).join('  ').trimEnd(),
  )
}

export const age = (fromMs: number, nowMs: number): string => {
  const s = Math.max(0, Math.round((nowMs - fromMs) / 1000))
  if (s < 90) return `${s}s`
  const m = Math.round(s / 60)
  if (m < 90) return `${m}m`
  const h = Math.round(m / 60)
  if (h < 36) return `${h}h`
  return `${Math.round(h / 24)}d`
}

// ---------------------------------------------------------------- /ci

export const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
export const BRANCH = /^[A-Za-z0-9_./-]+$/

export type Run = {
  name: string
  branch: string
  status: string
  conclusion: string | null
  sha: string
  createdMs: number
  url: string
}

export const parseRuns = (body: string): Run[] => {
  const data = JSON.parse(body) as { workflow_runs?: unknown }
  if (!Array.isArray(data.workflow_runs)) return []
  return data.workflow_runs.flatMap((raw): Run[] => {
    const r = raw as Record<string, unknown>
    if (typeof r.head_branch !== 'string') return []
    return [{
      name: typeof r.name === 'string' ? r.name : '?',
      branch: r.head_branch,
      status: typeof r.status === 'string' ? r.status : '?',
      conclusion: typeof r.conclusion === 'string' ? r.conclusion : null,
      sha: typeof r.head_sha === 'string' ? r.head_sha.slice(0, 7) : '?',
      createdMs: typeof r.created_at === 'string' ? Date.parse(r.created_at) : 0,
      url: typeof r.html_url === 'string' ? r.html_url : '',
    }]
  })
}

/** The newest run per (branch, workflow), branches ordered by their newest run. */
export const latestPerBranch = (runs: readonly Run[], maxBranches: number): Run[] => {
  const sorted = [...runs].sort((a, b) => b.createdMs - a.createdMs)
  const seen = new Set<string>()
  const branches: string[] = []
  const byBranch = new Map<string, Run[]>()
  for (const run of sorted) {
    const key = `${run.branch}\u0000${run.name}`
    if (seen.has(key)) continue
    seen.add(key)
    if (!byBranch.has(run.branch)) {
      if (branches.length >= maxBranches) continue
      branches.push(run.branch)
      byBranch.set(run.branch, [])
    }
    byBranch.get(run.branch)?.push(run)
  }
  return branches.flatMap(b => byBranch.get(b) ?? [])
}

export const formatRuns = (repo: string, runs: readonly Run[], nowMs: number, note: string): string => {
  if (runs.length === 0) return `CI ${repo}: no workflow runs found.${note}`
  const rows = [
    ['branch', 'workflow', 'result', 'sha', 'age'],
    ...runs.map(r => [
      r.branch,
      r.name,
      r.status === 'completed' ? (r.conclusion ?? 'completed') : r.status,
      r.sha,
      age(r.createdMs, nowMs),
    ]),
  ]
  return [`CI ${repo} (newest run per branch and workflow)${note}`, ...table(rows)].join('\n')
}

// ---------------------------------------------------------------- /antraege

export const DEFAULT_CLOSED = 'umgesetzt,erledigt,abgelehnt,zurueckgezogen,done,closed,rejected,withdrawn'

export type Antrag = { file: string; status: string; isOpen: boolean }

/** The `status:` value of a Markdown file (front matter or a plain line). */
export const statusOf = (text: string): string | undefined => {
  for (const line of text.split('\n').slice(0, 60)) {
    const m = /^status:\s*(.*?)\s*$/.exec(line)
    if (m) return (m[1] ?? '').replace(/^["']|["']$/g, '') || undefined
  }
  return undefined
}

export const closedSet = (option: string): Set<string> =>
  new Set(option.split(',').map(s => s.trim().toLowerCase()).filter(s => s !== ''))

export const classify = (file: string, status: string | undefined, closed: Set<string>): Antrag => {
  const value = status ?? '(no status)'
  return { file, status: value, isOpen: status === undefined || !closed.has(value.toLowerCase()) }
}

export const formatAntraege = (dir: string, items: readonly Antrag[], maxClosed: number): string => {
  if (items.length === 0) return `Antraege in ${dir}: no Markdown files with a status found.`
  const byNameDesc = (a: Antrag, b: Antrag) => (a.file < b.file ? 1 : a.file > b.file ? -1 : 0)
  const open = items.filter(a => a.isOpen).sort(byNameDesc)
  const closed = items.filter(a => !a.isOpen).sort(byNameDesc)
  const shown = closed.slice(0, maxClosed)
  const lines = [`Antraege in ${dir}: ${open.length} open, ${closed.length} closed`]
  lines.push('open:')
  if (open.length === 0) lines.push('  (none)')
  else lines.push(...table(open.map(a => [a.status, a.file])).map(l => `  ${l}`))
  lines.push('closed (newest first):')
  if (shown.length === 0) lines.push('  (none)')
  else lines.push(...table(shown.map(a => [a.status, a.file])).map(l => `  ${l}`))
  if (closed.length > shown.length) lines.push(`  ... ${closed.length - shown.length} more`)
  return lines.join('\n')
}

// ---------------------------------------------------------------- /wp

export type Worktree = {
  path: string
  head: string
  branch: string
  isBare: boolean
  isPrunable: boolean
}

/** Parses `git worktree list --porcelain`. */
export const parseWorktrees = (porcelain: string): Worktree[] => {
  const out: Worktree[] = []
  let cur: Worktree | undefined
  for (const line of porcelain.split('\n')) {
    if (line.startsWith('worktree ')) {
      cur = { path: line.slice('worktree '.length), head: '', branch: '', isBare: false, isPrunable: false }
      out.push(cur)
    } else if (cur === undefined) {
      continue
    } else if (line.startsWith('HEAD ')) {
      cur.head = line.slice(5, 12)
    } else if (line.startsWith('branch ')) {
      cur.branch = line.slice('branch '.length).replace(/^refs\/heads\//, '')
    } else if (line === 'detached') {
      cur.branch = '(detached)'
    } else if (line === 'bare') {
      cur.isBare = true
    } else if (line.startsWith('prunable')) {
      cur.isPrunable = true
    }
  }
  return out
}

/** `git rev-list --left-right --count main...HEAD`: "<behind>\t<ahead>". */
export const parseAheadBehind = (text: string): { ahead: number; behind: number } | undefined => {
  const m = /^\s*(\d+)\s+(\d+)\s*$/.exec(text)
  return m ? { behind: Number(m[1]), ahead: Number(m[2]) } : undefined
}

export const dirtyCount = (porcelain: string): number =>
  porcelain.split('\n').filter(l => l.trim() !== '').length

export type WorktreeState = Worktree & {
  aheadBehind?: { ahead: number; behind: number }
  dirty?: number
}

export const formatWorktrees = (states: readonly WorktreeState[]): string => {
  if (states.length === 0) return 'No worktrees found.'
  const rows = [
    ['branch', 'vs main', 'dirty', 'head', 'path'],
    ...states.map(s => [
      s.branch || '?',
      s.isBare ? 'bare' : s.isPrunable ? 'prunable'
        : s.aheadBehind ? `+${s.aheadBehind.ahead}/-${s.aheadBehind.behind}` : '?',
      s.dirty === undefined ? '?' : s.dirty === 0 ? 'clean' : `${s.dirty} files`,
      s.head || '?',
      s.path,
    ]),
  ]
  return ['Worktrees (vs main: +ahead/-behind)', ...table(rows)].join('\n')
}
