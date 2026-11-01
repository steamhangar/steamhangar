import type { EngineInterface, PluginOptions, Register } from 'claude-code'
import {
  BRANCH,
  DEFAULT_CLOSED,
  REPO,
  classify,
  closedSet,
  dirtyCount,
  formatAntraege,
  formatRuns,
  formatWorktrees,
  latestPerBranch,
  parseAheadBehind,
  parseRuns,
  parseWorktrees,
  statusOf,
  type Antrag,
  type WorktreeState,
} from './parse.ts'

// The only host commands this mod ever runs: fixed argv, no shell, nothing
// from the person or the model. Only the working directory varies: the
// `repoDir` option (or the session's cwd) and the worktree paths git itself
// lists. See README.md.
const GIT = {
  worktrees: ['git', 'worktree', 'list', '--porcelain'],
  aheadBehind: ['git', 'rev-list', '--left-right', '--count', 'main...HEAD'],
  status: ['git', 'status', '--porcelain'],
} as const

type GitCommand = keyof typeof GIT

const text = (options: PluginOptions, key: string): string => {
  const value = options[key]
  return typeof value === 'string' ? value.trim() : ''
}

async function git(
  $: EngineInterface,
  which: GitCommand,
  cwd: string | undefined,
): Promise<string | undefined> {
  try {
    const ran = await $.process.run(GIT[which], { cwd, timeoutMs: 15000 })
    return ran.exitCode === 0 ? ran.stdout : undefined
  } catch {
    return undefined
  }
}

async function ci($: EngineInterface, repo: string, args: string): Promise<string> {
  if (!REPO.test(repo)) {
    return 'dev-commands: set the plugin option githubRepo to "owner/repo" for /ci.'
  }
  const branch = args.trim()
  if (branch !== '' && !BRANCH.test(branch)) return `dev-commands: "${branch}" is not a branch name.`
  const query = branch === '' ? '' : `&branch=${encodeURIComponent(branch)}`
  const url = `https://api.github.com/repos/${repo}/actions/runs?per_page=100${query}`
  let response
  try {
    response = await $.http.fetch(url, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'claude-mods-dev-commands' },
    })
  } catch (error) {
    return `CI ${repo}: request failed (${error instanceof Error ? error.message : 'unknown error'}).`
  }
  const left = response.headers['x-ratelimit-remaining']
  const note = left === undefined ? '' : `; ${left} API requests left this hour`
  if (!response.ok) {
    const hint = response.status === 403 || response.status === 429 ? ' (rate limit without a token: 60/h)' : ''
    return `CI ${repo}: GitHub answered HTTP ${response.status}${hint}.`
  }
  let runs
  try {
    runs = parseRuns(response.text)
  } catch {
    return `CI ${repo}: the answer was not JSON.`
  }
  const now = await $.clock.now()
  return formatRuns(repo, latestPerBranch(runs, 12), now, note)
}

async function antraege($: EngineInterface, dir: string, closedOption: string): Promise<string> {
  if (dir === '') return 'dev-commands: set the plugin option antraegeDir for /antraege.'
  const root = dir.replace(/\/+$/, '')
  const closed = closedSet(closedOption === '' ? DEFAULT_CLOSED : closedOption)
  const items: Antrag[] = []
  for (const sub of ['', 'erledigt/']) {
    let entries
    try {
      entries = await $.fs.list(`${root}/${sub}`)
    } catch {
      if (sub === '') return `Antraege: cannot list ${root}.`
      continue
    }
    for (const entry of entries) {
      if (entry.kind !== 'file' || !entry.name.endsWith('.md') || entry.name.startsWith('_')) continue
      let body: string
      try {
        body = String(await $.fs.read(`${root}/${sub}${entry.name}`))
      } catch {
        continue
      }
      items.push(classify(`${sub}${entry.name}`, statusOf(body), closed))
    }
  }
  return formatAntraege(root, items, 15)
}

async function worktrees($: EngineInterface, repoDir: string): Promise<string> {
  const cwd = repoDir === '' ? undefined : repoDir
  const listed = await git($, 'worktrees', cwd)
  if (listed === undefined) return `/wp: ${cwd ?? 'the working directory'} is not a git repository.`
  const states: WorktreeState[] = []
  for (const tree of parseWorktrees(listed)) {
    if (tree.isBare || tree.isPrunable) {
      states.push(tree)
      continue
    }
    const counts = await git($, 'aheadBehind', tree.path)
    const status = await git($, 'status', tree.path)
    states.push({
      ...tree,
      aheadBehind: counts === undefined ? undefined : parseAheadBehind(counts),
      dirty: status === undefined ? undefined : dirtyCount(status),
    })
  }
  return formatWorktrees(states)
}

export const register: Register = (on, options) => {
  const githubRepo = text(options, 'githubRepo')
  const antraegeDir = text(options, 'antraegeDir')
  const closedStatuses = text(options, 'closedStatuses')
  const repoDir = text(options, 'repoDir')

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'ci',
      description: 'Newest GitHub Actions run per branch and workflow (public API, no token)',
      argumentHint: '[branch]',
      immediate: true,
    })
    await $.command.register({
      name: 'antraege',
      description: 'Status of the Antrag files, open ones first',
      immediate: true,
    })
    await $.command.register({
      name: 'wp',
      description: 'Git worktrees: branch, ahead/behind main, dirty files',
      immediate: true,
    })
    return next(e)
  })

  on('command.run', { command: 'ci' }, async ($, e) => ({ text: await ci($, githubRepo, e.args) }))
  on('command.run', { command: 'antraege' }, async $ => ({
    text: await antraege($, antraegeDir, closedStatuses),
  }))
  on('command.run', { command: 'wp' }, async $ => ({ text: await worktrees($, repoDir) }))
}
