# Docker and Gerrit API recipe

Operational command reference for `jj-ephemeral-gerrit-review`; placeholders are not instructions to execute. For the dependency-ordered first-run route, one authorization checklist, and detailed browser setup, use [streamlined setup](streamlined-setup.md). For macOS Docker Desktop and native headed Brave/CDP caveats, use [macOS setup notes](macos-setup.md). The entrypoint facts were inspected in Docker Gerrit v3.14.4 Ubuntu 24 source; verify the exact image at runtime. Resolve its immutable digest/platform and inspect entrypoint, paths, tools, Gerrit version, API schema, and installed jj options. A tag alone does not guarantee registry contents.

## Installed jj remote compatibility

Check `jj gerrit upload --help`, `jj git remote add --help`, and `jj git remote list` before selecting `--remote`. Installed behavior differs by jj version and the help text alone may not prove runtime URL support. With jj 0.46.0, `--remote` resolves a configured remote name; in one jj 0.45.1 session, help described both names and full SSH URLs, but a dry-run with the exact URL failed with `remote ... does not exist`. Test the approved exact form with dry-run first. Never silently add a remote or alter an existing remote to work around this.

If the installed version requires a name, request explicit approval for one unique name and its exact verified Gerrit SSH URL. Verify the name is absent and record existing remotes, including `origin`, before using the supported command:

```sh
jj git remote add '<unique-gerrit-remote-name>' '<verified-gerrit-ssh-url>'
```

Verify the exact new mapping and unchanged existing remotes afterward. Retain the remote unless the user separately authorizes its removal. This is a repository-config write, not an implicit part of setup.

## Isolated Compose setup

Create a new absolute temp directory outside the repository. Substitute every `<...>` placeholder below with a reviewed value; do not copy placeholders literally. Resolve `GERRIT_IMAGE` to a verified image digest (for example, `gerritcodereview/gerrit@sha256:<verified-digest>`), not an invented version or `latest`. Use a unique Compose project name. Compose prefixes the declared volume names with that project name; do not add fixed `name:` values or reuse another project. Verify the pinned image's volume paths and default entrypoint. Never mount one volume over all of `/var/gerrit`, which can hide the image's Gerrit WAR.

Complete Compose YAML template:

```yaml
services:
  gerrit:
    image: ${GERRIT_IMAGE:?set to verified image digest}
    ports:
      - "127.0.0.1:${GERRIT_HTTP_PORT:?choose free HTTP port}:8080"
      - "127.0.0.1:${GERRIT_SSH_PORT:?choose free SSH port}:29418"
    environment:
      CANONICAL_WEB_URL: "${CANONICAL_WEB_URL:?set loopback URL with trailing slash}"
    volumes:
      - gerrit_etc:/var/gerrit/etc
      - gerrit_git:/var/gerrit/git
      - gerrit_db:/var/gerrit/db
      - gerrit_index:/var/gerrit/index
      - gerrit_cache:/var/gerrit/cache
      # Optional: retain only if the pinned image writes logs to this path.
      - gerrit_logs:/var/gerrit/logs

volumes:
  gerrit_etc: {}
  gerrit_git: {}
  gerrit_db: {}
  gerrit_index: {}
  gerrit_cache: {}
  gerrit_logs: {}
```

Verify those mount paths against the selected image release before using this template. Select/check free loopback ports; the check is racy, so if Docker reports a conflict, stop and choose/check new ports. Set `CANONICAL_WEB_URL=http://127.0.0.1:<HTTP-port>/`. Do not add credentials to this file. Save it with restricted permissions. For every Compose operation, use both `-p '<unique-project>'` and `-f '<absolute-compose-file>'`.

Export the substituted values in the shell that runs Compose. These are placeholders, not actual values:

```sh
export GERRIT_IMAGE='gerritcodereview/gerrit@sha256:<verified-digest>'
export GERRIT_HTTP_PORT='<chosen-free-http-port>'
export GERRIT_SSH_PORT='<chosen-free-ssh-port>'
export CANONICAL_WEB_URL="http://127.0.0.1:${GERRIT_HTTP_PORT}/"
```

The v3.14.4 Ubuntu 24 Dockerfile declares `etc`, `git`, `index`, `cache`, and `db`; the separate `logs` volume is optional, not image-declared. Verify image paths/configuration before use. Sources: [entrypoint](https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/entrypoint.sh), [Dockerfile](https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/Dockerfile).

