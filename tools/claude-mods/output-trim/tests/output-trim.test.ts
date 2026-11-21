import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

type Write = { path: string; text: string }

const numbered = (count: number, mark: (i: number) => string = () => ''): string =>
  Array.from({ length: count }, (_, i) => `line ${i + 1}${mark(i + 1)}`).join('\n') + '\n'

// The engine beneath the plugin: Bash answers `stdout`, fs.write records.
const engine = (on: On, stdout: string, stderr = '') => {
  const writes: Write[] = []
  on('tool.call', () => ({ result: { stdout, stderr, interrupted: false, isImage: false } }))
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { HOME: '/home/dev' })
  return writes
}

const stdoutOf = (ran: { result?: unknown }): string =>
  (ran.result as { stdout: string }).stdout

test('long output becomes head + failures + tail, the full text kept in a file', async ($, on) => {
  const long = numbered(1000, i => (i === 500 ? ' FAILED test_x' : i === 600 ? ' panic: boom' : ''))
  const writes = engine(on, long)

  const ran = await $.tool.call({ tool: 'Bash', command: 'pytest -q' })
  const out = stdoutOf(ran)

  expect(writes.length).toBe(1)
  expect(writes[0]?.text).toBe(long)
  expect(writes[0]?.path).toMatch(/^\/home\/dev\/\.claude\/plugins\/data\/output-trim\/bash-.*\.log$/)
  expect(out).toContain('line 1\n')
  expect(out).toContain('line 40\n')
  expect(out).not.toContain('line 41\n')
  expect(out).toContain('500: line 500 FAILED test_x')
  expect(out).toContain('600: line 600 panic: boom')
  expect(out).toContain('line 921\n')
  expect(out).toContain('line 1000\n')
  expect(out).not.toContain('line 920\n')
  expect(out).toContain('880 lines omitted; 2 matching lines')
  expect(out).toContain(`full output (1000 lines, ${long.length} bytes) in ${writes[0]?.path}`)
})

test('short output is left alone and nothing is written', async ($, on) => {
  const writes = engine(on, numbered(150))
  const ran = await $.tool.call({ tool: 'Bash', command: 'ls -la' })
  expect(stdoutOf(ran)).toBe(numbered(150))
  expect(writes.length).toBe(0)
})

for (const command of [
  'cat big.log | head -n 500',
  'tail -500 big.log',
  "sed -n '1,400p' big.log",
  'grep -c ERROR big.log',
  'git log | wc -l',
]) {
  test(`a command asking for a small range is never trimmed: ${command}`, async ($, on) => {
    const writes = engine(on, numbered(1000))
    const ran = await $.tool.call({ tool: 'Bash', command })
    expect(stdoutOf(ran)).toBe(numbered(1000))
    expect(writes.length).toBe(0)
  })
}

test('options: a lower line threshold and an own directory', {
  options: { maxLines: 50, headLines: 5, tailLines: 5, outputDir: '/srv/trim/' },
}, async ($, on) => {
  const writes = engine(on, numbered(60))
  const ran = await $.tool.call({ tool: 'Bash', command: 'make test' })
  const out = stdoutOf(ran)
  expect(writes[0]?.path).toMatch(/^\/srv\/trim\/bash-.*\.log$/)
  expect(out).toContain('line 5\n')
  expect(out).not.toContain('line 6\n')
  expect(out).toContain('line 56\n')
  expect(out).toContain('50 lines omitted; 0 matching lines')
})

test('reading back the kept file with cat is not trimmed again', {
  options: { outputDir: '/srv/trim' },
}, async ($, on) => {
  const writes = engine(on, numbered(1000))
  const ran = await $.tool.call({ tool: 'Bash', command: 'cat /srv/trim/bash-x.log' })
  expect(stdoutOf(ran)).toBe(numbered(1000))
  expect(writes.length).toBe(0)
})

test('more than 20 KB in few lines is cut per line', async ($, on) => {
  const wide = Array.from({ length: 10 }, (_, i) => `${i}:${'x'.repeat(3000)}`).join('\n')
  const writes = engine(on, wide)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'cat wide.json' }))
  expect(writes.length).toBe(1)
  expect(out).toContain('[... 2602 more chars]')
  expect(out.length).toBeLessThan(6000)
})

