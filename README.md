# oh-git-memory plugin

Git-backed, cross-conversation project memory for OpenHands (per-conversation
Docker mode). Git is the only channel in this deployment that reliably leaves a
conversation container: egress is open, and `git` is present in the image.

Memory is not whatever the agent happened to write during a turn. Two separate
**agents** handle the turn when it ends: one decides what is worth keeping and
stages that text verbatim, a second condenses it and files it.

## What it does

| Event | Step | Action |
|---|---|---|
| `UserPromptSubmit` | 1 | publish anything pending, pull, mark the end of the transcript, then inject the memory paths and contents |
| `Stop` | 1 | **collect** - build the decide agent's input, and stage anything already decided |
| `Stop` | 2 | **decide** - an `agent` hook judges the turn; if anything is durable it copies the text verbatim into the staging file |
| `Stop` | 3 | **stage** - build the remember agent's input from the staged notes |
| `Stop` | 4 | **remember** - an `agent` hook condenses the notes into bullets and files them |
| `Stop` | 5 | **publish** - commit and push. A no-op when nothing was filed |

`<key>` = the agent profile id (`OH_RUNTIME_LAUNCHED_PROFILE.agent_profile_id`).
There is no override: one profile is one memory folder.

## Tiers

| Tier | File in the repo | Scope |
|---|---|---|
| Global | `global/MEMORY.md` | every conversation using this repo, any profile |
| Project | `agents/<key>/MEMORY.md` | same project / agent profile |

Both are injected before every prompt. The **remember** agent picks the tier when
it writes: cross-project facts to global, project-specific facts to the project
file. A staged note that names a tier decides it instead.

## How a turn is captured

```
UserPromptSubmit  -->  record .ohgm/.turn-mark = number of transcript events
       |
   main agent works
       |
Stop 1  collect   -->  .ohgm/.turn-digest.md = decide instructions
                                              + the turn (events after the mark)
                                              + current memory + the file paths
                       if the remember command was used: write that payload straight
                       to .ohgm/to-remember.md
Stop 2  decide    -->  reads the digest, and either copies the durable text
                       verbatim into .ohgm/to-remember.md or changes nothing
Stop 3  stage     -->  .ohgm/.remember-input.md = remember instructions
                                                + whatever is staged
                                                + current memory + the file paths
Stop 4  remember  -->  condenses each staged note into a bullet, appends it to the
                       right MEMORY.md, then empties the staging file
Stop 5  publish   -->  git add/commit/push (skipped when nothing changed)
```

Two agents rather than one, because *selection* and *editing* are different jobs
and only the first needs judgement. The decide agent never summarises - it copies
text - so the second works from the original wording rather than a paraphrase.
Splitting them also gives the deterministic paths a place to sit that the first
agent cannot veto (see "Telling the agents to save").

The staging file is the handoff. The remember agent empties it once the bullets are
written, so a note is never filed twice; if it dies before that, the notes stay
staged and the next turn retries them.

**Why the transcript is read from disk.** A `Stop` hook event carries only
`{"reason": "agent_finished"}` - no messages, no history, and the hook's
sub-conversation starts with empty context (no skills, no MCP config, no parent
messages). The only way a Stop-time agent can know what happened is the
conversation transcript, which the runtime persists outside the workspace, one
JSON file per event:

```
$OH_CONVERSATIONS_PATH/<conversation-id>/events/event-<N>-<uuid>.json
```

`OH_CONVERSATIONS_PATH` is in hook environments (it is not stripped), and
`OPENHANDS_SESSION_ID` is the conversation id. `turn_digest.py` uses both, falling
back to the newest directory under `$OH_CONVERSATIONS_PATH` if the id does not
name it. The high-water mark is what makes this precise: it is recorded at
`UserPromptSubmit`, so the digest contains exactly the turn that just ended rather
than the whole conversation. The runtime persists the user's message *before* the
hook runs - persistence is the first callback in the chain and the hooks are
appended after it - so the mark lands one event past that message, and
`turn_digest.py` steps back over it. Without that step the turn reads as if the user
had said nothing, and the command token below would never be found. With no mark at
all it falls back to the last 80 events.

