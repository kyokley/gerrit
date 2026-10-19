# macOS local Gerrit setup notes

Platform-specific supplement to the [streamlined setup](streamlined-setup.md) and [Docker/API recipe](docker-and-api.md). This records observed macOS Docker Desktop, Brave, and Gerrit 3.14.4 behavior. Treat each versioned observation as a reason to verify live state, not as a universal default. The invocation and default-setup contract is in the main skill; this historical reference neither starts a run nor broadens that scope.

Use the canonical `opencode/` tree. A documentation-only request has no live side effects. An explicit run request authorizes a fresh per-run browser profile and bounded Gerrit setup, but not tool installation, shell/system/browser preference changes, Docker-context changes, or Jujutsu repository configuration edits. A named Jujutsu remote remains a separately approved write unless that exact mapping is explicitly included.

## 1. Docker Desktop and host locality

Before a pull or any container/volume creation, verify client and server versions, current context, its Docker endpoint, server OS/architecture, existing resources, and loopback listeners. Use only non-secret context metadata:

```sh
docker --version
docker compose version
docker context show
docker context inspect "$(docker context show)" --format '{{json .Endpoints.docker}}'
docker info --format 'Server={{.ServerVersion}} OS={{.OperatingSystem}} Arch={{.Architecture}}'
docker ps -a
docker volume ls
docker network ls
lsof -nP -iTCP:<candidate-port> -sTCP:LISTEN
```

A macOS Docker Desktop context was observed using a local Unix socket under the user's `~/.docker/run/` directory and an `aarch64` daemon. These are observations, not constants: inspect the active endpoint and architecture every time. A Unix socket endpoint is local; a remote TCP/SSH Docker endpoint is not. A remote daemon's loopback ports are not reachable from the Mac browser unless a separately approved secure tunnel is used. Never change context or publish on `0.0.0.0` to work around locality.

Resolve the selected image's immutable digest and platform at execution. A registry manifest inspection is read-only and can distinguish the platform-specific digest from an image-index digest:

```sh
docker manifest inspect --verbose '<image-tag>'
```

Do not infer the tag from the Gerrit version, assume the host and daemon architectures match, or use `latest`. Pull only the approved digest/platform. After pull, inspect only safe image metadata such as image ID, OS/architecture, entrypoint, and declared volume paths. Do not print the full image config or its environment, which may contain sensitive values.

For image-content checks, use a disposable diagnostic with read-only root, network disabled, and explicit tmpfs mounts at **every** image-declared volume path. Do not let Docker create anonymous volumes. Verify the exact entrypoint, Bash/Git, WAR path, config path, and log path before initialization. A v3.14.4 Ubuntu 24 image inspection observed declared volumes for `etc`, `git`, `db`, `index`, and `cache`; `/var/gerrit/logs` existed but was not image-declared. Add a separate named `logs` volume only when the selected release's logging path/configuration is confirmed. Never mount over all of `/var/gerrit`, which can hide the WAR.

An observed `java -jar /var/gerrit/bin/gerrit.war version` check failed under a strictly read-only diagnostic root because Gerrit attempted temporary file creation. Do not weaken read-only inspection to make that command work. After approved startup, verify the deployed version through the bounded loopback HTTP version endpoint and require HTTP 200.

Create a dedicated `0700` state directory outside the repository only after its parent and exact target are checked. Keep Compose/checkpoint/receipt files `0600`, use fresh project-prefixed named volumes, and pass both the unique Compose project and absolute Compose file on every command. Check candidate ports again immediately before startup; a free-port check is racy. Bind published HTTP/SSH only to `127.0.0.1` and set `CANONICAL_WEB_URL` with the chosen loopback HTTP port.

## 2. Headed Brave and CDP on macOS

Do not require a PATH-installed `google-chrome` binary. An appropriate generic Brave app-bundle executable is:

```sh
'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
```

```sh
'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser' \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=<checked-port> \
  --user-data-dir='<unique-private-profile-path>' \
  --no-first-run --no-default-browser-check
```

If the executable or user GUI session is unavailable, stop and ask; do not install a browser or silently switch to headless. In one macOS session, a usable native GUI login session existed while both `DISPLAY` and `WAYLAND_DISPLAY` were unset. Check native macOS GUI availability (for example, `launchctl print "gui/$(id -u)"`) rather than treating those Linux display variables as a macOS requirement. Do not add `--no-sandbox`.

Before launch, create and hold a private launch lock while rechecking the CDP port and unique profile path. Use a fresh `0700` profile under the approved state directory; do not adopt an unknown listener or reuse a user's normal profile. Keep the setup foreground and one browser owner. Verify exact PID/arguments, profile permissions, the listener bound only to `127.0.0.1`, and `/json/version` on that exact port. Verify the returned WebSocket host/port before attaching. Do not dump DOMs, screenshots, cookies, headers, or network bodies.

