import type { EngineInterface, PluginOptions, Register, ToolCallResult } from 'claude-code'
import {
  DEFAULTS,
  asksForSmallRange,
  fileName,
  footer,
  fullOutputFile,
  isLong,
  trimAny,
  trimText,
  type TrimSettings,
} from './trim.ts'

const positive = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback

const settingsFrom = (options: PluginOptions): TrimSettings => ({
  ...DEFAULTS,
  maxLines: positive(options.maxLines, DEFAULTS.maxLines),
  maxBytes: positive(options.maxBytes, DEFAULTS.maxBytes),
  headLines: positive(options.headLines, DEFAULTS.headLines),
  tailLines: positive(options.tailLines, DEFAULTS.tailLines),
  maxMatches: positive(options.maxMatches, DEFAULTS.maxMatches),
})

/** The option, else the plugin data dir Claude Code sets, else its usual place. */
async function outputDir($: EngineInterface, configured: string): Promise<string | undefined> {
  if (configured !== '') return configured.replace(/\/+$/, '')
  const data = await $.env.get('CLAUDE_PLUGIN_DATA')
  if (data !== undefined && data !== '') return `${data.replace(/\/+$/, '')}/output-trim`
  const home = await $.env.get('HOME')
  if (home !== undefined && home !== '') return `${home}/.claude/plugins/data/output-trim`
  return undefined
}

type BashRecord = { stdout: string; stderr: string } & Record<string, unknown>

const isBashRecord = (value: unknown): value is BashRecord =>
  typeof value === 'object' && value !== null &&
  typeof (value as BashRecord).stdout === 'string' &&
  typeof (value as BashRecord).stderr === 'string'

/** Writes the full text and answers the footer line, or undefined on failure. */
async function keep(
  $: EngineInterface,
  configuredDir: string,
  toolUseId: string,
  full: string,
): Promise<string | undefined> {
  const dir = await outputDir($, configuredDir)
  if (dir === undefined) return undefined
  const path = `${dir}/${fileName(Date.now(), toolUseId)}`
  try {
    await $.fs.write(path, full)
  } catch {
    return undefined
  }
  return footer(path, full)
}

/** The text of a file, or undefined when it cannot be read (missing, over 4 MiB). */
async function readText($: EngineInterface, path: string): Promise<string | undefined> {
  try {
    const text = await $.fs.read(path)
    return typeof text === 'string' ? text : undefined
  } catch {
    return undefined
  }
}

// Fields with which the Bash record says that the harness kept the output in
// its own file and that `stdout` is only the start of it.
const PERSISTED_FIELDS = ['persistedOutputPath', 'persistedOutputSize', 'stdoutHoldsOutputEnd'] as const

/**
 * The Bash record with long output trimmed, or undefined to leave it as it
 * came.
 *
 * Output above about 30 KB reaches the hook already cut by the harness,
 * which keeps the whole output in its own file (`persistedOutputPath`) and
 * shows the model a 2 KB preview of `stdout`. The whole text is then read
 * back from that file and trimmed (`trimAny`: TAP and spec test output by
 * their structure), and the record is answered without the persisted
 * fields, so the trimmed text and the file line are shown inline. When the
 * file cannot be read, the cut `stdout` is trimmed as before this read-back
 * existed (head, failure lines, tail) and the persisted fields stay.
 */
async function trimRecord(
  $: EngineInterface,
  configuredDir: string,
  toolUseId: string,
  record: BashRecord,
  settings: TrimSettings,
): Promise<BashRecord | undefined> {
  const persisted = typeof record.persistedOutputPath === 'string' && record.persistedOutputPath !== ''
    ? record.persistedOutputPath
    : undefined
  const whole = persisted === undefined ? record.stdout : await readText($, persisted)
  const stdout = whole ?? record.stdout
  const { stderr } = record
  const full = fullOutputFile(stdout, stderr)
  if (!isLong(full, settings)) return undefined

  const newStdout = whole === undefined
    ? (isLong(stdout, settings) ? trimText(stdout, settings) : undefined)
    : trimAny(stdout, settings)
  const newStderr = isLong(stderr, settings) ? trimText(stderr, settings) : undefined
  if (newStdout === undefined && newStderr === undefined) return undefined
  // Never put a whole read-back output inline untrimmed.
  if (persisted !== undefined && newStdout === undefined) return undefined

  const line = await keep($, configuredDir, toolUseId, full)
  if (line === undefined) return undefined

  const result: BashRecord = { ...record, stdout: `${newStdout ?? stdout}\n${line}`, stderr: newStderr ?? stderr }
  if (persisted !== undefined && whole !== undefined) {
    for (const field of PERSISTED_FIELDS) delete result[field]
  }
  return result
}

export const register: Register = (on, options) => {
  const settings = settingsFrom(options)
  const configuredDir = typeof options.outputDir === 'string' ? options.outputDir.trim() : ''

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny !== undefined) return ran

    const command = typeof e.command === 'string' ? e.command : ''
    if (asksForSmallRange(command)) return ran
    // Reading back a file this mod wrote is never trimmed again.
    if (command.includes('bash-') && command.includes('output-trim')) return ran
    if (configuredDir !== '' && command.includes(configuredDir)) return ran

    const toolUseId = typeof e.tool_use_id === 'string' ? e.tool_use_id : 'call'

    // A failing command (non-zero exit) is answered as core gave it. Its
    // error text holds only the first ~30 KB (no persisted file to read the
    // rest from), and Claude Code 2.1.319 refuses a hook's rewritten errored
    // result (`{ isError, result: <text> }` fails the Bash output shape and
    // the model got no output at all); no documented shape carries
    // replacement text for an errored call.
    if (ran.isError === true) return ran

    if (!isBashRecord(ran.result)) return ran
    const result = await trimRecord($, configuredDir, toolUseId, ran.result, settings)
    if (result === undefined) return ran
    return { result, context: ran.context } as ToolCallResult
  }).catch(($, e, next) => next(e))
}