The digest deliberately strips the noise - tool observations, state updates, the
initial system prompt - and keeps the user's message plus, for every step, what the
agent said as it acted and one line for the tool it called with the runtime's own
summary. A turn whose closing message is a bare "Done." is therefore still legible
from the steps above it. When a turn is too big to fit, entries are elided from the
middle: the opening request and the closing summary are the parts worth keeping. A
cheap model reading one ~20KB file is far more reliable than one being asked to go
exploring. The remember agent's input is smaller still, since it holds only the
staged notes.

## Telling the agents to save

The decide agent judges on its own, which is what you want day to day and awkward
when you are testing. So there is a command:

```
/oh-git-memory:remember the deploy target is staging
/oh-git-memory:remember global we always use uv here, never pip
```

A plugin `commands/*.md` file is not a subprocess. The SDK converts it into a
**keyword-triggered skill** with the trigger `/<plugin-name>:<command-name>` and
merges it into the agent context like any other skill - which is also why it
appears in the plugin's skill list in Agent Canvas. Typing the command activates
that skill content inside the user message; the main agent sees the instruction,
acknowledges, and does not write anything itself.

The save is not left to any agent's judgement. `turn_digest.py` looks for the
command token in the turn's user messages and, when it finds it, writes the payload
**straight into the staging file** with its tier - before the decide agent runs.
The decide agent is then told it is already staged and has nothing to add, so the
note reaches the remember agent whether or not the first agent agreed. No LLM
decides whether an explicit request survives.

Four plumbing notes:

- The trigger is namespaced as `/oh-git-memory:remember`. The plugin name is part
  of the trigger, so it cannot be shortened to `/remember`.
- The `$ARGUMENTS` in `commands/remember.md` is *not* substituted - a command is
  static text. The payload is read from the user's actual message instead, which is
  why detection works off the transcript rather than the command body. Extraction is
  bounded to the one line the command was typed on, so the skill text the runtime
  appends to that same message is not swallowed as the payload.
- This plugin uses the **Claude Code layout** (`.plugin/plugin.json`), where
  `commands/`, `skills/` and `hooks/` sit at the plugin root. The portable Agent
  Plugins layout requires a root `plugin.json` carrying a `$schema`, and then moves
  all three under `dev.openhands/` - so a plugin that switches formats has to move
  those directories, not just the manifest.
- `POST /api/plugins/install` reports only the `skills/` directory. Command-derived
  skills never appear in that response: they are added at conversation build time
  through `plugin.add_skills_to()`, which calls `get_all_skills()`. Agent Canvas
  shows them separately, as "command-derived skills".

## What the SDK actually supports

Verified by reading `software-agent-sdk` source (`openhands/sdk/hooks/` and
`openhands/sdk/plugin/`), not just the docs. These facts drive the design:

| Fact | Consequence here |
|---|---|
| Command hooks run with `shell=True`, `cwd` = the **workspace** | a relative path in a hook command resolves to the user's project, not the plugin - so hooks reference scripts by absolute path |
| The event JSON arrives on **stdin** | a hook can inspect the event, but must read stdin before any long child process |
| `stdout` JSON keys are `decision`, `reason`, `additionalContext`, `continue` | `additionalContext` is the injection channel |
| **Only `UserPromptSubmit` injects `additionalContext`** | `run_session_start()` records the result and drops it; `Stop` only uses it as feedback when it denies a stop |
| Exit `2` blocks; any other non-zero is a non-fatal error | every hook here exits 0, so it can never block a prompt |
| `type: "agent"` hooks spin up a **sub-conversation** with the parent's LLM | both agents can think, use tools and edit files |
| ...but that sub-conversation gets no conversation history, no skills, no MCP, and only `tools` + `FinishTool` | their instructions must come from `system_prompt` or a file they read - a skill would not load |
| Agent hooks also honour `system_prompt`, `tools`, `max_iterations` (default 3) and `timeout` (used as the LLM timeout) | decide runs with 4 iterations, remember with 8, both at 180s |
| Agent hooks run **in list order**, and the list itself is static | an agent cannot be skipped conditionally, so a command hook stages the text first and tells the decide agent it has nothing to add |
| A JSON hook may **not** set `command` when `type` is `"agent"` | neither agent entry has a `command` key |
| Hook env = the server env **minus** `SESSION_API_KEY`, `OH_SECRET_KEY`, `OH_SESSION_API_KEYS_*`, **plus** `OPENHANDS_PROJECT_DIR` / `OPENHANDS_SESSION_ID` / `OPENHANDS_EVENT_TYPE` | `OH_GIT_MEMORY_REPO` / `OH_GIT_MEMORY_TOKEN` reach the hooks, and so does `OH_CONVERSATIONS_PATH` |
| `Stop` runs when the agent's execution status becomes `FINISHED` | that is the capture point |
| Hook output is recorded as a `HookExecutionEvent` (stdout, stderr, context) | a broken hook is visible in the UI |