// The error path's shape, pinned: a non-zero exit is answered exactly as
// core gave it (no rewritten `{ isError, result: <text> }`, which Claude
// Code 2.1.319 refuses as not matching Bash's output shape).
test('a long failing command is answered as core gave it, nothing written', async ($, on) => {
  const long = `Exit code 1\n${numbered(1000, i => (i === 300 ? ' Traceback (most recent call last):' : ''))}`
  const inner = { isError: true as const, result: long, text: long, context: ['inner note'] }
  const writes: Write[] = []
  on('tool.call', () => inner)
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { CLAUDE_PLUGIN_DATA: '/data/plugin' })

  const ran = await $.tool.call({ tool: 'Bash', command: 'go test ./...' })
  expect(ran).toEqual(inner)
  expect(writes.length).toBe(0)
})

test('when the file cannot be written the output stays whole', async ($, on) => {
  on('tool.call', () => ({ result: { stdout: numbered(1000), stderr: '', interrupted: false } }))
  on('fs.write', () => ({ deny: 'read-only file system' }))
  mock.env(on, { HOME: '/home/dev' })
  const ran = await $.tool.call({ tool: 'Bash', command: 'npm test' })
  expect(stdoutOf(ran)).toBe(numbered(1000))
})

test('context from the inner result survives a trim', async ($, on) => {
  on('tool.call', () => ({
    result: { stdout: numbered(1000), stderr: '', interrupted: false },
    context: ['note from an inner hook'],
  }))
  on('fs.write', () => ({ value: undefined }))
  mock.env(on, { HOME: '/home/dev' })
  const ran = await $.tool.call({ tool: 'Bash', command: 'pytest -q' })
  expect(stdoutOf(ran)).toContain('lines omitted')
  expect(ran.context).toEqual(['note from an inner hook'])
})

// --- TAP (node --test --test-reporter=tap) -------------------------------

const okPoint = (indent: string, n: number, name: string): string[] => [
  `${indent}# Subtest: ${name}`,
  `${indent}ok ${n} - ${name}`,
  `${indent}  ---`,
  `${indent}  duration_ms: 0.5${n}`,
  `${indent}  type: 'test'`,
  `${indent}  ...`,
]

// A failing point's YAML block, as node writes it (blank lines inside keep
// their indentation).
const notOkBlock = (indent: string, n: number, name: string): string[] => [
  `${indent}not ok ${n} - ${name}`,
  `${indent}  ---`,
  `${indent}  duration_ms: 0.86`,
  `${indent}  type: 'test'`,
  `${indent}  location: '/repo/web/tests/x.test.js:${n}:7'`,
  `${indent}  failureType: 'testCodeFailure'`,
  `${indent}  error: |-`,
  `${indent}    Expected values to be strictly equal:`,
  `${indent}    `,
  `${indent}    1 !== ${n}`,
  `${indent}    `,
  `${indent}  code: 'ERR_ASSERTION'`,
  `${indent}  stack: |-`,
  `${indent}    TestContext.<anonymous> (/repo/web/tests/x.test.js:${n}:9)`,
  `${indent}  ...`,
]

const summary = (tests: number, fail: number): string[] => [
  `# tests ${tests}`,
  '# suites 0',
  `# pass ${tests - fail}`,
  `# fail ${fail}`,
  '# cancelled 0',
  '# skipped 0',
  '# todo 0',
  '# duration_ms 812.5',
]

const passingTap = (count: number): string => [
  'TAP version 13',
  ...Array.from({ length: count }, (_, i) => okPoint('', i + 1, `case ${i + 1}`)).flat(),
  `1..${count}`,
  ...summary(count, 0),
].join('\n') + '\n'