Before volume creation, inspect the exact pulled image without running its normal entrypoint: verify platform, entrypoint contents, Bash/Git, WAR, config and volume/log paths. A disposable diagnostic must use read-only root plus explicit tmpfs mounts for every image-declared volume path; do not let Docker create anonymous volumes. Stop on divergence. Then verify the Compose config and that every exact named volume is absent. In verified v3.14.4 Ubuntu 24 source, the entrypoint runs init if All-Projects is absent or when passed the `init` argument; init can upgrade state. Run it once only against brand-new, empty volumes. Never rerun it on resume. Runtime init may create an initial account record; record its receipt, then verify exact identity and Administrators membership in the UI before any privileged reuse. Reuse only when the user explicitly authorized that account conditionally. Do not infer admin rights from `admin`, account order, or an init-created record. Stop on any other unexpected account; the first-account-admin flow applies only to a verified truly empty account state. The config-only run overrides the normal entrypoint and shares named volumes, so the auth setting persists before daemon startup.

```sh
docker compose -p '<unique-project>' -f '<absolute-compose-file>' config --quiet

# Verified brand-new empty volumes only; use the image's verified default entrypoint.
docker compose -p '<unique-project>' -f '<absolute-compose-file>' run --rm gerrit init

# Use only after verifying /bin/bash and git exist in this pinned image.
docker compose -p '<unique-project>' -f '<absolute-compose-file>' run --rm --entrypoint /bin/bash gerrit -c 'git config --file /var/gerrit/etc/gerrit.config auth.type DEVELOPMENT_BECOME_ANY_ACCOUNT && git config --file /var/gerrit/etc/gerrit.config --get auth.type'

# Start only after the preceding command prints DEVELOPMENT_BECOME_ANY_ACCOUNT.
docker compose -p '<unique-project>' -f '<absolute-compose-file>' up -d

# Verify the persisted setting after startup.
docker compose -p '<unique-project>' -f '<absolute-compose-file>' exec -T gerrit /bin/bash -lc 'git config --file /var/gerrit/etc/gerrit.config --get auth.type'

# Confirm published bindings; output must show loopback host addresses.
docker compose -p '<unique-project>' -f '<absolute-compose-file>' port gerrit 8080
docker compose -p '<unique-project>' -f '<absolute-compose-file>' port gerrit 29418

# Check HTTP readiness; expect exactly HTTP 200 from the verified URL.
test "$(curl --max-time 5 --output /dev/null --write-out '%{http_code}' 'http://127.0.0.1:<HTTP-port>/config/server/version')" = 200

# After registering the dedicated key and verifying known_hosts, check SSH auth.
ssh -p '<SSH-port>' -i '<restricted-private-key-path>' -o UserKnownHostsFile='<per-instance-known-hosts-path>' -o StrictHostKeyChecking=yes '<verified-ssh-username>'@127.0.0.1 gerrit version
```

Do not assume the image has Bash/Git or that a documented config path applies to another tag. Stop if verification fails. A remote Docker daemon does not make its loopback ports local to the browser; require an explicitly approved secure tunnel instead of changing bind addresses or Docker context. Record digest, versions, Compose project/file, volume names, ports, URL, config verification, and readiness evidence. Never report a check that was not performed.

## Browser accounts, SSH keys, and HTTP credentials

