# tools/claude-mods/

Claude Code mods (function-hook plugins) for working on this repository.
This folder is a local plugin marketplace: `.claude-plugin/marketplace.json`
lists one entry per mod, each mod lives in its own subfolder. Written
against Claude Code 2.1.316; the hook API is early access, so re-run the
tests and `claude plugin validate` after a Claude Code update.

The mods hold no host paths. Anything host-specific (where a log goes) is
a plugin option the operator sets.

| mod | does |
|---|---|
| `token-meter` | one JSONL line per model request (main loop and subagents) with token counts; `/tokens` prints a summary |
| `read-brake` | denies whole-file reads of the big planning docs and of very large files |
| `output-trim` | keeps long Bash output in a file, returns head, tail and failure lines |
| `dev-commands` | `/ci`, `/antraege`, `/wp` answered without a model turn |

## token-meter

Hooks `turn.step` (every model request, main loop and subagents) and
`agent.spawn` (to learn a subagent's type and label). It passes the model's
stream through untouched and, once the response is complete, appends one
line to `<logDir>/<session-id>.jsonl`:

```json
{"kind":"request","ts":"2026-11-01T12:00:01.234Z","session":"…","agentId":"main",
 "model":"…","turnId":"…","step":0,"input":100,"output":20,"cacheRead":5000,
 "cacheWrite":300,"durationMs":1234,"stopReason":"end_turn"}
```

Subagent lines carry the subagent's id plus `agentType` and `agentLabel`
(the Agent call's short description). Each spawn also writes a
`{"kind":"spawn",…}` line. A request that returned no usage is logged with
zero counts and `"noUsage":true`. Prompt, answer and tool content are never
written, only counts. `durationMs` is the time from the request being sent
to its last chunk.

Files roll over to `<session-id>.1.jsonl`, `.2.jsonl`, … before a part
reaches the part size, because a hook module reads files only up to 4 MiB
and an append is a read plus a write.

`/tokens [session-id]` reads those files and prints requests, input, output,
cache-read and cache-write tokens and time per agent and in total for the
current session (or the one named). It runs no model call; the summary is
an ordinary command output row.

Options:

| option | default | meaning |
|---|---|---|
| `logDir` | empty | absolute directory for the JSONL files. Empty: `$CLAUDE_PLUGIN_DATA` if set, else `<$CLAUDE_CONFIG_DIR or ~/.claude>/plugins/data/token-meter` |
| `partKb` | 3072 | roll-over size per part file, 1 to 3584 KB |

Not covered: requests made outside a turn's loop (a compaction's summary
request, another mod's `$.model.complete`) do not pass `turn.step`. A
subagent that another plugin spawned through `$.agent.spawn` is invisible
to that plugin's own hooks only, not to this one.

## read-brake

Hooks `tool.call` for `Read` and `Bash` and answers `{ deny }` with a short
hint (`grep -n '<term>' <file>`, then Read with offset and limit) when a
whole-file read hits one of these rules:

- `docs/WORKPACKAGES.md` and any file directly in `docs/plan-archive/`:
  always braked as a whole read;
- `docs/PROJECT_PLAN.md`: braked only above `planKb` (the core plan is
  meant to be read whole while it stays small);
- any file above `largeKb`.

A Read with `offset`, `limit` or `pages` is a ranged read and always passes.
In Bash only a plain `cat <file>…` pipeline is braked (split on `&&`, `||`,
`;` and newlines; a stage with a pipe, a redirection, a substitution or a
heredoc is left alone, so `cat f | head` passes). Relative paths are
resolved against the session's directory, not a `cd` earlier in the
command. A brake that fails (stat error, a bug) lets the read through: the
mod saves tokens, it is not a security boundary.

Options:

| option | default | meaning |
|---|---|---|
| `planKb` | 100 | whole-read limit for `docs/PROJECT_PLAN.md` |
| `largeKb` | 150 | whole-read limit for any file |

## Checking a mod

```sh
claude plugin test tools/claude-mods/token-meter
claude plugin test tools/claude-mods/read-brake
claude plugin validate --strict tools/claude-mods
claude plugin validate --strict tools/claude-mods/token-meter
claude plugin validate --strict tools/claude-mods/read-brake
```

The tests run each mod against an in-memory engine (`claude-code/testing`):
no files are written and no model is called.

## Installing them (operator)

For a single session while developing, load a mod from its folder:

```sh
claude --plugin-dir tools/claude-mods/token-meter --plugin-dir tools/claude-mods/read-brake
```

Options of a mod loaded this way are read from the `pluginConfigs` block of
the settings, keyed by the mod's name:

```json
{ "pluginConfigs": { "token-meter": { "logDir": "/absolute/log/dir" } } }
```

As managed mods (a host that only admits mods its managed settings name,
for example with `allowManagedModsOnly`): add this folder, or the
repository's git URL with this path, as a known marketplace in the managed
settings and enable the mods there, for example

```json
{
  "extraKnownMarketplaces": {
    "steamhangar-dev-mods": {
      "source": { "source": "directory", "path": "/absolute/path/to/tools/claude-mods" }
    }
  },
  "enabledPlugins": {
    "token-meter@steamhangar-dev-mods": true,
    "read-brake@steamhangar-dev-mods": true
  }
}
```

and set the options with `claude plugin configure <mod>` (or the
`pluginConfigs` block). Check the managed-settings key names against the
documentation of the installed Claude Code version; they are not
exercised by the tests here. A marketplace that is a folder is read from
that folder itself, so after an update of the checkout `/reload-plugins`
picks up the new code.

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