// 150 passing top-level tests, then a suite with a passing and a failing
// subtest (the failing one nested two deep), a top-level failure, noise
// that is not TAP, and the summary.
const failingTap = (): { text: string; inner: string[]; outer: string[]; top: string[] } => {
  const inner = notOkBlock('        ', 1, 'deep fails')
  const outer = [
    'not ok 151 - suite',
    '  ---',
    "  failureType: 'subtestsFailed'",
    "  error: '1 subtest failed'",
    '  ...',
  ]
  const top = notOkBlock('', 152, 'top fails')
  const lines = [
    '== dev.sh test-web',
    'TAP version 13',
    '# console noise from a passing test',
    ...Array.from({ length: 150 }, (_, i) => okPoint('', i + 1, `case ${i + 1}`)).flat(),
    '# Subtest: suite',
    ...okPoint('    ', 1, 'sibling passes'),
    '    # Subtest: group',
    ...okPoint('        ', 2, 'deep passes'),
    '        # Subtest: deep fails',
    ...inner,
    '        1..2',
    '    not ok 2 - group',
    '      ---',
    "      failureType: 'subtestsFailed'",
    '      ...',
    '    1..2',
    ...outer,
    'Error: stray stack trace printed outside TAP',
    '    at Object.<anonymous> (/repo/web/tests/y.test.js:3:9)',
    '# Subtest: top fails',
    ...top,
    '1..152',
    ...summary(155, 4),
  ]
  return { text: lines.join('\n') + '\n', inner, outer, top }
}

test('TAP: a passing run shrinks to the summary plus the file hint', async ($, on) => {
  const tap = passingTap(400)
  const writes = engine(on, tap)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web' }))

  expect(writes.length).toBe(1)
  expect(writes[0]?.text).toBe(tap)
  expect(out).toBe([
    'TAP version 13',
    `[output-trim: TAP, ${400 * 6 + 1} lines omitted: 400 ok test points with their YAML blocks,` +
      ' subtest headers, plans and comments; 0 not ok kept with their blocks]',
    ...summary(400, 0),
    `[output-trim: full output (${400 * 6 + 10} lines, ${tap.length} bytes) in ${writes[0]?.path}]`,
  ].join('\n'))
})

test('TAP: a failing run keeps every not ok block verbatim, with its parent subtests', async ($, on) => {
  const { text, inner, outer, top } = failingTap()
  const writes = engine(on, text)
  // `|| true`: the run failed, the command did not (the non-zero exit path
  // is covered below).
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web || true' }))

  expect(writes[0]?.text).toBe(text)
  // Parents named once, outermost first, right before the deepest failure.
  expect(out).toContain(['# Subtest: suite', '    # Subtest: group', ...inner].join('\n'))
  expect(out).toContain(['    not ok 2 - group', '      ---', "      failureType: 'subtestsFailed'", '      ...'].join('\n'))
  expect(out).toContain(outer.join('\n'))
  expect(out).toContain(top.join('\n'))
  // Not TAP: kept where it stood.
  expect(out).toContain('== dev.sh test-web\nTAP version 13\n')
  expect(out).toContain('Error: stray stack trace printed outside TAP\n    at Object.<anonymous>')
  expect(out).toContain(summary(155, 4).join('\n'))
  // Passing points, their blocks, their headers, plans and comments are gone.
  expect(out).not.toMatch(/^ *ok \d+ - /m)
  expect(out).not.toContain('sibling passes')
  expect(out).not.toContain('deep passes')
  expect(out).not.toContain("type: 'test'\n  ...\n# Subtest: case")
  expect(out).not.toContain('console noise')
  expect(out).not.toMatch(/^ *\d+\.\.\d+$/m)
  expect(out).not.toContain('# Subtest: deep fails')
  expect(out).toContain('152 ok test points with their YAML blocks')
  expect(out).toContain('4 not ok kept with their blocks]')
  expect(out.split('\n').length).toBeLessThan(80)
})

test('TAP: output the harness cut is read back whole from its persisted file', async ($, on) => {
  const { text } = failingTap()
  const cut = text.slice(0, 3000)
  const writes: Write[] = []
  const reads: string[] = []
  on('tool.call', () => ({
    result: {
      stdout: cut,
      stderr: '',
      interrupted: false,
      persistedOutputPath: '/home/dev/.claude/projects/p/s/tool-results/abc.txt',
      persistedOutputSize: text.length,
    },
  }))
  on('fs.read', (_, e) => {
    reads.push(e.path)
    return { value: text }
  })
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { HOME: '/home/dev' })

  const ran = await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web; echo done' })
  const result = ran.result as Record<string, unknown>
  expect(reads).toEqual(['/home/dev/.claude/projects/p/s/tool-results/abc.txt'])
  expect(writes[0]?.text).toBe(text)
  expect(result.stdout).toContain(summary(155, 4).join('\n'))
  expect(result.stdout).toContain('not ok 152 - top fails')
  expect(result.stdout).toContain(`full output (${text.split('\n').length - 1} lines`)
  // Without these the trimmed text is shown inline, not as a 2 KB preview.
  expect('persistedOutputPath' in result).toBe(false)
  expect('persistedOutputSize' in result).toBe(false)
  expect(result.interrupted).toBe(false)
})

