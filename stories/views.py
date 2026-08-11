import io
import secrets
from collections import defaultdict
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import (
    Claim,
    NodeKind,
    ResultStatus,
    RunNode,
    StoryRun,
    StoryTemplate,
    TemplateNode,
)
from .outline import OutlineError, numbered_outline, parse_outline, replace_template_from_outline, template_as_outline
from .services import (
    PARTICIPANT_COOKIE,
    complete_run,
    create_claim,
    create_participant,
    create_run_from_template,
    release_claim,
    reopen_run,
    reset_run_nodes,
    resolve_participant,
    run_result_sections,
    run_summary,
    run_text,
    save_claim_drafts,
    submit_claim,
    tree_rows,
    node_visible_note,
)


PREVIEW_REVISION = "preview-v2"


def manage_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.session.get("story_admin"):
            return redirect(f"{reverse('stories:manage_login')}?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapped


def annotated_run_tree(run):
    nodes = list(
        run.nodes.select_related("parent")
        .prefetch_related("skip_events")
        .order_by("position", "id")
    )
    claimed = {
        item.node_id: item.claim.participant.display_name
        for claim in run.claims.filter(state=Claim.State.OPEN).prefetch_related("items", "participant")
        for item in claim.items.all()
    }
    children = defaultdict(list)
    by_id = {node.id: node for node in nodes}
    for node in nodes:
        children[node.parent_id].append(node.id)

    def stats(node_id):
        node = by_id[node_id]
        if node.kind == NodeKind.CHECK:
            done = 1 if node.result_status or node.is_skipped else 0
            busy = 1 if node.id in claimed else 0
            free = 1 if not done and not busy else 0
            return done, busy, free, 1
        total = [stats(child_id) for child_id in children[node_id]]
        return tuple(sum(values) for values in zip(*total)) if total else (0, 0, 0, 0)

    rows = []
    for node, depth in tree_rows(nodes):
        done, busy, free, total = stats(node.id)
        skip_events = list(node.skip_events.all())
        rows.append(
            {
                "node": node,
                "depth": depth,
                "done": done,
                "busy": busy,
                "free": free,
                "total": total,
                "claimable": free > 0,
                "claimed_by": claimed.get(node.id, ""),
                "was_skipped": bool(skip_events),
                "visible_note": node_visible_note(node, skip_events),
            }
        )
    return rows


def annotated_run_sections(run):
    sections = []
    current = None
    for row in annotated_run_tree(run):
        if row["depth"] == 0:
            current = {"heading": row, "rows": [row]}
            sections.append(current)
        elif current is not None:
            current["rows"].append(row)
    return sections


def claim_work_rows(claim, items):
    nodes = list(claim.run.nodes.select_related("parent").prefetch_related("skip_events").all())
    by_id = {node.id: node for node in nodes}
    item_by_node_id = {item.node_id: item for item in items}
    included_ids = set(item_by_node_id)
    for node_id in item_by_node_id:
        parent_id = by_id[node_id].parent_id
        while parent_id:
            included_ids.add(parent_id)
            parent_id = by_id[parent_id].parent_id

    rows = []
    for node, depth in tree_rows(nodes):
        if node.id not in included_ids:
            continue
        skip_events = list(node.skip_events.all())
        rows.append(
            {
                "node": node,
                "depth": depth,
                "item": item_by_node_id.get(node.id),
                "skip_events": skip_events,
                "visible_note": node_visible_note(node, skip_events),
            }
        )
    for index, row in enumerate(rows):
        if row["node"].kind != NodeKind.GROUP:
            row["affected_count"] = 1
            continue
        affected_count = 0
        for descendant in rows[index + 1 :]:
            if descendant["depth"] <= row["depth"]:
                break
            affected_count += descendant["item"] is not None
        row["affected_count"] = affected_count
    return rows


@require_GET
def home(request):
    participant = resolve_participant(request)
    active_runs = list(StoryRun.objects.filter(state=StoryRun.State.ACTIVE).order_by("os"))
    for run in active_runs:
        run.summary = run_summary(run)
        run.done_count, run.total_count = run.summary["done"], run.summary["total"]
        run.open_claims = list(run.claims.filter(state=Claim.State.OPEN).select_related("participant"))
        run.my_claims = [claim for claim in run.open_claims if participant and claim.participant_id == participant.id]

    completed = StoryRun.objects.filter(state=StoryRun.State.COMPLETED)
    os_filter = request.GET.get("os", "")
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "newest")
    if os_filter in StoryRun.OS.values:
        completed = completed.filter(os=os_filter)
    if query:
        query_filter = Q(version__icontains=query) | Q(build__icontains=query) | Q(template_name__icontains=query)
        if query.casefold() in {"android", "андроид"}:
            query_filter |= Q(os=StoryRun.OS.ANDROID)
        if query.casefold() in {"ios", "айос"}:
            query_filter |= Q(os=StoryRun.OS.IOS)
        completed = completed.filter(query_filter)
    completed = completed.order_by("completed_at" if sort == "oldest" else "-completed_at")
    page_obj = Paginator(completed, 20).get_page(request.GET.get("page"))
    runs = list(page_obj.object_list)
    for run in runs:
        run.summary = run_summary(run)

    page_query = request.GET.copy()
    page_query.pop("page", None)

    return render(
        request,
        "stories/home.html",
        {
            "active_runs": active_runs,
            "runs": runs,
            "page_obj": page_obj,
            "page_query": page_query.urlencode(),
            "os_filter": os_filter,
            "query": request.GET.get("q", ""),
            "sort": sort,
            "has_filters": bool(os_filter or query or sort == "oldest"),
            "participant": participant,
        },
    )


