"""Offline end-to-end calculations, review, persistence and scoped report API."""

import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch
from uuid import uuid4

import test_dynamic_staffing as staffing
import test_research_storage as storage
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from dolphinlab.api.schemas import CreateGoal
from dolphinlab.application.results import ResultsService
from dolphinlab.domain.research import GoalSpec
from dolphinlab.infrastructure.database import InputFile, ResearchObject, Run, Session
from dolphinlab.infrastructure.store import PostgresStore
from dolphinlab.research.config import Config
from dolphinlab.research.jobs import JobQueue
from dolphinlab.research.staffing import freeze_profiles


class LocalJobs(JobQueue):
    """Real stdlib Python in a disposable folder; no model and no remote executor."""

    async def _run(self, ident, adopted=False):
        job = self.store.get("job", ident)
        job.update(state="running", started=time.time())
        self.store.put("job", ident, job)
        env = {
            "SystemRoot": os.getenv("SystemRoot", "C:\\Windows"),
            "JOB_DIR": job["folder"],
            "TASK_ROOT": str(self.config.task_path.parent),
            "PYTHONUTF8": "1",
        }
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, job["script"]],
            cwd=job["folder"],
            env=env,
            capture_output=True,
            timeout=8,
        )
        for name, content in (("stdout", result.stdout), ("stderr", result.stderr)):
            (Path(job["folder"]) / (name + ".log")).write_bytes(content)
        job.update(
            state="succeeded" if result.returncode == 0 else "failed",
            finished=time.time(),
            returncode=result.returncode,
        )
        self.store.put("job", ident, job)
        await self.notify(job)


