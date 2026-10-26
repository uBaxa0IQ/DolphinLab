"""Passive Markdown monitoring; presentation is shared by live and read-only offline views."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from research_jobs import TERMINAL


ROLES = {"tech_lead": "Техлид", "team_lead": "Тимлид", "ml_engineer": "ML-инженер",
         "data_engineer": "Data-инженер", "evaluator": "Evaluator"}
STATES = {"running": "Работает", "ready": "Готов к следующему шагу", "waiting": "Ожидает",
          "closed": "Завершён", "blocked": "Заблокирован", "active": "Активна",
          "draining": "Завершает работу", "archived": "В архиве", "expired": "Лимит времени исчерпан",
          "completed": "Завершён", "stopped": "Остановлен", "succeeded": "Успешно",
          "failed": "Ошибка", "queued": "В очереди", "cancelled": "Отменено",
          "timed_out": "Таймаут", "interrupted": "Прервано", "launching": "Запускается"}


def text(value):
    return "—" if value is None or value == "" else str(value)


def cell(value):
    return text(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def table(headers, rows):
    return ["| " + " | ".join(cell(v) for v in headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *["| " + " | ".join(cell(v) for v in row) + " |" for row in rows], ""]


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def moscow(stamp):
    return datetime.fromtimestamp(stamp, timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M:%S МСК")


def database_snapshot(db, folder):
    def rows(kind):
        return [json.loads(r[0]) for r in db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,))]
    run = rows("run")[0]
    usage_records = [json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE kind='model_usage' ORDER BY seq")]
    usage = [r.get("usage", {}) for r in usage_records]
    all_events = [(kind, json.loads(body), stamp) for kind, body, stamp in db.execute(
        "SELECT kind,data,created FROM events WHERE kind IN ('model_error','driver_error','compaction','monitor_error','tool_finished') ORDER BY seq")]
    errors = [(k, b, t) for k, b, t in all_events if k != "tool_finished" or (isinstance(b.get("result"), dict) and b["result"].get("error"))]
    pointers = rows("evaluation_active")
    jobs = rows("job")
    active = [j for j in jobs if j["state"] in ("running", "launching")]
    remaining = max(0., run["deadline_wall"] - time.time()) if run["status"] not in ("completed", "expired", "stopped") else max(0., run["seconds"] - run["elapsed_seconds"])
    return {"folder": str(folder), "run": run, "teams": rows("team"), "agents": rows("agent"),
            "jobs": jobs, "budget": rows("budget"), "usage_records": usage_records, "portfolio": rows("portfolio"), "stop_decisions": rows("stop_decision"),
            "reports": rows("research_report"), "journal": rows("team_journal"),
            "findings": rows("finding"), "candidates": rows("candidate"),
            "published": next((p for p in rows("publication") if p.get("candidate_id")), None),
            "protocol": pointers[0]["id"] if pointers else None, "remaining": remaining,
            "phase": "finalization" if remaining <= run["reserve"] else "research",
            "used_cpu": sum(j["cpus"] for j in active), "used_ram": sum(j["ram_mb"] for j in active),
            "requests": len(usage), "tokens": sum(int(u.get("total_tokens", 0) or 0) for u in usage),
            "cost": sum(float(u.get("cost", 0) or 0) for u in usage), "errors": errors[-8:],
            "compactions": sum(k == "compaction" for k, _, _ in all_events), "api_blocked": False,
            "api_pause": 0., "reasoning": run.get("reasoning_effort", "low"), "mirror_error": None}


def render_status(s):
    run = s["run"]
    agents = {a["id"]: a for a in s["agents"]}
    finished = run["status"] in ("completed", "expired", "stopped")
    lines = ["# Статус исследовательского запуска", "", f"Обновлено: {moscow(time.time())}", "", "## Обзор", ""]
    lines += table(["Показатель", "Значение"], [
        ["Состояние", STATES.get(run["status"], run["status"])], ["Осталось времени", duration(s["remaining"])],
        ["Фаза", "Работа завершена" if finished else "Финализация" if s["phase"] == "finalization" else "Исследование"],
        ["CPU: занято / доступно", f"{s['used_cpu']} / {run['cpus']}"],
        ["RAM: заявки / бюджет, MiB", f"{s['used_ram']} / {run['ram_mb']}"],
        ["Завершённых запросов LLM", s["requests"]], ["Токенов всего", f"{s['tokens']:,}".replace(",", " ")],
        ["Reported cost, единицы API", f"{s['cost']:.6f}"], ["Reasoning", s["reasoning"]],
        ["API", "Прогон завершён" if finished else "Заблокирован" if s["api_blocked"] else "Доступен"],
        ["Общая пауза API", duration(s["api_pause"])], ["Сжатий контекста", s["compactions"]],
        ["Соглашение оценки", s["protocol"]],
        ["Проверенная публикация", s["published"]["candidate_id"] if s["published"] else "Отсутствует"]])
    if s.get("budget"):
        budget = s["budget"][0]
        lines += table(["Бюджет запуска", "Учтено, ₽", "Резерв активных запросов, ₽", "Маршрут"],
                       [[f"{budget['limit_rub']:.2f} ₽", f"{budget['accounted_rub']:.4f}", f"{budget['reserved_rub']:.4f}", budget["provider"]]])
    lines += ["**Задача**", "", f"`{run['task_path']}`", "", "**Каталог запуска**", "", f"`{s['folder']}`", ""]
    if s.get("portfolio"):
        lines += ["**Текущая стратегия техлида**", "", s["portfolio"][0]["strategy"], ""]
    if s.get("stop_decisions"):
        lines += ["**Обоснование завершения техлидом**", "", s["stop_decisions"][0]["reason"], ""]
    lines += ["## Участники", ""]
    roster = []
    for a in sorted(s["agents"], key=lambda a: (a["role"] != "tech_lead", a["team_id"] or "", a["role"])):
        context = a.get("context_status", {})
        own_usage = [r.get("usage", {}) for r in s.get("usage_records", []) if r.get("agent") == a["id"]]
        last_actual = context.get("last_actual_prompt_tokens")
        if last_actual is None:
            research_usage = [r.get("usage", {}) for r in s.get("usage_records", []) if r.get("agent") == a["id"] and r.get("purpose", "research") == "research"]
            last_actual = research_usage[-1].get("prompt_tokens") if research_usage else None
        roster.append([ROLES.get(a["role"], a["role"]), STATES.get(a["state"], a["state"]), a.get("activity"),
                       last_actual, context.get("estimated_input_tokens") if "system_bytes" in context else "Не измерен после хода",
                       len(own_usage), sum(u.get("prompt_tokens", 0) or 0 for u in own_usage),
                       sum(u.get("completion_tokens", 0) or 0 for u in own_usage), a["failures"]])
    lines += table(["Роль", "Состояние", "Последняя активность" if finished else "Текущая активность",
                    "Последний запрос, вход (API)", "Текущий контекст, ~токенов", "Запросов", "Вход всего", "Выход всего", "Ошибок подряд"], roster)
    for a in s["agents"]:
        lines += [f"### {ROLES.get(a['role'], a['role'])}", "", f"ID: `{a['id']}`", "",
                  "**Текущий фокус**", "", text(a.get("focus")), "",
                  "**Последний архив контекста**", "", text(a.get("last_archive")), ""]
        compaction = a.get("last_compaction")
        if compaction:
            lines += ["**Последнее сжатие памяти, байт UTF-8**", ""]
            lines += table(["До: старые эпизоды + резюме", "После: новое резюме", "Свежий хвост сохранён"],
                           [[compaction["before_memory_bytes"], compaction["summary_bytes"], compaction["retained_tail_bytes"]]])
        retry = a.get("retry_at", 0) - time.monotonic()
        if retry > 0 and not finished:
            lines += [f"Повтор через: {duration(retry)}", ""]
    lines += ["## Направления команд", ""]
    for t in s["teams"]:
        lines += [f"### {t['id']}", ""]
        lines += table(["Поле", "Значение"], [["Состояние", STATES.get(t["state"], t["state"])], ["Ревизия", t["revision"]],
                                                ["Участников", len(t["members"])]])
        for label, key in (("Направление", "direction"), ("Цель", "goal"), ("Область работы", "scope"), ("Исключения", "out_of_scope")):
            lines += [f"**{label}**", "", text(t.get(key)), ""]
        plan = t.get("time_plan", {})
        deadline = min(run["deadline_wall"], t.get("allocation_deadline_wall", run["deadline_wall"]))
        stamp = run["checkpoint_wall"] if finished else time.time()
        team_remaining = max(0., min(s["remaining"], deadline - stamp))
        override = plan.get("phase", "auto")
        finalizing = s["phase"] == "finalization" or override == "finalization" or (
            override == "auto" and plan.get("finalization_reserve_seconds", 0) > 0 and team_remaining <= plan["finalization_reserve_seconds"])
        lines += ["**План времени тимлида**", ""]
        lines += table(["Показатель", "Значение"], [
            ["Остаток выделенного времени", duration(team_remaining)],
            ["Фаза команды", "Финализация" if finalizing else "Исследование"],
            ["Резерв тимлида", duration(plan["finalization_reserve_seconds"]) if "finalization_reserve_seconds" in plan else "Пока не назначен"],
            ["Первый проверенный результат к", moscow(plan["first_result_deadline_wall"]) if plan.get("first_result_deadline_wall") else None],
            ["Следующий пересмотр к", moscow(plan["review_wall"]) if plan.get("review_wall") else None]])
        lines += ["**Основание плана**", "", text(plan.get("reason")), ""]
        journal = [j for j in s.get("journal", []) if j["team_id"] == t["id"]][-5:]
        if journal:
            lines += ["**Последние сообщения в общем журнале**", ""]
            for j in journal:
                lines += [f"- {moscow(j['created'])} · `{j['author']}` · {j['kind']} · {j['claim_status']}", "",
                          text(j["text"]), ""]
    lines += ["## Исследовательские выводы", ""]
    if not s["reports"]:
        lines += ["Структурированных отчётов пока нет; сообщения команды видны в общем журнале выше.", ""]
    for r in s["reports"][-5:]:
        lines += [f"### {r['id']}", "", "**Вопрос**", "", text(r.get("question")), "",
                  "**Что узнали**", "", text(r["learned"]), "", "**Следующий шаг**", "", text(r["next_action"]), ""]
    lines += ["## Кандидаты и проверка", ""]
    if s["candidates"]:
        lines += table(["Кандидат", "Вердикт evaluator", "Подтверждённая схема", "Сопоставим"],
                       [[c["id"], "Принят" if c.get("approved") else "Заблокирован" if c.get("review_state") == "blocked" else "Отклонён" if c.get("reviewer") else "Ожидает вердикта",
                         c.get("review_protocol_id"), "Да" if c.get("comparable") else "Не подтверждено"] for c in s["candidates"]])
        for c in s["candidates"]:
            lines += [f"**Файл `{c['id']}`**", "", f"`{c['submission_path']}`", "",
                      "**Заключение evaluator**", "", text(c.get("conclusion")), ""]
    else:
        lines += ["Кандидатов пока нет.", ""]
    lines += ["## Ошибки и события контекста", ""]
    if not s["errors"]:
        lines += ["В журнале нет соответствующих событий.", ""]
    for kind, e, stamp in reversed(s["errors"]):
        detail = (e.get("result") or {}).get("error") if kind == "tool_finished" else e.get("detail", e.get("archive", e))
        lines += [f"### {moscow(stamp)} · {kind}", "", f"Участник: `{e.get('agent', '—')}`", "",
                  f"Инструмент: `{e.get('tool', '—')}`", "", text(detail), ""]
    if s["mirror_error"]:
        lines += ["**Ошибка зеркала JSONL; оригинал сохранён в SQLite**", "", s["mirror_error"], ""]
    lines += ["## Полная история", ""]
    folder = Path(s["folder"])
    for label, path in (("Лента событий и сообщений", folder / "events.jsonl"), ("Запросы и ответы модели", folder / "requests"),
                        ("Каноническое состояние", folder / "state.sqlite3"), ("Архивы контекста", folder / "transcripts")):
        lines += [f"**{label}**", "", f"`{path}`", ""]
    lines += ["## Python-задания", ""]
    active = [j for j in s["jobs"] if j["state"] not in TERMINAL]
    lines += [f"Всего: {len(s['jobs'])}", "", f"Активных и ожидающих: {len(active)}", ""]
    lines += table(["Задание", "Состояние", "Исполнитель", "Время работы", "CPU", "RAM, MiB", "Exit"],
                   [[j["id"], STATES.get(j["state"], j["state"]), ROLES.get(agents[j["owner"]]["role"], j["owner"]),
                     duration(j.get("finished", time.time()) - j.get("started", j["submitted"])),
                     j["cpus"], j["ram_mb"], j.get("exit_code")] for j in sorted(s["jobs"], key=lambda j: j["submitted"])])
    for j in active:
        lines += [f"### {j['id']}", "", "**Цель задания**", "", text(j.get("metadata", {}).get("purpose")), "",
                  "**Код и логи**", "", f"`{j['folder']}`", ""]
    return "\n".join(lines)


def atomic_write(target, content):
    target = Path(target)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)


def render_database(database, targets):
    database = Path(database).resolve()
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        content = render_status(database_snapshot(db, database.parent))
    finally:
        db.close()
    for target in targets:
        atomic_write(target, content)


class Monitor:
    def __init__(self, runtime):
        self.runtime, self.previous = runtime, 0.

    def update(self, force=False):
        if not force and time.monotonic() - self.previous < 3:
            return
        self.previous = time.monotonic()
        rt = self.runtime
        try:
            snapshot = database_snapshot(rt.store.db, rt.config.work_dir)
            snapshot.update(remaining=rt.config.remaining, phase=rt.config.public()["phase"], used_cpu=rt.jobs.used_cpu,
                            used_ram=rt.jobs.used_ram, api_blocked=rt.api_blocked,
                            api_pause=max(0., getattr(rt.client, "blocked_until", 0) - time.monotonic()),
                            reasoning=rt.config.reasoning_effort, mirror_error=rt.store.log_error)
            content = render_status(snapshot)
            targets = {rt.config.work_dir / "STATUS.md"}
            if rt.config.status_file is not None:
                targets.add(rt.config.status_file)
            for target in targets:
                atomic_write(target, content)
        except (OSError, sqlite3.Error) as error:
            rt.store.event("monitor_error", {"detail": f"{type(error).__name__}: {error}"})