The consolidated authorization checklist, one-browser-owner flow, rendered account setup, SSH-key verification, and token UI/capture procedure live in [streamlined setup](streamlined-setup.md#2-consolidate-authorization-once-early). Use that reference rather than duplicating browser steps here.

Keep admin, `review-bot`, and `user` identities separate. Match an init-created account to its verified receipt, then verify admin identity and Administrators membership in the rendered UI before privileged reuse; account name/order is not proof. Create/reuse each account, register the bot key, and generate HTTP credentials only under explicit account-specific approval. A review-instance approval alone does not authorize account creation or sign-in.

HTTP tokens are for authenticated REST only; SSH upload uses the bot's dedicated key. Never use the admin token as the bot credential, touch/revoke a pre-existing token such as `initialToken`, or copy browser cookies into REST clients. Prefer a finite approved lifetime. If admin and bot tokens are both required, generate them close to their uses: the admin token must cover bootstrap and temporary-grant removal; the bot token must cover authorized polling. On expired/401 credentials, stop and ask; do not silently extend or regenerate.

Capture a one-time token only from the verified account's rendered token modal, directly into process memory, then write it once to a new mode-`0600` file inside a mode-`0700` directory outside the repository. Never put secret values in commands, environment, output, logs, screenshots, source, or checkpoint. Record only label, finite expiry, secure path, and separate UI/REST validation results. A failed or uncertain capture is a stop condition, not permission to retry generation.

## Group visibility, permission checks, and access updates

Groups are Gerrit-server resources, not project-local ACL entries. Create a unique group only after checking for a name collision and receiving explicit authorization. Gerrit v3.14.4 `GroupInput` supports an `owner_id`, explicit `members`, and `visible_to_all:false`; verify the live owner UUID, direct member IDs, and included groups. Some `GroupInfo` responses omit a false visibility option, so do not infer public visibility from an omitted field or treat an unauthenticated 403 alone as complete proof of privacy. Do not leave the creator unexpectedly in a bot-only or reviewer-only group.

Gerrit v3.14.4 `CheckAccess` checks reference permissions such as `read`, `push`, `create`, `forgeAuthor`, and `forgeCommitter`; it does not accept `label-Code-Review` as a ref-permission check and returns HTTP 400 for that label permission. For label access, inspect the project's local/inherited access rules and the target account's groups. Record label min/max, `exclusive`, and `BLOCK` rules as configuration evidence; do not cast a vote to test them. Permission inheritance varies by server. Check each account/ref/permission pair in a separate bounded request, then add only missing, narrow project-local rules. Never modify `All-Projects`, global capabilities, owner permissions, or identities to work around a failed check.

`POST /a/projects/{project}/access` add/remove diffs use an `AccessInput` wrapper whose ref section contains a `permissions` map and whose permission contains a `rules` map. Correct minimal add shape:

```json
{
  "add": {
    "refs/heads/<branch>": {
      "permissions": {
        "forgeCommitter": {
          "rules": {
            "<bot-group-uuid>": { "action": "ALLOW" }
          }
        }
      }
    }
  }
}
```

For temporary seed grants, use the same nesting under `remove`, with only the bot group's exact `create` and direct `push` rules on the target ref:

```json
{
  "remove": {
    "refs/heads/<branch>": {
      "permissions": {
        "create": { "rules": { "<bot-group-uuid>": { "action": "ALLOW" } } },
        "push": { "rules": { "<bot-group-uuid>": { "action": "ALLOW" } } }
      }
    }
  }
}
```

Structurally validate every `add` and `remove` payload before a write: exact operation, exact ref, `permissions` object, each permission's `rules` object, and only the authorized group UUID. A malformed unwrapped permission map caused Gerrit 3.14.4 `SetAccess` to fail with HTTP 500 (`permissions` was null). A failed POST is not proof that state is unchanged; re-read project access before any retry.

## Fresh project and base bootstrap

URL-encode the project component and authenticate with the verified admin. Gerrit v3.14.4 `ProjectInput` supports `PUT /a/projects/{project-name}` with `create_empty_commit:false` and `branches:["<branch>"]`; verify the deployed version's schema. The `branches` input is not proof the ref exists: after creation, read the branch list and verify the exact target ref is absent before seeding. Confirm the project is new and empty. Inspect inherited/local access plus effective `check.access` results for the bot/reviewer before adding rules. In one observed setup, inherited rules supplied read, review-push, and forgeAuthor while forgeCommitter was missing; reviewer Code-Review `-1..+1` was inherited through Registered Users. This varies by server config. Add only missing, narrow project-local permissions. Never grant global/admin/project-owner access, alter All-Projects, or rewrite author/committer identities.

Before base seeding, verify the project is newly created, the target branch does not exist, and the exact base SHA is present in the intended local Git backing repository. Gerrit v3.14.4 `CreateRefControl` source requires both Create Reference and direct Push (`UPDATE`) for an unreachable commit. This session granted both before seeding; it did not attempt a Create-only push. Grant both only to the authorized bot-only project group on the exact `refs/heads/<branch>` after separate explicit approval. Seed the base and ancestors only, never review commits:

```sh
GIT_SSH_COMMAND='ssh -i <per-instance-private-key> -o UserKnownHostsFile=<per-instance-known-hosts> -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2' git -C '<verified-local-git-repo>' push '<dedicated-gerrit-ssh-url>' '<base-sha>:refs/heads/<branch>'
```

Use the direct Git push above only for this base-seed exception. Bind the command to the per-instance key with per-command `GIT_SSH_COMMAND` options (`IdentitiesOnly=yes`, the per-instance `UserKnownHostsFile`, `StrictHostKeyChecking=yes`, and bounded connection/keepalive values). Do not put secret values in arguments/environment and do not change global SSH config. Verify target branch head equals the exact base SHA. Remove exactly both temporary rules in a `finally` cleanup on success or failure, then verify the local rules are absent and the bot is denied Create/direct Push; preserve inherited ACLs. If permission cleanup cannot be verified, stop and report it incomplete. If project/branch state differs, never overwrite or reuse it. Review commits use native jj only.

## Native jj upload

Check installed `jj gerrit upload --help` and resolve the exact closure `mutable()::(<explicit-revset>)`; verify every included commit. With jj 0.46.0, `--remote` requires a configured remote name, so a URL can fail as nonexistent. Do not silently add config: use the approved unique remote procedure above, preserving `origin`. jj 0.46 invokes Git with inherited environment; set `GIT_SSH_COMMAND` on the single command, using the dedicated key, `IdentitiesOnly=yes`, strict per-instance known_hosts, and bounded connect options. A dry-run may not contact Gerrit. Verify project/branch/base and permissions separately. Then run:

```sh
GIT_SSH_COMMAND='ssh -i <per-instance-private-key> -o UserKnownHostsFile=<per-instance-known-hosts> -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2' jj gerrit upload -r '<explicit-revset>' --remote '<verified-gerrit-remote-name>' --remote-branch '<branch>' --dry-run
```

Get fresh explicit approval after showing exact closure, destination, branch, base, and dry-run. Repeat the exact command without `--dry-run` only after approval. Do not use submit, skip-validation, or unrequested push options. Upload creates/updates review changes; it does not submit or merge them. Verify exactly the expected changes, current patch sets, uploaded SHAs, URLs, authors/committers, and that branch head remains base. Fetch details/messages/labels and published comments for those exact change IDs. Native upload derives missing Change-Ids from stable jj change IDs for uploaded commits only; it does not add trailers locally, so uploaded SHAs can differ. Map stable jj ID, local SHA, Gerrit Change-Id/number, uploaded SHA, patch set, and URL. Never describe review upload as submitted/merged.

## REST detail, comments, and safe replies

Use only authenticated Gerrit REST APIs documented for the deployed version. Encode path components correctly. Join `/a/` with relative `changes/...` paths or `/a` with `/changes/...`; do not accidentally produce `/a/a/`. Before polling, validate a bounded authenticated request for one exact tracked change and verify the response shape. Gerrit JSON responses commonly begin with the XSSI prefix `)]}'`; remove that prefix before parsing JSON. Query exact tracked change IDs, not arbitrary project/user data.

Fetch detail and published comments separately. For each encoded change ID, use the documented detail options and verify their response shape against the deployed release:

```text
GET /a/changes/{encoded-change-id}/detail?o=ALL_REVISIONS&o=MESSAGES&o=LABELS&o=DETAILED_LABELS&o=DETAILED_ACCOUNTS
GET /a/changes/{encoded-change-id}/comments
```

The root/coordinator runs at most one foreground polling session: 15-second interval and 5-minute total monotonic budget. Do not run it in a child, daemon, or background process. Before every request, compute remaining monotonic budget; stop starting operations when `remaining <= 0`, and set each request timeout to `min(5 seconds, remaining budget)`. Cap backoff and sleep by the same remaining budget. On 401/expired token, stop without generating or extending credentials. Preserve the last full snapshot and write a separate terminal timeout/error receipt.

Track exact message IDs, published comment IDs, and detailed vote identities/values across all patch sets; timestamps alone are insufficient. Retain each comment's source revision, path, line/range, author, ID, message, and unresolved status. Record full snapshots before the terminal receipt. Do not silently baseline existing human feedback on the first run. Ignore only the automation account's own messages; stop and return reviewer-account or unexpected-author feedback to root for judgment. Do not treat draft comments as published feedback; retrieve bot drafts separately only when needed. Absence of feedback, silence, and timeouts never constitute approval.

For a reply, use the authenticated revision review endpoint. The revision must be the original source SHA of the thread, including for a comment on an older patch set. Re-read change and thread immediately before writing. Example JSON body (substitute values; this is not a command or approval to send):

```json
{
  "notify": "NONE",
  "comments": {
    "src/file.c": [
      {
        "in_reply_to": "<comment-id>",
        "message": "<reply text>",
        "unresolved": true
      }
    ]
  }
}
```

Send as `POST /a/changes/{encoded-change-id}/revisions/{source-sha}/review` with JSON content type. This body has no `id`: for a new reply, `in_reply_to` identifies the existing parent comment; `id` identifies a draft comment to update. Confirm the precise schema against the deployed Gerrit release. Do not add labels, vote, or resolve a thread without explicit instruction. Do not reply against a guessed current revision.

After the write, re-fetch and confirm the reply, then persist returned IDs before polling again. On timeout/uncertain POST outcome, re-read the thread and ledger; do not blindly retry. After an upload, verify the intended Gerrit change now has the expected current patch set, and re-fetch feedback to detect comments arriving during the update. Comment IDs/message IDs are dedupe keys; timestamps alone are not.

## Checkpoint and cleanup

The checkpoint may contain non-secret operational metadata: unique project and Compose names, absolute Compose path, named volumes, image digest, tool versions, loopback endpoints, base SHA, branch, original revset, stable jj IDs, Gerrit mappings, patch-set state, comment/message cursors, and action receipts. Never store tokens, passwords, cookies, SSH private keys, or secret values. Protect checkpoint and key files. Resume only if live containers, volumes, project, branch, and base agree with it. Never reinitialize on resume.

Do not clean up on completion, timeout, rejection, or explicit review approval. Retain state until the user explicitly requests deletion. Before cleanup, enumerate and confirm the exact Compose project, file, containers, volumes, checkpoint, and temporary credentials. Delete only verified per-review resources after confirmation. Never run global prune or act on ambiguous resource names.

## Authoritative documentation

- Docker Gerrit image: https://hub.docker.com/r/gerritcodereview/gerrit
- Jujutsu Gerrit: https://docs.jj-vcs.dev/latest/gerrit/
- Gerrit REST API: https://gerrit-review.googlesource.com/Documentation/rest-api.html
- Gerrit changes API: https://gerrit-review.googlesource.com/Documentation/rest-api-changes.html
- Gerrit projects API: https://gerrit-review.googlesource.com/Documentation/rest-api-projects.html
- Gerrit accounts API: https://gerrit-review.googlesource.com/Documentation/rest-api-accounts.html
- Gerrit Change-Id: https://gerrit-review.googlesource.com/Documentation/user-changeid.html
- Gerrit config: https://gerrit-review.googlesource.com/Documentation/config-gerrit.html
- Gerrit init: https://gerrit-review.googlesource.com/Documentation/pgm-init.html
- Gerrit v3.14.4 [`ProjectInput.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/extensions/api/projects/ProjectInput.java), [Project REST API](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/Documentation/rest-api-projects.txt), and [Access Control](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/Documentation/access-control.txt): verify project-create fields, ref permission names, and seed/update requirements against the deployed version.
- Gerrit v3.14.4 [`GroupInput.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/extensions/api/groups/GroupInput.java) and [Groups REST API](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/Documentation/rest-api-groups.txt): owner/member/visibility inputs and membership queries.
- Gerrit v3.14.4 [`CheckAccess.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/restapi/project/CheckAccess.java), [`DefaultPermissionMappings.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/permissions/DefaultPermissionMappings.java), and [`SetAccess.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/restapi/project/SetAccess.java): ref permission mapping, label permission distinction, and access-diff input shape.
- Gerrit v3.14.4 [CreateRefControl source](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/project/CreateRefControl.java): version-specific ref creation checks.
- Docker Gerrit v3.14.4 Ubuntu 24 entrypoint: https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/entrypoint.sh
- Docker Gerrit v3.14.4 Ubuntu 24 Dockerfile: https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/Dockerfile
- Become any account registration form: https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/resources/com/google/gerrit/httpd/auth/become/BecomeAnyAccount.html
- Become account registration handler: https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/java/com/google/gerrit/httpd/auth/become/BecomeAnyAccountLoginServlet.java
