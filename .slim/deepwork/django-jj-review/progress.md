status: active
Pass 1: runnable Django review core, admin, immutable patchsets, trusted local jj import, security tests.
Pass 2: authenticated project/change browser, patchset diffs, comments, votes, owner state controls.
User approved local MVP under django_review/ based on 01dfd8f0, no upload or repository setup changes.
Local jj stack only; preserve existing infrastructure and skills. Initial working change xyuqlkuzuopopumyrzyotryksqwspkzk.
Normal jj status snapshots were disclosed; preflight used --ignore-working-copy until implementation authorization.
Use Django 5.2 LTS; isolated interpreter /tmp/opencode/gerrit-django-venv/bin/python (Django 5.2.18).
Identity is (project, target branch, full native jj change ID); commit IDs identify immutable patchsets.
No Gerrit trailers, web shell execution, Git receive-pack, submit/merge, or production-readiness claims.
Project owner or members can read; change owner imports; current-patchset votes do not carry forward.
SQLite requires actual serialization, not select_for_update alone. Trusted superuser-only admin.
Oracle design reviewed: immutable-ID command capture, output/time limits, admin protections, bounded writes.
Evidence: core service and real-jj tests, membership/CSRF/escaping tests, final browser smoke where available.
Orchestrator owns broad suite once per pass, source reconciliation, oracle gates and commits.
Next: dispatch core implementation. Upstream source evidence will be documented with exact links.
Known issues: no SSH/Git push transport or branch integration; local operator import only.
