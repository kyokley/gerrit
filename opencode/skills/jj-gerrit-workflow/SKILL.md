---
name: jj-gerrit-workflow
description: Plan and implement feature work as focused Jujutsu change stacks, validate each relevant change, and safely prepare or upload those stacks for Gerrit review. Use this skill whenever a feature is developed in a jj repository intended for Gerrit, or the user asks to plan, split, validate, or upload jj changes to Gerrit. Never upload without fresh explicit user approval.
compatibility: Requires Jujutsu (jj); native Gerrit upload requires `jj gerrit upload` support. Do not substitute an unverified fallback.
---

# Jujutsu feature stack and Gerrit review

Use this for feature work from initial preflight through review upload. Keep the stack understandable, preserve user work, and stop at the first unresolved setup or safety blocker.

## 1. Preflight before setup or edits

1. Confirm this is the intended repository and inspect its local instructions. Identify current branch/bookmark, base, configured remotes, working-copy state, and existing user changes before editing.
2. Check `jj --version`, `jj root`, `jj status`, `jj git remote list`, `jj config get user.name`, `jj config get user.email`, `jj log`, and `jj bookmark list` as appropriate. `jj status` normally snapshots the working copy; disclose that side effect. `--ignore-working-copy` avoids snapshotting but may show stale state.
3. Confirm a usable author identity, the intended Gerrit remote/project, and target branch from project documentation or an approved read-only remote check. A local Gerrit container, a configured GitHub remote, or a remote name alone does not prove Gerrit hosting or the target branch.
4. Ask before any setup/config/init changes, including creating a remote/bookmark, changing identity, initializing/converting a repository, or adding automatic trailer configuration. Do not expose credentials or invent network/auth setup. Ask the user if host, project, remote, branch, base, or setup is unclear.
5. If native upload is planned, inspect `jj gerrit upload --help` for the installed version. Use native support only when present. If absent, stop and ask whether to upgrade or research a compatible path; do not invent fallback commands.
6. Preserve pre-existing modifications and review identities. Do not discard, abandon, rewrite, or absorb user work. If work overlaps, stop and ask how to proceed.

## 2. Plan and implement incrementally

1. Clarify feature goal, acceptance checks, relevant tests, and a safe base. Plan major components as small, single-topic, reviewable commits in dependency order. A trivial feature may be one commit; do not impose a commit count or LOC threshold.
2. Keep a concise plan of intended changes and tests. Separate unrelated work and retain all pre-existing changes outside the feature stack.
3. Implement one component at a time. Before advancing, review the changed files and diff, run relevant focused tests/checks, and make the single-topic change into a jj commit. Include tests with the commit they validate when practical; do not implement everything first and split it afterward.
4. Ensure each commit has a useful description, coherent scope, no conflicts, no unintended empty changes, and is buildable/testable in stack order where practical. Avoid mixing formatting or unrelated cleanup into feature changes.
5. Before finalizing, validate the complete feature and stack with appropriate tests, inspect status/diffs and commit descriptions, check conflicts, and confirm no unrelated user changes entered the stack. Report skipped tests and unresolved issues.

Use `jj describe -m '<topic and rationale>'` to describe a feature-owned working change, then `jj new` to start its next dependent component. If the working change contains pre-existing work, isolate that work with permission before using this sequence. Use jj for local history management, not `git add`/`git commit`; jj records working-copy changes automatically.

## 3. Preserve Gerrit change identity

- A Gerrit review is identified by project, target branch, and `Change-Id`. Updating the same jj change preserves its change identity; keep its existing Gerrit `Change-Id` trailer intact when updating a patchset.
- For a split or squash, retain an existing explicit `Change-Id` only on the intended surviving review. New independent topics need distinct identities. Do not duplicate a `Change-Id` across commits or hand-invent one.
- Native `jj gerrit upload` generates a missing `Change-Id` from the jj change ID for the uploaded commit only. It does not add that trailer to the local commit; Gerrit-uploaded commit ID may therefore differ from the local one. Explain this before upload and account for possible divergence when later fetching merged changes.
- jj does not run Git `commit-msg` hooks. Do not assume a hook will add trailers. Do not change config to add `commit_trailers` without explicit user consent and checking the installed jj version/config support.

## 4. Preflight and upload safely

1. Before upload preparation, reconfirm remote destination, Gerrit project, target branch, base, and intended revisions. Verify the remote push destination and branch through project documentation or an approved read-only check. Do not infer a Gerrit target from `origin`, `main`, `git HEAD`, local container ports, or defaults.
2. Select explicit revision(s) and explicit `--remote` and `--remote-branch`. Never rely on the default `@`/`@-` selection: `@` can be empty. Do not assume `BASE..TIP` limits what upload publishes.
3. Native upload includes every selected revision **and all mutable ancestors**; a selection is not a publication boundary. Inspect the effective closure, for example `mutable()::(<selected-revset>)`, and compare every included change against the intended stack. Stop if it contains unrelated ancestors or user work. Do not make changes immutable to hide them. If a safe rebase or stack repair is needed, explain the effect and get permission before rewriting anything.
4. Run only a dry-run first, with the exact explicit selection, remote, and branch:

   ```sh
   jj gerrit upload -r '<selected-revset>' --remote '<gerrit-remote>' --remote-branch '<target-branch>' --dry-run
   ```

   Inspect its output. A dry-run is not upload approval. If any part is surprising, stop.

5. Get fresh, explicit user permission for the actual upload after reporting the exact revisions/ancestor closure, remote/project, branch, dry-run result, Change-Id behavior, and risks. Only then repeat the same command without `--dry-run`. Do not upload in the same step as obtaining approval unless approval clearly covers this exact preflight result.
6. Never pass `--submit` or `--skip-validation`. Do not add reviewers, CCs, labels, topics, hashtags, WIP/private state, draft/comment publication options, or other push options unless specifically requested and approved. Never merge or submit changes.
7. Report actual result and Gerrit review URLs when returned. If no upload was approved/performed, say so plainly. Do not claim a dry-run created review changes.

## Output

Give a brief status with:

- Setup blockers or verified repo/remote/project/branch.
- Planned or completed commits, their topics, and relevant validation results.
- Effective upload selection/ancestor closure and dry-run result, when applicable.
- Whether upload was explicitly approved and performed; include returned Gerrit URLs only for actual reviews.
- Any skipped validation, unresolved identity/Change-Id concern, or divergence caveat.

## References

- Jujutsu Gerrit support: https://docs.jj-vcs.dev/latest/gerrit/
- Gerrit Change-Id: https://gerrit-review.googlesource.com/Documentation/user-changeid.html
- Gerrit upload: https://gerrit-review.googlesource.com/Documentation/user-upload.html
