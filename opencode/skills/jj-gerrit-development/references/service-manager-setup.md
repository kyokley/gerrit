# `service-manager-modernization-spec` project profile

This is a project-scoped setup record, not a default Gerrit configuration or
standing authorization for another project or future session. Apply it only
when the requested repository is `service-manager-modernization-spec` and the
user's current request authorizes the relevant setup or network activity.
Treat observations below as historical evidence, not current connectivity or
credential guarantees. Re-inspect the repository, workspace state, installed
tools, configuration, account, host-key trust, and target before acting.

## Gerrit destination and remote names

- Project: `service-manager-modernization-spec`.
- HTTP API base URL observed: `http://phxtpmdev17.snphxprshared1.gbucdsint02phx.oraclevcn.com`
  (port 80, root context). HTTP Basic credentials are unencrypted in transit;
  warn before use. Prefer HTTPS only if a valid HTTPS endpoint is verified.
- SSH endpoint: alias `dev17`, port `29418`; the user's SSH configuration
  mapped it to `phxtpmdev17.snphxprshared1.gbucdsint02phx.oraclevcn.com`.
- Automation account: `automation`. Target branch: `modernization`.
- Observed server version: Gerrit `3.15.0-rc0`; recheck the deployed version.
- Existing `origin` URL observed and to preserve:
  `ssh://kyokley@dev17:29418/service-manager-modernization-spec`.
- A separate shared Git remote named `automation` was added with URL
  `ssh://automation@dev17:29418/service-manager-modernization-spec`. Inspect
  current remotes before adding it. Never overwrite `origin` or a same-name
  remote conflict. Git remotes are shared across Jujutsu workspaces, not
  workspace-specific.
  After confirming the name is unused, add it with
  `jj --no-pager git remote add automation ssh://automation@dev17:29418/service-manager-modernization-spec`.

## Workspace setup and safe revision targeting

Observed paths were source checkout
`/home/yokley/workspace/service-manager-modernization-spec` and agent checkout
`/home/yokley/workspace/service-manager-modernization-spec-agent`. The latter
was a non-colocated Jujutsu workspace named `agent`. Those paths, workspace
name, revisions, and IDs are historical: inspect current workspace lists,
destination paths, source status, and revision graph before use. The agent
workspace was initially based on `modernization@origin`; it was later
explicitly switched to `tt` with `jj edit`. At that observation, `tt` resolved
to change-ID prefix `ttvyyvonnykx` and commit-ID prefix `ce7fa2cddb0f`. Always resolve
revsets and IDs again; never assume this historical target is current.

- Inspect existing workspace registrations and destination directories before
  creating anything. Choose the intended base explicitly. Historical `jj`
  version was `0.46.0`; verify installed help and version.
- For a new workspace, the installed-help-supported add form is
  `jj --no-pager workspace add --no-colocate --name agent -r "$base" "$agent_dir"`.
  Do not pass `--ignore-working-copy`: an earlier attempt registered the
  workspace and then failed to update its working copy. If setup fails after
  partial registration, inspect state and recover deliberately; do not blindly
  rerun the add command.
- When a workspace is stale, inspect its state and use
  `jj --no-pager workspace update-stale` before the requested `jj edit`.
  Preserve concurrent user changes. Do not reset, revert, or expose secret
  diffs. Do not force creation of a child revision when the user explicitly
  requested editing a particular revision; instead verify the target and do
  not share the current revision with another workspace.
- Commands that create or move a working copy must be allowed to update it.
  Reserve `--ignore-working-copy` here for read-only diagnostics; never use it
  to bypass a working-copy update required by workspace setup or checkout.
- From the agent workspace, the historical workspace-scoped configuration
  commands were:

  ```sh
  jj --no-pager config set --workspace gerrit.default-remote automation
  jj --no-pager config set --workspace gerrit.default-remote-branch modernization
  jj --no-pager config set --workspace git.executable-path /home/yokley/workspace/service-manager-modernization-spec-agent/.jj/automation/git-wrapper
  ```

  Installed `jj` help confirmed `git.executable-path` support and that Git
  remote operations spawn Git. Verify current support and exact syntax before
  changing configuration. These settings were scoped to the agent workspace;
  source-workspace defaults were not changed. The Git remote itself is shared.

## Scoped credential and transport provider

Historical Secretspec version was `0.21.1`; the manifest used profile
`default` and provider `dotenv`. Declared values included `GERRIT_TOKEN` for
HTTP, `GERRIT_AUTOMATION_USERNAME` defaulting to `automation`, and
`GERRIT_AUTOMATION_SSH_KEY` containing private-key material. `GERRIT_HOST`
defaulted to `http://localhost:8080`, which differs from the observed Gerrit URL; do not
use that default as the destination without explicit verification. Inspect the
current manifest and installed Secretspec help. Provider runs in this setup
required a nonsecret `--reason`; pass one and do not disable provider policy.

