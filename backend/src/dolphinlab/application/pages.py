"""Page composition with immutable history and independent scientific gates."""

import copy
import hashlib
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from dolphinlab.api.dependencies import require_project
from dolphinlab.domain.pages import (
    EditorIdentity,
    PageFeedbackChange,
    PagePublish,
    PageSave,
    parse_page,
)
from dolphinlab.domain.research import request_fingerprint
from dolphinlab.infrastructure.database import (
    EditorJob,
    Event,
    PageFeedback,
    PageRefreshCursor,
    PageRelease,
    PageRevision,
    Project,
    ProjectPage,
    ResearchObject,
    Run,
)
from dolphinlab.research.results import uncovered_criteria

from .base import Service
from .results import ResultsService


def revision_view(row):
    return {
        "id": row.id,
        "revision": row.revision,
        "page": row.spec,
        "author": row.author,
        "feedback_revision": row.feedback_revision,
        "created": row.created.isoformat(),
    }


def page_view(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "run_id": row.run_id,
        "team_id": row.team_id,
        "revision": row.revision,
        "feedback_revision": row.feedback_revision,
        "published_id": row.published_id,
    }


class PageService(Service):
    def lock(self, db, project_id):
        row = db.scalar(
            select(Project).where(Project.id == project_id).with_for_update(key_share=True)
        )
        if not row:
            raise HTTPException(404, "Проект не найден")
        if row.archived:
            raise HTTPException(409, "Проект в архиве")
        return row

    def actor(self, db, project_id, actor):
        if actor is None:
            return {"kind": "user", "id": "workspace-user"}
        if isinstance(actor, EditorIdentity):
            job = db.get(EditorJob, actor.job_id)
            if not job or job.project_id != project_id or job.state != "processing":
                raise HTTPException(403, "Редакторская сборка недоступна")
            return {
                "kind": "editor",
                "id": job.id,
                "run_id": job.run_id,
                "teams": [[t["run_id"], t["id"]] for t in job.snapshot["teams"]],
            }
        run = db.scalar(select(Run).where(Run.id == actor["run_id"], Run.project_id == project_id))
        record = (
            db.scalar(
                select(ResearchObject).where(
                    ResearchObject.run_id == actor["run_id"],
                    ResearchObject.kind == "agent",
                    ResearchObject.object_id == actor["id"],
                )
            )
            if run
            else None
        )
        if not record or record.data["state"] == "closed":
            raise HTTPException(403, "Агент недоступен в проекте")
        profile = db.scalar(
            select(ResearchObject.data).where(
                ResearchObject.run_id == run.id,
                ResearchObject.kind == "role_profile",
                ResearchObject.object_id == record.data["role_profile_id"],
            )
        )
        if not profile or profile["revision"] < 5:
            raise HTTPException(403, "Страницы вне сохранённого профиля агента")
        assignment = db.scalar(
            select(ResearchObject.data).where(
                ResearchObject.run_id == run.id,
                ResearchObject.kind == "assignment",
                ResearchObject.object_id == record.data.get("assignment_id"),
            )
        )
        if not assignment or assignment["state"] != "active":
            raise HTTPException(403, "Для изменения страницы нужно действующее назначение")
        return {
            "kind": "agent",
            "id": record.object_id,
            "run_id": run.id,
            "role": record.data["role"],
            "team_id": record.data.get("team_id"),
        }

    def authorize(self, page, author):
        if author["kind"] in {"user", "system"}:
            return
        if author["kind"] == "editor":
            if page.kind == "main" or [page.run_id, page.team_id] in author["teams"]:
                return
            raise HTTPException(403, "Направление вне редакторской сборки")
        if page.kind == "main":
            allowed = author["role"] in {"tech_lead", "report_editor"}
        else:
            allowed = page.run_id == author["run_id"] and (
                author["role"] == "tech_lead"
                or (
                    author["team_id"] == page.team_id
                    and author["role"] in {"team_lead", "report_editor"}
                )
            )
        if not allowed:
            raise HTTPException(403, "Эта страница принадлежит другой роли или команде")

    def page(self, db, project_id, page_id):
        require_project(db, project_id)
        row = db.scalar(
            select(ProjectPage).where(
                ProjectPage.id == page_id, ProjectPage.project_id == project_id
            )
        )
        if not row:
            raise HTTPException(404, "Страница не найдена в проекте")
        return row

    def replay(self, db, model, project_id, body, extra=""):
        digest = hashlib.sha256((extra + request_fingerprint(body)).encode()).hexdigest()
        row = db.scalar(
            select(model).where(
                model.project_id == project_id, model.request_id == str(body.client_id)
            )
        )
        if row and row.request_hash != digest:
            raise HTTPException(409, "ID операции уже использован для другого содержимого")
        return row, digest

    def validate_sources(self, db, project_id, spec):
        for block in spec.blocks:
            row = db.scalar(
                select(ResearchObject)
                .join(Run, Run.id == ResearchObject.run_id)
                .where(
                    Run.project_id == project_id,
                    Run.id == str(block.run_id),
                    ResearchObject.kind == "result_revision",
                    ResearchObject.object_id == block.result_id,
                )
            )
            if not row:
                raise HTTPException(404, "Результат блока не найден в проекте")
            if row.data["sha256"] != block.sha256:
                raise HTTPException(409, "SHA256 результата не совпадает с выбранной ревизией")
            view = getattr(block, "presentation", None)
            if view and view.view == "chart":
                if row.data["kind"] not in {"table", "dataset"}:
                    raise HTTPException(422, "График требует подготовленную таблицу")
                columns = {c["name"]: c for c in row.data["payload"]["columns"]}
                if (
                    view.chart.x not in columns
                    or view.chart.y not in columns
                    or columns[view.chart.y]["type"] != "number"
                    or (view.chart.type == "scatter" and columns[view.chart.x]["type"] != "number")
                ):
                    raise HTTPException(422, "Оси графика не соответствуют подготовленным данным")
            if block.origin_revision_id:
                origin = db.scalar(
                    select(PageRevision).where(
                        PageRevision.project_id == project_id,
                        PageRevision.id == str(block.origin_revision_id),
                    )
                )
                if not origin or not any(
                    b["run_id"] == str(block.run_id)
                    and b["result_id"] == block.result_id
                    and b["sha256"] == block.sha256
                    for b in origin.spec["blocks"]
                ):
                    raise HTTPException(409, "Исходная страница не содержит эту версию результата")

    def save(self, project_id, body: PageSave, *, actor=None, db=None):
        if db is None:
            with self.sessions.begin() as connection:
                return self.save(project_id, body, actor=actor, db=connection)
        self.lock(db, project_id)
        author = self.actor(db, project_id, actor)
        existing, fingerprint = self.replay(
            db, PageRevision, project_id, body, json.dumps(author, sort_keys=True)
        )
        if existing:
            self.authorize(self.page(db, project_id, existing.page_id), author)
            return {"page_id": existing.page_id, **revision_view(existing)}
        key = "main" if body.kind == "main" else f"team:{body.run_id}:{body.team_id}"
        page = db.scalar(
            select(ProjectPage).where(
                ProjectPage.project_id == project_id, ProjectPage.page_key == key
            )
        )
        if page is None:
            if body.kind == "team":
                team = db.scalar(
                    select(ResearchObject)
                    .join(Run, Run.id == ResearchObject.run_id)
                    .where(
                        Run.project_id == project_id,
                        Run.id == str(body.run_id),
                        ResearchObject.kind == "team",
                        ResearchObject.object_id == body.team_id,
                    )
                )
                if team is None:
                    raise HTTPException(404, "Команда не найдена в указанном запуске")
            page = ProjectPage(
                id=str(uuid4()),
                project_id=project_id,
                page_key=key,
                kind=body.kind,
                run_id=str(body.run_id) if body.run_id else None,
                team_id=body.team_id,
            )
            db.add(page)
            db.flush()
        self.authorize(page, author)
        if page.revision != body.expected_revision:
            raise HTTPException(409, "Черновик изменился; открой актуальную ревизию")
        spec = body.page.model_dump(mode="json")
        pins = page.user_state.get("pins", {})
        previous = db.scalar(
            select(PageRevision).where(
                PageRevision.page_id == page.id, PageRevision.revision == page.revision
            )
        )
        for ident, block in pins.items():
            incoming = next((b for b in spec["blocks"] if b["id"] == ident), None)
            if incoming and incoming != block:
                raise HTTPException(
                    409,
                    "Закреплённый пользователем блок нельзя заменить; сначала сними закрепление",
                )
            if incoming is None:
                position = (
                    next(
                        (i for i, b in enumerate(previous.spec["blocks"]) if b["id"] == ident),
                        len(spec["blocks"]),
                    )
                    if previous
                    else len(spec["blocks"])
                )
                spec["blocks"].insert(min(position, len(spec["blocks"])), copy.deepcopy(block))
        if len(spec["blocks"]) > 20:
            raise HTTPException(409, "Освободи место для закреплённых блоков: предел страницы — 20")
        spec = parse_page(spec)
        self.validate_sources(db, project_id, spec)
        row = self.write_revision(
            db, page, spec.model_dump(mode="json"), author, str(body.client_id), fingerprint
        )
        return {"page_id": page.id, **revision_view(row)}

    def write_revision(self, db, page, spec, author, request_id, fingerprint):
        page.revision += 1
        row = PageRevision(
            id=str(uuid4()),
            project_id=page.project_id,
            page_id=page.id,
            revision=page.revision,
            spec=spec,
            author=author,
            feedback_revision=page.feedback_revision,
            request_id=request_id,
            request_hash=fingerprint,
        )
        db.add(row)
        db.add(
            Event(
                project_id=page.project_id,
                kind="page_draft_saved",
                payload={"page_id": page.id, "revision": page.revision},
            )
        )
        db.flush()
        return row

    def manifest(self, db, project_id, spec, outcome, *, enforce=False):
        readers, sources, notices = {}, {}, []
        accepted = {"verified"} if outcome == "complete" else {"verified", "inconclusive"}
        for block in spec["blocks"]:
            run_id, ident = block["run_id"], block["result_id"]
            if run_id not in readers:
                readers[run_id] = ResultsService(self.sessions).reader(db, project_id, run_id)[0]
            registry = readers[run_id]
            try:
                target = registry.get(ident)
                if target["sha256"] != block["sha256"]:
                    raise ValueError("Result hash changed")
                closure = registry.closure(ident, required=True)
                statuses = {key: registry.status(registry.get(key)) for key in closure}
                for key, status in statuses.items():
                    evidence = registry.get(key)
                    assessment = registry.latest_assessment(key)
                    sources[run_id + ":" + key] = {
                        "run_id": run_id,
                        "result_id": key,
                        "sha256": evidence["sha256"],
                        "status": status,
                        "assessment_id": assessment["id"] if assessment else None,
                    }
                valid = all(status in accepted for status in statuses.values())
                if target["kind"] == "report":
                    assessment = registry.latest_assessment(ident)
                    contract = registry.contract()
                    valid = (
                        valid
                        and contract["kind"] != "auto"
                        and bool(contract["criteria"])
                        and not uncovered_criteria(
                            contract["criteria"], assessment["checks"] if assessment else []
                        )
                    )
                if not valid:
                    if enforce and block["required"]:
                        raise HTTPException(
                            409,
                            "Обязательный блок или его зависимости не прошли независимую проверку",
                        )
                    notices.append(
                        {
                            "block_id": block["id"],
                            "required": block["required"],
                            "reason": "evidence_needs_review",
                        }
                    )
            except (KeyError, ValueError) as error:
                if enforce and block["required"]:
                    raise HTTPException(
                        409, "Доказательство блока отсутствует или повреждено"
                    ) from error
                notices.append(
                    {
                        "block_id": block["id"],
                        "required": block["required"],
                        "reason": "evidence_unavailable",
                    }
                )
            if len(sources) > 100:
                raise HTTPException(413, "Слишком большой граф доказательств для одной страницы")
        return {
            "outcome": outcome,
            "sources": list(sources.values()),
            "notices": notices,
            "needs_review": any(n["required"] for n in notices),
        }

    def publish(self, project_id, page_id, body: PagePublish, *, actor=None, db=None):
        if db is None:
            with self.sessions.begin() as connection:
                return self.publish(project_id, page_id, body, actor=actor, db=connection)
        self.lock(db, project_id)
        page, author = self.page(db, project_id, page_id), self.actor(db, project_id, actor)
        self.authorize(page, author)
        existing, fingerprint = self.replay(
            db, PageRelease, project_id, body, page_id + json.dumps(author, sort_keys=True)
        )
        if existing:
            return self.release_view(db, page, existing)
        if (page.revision, page.feedback_revision) != (
            body.expected_revision,
            body.expected_feedback_revision,
        ):
            raise HTTPException(409, "Черновик или пользовательские правки изменились")
        revision = db.scalar(
            select(PageRevision).where(
                PageRevision.id == str(body.revision_id), PageRevision.page_id == page.id
            )
        )
        if (
            not revision
            or not revision.spec["blocks"]
            or not any(b["required"] for b in revision.spec["blocks"])
        ):
            raise HTTPException(409, "Выбери непустую ревизию с обязательными доказательствами")
        for ident, pinned in page.user_state.get("pins", {}).items():
            if not any(b == pinned for b in revision.spec["blocks"]):
                raise HTTPException(409, "Публикация должна сохранять закреплённые блоки")
        self.validate_sources(db, project_id, parse_page(revision.spec))
        manifest = self.manifest(db, project_id, revision.spec, body.outcome, enforce=True)
        if revision.revision != page.revision:
            revision = self.write_revision(
                db,
                page,
                copy.deepcopy(revision.spec),
                {**author, "restored_from": revision.id},
                str(uuid5(NAMESPACE_URL, "page-restore:" + str(body.client_id))),
                fingerprint,
            )
        release = PageRelease(
            id=str(uuid4()),
            project_id=project_id,
            page_id=page.id,
            revision_id=revision.id,
            manifest=manifest,
            user_state=copy.deepcopy(page.user_state),
            author=author,
            request_id=str(body.client_id),
            request_hash=fingerprint,
        )
        db.add(release)
        db.flush()
        page.published_id = release.id
        db.add(
            Event(
                project_id=project_id,
                kind="page_published",
                payload={"page_id": page.id, "release_id": release.id, "revision_id": revision.id},
            )
        )
        return self.release_view(db, page, release)

    def release_view(self, db, page, release):
        revision = db.get(PageRevision, release.revision_id)
        return {
            "id": release.id,
            "revision_id": revision.id,
            "revision": revision.revision,
            "page": revision.spec,
            "manifest": release.manifest,
            "current_evidence": self.manifest(
                db, page.project_id, revision.spec, release.manifest["outcome"]
            ),
            "user_state": release.user_state,
            "author": release.author,
            "created": release.created.isoformat(),
        }

    def feedback(
        self, project_id, page_id, body: PageFeedbackChange, *, expected_page_revision=None
    ):
        with self.sessions.begin() as db:
            self.lock(db, project_id)
            page = self.page(db, project_id, page_id)
            existing, fingerprint = self.replay(
                db,
                PageFeedback,
                project_id,
                body,
                page_id
                + (f":{expected_page_revision}" if expected_page_revision is not None else ""),
            )
            if existing:
                return {"revision": existing.revision, "user_state": existing.user_state}
            if expected_page_revision is not None and page.revision != expected_page_revision:
                raise HTTPException(
                    409, "Черновик обновился. Обновите страницу перед закреплением блока"
                )
            if page.feedback_revision != body.expected_revision:
                raise HTTPException(409, "Пользовательские правки изменились; обнови страницу")
            state = copy.deepcopy(page.user_state)
            block_id = str(body.block_id) if body.block_id else None
            if body.action == "pin":
                draft = db.scalar(
                    select(PageRevision).where(
                        PageRevision.page_id == page.id, PageRevision.revision == page.revision
                    )
                )
                block = (
                    next((b for b in draft.spec["blocks"] if b["id"] == block_id), None)
                    if draft
                    else None
                )
                if not block:
                    raise HTTPException(404, "Блок не найден в черновике")
                state["pins"][block_id] = block
            elif body.action == "unpin":
                state["pins"].pop(block_id, None)
            elif body.action == "comment":
                if block_id:
                    draft = db.scalar(
                        select(PageRevision).where(
                            PageRevision.page_id == page.id, PageRevision.revision == page.revision
                        )
                    )
                    if not draft or not any(b["id"] == block_id for b in draft.spec["blocks"]):
                        raise HTTPException(404, "Блок комментария не найден в черновике")
                state["comments"][str(body.comment_id)] = {
                    "id": str(body.comment_id),
                    "block_id": block_id,
                    "text": body.text.strip(),
                    "author": "workspace-user",
                }
            else:
                state["comments"].pop(str(body.comment_id), None)
            if len(state["pins"]) > 20 or len(state["comments"]) > 100:
                raise HTTPException(400, "Достигнут предел закреплений или комментариев")
            page.feedback_revision += 1
            page.user_state = state
            db.add(
                PageFeedback(
                    id=str(uuid4()),
                    project_id=project_id,
                    page_id=page.id,
                    revision=page.feedback_revision,
                    change=body.model_dump(mode="json"),
                    user_state=state,
                    request_id=str(body.client_id),
                    request_hash=fingerprint,
                )
            )
            return {"revision": page.feedback_revision, "user_state": state}

    def index(self, project_id):
        with self.sessions() as db:
            require_project(db, project_id)
            return [
                {
                    **page_view(p),
                    "title": db.scalar(
                        select(PageRevision.spec).where(
                            PageRevision.page_id == p.id, PageRevision.revision == p.revision
                        )
                    )["title"],
                }
                for p in db.scalars(
                    select(ProjectPage)
                    .where(ProjectPage.project_id == project_id)
                    .order_by(ProjectPage.kind, ProjectPage.created)
                    .limit(100)
                )
            ]

    def read(self, project_id, page_id):
        with self.sessions() as db:
            page = self.page(db, project_id, page_id)
            draft = db.scalar(
                select(PageRevision).where(
                    PageRevision.page_id == page.id, PageRevision.revision == page.revision
                )
            )
            releases = list(
                db.scalars(
                    select(PageRelease)
                    .where(PageRelease.page_id == page.id)
                    .order_by(PageRelease.created.desc())
                    .limit(30)
                )
            )
            revisions = list(
                db.scalars(
                    select(PageRevision)
                    .where(PageRevision.page_id == page.id)
                    .order_by(PageRevision.revision.desc())
                    .limit(30)
                )
            )
            return {
                **page_view(page),
                "draft": revision_view(draft),
                "publication": self.release_view(db, page, db.get(PageRelease, page.published_id))
                if page.published_id
                else None,
                "history": [
                    {"id": r.id, "revision_id": r.revision_id, "created": r.created.isoformat()}
                    for r in releases
                ],
                "revisions": [revision_view(r) for r in revisions],
                "user_state": page.user_state,
            }

    def publication(self, project_id, page_id, release_id):
        with self.sessions() as db:
            page = self.page(db, project_id, page_id)
            release = db.scalar(
                select(PageRelease).where(
                    PageRelease.id == release_id, PageRelease.page_id == page.id
                )
            )
            if not release:
                raise HTTPException(404, "Публикация не найдена")
            return self.release_view(db, page, release)

    def export(self, project_id, page_id, release_id=None):
        selected = (
            self.publication(project_id, page_id, release_id)
            if release_id
            else self.read(project_id, page_id)
        )
        spec = selected["page"] if release_id else selected["draft"]["page"]
        payloads = {}
        for block in spec["blocks"]:
            with self.sessions() as db:
                registry, _ = ResultsService(self.sessions).reader(db, project_id, block["run_id"])
                try:
                    targets = registry.closure(block["result_id"])
                except (KeyError, ValueError):
                    targets = {block["result_id"]}
            for ident in targets:
                key = block["run_id"] + ":" + ident
                if key in payloads:
                    continue
                if len(payloads) >= 100:
                    raise HTTPException(413, "Экспорт содержит слишком много зависимостей")
                try:
                    result = ResultsService(self.sessions).result(
                        project_id, block["run_id"], ident
                    )
                    payloads[key] = {
                        k: result[k]
                        for k in (
                            "id",
                            "kind",
                            "title",
                            "sha256",
                            "status",
                            "applicable",
                            "payload",
                            "assessment",
                            "dependencies",
                        )
                    }
                except HTTPException as error:
                    payloads[key] = {"unavailable": True, "detail": str(error.detail)}
        result = {
            "format": "dolphinlab-page-v1",
            "selection": "publication" if release_id else "draft",
            "document": selected,
            "results": payloads,
        }
        if len(json.dumps(result, ensure_ascii=False).encode()) > 10 * 1024 * 1024:
            raise HTTPException(413, "Экспорт больше 10 МиБ; уменьши представление")
        return result

    def refresh_sources(self):
        """Background updates affect drafts only. Cursor and new revisions commit together."""
        with self.sessions.begin() as db:
            db.execute(
                insert(PageRefreshCursor).values(id="pages", sequence=0).on_conflict_do_nothing()
            )
            cursor = db.get(PageRefreshCursor, "pages", with_for_update=True)
            records = db.scalars(
                select(ResearchObject)
                .where(ResearchObject.seq > cursor.sequence)
                .order_by(ResearchObject.seq)
                .limit(100)
            ).all()
            changed = 0
            for record in records:
                previous = (
                    record.data.get("previous_id") if record.kind == "result_revision" else None
                )
                if previous:
                    run = db.get(Run, record.run_id)
                    project = (
                        db.scalar(
                            select(Project)
                            .where(Project.id == run.project_id)
                            .with_for_update(key_share=True)
                        )
                        if run
                        else None
                    )
                    if not project or project.archived:
                        cursor.sequence = record.seq
                        continue
                    for page in db.scalars(
                        select(ProjectPage).where(ProjectPage.project_id == run.project_id)
                    ):
                        db.refresh(page)
                        revision = db.scalar(
                            select(PageRevision).where(
                                PageRevision.page_id == page.id,
                                PageRevision.revision == page.revision,
                            )
                        )
                        spec, updated = copy.deepcopy(revision.spec), False
                        for block in spec["blocks"]:
                            if (
                                block["run_id"] == run.id
                                and block["result_id"] == previous
                                and block["id"] not in page.user_state["pins"]
                            ):
                                block.update(
                                    result_id=record.object_id,
                                    sha256=record.data["sha256"],
                                    origin_revision_id=None,
                                )
                                updated = True
                        if updated:
                            ident = str(
                                uuid5(NAMESPACE_URL, f"page-refresh:{page.id}:{record.seq}")
                            )
                            self.write_revision(
                                db,
                                page,
                                spec,
                                {
                                    "kind": "system",
                                    "id": "source-refresh",
                                    "source_sequence": record.seq,
                                },
                                ident,
                                hashlib.sha256(
                                    json.dumps(spec, sort_keys=True).encode()
                                ).hexdigest(),
                            )
                            changed += 1
                cursor.sequence = record.seq
            return changed

    def catalog(self, project_id):
        with self.sessions() as db:
            require_project(db, project_id)
            runs = list(
                db.scalars(
                    select(Run)
                    .where(Run.project_id == project_id)
                    .order_by(Run.created.desc())
                    .limit(30)
                )
            )
            teams = list(
                db.scalars(
                    select(ResearchObject)
                    .where(
                        ResearchObject.run_id.in_([r.id for r in runs]),
                        ResearchObject.kind == "team",
                    )
                    .order_by(ResearchObject.seq)
                )
            )
            return {
                "runs": [
                    {
                        "id": r.id,
                        "question": r.goal_snapshot.get("question", "")[:500],
                        "definitions": r.goal_snapshot.get("definitions", {}),
                        "document_sources": r.goal_snapshot.get("document_sources", []),
                        "inputs": [{"name": f["name"], "sha256": f["sha256"]} for f in r.inputs],
                        "status": r.status,
                        "created": r.created.isoformat(),
                    }
                    for r in runs
                ],
                "teams": [
                    {
                        "id": t.object_id,
                        "run_id": t.run_id,
                        "name": t.data.get("direction", t.object_id),
                        "state": t.data.get("state"),
                    }
                    for t in teams
                ],
            }
