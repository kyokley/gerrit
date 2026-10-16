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

## Authorized browser account setup

This procedure is permitted only after the user explicitly authorizes the account setup and identifies whether to use an existing administrator or create the first account on a truly empty instance. An authorization to provision a Docker review does not authorize account creation, sign-in, or key registration. Do not invoke this procedure during a documentation-only request. Use the ai-browser skill, the verified Gerrit Web URL, and the user's existing or explicitly selected CDP session. Respect browser-session ownership and port-selection rules; never discover and adopt an arbitrary CDP endpoint. Before each write, recheck the current URL, signed-in identity, and intended account/key target.

### Establish the intended administrator and account state

1. Inspect the rendered login/register UI and existing accounts before creating anything. Do not use the account's display name or a username such as `admin` as proof of privilege. When reusing an existing administrator, sign in only as authorized and verify its membership/access in the rendered UI, e.g. **Browse → Groups → Administrators → Members**, matching the currently signed-in account's displayed identity/email to a member. If an allegedly fresh checkpoint already has accounts, or its account state conflicts with the user's stated expectation, stop and ask rather than assuming it is an empty instance or silently reusing an account.
2. The documented first-account administrator rule applies only when the account state is actually empty before registration. Do not grant privileges or infer that a pre-existing account is administrator based on its name or creation order. If the user has chosen an existing admin, use it only after verifying its membership/access as above; create no replacement administrator.
3. If the account state is verified empty and the user authorized creating the first administrator, register only the user-chosen admin username through the rendered **Sign in → Register → New Account** (or **Become → New Account**) flow. Complete any profile modal using the same modal-scoped **Save** procedure described below. Verify the saved username and account ID in Profile/header, then verify that account's membership in **Administrators → Members** before proceeding. The first-account rule alone is not proof of administrator access; if membership is absent or cannot be verified, stop without granting privileges.
4. Gerrit 3.14.4 `DEVELOPMENT_BECOME_ANY_ACCOUNT` may render **Sign in** linking to `/login/`, with **Username**, **Email Address**, **Account ID**, a **Choose** list, and **Register → New Account**, rather than a visible **Become** menu. This alternate flow was observed in a 3.14.4 session; it is version/UI-specific evidence, not a guarantee for every version. Use the actual rendered labels and controls. Do not stop just because this expected alternate label flow differs from old **Become → New Account** instructions. If the actual flow is materially different or asks for an unapproved credential, permission, or configuration change, stop and ask; do not invent a route or use REST as a substitute.

### Register or resume the distinct automation account

1. Before a fresh automation registration, confirm the requested username is not already assigned to another account. If it already exists, stop; do not hijack, modify, or recreate it. If the administrator is currently signed in, use the rendered **Sign out** control, then return through the verified **Sign in/Register** UI. Sign out only after admin verification and before starting a fresh automation registration.
2. If an authorized automation registration has already created an account and its profile modal is still pending, resume that same account in the existing session instead: do not sign out, discard the modal, or click **New Account** again. In the observed 3.14.4 flow, **New Account** creates an account immediately and redirects to Settings; profile completion then presents a `gr-registration-dialog` modal with the username field.
3. In the automation profile modal, enter only the user-approved username; do not invent an email address, display name, or password. Use the rendered modal's own **Save** control scoped inside `gr-registration-dialog` (including its open shadow root), not the separate Profile **Save Changes**, **Close**, or a global arbitrary button. Wait for the modal to close, then verify the exact saved username and account ID in Profile and the signed-in header. Stop on validation errors or if the target identity is uncertain. Do not grant admin or other privileges.
4. Inspect Gerrit web components through open shadow roots when needed. Rendered `innerText` may be empty at an outer level; inspect visible UI/text content recursively and only the relevant subtree. When setting a native input/textarea value through CDP, use its native value setter and dispatch bubbling `input`/`change` events; verify the rendered value before clicking its explicitly scoped action. Do not dump whole settings pages, tokens, cookies, or account state to logs.

### Register and verify the authorized SSH public key

1. With the verified automation account still signed in, open **Settings → SSH Keys** in the rendered UI. Inspect existing keys first. If the same public key is already present, do not add a duplicate; verify its details, validity, and persistence instead.
2. Otherwise enter the exact per-instance public key supplied by the user, or the public `.pub` portion of the already authorized per-instance dedicated key recorded in the verified checkpoint, in **New SSH Key**. Do not read a private key, generate credentials, or copy a session-specific key into documentation. Click that SSH Keys section's **Add New SSH Key**. Verify the resulting row is for the expected key and has **Valid** status. Open **Click To View** and compare algorithm and base64 public-key material against the supplied/checkpointed public key. Gerrit may display only part of the whitespace-separated key comment (an observed key comment ending in a descriptive token was shown without that trailing token); compare algorithm and key material, not comment text. Record only a key identity/fingerprint or algorithm/material-match evidence in a checkpoint, not a private key; never print private-key material.
3. Reload Settings and verify the same key remains **Valid** and the header/Profile still identify the exact automation username and account ID. A valid registered SSH key does not itself prove SSH authentication or any HTTP REST credential works. Keep the dedicated restricted private key and per-instance `known_hosts`; verify host key through a trusted channel and require strict checking. Never alter global SSH settings.

