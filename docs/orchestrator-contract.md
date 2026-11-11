# Agent orchestrator contract

This application takes Chloe's parallel task panes and workspace isolation as
product principles, not as code to copy. Python's standard library supplies a
loopback dashboard and supervisor. Live actions occur only after an operator
starts a task with complete settings; developing or opening this application
does not contact Gerrit.

## Module ownership and interfaces

`settings.py` owns `Settings.from_dict(data)`, `Settings.to_dict()`,
`Settings.validate(live=False)`, `load_settings(path)`, `save_settings(path, settings)`.
Use a flat JSON settings object with these keys:

- `repository` (absolute local jj repository path), `agent_command` (argv list;
  default `["opencode", "run", "--format", "json", "{prompt}"]`),
  `max_parallel` (default 3), `max_units` (default 20), `agent_timeout` (1800).
- `http_url`, `ssh_host`, `ssh_port` (29418), `username`, `project`,
  `base_branch` (`main`), `known_hosts` (absolute, independently trusted file).
- `credential_mode` (`token-file` or `secretspec`), `token_file`, `ssh_key_file`.
  Files contain existing explicitly designated credential material, not inline values.
- `spec_file`, `profile` (`default`), `scope` (`gerrit`),
  `token_env` (`GERRIT_TOKEN`), `ssh_key_env` (`GERRIT_SSH_KEY`),
  `field_env` (mapping of nonsecret Gerrit setting names to SecretSpec variable names).
- `poll_interval` (300 seconds), `monitor_hours` (minimum/default 12).

No token or private key values may appear in settings or API responses. Unknown
keys are errors. Missing destination values are permitted when saving settings,
not when starting tasks. SecretSpec field references may satisfy missing fields.

`credentials.py` owns a context manager `credentials(settings)` yielding
`CredentialContext.settings` (resolved snapshot), `.token`, `.key_path`,
`.environment()` (a sanitized allowlist for ordinary child processes), and
`.ssh_environment()` (strict process-scoped Git/OpenSSH transport environment).
It resolves only explicitly configured sources, verifies SecretSpec installed
help before running its child helper, sanitizes failures, and privately
materializes a managed key outside any repository. SecretSpec resolution happens
in a child; only the allowlisted needed values return privately to the supervisor.
This reduces exposure; same-user agents are trusted, not OS-sandboxed.

`review.py` owns `GerritClient(context)` and `ReviewError`:
`verify_identity() -> dict`, `ensure_branch(branch) -> base_revision`,
`read_change(change_id, branch) -> dict`, `feedback(changes) -> list[dict]`,
`verify_revision(change_id, branch, revision)`, and
`post_review(change_id, branch, revision, message)`.
REST calls are authenticated, bounded, have no redirect credential forwarding,
and preserve context paths. Branch names are derived by `task_branch(title)`:
`agents/<ascii-slug>-<sha256(title)[:8]>`. Existing branches are never moved;
new branches use the immutable current base-branch revision, with race readback.
`chain_url(settings, branch)` is a project-and-branch Gerrit search URL.

`runtime.py` owns `Supervisor(settings, state_dir)`:
`snapshot() -> {tasks: [...], max_parallel: int}`, `start(title) -> task dict`,
`cancel(task_id) -> task dict`, `update_settings(settings)`, `close()`.
Public task objects contain `id`, `title`, `branch`, `state`, `workspace`,
`chain_url`, `changes`, `events` (bounded entries with `time`, `kind`, `text`),
`error`, `created_at`, `monitor` (coverage/deadline/last_poll/pending/gaps).
States: `queued`, `preflight`, `running`, `validating`, `uploading`,
`monitoring`, `completed`, `failed`, `cancelled`, `reconciliation_required`.
Settings are snapshotted at start; updates affect subsequent tasks only.

Each task has an isolated jj workspace. A global lock serializes shared jj
metadata mutations. One agent invocation produces at most one review unit and
exits; its process group must be quiescent before tests or uploads. The child
receives nonsecret `GERRIT_*` destination fields and a prompt containing the
workflow, task, verified destination, and prior feedback. It writes a JSON result
to `GERRIT_RESULT_FILE` outside the workspace:
`{"message": "component: Imperative summary", "tests": [["python", "-m", "unittest"]], "done": false}`.
For no changes, `{"done": true}` is sufficient. A configured agent is trusted to
edit its assigned workspace; result files and reviewer text still require parsing,
bounds, and scope validation. Test commands run as argv, never a shell.

Before feature agents run, validate jj capabilities, identity, managed-key
account binding, transport, destination and isolated disposable readiness probe.
The probe never becomes a feature ancestor. Each nonempty unit runs its focused
checks and exact immutable diff/message/selection review, then dry-run, upload,
authenticated readback, immediate chain event, and only then the next invocation.
Unknown write outcomes halt and require reconciliation, never blind retries.
Never submit, merge, raw-push as fallback, bypass validation, or abandon probes.
Record distinct jj change/local commit/Gerrit Change-Id/server revision identities.

Maintain a private, atomic, bounded nonsecret journal outside repositories.
Restart reports interrupted work; it never restores execution or authority.
Foreground `serve` owns monitoring; cancellation/shutdown records a coverage gap
and explicit handoff, not completion. Poll published feedback for every upload,
including probes. Minimum 12-hour deadline starts with first confirmed upload.
Untrusted feedback must not expand authority; unresolved feedback prevents
completion. Do not claim active feedback handling where only collection exists.

`server.py` owns `serve(settings_path, state_dir, port=8765)` and HTTP. Bind only
127.0.0.1; enforce exact Host/Origin, a per-launch CSRF token, bounded JSON bodies,
no CORS, static allowlist, no arbitrary file serving. `__init__.py`/`__main__.py`
own CLI parsing (`gerrit serve`, `gerrit check`, `gerrit init`).

## Dashboard API

GET `/api/state` returns supervisor snapshot plus `csrf_token` and
`settings` (nonsecret persisted values). GET `/api/settings` returns settings.
POST `/api/settings` accepts the flat settings object and returns it.
POST `/api/tasks` accepts `{"title": "..."}` and returns the new task.
POST `/api/tasks/<id>/cancel` returns the task. Mutations require
`X-CSRF-Token`. Errors are JSON `{"error": "safe message"}` with 4xx/5xx.
The frontend polls `/api/state` and renders all output as text, never HTML.

## Evidence path

Settings/credential owner: malformed input, permissions, provider failure,
allowlisted injection, no raw diagnostics, identity/branch race/redirect tests.
Runtime owner: fake executable/HTTP-backed lifecycle, immutable upload ordering,
probe separation, unknown outcome, cancellation, settings snapshots, journal
restart, deadline/feedback tests, and one real isolated jj workspace test.
Server owner: loopback, Host/Origin/CSRF, body bounds, static traversal and API tests.
Designer: actual opened browser screenshots at desktop/mobile where tooling exists.
Integrator: read decisive artifacts, then broad unittest discovery once reconciled,
compile/package checks, a local CLI/server smoke, and an oracle pass gate.
Fake-backed evidence does not establish real Gerrit, agent, or Secretspec compatibility.
