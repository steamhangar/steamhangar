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
