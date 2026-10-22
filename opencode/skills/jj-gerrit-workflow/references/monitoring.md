# Read-only Gerrit feedback monitoring

The helper stores published Gerrit messages and inline comments for explicitly
enrolled changes. It is a durable inbox, not an automation agent: it does not
write to Gerrit, edit source, execute feedback, or promise reactions while an
AI session is offline. Events remain pending until explicitly acknowledged.
They can be inspected and handled manually in a resumed session; there are no
automatic replies.

## Requirements and protected files

Use Python 3 and a dedicated session directory outside the repository. The
session directory must be current-user-owned with mode `0700`. Config, token,
database, lock, and SQLite sidecars must be current-user-owned regular files
with mode `0600` or stricter. Protected file reads reject symlinks. Keep the
token out of argv, shell history, logs, and service files. Use HTTPS. HTTP is
allowed only for literal loopback IPs (`127.0.0.0/8` or `::1`) with the explicit
`--allow-http-loopback` flag. Hostname `localhost` is rejected. Environment
proxy settings are disabled for all requests.

Create a fresh, unique session directory under a persistent state parent. Do
not chmod or overwrite an existing session directory or config. `TOKEN_FILE`
must name an existing owner-only protected token file supplied by the user;
this setup does not prompt for, generate, or read the token.

```sh
set -eu
STATE_PARENT="${XDG_STATE_HOME:-$HOME/.local/state}"
mkdir -p "$STATE_PARENT"
SESSION="$(mktemp -d "$STATE_PARENT/gerrit-feedback.XXXXXXXX")"
chmod 700 "$SESSION"
printf 'New session directory: %s\n' "$SESSION"
TOKEN_FILE="/absolute/path/to/user-supplied/protected-token"
export TOKEN_FILE
python3 - "$SESSION/config.json" <<'PY'
import json, os, sys
config = {
    "rest_base_url": "https://gerrit.example/",
    "username": "review-bot",
    "expected_account_id": 1000001,
    "project": "team/project",
    "branch": "main",
    "token_file": os.environ["TOKEN_FILE"],
}
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as config_file:
    json.dump(config, config_file, indent=2)
    config_file.write("\n")
PY
```

Replace the non-secret Gerrit scope values and absolute token path before
running the config creation snippet. The JSON object must contain exactly the
six shown fields. `expected_account_id` must be a positive integer. The
username must not contain a colon or control characters. The REST URL may have
a Gerrit context path but no credentials, query, fragment, or control
characters. Obtain the expected account ID from Gerrit account settings or
another trusted source; do not guess it or discover it by silently adopting an
identity returned by authentication. Before enrolling changes, verify the
configured username and ID using the explicit config path:

```sh
python3 /absolute/path/to/jj-gerrit-workflow/scripts/gerrit_poller.py \
  --config "$SESSION/config.json" verify
```

`verify` performs only `GET /a/accounts/self`, opens no database, and emits
only the verified account ID and username. It checks that the returned ID is
an actual integer equal to the expected ID and that the returned username
matches. `/a/accounts/self` is also checked on every poll. HTTP errors expose
only a sanitized status, never response bodies or credentials. Invalid token
UTF-8 is reported safely.

## Enrollment and commands

Set `SESSION` to the exact fresh path printed during setup. Run from the skill
directory, or set `SCRIPT` to its absolute path. Enrollment accepts explicit
positive numeric Gerrit change numbers only. The project and branch are fixed
by the database's immutable config binding.

```sh
SCRIPT="/absolute/path/to/jj-gerrit-workflow/scripts/gerrit_poller.py"
SESSION="/absolute/path/to/gerrit-feedback.ABCDEFGH"
python3 "$SCRIPT" --config "$SESSION/config.json" --db "$SESSION/events.sqlite3" \
  enroll 12345 12346
python3 "$SCRIPT" --config "$SESSION/config.json" --db "$SESSION/events.sqlite3" \
  --interval 300 poll --once
python3 "$SCRIPT" --config "$SESSION/config.json" --db "$SESSION/events.sqlite3" status
python3 "$SCRIPT" --config "$SESSION/config.json" --db "$SESSION/events.sqlite3" events
python3 "$SCRIPT" --config "$SESSION/config.json" --db "$SESSION/events.sqlite3" ack 1
```

`poll --once` performs one complete poll. Process restarts receive no coverage
credit. `poll` without `--once` repeats at the configured interval and exits
successfully once every enrolled change has 43,200 credited seconds. Pending
events remain visible after coverage completes. If additional changes are
enrolled after the poller exits at completion, explicitly restart the already
approved service or start a new approved poll run to monitor them.

`status` reports per-change coverage, required coverage, completion, pending
event count, last successful poll time, and sanitized last error. `events`
returns pending events as JSON. `ack EVENT_ID` is an explicit irreversible
local acknowledgment. Acknowledge only after the consumer durably accepts the
event. Delivery is at-least-once; no command auto-acknowledges.

