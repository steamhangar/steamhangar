import { type Engine, expect, mock, test } from 'claude-code/testing'
import type { On, TurnStepResult } from 'claude-code'

const SESSION = 'sess-1'

// An in-memory file system beneath the plugin: what $.fs reaches in a session.
function fakeFs(on: On, seed: Record<string, string> = {}) {
  const files = new Map<string, string>(Object.entries(seed))
  const dirOf = (p: string) => p.slice(0, p.lastIndexOf('/'))
  on('fs.exists', (_, e) => ({
    value: files.has(e.path) || [...files.keys()].some(k => dirOf(k) === e.path),
  }))
  on('fs.read', (_, e) => {
    const text = files.get(e.path)
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: text }
  })
  on('fs.write', (_, e) => {
    files.set(e.path, e.text)
    return { value: undefined }
  })
  on('fs.stat', (_, e) => ({
    value: { kind: 'file', size: (files.get(e.path) ?? '').length, mtimeMs: 0, isLink: false },
  }))
  on('fs.list', (_, e) => ({
    value: [...files.keys()]
      .filter(k => dirOf(k) === e.path)
      .map(k => ({ name: k.slice(k.lastIndexOf('/') + 1), kind: 'file', size: 0, mtimeMs: 0, isLink: false })),
  }))
  return files
}

