# tools/claude-mods/

Claude Code mods (function-hook plugins) for working on this repository
(DEV-MODS-1, rc22). The folder is a local marketplace
(`.claude-plugin/marketplace.json`); each mod is its own plugin folder with
its own marketplace entry. The code is generic: no host paths, addresses or
host names. Everything host-specific (directories, the GitHub repository)
comes in as a plugin option (`userConfig`).

Requires Claude Code >= 2.1.287 (developed and tested against 2.1.316).

## Loading

Where mods are limited to managed ones (`allowManagedModsOnly`), the
operator adds this folder (or a copy of it) as a managed marketplace and
enables the mods there; a session's own `--plugin-dir` is refused by policy.
Without that policy, for development:

```sh
claude --plugin-dir tools/claude-mods/output-trim --plugin-dir tools/claude-mods/dev-commands
```

Options for a `--plugin-dir` mod are read from user settings,
`pluginConfigs["<mod>"].options`; for an installed mod, from `/config`.

## Checks

Each mod ships `claude plugin test` tests under `tests/` and must pass
`claude plugin validate` cleanly:

```sh
claude plugin test tools/claude-mods/output-trim
claude plugin test tools/claude-mods/dev-commands
claude plugin validate tools/claude-mods/output-trim
claude plugin validate tools/claude-mods/dev-commands
claude plugin validate tools/claude-mods
```

## output-trim

Hooks `tool.call` for Bash. After the command ran, if its output has more
than `maxLines` lines or more than `maxBytes` bytes, the full output
(stdout, then stderr after a `--- stderr ---` line) is written to a file and
the model gets instead:

- the first `headLines` lines,
- one marker line with the number of omitted lines, then up to `maxMatches`
  lines from the omitted middle that match
  `FAIL|FAILED|ERROR|error:|panic|Traceback|AssertionError`, each prefixed
  with its line number,
- the last `tailLines` lines,
- one line naming the file with the full output.

A few very long lines (minified JSON) are cut per line at 400 characters.
A failing command (non-zero exit) is trimmed the same way; its error text is
what is kept.

Never trimmed:

- short output (at or under both thresholds);
- commands that already ask for a small range or a count: `head`, `tail`,
  `sed -n`, `grep -c` / `--count`, `wc`;
- commands that read a kept file back (they name the output directory);
- anything when the file cannot be written: then the output stays whole.

| option | default | meaning |
|---|---|---|
| `outputDir` | empty | where full outputs are kept; empty: `$CLAUDE_PLUGIN_DATA/output-trim`, else `~/.claude/plugins/data/output-trim` |
| `maxLines` | 200 | trim above this many lines |
| `maxBytes` | 20480 | trim above this many bytes |
| `headLines` | 40 | lines kept from the start |
| `tailLines` | 80 | lines kept from the end |
| `maxMatches` | 30 | matching middle lines kept |

The kept files are not cleaned up by the mod; prune the directory now and
then.

## dev-commands

Three slash commands answered by the mod itself: no model turn is started
(the output row is visible to the model on the next turn, like any command
output). All run `immediate`, so they work while a turn is running.

| command | does | option |
|---|---|---|
| `/ci [branch]` | newest GitHub Actions run per branch and workflow (12 branches at most), from the public API without a token (60 requests per hour per IP; the answer shows what is left) | `githubRepo` (`owner/repo`) |
| `/antraege` | the `status:` of every `*.md` in the directory and its `erledigt/` subfolder (files starting with `_` are templates and skipped), open ones first, then the 15 newest closed ones | `antraegeDir`, `closedStatuses` |
| `/wp` | every git worktree: branch, ahead/behind `main`, dirty file count, head, path | `repoDir` (empty: the session's working directory) |

`closedStatuses` is a comma-separated list; empty means
`umgesetzt,erledigt,abgelehnt,zurueckgezogen,done,closed,rejected,withdrawn`.
A file without a `status:` line counts as open.

### Host commands of /wp

`/wp` uses `$.process.run`, which runs a command by argv with no shell. The
mod runs exactly these three argv lists and nothing else (the `GIT` table in
`dev-commands/hooks/register.ts`):

| argv | working directory |
|---|---|
| `git worktree list --porcelain` | the `repoDir` option, or the session's working directory |
| `git rev-list --left-right --count main...HEAD` | each worktree path the first command listed |
| `git status --porcelain` | each worktree path the first command listed |

No argument comes from the person or the model: `/wp` ignores what is typed
after it, and only the working directory varies. Claude Code runs git with
repository hooks off. Bare and prunable worktrees are listed without running
the other two commands. The base branch is fixed to `main`.

`/ci` uses `$.http.fetch` (GET `https://api.github.com/repos/<repo>/actions/runs`,
the branch filter URL-encoded and limited to `[A-Za-z0-9_./-]`); `/antraege`
only lists and reads files with `$.fs`.
