# Docker and Gerrit API recipe

Operational reference for `jj-ephemeral-gerrit-review`. These are command shapes, not an instruction to execute them. The entrypoint facts were inspected in Docker Gerrit v3.14.4 Ubuntu 24 source; they are not live-tested and may differ in another image. At runtime resolve the image digest and architecture, then verify entrypoint, paths, available tools, Gerrit version, schema, and installed jj options. A version tag alone does not guarantee registry contents. Do not copy placeholder values literally.

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

After verifying the pinned image includes `/bin/bash` and Git and confirming its config path, run these in order. `config --quiet` must pass before any initialization or startup. In verified v3.14.4 Ubuntu 24 source, the entrypoint runs init if All-Projects is absent or when passed the `init` argument; init can upgrade existing state. Run it only on new empty volumes. Never rerun it on resume. The config-only run overrides the normal entrypoint and shares the same named volumes, so the auth setting persists before the daemon starts.

```sh
docker compose -p '<unique-project>' -f '<absolute-compose-file>' config --quiet

# New empty volumes only; use the image's verified default entrypoint.
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

## Manual development-auth bootstrap

Open the verified loopback Gerrit Web URL, select **Become**, select **New Account**, and complete registration. In verified Gerrit v3.14.4, the first account created this way is granted administrator access; this does not mean arbitrary REST requests are unauthenticated. Then create a second, distinct automation account and use its supported account UI/API to register a dedicated SSH public key and create an HTTP credential only if needed for documented REST operations. Pause for a human to confirm identity and setup. If the selected version's form or behavior differs, stop and ask rather than guessing.

Sources: [v3.14.4 Become form](https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/resources/com/google/gerrit/httpd/auth/become/BecomeAnyAccount.html), [registration handler](https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/java/com/google/gerrit/httpd/auth/become/BecomeAnyAccountLoginServlet.java). Keep secrets out of output/checkpoint. Use a dedicated restricted key and per-instance `known_hosts`; never disable strict host checking or edit global SSH configuration.

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

URL-encode the project component and authenticate with the real account. Project creation uses `PUT /a/projects/{project-name}` with `create_empty_commit:false`. Consult the API schema for the pinned Gerrit release. Grant only required permissions on this fresh project; do not create global access rules.

Before base seeding, verify the project is newly created, the target branch does not exist, and the exact base SHA is present in the intended local Git backing repository. Seed the base and ancestors only, never review commits:

```sh
git -C '<verified-local-git-repo>' push '<dedicated-gerrit-ssh-url>' '<base-sha>:refs/heads/<branch>'
```

Then verify target branch head equals the exact base SHA. The `git push` above is the bootstrap exception; review publication uses `jj gerrit upload`. If any project/branch state differs, do not overwrite or reuse it.

## Native jj upload

Check the installed `jj gerrit upload --help`. Inspect the closure `mutable()::(<explicit-revset>)` and confirm all included changes are intended. A dry-run may not contact Gerrit. Verify SSH/auth and project/branch/base separately. First run:

```sh
jj gerrit upload -r '<explicit-revset>' --remote '<dedicated-gerrit-ssh-url>' --remote-branch '<branch>' --dry-run
```

Get explicit publish approval after showing exact closure, destination, branch, base, and dry-run. Repeat the exact command without `--dry-run` only after approval. Do not use submit or skip-validation options. Native upload can derive a missing Change-Id from jj change ID for the upload without adding a local trailer; keep a mapping of stable jj ID, Gerrit Change-Id, Gerrit change number, uploaded SHA, and patch set.

## REST detail, comments, and safe replies

Use only authenticated Gerrit REST APIs documented for the deployed version. Encode path components correctly. Gerrit JSON responses commonly begin with the XSSI prefix `)]}'`; remove that prefix before parsing JSON. Use bounded request timeouts, and pagination only when discovering results. Query exact tracked change IDs, not all project changes or arbitrary user data.

Fetch detail and published comments separately. For each encoded change ID, use the documented detail options and verify their response shape against the deployed release:

```text
GET /a/changes/{encoded-change-id}/detail?o=ALL_REVISIONS&o=MESSAGES&o=LABELS&o=DETAILED_ACCOUNTS
GET /a/changes/{encoded-change-id}/comments
```

Inspect comments across all patch sets, retaining each comment's source revision, path, line/range, author, ID, message, and unresolved status. Also inspect general messages and votes. Do not treat draft comments as published feedback; retrieve the automation user's drafts separately only when necessary.

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
- Docker Gerrit v3.14.4 Ubuntu 24 entrypoint: https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/entrypoint.sh
- Docker Gerrit v3.14.4 Ubuntu 24 Dockerfile: https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/Dockerfile
- Become any account registration form: https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/resources/com/google/gerrit/httpd/auth/become/BecomeAnyAccount.html
- Become account registration handler: https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/java/com/google/gerrit/httpd/auth/become/BecomeAnyAccountLoginServlet.java