@require_GET
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def run_detail(request, public_id):
    run = get_object_or_404(StoryRun, public_id=public_id)
    summary = run_summary(run)
    done_count, total_count = summary["done"], summary["total"]
    assigned_count = run.claims.filter(state=Claim.State.OPEN).aggregate(
        total=Count("items")
    )["total"] or 0
    share_version = (
        f"{PREVIEW_REVISION}-{run.state}-{run.final_status or 'none'}-{done_count}-"
        f"{summary['not_ok']}-{summary['skipped']}-{summary['notes']}-{assigned_count}-"
        f"{int((run.completed_at or run.created_at).timestamp())}"
    )
    detail_url = request.build_absolute_uri(reverse("stories:run_detail", args=[run.public_id]))
    share_url = f"{detail_url}?v={share_version}"
    preview_url = request.build_absolute_uri(reverse("stories:run_preview", args=[run.public_id]))
    preview_url = f"{preview_url}?v={share_version}"
    if run.state == StoryRun.State.ACTIVE:
        if run.is_ready:
            share_title = f"{run.display_label} — ожидает завершения"
            share_description = f"Все {total_count} пунктов заполнены. Ожидает завершения администратором."
        else:
            share_title = f"{run.display_label} — в работе"
            share_description = f"Заполнено {done_count} из {total_count}, в работе {assigned_count}."
    else:
        share_title = f"{run.display_label} — {summary['status'].casefold()}"
        share_description = (
            f"{summary['status']}. Решение администратора: {summary['decision']}. "
            f"Заполнено {done_count} из {total_count}; НЕ ОК: {summary['not_ok']}, "
            f"пропуски: {summary['skipped']}, замечания: {summary['notes']}."
        )
        if run.final_comment:
            share_description += f" {run.final_comment}"
    og_image_alt = (
        f"{summary['status']}: {run.get_os_display()} {run.version}, сборка {run.build}. "
        f"Заполнено {done_count} из {total_count}; НЕ ОК: {summary['not_ok']}, "
        f"пропуски: {summary['skipped']}, замечания: {summary['notes']}."
    )
    return render(
        request,
        "stories/run_detail.html",
        {
            "run": run,
            "text_body": run_text(run),
            "result_sections": run_result_sections(run),
            "summary": summary,
            "done_count": done_count,
            "total_count": total_count,
            "assigned_count": assigned_count,
            "share_title": share_title,
            "share_description": share_description,
            "share_url": share_url,
            "preview_url": preview_url,
            "og_image_alt": og_image_alt,
        },
    )