test('TAP: when the persisted file cannot be read, the generic trimming applies', async ($, on) => {
  const tap = passingTap(400)
  const cut = tap.slice(0, 30000)
  on('tool.call', () => ({
    result: { stdout: cut, stderr: '', interrupted: false, persistedOutputPath: '/gone/abc.txt' },
  }))
  on('fs.read', () => ({ deny: 'no such file' }))
  on('fs.write', () => ({ value: undefined }))
  mock.env(on, { HOME: '/home/dev' })
  const ran = await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web' })
  const out = stdoutOf(ran)
  expect(out).toContain('lines omitted; ')
  expect(out).not.toContain('[output-trim: TAP')
  expect((ran.result as Record<string, unknown>).persistedOutputPath).toBe('/gone/abc.txt')
})

test('TAP: still long after the TAP cut, head, tail and not ok lines are kept', {
  options: { maxLines: 40, headLines: 5, tailLines: 10 },
}, async ($, on) => {
  const points = Array.from({ length: 20 }, (_, i) => [
    ...okPoint('', 2 * i + 1, `fine ${i + 1}`),
    ...notOkBlock('', 2 * i + 2, `broken ${i + 1}`),
  ]).flat()
  const tap = ['TAP version 13', ...points, '1..40', ...summary(40, 20)].join('\n') + '\n'
  const writes = engine(on, tap)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web || true' }))
  expect(writes[0]?.text).toBe(tap)
  // Head of the TAP cut: the version line, the marker, the first failure.
  expect(out).toContain('TAP version 13\n[output-trim: TAP, 121 lines omitted: 20 ok test points')
  expect(out).toContain('\nnot ok 2 - broken 1\n')
  // The middle: one numbered line per failure, not its `error:` lines.
  // Numbered as in the full output (the kept file): after the version line,
  // nine pairs of an ok block (6 lines) and a not ok block (15), then the
  // tenth ok block.
  expect(out).toContain(`${1 + 9 * (6 + 15) + 6 + 1}: not ok 20 - broken 10`)
  expect(writes[0]?.text.split('\n')[196]).toBe('not ok 20 - broken 10')
  expect(out).not.toMatch(/^\d+: +error:/m)
  // Tail: the end of the last failure's block and the summary.
  expect(out).toContain(`${notOkBlock('', 40, 'broken 20').slice(-2).join('\n')}\n${summary(40, 20).join('\n')}`)
  expect(out).not.toContain('fine ')
})

test('TAP: a failing TAP run (non-zero exit) is left as the harness gave it', async ($, on) => {
  const { text } = failingTap()
  const shown = `Exit code 1\n${text}`
  const writes: Write[] = []
  on('tool.call', () => ({ isError: true, result: shown, text: shown }))
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { HOME: '/home/dev' })
  const ran = await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web' })
  expect(ran.isError).toBe(true)
  expect(ran.text).toBe(shown)
  expect(writes.length).toBe(0)
})

test('not TAP: test-point-like lines without a TAP version line get the generic trim', async ($, on) => {
  const long = Array.from({ length: 1000 }, (_, i) => `ok ${i + 1} - step`).join('\n') + '\n'
  const writes = engine(on, long)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'make check' }))
  expect(writes[0]?.text).toBe(long)
  expect(out).toContain('ok 40 - step\n[output-trim: 880 lines omitted; 0 matching lines')
  expect(out).not.toContain('[output-trim: TAP')
})

// --- read-back of output the harness cut (any format) --------------------