@unittest.skipUnless(storage.TEST_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class AnalyticalResultsTest(unittest.TestCase):
    reset = storage.ResearchStorageTest.reset
    start = storage.ResearchStorageTest.start
    dispatch = staffing.DynamicStaffingTest.dispatch
    team = staffing.DynamicStaffingTest.team
    member = staffing.DynamicStaffingTest.member

    @classmethod
    def setUpClass(cls):
        storage.ResearchStorageTest.setUpClass()

    def runtime(self, resume=False, max_agents=25, legacy=False):
        root = Path(os.environ["RUNS_ROOT"]) / self.run["id"] / "runtime"
        config = Config(
            self.task,
            root,
            seconds=600,
            model="fake",
            resume=resume,
            max_agents=max_agents,
            role_catalog=self.profiles,
            goal_spec={"research_type": "analysis"},
            scope={
                "project_id": self.pid,
                "run_id": self.run["id"],
                "iteration_id": self.run["iteration_id"],
            },
        )
        return staffing.Harness(config, PostgresStore(self.run["id"], root))

    def setUp(self):
        staffing.DynamicStaffingTest.setUp(self)
        self.rt.jobs = LocalJobs(self.rt.config, self.rt.store, self.rt.job_finished)
        self.analysis = self.team("data_analyst", "report_editor", "evaluator")
        self.author = self.member(self.analysis, "data_analyst")
        self.editor = self.member(self.analysis, "report_editor")
        self.reviewer = self.member(self.analysis, "evaluator")
        self.dispatch(
            self.rt.root,
            "declare_delivery",
            kind="analysis",
            criteria=["Месячные итоги", "Корректный рост"],
            reason="Задача анализа",
        )
        self.sales = self.task.parent / "sales.csv"
        raw = b"month,region,amount\n2026-08,A,100000\n2026-08,B,60000\n2026-09,A,80000\n2026-09,B,90000\n"
        self.sales.write_bytes(raw)
        with Session.begin() as db:
            item = db.get(InputFile, db.get(Run, self.run["id"]).inputs[0]["id"])
            item.name, item.sha256, item.size = (
                "sales.csv",
                hashlib.sha256(raw).hexdigest(),
                len(raw),
            )
            self.inputs = [
                {
                    "id": item.id,
                    "name": item.name,
                    "stored_name": item.stored_name,
                    "sha256": item.sha256,
                    "size": item.size,
                }
            ]
            db.get(Run, self.run["id"]).inputs = self.inputs
        self.rt.config.input_versions = self.inputs

    def job(self, actor, code):
        result = self.dispatch(
            actor,
            "run_python",
            code=code,
            purpose="Offline fixture calculation",
            cpus=1,
            ram_mb=128,
            wait_for_seconds=3,
        )
        job = self.rt.store.get("job", result.get("job_id", result.get("id")))
        self.assertEqual(
            job["state"],
            "succeeded",
            (Path(job["folder"]) / "stderr.log").read_text(encoding="utf-8"),
        )
        return job

    def register(self, kind, payload, *, dependencies=None, actor=None, previous=None):
        actor = actor or (self.editor if kind in ("chart", "report") else self.author)
        if actor["role"] == "report_editor":
            path = Path(actor["workspace"]) / (str(uuid4()) + ".json")
            self.dispatch(
                actor, "write_file", path=str(path), content=json.dumps(payload, ensure_ascii=False)
            )
            args = {}
        else:
            code = (
                "import json,os\nfrom pathlib import Path\nPath(os.environ['JOB_DIR'],'payload.json').write_text("
                + repr(json.dumps(payload, ensure_ascii=False))
                + ",encoding='utf-8')"
            )
            job = self.job(actor, code)
            path = Path(job["folder"]) / "payload.json"
            args = {"job_id": job["id"]}
        if previous:
            args["previous_id"] = previous
        return self.dispatch(
            actor,
            "register_result",
            kind=kind,
            title=kind,
            path=str(path),
            dependencies=dependencies or [],
            **args,
        )

    def table(self):
        code = """import csv,json,os
from pathlib import Path
totals={}
with Path(os.environ['TASK_ROOT'],'sales.csv').open() as f:
    rows=list(csv.DictReader(f))
for row in rows: totals[row['month']]=totals.get(row['month'],0)+float(row['amount'])
view={'columns':[{'name':'month','label':'Месяц','type':'string'},{'name':'amount','label':'Выручка','type':'number','unit':'RUB'}],
      'rows':[[k,v] for k,v in sorted(totals.items())],'selection':'Август–сентябрь 2026, все регионы','sample_size':len(rows)}
Path(os.environ['JOB_DIR'],'totals.json').write_text(json.dumps(view,ensure_ascii=False),encoding='utf-8')
"""
        job = self.job(self.author, code)
        return self.dispatch(
            self.author,
            "register_result",
            kind="table",
            title="Выручка по месяцам",
            job_id=job["id"],
            path=str(Path(job["folder"]) / "totals.json"),
        )

    def assess(self, result, status="verified", *, checks=None):
        artifact = self.rt.store.get("artifact", self.rt.results.get(result["id"])["artifact_id"])
        payload = {
            "result_id": result["id"],
            "sha256": result["sha256"],
            "status": status,
            "checks": checks
            or [
                {
                    "name": "Месячные итоги",
                    "passed": status != "refuted",
                    "detail": "Проверено на исходной таблице",
                },
                {"name": "Корректный рост", "passed": True, "detail": "Ограничения описаны"},
            ],
            "conclusion": "Вердикт локальной проверки",
        }
        code = (
            "import json,os\nfrom pathlib import Path\nassert Path("
            + repr(artifact["path"])
            + ").is_file()\nPath(os.environ['JOB_DIR'],'verification.json').write_text("
            + repr(json.dumps(payload, ensure_ascii=False))
            + ",encoding='utf-8')"
        )
        job = self.job(self.reviewer, code)
        return self.dispatch(
            self.reviewer,
            "assess_result",
            result_id=result["id"],
            job_id=job["id"],
            path=str(Path(job["folder"]) / "verification.json"),
        )

    def report(self, blocks):
        return self.register(
            "report",
            {
                "summary": "Выручка выросла с 160000 до 170000 RUB; рост 6,25%.",
                "blocks": [{"result_id": b["id"]} for b in blocks],
                "limitations": ["Причинность не установлена"],
            },
        )

    def test_real_calculation_report_publication_and_analysis_finish_without_submission(self):
        table = self.table()
        self.assertEqual(
            self.rt.results.get(table["id"])["payload"]["rows"],
            [["2026-08", 160000.0], ["2026-09", 170000.0]],
        )
        metric = self.register(
            "metric",
            {
                "value": 6.25,
                "unit": "%",
                "selection": "Сентябрь к августу, все регионы",
                "sample_size": 4,
            },
            dependencies=[table["id"]],
        )
        chart = self.register(
            "chart",
            {
                "type": "bar",
                "table_id": table["id"],
                "x": "month",
                "y": "amount",
                "x_label": "Месяц",
                "y_label": "Выручка, RUB",
                "caption": "Совокупная выручка по месяцам",
            },
        )
        report = self.report([metric, chart])
        for result in (table, metric, chart, report):
            self.assess(result)
        page = self.dispatch(
            self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
        )
        self.assertFalse(page["draft"])
        self.assertEqual(self.rt.delivery()["kind"], "report")
        self.assertIsNone(self.rt.published)
        self.dispatch(
            self.rt.root, "close_team", team_id=self.analysis["id"], reason="Done", mode="immediate"
        )
        finished = self.dispatch(
            self.rt.root, "finish", reason="Declared analytical criteria satisfied"
        )
        self.assertTrue(finished["finished"])
        self.assertFalse((self.task.parent / "submission.csv").exists())

    def test_independent_recomputation_refutes_wrong_denominator(self):
        wrong = self.register(
            "metric",
            {
                "value": 10000 / 170000 * 100,
                "unit": "%",
                "selection": "Сентябрь к августу",
                "sample_size": 4,
            },
        )
        artifact = self.rt.store.get("artifact", wrong["artifact_id"])
        code = (
            """import csv,json,os
from pathlib import Path
rows=list(csv.DictReader(Path(os.environ['TASK_ROOT'],'sales.csv').open()))
aug=sum(float(r['amount']) for r in rows if r['month']=='2026-08')
sep=sum(float(r['amount']) for r in rows if r['month']=='2026-09')
expected=(sep-aug)/aug*100
value=json.loads(Path(TARGET).read_text())['value']
passed=abs(value-expected)<1e-9
evidence={'result_id':RESULT,'sha256':HASH,'status':'verified' if passed else 'refuted',
          'checks':[{'name':'Корректный рост','passed':passed,'detail':f'Ожидалось {expected}, получено {value}'}],
          'conclusion':'Знаменатель должен соответствовать базовому августу'}
Path(os.environ['JOB_DIR'],'verification.json').write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
""".replace("TARGET", repr(artifact["path"]))
            .replace("RESULT", repr(wrong["id"]))
            .replace("HASH", repr(wrong["sha256"]))
        )
        job = self.job(self.reviewer, code)
        verdict = self.dispatch(
            self.reviewer,
            "assess_result",
            result_id=wrong["id"],
            job_id=job["id"],
            path=str(Path(job["folder"]) / "verification.json"),
        )
        self.assertEqual(verdict["status"], "refuted")
        report = self.report([wrong])
        self.assess(report)
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
            )

    def test_unchecked_draft_does_not_finish_and_editor_cannot_publish(self):
        report = self.report([self.table()])
        draft = self.dispatch(
            self.editor, "publish_report", report_id=report["id"], outcome="complete", draft=True
        )
        self.assertTrue(draft["draft"])
        self.assertIsNone(self.rt.delivery())
        with self.assertRaises(ValueError):
            self.dispatch(self.editor, "publish_report", report_id=report["id"], outcome="complete")
        with self.assertRaises(ValueError):
            self.dispatch(self.rt.root, "finish", reason="Only a draft")

    def test_no_self_approval_or_false_verified_check(self):
        own = self.register(
            "metric",
            {"value": 1, "unit": "RUB", "selection": "test", "sample_size": 1},
            actor=self.reviewer,
        )
        with self.assertRaises(ValueError):
            self.assess(own)
        result = self.table()
        with self.assertRaises(ValueError):
            self.assess(
                result, checks=[{"name": "Arithmetic", "passed": False, "detail": "Mismatch"}]
            )

    def test_inconclusive_is_preserved_as_a_distinct_outcome(self):
        finding = self.register(
            "finding",
            {
                "text": "Нет данных о возвратах",
                "limitations": ["Нельзя пересчитать чистую выручку"],
            },
        )
        self.assess(finding)
        report = self.report([finding])
        self.assess(report, status="inconclusive")
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
            )
        page = self.dispatch(
            self.rt.root, "publish_report", report_id=report["id"], outcome="inconclusive"
        )
        self.assertEqual(page["outcome"], "inconclusive")

    def test_hash_corruption_and_later_refutation_revoke_current_publication(self):
        table = self.table()
        self.assess(table)
        report = self.report([table])
        self.assess(report)
        page = self.dispatch(
            self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
        )
        self.assess(table, status="refuted")
        self.assertIsNone(self.rt.results.current())
        self.assertEqual(
            self.rt.store.get("page_revision", page["id"])["statuses"][table["id"]], "verified"
        )
        artifact = self.rt.store.get("artifact", table["artifact_id"])
        Path(artifact["path"]).write_text("{}")
        self.assertEqual(self.rt.results.status(self.rt.results.get(table["id"])), "broken")

    def test_versions_and_sql_immutability(self):
        old = self.register(
            "metric", {"value": 1, "unit": "RUB", "selection": "one", "sample_size": 1}
        )
        new = self.register(
            "metric",
            {"value": 2, "unit": "RUB", "selection": "two", "sample_size": 2},
            previous=old["id"],
        )
        self.assertEqual(new["revision"], 2)
        with self.assertRaises(ValueError):
            self.register(
                "metric",
                {"value": 3, "unit": "RUB", "selection": "three", "sample_size": 3},
                previous=old["id"],
            )
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.execute(
                update(ResearchObject)
                .where(
                    ResearchObject.run_id == self.run["id"],
                    ResearchObject.kind == "result_revision",
                )
                .values(data={"tampered": True})
            )

    def test_chart_validation_rejects_code_remote_sources_and_unknown_columns(self):
        table = self.table()
        spec = {
            "type": "bar",
            "table_id": table["id"],
            "x": "month",
            "y": "amount",
            "x_label": "Месяц",
            "y_label": "RUB",
            "caption": "Выручка",
        }
        for patch in (
            {"url": "https://example.invalid"},
            {"code": "alert(1)"},
            {"y": "unknown"},
            {"type": "html"},
        ):
            with self.assertRaises(ValueError):
                self.register("chart", {**spec, **patch})

    def test_api_scope_evidence_staleness_and_resume(self):
        result = self.table()
        service = ResultsService()
        detail = service.result(self.pid, self.run["id"], result["id"])
        self.assertTrue(detail["applicable"])
        with self.assertRaises(HTTPException):
            service.result(str(uuid4()), self.run["id"], result["id"])
        response = service.artifact(self.pid, self.run["id"], result["artifact_id"])
        self.assertTrue(Path(response.path).is_file())
        with Session.begin() as db:
            db.add(
                InputFile(
                    id=str(uuid4()),
                    project_id=self.pid,
                    name="sales.csv",
                    stored_name=str(uuid4()),
                    sha256="1" * 64,
                    size=1,
                )
            )
        self.assertFalse(service.result(self.pid, self.run["id"], result["id"])["applicable"])
        before = copy.deepcopy(self.rt.results.get(result["id"]))
        self.rt.store.close()
        self.rt = self.runtime(resume=True)
        self.assertEqual(self.rt.results.get(result["id"]), before)

    def test_v2_profile_keeps_its_original_tools(self):
        v2 = freeze_profiles(revision=2)
        self.assertTrue(all(p["revision"] == 2 for p in v2))
        self.assertNotIn("register_result", [t["function"]["name"] for p in v2 for t in p["tools"]])

    def test_source_job_identity_and_exact_review_target_are_enforced(self):
        table = self.table()
        source = self.rt.results.get(table["id"])
        artifact = self.rt.store.get("artifact", source["artifact_id"])
        with self.assertRaises(ValueError):
            self.dispatch(
                self.reviewer,
                "register_result",
                kind="table",
                title="Stolen",
                job_id=source["job_id"],
                path=artifact["source_path"],
            )
        forged = {
            "result_id": table["id"],
            "sha256": "0" * 64,
            "status": "verified",
            "checks": [{"name": "x", "passed": True, "detail": "x"}],
            "conclusion": "Wrong target hash",
        }
        job = self.job(
            self.reviewer,
            "import os\nfrom pathlib import Path\nPath(os.environ['JOB_DIR'],'v.json').write_text("
            + repr(json.dumps(forged))
            + ",encoding='utf-8')",
        )
        with self.assertRaises(ValueError):
            self.dispatch(
                self.reviewer,
                "assess_result",
                result_id=table["id"],
                job_id=job["id"],
                path=str(Path(job["folder"]) / "v.json"),
            )

    def test_damaged_verification_evidence_requires_reassessment(self):
        table = self.table()
        verdict = self.assess(table)
        verification = self.rt.store.get("artifact", verdict["artifact_id"])
        Path(verification["path"]).write_text("{}")
        self.assertEqual(self.rt.results.status(self.rt.results.get(table["id"])), "broken")

    def continuation(self, table):
        with Session.begin() as db:
            db.get(Run, self.run["id"]).status = "completed"
        body = CreateGoal(
            client_id=uuid4(),
            expected_revision=1,
            goal=GoalSpec(
                question="Проверить влияние скидок без повторения месячного расчёта",
                research_type="analysis",
                prior_results=[
                    {
                        "run_id": self.run["id"],
                        "result_id": table["id"],
                        "sha256": table["sha256"],
                        "reason": "Переиспользовать месячные итоги",
                    }
                ],
            ),
        )
        self.research.create_goal(self.pid, body)
        return self.start()

    def test_continuation_freezes_selected_evidence_and_materializes_it_after_restart(self):
        from dolphinlab.agents.session import ResearchSession
        from dolphinlab.infrastructure.database import DATA_ROOT, Project

        table = self.table()
        self.assess(table)
        next_run = self.continuation(table)
        with Session() as db:
            row = db.get(Run, next_run["id"])
            selected = row.project_context["selected_results"][0]
            self.assertEqual(selected["id"], table["id"])
            self.assertEqual(selected["status"], "verified")
            self.assertTrue(selected["applicable_to_inputs"])
            self.assertEqual(row.project_context["previous_runs"][0]["id"], self.run["id"])
            item = db.get(InputFile, row.inputs[0]["id"])
            upload = DATA_ROOT / "projects" / self.pid / "inputs" / item.stored_name
            upload.parent.mkdir(parents=True, exist_ok=True)
            upload.write_bytes(self.sales.read_bytes())
            with mock_patch("dolphinlab.agents.session.RUNS_ROOT", Path(os.environ["RUNS_ROOT"])):
                # New service instances read persisted state; no reference to the previous runtime.
                task = ResearchSession(None, None).materialize(row, db.get(Project, self.pid))
                self.assertIn(
                    "analysis requires registered results", task.read_text(encoding="utf-8")
                )
                frozen = task.parent / "prior-result-1.json"
                self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(), table["sha256"])
                manifest = json.loads(
                    (task.parent / "project-context.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["selected_results"][0]["artifact_path"], str(frozen))
                self.assertTrue((task.parent / "result-schemas.json").is_file())
        # A new run receives no automatic approval or new-run result revisions.
        self.assertEqual(ResultsService().index(self.pid, next_run["id"])["results"], [])
        self.assess(table, "refuted")
        with Session() as db:
            saved = db.get(Run, next_run["id"]).project_context["selected_results"][0]
            self.assertEqual(saved["status"], "verified")

    def test_prior_result_scope_hash_and_integrity_are_enforced(self):
        from dolphinlab.application.result_context import selected_results, validate_prior_results
        from dolphinlab.domain.research import PriorResult

        table = self.table()
        with Session.begin() as db:
            db.get(Run, self.run["id"]).status = "completed"
        ref = PriorResult(
            run_id=self.run["id"], result_id=table["id"], sha256=table["sha256"], reason="Reuse"
        )
        with Session() as db:
            with self.assertRaises(HTTPException):
                validate_prior_results(db, str(uuid4()), [ref])
            with self.assertRaises(HTTPException):
                validate_prior_results(db, self.pid, [ref.model_copy(update={"sha256": "0" * 64})])
            chosen = selected_results(db, self.pid, [ref], set())
            self.assertFalse(chosen[0]["applicable_to_inputs"])
            artifact = self.rt.store.get("artifact", table["artifact_id"])
            Path(artifact["path"]).write_text("{}", encoding="utf-8")
            with self.assertRaises(HTTPException):
                selected_results(db, self.pid, [ref], {f["id"] for f in self.inputs})

    def test_explicit_goal_type_and_declared_criteria_cannot_be_silently_changed(self):
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root,
                "declare_delivery",
                kind="ml",
                criteria=["Other goal"],
                reason="Changed my mind",
            )
        table = self.table()
        self.assess(table)
        report = self.report([table])
        self.assess(
            report,
            checks=[{"name": "Formatting", "passed": True, "detail": "No scientific criteria"}],
        )
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root, "publish_report", report_id=report["id"], outcome="complete"
            )