Node 26.4 in one observed macOS environment provided the built-in `WebSocket` global (`typeof WebSocket === 'function'`). Feature-detect it at runtime; do not install Playwright, Python WebSocket packages, or another CDP dependency without separate approval.

## 3. Init-created account provenance and registration

The image shell entrypoint can run Gerrit initialization without creating an account explicitly, while Gerrit's internal initialization/runtime paths may create the first account record. A chooser entry named `admin` is not proof that it matches the init receipt or belongs to Administrators.

When an account appears unexpectedly, stop account creation and compare only non-secret provenance in the exact fresh instance: verified-empty named volumes, the sole init operation/window, the `All-Users` account ref and `Create Account` metadata, and any recorded non-secret init receipt. In one v3.14.4 session, an `All-Users` account ref's `Create Account` commit timestamp fell inside the sole initialization window after fresh volumes were confirmed absent; that reconciled the account to init. Do not read token data, `initialToken`, passwords, SSH private keys, or broad logs/config. If provenance or rendered UI membership cannot be proven, stop rather than creating a duplicate admin or granting membership.

When a new account is authorized and absent from the chooser, the rendered **New Account** action creates the account before its registration modal appears. Click it once, resume the pending modal, set only the approved username, and save only inside `gr-registration-dialog`. Do not click **New Account** again while the modal is pending or invent email/display/full-name/password fields. Verify the exact Profile username/account ID after reload. Preserve the init-created admin and existing accounts.

## 4. Token modal and recovery behavior

On Gerrit 3.14.4, token UI preflight must compare the exact label and finite lifetime in the native inputs, Material host values, and `gr-auth-token.newTokenId`/`newLifetime` model. The observed `maxLifetime` value was `unlimited`; never rely on an empty lifetime field. Require valid fields, `loading === false`, no existing label collision, no active token modal, one enabled Generate target across host/Material/native layers, and a verified composed-tree hit target. Enter values with trusted CDP input, then use one trusted mouse press/release on the verified Generate target. Never press Enter, dispatch synthetic DOM events, or retry the action.

The one-time token must move directly from the active rendered modal to Node process memory and then to a new exclusive `0600` file in a `0700` directory. Gerrit 3.14.4's observed modal is a rendered `dialog` with ID `generatedAuthTokenModal` and a token display section `#generatedAuthTokenDisplay`; the secret was present as rendered text rather than a visible native text field. These selectors are version-specific observations, not universal selectors. Locate the active modal using non-secret structural metadata; extract only the one token candidate inside that exact display section. Do not dump modal text/DOM or return token-bearing CDP results to a tool. Verify file mode/nonempty state, then close the modal. Record only the label, finite lifetime/expiry, path/permissions, and separate UI/REST validation outcomes.

If capture is uncertain, preserve the still-open modal and state. Under the active run scope, recover only the same already-generated token from the still-rendered source-verified display into its prechecked secure file; this is not permission to Generate again. Do not navigate, reload, close, delete, or revoke before storage. If the one-time value is unavailable, stop and ask; never silently refresh an expired token. Keep the admin token's finite lifetime close to bootstrap and the bot credential separate. Do not place token values in commands, arguments, environment, source, logs, screenshots, checkpoints, or chat.

## 5. Gerrit groups, project access, and base seed

Groups are server-scoped; project ACLs are project-local. Check exact group-name collisions before creation. For an approved private bot group, v3.14.4 `GroupInput` supports `owner_id`, `members`, and `visible_to_all:false`; verify the owner UUID, exact direct member IDs, included groups, and that the creator was not added unexpectedly. Some `GroupInfo` responses omitted the `visible_to_all:false` field, so do not infer privacy from an omitted false value or an unauthenticated 403 alone; verify the creation request and the supported visibility evidence. Create a reviewer group only when effective inherited read/label access is actually missing.

For a new v3.14.4 project, `ProjectInput` supports `create_empty_commit:false` and a `branches` list. One observed PUT with a requested branch produced `HEAD` and `refs/meta/config` while the requested `refs/heads/<branch>` remained absent. Verify the exact project is new and the branch absent after creation; never infer branch state from the `branches` input. Preserve inherited/local access and owner/global-capability rules.

The `POST /a/projects/{project}/access` `AccessInput` wrapper must include a `permissions` object for each ref section and a `rules` object for each permission. Valid minimal add shape:

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

Temporary seed grant removal uses the same nesting under `remove`, with only the exact bot group `create` and direct `push` rules on `refs/heads/<branch>`. Structurally validate payloads before any POST; malformed unwrapped permission maps caused a v3.14.4 `SetAccess` null-permissions failure (HTTP 500). A failed write is not proof of state; re-read local access before any retry. Remove grants in a `finally` path and verify local rules are absent **and** effective `create`/direct-push checks are denied. If inherited access prevents denial, stop without global blocks or workarounds.

