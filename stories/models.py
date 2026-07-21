import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class NodeKind(models.TextChoices):
    GROUP = "group", "Раздел"
    CHECK = "check", "Проверяемый пункт"


class ResultStatus(models.TextChoices):
    OK = "ok", "ОК"
    NOT_OK = "not_ok", "НЕ ОК"


class StoryTemplate(models.Model):
    name = models.CharField(max_length=200, default="Основная User Story")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class TemplateNode(models.Model):
    template = models.ForeignKey(StoryTemplate, on_delete=models.CASCADE, related_name="nodes")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    code = models.CharField(max_length=40)
    title = models.CharField(max_length=300)
    kind = models.CharField(max_length=10, choices=NodeKind.choices)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["template", "code"], name="unique_template_node_code"),
        ]

    def __str__(self):
        return f"{self.code} {self.title}"


class StoryRun(models.Model):
    class OS(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    class State(models.TextChoices):
        ACTIVE = "active", "Активен"
        COMPLETED = "completed", "Завершён"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    os = models.CharField(max_length=10, choices=OS.choices)
    version = models.CharField(max_length=64)
    build = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)
    final_status = models.CharField(max_length=10, choices=ResultStatus.choices, null=True, blank=True)
    forced = models.BooleanField(default=False)
    template_name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-completed_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["os"],
                condition=Q(state="active"),
                name="one_active_run_per_os",
            )
        ]

    @property
    def has_notes(self):
        return self.nodes.filter(kind=NodeKind.CHECK).exclude(note="").exists()

    @property
    def status_label(self):
        if self.state == self.State.ACTIVE:
            return "АКТИВЕН"
        label = "ОК ✅" if self.final_status == ResultStatus.OK else "НЕ ОК ❌"
        return f"{label} ⚠️" if self.has_notes else label

    @property
    def display_label(self):
        base = f"{self.get_os_display()} — {self.version} ({self.build})"
        if self.state == self.State.ACTIVE:
            return base
        status_icon = "✅" if self.final_status == ResultStatus.OK else "❌"
        warning = " ⚠️" if self.has_notes else ""
        return f"{status_icon} {base}{warning}"

    @property
    def progress(self):
        checks = self.nodes.filter(kind=NodeKind.CHECK)
        return checks.exclude(result_status__isnull=True).count(), checks.count()

    def __str__(self):
        return self.display_label


class RunNode(models.Model):
    run = models.ForeignKey(StoryRun, on_delete=models.CASCADE, related_name="nodes")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    source_node_id = models.PositiveBigIntegerField(null=True, blank=True)
    code = models.CharField(max_length=40)
    title = models.CharField(max_length=300)
    kind = models.CharField(max_length=10, choices=NodeKind.choices)
    position = models.PositiveIntegerField(default=0)
    result_status = models.CharField(max_length=10, choices=ResultStatus.choices, null=True, blank=True)
    note = models.TextField(blank=True)
    completed_by_name = models.CharField(max_length=80, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["run", "code"], name="unique_run_node_code")]

    def __str__(self):
        return f"{self.code} {self.title}"


class Participant(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    token_hash = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.display_name


class Claim(models.Model):
    class State(models.TextChoices):
        OPEN = "open", "В работе"
        SUBMITTED = "submitted", "Отправлено"
        RELEASED = "released", "Освобождено"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    run = models.ForeignKey(StoryRun, on_delete=models.CASCADE, related_name="claims")
    participant = models.ForeignKey(Participant, on_delete=models.PROTECT, related_name="claims")
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.participant} — {self.run}"


class ClaimItem(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="items")
    node = models.OneToOneField(RunNode, on_delete=models.CASCADE, related_name="claim_item")

    def __str__(self):
        return f"{self.claim}: {self.node}"


class DraftResult(models.Model):
    class Action(models.TextChoices):
        OK = "ok", "ОК"
        NOT_OK = "not_ok", "НЕ ОК"
        SKIP = "skip", "Пропустить"

    claim_item = models.OneToOneField(ClaimItem, on_delete=models.CASCADE, related_name="draft")
    action = models.CharField(max_length=10, choices=Action.choices, blank=True)
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class SkipEvent(models.Model):
    node = models.ForeignKey(RunNode, on_delete=models.CASCADE, related_name="skip_events")
    participant_name = models.CharField(max_length=80)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
