import hashlib
import secrets
from collections import defaultdict

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Claim,
    ClaimItem,
    DraftResult,
    NodeKind,
    Participant,
    ResultStatus,
    RunNode,
    SkipEvent,
    StoryRun,
)


PARTICIPANT_COOKIE = "story_runner_participant"


def token_hash(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def resolve_participant(request):
    raw_token = request.COOKIES.get(PARTICIPANT_COOKIE)
    if not raw_token:
        return None
    participant = Participant.objects.filter(token_hash=token_hash(raw_token)).first()
    if participant:
        Participant.objects.filter(pk=participant.pk).update(last_seen_at=timezone.now())
    return participant


def create_participant(display_name):
    raw_token = secrets.token_urlsafe(32)
    participant = Participant.objects.create(
        display_name=display_name.strip()[:80],
        token_hash=token_hash(raw_token),
    )
    return participant, raw_token


@transaction.atomic
def create_run_from_template(template, os_name, version, build):
    run = StoryRun.objects.create(
        os=os_name,
        version=version.strip(),
        build=build.strip(),
        template_name=template.name,
    )
    node_map = {}
    ordered_nodes = [node for node, _depth in tree_rows(list(template.nodes.select_related("parent").all()))]
    for node in ordered_nodes:
        is_android_only = node.kind == NodeKind.GROUP and node.title.strip().startswith("(Android)")
        parent_was_skipped = node.parent_id is not None and node.parent_id not in node_map
        if (os_name == StoryRun.OS.IOS and is_android_only) or parent_was_skipped:
            continue
        node_map[node.id] = RunNode.objects.create(
            run=run,
            parent=node_map.get(node.parent_id),
            source_node_id=node.id,
            code=node.code,
            title=node.title,
            kind=node.kind,
            position=node.position,
        )
    return run


def tree_rows(nodes):
    children = defaultdict(list)
    for node in nodes:
        children[node.parent_id].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda item: (item.position, item.id))
    rows = []

    def visit(parent_id, depth):
        for node in children[parent_id]:
            rows.append((node, depth))
            visit(node.id, depth + 1)

    visit(None, 0)
    return rows


def descendant_check_ids(nodes, selected_ids):
    children = defaultdict(list)
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        children[node.parent_id].append(node.id)
    result = set()

    def collect(node_id):
        node = by_id.get(node_id)
        if not node:
            return
        if node.kind == NodeKind.CHECK:
            result.add(node_id)
            return
        for child_id in children[node_id]:
            collect(child_id)

    for selected_id in selected_ids:
        collect(selected_id)
    return result


@transaction.atomic
def create_claim(run, participant, selected_ids):
    if run.state != StoryRun.State.ACTIVE:
        raise ValueError("Прогон уже завершён")
    nodes = list(run.nodes.all())
    check_ids = descendant_check_ids(nodes, selected_ids)
    free_ids = set(
        RunNode.objects.filter(
            id__in=check_ids,
            result_status__isnull=True,
            claim_item__isnull=True,
        ).values_list("id", flat=True)
    )
    if not free_ids:
        raise ValueError("Выбранные пункты уже заняты или завершены")
    claim = Claim.objects.create(run=run, participant=participant)
    try:
        ClaimItem.objects.bulk_create([ClaimItem(claim=claim, node_id=node_id) for node_id in free_ids])
    except IntegrityError as exc:
        raise ValueError("Часть пунктов уже забрал другой участник") from exc
    return claim


def save_claim_drafts(claim, payload):
    for item in claim.items.select_related("node"):
        action = payload.get(f"action_{item.id}", "")
        note = payload.get(f"note_{item.id}", "").strip()
        if action not in DraftResult.Action.values:
            action = ""
        DraftResult.objects.update_or_create(
            claim_item=item,
            defaults={"action": action, "note": note},
        )


