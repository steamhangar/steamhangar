import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const KB = 1024

// Files beneath the plugin by size; `ran` records the calls that reached the tool.
function world(on: On, sizes: Record<string, number>) {
  const ran: string[] = []
  on('fs.stat', (_, e) => {
    // the engine hands relative paths over resolved against the session's
    // directory, so a relative fixture matches as a suffix
    const key = Object.keys(sizes).find(k => e.path === k || (!k.startsWith('/') && e.path.endsWith(`/${k}`)))
    if (key === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: { kind: 'file', size: sizes[key] ?? 0, mtimeMs: 0, isLink: false, realPath: e.path } }
  })
  on('tool.call', (_, e) => {
    ran.push(String(e.tool))
    return { result: {} }
  })
  return ran
}

// $.tool.call answers a plugin's refusal as { deny }
const isDenied = (r: { deny?: string }) => /^read-brake: /.test(r.deny ?? '')

const SIZES = {
  '/repo/docs/PROJECT_PLAN.md': 88 * KB,
  '/big/docs/PROJECT_PLAN.md': 120 * KB,
  '/repo/docs/WORKPACKAGES.md': 90 * KB,
  '/repo/docs/plan-archive/rc21.md': 30 * KB,
  '/repo/web/app.js': 200 * KB,
  '/repo/web/small.js': 10 * KB,
  'docs/WORKPACKAGES.md': 90 * KB,
  'web/app.js': 200 * KB,
  'README.md': 2 * KB,
}

test('Read: whole reads of the braked files are denied with a grep hint', async ($, on) => {
  const ran = world(on, SIZES)
  for (const file_path of ['/repo/docs/WORKPACKAGES.md', '/repo/docs/plan-archive/rc21.md', '/big/docs/PROJECT_PLAN.md', '/repo/web/app.js']) {
    const r = await $.tool.call({ tool: 'Read', file_path })
    expect(isDenied(r)).toBe(true)
    expect(r.deny).toContain(`grep -n '<term>' ${file_path}`)
    expect(r.deny).toContain('offset and limit')
  }
  expect(ran).toEqual([])
})

test('Read: the core plan under its limit, small files and ranged reads pass', async ($, on) => {
  const ran = world(on, SIZES)
  const calls = [
    { file_path: '/repo/docs/PROJECT_PLAN.md' },
    { file_path: '/repo/web/small.js' },
    { file_path: '/repo/docs/WORKPACKAGES.md', offset: 100, limit: 50 },
    { file_path: '/repo/docs/plan-archive/rc21.md', limit: 40 },
    { file_path: '/repo/web/app.js', offset: 1 },
    { file_path: '/repo/missing.md' },
  ]
  for (const c of calls) {
    const r = await $.tool.call({ tool: 'Read', ...c })
    expect(isDenied(r)).toBe(false)
  }
  expect(ran.length).toBe(calls.length)
})

test('Read: the limits come from the options', { options: { planKb: 80, largeKb: 300 } }, async ($, on) => {
  world(on, SIZES)
  expect(isDenied(await $.tool.call({ tool: 'Read', file_path: '/repo/docs/PROJECT_PLAN.md' }))).toBe(true)
  expect(isDenied(await $.tool.call({ tool: 'Read', file_path: '/repo/web/app.js' }))).toBe(false)
})

test('Bash: a plain cat of a braked file is denied, other commands pass', async ($, on) => {
  const ran = world(on, SIZES)
  for (const command of ['cat docs/WORKPACKAGES.md', 'cd x && cat -n "web/app.js"', 'cat README.md; cat docs/WORKPACKAGES.md']) {
    expect(isDenied(await $.tool.call({ tool: 'Bash', command }))).toBe(true)
  }
  for (const command of [
    'cat README.md',
    'cat docs/WORKPACKAGES.md | head -50',
    'grep -n DEV-MODS docs/WORKPACKAGES.md',
    'cat docs/WORKPACKAGES.md > /tmp/copy',
    'sed -n 1,40p web/app.js',
    'cat $(echo docs/WORKPACKAGES.md)',
  ]) {
    expect(isDenied(await $.tool.call({ tool: 'Bash', command }))).toBe(false)
  }
  expect(ran.length).toBe(6)
})
