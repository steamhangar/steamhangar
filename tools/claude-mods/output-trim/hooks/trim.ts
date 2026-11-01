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
 * result would not be shorter than the input.
 */
export const trimText = (text: string, settings: TrimSettings): string | undefined => {
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
      if (!MATCH.test(line)) continue
      matchCount++
      if (matches.length < settings.maxMatches) matches.push(`${i + 1}: ${cap(line)}`)
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
