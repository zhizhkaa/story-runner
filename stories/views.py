import io
import secrets
from collections import OrderedDict, defaultdict
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count
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
    create_claim,
    create_participant,
    create_run_from_template,
    force_complete,
    release_claim,
    resolve_participant,
    run_result_rows,
    run_text,
    save_claim_drafts,
    submit_claim,
    tree_rows,
    node_visible_note,
)


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
            done = 1 if node.result_status else 0
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
    return rows


@require_GET
def home(request):
    participant = resolve_participant(request)
    active_runs = list(StoryRun.objects.filter(state=StoryRun.State.ACTIVE).order_by("os"))
    for run in active_runs:
        run.done_count, run.total_count = run.progress
        run.open_claims = list(run.claims.filter(state=Claim.State.OPEN).select_related("participant"))
        run.my_claims = [claim for claim in run.open_claims if participant and claim.participant_id == participant.id]

    completed = StoryRun.objects.filter(state=StoryRun.State.COMPLETED)
    os_filter = request.GET.get("os", "")
    query = request.GET.get("q", "").strip().casefold()
    sort = request.GET.get("sort", "newest")
    if os_filter in StoryRun.OS.values:
        completed = completed.filter(os=os_filter)
    runs = list(completed.prefetch_related("nodes__skip_events"))
    if query:
        runs = [run for run in runs if query in run.display_label.casefold()]
    runs.sort(key=lambda run: run.completed_at or run.created_at, reverse=sort != "oldest")

    grouped = OrderedDict()
    for run in runs:
        run.text_body = run_text(run)
        run.result_rows = run_result_rows(run)
        key = (run.os, run.version, run.build)
        if key not in grouped:
            grouped[key] = {
                "os": run.get_os_display(),
                "version": run.version,
                "build": run.build,
                "runs": [],
            }
        grouped[key]["runs"].append(run)

    return render(
        request,
        "stories/home.html",
        {
            "active_runs": active_runs,
            "groups": grouped.values(),
            "os_filter": os_filter,
            "query": request.GET.get("q", ""),
            "sort": sort,
            "participant": participant,
        },
    )


@require_GET
def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def run_detail(request, public_id):
    run = get_object_or_404(StoryRun, public_id=public_id)
    done_count, total_count = run.progress
    assigned_count = run.claims.filter(state=Claim.State.OPEN).aggregate(
        total=Count("items")
    )["total"] or 0
    share_version = f"{run.state}-{done_count}-{assigned_count}-{int((run.completed_at or run.created_at).timestamp())}"
    detail_url = request.build_absolute_uri(reverse("stories:run_detail", args=[run.public_id]))
    share_url = f"{detail_url}?v={share_version}"
    preview_url = request.build_absolute_uri(reverse("stories:run_preview", args=[run.public_id]))
    preview_url = f"{preview_url}?v={share_version}"
    if run.state == StoryRun.State.ACTIVE:
        share_title = f"🔵 {run.get_os_display()} — {run.version} ({run.build})"
        share_description = f"Активен: готово {done_count} из {total_count}, в работе {assigned_count}."
    else:
        share_title = run.display_label
        result = "все пункты пройдены" if run.final_status == ResultStatus.OK else "есть ошибки"
        share_description = f"Завершён: {done_count} из {total_count}, {result}."
    return render(
        request,
        "stories/run_detail.html",
        {
            "run": run,
            "text_body": run_text(run),
            "result_rows": run_result_rows(run),
            "done_count": done_count,
            "total_count": total_count,
            "assigned_count": assigned_count,
            "share_title": share_title,
            "share_description": share_description,
            "share_url": share_url,
            "preview_url": preview_url,
        },
    )


@require_GET
def run_preview(request, public_id):
    from PIL import Image, ImageDraw, ImageFont

    run = get_object_or_404(StoryRun, public_id=public_id)
    done_count, total_count = run.progress
    if run.state == StoryRun.State.ACTIVE:
        accent = "#2563eb"
        status = "ACTIVE"
    elif run.final_status == ResultStatus.OK:
        accent = "#059669"
        status = "PASSED"
    else:
        accent = "#dc2626"
        status = "FAILED"

    image = Image.new("RGB", (1200, 630), "#f4f4f5")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 48, 1152, 582), radius=42, fill="#ffffff", outline="#e4e4e7", width=3)
    draw.ellipse((90, 88, 210, 208), fill=accent)
    if status == "PASSED":
        draw.line((122, 150, 145, 174), fill="#ffffff", width=13)
        draw.line((145, 174, 181, 125), fill="#ffffff", width=13)
    elif status == "FAILED":
        draw.line((126, 124, 176, 174), fill="#ffffff", width=13)
        draw.line((176, 124, 126, 174), fill="#ffffff", width=13)
    else:
        draw.polygon(((137, 122), (137, 178), (180, 150)), fill="#ffffff")

    small = ImageFont.load_default(size=28)
    medium = ImageFont.load_default(size=46)
    large = ImageFont.load_default(size=72)
    draw.text((242, 94), "STORY RUNNER", font=small, fill="#71717a")
    draw.text((242, 142), status, font=medium, fill=accent)
    draw.text((90, 275), run.get_os_display().upper(), font=large, fill="#18181b")
    draw.text((90, 370), f"{run.version}  ({run.build})", font=medium, fill="#3f3f46")
    draw.text((90, 485), f"{done_count} / {total_count} CHECKS", font=small, fill="#71717a")

    for index, width in enumerate((330, 390, 290, 350)):
        y = 280 + index * 58
        draw.rounded_rectangle((730, y, 730 + width, y + 20), radius=10, fill="#e4e4e7")
    progress_width = 420
    draw.rounded_rectangle((730, 500, 730 + progress_width, 520), radius=10, fill="#e4e4e7")
    filled_width = int(progress_width * done_count / total_count) if total_count else 0
    if filled_width:
        draw.rounded_rectangle((730, 500, 730 + filled_width, 520), radius=10, fill=accent)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    response = HttpResponse(output.getvalue(), content_type="image/png")
    if run.state == StoryRun.State.COMPLETED:
        response["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response["Cache-Control"] = "public, max-age=60"
    return response


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
        {"run": run, "rows": annotated_run_tree(run), "participant": participant},
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
                messages.success(request, "Результаты отправлены. Пропущенные пункты доступны другим участникам.")
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
        if action == "force_complete":
            run = get_object_or_404(StoryRun, pk=request.POST.get("run_id"), state=StoryRun.State.ACTIVE)
            force_complete(run)
            messages.success(request, "Прогон досрочно завершён со статусом «НЕ ОК».")
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
            node.note = ""
            node.completed_by_name = ""
            node.completed_at = None
            node.save(update_fields=["result_status", "note", "completed_by_name", "completed_at"])
            messages.success(request, f"Пункт {node.code} снова доступен.")
            return redirect("stories:manage_dashboard")

    active_runs = list(StoryRun.objects.filter(state=StoryRun.State.ACTIVE).prefetch_related("claims__participant", "nodes"))
    for run in active_runs:
        run.done_count, run.total_count = run.progress
        run.tree_rows = annotated_run_tree(run)
        run.open_claims = list(run.claims.filter(state=Claim.State.OPEN).select_related("participant"))
    recent_runs = StoryRun.objects.filter(state=StoryRun.State.COMPLETED)[:10]
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