Browser session cookies are not documented HTTP Basic REST credentials. This UI workflow does not establish an unauthenticated REST bootstrap path: `/a/` operations require genuine credentials and appropriate permissions. Never copy browser cookies into a checkpoint, guess usernames/passwords, use `X-Gerrit-RunAs`, or substitute REST account writes for the authorized UI. Create an HTTP password/token only if a documented REST operation actually needs it and the user has authorized its creation; keep it secret. If browser setup is unavailable, provide concise manual steps and wait for the user's confirmation rather than performing a different write path.

### Automation HTTP token

An HTTP token is a separate credential for the automation account's documented REST polling/reply operations; it is not needed for SSH upload and must not be borrowed from the administrator. Create it only when REST auth is needed and the user has authorized creating a token for that exact automation account. In Settings → HTTP Credentials, verify the current Gerrit URL, signed-in automation username, and account ID before any token action. Inspect only the token list's non-secret labels/expiration metadata. Never touch or revoke pre-existing tokens (including an `initialToken`); choose a unique, descriptive, non-secret label.

Before generating, inspect the rendered Lifetime field and its maximum/description. In the observed Gerrit UI, an empty Lifetime field uses the maximum allowed lifetime; when the maximum is unlimited, leaving the field blank creates an unlimited token. Ask the user for a bounded lifetime if the default would be unlimited and unlimited has not been explicitly authorized. Do not silently use unlimited, select a never-expire option, or extend an existing lifetime. If a finite duration is authorized, enter it in the form's accepted units and verify the displayed expiration after generation. The user's explicit choice of Unlimited for a different account/token does not authorize unlimited lifetime for the automation account.

Enter the approved label and lifetime, then click **Generate** in the verified automation account's HTTP Credentials section. Capture the one-time value directly from the rendered New Token modal through CDP into process memory and write it immediately to a new unique file outside the repository: create a private directory with mode `0700`, then a non-colliding file with mode `0600`; write only the token followed by a newline. Do not expose the CDP evaluation result through `cdp.py`/terminal output, browser text/screenshots, shell arguments, environment variables, logs, source code, checkpoints, or chat. Do not print or copy the full modal. Verify the file exists, is nonempty, and has the required permissions, then close the sensitive modal. Report/store only its path, label, selected lifetime/expiration, and verification status. A checkpoint may contain that non-secret metadata/path, never the token value.

If capture/storage fails or the result is uncertain, do not blindly generate another token. Inspect only the non-secret token list to determine whether the labeled token was created; if the one-time value cannot be recovered safely, stop and ask the user before any further token change. Remove an unneeded secret file only under the existing explicit cleanup authorization. A token appearing in the UI and being saved does not prove REST authentication. If a documented REST operation requires it and a test is authorized, verify with a bounded, documented read-only endpoint using the secure stored credential without exposing it; report UI generation/storage and REST validation as separate facts. Do not make REST writes in token setup.

Evidence boundary: the observed session generated an HTTP token for the administrator with an explicitly selected Unlimited lifetime and created reviewer username `user`. That account-specific choice does not authorize unlimited lifetime for another token. It does not mean an automation-account token exists or that REST authentication was tested. The same rendered token UI procedure above may be applied to the verified automation account after the required authorization and lifetime choice; do not claim that application has been live-verified by this session.

### Human reviewer account

The dedicated human reviewer username for this workflow is exactly `user`, distinct from the verified administrator and automation account. Create it only when the user has authorized that reviewer account. If an automation token is being created, store it safely and close the one-time modal before signing out of the automation identity. Then return through the rendered Sign in/Register page and inspect the listed accounts before selecting **Register → New Account**. If `user` is already listed, do not hijack, modify, or duplicate it; stop and ask the user to confirm whether that exact existing identity is intended. If a checkpoint records that this same authorized setup already created `user`, resume/verify that identity rather than registering again.

In the observed 3.14.4 flow, **New Account** creates the account before profile completion and opens a `gr-registration-dialog`. For a fresh authorized registration, enter only username `user` using the native field setter plus bubbling input/change events, then click the modal's own **Save** scoped within its open shadow root. Do not invent an email, password, or display name, and do not use the unrelated Profile **Save Changes**. If interrupted with this account's modal pending, resume that same account without signing out, discarding it, or clicking **New Account** again. Wait for the modal to close; verify username and account ID in Profile/header, reload, and verify both remain. Leave the browser signed in as `user` for human review. For later review, the observed development-auth UI allows choosing `user` from **Sign in → Choose**; this is local identity selection, not genuine multi-user isolation or proof that the reviewer has project permissions. Do not grant admin/global access, share automation credentials, use `user` for bot replies, or imply review/vote permissions. Ask if project access is absent rather than broadening permissions. Offer only actual review URLs after authorized publication; do not invent links before then.

Sources: [v3.14.4 Become form](https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/resources/com/google/gerrit/httpd/auth/become/BecomeAnyAccount.html), [registration handler](https://raw.githubusercontent.com/GerritCodeReview/gerrit/v3.14.4/java/com/google/gerrit/httpd/auth/become/BecomeAnyAccountLoginServlet.java). These source links do not imply identical labels/behavior across Gerrit releases.

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
