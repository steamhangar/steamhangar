import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const NOW = Date.parse('2026-11-01T12:00:00Z')

// The engine beneath the plugin: registrations succeed, the session starts.
const start = async ($: Parameters<Parameters<typeof test>[1]>[0], on: On) => {
  const registered: string[] = []
  on('command.register', (_, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  mock.clock(on, { now: NOW })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  return registered
}

const run = (
  workflow: string, branch: string, created: string, status: string, conclusion: string | null, sha: string,
) => ({
  name: workflow, head_branch: branch, created_at: created, status, conclusion,
  head_sha: sha, html_url: `https://github.example/run/${sha}`,
})

// ------------------------------------------------------------------ /ci

test('/ci lists the newest run per branch and workflow', {
  options: { githubRepo: 'octo/demo' },
}, async ($, on) => {
  const urls: string[] = []
  on('http.fetch', (_, e) => {
    urls.push(e.url)
    return {
      value: {
        status: 200, ok: true, headers: { 'x-ratelimit-remaining': '57' },
        text: JSON.stringify({
          workflow_runs: [
            run('ci', 'main', '2026-11-01T11:00:00Z', 'completed', 'success', 'aaaaaaa1111'),
            run('ci', 'main', '2026-11-01T09:00:00Z', 'completed', 'failure', 'bbbbbbb2222'),
            run('android', 'main', '2026-11-01T10:00:00Z', 'completed', 'failure', 'ccccccc3333'),
            run('ci', 'wp/x', '2026-11-01T11:58:00Z', 'in_progress', null, 'ddddddd4444'),
          ],
        }),
      },
    }
  })

  const registered = await start($, on)
  const out = await $.command.run({ command: 'ci', args: '' })

  expect(registered).toEqual(['ci', 'antraege', 'wp'])
  expect(urls).toEqual(['https://api.github.com/repos/octo/demo/actions/runs?per_page=100'])
  const text = out.text ?? ''
  expect(text).toContain('57 API requests left')
  const lines = text.split('\n')
  expect(lines[2]).toMatch(/^wp\/x\s+ci\s+in_progress\s+ddddddd\s+2m$/)
  expect(lines[3]).toMatch(/^main\s+ci\s+success\s+aaaaaaa\s+60m$/)
  expect(lines[4]).toMatch(/^main\s+android\s+failure\s+ccccccc\s+2h$/)
  expect(text).not.toContain('bbbbbbb')
})

test('/ci <branch> filters by branch; a bad name is refused', {
  options: { githubRepo: 'octo/demo' },
}, async ($, on) => {
  const urls: string[] = []
  on('http.fetch', (_, e) => {
    urls.push(e.url)
    return { value: { status: 200, ok: true, headers: {}, text: '{"workflow_runs":[]}' } }
  })
  await start($, on)
  expect((await $.command.run({ command: 'ci', args: 'wp/rc1' })).text).toContain('no workflow runs')
  expect(urls[0]).toContain('&branch=wp%2Frc1')
  expect((await $.command.run({ command: 'ci', args: 'x; rm -rf /' })).text).toContain('is not a branch name')
  expect(urls.length).toBe(1)
})

test('/ci without a repo option says what to set and fetches nothing', async ($, on) => {
  let fetched = 0
  on('http.fetch', () => {
    fetched++
    return { value: { status: 500, ok: false, headers: {}, text: '' } }
  })
  await start($, on)
  expect((await $.command.run({ command: 'ci', args: '' })).text).toContain('githubRepo')
  expect(fetched).toBe(0)
})

test('/ci names the rate limit on HTTP 403', { options: { githubRepo: 'octo/demo' } }, async ($, on) => {
  on('http.fetch', () => ({ value: { status: 403, ok: false, headers: {}, text: '{}' } }))
  await start($, on)
  expect((await $.command.run({ command: 'ci', args: '' })).text).toContain('HTTP 403 (rate limit')
})

// ------------------------------------------------------------------ /antraege

const files: Record<string, string> = {
  '/inbox/antraege/A-20261101-01-foo.md': '---\nstatus: offen\n---\n# foo',
  '/inbox/antraege/A-20261102-01-bar.md': '---\ntitle: bar\nstatus: rueckfrage\n---\n',
  '/inbox/antraege/A-20261020-01-old.md': 'status: umgesetzt\n',
  '/inbox/antraege/_vorlage.md': 'status: offen\n',
  '/inbox/antraege/notes.txt': 'status: offen\n',
  '/inbox/antraege/erledigt/A-20261001-01-done.md': '---\nstatus: umgesetzt\n---\n',
  '/inbox/antraege/erledigt/A-20261002-01-gone.md': '---\nstatus: zurueckgezogen\n---\n',
}

const fsBeneath = (on: On) => {
  on('fs.list', (_, e) => {
    const dir = e.path.replace(/\/+$/, '') + '/'
    const names = Object.keys(files)
      .filter(p => p.startsWith(dir) && !p.slice(dir.length).includes('/'))
      .map(p => ({ name: p.slice(dir.length), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false }))
    if (dir === '/inbox/antraege/') names.push({ name: 'erledigt', kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false })
    return { value: names }
  })
  on('fs.read', (_, e) => {
    const body = files[e.path]
    return body === undefined ? { deny: 'ENOENT' } : { value: body }
  })
}

test('/antraege lists open ones first, then closed, skipping templates', {
  options: { antraegeDir: '/inbox/antraege/' },
}, async ($, on) => {
  fsBeneath(on)
  await start($, on)
  const text = (await $.command.run({ command: 'antraege', args: '' })).text ?? ''
  const lines = text.split('\n')
  expect(lines[0]).toBe('Antraege in /inbox/antraege: 2 open, 3 closed')
  expect(lines[1]).toBe('open:')
  expect(lines[2]).toMatch(/^ {2}rueckfrage\s+A-20261102-01-bar\.md$/)
  expect(lines[3]).toMatch(/^ {2}offen\s+A-20261101-01-foo\.md$/)
  expect(lines[4]).toBe('closed (newest first):')
  expect(text).toMatch(/umgesetzt\s+erledigt\/A-20261001-01-done\.md/)
  expect(text).toMatch(/zurueckgezogen\s+erledigt\/A-20261002-01-gone\.md/)
  expect(text).not.toContain('_vorlage')
  expect(text).not.toContain('notes.txt')
})

test('/antraege takes its closed statuses from the option', {
  options: { antraegeDir: '/inbox/antraege', closedStatuses: 'umgesetzt, rueckfrage' },
}, async ($, on) => {
  fsBeneath(on)
  await start($, on)
  const text = (await $.command.run({ command: 'antraege', args: '' })).text ?? ''
  // zurueckgezogen is open now; rueckfrage closed.
  expect(text.split('\n')[0]).toBe('Antraege in /inbox/antraege: 2 open, 3 closed')
  expect(text).toMatch(/open:\n {2}zurueckgezogen/)
})

test('/antraege without the option says what to set', async ($, on) => {
  await start($, on)
  expect((await $.command.run({ command: 'antraege', args: '' })).text).toContain('antraegeDir')
})

// ------------------------------------------------------------------ /wp

const PORCELAIN = [
  'worktree /src/repo', 'HEAD 1111111aaaa', 'branch refs/heads/main', '',
  'worktree /src/wt-feature', 'HEAD 2222222bbbb', 'branch refs/heads/wp/feature', '',
  'worktree /src/wt-old', 'HEAD 3333333cccc', 'detached', 'prunable gitdir file points to non-existent location', '',
].join('\n')

test('/wp runs only the fixed git argv list and reports each worktree', {
  options: { repoDir: '/src/repo' },
}, async ($, on) => {
  const calls: { argv: string; cwd?: string }[] = []
  on('process.run', (_, e) => {
    const argv = e.argv.join(' ')
    calls.push({ argv, cwd: e.init?.cwd })
    const ok = (stdout: string) => ({
      value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    })
    if (argv === 'git worktree list --porcelain') return ok(PORCELAIN)
    if (argv === 'git rev-list --left-right --count main...HEAD') {
      return ok(e.init?.cwd === '/src/wt-feature' ? '2\t5\n' : '0\t0\n')
    }
    if (argv === 'git status --porcelain') return ok(e.init?.cwd === '/src/wt-feature' ? ' M a\n?? b\n' : '')
    return { value: { exitCode: 1, stdout: '', stderr: 'unexpected', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  await start($, on)
  const text = (await $.command.run({ command: 'wp', args: '--evil; rm -rf /' })).text ?? ''

  expect(calls).toEqual([
    { argv: 'git worktree list --porcelain', cwd: '/src/repo' },
    { argv: 'git rev-list --left-right --count main...HEAD', cwd: '/src/repo' },
    { argv: 'git status --porcelain', cwd: '/src/repo' },
    { argv: 'git rev-list --left-right --count main...HEAD', cwd: '/src/wt-feature' },
    { argv: 'git status --porcelain', cwd: '/src/wt-feature' },
  ])
  const lines = text.split('\n')
  expect(lines[2]).toMatch(/^main\s+\+0\/-0\s+clean\s+1111111\s+\/src\/repo$/)
  expect(lines[3]).toMatch(/^wp\/feature\s+\+5\/-2\s+2 files\s+2222222\s+\/src\/wt-feature$/)
  expect(lines[4]).toMatch(/^\(detached\)\s+prunable\s+\?\s+3333333\s+\/src\/wt-old$/)
})

test('/wp outside a git repository says so', async ($, on) => {
  on('process.run', () => ({
    value: { exitCode: 128, stdout: '', stderr: 'not a git repository', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  await start($, on)
  expect((await $.command.run({ command: 'wp', args: '' })).text).toContain('is not a git repository')
})