@require_GET
def run_preview(request, public_id):
    from PIL import Image, ImageDraw

    run = get_object_or_404(StoryRun, public_id=public_id)
    data = _preview_data(run)

    image = Image.new("RGB", (1200, 630), "#f4f4f5")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 48, 1152, 582), radius=42, fill="#ffffff", outline="#e4e4e7", width=3)

    brand_font = _preview_font(26)
    status_font = _preview_font(27, bold=True)
    os_font = _preview_font(32)
    build_font = _preview_font(28)
    progress_font = _preview_font(64, bold=True)
    label_font = _preview_font(24)
    metric_font = _preview_font(44, bold=True)
    metric_label_font = _preview_font(21)

    draw.text((90, 86), "STORY RUNNER", font=brand_font, fill="#52525b")
    status_width = int(draw.textlength(data["status"], font=status_font)) + 68
    status_left = 1110 - status_width
    draw.rounded_rectangle((status_left, 76, 1110, 124), radius=24, fill=data["soft"])
    draw.ellipse((status_left + 20, 93, status_left + 34, 107), fill=data["accent"])
    draw.text((status_left + 46, 83), data["status"], font=status_font, fill=data["accent"])
    draw.line((90, 150, 1110, 150), fill="#e4e4e7", width=2)

    draw.text((90, 190), data["os"], font=os_font, fill="#52525b")
    version_font, version = _preview_fit_text(draw, data["version"], 500, 76, 42)
    draw.text((90, 232), version, font=version_font, fill="#18181b")
    build = _preview_clip_text(draw, f"Сборка {data['build']}", build_font, 500)
    draw.text((90, 335), build, font=build_font, fill="#52525b")

    progress_text = f"{data['done']} / {data['total']}"
    draw.text((650, 195), progress_text, font=progress_font, fill="#18181b")
    draw.text((650, 278), "пунктов заполнено", font=label_font, fill="#52525b")
    progress_width = 460
    draw.rounded_rectangle((650, 332, 650 + progress_width, 350), radius=9, fill="#e4e4e7")
    filled_width = int(progress_width * data["done"] / data["total"]) if data["total"] else 0
    if filled_width:
        draw.rounded_rectangle((650, 332, 650 + filled_width, 350), radius=9, fill=data["accent"])

    draw.line((90, 410, 1110, 410), fill="#e4e4e7", width=2)
    metric_width = 255
    for index, (label, value) in enumerate(data["metrics"]):
        left = 90 + index * metric_width
        if index:
            draw.line((left, 442, left, 540), fill="#e4e4e7", width=2)
        draw.text((left + (18 if index else 0), 438), str(value), font=metric_font, fill="#18181b")
        clipped_label = _preview_clip_text(draw, label, metric_label_font, metric_width - 36)
        draw.text((left + (18 if index else 0), 502), clipped_label, font=metric_label_font, fill="#52525b")

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    response = HttpResponse(output.getvalue(), content_type="image/png")
    if run.state == StoryRun.State.COMPLETED:
        response["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response["Cache-Control"] = "public, max-age=60"
    return response


def _preview_data(run):
    stats = run_summary(run)
    done = stats["done"]
    open_claims = run.claims.filter(state=Claim.State.OPEN)
    assigned = open_claims.aggregate(total=Count("items"))["total"] or 0
    participants = open_claims.values("participant_id").distinct().count()
    free = max(stats["total"] - done - assigned, 0)

    if stats["tone"] == "ready":
        status = stats["status"]
        accent = "#b45309"
        soft = "#fffbeb"
        metrics = (
            ("ОК", stats["ok"]),
            ("НЕ ОК", stats["not_ok"]),
            ("Пропусков", stats["skipped"]),
            ("Замечаний", stats["notes"]),
        )
    elif stats["tone"] == "active":
        status = stats["status"]
        accent = "#1d4ed8"
        soft = "#eff6ff"
        metrics = (
            ("В работе", assigned),
            ("Свободно", free),
            ("Участников", participants),
            ("Пропусков", stats["skipped"]),
        )
    elif stats["tone"] == "ok":
        status = stats["status"]
        accent = "#047857"
        soft = "#ecfdf5"
        metrics = (
            ("ОК", stats["ok"]),
            ("НЕ ОК", stats["not_ok"]),
            ("Пропусков", stats["skipped"]),
            ("Замечаний", stats["notes"]),
        )
    elif stats["tone"] == "warning":
        status = stats["status"]
        accent = "#b45309"
        soft = "#fffbeb"
        metrics = (
            ("ОК", stats["ok"]),
            ("НЕ ОК", stats["not_ok"]),
            ("Пропусков", stats["skipped"]),
            ("Замечаний", stats["notes"]),
        )
    else:
        status = stats["status"]
        accent = "#b91c1c"
        soft = "#fef2f2"
        metrics = (
            ("ОК", stats["ok"]),
            ("НЕ ОК", stats["not_ok"]),
            ("Пропусков", stats["skipped"]),
            ("Замечаний", stats["notes"]),
        )

    return {
        "os": run.get_os_display(),
        "version": run.version,
        "build": run.build,
        "status": status,
        "accent": accent,
        "soft": soft,
        "done": done,
        "total": stats["total"],
        "metrics": metrics,
    }


def _preview_fit_text(draw, text, max_width, max_size, min_size):
    for size in range(max_size, min_size - 1, -2):
        font = _preview_font(size, bold=True)
        if draw.textlength(text, font=font) <= max_width:
            return font, text
    font = _preview_font(min_size, bold=True)
    return font, _preview_clip_text(draw, text, font, max_width)


def _preview_font(size, bold=False):
    from pathlib import Path

    from PIL import ImageFont

    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    mac_filename = "Arial Bold.ttf" if bold else "Arial.ttf"
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/System/Library/Fonts/Supplemental") / mac_filename,
        Path("C:/Windows/Fonts") / ("arialbd.ttf" if bold else "arial.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(path, size=size)
    raise RuntimeError("Не найден шрифт с поддержкой кириллицы для social preview.")


def _preview_clip_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    clipped = text
    while clipped and draw.textlength(clipped + suffix, font=font) > max_width:
        clipped = clipped[:-1]
    return clipped.rstrip() + suffix


@require_http_methods(["GET", "POST"])
def claim_select(request, public_id):
    run = get_object_or_404(StoryRun, public_id=public_id, state=StoryRun.State.ACTIVE)
    participant = resolve_participant(request)
    if request.method == "POST":
        display_name = request.POST.get("display_name", "").strip()
        raw_token = None
        if participant is None:
            if not display_name:
                messages.error(request, "Укажите имя участника.")
                return redirect("stories:claim_select", public_id=run.public_id)
            participant, raw_token = create_participant(display_name)
        elif display_name and display_name != participant.display_name:
            participant.display_name = display_name[:80]
            participant.save(update_fields=["display_name"])
        try:
            selected_ids = [int(value) for value in request.POST.getlist("nodes")]
            claim = create_claim(run, participant, selected_ids)
        except (ValueError, IntegrityError) as exc:
            messages.error(request, str(exc))
            return redirect("stories:claim_select", public_id=run.public_id)
        response = redirect("stories:claim_work", public_id=claim.public_id)
        if raw_token:
            response.set_cookie(
                PARTICIPANT_COOKIE,
                raw_token,
                max_age=31536000,
                httponly=True,
                secure=not settings.DEBUG,
                samesite="Lax",
            )
        return response
    return render(
        request,
        "stories/claim_select.html",
        {"run": run, "sections": annotated_run_sections(run), "participant": participant},
    )


@require_http_methods(["GET", "POST"])
def claim_work(request, public_id):
    claim = get_object_or_404(Claim.objects.select_related("participant", "run"), public_id=public_id)
    participant = resolve_participant(request)
    if not participant or participant.pk != claim.participant_id:
        return HttpResponseForbidden("Это назначение принадлежит другому участнику.")
    if claim.state != Claim.State.OPEN:
        messages.info(request, "Это назначение уже закрыто.")
        return redirect("stories:home")
    if request.method == "POST":
        action = request.POST.get("form_action")
        if action == "release":
            release_claim(claim)
            messages.success(request, "Пункты снова доступны участникам.")
            return redirect("stories:home")
        if action == "save":
            save_claim_drafts(claim, request.POST)
            messages.success(request, "Черновик сохранён.")
            return redirect("stories:claim_work", public_id=claim.public_id)
        if action == "submit":
            try:
                submit_claim(claim, request.POST)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Результаты отправлены.")
                return redirect("stories:home")

    items = list(claim.items.select_related("node").prefetch_related("node__skip_events", "draft"))
    for item in items:
        draft = getattr(item, "draft", None)
        item.draft_action = draft.action if draft else ""
        item.draft_note = draft.note if draft else ""
    return render(
        request,
        "stories/claim_work.html",
        {"claim": claim, "rows": claim_work_rows(claim, items)},
    )


@require_http_methods(["GET", "POST"])
def manage_login(request):
    if request.session.get("story_admin"):
        return redirect("stories:manage_dashboard")
    if request.method == "POST":
        supplied = request.POST.get("password", "")
        if secrets.compare_digest(supplied, settings.ADMIN_PASSWORD):
            request.session.cycle_key()
            request.session["story_admin"] = True
            request.session.set_expiry(43200)
            next_url = request.POST.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("stories:manage_dashboard")
        messages.error(request, "Неверный пароль.")
    return render(request, "stories/manage/login.html", {"next": request.GET.get("next", "")})


@require_POST
def manage_logout(request):
    request.session.flush()
    return redirect("stories:home")


@manage_required
@require_http_methods(["GET", "POST"])
def manage_dashboard(request):
    template = StoryTemplate.objects.first()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_run":
            os_name = request.POST.get("os")
            version = request.POST.get("version", "").strip()
            build = request.POST.get("build", "").strip()
            if os_name not in StoryRun.OS.values or not version or not build:
                messages.error(request, "Заполните ОС, версию и сборку.")
            else:
                try:
                    create_run_from_template(template, os_name, version[:64], build[:64])
                except IntegrityError:
                    messages.error(request, "Для этой ОС уже есть активный прогон.")
                else:
                    messages.success(request, "Активный прогон создан.")
            return redirect("stories:manage_dashboard")
        if action == "complete_run":
            run = get_object_or_404(StoryRun, pk=request.POST.get("run_id"), state=StoryRun.State.ACTIVE)
            final_status = request.POST.get("final_status")
            final_comment = request.POST.get("final_comment", "")
            try:
                complete_run(run, final_status, final_comment)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Прогон завершён.")
            return redirect("stories:manage_dashboard")
        if action == "reopen_run":
            run = get_object_or_404(StoryRun, pk=request.POST.get("run_id"), state=StoryRun.State.COMPLETED)
            try:
                reopen_run(run)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Прогон снова открыт.")
            return redirect("stories:manage_dashboard")
        if action == "delete_run":
            run = get_object_or_404(StoryRun, pk=request.POST.get("run_id"))
            run_label = run.display_label
            run.delete()
            messages.success(request, f"Прогон «{run_label}» удалён.")
            return redirect("stories:manage_dashboard")
        if action == "release_claim":
            claim = get_object_or_404(Claim, pk=request.POST.get("claim_id"), state=Claim.State.OPEN)
            release_claim(claim)
            messages.success(request, "Назначение освобождено.")
            return redirect("stories:manage_dashboard")
        if action == "reset_node":
            node = get_object_or_404(
                RunNode,
                pk=request.POST.get("node_id"),
                run__state=StoryRun.State.ACTIVE,
                kind=NodeKind.CHECK,
            )
            node.result_status = None
            node.is_skipped = False
            node.note = ""
            node.completed_by_name = ""
            node.completed_at = None
            node.save(update_fields=["result_status", "is_skipped", "note", "completed_by_name", "completed_at"])
            messages.success(request, f"Пункт {node.code} снова доступен.")
            return redirect("stories:manage_dashboard")
        if action == "bulk_reset_nodes":
            run = get_object_or_404(StoryRun, pk=request.POST.get("run_id"), state=StoryRun.State.ACTIVE)
            node_ids = request.POST.getlist("node_ids")
            if not node_ids:
                messages.error(request, "Выберите хотя бы один пункт.")
            else:
                reset_count = reset_run_nodes(run, node_ids)
                if reset_count:
                    messages.success(request, f"Сброшено пунктов: {reset_count}.")
                else:
                    messages.error(request, "Выбранные пункты уже сброшены.")
            return redirect("stories:manage_dashboard")

    active_runs = list(StoryRun.objects.filter(state=StoryRun.State.ACTIVE).prefetch_related("claims__participant", "nodes"))
    for run in active_runs:
        run.summary = run_summary(run)
        run.done_count, run.total_count = run.summary["done"], run.summary["total"]
        run.tree_rows = annotated_run_tree(run)
        run.open_claims = list(run.claims.filter(state=Claim.State.OPEN).select_related("participant"))
    recent_runs = list(StoryRun.objects.filter(state=StoryRun.State.COMPLETED)[:20])
    for run in recent_runs:
        run.summary = run_summary(run)
    return render(
        request,
        "stories/manage/dashboard.html",
        {"template": template, "active_runs": active_runs, "recent_runs": recent_runs},
    )


@manage_required
@require_http_methods(["GET", "POST"])
def manage_template(request):
    template = StoryTemplate.objects.first()
    outline = template_as_outline(template)
    if request.method == "POST":
        outline = request.POST.get("outline", "")
        try:
            entries = parse_outline(outline)
            replace_template_from_outline(template, entries)
        except OutlineError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Шаблон сохранён и пронумерован.")
            return redirect("stories:manage_template")
    try:
        preview = numbered_outline(parse_outline(outline))
    except OutlineError:
        preview = ""
    return render(
        request,
        "stories/manage/template.html",
        {"template": template, "outline": outline, "preview": preview},
    )