const persistedEngine = (on: On, cut: string, whole: string | undefined) => {
  const writes: Write[] = []
  const reads: string[] = []
  on('tool.call', () => ({
    result: {
      stdout: cut,
      stderr: '',
      interrupted: false,
      persistedOutputPath: '/home/dev/.claude/projects/p/s/tool-results/xyz.txt',
      persistedOutputSize: (whole ?? cut).length,
      stdoutHoldsOutputEnd: false,
    },
  }))
  on('fs.read', (_, e) => {
    reads.push(e.path)
    return whole === undefined ? { deny: 'no such file' } : { value: whole }
  })
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { HOME: '/home/dev' })
  return { writes, reads }
}

test('cut generic output is read back whole: right tail, line count and file line, shown inline', async ($, on) => {
  const whole = numbered(40000, i => (i === 20000 ? ' FAILED deep in the middle' : ''))
  const cut = whole.slice(0, 30000)
  const { writes, reads } = persistedEngine(on, cut, whole)
  const ran = await $.tool.call({ tool: 'Bash', command: 'seq-like run' })
  const result = ran.result as Record<string, unknown>
  const out = result.stdout as string

  expect(reads).toEqual(['/home/dev/.claude/projects/p/s/tool-results/xyz.txt'])
  expect(writes[0]?.text).toBe(whole)
  expect(out).toContain('line 40\n[output-trim: 39880 lines omitted; 1 matching lines')
  expect(out).toContain('20000: line 20000 FAILED deep in the middle')
  expect(out).toContain('line 39921\n')
  expect(out).toContain('line 40000\n')
  expect(out).toContain(`full output (40000 lines, ${whole.length} bytes) in ${writes[0]?.path}]`)
  for (const field of ['persistedOutputPath', 'persistedOutputSize', 'stdoutHoldsOutputEnd']) {
    expect(field in result).toBe(false)
  }
})

test('cut generic output whose file cannot be read: the cut is trimmed, persisted fields stay', async ($, on) => {
  const cut = numbered(40000).slice(0, 30000)
  const { writes } = persistedEngine(on, cut, undefined)
  const ran = await $.tool.call({ tool: 'Bash', command: 'seq-like run' })
  const result = ran.result as Record<string, unknown>
  expect(writes[0]?.text).toBe(cut)
  expect(result.stdout).toContain('lines omitted; 0 matching lines')
  expect(result.persistedOutputPath).toBe('/home/dev/.claude/projects/p/s/tool-results/xyz.txt')
})

// --- node --test spec reporter (node 24's default) -----------------------

const passLine = (indent: string, name: string) => `${indent}✔ ${name} (0.51ms)`
const specSummary = (tests: number, fail: number): string[] => [
  `ℹ tests ${tests}`,
  'ℹ suites 1',
  `ℹ pass ${tests - fail}`,
  `ℹ fail ${fail}`,
  'ℹ cancelled 0',
  'ℹ skipped 1',
  'ℹ todo 0',
  'ℹ duration_ms 812.5',
]

const passingSpec = (count: number): string => [
  ...Array.from({ length: count }, (_, i) => passLine('', `case ${i + 1}`)),
  ...specSummary(count, 0),
].join('\n') + '\n'

// As node 24 prints it: a suite with a nested failing test, a skipped one,
// console output, the summary, then the failing tests section.
const failingSpec = (): { text: string; section: string[] } => {
  const section = [
    '✖ failing tests:',
    '',
    'test at web/tests/x.test.js:6:5',
    '✖ deep fails (2.0ms)',
    '  AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:',
    '  ',
    '  1 !== 2',
    '  ',
    '      at TestContext.<anonymous> (/repo/web/tests/x.test.js:6:37) {',
    "    code: 'ERR_ASSERTION'",
    '  }',
    '',
    'test at web/tests/x.test.js:11:1',
    '✖ top fails (0.12ms)',
    '  Error: boom top',
    '      at TestContext.<anonymous> (/repo/web/tests/x.test.js:11:33)',
  ]
  const lines = [
    'log line from a test',
    ...Array.from({ length: 300 }, (_, i) => passLine('', `case ${i + 1}`)),
    '▶ outer',
    passLine('  ', 'sibling passes'),
    '  ▶ inner',
    passLine('    ', 'deep passes'),
    '    ✖ deep fails (2.0ms)',
    '  ✖ inner (2.4ms)',
    '  ﹣ skipped one (0.09ms) # SKIP',
    '✖ outer (4.3ms)',
    '▶ other suite',
    passLine('  ', 'all fine here'),
    passLine('', 'other suite'),
    '✖ top fails (0.12ms)',
    '  Error: printed under the line',
    '      at x (/repo/web/tests/x.test.js:11:9)',
    ...specSummary(305, 4),
    '',
    ...section,
  ]
  return { text: lines.join('\n') + '\n', section }
}