The HTTP helper reads the injected token in the child process. Do not export it
to a file or copy `.env` into another workspace. The SSH helper treats
`GERRIT_AUTOMATION_SSH_KEY` as private-key contents, not a pathname. Use it only
when the user explicitly designates this existing managed key. In the child
helper, materialize it temporarily outside the repository in a mode-`0700`
directory and mode-`0600` key file, then clean it up on success, failure, or
launch error. Never include secret values in arguments, diagnostics, logs,
traces, environment dumps, notes, checkpoints, or plaintext exports. Do not
fallback to another provider or key after a failure.

Observed local helper design (not a built-in `jj` setting):

- `.jj/automation/git-wrapper` and `.jj/automation/ssh-wrapper` were mode
  `0700`; workspace `known_hosts` was mode `0600` in a mode-`0700` directory.
- The Git wrapper delegated to the real Git executable with unchanged
  arguments, set `GIT_SSH_COMMAND` to the absolute SSH-helper path and
  `GIT_SSH_VARIANT=ssh`, and removed `GIT_SSH`.
- The SSH helper invoked Secretspec from the source checkout, with explicit
  `-f "$source/secretspec.toml" -P default -p dotenv --reason "Gerrit automation SSH transport"`.
  It used an isolated temporary key and
  OpenSSH with `-F /dev/null`, `IdentitiesOnly=yes`, `IdentityAgent=none`,
  `BatchMode=yes`, `StrictHostKeyChecking=yes`, the workspace-pinned
  `UserKnownHostsFile`, `GlobalKnownHostsFile=/dev/null`, verified FQDN,
  `ConnectTimeout=15`, and `ConnectionAttempts=1`. It stripped token/key
  variables from the final SSH process. These are historical wrapper facts,
  not universal `jj` flags; inspect installed paths and wrapper contents
  without revealing secrets. If copying or moving a workspace, verify and
  update absolute executable paths deliberately. Keep the source checkout
  available to helpers that load its Secretspec files. Do not invent a `jj`
  SSH setting or assume an upload backend honors an unverified option.
- The host alias `dev17` mapped to the FQDN while preserving port-aware
  `known_hosts` lookup. Do not configure `HostKeyAlias` as the bare FQDN when
  the pin is stored as `[FQDN]:29418`; those lookup names differ.
- A local `~/.ssh/known_hosts` entry historically supplied an ED25519 pin with
  fingerprint `SHA256:Rx+yjMTHm1CdxBOvZzyVgiZYD3LjhfBzCxD9m+MKW3U` for
  `[phxtpmdev17.snphxprshared1.gbucdsint02phx.oraclevcn.com]:29418`. This
  historical fingerprint is not independent trust proof for a future session.
  Re-verify through an independent trusted channel; never trust blind
  `ssh-keyscan` output. Keep only the verified pin in workspace/session
  `known_hosts` and enforce strict host checking.

## Historical checks and unresolved status

These checks were observed on `2026-11-06`; they do not authorize current
activity or prove current availability:

- At `2026-11-06T17:03:53Z`, HTTP checks returned account `automation`
  (account ID `1000002`), the expected project and
  `refs/heads/modernization`; the account detail and own SSH-key endpoints
  returned HTTP 200, with one registered SSH key.
- An earlier HTTP 401 was resolved after the user replaced the token. Do not
  retry an old or rejected credential. A later key-binding check over HTTP
  timed out, so the managed private key was not verified against the account's
  registered public key.
- `jj git fetch --remote automation -b modernization` later failed with SSH
  exit 255. The diagnostic showed banner-exchange timeout before server
  banner, host-key exchange, or user authentication. This neither proves a
  bad key nor establishes account permissions. TCP connection alone is not
  authentication evidence.
- Offline wrapper checks for syntax, modes, environment isolation, strict
  options, temporary-file cleanup on success/failure, and launch-error cleanup
  passed. Actual SSH authentication, fetch, and upload readiness remain
  unverified. No upload probe, upload, or monitoring was started.

Re-run identity and connectivity checks only under an explicit current request
and scope. Fail closed on provider errors, HTTP 401/403, SSH authentication or
host-key uncertainty, or a transport that cannot be bound to the selected
identity. Keep upload probe, write, and foreground-monitoring requirements in
the main skill and security/monitoring references unchanged.
