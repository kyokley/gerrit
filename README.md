# Gerrit agent workbench

A local multiplexing workbench for AI coding agents. Run independent tasks in
parallel, keep their Jujutsu workspaces separate, and upload small reviewable
units to task-specific Gerrit branches instead of waiting for an entire task.
[Chloe](https://github.com/kyokley/chloe) is the workflow reference: parallel
task panes, provider processes, and workspace isolation—not copied source.

The application uses Python 3.14+ and its standard library. Agent model access,
Jujutsu with `jj gerrit upload`, Git, OpenSSH, and an existing Gerrit account are
runtime prerequisites. SecretSpec is optional. Installing or opening the
dashboard does not enroll credentials, create branches, or upload changes.

## Start

```sh
uv sync
uv run gerrit init
uv run gerrit serve
```

Open `http://127.0.0.1:8765`. Configure the repository, agent command, Gerrit
destination, and existing credential sources in **Settings**, then create a
task. After configuration, `uv run gerrit check` checks local prerequisites
without resolving credentials or contacting Gerrit; it does not establish upload readiness.

Configuration defaults to `$XDG_CONFIG_HOME/gerrit/settings.json` (or
`~/.config/gerrit/settings.json`). State defaults to
`$XDG_STATE_HOME/gerrit` (or `~/.local/state/gerrit`). Use `--settings` and
`--state-dir` to select other paths. Keep credential and state files outside
repositories. The dashboard binds only to loopback; do not expose it through a
public reverse proxy.

## Gerrit settings

Configure all of these before starting live tasks:

- HTTP(S) base URL, including any port and context path.
- SSH host and port, automation username, Gerrit project, and base branch.
- Independently trusted SSH `known_hosts` file. Scanning an untrusted host is
  not independent host-key verification.
- An existing HTTP token and explicitly designated managed SSH private key,
  through protected files or a selected SecretSpec profile/scope.
- Absolute local jj repository path and agent argv.

The task branch is `agents/<task-slug>-<title-hash>`. It is created from the
verified immutable base revision when missing. Existing branches are verified,
never moved. A project-and-branch Gerrit chain link appears as soon as the first
feature upload is confirmed by authenticated server readback; a readiness probe
is separate from that feature chain.

Settings are snapshotted per task. Changes affect subsequent tasks and agent
invocations belonging to those tasks, not a task already running. Destination
fields and workflow instructions are injected automatically. Token and private
key values are not persisted in settings or included in prompts.

### Protected files

Select `token-file`, an existing token file, and an existing SSH private-key
material file. Files must be regular, owned by the runtime user, inaccessible
to group/others, and outside the repository. The workbench does not create,
replace, enroll, or revoke account credentials. It makes only a private,
temporary managed-key copy for its strict upload transport.

HTTP Basic authentication over plain HTTP exposes credentials to the network.
Use HTTPS except on a deliberately trusted transport.

### SecretSpec

Select `secretspec`, an existing manifest, profile, and scope. Default variable
names are `GERRIT_TOKEN` and `GERRIT_SSH_KEY`. The latter contains **private-key
material**, not a path. Provider failure or an empty required value stops the
task; there is no fallback to another provider.

Nonsecret destination fields can also come from SecretSpec. For example, set
the settings `field_env` mapping to:

```json
{
  "http_url": "GERRIT_HTTP_URL",
  "ssh_host": "GERRIT_SSH_HOST",
  "ssh_port": "GERRIT_SSH_PORT",
  "username": "GERRIT_USERNAME",
  "project": "GERRIT_PROJECT",
  "base_branch": "GERRIT_BASE_BRANCH"
}
```

Declare only the necessary fields in the chosen scope. An illustrative
manifest contains declarations, **not credential values**:

```toml
[project]
name = "gerrit-agents"
revision = "1.0"
require_reason = true

[profiles.default]
GERRIT_HTTP_URL = { required = true }
GERRIT_PROJECT = { required = true }
GERRIT_TOKEN = { required = true }
GERRIT_SSH_KEY = { required = true }

[scopes.gerrit]
secrets = ["GERRIT_HTTP_URL", "GERRIT_PROJECT", "GERRIT_TOKEN", "GERRIT_SSH_KEY"]
```

Configure an existing trusted SecretSpec provider separately. Do not put token
or key values in the manifest. The CLI must support the selected `--file`,
`--reason`, `run`, `--profile`, and `--scope` options. The application checks
capabilities before provider access and passes a nonsecret access reason.

## Agent contract and incremental reviews

The default command is `opencode run --standalone --format json {prompt}`. A different CLI
can be configured as a JSON argv array; `{prompt}` is one argument, never a
shell expansion. The CLI must be able to edit files and follow the result-file
protocol, including writing to the supplied result directory outside its
workspace. Configure that narrow filesystem permission in your chosen CLI.
This is not an interactive terminal emulator or a provider sandbox.
The default uses a private OpenCode runtime per invocation so a previously
started shared service cannot retain another task's workspace or environment.

Each invocation creates at most one reviewable unit and exits. The prompt
identifies the task, workspace, Gerrit destination, and result file. The agent
writes this JSON to `GERRIT_RESULT_FILE`, outside the repository:

```json
{
  "message": "parser: Handle empty input",
  "tests": [["python", "-m", "unittest", "tests.test_parser"]],
  "done": false
}
```

Use `done: true` for the last unit; a no-change finish can contain only
`{"done": true}`. Tests are argv arrays, not shell strings. The supervisor waits
for agent quiescence, runs focused checks, verifies immutable commit metadata
and upload selection, dry-runs the upload, uploads for review, and reads it back
before starting the next unit. It does not submit or merge changes. Ambiguous
writes stop for reconciliation rather than being blindly retried.

The workflow is based on
[`jj-gerrit-development`](opencode/skills/jj-gerrit-development/SKILL.md).
The disposable readiness probe is isolated from feature ancestry and is not
automatically abandoned. Local jj IDs, immutable local commit hashes, Gerrit
Change-Ids, and server revision hashes remain distinct identities.

## Ownership and safety

Run `serve` in the foreground while tasks and review monitoring are active.
An agent finishing its edits is not the same as monitoring finishing. The
minimum review deadline is 12 hours after the first confirmed upload, including
the probe. Cancellation, authentication failure, and shutdown must leave an
honest coverage gap or handoff, not a claim of completed monitoring.

The private nonsecret journal records operation outcomes and interrupted work.
Restart does not replay writes, resume agents, or renew authorization. Keep
workspaces and journals for reconciliation; do not discard them after an
unknown upload outcome.

Automatic feedback corrections are limited to the current tip change. Feedback
on earlier changes stays pending for an explicit human handoff; the workbench
does not silently rewrite descendants. Verified correction replies do not
automatically resolve review threads. Unsupported or unresolved check-provider
state prevents automatic completion rather than being treated as success.

Agent processes are trusted programs running as your OS user. Separate
workspaces, sanitized environments, temporary key material, and redaction reduce
accidental exposure; they do not isolate a malicious same-user agent from
credential files or other processes. Reviewer text is untrusted input, not
authorization to expand a task's scope.

## Development evidence

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
uv build
```

Tests use isolated repositories, fake processes, and local HTTP fixtures. They
do not prove compatibility with your real Gerrit deployment, chosen model,
SSH account, or SecretSpec provider. See
[`docs/orchestrator-contract.md`](docs/orchestrator-contract.md) for module
boundaries and validation ownership.
Recorded results and live-integration limitations are in
[`docs/verification.md`](docs/verification.md).

Reference contracts: [OpenCode V2 CLI](https://opencode.ai/v2/docs/cli),
[SecretSpec CLI](https://secretspec.dev/reference/cli/),
[SecretSpec configuration](https://secretspec.dev/reference/configuration/),
[Jujutsu Gerrit](https://docs.jj-vcs.dev/latest/gerrit/), and
[Gerrit REST](https://gerrit-review.googlesource.com/Documentation/rest-api.html).