test('spec: a passing run shrinks to the summary plus the file hint', async ($, on) => {
  const spec = passingSpec(400)
  const writes = engine(on, spec)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web' }))
  expect(writes[0]?.text).toBe(spec)
  expect(out).toBe([
    '[output-trim: spec, 400 lines omitted: 400 passed or skipped tests and suites, suite headers;' +
      ' 0 failing tests and suites kept with their parent suites]',
    ...specSummary(400, 0),
    `[output-trim: full output (408 lines, ${new TextEncoder().encode(spec).length} bytes) in ${writes[0]?.path}]`,
  ].join('\n'))
})

test('spec: a failing run keeps every failure with its parents, the summary and the failing section', async ($, on) => {
  const { text, section } = failingSpec()
  const writes = engine(on, text)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web || true' }))

  expect(writes.length).toBe(1)
  expect(writes[0]?.text === text).toBe(true)
  expect(out).toContain('log line from a test\n[output-trim: spec, ')
  expect(out).toContain(['▶ outer', '  ▶ inner', '    ✖ deep fails (2.0ms)', '  ✖ inner (2.4ms)',
    '✖ outer (4.3ms)'].join('\n'))
  // A failure's indented lines go with it.
  expect(out).toContain('✖ top fails (0.12ms)\n  Error: printed under the line\n      at x (')
  expect(out).toContain(`${specSummary(305, 4).join('\n')}\n\n${section.join('\n')}\n[output-trim: full output`)
  expect(out).not.toContain('✔')
  expect(out).not.toContain('skipped one')
  expect(out).not.toContain('other suite')
  expect(out).toContain('306 lines omitted: 305 passed or skipped tests and suites')
  expect(out).toContain('4 failing tests and suites kept with their parent suites]')
})

test('spec: output the harness cut is read back and trimmed by its structure', async ($, on) => {
  const { text, section } = failingSpec()
  const { writes } = persistedEngine(on, text.slice(0, 2000), text)
  const ran = await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web; true' })
  const result = ran.result as Record<string, unknown>
  expect(writes.length).toBe(1)
  expect(writes[0]?.text === text).toBe(true)
  expect(result.stdout).toContain(section.join('\n'))
  expect(result.stdout).toContain('[output-trim: spec, ')
  expect('persistedOutputPath' in result).toBe(false)
})

test('spec: still long after the spec cut, the failing lines are kept numbered as in the file', {
  options: { maxLines: 40, headLines: 5, tailLines: 10 },
}, async ($, on) => {
  const lines = Array.from({ length: 30 }, (_, i) => [
    passLine('', `fine ${i + 1}`),
    `✖ broken ${i + 1} (1ms)`,
    `  Error: number ${i + 1}`,
  ]).flat()
  const spec = [...lines, ...specSummary(60, 30)].join('\n') + '\n'
  const writes = engine(on, spec)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'dev.sh test-web || true' }))
  expect(out).toContain('[output-trim: spec, 30 lines omitted')
  // `broken 15` is line 3 * 14 + 2 of the full output.
  expect(out).toContain(`${3 * 14 + 2}: ✖ broken 15 (1ms)`)
  expect(writes[0]?.text.split('\n')[3 * 14 + 1]).toBe('✖ broken 15 (1ms)')
  expect(out).not.toMatch(/^\d+: +Error:/m)
  expect(out).toContain(specSummary(60, 30).join('\n'))
})

test('not spec: check marks without an ℹ summary get the generic trim', async ($, on) => {
  const long = Array.from({ length: 1000 }, (_, i) => `✔ step ${i + 1} (1ms)`).join('\n') + '\n'
  engine(on, long)
  const out = stdoutOf(await $.tool.call({ tool: 'Bash', command: 'make check' }))
  expect(out).toContain('880 lines omitted; 0 matching lines')
  expect(out).not.toContain('[output-trim: spec')
})