## Plugin inputs are secrets, not a config UI

Adding a plugin from a URL does **not** generate a settings form. `plugin.json`
carries only `name`, `version`, `description`, `author` and `entry_command`; there
is no declared input schema. The SDK's only mechanism for feeding values to a
plugin is `${VAR}` placeholders resolved from the **secret registry** - in a
plugin's `source`/`ref` (raising `missing_secret` when unset) and in MCP configs
via `expand_mcp_servers(config, get_secret)`.

There *is* a clickable parameters UI, but only on Cloud: a `/launch` link may carry
per-plugin `parameters`, which cloud shows as editable inputs before the
conversation starts. On a **local** backend Agent Canvas does not deliver them at
all, and a non-empty `parameters` value disables the Start button rather than
silently dropping it.

Two further limits worth knowing: `${VAR}` expansion does **not** apply to hook
commands (they are plain strings), so hooks can only read configuration from the
environment. And env vars in a conversation container are delivered as profile
secrets - which is why a plugin needing configuration asks for secrets even when
the value is not sensitive, like this plugin's repo URL.

## What is a skill vs a hook

| Content | Mechanism | Why |
|---|---|---|
| Git plumbing, transcript digest, context injection | **hooks** | a skill cannot execute anything |
| What the *main* agent should know about memory | **`skills/memory-policy.md`**, always loaded | standing guidance for every task |
| Wrap-up routine | **`skills/wrap-up/SKILL.md`**, keyword-triggered | fires on the phrase, stays model-invocable |
| `/oh-git-memory:remember` | **`commands/remember.md`** -> becomes a command-derived keyword skill | a command is only a trigger: the SDK converts it into a skill, so it reaches the agent as context and shows up in the plugin's skill list |
| Agent instructions | **`hooks/text/decide.md`** and **`hooks/text/remember.md`**, embedded in each agent's input file | an agent-hook sub-conversation gets no skills, so a skill would never load for it |
| Hook output prose (paths, section headers, reminder) | **`hooks/text/*.md`** | assembled from runtime values, not instructions |

`memory-policy.md` deliberately has no frontmatter and no `triggers:`. A legacy
`*.md` skill without a trigger is loaded in **full** into `<REPO_CONTEXT>` at
conversation start, so the instruction is guaranteed to be present rather than
model-mediated. A `SKILL.md` would only advertise its description and wait to be
invoked - wrong for a standing policy.

The wrap-up routine is the opposite case: it should fire on a phrase, and a
keyword trigger does that declaratively while leaving the skill model-invocable
too, so paraphrases can still reach it.

A path-triggered rule on `MEMORY.md` would be the ideal home for "append, do not
rewrite", but rules are repo-scoped - "touching a file outside the workspace never
fires a rule" - and the clone lives in `$HOME`, outside the workspace.

## Layout

```
.plugin/plugin.json
commands/remember.md      # /oh-git-memory:remember -> stages the payload
hooks/hooks.json          # the whole wiring: 1 UserPromptSubmit + 5 Stop steps
hooks/scripts/
├── common.sh             # shared setup: config, clone, bootstrap, pull, publish
├── ohgm-prompt.sh        # UserPromptSubmit: publish, pull, mark, inject
├── ohgm-collect.sh       # Stop 1: decide agent's input + staging of decided notes
├── ohgm-stage.sh         # Stop 3: remember agent's input, from the staged notes
├── ohgm-publish.sh       # Stop 5: commit and push
├── turn_digest.py        # transcript -> .ohgm/.turn-digest.md; --mark
├── stage_notes.py        # staged notes -> .ohgm/.remember-input.md
├── emit_context.py       # the injected context, as JSON
├── templates.py          # {{placeholder}} renderer for hooks/text/
└── profile_key.py        # profile id -> memory folder key
hooks/text/               # hook output and agent prose, as markdown
├── decide.md             # what the decide agent must do
├── remember.md           # what the remember agent must do
├── memory-paths.md
├── global-section.md
├── project-section.md
└── reminder.md
skills/
├── memory-policy.md      # no trigger -> always loaded
└── wrap-up/SKILL.md      # keyword-triggered
```