Coverage starts at zero per enrolled change. It credits only monotonic process
time bracketed by two complete successful polls for that same change. A newly
enrolled change has no baseline until its first complete observation, so it
cannot receive earlier coverage. A complete poll requires identity verification
and valid details plus comments for every enrolled change. Failures reset all
process-local baselines; failures, downtime, malformed/truncated responses,
and restarts are not credited. Gaps greater than twice `--interval` are not
credited. This measures polling coverage only, not human or AI review/action.

The database stores events, content fingerprints, enrollment, coverage,
acknowledgment, last-success time, sanitized errors, and the config binding.
Use a new unique session/database for another host, account, project, or branch.
Gerrit lookups use `/a/changes/{numeric-change-number}/...`; each detail
response must return that exact `_number` and the configured project/branch.
The comments endpoint returns published comments across revisions. Inline
events retain comment ID, `patch_set`, `in_reply_to`, `unresolved`, `updated`,
and available location metadata (`path`, `side`, `line`, `range`, `parent`).
Message events retain message ID and `_revision_number`. Fingerprints cover the
complete emitted event state, so resolution-only or location/state changes
create a new event while unchanged polls deduplicate.

## Optional systemd user service

Do not install, enable, start, stop, or otherwise alter a service without the
user's explicit approval. The following example uses the unique unit name
`jj-gerrit-feedback-review.service`; replace all absolute paths with verified
paths. Install the script outside the repository at
`$HOME/.local/lib/jj-gerrit-feedback-v1/scripts/gerrit_poller.py`. Choose the
exact session path printed above. Do not overwrite an existing installed file
or unit; choose a new versioned install path or unit name if either exists.

Unit file path: `$HOME/.config/systemd/user/jj-gerrit-feedback-review.service`.
After choosing an absolute `SESSION_DIR`, install the script at the versioned
path and create the unit with exclusive-create semantics. These steps do not
enable or start it; obtain approval before installation too.

```sh
set -eu
INSTALL_DIR="$HOME/.local/lib/jj-gerrit-feedback-v1/scripts"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/jj-gerrit-feedback-review.service"
mkdir -p "$INSTALL_DIR" "$UNIT_DIR"
test ! -e "$INSTALL_DIR/gerrit_poller.py"
install -m 0644 "$SCRIPT" "$INSTALL_DIR/gerrit_poller.py"
export SESSION_DIR="/absolute/path/to/unique/gerrit-feedback-session"
python3 - "$UNIT_FILE" <<'PY'
import os, sys
session = os.environ["SESSION_DIR"]
unit = f"""[Unit]
Description=Read-only Gerrit feedback poller for review scope

[Service]
Type=simple
WorkingDirectory={session}
ExecStart=/usr/bin/python3 %h/.local/lib/jj-gerrit-feedback-v1/scripts/gerrit_poller.py --config {session}/config.json --db {session}/events.sqlite3 --interval 300 poll
Restart=on-failure
RestartSec=30
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, "w", encoding="utf-8") as unit_file:
    unit_file.write(unit)
PY
```

Review the unit's contents and paths before approving enable/start. Its unit:
Do not add `PrivateTmp=true`: it would hide approved token files under `/tmp`.

```ini
[Unit]
Description=Read-only Gerrit feedback poller for review scope

[Service]
Type=simple
WorkingDirectory=/absolute/path/to/unique/gerrit-feedback-session
ExecStart=/usr/bin/python3 %h/.local/lib/jj-gerrit-feedback-v1/scripts/gerrit_poller.py --config /absolute/path/to/unique/gerrit-feedback-session/config.json --db /absolute/path/to/unique/gerrit-feedback-session/events.sqlite3 --interval 300 poll
Restart=on-failure
RestartSec=30
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
```

After approved installation of the script and unit, these are the manual
commands to enable/run and inspect it. Do not execute them without approval:

```sh
systemctl --user daemon-reload
systemctl --user enable --now jj-gerrit-feedback-review.service
systemctl --user status jj-gerrit-feedback-review.service
journalctl --user -u jj-gerrit-feedback-review.service
systemctl --user stop jj-gerrit-feedback-review.service
systemctl --user disable jj-gerrit-feedback-review.service
```

Check whether the user manager persists across logout with
`loginctl show-user "$USER" -p Linger`. A user service can stop at logout unless
lingering is enabled. Enabling lingering changes account behavior and requires
separate explicit approval; without a persistent user manager, polling does
not continue while logged out. `Restart=on-failure` retries authentication or
network failures but does not credit failed polls. The service exits normally
when coverage completes; after enrolling additional changes, restart that
approved service explicitly. Polling only collects events; a later resumed
session must inspect and handle feedback manually.

## Scope and limits

The helper uses read-only authenticated GET requests for `/a/accounts/self`,
explicit numeric change details/messages, and published comments across all
revisions. It does not use global search or list endpoints. Gerrit `ReviewInput`
POST endpoints are not used. Redirects are rejected, HTTPS uses standard
certificate verification, timeouts are bounded, and raw HTTP error payloads
are never displayed. A non-blocking file lock prevents concurrent pollers for
one database. Status, enrollment, event inspection, and acknowledgment do not
take that runner lock.

The helper does not comment, vote, submit, merge, execute shell feedback, alter
a checkout, monitor unenrolled changes, or cause an inactive AI session to
act. Protect database backups as carefully as the original because review
payloads may be confidential.
