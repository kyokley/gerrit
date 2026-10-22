# Gerrit authentication setup

Use only after the user supplies the separately confirmed SSH host/port, automation username, REST base URL, protected HTTP-token path, project, branch, base, and acceptance constraints. Ask the user to confirm the expected automation account ID from Gerrit account settings or another trusted source. Keep the token file user-provided and protected. Do not ask for, print, echo, log, place in argv, store in browser storage, or open it in an editor. Verify REST identity by account ID from authenticated `/a/accounts/self`; browser session identity is not evidence.

## Fresh isolated SSH key

Generate one session-specific Ed25519 key in a private temporary directory under `/tmp/opencode`. Run this noninteractive shell snippet with a task-specific marker:

```sh
umask 077
session_dir=$(mktemp -d /tmp/opencode/jj-gerrit-XXXXXX)
chmod 700 "$session_dir"
ssh-keygen -t ed25519 -N '' -f "$session_dir/id_ed25519" -C 'jj-gerrit-task-session'
chmod 600 "$session_dir/id_ed25519"
python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text(), end="")' "$session_dir/id_ed25519.pub"
ssh-keygen -lf "$session_dir/id_ed25519.pub"
```

The snippet displays only the public key and its fingerprint; never display or transmit the private key. Preserve the session directory and key on resume; do not regenerate them.

Give the user these manual steps: sign in to the expected Gerrit account; open **Settings > SSH Keys**; paste the displayed public key under **Add Key**; select **Add Key**. Then ask, using the question tool, whether the key was added to the expected account. Do not continue until confirmed.

Verify the Gerrit SSH host fingerprint through an independent trusted channel. `ssh-keyscan` can collect a presented key but cannot establish trust by itself. Store the verified host key in an isolated `known_hosts` file with restrictive permissions. Do not modify global SSH configuration or known-hosts files.

Create an isolated SSH config with a dedicated alias and explicit `HostName`, `Port`, `User`, `IdentityFile`, `IdentitiesOnly yes`, `IdentityAgent none`, `UserKnownHostsFile`, `GlobalKnownHostsFile /dev/null`, `StrictHostKeyChecking yes`, and `BatchMode yes`. Reject newlines and unexpected syntax in supplied host/user/path values; never interpolate them into executable shell text. Set private file permissions. Test with the dedicated config and alias only:

```sh
ssh -T -F '<isolated-config>' '<alias>' gerrit version
```

Confirm the key was registered to the expected Gerrit account and that the configured remote uses the approved automation username, isolated SSH alias, and port. `gerrit version` confirms an SSH server response; it does not identify the REST account. Independently verify the expected REST account ID with `/a/accounts/self` and check that the SSH registration and REST identity correspond to the approved automation account. For jj 0.46.0, a scratch-repository mock-SSH test proved `GIT_SSH_COMMAND` reaches the native upload Git subprocess; no real Gerrit network call was made. Use the per-command invocation documented in the main skill. Reverify transport after jj/backend changes. Do not invent jj flags or use raw Git as fallback. If routing cannot be proven for the installed version, stop.

## REST token and safe requests

Require a protected token file path supplied by the user. Verify that it is a regular file with owner-only permissions, without reading its contents into tool output. Refuse insecure permissions; ask the user to correct them. Do not copy the secret to another location unless explicitly approved. Token lifetime or an approved renewal procedure must cover the requested actual monitoring period of at least 12 hours. Do not promise coverage if credentials expire sooner or renewal is unverified.

Use HTTPS with certificate validation and do not follow redirects with authorization headers. Do not put the token in command-line arguments, shell tracing, logs, chat, browser storage, or an editor. Prefer a narrowly scoped helper that reads the protected file directly and supplies the authorization header without displaying it; suppress request/response headers and redact sensitive error output. Never use insecure TLS options.

Use authenticated `GET <REST_BASE_URL>/a/accounts/self` to verify REST identity and record only the returned account ID and non-secret status. A browser login does not authenticate this request. If HTTP is used instead of HTTPS, proceed only for an explicitly approved loopback endpoint; reject non-loopback cleartext transport. Do not infer that a local host is loopback without checking the resolved endpoint.

Use the runner's `verify` command in [monitoring](monitoring.md) for the protected authenticated identity check before enrolling reviews. The helper's cleartext exception accepts only literal loopback IP addresses, not hostname aliases.

Do not revoke or rotate a user-owned token automatically. Remove only this task's own temporary config, known-hosts file, and key material after stopping the monitor and completing work, or after approved cancellation. Revoke this task's Gerrit public-key registration only when its ownership is established and the user approved cleanup.