One script per task, shared logic in `common.sh`, Python in `.py` files (runnable
on their own), prose in markdown. No logic lives inside `hooks.json`, and each
agent entry there carries only a pointer to the file it should read.

## Resolving the hook scripts

A hook command is just a string, and the SDK does **not** substitute a plugin-root
variable (there is no `${CLAUDE_PLUGIN_ROOT}` equivalent), so each command locates
its own script:

1. `$OH_GIT_MEMORY_PLUGIN_DIR/hooks/scripts/<script>` if you set that override;
2. otherwise `$HOME/plugins/installed/oh-git-memory/hooks/scripts/<script>`;
3. otherwise a bounded `find` (`-maxdepth 8`) over `$HOME/plugins`,
   `$HOME/cache/plugins`, `$PWD/.agents/plugins` and `$PWD/.openhands/plugins`.

The fallback matters because where the files live depends on how the plugin was
loaded. An *installed* plugin keeps them under `$INSTALLED_DIR/<name>`. A plugin
attached as a `PluginSource` - a marketplace entry, or the `/launch` route - is
instead fetched into `$PERSISTENCE/cache/plugins/<repo>-<hash>/resolved-refs/<sha>/`,
and that directory is *not* named after the plugin. That is why the fallback
matches the script's unique file name (`ohgm-prompt.sh`) rather than the plugin
directory - matching `*/oh-git-memory/hooks/...` would miss the cache copy.

If no script is found the hook writes a warning to stderr and exits 0. That stderr
becomes a `HookExecutionEvent`, so a broken install is visible in the UI instead of
failing mute.

Note `$HOME` here is already `/var/openhands/.openhands`, so the install path is
`$HOME/plugins/...`, not `$HOME/.openhands/plugins/...`.

## The GitHub repo

The repo holds **only memory data**:

```
.gitattributes          *.md merge=union
.gitignore              .ohgm/            <- plugin scratch, never committed
global/MEMORY.md
agents/<key>/MEMORY.md
.ohgm/                  transcript mark, both agent inputs, staged notes (ignored)
```

The plugin bootstraps all of it on first run, so the repo can start completely
empty. `.ohgm/` keeps all of this out of git; without it the mark file, the agent
inputs and the staged notes get swept up by `git add -A` and pushed as if they were
memory.

## Debugging

Run a step by hand:

```sh
OH_GIT_MEMORY_REPO=https://github.com/you/oh-git-memory.git sh hooks/scripts/ohgm-prompt.sh
OH_GIT_MEMORY_REPO=https://github.com/you/oh-git-memory.git sh hooks/scripts/ohgm-collect.sh
OH_GIT_MEMORY_REPO=https://github.com/you/oh-git-memory.git sh hooks/scripts/ohgm-stage.sh
OH_GIT_MEMORY_REPO=https://github.com/you/oh-git-memory.git sh hooks/scripts/ohgm-publish.sh
```

The three files between the hooks are exactly what each stage saw:

```sh
less "$HOME/.oh-git-memory/.ohgm/.turn-digest.md"     # the decide agent's input
less "$HOME/.oh-git-memory/.ohgm/to-remember.md"      # the notes it staged
less "$HOME/.oh-git-memory/.ohgm/.remember-input.md"  # the remember agent's input
```

Both agents are `agent` hooks, so neither can be invoked from a shell. To exercise
the pipeline offline: run `ohgm-collect.sh` against a fixture transcript, append a
`## staged:` section to `to-remember.md` by hand exactly as the decide agent would,
run `ohgm-stage.sh`, and read its input file.

To test capture end to end, type the command in a conversation:

```
/oh-git-memory:remember project the staging deploy uses a different bucket
```

then check the repo: a commit should add that bullet to `agents/<key>/MEMORY.md`.
The payload is staged verbatim before either agent runs, so if the bullet is missing
the fault is in the remember agent rather than in detection.

Each script prints its JSON result (or nothing) and always exits 0, so they are
safe to run repeatedly. To exercise the hook *commands* - including script
resolution - copy a `command` string out of `hooks/hooks.json` and run it with
`sh`. Set `OH_GIT_MEMORY_DIR` to a scratch path to test without touching the real
clone.

## Where writes come from

Recognising "this looks like project memory" is a judgement, so it is an agent's
job - but neither the main agent's nor a single agent's. The main agent keeps
working; capture happens at the end of the turn. The division of labour:

