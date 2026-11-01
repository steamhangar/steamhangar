// token-meter: one JSONL line per model request (main loop and subagents),
// counts only. Never writes prompt, answer or tool content.
//
// Files: <dir>/<session>.jsonl, rolling to <session>.<n>.jsonl before a part
// reaches the part size ($.fs.read refuses files over 4 MiB, and appending is a
// read plus a write). <dir> is the `logDir` option, else $CLAUDE_PLUGIN_DATA,
// else <$CLAUDE_CONFIG_DIR or $HOME/.claude>/plugins/data/token-meter.

import type { EngineInterface, Register, TurnStepInput, TurnStepResult } from 'claude-code'
import {
  type MeterLine,
  type RequestLine,
  parseLines,
  partFile,
  partOf,
  summarize,
} from './summary'

// 3 MiB by default; the `partKb` option lowers it (clamped to 1..3584 KB).
const DEFAULT_PART_KB = 3072
const MAX_PART_KB = 3584

type AgentMeta = { type: string; label: string }

// Module state: rebuilt after a reload (agent names fall back to
// $.agent.list(), the part number to a directory listing).
const agents = new Map<string, AgentMeta>()
let configured = ''
let partBytes = DEFAULT_PART_KB * 1024
let dirCache: string | undefined
const parts = new Map<string, { part: number; size: number }>()
let queue: Promise<void> = Promise.resolve()

async function logDir($: EngineInterface): Promise<string> {
  if (dirCache !== undefined) return dirCache
  if (configured !== '') return (dirCache = configured)
  const data = await $.env.get('CLAUDE_PLUGIN_DATA')
  if (data) return (dirCache = data)
  const config = await $.env.get('CLAUDE_CONFIG_DIR')
  const home = await $.env.get('HOME')
  const base = config || (home ? `${home}/.claude` : '.claude')
  return (dirCache = `${base.replace(/\/+$/, '')}/plugins/data/token-meter`)
}

async function currentPart($: EngineInterface, dir: string, session: string) {
  const known = parts.get(session)
  if (known) return known
  let part = 0
  if (await $.fs.exists(dir)) {
    for (const entry of await $.fs.list(dir)) {
      const n = partOf(entry.name, session)
      if (n !== undefined && n > part) part = n
    }
  }
  const file = partFile(dir, session, part)
  const size = (await $.fs.exists(file)) ? (await $.fs.stat(file)).size : 0
  const state = { part, size }
  parts.set(session, state)
  return state
}

// Appends are serialised: parallel subagents finish steps concurrently,
// and an unserialised read-plus-write would drop lines.
function append($: EngineInterface, line: MeterLine): Promise<void> {
  const run = async () => {
    const dir = await logDir($)
    const text = `${JSON.stringify(line)}\n`
    const state = await currentPart($, dir, line.session)
    if (state.size > 0 && state.size + text.length > partBytes) {
      state.part += 1
      state.size = 0
    }
    const file = partFile(dir, line.session, state.part)
    // read only when the file exists: a failed read must never turn into
    // an overwrite of what is already there
    const before = (await $.fs.exists(file)) ? await $.fs.read(file) : ''
    await $.fs.write(file, before + text)
    state.size = before.length + text.length
  }
  const done = queue.then(run)
  queue = done.catch(() => undefined)
  return done
}

async function agentMeta($: EngineInterface, agentId: string): Promise<AgentMeta | undefined> {
  const known = agents.get(agentId)
  if (known) return known
  const listed = (await $.agent.list().catch(() => [])).find(a => a.id === agentId)
  if (!listed) return undefined
  const meta = { type: listed.type, label: listed.description }
  agents.set(agentId, meta)
  return meta
}

async function recordStep(
  $: EngineInterface,
  e: TurnStepInput,
  r: TurnStepResult,
  durationMs: number,
): Promise<void> {
  const agentId = e.agentId ?? 'main'
  const meta = e.agentId === undefined ? undefined : await agentMeta($, e.agentId)
  const u = r.usage
  const line: RequestLine = {
    kind: 'request',
    ts: new Date(await $.clock.now()).toISOString(),
    session: await $.session.id(),
    agentId,
    ...(meta ? { agentType: meta.type, agentLabel: meta.label } : {}),
    model: u?.model ?? e.model,
    turnId: e.turnId,
    step: e.index,
    input: u?.input_tokens ?? 0,
    output: u?.output_tokens ?? 0,
    cacheRead: u?.cache_read_input_tokens ?? 0,
    cacheWrite: u?.cache_creation_input_tokens ?? 0,
    durationMs: Math.max(0, Math.round(durationMs)),
    stopReason: r.stopReason,
    ...(u ? {} : { noUsage: true as const }),
  }
  await append($, line)
}

export const register: Register = (on, options) => {
  configured = typeof options.logDir === 'string' ? options.logDir.trim() : ''
  dirCache = undefined
  const kb = typeof options.partKb === 'number' ? options.partKb : DEFAULT_PART_KB
  partBytes = Math.min(MAX_PART_KB, Math.max(1, Math.floor(kb))) * 1024

  // One model request: pass the stream through untouched, then record.
  on('turn.step', async function* ($, e, next) {
    const started = await $.clock.now()
    const result = yield* next(e)
    try {
      await recordStep($, e, result, (await $.clock.now()) - started)
    } catch (err) {
      $.ui.log(`token-meter: could not record a request (${String(err)})`)
    }
    return result
  })

  // A subagent start: remember its type and label, and log a spawn line.
  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (r.deny === undefined && r.agentId) {
      agents.set(r.agentId, { type: e.subagentType, label: e.description })
      try {
        await append($, {
          kind: 'spawn',
          ts: new Date(await $.clock.now()).toISOString(),
          session: await $.session.id(),
          agentId: r.agentId,
          agentType: e.subagentType,
          agentLabel: e.description,
          model: r.model,
        })
      } catch (err) {
        $.ui.log(`token-meter: could not record a spawn (${String(err)})`)
      }
    }
    return r
  }).catch(($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const r = await next(e)
    await $.command.register({
      name: 'tokens',
      description: 'Token use of this session per agent (from the token-meter log, no model call)',
      argumentHint: '[session-id]',
      immediate: true,
    })
    return r
  })

  on('command.run', { command: 'tokens' }, async ($, e) => {
    const session = e.args.trim() || (await $.session.id())
    const dir = await logDir($)
    await queue
    const lines: MeterLine[] = []
    if (await $.fs.exists(dir)) {
      const found = (await $.fs.list(dir))
        .map(entry => ({ name: entry.name, part: partOf(entry.name, session) }))
        .filter((x): x is { name: string; part: number } => x.part !== undefined)
        .sort((a, b) => a.part - b.part)
      for (const f of found) lines.push(...parseLines(await $.fs.read(`${dir}/${f.name}`)))
    }
    return { text: summarize(session, lines) }
  })
}
