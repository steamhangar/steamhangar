import type { EngineInterface, PluginOptions, Register, ToolCallResult } from 'claude-code'
import {
  DEFAULTS,
  asksForSmallRange,
  fileName,
  footer,
  fullOutputFile,
  isLong,
  isTap,
  trimTapText,
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
 * The Bash record with its TAP stdout trimmed (see trimTapText), or
 * undefined to leave the record to the generic trimming.
 *
 * Output above about 30 KB reaches the hook already cut by the harness,
 * which keeps the whole output in its own file (`persistedOutputPath`) and
 * shows the model a 2 KB preview of `stdout`. The TAP summary sits at the
 * end, so the whole text is read back from that file, trimmed, and the
 * record is answered without the persisted fields: the trimmed text is then
 * shown inline, footer included, instead of a preview that cuts it off.
 */
async function trimTapRecord(
  $: EngineInterface,
  configuredDir: string,
  toolUseId: string,
  record: BashRecord,
  settings: TrimSettings,
): Promise<BashRecord | undefined> {
  const persisted = typeof record.persistedOutputPath === 'string' && record.persistedOutputPath !== ''
    ? record.persistedOutputPath
    : undefined
  const stdout = persisted === undefined ? record.stdout : await readText($, persisted)
  if (stdout === undefined) return undefined
  const { stderr } = record
  const full = fullOutputFile(stdout, stderr)
  if (!isLong(full, settings)) return undefined

  const newStdout = trimTapText(stdout, settings)
  if (newStdout === undefined) return undefined
  const newStderr = isLong(stderr, settings) ? trimText(stderr, settings) : undefined

  const line = await keep($, configuredDir, toolUseId, full)
  if (line === undefined) return undefined

  const result: BashRecord = { ...record, stdout: `${newStdout}\n${line}`, stderr: newStderr ?? stderr }
  for (const field of PERSISTED_FIELDS) delete result[field]
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

    if (ran.isError === true) {
      // A failing command (non-zero exit): the model reads the error text.
      const text = typeof ran.text === 'string' ? ran.text
        : typeof ran.result === 'string' ? ran.result : undefined
      if (text === undefined || !isLong(text, settings)) return ran
      // A failing TAP run: the error text holds only the first ~30 KB (no
      // summary, no persisted file to read it from), so it is left as the
      // harness gave it rather than trimmed into a misleading excerpt.
      if (isTap(text)) return ran
      const trimmed = trimText(text, settings)
      if (trimmed === undefined) return ran
      const line = await keep($, configuredDir, toolUseId, text)
      if (line === undefined) return ran
      const shown = `${trimmed}\n${line}`
      return { isError: true, result: shown, text: shown, context: ran.context } satisfies ToolCallResult
    }

    if (!isBashRecord(ran.result)) return ran
    const { stdout, stderr } = ran.result
    if (isTap(stdout)) {
      const tap = await trimTapRecord($, configuredDir, toolUseId, ran.result, settings)
      if (tap !== undefined) return { result: tap, context: ran.context } as ToolCallResult
    }
    const full = fullOutputFile(stdout, stderr)
    if (!isLong(full, settings)) return ran

    const newStdout = isLong(stdout, settings) ? trimText(stdout, settings) : undefined
    const newStderr = isLong(stderr, settings) ? trimText(stderr, settings) : undefined
    if (newStdout === undefined && newStderr === undefined) return ran

    const line = await keep($, configuredDir, toolUseId, full)
    if (line === undefined) return ran

    const result = {
      ...ran.result,
      stdout: `${newStdout ?? stdout}\n${line}`,
      stderr: newStderr ?? stderr,
    }
    return { result, context: ran.context } as ToolCallResult
  }).catch(($, e, next) => next(e))
}
