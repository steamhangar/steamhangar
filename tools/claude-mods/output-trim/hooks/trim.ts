// Pure trimming logic for output-trim: no `$`, so it is easy to test and read.

export type TrimSettings = {
  maxLines: number
  maxBytes: number
  headLines: number
  tailLines: number
  maxMatches: number
  maxLineChars: number
}

export const DEFAULTS: TrimSettings = {
  maxLines: 200,
  maxBytes: 20480,
  headLines: 40,
  tailLines: 80,
  maxMatches: 30,
  maxLineChars: 400,
}

// Lines worth keeping from the omitted middle (test runners, Go, Python).
export const MATCH = /FAIL|FAILED|ERROR|error:|panic|Traceback|AssertionError/

// Commands that already ask for a small range or a count: never trimmed.
const SMALL_RANGE: readonly RegExp[] = [
  /(^|[|;&(\s])(head|tail)(\s|$)/,
  /(^|[|;&(\s])sed\s+(-[A-Za-z]*\s+)*-n/,
  /(^|[|;&(\s])grep\s+([^|;&]*\s)?(-[A-Za-z]*c[A-Za-z]*|--count)(\s|$)/,
  /(^|[|;&(\s])wc(\s|$)/,
]

export const asksForSmallRange = (command: string): boolean =>
  SMALL_RANGE.some(pattern => pattern.test(command))

const encoder = new TextEncoder()

export const byteLength = (text: string): number => encoder.encode(text).length

export const lineCount = (text: string): number =>
  text === '' ? 0 : text.replace(/\n$/, '').split('\n').length

export const isLong = (text: string, settings: TrimSettings): boolean =>
  lineCount(text) > settings.maxLines || byteLength(text) > settings.maxBytes

const capLine = (line: string, max: number): string =>
  line.length <= max ? line : `${line.slice(0, max)} [... ${line.length - max} more chars]`

/**
 * Head + matching middle lines + tail of `text`. Returns undefined when the
 * result would not be shorter than the input. A matching line is prefixed
 * with its number in the full output: `numbers[i]` for line `i` when given
 * (text that is itself an excerpt), else `i + 1`.
 */
export const trimText = (
  text: string,
  settings: TrimSettings,
  match: RegExp = MATCH,
  numbers?: readonly number[],
): string | undefined => {
  const lines = text.replace(/\n$/, '').split('\n')
  const cap = (line: string) => capLine(line, settings.maxLineChars)
  const out: string[] = []

  if (lines.length <= settings.headLines + settings.tailLines) {
    // Few but very long lines: cap each line.
    out.push(...lines.map(cap))
  } else {
    const middleStart = settings.headLines
    const middleEnd = lines.length - settings.tailLines
    const omitted = middleEnd - middleStart
    const matches: string[] = []
    let matchCount = 0
    for (let i = middleStart; i < middleEnd; i++) {
      const line = lines[i] ?? ''
      if (!match.test(line)) continue
      matchCount++
      if (matches.length < settings.maxMatches) matches.push(`${numbers?.[i] ?? i + 1}: ${cap(line)}`)
    }
    out.push(...lines.slice(0, middleStart).map(cap))
    const shown = matchCount > matches.length
      ? `${matches.length} of ${matchCount} matching lines`
      : `${matchCount} matching lines`
    out.push(`[output-trim: ${omitted} lines omitted; ${shown} below as <line>: <text>]`)
    out.push(...matches)
    if (matches.length > 0) out.push('[output-trim: end of matching lines]')
    out.push(...lines.slice(middleEnd).map(cap))
  }

  const trimmed = out.join('\n')
  return byteLength(trimmed) < byteLength(text) ? trimmed : undefined
}

// TAP (node --test with the tap reporter): what a line is.
const TAP_VERSION = /^TAP version \d+$/
const TEST_POINT = /^( *)(not ok|ok)(?= |$)/
const SUBTEST = /^( *)# Subtest: /
const SUMMARY = /^# (tests|suites|pass|fail|cancelled|skipped|todo|duration_ms)( |$)/
const BAIL_OUT = /^ *Bail out!/
const PLAN = /^ *\d+\.\.\d+( |$)/
const COMMENT = /^ *#/

// Lines worth keeping when even the trimmed TAP output is still long (every
// failure block also holds `error:` lines; one line per failure is enough).
export const TAP_MATCH = /^ *not ok |Bail out!/

/** A `TAP version N` line and at least one test point. */
export const isTap = (text: string): boolean => {
  const lines = text.split('\n').map(line => line.trimEnd())
  return lines.some(line => TAP_VERSION.test(line)) && lines.some(line => TEST_POINT.test(line))
}

/**
 * Index of the last line of the YAML block that follows the test point at
 * `start` (indented two more than the point, from `---` to `...`), or
 * `start` when no complete block follows.
 */
const yamlEnd = (lines: readonly string[], start: number, indent: number): number => {
  const pad = ' '.repeat(indent + 2)
  if ((lines[start + 1] ?? '').trimEnd() !== `${pad}---`) return start
  for (let j = start + 2; j < lines.length; j++) {
    if ((lines[j] ?? '').trimEnd() === `${pad}...`) return j
  }
  return start
}

type Kept = { lines: string[]; numbers: number[] }

/**
 * TAP output reduced to what a reader needs: the `TAP version` line, every
 * `not ok` test point with its whole YAML block (verbatim, after the names
 * of its parent subtests), `Bail out!`, the closing `# tests/# pass/...`
 * summary and every line that is not TAP (noise around the run, stack
 * traces). Dropped: `ok` test points with their YAML blocks, the other
 * subtest headers, plans and comments. One marker line says what went.
 * `numbers` holds each kept line's number in `text` (0 for the marker).
 * Undefined for output that is not TAP or when nothing is dropped.
 */
const keepTap = (text: string): Kept | undefined => {
  if (!isTap(text)) return undefined
  const lines = text.replace(/\n$/, '').split('\n')
  const out: string[] = []
  const numbers: number[] = []
  const push = (from: number, to = from) => {
    for (let k = from; k <= to; k++) {
      out.push(lines[k] ?? '')
      numbers.push(k + 1)
    }
  }
  // Open `# Subtest:` headers, outermost first.
  const open: { indent: number; index: number; shown: boolean }[] = []
  const close = (indent: number) => {
    while (open.length > 0 && (open[open.length - 1]?.indent ?? -1) >= indent) open.pop()
  }
  let passed = 0
  let failed = 0
  let markerAt = -1
  const drop = () => {
    if (markerAt < 0) markerAt = out.length
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    const bare = line.trimEnd()
    const subtest = SUBTEST.exec(bare)
    if (subtest !== null) {
      const indent = subtest[1]?.length ?? 0
      close(indent)
      open.push({ indent, index: i, shown: false })
      drop()
      continue
    }
    const point = TEST_POINT.exec(bare)
    if (point !== null) {
      const indent = point[1]?.length ?? 0
      const end = yamlEnd(lines, i, indent)
      if (point[2] === 'not ok') {
        for (const parent of open) {
          if (parent.indent < indent && !parent.shown) {
            push(parent.index)
            parent.shown = true
          }
        }
        push(i, end)
        failed++
      } else {
        passed++
        drop()
      }
      // A test point ends its own subtest and every deeper one.
      close(indent)
      i = end
      continue
    }
    if (TAP_VERSION.test(bare) || SUMMARY.test(bare) || BAIL_OUT.test(bare)) {
      push(i)
      continue
    }
    if (PLAN.test(bare) || COMMENT.test(bare)) {
      drop()
      continue
    }
    push(i)
  }

  const omitted = lines.length - out.length
  if (omitted <= 0) return undefined
  const marker = `[output-trim: TAP, ${omitted} lines omitted: ${passed} ok test points with their` +
    ` YAML blocks, subtest headers, plans and comments; ${failed} not ok kept with their blocks]`
  const at = markerAt < 0 ? out.length : markerAt
  out.splice(at, 0, marker)
  numbers.splice(at, 0, 0)
  return { lines: out, numbers }
}

/** `keepTap` as text; undefined when not TAP or not shorter. */
export const trimTap = (text: string): string | undefined => {
  const kept = keepTap(text)
  if (kept === undefined) return undefined
  const trimmed = kept.lines.join('\n')
  return byteLength(trimmed) < byteLength(text) ? trimmed : undefined
}

/**
 * TAP output trimmed by `trimTap`; when that is still long, its head, tail
 * and `not ok` lines (numbered as in the full output) as `trimText` cuts
 * any output. Undefined when `text` is not TAP or nothing gets shorter.
 */
export const trimTapText = (text: string, settings: TrimSettings): string | undefined => {
  if (!isTap(text)) return undefined
  const lines = text.replace(/\n$/, '').split('\n')
  const kept = keepTap(text) ?? { lines, numbers: lines.map((_, k) => k + 1) }
  const tap = kept.lines.join('\n')
  const shorter = byteLength(tap) < byteLength(text) ? tap : undefined
  if (!isLong(tap, settings)) return shorter
  return trimText(tap, settings, TAP_MATCH, kept.numbers) ?? shorter
}

/** One file per call, named so a listing sorts by time. */
export const fileName = (nowMs: number, toolUseId: string): string => {
  const stamp = new Date(nowMs).toISOString().replace(/[:.]/g, '-')
  const id = toolUseId.replace(/[^A-Za-z0-9_-]/g, '').slice(-12) || 'call'
  return `bash-${stamp}-${id}.log`
}

export const fullOutputFile = (stdout: string, stderr: string): string =>
  stderr === '' ? stdout : `${stdout}${stdout.endsWith('\n') || stdout === '' ? '' : '\n'}--- stderr ---\n${stderr}`

export const footer = (path: string, text: string): string =>
  `[output-trim: full output (${lineCount(text)} lines, ${byteLength(text)} bytes) in ${path}]`
