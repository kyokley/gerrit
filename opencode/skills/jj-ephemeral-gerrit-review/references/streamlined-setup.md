# Streamlined isolated Gerrit setup

Procedural companion to the skill and [Docker/API recipe](docker-and-api.md). Placeholders below are illustrative; do not reuse runtime values from another session. This reference does not authorize live operations.

## 1. Parallel read-only preflight

Read repository instructions and current status first. Preserve unrelated workspaces and files. Edit only the canonical `opencode/` tree. If `.opencode` is absent, do not create a symlink or configuration as part of this workflow.

Run independent checks in parallel, without creating resources:

- **Jujutsu/repository:** check operation state, bookmarks, configured identity, installed `jj` version and `jj gerrit upload --help`. Resolve the exact user-selected revset, its complete effective closure `mutable()::(<revset>)`, immutable base commit, ancestry, and target branch. Freeze the base and closure in one checkpoint. Never infer the revset or include commits outside its verified closure.
- **Docker/image:** inspect Docker/Compose versions and context, image tag/digest/platform, image entrypoint/tools/paths, candidate loopback ports, and whether intended named resources already exist. A remote Docker context does not give the browser access to the daemon's loopback ports. For Docker Desktop/macOS endpoint and architecture checks, see [macOS setup notes](macos-setup.md#1-docker-desktop-and-host-locality).
- **Browser/CDP:** inspect existing browser processes, binaries, display availability, Node version and builtin WebSocket, and other already-present CDP clients. The [ai-browser guide](https://github.com/kyokley/ai-browser) is a reference, not a prerequisite to install. See [macOS setup notes](macos-setup.md) for Docker Desktop locality, app-bundle Brave discovery, native headed GUI checks, and a separate v3.14.4 token/account recovery observation. These are observations, not environment guarantees. Do not fetch, build, or install browser tools/dependencies without separate approval.

Resolve exact base, revset closure, image/platform, ports, browser ownership, and authorization gaps before writes. Create one restricted checkpoint outside the repository with verified decisions, owners, identities/IDs, paths, deadlines, and receipts. Never put token values, passwords, cookies, or private keys in it.

## 2. Consolidate authorization once, early

Present one checklist covering known choices. Its bullets are distinct decisions within that single checklist, not separate prompts at each later step. Provisioning a Gerrit instance does **not** authorize browser creation, sign-in, account or key creation, token creation/lifetime, a named repository remote, project creation, ACL changes, base seeding, or publication. Record each approved role, target, lifetime, scope, and non-action. Do not ask again about settled choices; stop and ask if live state materially differs or a new choice is required.

Confirm separately:

- Whether an already-owned browser/CDP session may be used, or whether a new isolated browser/profile may be created; whether headed display is required.
- Admin handling: reuse the exact init-created administrator only after user authorization **and** rendered UI proof of Administrators membership matching its profile identity; otherwise, create the first admin only when account state is truly empty and that creation is authorized. Treat an init-created account as expected only when it matches the initialization receipt/checkpoint; reuse remains separately authorized and unexpected accounts remain a stop condition. Never infer admin rights from a username or account order.
- Distinct roles: admin for bootstrap; `review-bot` for automation; `user` for reviewer. Confirm which accounts may be created/reused, the dedicated SSH public key, and per-account HTTP token need, label, and finite lifetime. Unlimited lifetime is a separate explicit choice, never an empty-field default.
- Installed `jj` remote-mode compatibility. If this version requires a named remote, approve a unique remote name and its full verified Gerrit URL before any repository-config write; do not require a named remote or a full URL when the installed version supports the other form directly.
- Any later project/branch, exact user-scoped permission and ref scope, one-time direct-push base, and grant-removal step. These are independent of provisioning and of each other.
- Publication approval only after a fresh exact-closure dry-run; cleanup only after a separate explicit request and target confirmation.

Unexpected existing accounts, duplicate usernames, unverified membership, occupied state, or a lost one-time secret are not covered by blanket approval. Stop rather than repurpose, duplicate, revoke, or silently expand scope.

## 3. Create and verify the isolated instance

1. Only after setup authorization, use a new restricted temporary directory, unique Compose project, fresh uniquely named volumes, verified image digest/platform, and loopback-only HTTP/SSH bindings. Match image paths; do not mount over all of `/var/gerrit`.
2. Run init only against verified-empty volumes. Init may create the initial account and can upgrade existing state. Never reinitialize on resume. Configure and verify the selected auth mode before startup.
3. Check actual readiness, Gerrit version, persisted auth mode, exact loopback URLs/bindings, and container/image/project identity. A running container alone is not readiness.
4. Freeze a checkpoint before continuing. Assign one live browser owner. If several workers help, they must not race the same tab, account, dialog, or token.

## 4. Choose and own a safe browser

Prefer a user's verified owned CDP session when authorized. Otherwise create a fresh private profile only with explicit authorization. Check for an unused port while holding a launch lock; never adopt an unknown listener. The ai-browser launcher may reuse an occupied port and does not explicitly bind loopback, so do not use it unchanged. Launch with an explicit `--remote-debugging-address=127.0.0.1`, a checked `--remote-debugging-port=<port>`, and a unique `--user-data-dir=<private-path>`; include `--no-first-run` and `--no-default-browser-check`. Never add `--no-sandbox`.

On Linux, use headed mode only when the required display is usable. On macOS, check the native logged-in GUI session; unset `DISPLAY`/`WAYLAND_DISPLAY` alone does not prove that headed mode is unavailable. Do not silently install a browser or switch to headless. Verify process PID/arguments, profile mode `0700`, socket bound only to `127.0.0.1`, and `/json/version` for that exact owned endpoint. Keep this browser alive through the authorized setup; do not tear it down during handoff. See [macOS setup notes](macos-setup.md#2-headed-brave-and-cdp-on-macos).

## 5. Admin and account UI flow

1. Open the verified Gerrit URL. Follow the rendered labels: Gerrit 3.14.4 may show **Sign in → Register → New Account**, not only **Become → New Account**. Inspect the chooser and profile. For a reused admin, open **Browse → Groups → Administrators → Members** and match the signed-in profile identity to the listed member. If membership cannot be proven, do not use that account for privileged setup; grant no privilege as a workaround.
2. If a genuinely empty account state and first-admin creation are both authorized, use **New Account** exactly once. It creates an account before profile completion. Resume its pending `gr-registration-dialog`; never click New Account again while that modal is pending.
3. For each authorized new account, check the signed-out chooser first. Stop if the username already exists unexpectedly. In the modal, set only its approved username; do not invent email, display name, full name, or password. Scope the **Save** control to `gr-registration-dialog`, including open shadow roots; do not click Profile **Save Changes**. Verify exact username and account ID in Profile/header, reload, then verify again.
4. With the verified automation account signed in, inspect Settings → SSH Keys before adding anything. Compare the exact authorized public `.pub` material; never read a private key. Add once through **Add New SSH Key** only if absent. Verify algorithm/material, **Valid** status, and persistence after reload for the same bot account ID. A key comment may be displayed differently; compare key material, not a partial comment.
5. Sign out only after any token modal is closed and its secret is stored. Register the authorized reviewer username `user` only if absent; verify its ID after reload and leave the browser signed in as `user`. Do not give the reviewer bot credentials, admin membership, or unapproved project rights.

## 6. HTTP token generation: exact UI model and one-time capture

An enabled input and native browser validity are not proof Gerrit's component model received the values. In Gerrit 3.14.4, `gr-auth-token` has native inputs, Material host values, and component model fields (`newTokenId`, `newLifetime`). Require the exact authorized label and finite lifetime in all three layers. Empty `newLifetime` can request the default maximum, even when the native input appeared to contain `1h`; an unlimited maximum therefore makes an empty field unsafe.

1. Verify URL, signed-in username/account ID, pre-existing non-secret token labels, absence of the new label, and absence of a pending token modal. Never read an existing token's secret value or delete/revoke it; preserve tokens such as `initialToken`.
2. Inspect the UI's accepted duration and maximum. If the default is unlimited, use only the explicitly approved finite duration. Focus each rendered input with CDP mouse input, select existing text safely, and use `Input.insertText`; do not press Enter, which may trigger generation.
3. Read only the two native values, Material host values, and `gr-auth-token.newTokenId/newLifetime`. Require exact approved values, valid bounds, `loading === false`, one visible Generate target, every host/Material/native button layer enabled, and a verified hit target in the composed tree. Check rendered validation errors. Recheck immediately before the action.
4. Use one trusted CDP `mousePressed`/`mouseReleased` at the verified Generate target. Do not use synthetic DOM event dispatch or automated retries. Check CDP `exceptionDetails` explicitly; never hide failures behind optional chaining. Record only non-secret request method/path/status and safe UI state—never request/response bodies, headers, cookies, or full DOM/modal dumps.
5. Gerrit 3.14.4 opens the generated-token dialog before its REST result. If no modal appears, inspect the non-secret token label and pending-modal state before deciding anything. A persisted label with a lost one-time value means stop and ask; do not create or revoke another token. Any further generation requires fresh user approval.
6. Capture the token from the active rendered modal directly into Node process memory. Exclusively create a new file outside the repository in a mode-`0700` directory; write only token plus newline to a mode-`0600` file. Consume token-bearing CDP results only in memory. Do not expose them through `cdp.py eval`, console/tool output, screenshots, logs, shell arguments, environment, source, checkpoint, or chat. Verify file permissions/nonempty state, then close the modal.
7. Verify finite displayed expiry and label persistence after reload. If REST use is authorized, separately make a bounded read-only `GET /a/accounts/self` with the correct account's stored credential and verify exact username/account ID/HTTP 200. Report UI generation/storage and REST auth as separate checks. Never use the admin token for the bot or vice versa.

These steps are deliberately not “streamlined” into one blind Generate click. The earlier handler failure's root cause was not proven; synthetic shadow-boundary event behavior is only a hypothesis. The observed successful path used trusted CDP mouse input after all native, Material, and Gerrit model values matched. Tokens have the approved lifetime only; generate each as late as practical before use. Budget the admin token for its bounded bootstrap and grant-removal window; the bot token should cover authorized polling. On 401/expiry, stop and ask—do not silently extend or regenerate.

## 7. Minimal dependency-ordered runbook

1. Complete parallel preflight, resolve the exact revset/base/closure, verify image and browser availability, agree authorization once, and freeze one checkpoint.
2. Provision only the authorized fresh Docker instance; verify init/config, readiness, exact image, and loopback endpoints.
3. Match any init-created account to the initialization receipt/checkpoint, then inspect it in UI. An authorized sign-in may be needed solely to verify membership; without proof, stop privileged reuse/setup. Create a first admin only in truly empty state and when authorized.
4. Prepare the dedicated SSH key and per-instance known-hosts independently. Register/verify the authorized bot identity, key, and finite bot token under the single browser owner. Test REST identity separately. Register/verify reviewer `user`; leave signed in as reviewer when account setup finishes.
5. Immediately before any separately authorized project bootstrap, sign in as the verified admin and generate its approved finite token only if needed. Budget its lifetime for bootstrap and grant removal. Perform only approved project/grant/base steps, remove temporary grants as authorized, and verify least-privilege ACLs. Sign out as admin, then reselect authorized reviewer `user` so the browser ends in the intended human identity. Do not create project, grant, or seed state during account setup alone.
6. Before any review upload, re-verify exact project/branch/base and current closure, run and report the exact dry-run, then obtain fresh publication approval. Use the remote form supported by the installed `jj`: prefer the approved uniquely named remote when that version requires a remote name; otherwise use the explicitly approved full verified Gerrit URL. Do not add repository configuration unless approved. After approved upload, report only actual returned change URLs. Run feedback polling only as a bounded foreground session.
7. Preserve state through review. Cleanup is a separate destructive operation requiring fresh explicit authorization and a verified target list.

## 8. Offline safety scenarios

Use these as documentation/static-review cases, not live account/token actions:

- Init receipt/checkpoint matches an existing `admin`, but membership is absent/unverifiable: authorized sign-in may be used only for read-only membership verification; stop privileged reuse/setup unless membership is proven. Never grant the missing membership as a workaround.
- Existing account chooser contains `admin`; profile and Administrators member match under explicit reuse authorization: reuse that exact ID; do not create a second admin.
- Lifetime native input reads `1h`, but Material or `gr-auth-token.newLifetime` is empty: disable the Generate gate; do not click.
- A trusted click creates the requested non-secret token label but the one-time modal value cannot be safely captured: preserve state and stop; no duplicate generation/revoke.
- A fresh automation/reviewer username is already assigned, or a project/branch/base is not empty as expected: stop and ask; never hijack, overwrite, or force-push.
- A named remote is required by installed `jj`, or an HTTP token expires before the bounded operation: pause and obtain only the missing authorization; do not change config or regenerate silently. During polling, stop starting work when `remaining <= 0`, set each request timeout to `min(5 seconds, remaining budget)`, and cap backoff/sleep by the remainder; preserve the last full snapshot and a separate terminal receipt.

## Versioned evidence

- [ai-browser guide](https://github.com/kyokley/ai-browser): session ownership, fresh profile, port selection, and CDP usage. Its launcher must not be used unchanged when endpoint ownership/loopback binding is uncertain.
- Gerrit v3.14.4 [`gr-auth-token.ts`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/polygerrit-ui/app/elements/settings/gr-auth-token/gr-auth-token.ts) and [`gr-auth-token_test.ts`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/polygerrit-ui/app/elements/settings/gr-auth-token/gr-auth-token_test.ts): field/model handling, Generate action, modal, and UI tests. The test queries `#generateButton` as `GrButton` and calls `.click()` on the `GR-BUTTON` host; it does not show a click on a nested native button. That tested path reaches the handler but does not explain why the earlier CDP click missed it. The prior failure's root cause remains unproven. The observed successful path used trusted CDP mouse input after all native, Material, and Gerrit model values matched.
- Gerrit v3.14.4 [`CreateToken.java`](https://gerrit.googlesource.com/gerrit/+/refs/tags/v3.14.4/java/com/google/gerrit/server/restapi/account/CreateToken.java): server token creation and lifetime behavior. Verify these sources against the deployed version before relying on details for another Gerrit release.