`check.access` verifies reference permissions such as `read`, `push`, `create`, `forgeAuthor`, and `forgeCommitter`; v3.14.4 `CheckAccess`/default permission mappings do not recognize `label-Code-Review` there and return HTTP 400. Verify label ranges through inherited/local access configuration and group membership instead. Record `min`, `max`, `exclusive`, and any `BLOCK` rules as configuration evidence; do not cast a vote. One observed fresh project inherited read from `Anonymous Users`, review push and `forgeAuthor` from `Registered Users`, Code-Review `-1..+1` from `Registered Users`, and lacked `forgeCommitter`; this varies by server. Do not widen inherited grants or change `All-Projects`.

Before adding temporary seed grants, verify the local base object, absent target branch, baseline effective bot create/direct-push denial, and exact temporary rule scope. After adding the approved grants, verify both required permissions are allowed, then seed using only the approved one-time base refspec, per-command dedicated key/known_hosts, strict host checking, `IdentitiesOnly=yes`, and bounded SSH options. Do not seed selected review commits or force-push. Verify remote branch head equals the exact base SHA. On success or failure, remove only the temporary rules in `finally` and verify effective denial again.

## 6. Jujutsu remote and upload compatibility observations

An observed jj 0.45.1 help described `--remote` as accepting a configured remote or full SSH URL, but the exact local Gerrit URL failed at runtime with `remote ... does not exist`. If the explicitly approved direct-URL dry-run fails for that reason, check for collision and use only the preapproved unique remote name. Capture before/after remote lists, prove `origin` unchanged, verify the new mapping, and retain it until separately authorized removal. Do not fallback to or change `origin`.

An observed native upload had no explicit local `Change-Id` trailers; native jj upload derived missing Change-Ids from stable jj change IDs in uploaded copies, changing uploaded SHA while preserving local commits. Record the stable jj ID/local SHA/Change-Id/number/patch set/uploaded SHA/URL map. Do not invent or add trailers. A dry-run may list only stack heads, does not prove remote connectivity, and never authorizes upload. Obtain fresh approval for the exact command without `--dry-run`.

## Offline safety scenarios

- Mac Docker Desktop reports an arm64 daemon while the image manifest is multi-platform: pin the verified `linux/arm64` digest; do not pull an unverified tag or print full config/env values.
- Brave exists only inside the macOS `.app` bundle while `DISPLAY` and `WAYLAND_DISPLAY` are unset: use the exact approved app-bundle executable in the native GUI session; do not install Chrome or switch headless.
- The chooser lists `admin`, but the init receipt lacks an identity: stop duplicate creation; reconcile only safe fresh-volume/account-ref/`Create Account` timing and rendered Administrators membership.
- Gerrit has the same run's active one-time-token dialog but an input selector finds no visible field: preserve it and use non-secret structure/source evidence to locate the rendered token display. If the value remains, recover only that same value under the active run scope; no second Generate click. If the value is lost, stop.
- Correct AccessInput has an inherited allow for a requested permission: do not add a redundant local grant. If `check.access` rejects a label permission, inspect access rules/group membership and do not vote or broaden ACLs.
- A seed `POST /access` returns 500: read exact project access, validate the nested `section.permissions.permission.rules` shape, and stop unless the cause is verified; never blindly retry.
- Upload dry-run rejects a direct URL while help says it is supported: use the user-approved unique remote only after collision check; preserve `origin` and do not upload before fresh approval.

## Versioned sources and evidence

- Gerrit v3.14.4 [`ProjectInput.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/extensions/api/projects/ProjectInput.java) and [Project REST API](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/Documentation/rest-api-projects.txt): `create_empty_commit` and branch-input support; still verify live project/branch state.
- Gerrit v3.14.4 [`GroupInput.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/extensions/api/groups/GroupInput.java) and [Group REST API](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/Documentation/rest-api-groups.txt): group owner/membership/visibility inputs and direct/included group queries.
- Gerrit v3.14.4 [`CheckAccess.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/restapi/project/CheckAccess.java), [`DefaultPermissionMappings.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/permissions/DefaultPermissionMappings.java), and [`SetAccess.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/restapi/project/SetAccess.java): ref-permission check semantics and nested access-input handling.
- Gerrit v3.14.4 [`CreateRefControl.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/project/CreateRefControl.java): create/direct-push requirements for an unreachable base commit.
- Gerrit v3.14.4 Docker [entrypoint](https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/entrypoint.sh) and [Dockerfile](https://raw.githubusercontent.com/GerritCodeReview/docker-gerrit/v3.14.4/ubuntu/24/Dockerfile): version-specific init and image paths.
