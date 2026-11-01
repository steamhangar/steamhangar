// read-brake: denies whole-file reads of the big planning docs and of any
// very large file, telling the agent to grep and read a line range instead.
// Applies to the Read tool (no offset and no limit) and to a plain
// `cat <file>` in Bash. A brake that fails lets the read through: it saves
// tokens, it is not a security boundary.

import type { EngineInterface, Register } from 'claude-code'
import { type Limits, brakeMessage, catTargets, judge } from './rules'

let limits: Limits = { planKb: 100, largeKb: 150 }

const num = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : fallback

/** The deny text for a whole read of `path`, or undefined to let it run. */
async function check($: EngineInterface, path: string): Promise<string | undefined> {
  const stat = await $.fs.stat(path, { resolve: true }).catch(() => undefined)
  if (stat && stat.kind !== 'file') return undefined
  const spellings = stat?.realPath ? [path, stat.realPath] : [path]
  const verdict = judge(spellings, stat?.size, limits)
  return verdict.brake ? brakeMessage(path, verdict.why) : undefined
}

export const register: Register = (on, options) => {
  limits = { planKb: num(options.planKb, 100), largeKb: num(options.largeKb, 150) }

  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const isRange = e.offset !== undefined || e.limit !== undefined || e.pages !== undefined
    if (isRange) return next(e)
    const deny = await check($, e.file_path)
    return deny === undefined ? next(e) : { deny }
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    for (const path of catTargets(e.command)) {
      const deny = await check($, path)
      if (deny !== undefined) return { deny }
    }
    return next(e)
  }).catch(($, e, next) => next(e))
}
