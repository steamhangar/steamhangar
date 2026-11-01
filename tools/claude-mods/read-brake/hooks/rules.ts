// Pure rules of read-brake: which whole-file reads are braked, and which
// Bash commands are a plain `cat` of files. No `$` here.

export type Limits = { planKb: number; largeKb: number }

export type Verdict = { brake: false } | { brake: true; why: string }

const PLAN = /(^|\/)docs\/PROJECT_PLAN\.md$/
const ALWAYS: readonly RegExp[] = [/(^|\/)docs\/WORKPACKAGES\.md$/, /(^|\/)docs\/plan-archive\/[^/]+$/]

const norm = (p: string): string => p.replace(/\\/g, '/').replace(/^\.\//, '')

/**
 * Whether a whole-file read of `path` (any of its spellings) is braked.
 * `size` is undefined when the file could not be stat'ed: only the name
 * rules apply then.
 */
export const judge = (spellings: readonly string[], size: number | undefined, limits: Limits): Verdict => {
  const names = spellings.map(norm)
  const kb = size === undefined ? undefined : Math.ceil(size / 1024)
  if (names.some(n => ALWAYS.some(r => r.test(n)))) {
    return { brake: true, why: 'is a large planning file that is never read whole' }
  }
  if (names.some(n => PLAN.test(n)) && size !== undefined && size > limits.planKb * 1024) {
    return { brake: true, why: `is ${kb} KB, over the ${limits.planKb} KB plan limit` }
  }
  if (size !== undefined && size > limits.largeKb * 1024) {
    return { brake: true, why: `is ${kb} KB, over the ${limits.largeKb} KB whole-read limit` }
  }
  return { brake: false }
}

/** The deny text the agent reads. */
export const brakeMessage = (path: string, why: string): string =>
  `read-brake: ${path} ${why}. Find the part you need with \`grep -n '<term>' ${path}\`, ` +
  `then Read it with offset and limit (or \`sed -n '<from>,<to>p'\` in Bash).`

const unquote = (t: string): string => t.replace(/^(['"])(.*)\1$/, '$2')

/**
 * The files a Bash command prints whole with `cat`: every pipeline of the
 * command (split on `&&`, `||`, `;` and newlines) that is a single `cat`
 * stage with no redirection. `cat f | head`, `cat f > g` and anything
 * fancier (substitutions, heredocs) are left alone: they do not dump the
 * whole file into the context, or this parser cannot tell.
 */
export const catTargets = (command: string): string[] => {
  if (/[`$<]|<<|\(/.test(command)) return []
  const out: string[] = []
  for (const pipeline of command.split(/&&|\|\||;|\n/)) {
    const p = pipeline.trim()
    if (p === '' || p.includes('|') || p.includes('>')) continue
    const tokens = p.split(/\s+/).map(unquote)
    const head = tokens[0] ?? ''
    if (head !== 'cat' && !head.endsWith('/cat')) continue
    for (const t of tokens.slice(1)) {
      if (t === '' || t.startsWith('-')) continue
      out.push(t)
    }
  }
  return out
}
