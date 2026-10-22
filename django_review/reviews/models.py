from django.conf import settings
from django.core.validators import MaxLengthValidator, MinLengthValidator, RegexValidator
from django.db import models

jj_id_validator = RegexValidator(r"^[k-z]{32}$", "Expected a 32-character Jujutsu change ID.")
commit_validator = RegexValidator(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", "Expected a full lowercase commit hash.")


class Project(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_review_projects")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="review_projects")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.slug


class Change(models.Model):
    class State(models.TextChoices):
        OPEN = "OPEN", "Open"
        ABANDONED = "ABANDONED", "Abandoned"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="changes")
    target_branch = models.CharField(max_length=255)
    jj_change_id = models.CharField(max_length=32, validators=[jj_id_validator])
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_changes")
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "target_branch", "jj_change_id"), name="unique_project_branch_jj_change")]

    def __str__(self):
        return f"{self.project}:{self.jj_change_id}"


class PatchSet(models.Model):
    change = models.ForeignKey(Change, on_delete=models.PROTECT, related_name="patchsets")
    number = models.PositiveIntegerField()
    commit_id = models.CharField(max_length=64, validators=[commit_validator])
    description = models.TextField(validators=[MaxLengthValidator(20000)])
    diff = models.TextField(validators=[MaxLengthValidator(2_000_000)])
    parents = models.JSONField(default=list)
    uploader = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_patchsets")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("change", "number"), name="unique_change_patchset_number"),
            models.UniqueConstraint(fields=("change", "commit_id"), name="unique_change_patchset_commit"),
        ]
        ordering = ("number",)

    def __str__(self):
        return f"{self.change} patchset {self.number}"


class Review(models.Model):
    patchset = models.ForeignKey(PatchSet, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_reviews")
    score = models.SmallIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("patchset", "reviewer"), name="unique_patchset_reviewer"),
            models.CheckConstraint(condition=models.Q(score__in=(-1, 0, 1)), name="review_score_valid"),
        ]


class Comment(models.Model):
    patchset = models.ForeignKey(PatchSet, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="review_comments")
    body = models.TextField(validators=[MinLengthValidator(1), MaxLengthValidator(20000)])
    created_at = models.DateTimeField(auto_now_add=True)
