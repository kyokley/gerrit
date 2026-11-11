# Verification evidence

Date: 2026-11-11. Environment: Linux, Python 3.14.7, Jujutsu 0.47.1.

Final bounded source-review gate passed. The two final regressions close
post-upload tip-correction failure and low-number Gerrit ref construction.

## Reconciled implementation

- `PYTHONPATH=src python -m unittest discover -s tests -v`: exit 0,
  **74 tests passed**, 10.363 seconds.
- `ruff check --select F821 src/gerrit tests`: exit 0; no undefined names.
- `python -m compileall -q src tests`: exit 0.
- `uv build --offline --out-dir /tmp/opencode/gerrit-workbench-dist`: exit 0;
  produced a source archive and `gerrit-0.1.0-py3-none-any.whl`.
- Wheel inspection passed: executable helper modules and all three dashboard
  assets are present; Deepwork state and Python caches are absent.

The complete fake-backed default test runs the actual `Workflow`/`LiveAdapter`
code through an isolated probe and two feature units. It asserts
authenticated confirmation before the next agent invocation, and the first
feature-chain event before agent two. Credentials, Gerrit, SSH transport, and
Jujutsu object state are fixtures in this sequence; it is not a live upload.

Separate real local Git/Jujutsu tests cover preview formatting, immutable
selection, workspace isolation, cross-workspace feedback squash/freshness,
whole-tree binary/deletion/mode checks, and parent mapping. Fake SSH tests
cover strict identity options, host-pin snapshots, effective URL redirection
refusal, invalid SSH arguments, and operation evidence.
The final regressions also cover two-feature tip correction with revision-bound
replies and canonical Gerrit refs for change numbers 1, 9, 10, and 101.

Monitoring tests cover periodic polling, coding-slot release, stable feedback
deduplication, failed/unsupported checks, interruption gaps, deadline/final-sweep
requirements, and uncertain journal retention. They do not wait twelve hours.

## Local application and browser evidence

A real loopback HTTP server and real `Supervisor` passed a local smoke test:
asset serving, CSRF-protected settings persistence, 0600 settings permissions,
the standalone OpenCode default, and refusal to start an unconfigured task.
No credential provider, model, or Gerrit instance was contacted.

Chromium desktop/mobile screenshots were opened. An explicitly labeled local
API fixture exercised settings save, selected-task output, text escaping,
monitor counts, hidden panels, and absolute HTTP(S) chain-link validation.
The final fixture assertions are `/tmp/opencode/relay-fixture-checks.json`;
the inspected populated screenshot is
`/tmp/opencode/relay-fixture-task-hidden-fixed.png`. These temporary artifacts
are visual evidence, not server-upload receipts.

Installed help confirms SecretSpec 0.21.0 supports the required global and
`run` options. Installed OpenCode help confirms `run --format json` and
`--standalone`. Help inspection does not prove provider or model execution.

## Deliberately unverified or limited

No real Gerrit destination or credentials were supplied for this development
session. No live readiness probe, branch creation, review upload, credential
resolution, model call, or twelve-hour monitoring coverage is claimed.

Automatic corrections cover tip feedback only. Ancestor feedback requires
explicit handoff; review threads are not automatically resolved. Unknown or
unsupported check-provider state blocks automatic completion. Agent processes
are trusted same-user programs, not OS-sandboxed processes.