- **Selection** - the decide agent reads the turn digest and copies the durable
  text verbatim into the staging file, driven by `hooks/text/decide.md`.
- **Authoring** - the remember agent condenses the staged notes and appends
  bullets, driven by `hooks/text/remember.md`.
- **Transport** - Stop steps 1, 3 and 5 collect, stage and publish;
  `UserPromptSubmit` pulls before injecting.

Because neither agent is the main agent, `memory-policy.md` tells the main agent
*nothing to do*: don't edit the memory files, just acknowledge "remember this" and
let the turn end. Publishing then only ever contains filed facts - a turn where
nothing is staged produces no commit at all.

### Why the writer is an agent, not the main agent

The main agent's writes cannot be distinguished from turn noise: it is the same
context that produced the work. A separate agent sees the turn after the fact, with
the memory files in front of it, and can answer one narrow question - is any of this
durable? Splitting selection from authoring keeps that step cheap: deciding needs a
model but returns text rather than prose, so the second agent's job is mechanical
and its input stays small.

The risk to know about: if no LLM is configured for the hook, the SDK logs a
warning and **falls open** - both agents are skipped, so nothing is captured that
turn. The other hooks still publish normally.

## Setup

1. Create a **private** GitHub repo, e.g. `you/oh-git-memory`. It can be empty -
   the plugin creates the files it needs on first run.
2. Create a fine-grained PAT with **Contents: read and write** on that one repo.
3. In Agent Canvas, add these as secrets on your agent profile (secrets are
   injected into the conversation container as env vars):
   - `OH_GIT_MEMORY_REPO` - e.g. `https://github.com/you/oh-git-memory.git`
   - `OH_GIT_MEMORY_TOKEN` - the PAT above
4. Install this plugin on your backend and enable it.
5. Start a conversation. The repo should gain a first commit containing
   `.gitattributes`, `.gitignore`, `global/MEMORY.md` and `agents/<key>/MEMORY.md`.

That is the whole configuration: two secrets, and `OH_GIT_MEMORY_TOKEN` is skipped
if `GITHUB_TOKEN` is already in the environment. There is no plugin settings form
to fill in - see "Plugin inputs are secrets" above.

The PAT is readable by the agent's own terminal (only `SESSION_API_KEY`,
`OH_SECRET_KEY` and `OH_SESSION_API_KEYS_*` are stripped from the env). That is
acceptable here because the credential only grants access to the memory repo the
agent is already meant to read and write - but keep the PAT scoped to that single
repo, and do not reuse it elsewhere.

## Config

| Var | Default | Purpose |
|---|---|---|
| `OH_GIT_MEMORY_REPO` | - | required; repo URL (https or a local/file path) |
| `OH_GIT_MEMORY_TOKEN` / `GITHUB_TOKEN` | - | PAT for a private https repo |
| `OH_GIT_MEMORY_DIR` | `$HOME/.oh-git-memory` | optional: clone location |
| `OH_GIT_MEMORY_PLUGIN_DIR` | `$HOME/plugins/installed/oh-git-memory` | optional: where the hook scripts are |

## Design notes

- **Never blocks.** Every git call is time-bounded and wrapped; on any failure the
  hook still exits 0 and injects the last-known file, so a slow or unreachable
  remote degrades to "stale memory", never to a failed prompt. The agents are
  timed out by the SDK and their failure falls open.
- **Concurrency.** `*.md merge=union` + `pull --rebase --autostash` + push retries
  let several conversations append to the same file without conflicts. The
  transcript mark is per conversation, so concurrent turns do not interfere.
- **Latency.** The agents run after the main agent finishes, so they do not delay a
  response - but they do hold the turn open briefly, and there are now two of them.
  Lower `max_iterations` or `timeout` in `hooks/hooks.json` if that matters.
- **Where the clone lives.** `$HOME` is per-conversation persistence, so the clone
  survives idle stops and restarts - but it is still per conversation; the *repo*
  is what is shared, not the working copy.

## Prior art consulted

`examples/05_skills_and_plugins/02_loading_plugins/example_plugins/code-quality`
in `github.com/OpenHands/software-agent-sdk` is the reference plugin with hooks
(`.plugin/plugin.json` + `hooks/hooks.json` + `skills/`), and its structure is
mirrored here. The `agent` hook used for both agents is documented but not
exemplified there; its behaviour comes from `hooks/executor.py`
(`_execute_agent_hook`).