function engine(on: On, usage: TurnStepResult['usage']) {
  // a clock that reads T once, then T + 1234 ms: the step took 1.234 s
  let reads = 0
  on('clock.now', () => ({ value: Date.UTC(2026, 10, 1, 12, 0, 0) + (reads++ === 0 ? 0 : 1234) }))
  on('session.id', () => ({ value: SESSION }))
  on('agent.list', () => ({ value: [] }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('agent.spawn', () => ({ model: 'claude-x', agentId: 'agent-7' }))
  on('turn.step', async function* (_, e) {
    yield { kind: 'text', index: 0, text: 'secret answer text' }
    return {
      turnId: e.turnId,
      index: e.index,
      answer: 'secret answer text',
      toolUses: [],
      stopReason: 'end_turn',
      usage,
    }
  })
}

async function step($: Engine, input: Partial<{ agentId: string }>) {
  const stream = $.turn.step({ turnId: 't1', index: 0, model: 'claude-x', messageCount: 3, ...input })
  // read the stream by hand: a for-await drops the generator's return value
  const chunks: unknown[] = []
  let r = await stream.next()
  while (!r.done) {
    chunks.push(r.value)
    r = await stream.next()
  }
  return { chunks, result: r.value }
}

const USAGE = {
  model: 'claude-x',
  input_tokens: 100,
  output_tokens: 20,
  cache_read_input_tokens: 5000,
  cache_creation_input_tokens: 300,
}

test('a request is logged as one counts-only line in the option directory', { options: { logDir: '/logs' } }, async ($, on) => {
  const files = fakeFs(on)
  engine(on, USAGE)

  const { chunks, result } = await step($, {})
  // the stream passes through untouched
  expect(chunks).toEqual([{ kind: 'text', index: 0, text: 'secret answer text' }])
  expect(result.usage).toEqual(USAGE)

  const text = files.get(`/logs/${SESSION}.jsonl`) ?? ''
  const lines = text.trim().split('\n').map(l => JSON.parse(l))
  expect(lines.length).toBe(1)
  expect(lines[0]).toEqual(
    expect.objectContaining({
      kind: 'request',
      session: SESSION,
      agentId: 'main',
      model: 'claude-x',
      input: 100,
      output: 20,
      cacheRead: 5000,
      cacheWrite: 300,
      turnId: 't1',
      step: 0,
      stopReason: 'end_turn',
      ts: '2026-11-01T12:00:01.234Z',
      durationMs: 1234,
    }),
  )
  // never any content
  expect(text.includes('secret')).toBe(false)
})

test('without the option the plugin data directory is used', async ($, on) => {
  const files = fakeFs(on)
  mock.env(on, { CLAUDE_PLUGIN_DATA: '/data/tm' })
  engine(on, USAGE)
  await step($, {})
  expect(files.has(`/data/tm/${SESSION}.jsonl`)).toBe(true)
})

test('without option or CLAUDE_PLUGIN_DATA it falls back under HOME', async ($, on) => {
  const files = fakeFs(on)
  mock.env(on, { HOME: '/home/u' })
  engine(on, USAGE)
  await step($, {})
  expect(files.has(`/home/u/.claude/plugins/data/token-meter/${SESSION}.jsonl`)).toBe(true)
})

test('a subagent request carries the spawned type and label; lines append', { options: { logDir: '/logs' } }, async ($, on) => {
  const files = fakeFs(on, { [`/logs/${SESSION}.jsonl`]: '{"kind":"request","session":"sess-1","agentId":"main","input":1,"output":1,"cacheRead":0,"cacheWrite":0,"durationMs":0}\n' })
  engine(on, USAGE)

  const spawned = await $.agent.spawn({ prompt: 'do the secret thing', description: 'review WP', subagentType: 'reviewer' })
  expect(spawned.agentId).toBe('agent-7')
  await step($, { agentId: 'agent-7' })

  const text = files.get(`/logs/${SESSION}.jsonl`) ?? ''
  const lines = text.trim().split('\n').map(l => JSON.parse(l))
  expect(lines.length).toBe(3)
  expect(lines[1]).toEqual(expect.objectContaining({ kind: 'spawn', agentId: 'agent-7', agentType: 'reviewer', agentLabel: 'review WP' }))
  expect(lines[2]).toEqual(expect.objectContaining({ kind: 'request', agentId: 'agent-7', agentType: 'reviewer', agentLabel: 'review WP' }))
  expect(text.includes('secret')).toBe(false)
})

test('a step without usage is still counted, flagged noUsage', { options: { logDir: '/logs' } }, async ($, on) => {
  const files = fakeFs(on)
  engine(on, null)
  await step($, {})
  const line = JSON.parse((files.get(`/logs/${SESSION}.jsonl`) ?? '').trim())
  expect(line).toEqual(expect.objectContaining({ input: 0, output: 0, noUsage: true }))
})

test('a part at the part size rolls over to the next part file', { options: { logDir: '/logs', partKb: 1 } }, async ($, on) => {
  const full = 'x'.repeat(1000) + '\n'
  const files = fakeFs(on, { [`/logs/${SESSION}.jsonl`]: full })
  engine(on, USAGE)
  await step($, {})
  expect(files.get(`/logs/${SESSION}.jsonl`)).toBe(full)
  expect((files.get(`/logs/${SESSION}.1.jsonl`) ?? '').includes('"kind":"request"')).toBe(true)
  // the next request goes on in the new part, not back to part 0
  await step($, {})
  expect((files.get(`/logs/${SESSION}.1.jsonl`) ?? '').trim().split('\n').length).toBe(2)
})

test('/tokens sums per agent and in total across parts, without a model call', { options: { logDir: '/logs' } }, async ($, on) => {
  const line = (agentId: string, input: number, extra: Record<string, unknown> = {}) =>
    JSON.stringify({ kind: 'request', session: SESSION, agentId, input, output: 10, cacheRead: 1000, cacheWrite: 0, durationMs: 1500, ...extra })
  fakeFs(on, {
    [`/logs/${SESSION}.jsonl`]: [line('main', 100), JSON.stringify({ kind: 'spawn', session: SESSION, agentId: 'a1', agentType: 'coder', agentLabel: 'WP X' })].join('\n') + '\n',
    [`/logs/${SESSION}.1.jsonl`]: [line('a1', 2000), line('main', 50), 'torn{'].join('\n') + '\n',
    [`/logs/other.jsonl`]: line('main', 999999, { session: 'other' }) + '\n',
  })
  engine(on, USAGE)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

  const { text = '' } = await $.command.run({ command: 'tokens', args: '' })
  const rows = text.split('\n')
  expect(rows[0]).toBe(`token-meter: session ${SESSION}`)
  expect(rows[2]).toMatch(/^main\s+2\s+150\s+20\s+2,000\s+0\s+3\.0$/)
  expect(rows[3]).toMatch(/^coder: WP X\s+1\s+2,000\s+10\s+1,000\s+0\s+1\.5$/)
  expect(rows[4]).toMatch(/^total\s+3\s+2,150\s+30\s+3,000\s+0\s+4\.5$/)
  expect(text.includes('999,999')).toBe(false)

  // an explicit session id reads that session's file instead
  const other = await $.command.run({ command: 'tokens', args: 'other' })
  expect(other.text?.split('\n')[2]).toMatch(/^main\s+1\s+999,999\s/)
})

test('/tokens says so when nothing is recorded', { options: { logDir: '/logs' } }, async ($, on) => {
  fakeFs(on)
  engine(on, USAGE)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const { text } = await $.command.run({ command: 'tokens', args: '' })
  expect(text).toBe(`token-meter: no requests recorded for session ${SESSION}.`)
})
