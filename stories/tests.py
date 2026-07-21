from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Claim, DraftResult, NodeKind, Participant, ResultStatus, RunNode, SkipEvent, StoryRun, StoryTemplate
from .outline import numbered_outline, parse_outline, replace_template_from_outline
from .services import (
    PARTICIPANT_COOKIE,
    create_claim,
    create_participant,
    create_run_from_template,
    finalize_if_ready,
    force_complete,
    run_result_rows,
    run_text,
    submit_claim,
)


class StoryRunnerTests(TestCase):
    def setUp(self):
        self.template = StoryTemplate.objects.get()

    def make_run(self, os_name=StoryRun.OS.ANDROID, version="1.2.3", build="456"):
        return create_run_from_template(self.template, os_name, version, build)

    def participant(self, name="Анна"):
        participant, _token = create_participant(name)
        return participant

    def test_seed_contains_complete_story(self):
        self.assertEqual(self.template.nodes.filter(parent=None).count(), 14)
        self.assertEqual(self.template.nodes.filter(kind=NodeKind.CHECK).count(), 95)
        self.assertTrue(self.template.nodes.filter(code="8.1.а", title="Сдача биометрии").exists())
        self.assertTrue(self.template.nodes.filter(code="8.8.12").exists())

    def test_text_outline_builds_hierarchy_and_numbering(self):
        entries = parse_outline(
            "Раздел 1\n"
            "   Сделать то\n"
            "   Сделать это\n"
            "      Сделать это так\n"
            "      Сделать это сяк"
        )
        self.assertEqual([entry["code"] for entry in entries], ["1.", "1.1", "1.2", "1.2.а", "1.2.б"])
        self.assertEqual(entries[0]["kind"], NodeKind.GROUP)
        self.assertEqual(entries[1]["kind"], NodeKind.CHECK)
        self.assertEqual(entries[2]["kind"], NodeKind.GROUP)
        self.assertIn("    1.2.а Сделать это так", numbered_outline(entries))

        replace_template_from_outline(self.template, entries)
        self.assertEqual(self.template.nodes.count(), 5)
        child = self.template.nodes.get(code="1.2.а")
        self.assertEqual(child.parent.code, "1.2")

    def test_text_editor_replaces_template(self):
        session = self.client.session
        session["story_admin"] = True
        session.save()
        page = self.client.get(reverse("stories:manage_template"))
        self.assertContains(page, "Нумерация")
        self.assertContains(page, "outline-active-line", count=2)
        self.assertContains(page, 'wrap="off"', count=2)
        response = self.client.post(
            reverse("stories:manage_template"),
            {"outline": "Раздел\n  Первый пункт\n  Подраздел\n    Вложенный пункт"},
        )
        self.assertRedirects(response, reverse("stories:manage_template"))
        self.assertEqual(self.template.nodes.count(), 4)
        self.assertTrue(self.template.nodes.filter(code="1.2.а", title="Вложенный пункт").exists())

    def test_run_is_snapshot_and_only_one_active_per_os(self):
        run = self.make_run()
        self.assertEqual(run.nodes.filter(kind=NodeKind.CHECK).count(), 95)
        source = self.template.nodes.get(code="1.1")
        source.title = "Изменённый заголовок"
        source.save()
        self.assertNotEqual(run.nodes.get(code="1.1").title, source.title)
        with self.assertRaises(IntegrityError):
            with self.captureOnCommitCallbacks(execute=True):
                self.make_run(build="457")
        ios = self.make_run(StoryRun.OS.IOS)
        self.assertEqual(ios.os, StoryRun.OS.IOS)
        self.assertFalse(ios.nodes.filter(code="5").exists())
        self.assertFalse(ios.nodes.filter(code__startswith="5.").exists())
        self.assertEqual(ios.nodes.filter(kind=NodeKind.CHECK).count(), 92)

    def test_skip_releases_leaf_and_keeps_audit(self):
        run = self.make_run()
        first = self.participant("Анна")
        group = run.nodes.get(code="8.1")
        claim = create_claim(run, first, [group.id])
        items = list(claim.items.select_related("node"))
        payload = {}
        skipped_item = next(item for item in items if item.node.code == "8.1.а")
        for item in items:
            payload[f"action_{item.id}"] = DraftResult.Action.OK
            payload[f"note_{item.id}"] = ""
        payload[f"action_{skipped_item.id}"] = DraftResult.Action.SKIP
        payload[f"note_{skipped_item.id}"] = "Нет тестовых данных"
        submit_claim(claim, payload)

        skipped = run.nodes.get(code="8.1.а")
        self.assertIsNone(skipped.result_status)
        self.assertFalse(hasattr(skipped, "claim_item"))
        event = SkipEvent.objects.get(node=skipped)
        self.assertEqual(event.participant_name, "Анна")
        self.assertEqual(event.reason, "Нет тестовых данных")
        self.assertEqual(run.nodes.get(code="8.1.б").result_status, ResultStatus.OK)
        run.refresh_from_db()
        self.assertEqual(run.state, StoryRun.State.ACTIVE)

        skipped_row = next(row for row in run_result_rows(run) if row["node"].pk == skipped.pk)
        self.assertEqual(skipped_row["status_label"], "Пропуск ⚠️")
        self.assertEqual(skipped_row["note"], "Нет тестовых данных")
        self.assertIn(
            "8.1.а Сдача биометрии — Пропуск ⚠️ — Нет тестовых данных",
            run_text(run),
        )
        select_page = self.client.get(reverse("stories:claim_select", args=[run.public_id]))
        self.assertContains(select_page, "Пропуск")
        self.assertContains(select_page, "Нет тестовых данных")

        second, second_token = create_participant("Борис")
        second_claim = create_claim(run, second, [skipped.id])
        second_client = Client()
        second_client.cookies[PARTICIPANT_COOKIE] = second_token
        work_page = second_client.get(reverse("stories:claim_work", args=[second_claim.public_id]))
        self.assertContains(work_page, "Пропуск · Анна")
        self.assertContains(work_page, "Нет тестовых данных")
        self.assertNotContains(work_page, "<details")
        item = second_claim.items.get()
        submit_claim(second_claim, {f"action_{item.id}": "ok", f"note_{item.id}": ""})
        skipped.refresh_from_db()
        self.assertEqual(skipped.result_status, ResultStatus.OK)
        self.assertEqual(skipped.completed_by_name, "Борис")
        completed_row = next(row for row in run_result_rows(run) if row["node"].pk == skipped.pk)
        self.assertEqual(completed_row["status_label"], "ОК ✅ ⚠️")
        self.assertEqual(completed_row["note"], "Нет тестовых данных")

    def test_incomplete_submit_keeps_entered_drafts(self):
        run = self.make_run()
        claim = create_claim(run, self.participant(), [run.nodes.get(code="1").id])
        first_item = claim.items.select_related("node").order_by("node__position").first()

        with self.assertRaisesMessage(ValueError, "Выберите результат"):
            submit_claim(
                claim,
                {
                    f"action_{first_item.id}": DraftResult.Action.OK,
                    f"note_{first_item.id}": "Уже проверено",
                },
            )

        claim.refresh_from_db()
        draft = DraftResult.objects.get(claim_item=first_item)
        self.assertEqual(claim.state, Claim.State.OPEN)
        self.assertEqual(draft.action, DraftResult.Action.OK)
        self.assertEqual(draft.note, "Уже проверено")

    def test_claim_overlap_is_rejected(self):
        run = self.make_run()
        node = run.nodes.get(code="1.1")
        create_claim(run, self.participant("Первый"), [node.id])
        with self.assertRaisesMessage(ValueError, "заняты"):
            create_claim(run, self.participant("Второй"), [node.id])

    def test_completion_status_and_warning(self):
        run = self.make_run()
        run.nodes.filter(kind=NodeKind.CHECK).update(result_status=ResultStatus.OK)
        node = run.nodes.get(code="1.1")
        node.note = "Незначительное отличие"
        node.save(update_fields=["note"])
        self.assertTrue(finalize_if_ready(run))
        run.refresh_from_db()
        self.assertEqual(run.final_status, ResultStatus.OK)
        self.assertEqual(run.status_label, "ОК ✅ ⚠️")
        self.assertEqual(run.display_label, "✅ ⚠️ Android — 1.2.3 (456)")

    def test_skip_history_adds_run_warning_even_without_reason(self):
        run = self.make_run()
        node = run.nodes.get(code="1.1")
        SkipEvent.objects.create(node=node, participant_name="Анна", reason="")
        run.nodes.filter(kind=NodeKind.CHECK).update(result_status=ResultStatus.OK)

        self.assertTrue(finalize_if_ready(run))
        run.refresh_from_db()
        self.assertEqual(run.final_status, ResultStatus.OK)
        self.assertEqual(run.status_label, "ОК ✅ ⚠️")
        self.assertEqual(run.display_label, "✅ ⚠️ Android — 1.2.3 (456)")

    def test_any_failure_makes_run_failed(self):
        run = self.make_run()
        run.nodes.filter(kind=NodeKind.CHECK).update(result_status=ResultStatus.OK)
        run.nodes.filter(code="1.1").update(
            result_status=ResultStatus.NOT_OK,
            note="Нужно исправить",
        )
        finalize_if_ready(run)
        run.refresh_from_db()
        self.assertEqual(run.final_status, ResultStatus.NOT_OK)
        self.assertEqual(run.status_label, "НЕ ОК ❌ ⚠️")
        self.assertEqual(run.display_label, "❌ ⚠️ Android — 1.2.3 (456)")

    def test_failure_without_notes_or_skips_has_no_warning(self):
        run = self.make_run()
        run.nodes.filter(kind=NodeKind.CHECK).update(result_status=ResultStatus.OK)
        run.nodes.filter(code="1.1").update(result_status=ResultStatus.NOT_OK)

        self.assertTrue(finalize_if_ready(run))
        run.refresh_from_db()
        self.assertEqual(run.status_label, "НЕ ОК ❌")
        self.assertEqual(run.display_label, "❌ Android — 1.2.3 (456)")

    def test_forced_completion_is_failed_and_releases_claim(self):
        run = self.make_run()
        claim = create_claim(run, self.participant(), [run.nodes.get(code="1.1").id])
        force_complete(run)
        run.refresh_from_db()
        claim.refresh_from_db()
        self.assertEqual(run.state, StoryRun.State.COMPLETED)
        self.assertEqual(run.final_status, ResultStatus.NOT_OK)
        self.assertTrue(run.forced)
        self.assertEqual(claim.state, Claim.State.RELEASED)
        self.assertFalse(claim.items.exists())

    def test_participant_cookie_protects_claim(self):
        run = self.make_run()
        node = run.nodes.get(code="1.1")
        response = self.client.post(
            reverse("stories:claim_select", args=[run.public_id]),
            {"display_name": "Анна", "nodes": [node.id]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("story_runner_participant", response.cookies)
        claim = Claim.objects.get()
        response = self.client.get(reverse("stories:claim_work", args=[claim.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-work-kind="group"')
        self.assertContains(response, "Раздел метро")
        self.assertContains(response, 'data-work-depth="1"')
        stranger = Client()
        response = stranger.get(reverse("stories:claim_work", args=[claim.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_active_run_has_public_state_and_social_preview(self):
        run = self.make_run(build="123")
        create_claim(run, self.participant("Анна"), [run.nodes.get(code="1.1").id])

        response = self.client.get(reverse("stories:run_detail", args=[run.public_id]))
        self.assertContains(response, "Активен")
        self.assertContains(response, "Готово 0 из 95")
        self.assertContains(response, "В работе — Анна")
        self.assertContains(response, "Свободен")
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, '🔵 Android — 1.2.3 (123)')
        self.assertContains(response, 'property="og:image"')

        home = self.client.get(reverse("stories:home"))
        self.assertContains(home, "Состояние")
        self.assertContains(home, reverse("stories:run_detail", args=[run.public_id]))

        preview = self.client.get(reverse("stories:run_preview", args=[run.public_id]))
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview["Content-Type"], "image/png")
        self.assertTrue(preview.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_claim_page_uses_compact_run_title_and_cascade_tree(self):
        run = self.make_run(build="123")
        response = self.client.get(reverse("stories:claim_select", args=[run.public_id]))
        self.assertContains(response, "Android — 1.2.3 (123)")
        self.assertNotContains(response, "Android · 1.2.3 · 123")
        self.assertContains(response, 'class="claim-row', count=run.nodes.count())

    @override_settings(ADMIN_PASSWORD="secret")
    def test_management_requires_password_and_csrf(self):
        response = self.client.get(reverse("stories:manage_dashboard"))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("stories:manage_login"), {"password": "wrong"})
        self.assertContains(response, "Неверный пароль")
        response = self.client.post(reverse("stories:manage_login"), {"password": "secret"})
        self.assertRedirects(response, reverse("stories:manage_dashboard"))
        self.assertEqual(self.client.get(reverse("stories:manage_dashboard")).status_code, 200)

        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(reverse("stories:manage_login"), {"password": "secret"}).status_code, 403)

    def test_archive_filter_search_and_public_detail(self):
        run = self.make_run()
        run.nodes.filter(kind=NodeKind.CHECK).update(result_status=ResultStatus.OK)
        finalize_if_ready(run)
        response = self.client.get(reverse("stories:home"), {"os": "android", "q": "1.2.3"})
        self.assertContains(response, "✅ Android — 1.2.3 (456)")
        self.assertNotContains(response, "1 прогон")
        self.assertContains(response, "Открыть отдельно")
        detail = self.client.get(reverse("stories:run_detail", args=[run.public_id]))
        self.assertContains(detail, "1.1 Построение маршрута")
        self.assertContains(detail, "ОК ✅")

    def test_public_result_contains_only_items_and_statuses(self):
        run = self.make_run()
        node = run.nodes.get(code="1.1")
        node.result_status = ResultStatus.OK
        node.note = "Нужно перепроверить"
        node.completed_by_name = "Анна"
        node.save(update_fields=["result_status", "note", "completed_by_name"])
        text = run_text(run)
        self.assertIn(
            "1.1 Построение маршрута — ОК ✅ ⚠️ — Нужно перепроверить",
            text,
        )
        self.assertNotIn("Проверил", text)
        self.assertNotIn("Пропуск", text)
        self.assertNotIn("Примечание", text)
