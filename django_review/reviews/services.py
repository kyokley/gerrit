import re
import time

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, connections, transaction

from .models import Change, Comment, PatchSet, Project, Review

BRANCH_RE = re.compile(r"^(?![.-])(?!.*(?:\.\.|//|@\{|\\|\s|[\x00-\x20~^:?*\[]))[^/]+(?:/[^/]+)*$|^(?![.-])[^/]+$")
HASH_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
JJ_ID_RE = re.compile(r"^[k-z]{32}$")
MAX_DESCRIPTION = 20_000
MAX_DIFF = 2_000_000


def accessible_projects(user):
    if not user or not user.is_authenticated:
        return Project.objects.none()
    return Project.objects.filter(models_q(user))


def models_q(user):
    from django.db.models import Q
    return Q(owner=user) | Q(members=user)


def get_project_for_user(user, slug):
    return accessible_projects(user).get(slug=slug)


def require_project_access(user, project):
    if not user or not user.is_authenticated or not (project.owner_id == user.pk or project.members.filter(pk=user.pk).exists()):
        raise PermissionDenied("Project access required.")


def _validate_text(value, field, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ValidationError({field: f"Must be text no longer than {limit} characters."})


def _validate_branch(branch):
    if not isinstance(branch, str) or len(branch) > 255 or not BRANCH_RE.fullmatch(branch):
        raise ValidationError({"target_branch": "Invalid branch name."})
    parts = branch.split("/")
    if any(not p or p.startswith(".") or p.endswith((".", ".lock")) for p in parts):
        raise ValidationError({"target_branch": "Invalid branch name."})


def _validate_commit(commit_id):
    if not isinstance(commit_id, str) or not HASH_RE.fullmatch(commit_id):
        raise ValidationError({"commit_id": "Expected a full lowercase 40- or 64-character hash."})


def _sqlite_write_lock(project_id):
    if connections["default"].vendor == "sqlite":
        Project.objects.filter(pk=project_id).update(name=models_f("name"))


def models_f(name):
    from django.db.models import F
    return F(name)


def _serialized(project_id, operation):
    # SQLite's SELECT FOR UPDATE is a no-op; the no-op UPDATE obtains its database write lock.
    for attempt in range(3):
        try:
            with transaction.atomic():
                _sqlite_write_lock(project_id)
                project = Project.objects.get(pk=project_id)
                return operation(project)
        except OperationalError as exc:
            if connections["default"].vendor != "sqlite" or "locked" not in str(exc).lower() or attempt == 2:
                raise ValidationError("Database is busy; retry the operation.") from exc
            time.sleep(0.05 * (attempt + 1))


def import_patchset(project, actor, target_branch, jj_change_id, commit_id, description, diff, parents):
    _validate_branch(target_branch)
    if not isinstance(jj_change_id, str) or not JJ_ID_RE.fullmatch(jj_change_id):
        raise ValidationError({"jj_change_id": "Expected a full 32-character lowercase Jujutsu change ID."})
    _validate_commit(commit_id)
    _validate_text(description, "description", MAX_DESCRIPTION)
    _validate_text(diff, "diff", MAX_DIFF)
    if not isinstance(parents, list) or len(parents) > 16 or any(not isinstance(p, str) or not HASH_RE.fullmatch(p) for p in parents):
        raise ValidationError({"parents": "Expected a list of at most 16 full lowercase commit hashes."})

    def operation(locked_project):
        require_project_access(actor, locked_project)
        change = Change.objects.filter(project=locked_project, target_branch=target_branch, jj_change_id=jj_change_id).first()
        if change:
            if change.owner_id != actor.pk:
                raise PermissionDenied("Only the change owner may upload a replacement patchset.")
            existing = change.patchsets.filter(commit_id=commit_id).first()
            if existing:
                same = (existing.description == description and existing.diff == diff and existing.parents == parents)
                if same:
                    return existing, False
                raise ValidationError("Commit already exists with conflicting immutable payload.")
            number = (change.patchsets.order_by("-number").values_list("number", flat=True).first() or 0) + 1
        else:
            change = Change.objects.create(project=locked_project, target_branch=target_branch, jj_change_id=jj_change_id, owner=actor)
            number = 1
        return PatchSet.objects.create(change=change, number=number, commit_id=commit_id, description=description, diff=diff, parents=parents, uploader=actor), True

    return _serialized(project.pk, operation)


def _writable_patchset(patchset_id, actor):
    patchset = PatchSet.objects.select_related("change", "change__project").get(pk=patchset_id)
    require_project_access(actor, patchset.change.project)
    if patchset.change.state != Change.State.OPEN:
        raise ValidationError("Change is not open.")
    return patchset


def add_comment(patchset, actor, body):
    _validate_text(body, "body", 20_000)
    if not body.strip():
        raise ValidationError({"body": "Comment cannot be blank."})
    patchset_id = patchset.pk if isinstance(patchset, PatchSet) else patchset
    def operation(project):
        target = _writable_patchset(patchset_id, actor)
        if target.change.project_id != project.pk:
            raise PermissionDenied("Project access required.")
        return Comment.objects.create(patchset=target, author=actor, body=body)
    project_id = PatchSet.objects.values_list("change__project_id", flat=True).get(pk=patchset_id)
    return _serialized(project_id, operation)


def set_review(patchset, actor, score):
    if type(score) is not int or score not in (-1, 0, 1):
        raise ValidationError({"score": "Score must be -1, 0, or +1."})
    patchset_id = patchset.pk if isinstance(patchset, PatchSet) else patchset
    project_id = PatchSet.objects.values_list("change__project_id", flat=True).get(pk=patchset_id)
    def operation(project):
        target = _writable_patchset(patchset_id, actor)
        if target.change.project_id != project.pk:
            raise PermissionDenied("Project access required.")
        if score == 1 and target.change.owner_id == actor.pk:
            raise PermissionDenied("Change owner cannot approve their own change.")
        review, _ = Review.objects.update_or_create(patchset=target, reviewer=actor, defaults={"score": score})
        return review
    return _serialized(project_id, operation)


def change_state(change, actor, state):
    if state not in (Change.State.OPEN, Change.State.ABANDONED):
        raise ValidationError({"state": "Invalid change state."})
    change_id = change.pk if isinstance(change, Change) else change
    project_id = Change.objects.values_list("project_id", flat=True).get(pk=change_id)
    def operation(project):
        target = Change.objects.select_for_update().get(pk=change_id)
        if target.project_id != project.pk:
            raise PermissionDenied("Project access required.")
        if target.owner_id != actor.pk and project.owner_id != actor.pk:
            raise PermissionDenied("Only the change or project owner may change state.")
        target.state = state
        target.save(update_fields=("state",))
        return target
    return _serialized(project_id, operation)
