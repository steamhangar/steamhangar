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

test('a long failing command keeps its error text trimmed', async ($, on) => {
  const long = `Exit code 1\n${numbered(1000, i => (i === 300 ? ' Traceback (most recent call last):' : ''))}`
  const writes: Write[] = []
  on('tool.call', () => ({ isError: true, result: long, text: long }))
  on('fs.write', (_, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  mock.env(on, { CLAUDE_PLUGIN_DATA: '/data/plugin' })

  const ran = await $.tool.call({ tool: 'Bash', command: 'go test ./...' })
  expect(ran.isError).toBe(true)
  expect(ran.text).toContain('Exit code 1')
  expect(ran.text).toContain('301: line 300 Traceback')
  expect(ran.text).toContain('/data/plugin/output-trim/bash-')
  expect(writes[0]?.text).toBe(long)
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