def submit_claim(claim, payload):
    claim.refresh_from_db(fields=["state"])
    if claim.state != Claim.State.OPEN:
        raise ValueError("Это назначение уже закрыто")
    # Черновики сохраняются до валидации. Если часть пунктов не заполнена,
    # пользователь увидит ошибку, но уже введённые значения останутся на месте.
    save_claim_drafts(claim, payload)

    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim.pk)
        if claim.state != Claim.State.OPEN or claim.run.state != StoryRun.State.ACTIVE:
            raise ValueError("Это назначение уже закрыто")
        items = list(claim.items.select_related("node", "draft"))
        if not items or any(not hasattr(item, "draft") or not item.draft.action for item in items):
            raise ValueError("Выберите результат или пропуск для каждого пункта")
        now = timezone.now()
        for item in items:
            draft = item.draft
            node = item.node
            if draft.action == DraftResult.Action.SKIP:
                SkipEvent.objects.create(
                    node=node,
                    participant_name=claim.participant.display_name,
                    reason=draft.note,
                )
            else:
                node.result_status = draft.action
                node.note = draft.note
                node.completed_by_name = claim.participant.display_name
                node.completed_at = now
                node.save(update_fields=["result_status", "note", "completed_by_name", "completed_at"])
        claim.items.all().delete()
        claim.state = Claim.State.SUBMITTED
        claim.submitted_at = now
        claim.save(update_fields=["state", "submitted_at"])
        finalize_if_ready(claim.run)


@transaction.atomic
def release_claim(claim):
    claim = Claim.objects.select_for_update().get(pk=claim.pk)
    if claim.state != Claim.State.OPEN:
        return
    claim.items.all().delete()
    claim.state = Claim.State.RELEASED
    claim.save(update_fields=["state"])


@transaction.atomic
def finalize_if_ready(run):
    run = StoryRun.objects.select_for_update().get(pk=run.pk)
    if run.state != StoryRun.State.ACTIVE:
        return False
    checks = run.nodes.filter(kind=NodeKind.CHECK)
    if checks.filter(result_status__isnull=True).exists():
        return False
    run.final_status = ResultStatus.NOT_OK if checks.filter(result_status=ResultStatus.NOT_OK).exists() else ResultStatus.OK
    run.state = StoryRun.State.COMPLETED
    run.completed_at = timezone.now()
    run.save(update_fields=["final_status", "state", "completed_at"])
    return True


@transaction.atomic
def force_complete(run):
    run = StoryRun.objects.select_for_update().get(pk=run.pk)
    if run.state != StoryRun.State.ACTIVE:
        return
    for claim in run.claims.filter(state=Claim.State.OPEN):
        claim.items.all().delete()
        claim.state = Claim.State.RELEASED
        claim.save(update_fields=["state"])
    run.final_status = ResultStatus.NOT_OK
    run.state = StoryRun.State.COMPLETED
    run.forced = True
    run.completed_at = timezone.now()
    run.save(update_fields=["final_status", "state", "forced", "completed_at"])


def claimed_node_names(run):
    return {
        item.node_id: item.claim.participant.display_name
        for claim in run.claims.filter(state=Claim.State.OPEN).prefetch_related("items", "participant")
        for item in claim.items.all()
    }


def node_status(run, node, claimed_names):
    if node.result_status == ResultStatus.OK:
        return "ОК ✅", "ok"
    if node.result_status == ResultStatus.NOT_OK:
        return "НЕ ОК ❌", "not_ok"
    if run.state == StoryRun.State.ACTIVE and node.id in claimed_names:
        return f"В работе — {claimed_names[node.id]}", "working"
    if run.state == StoryRun.State.ACTIVE:
        return "Свободен", "empty"
    return "БЕЗ РЕЗУЛЬТАТА", "empty"


def run_text(run):
    lines = []
    claimed_names = claimed_node_names(run)
    for node, depth in tree_rows(list(run.nodes.all())):
        indent = "  " * depth
        if node.kind == NodeKind.GROUP:
            line = f"{indent}{node.code} {node.title}"
        else:
            status, _tone = node_status(run, node, claimed_names)
            warning = " ⚠️" if node.note else ""
            line = f"{indent}{node.code} {node.title} — {status}{warning}"
            if node.note:
                line += f" — {node.note}"
        lines.append(line)
    return "\n".join(lines)


def run_result_rows(run):
    rows = []
    claimed_names = claimed_node_names(run)
    for node, depth in tree_rows(list(run.nodes.all())):
        row = {
            "node": node,
            "indent": f"{depth * 1.25:g}rem",
            "is_group": node.kind == NodeKind.GROUP,
            "status_label": "",
            "status_tone": "",
            "note": node.note,
        }
        if node.kind == NodeKind.CHECK:
            row["status_label"], row["status_tone"] = node_status(run, node, claimed_names)
            if node.note:
                row["status_label"] += " ⚠️"
        rows.append(row)
    return rows
