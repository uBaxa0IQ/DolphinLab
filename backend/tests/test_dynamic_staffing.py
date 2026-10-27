"""Staffing and authority against disposable PostgreSQL; no model/executor calls."""

import asyncio
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import test_research_storage as storage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from dolphinlab.agents.engine import PlatformRuntime
from dolphinlab.domain.policy import ProjectPolicy
from dolphinlab.infrastructure.database import ResearchObject, Run, Session
from dolphinlab.infrastructure.store import PostgresStore
from dolphinlab.research.config import Config
from dolphinlab.research.roles import prompt
from dolphinlab.research.runtime import Runtime
from dolphinlab.research.tools import tools_for


class FakeJobs:
    def __init__(self, config, store, notify):
        self.config, self.store, self.tasks = config, store, {}
        self.used_cpu, self.used_ram = 0, 0

    def submit(self, owner, path, args, **options):
        ident = f"job-{len(self.store.all('job')) + 1}"
        folder = self.config.work_dir / ident
        folder.mkdir()
        job = {
            "id": ident,
            "owner": owner,
            "state": "succeeded",
            "script": str(path),
            "source_path": str(path),
            "folder": str(folder),
            "submitted": time.time(),
            "metadata": options.get("metadata", {}),
        }
        self.store.put("job", ident, job)
        self.tasks[ident] = asyncio.create_task(asyncio.sleep(0))
        return job

    def cancel(self, ident):
        job = self.store.get("job", ident)
        job["state"] = "cancelled"
        self.store.put("job", ident, job)
        return job


class Harness(PlatformRuntime):
    def __init__(self, config, store):
        self.capabilities = "Synthetic local test; no actual executor"
        self.policy, self.saver, self.platform_run_id = (
            ProjectPolicy().model_dump(),
            MemorySaver(),
            store.run_id,
        )
        self.pause_requested = self.finish_requested = False
        self.current_usage, self.graphs = {}, {}
        Runtime.__init__(
            self,
            config,
            SimpleNamespace(),
            store=store,
            jobs_factory=FakeJobs,
            monitor_factory=lambda _: SimpleNamespace(update=lambda **kw: None),
        )

    async def execute_turn(self, agent, tools):
        raise AssertionError("This test must never call a model")


@unittest.skipUnless(storage.TEST_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class DynamicStaffingTest(unittest.TestCase):
    reset = storage.ResearchStorageTest.reset
    start = storage.ResearchStorageTest.start

    def test_expensive_request_blocks_only_its_agent_not_cheaper_coordinator_work(self):
        from dolphinlab.infrastructure.llm import RequestBudgetExceeded

        team = self.team("data_analyst")
        actor = self.member(team, "data_analyst")

        async def rejected(agent, tools):
            raise RequestBudgetExceeded("Project API budget has insufficient headroom")

        self.rt.execute_turn = rejected
        asyncio.run(self.rt.turn(actor))
        self.assertEqual(actor["state"], "blocked")
        self.assertFalse(self.rt.api_blocked)
        self.assertNotEqual(self.rt.root["state"], "blocked")

    @classmethod
    def setUpClass(cls):
        storage.ResearchStorageTest.setUpClass()

    def setUp(self):
        storage.ResearchStorageTest.setUp(self)
        storage.ResearchStorageTest.seed_inputs(self)
        self.run = self.start()
        self.temp = tempfile.TemporaryDirectory(prefix="dolphinlab-staffing-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.task = self.folder / "task.md"
        self.task.write_text("Synthetic data research task", encoding="utf-8")
        with Session() as db:
            self.profiles = copy.deepcopy(db.get(Run, self.run["id"]).role_catalog)
        self.rt = self.runtime()
        self.addCleanup(lambda: self.rt.store.close())

    def runtime(self, resume=False, max_agents=25, legacy=False):
        config = Config(
            self.task,
            self.folder / "runtime",
            seconds=600,
            model="fake",
            max_agents=max_agents,
            resume=resume,
            role_catalog=[] if legacy else self.profiles,
        )
        return Harness(config, PostgresStore(self.run["id"], config.work_dir))

    def dispatch(self, agent, name, **args):
        return asyncio.run(self.rt.dispatch(agent, name, args))

    def team(self, *roles, coordinator=False):
        return self.dispatch(
            self.rt.root,
            "create_team",
            direction="Test direction",
            goal="Check data",
            members=[{"role": r, "assignment": "Evidence for " + r} for r in roles],
            coordinator=coordinator,
            reason="Capabilities required by this question",
        )

    def member(self, team, role):
        return next(self.rt.agents[m] for m in team["members"] if self.rt.agents[m]["role"] == role)

    def test_single_analyst_reports_directly_without_implicit_staff(self):
        team = self.team("data_analyst")
        self.assertEqual(len(team["members"]), 1)
        self.assertEqual(team["lead"], self.rt.root["id"])
        self.assertIsNone(team["evaluator"])
        analyst = self.member(team, "data_analyst")
        self.assertEqual(analyst["supervisor"], self.rt.root["id"])
        self.assertEqual(len(self.rt.agents), 2)
        self.assertEqual(self.rt.staffing.profile(analyst)["revision"], 3)
        self.assertEqual(
            self.rt.state(analyst)["assignment"]["question"], "Evidence for data_analyst"
        )
        self.dispatch(
            self.rt.root,
            "plan_team",
            team_id=team["id"],
            reason="Direct coordination",
            first_result_in_seconds=30,
        )

    def test_ml_and_analysis_have_different_compositions_and_optional_lead(self):
        analysis = self.team("data_analyst", "evaluator")
        ml = self.team("ml_engineer", "data_engineer", "evaluator", coordinator=True)
        self.assertEqual(len(analysis["members"]), 2)
        self.assertEqual(len(ml["members"]), 4)
        self.assertEqual(self.rt.agents[ml["lead"]]["role"], "team_lead")
        self.assertEqual(self.member(ml, "ml_engineer")["supervisor"], ml["lead"])
        self.assertEqual(len(self.rt.staffing.profiles), 7)

    def test_capacity_is_exact_and_invalid_composition_has_no_effect(self):
        self.rt.config.max_agents = 2
        with self.assertRaises(ValueError):
            self.team("data_analyst", "evaluator")
        self.assertEqual(len(self.rt.agents), 1)
        self.assertEqual(self.rt.store.all("team"), [])
        self.team("data_analyst")
        self.assertEqual(len(self.rt.agents), 2)
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root, "create_team", direction="Extra", goal="No members", reason="x"
            )

    def test_roles_cannot_escalate_tools_or_write_other_workspaces(self):
        team = self.team("data_analyst", "report_editor", "evaluator")
        analyst, editor, evaluator = (
            self.member(team, r) for r in ("data_analyst", "report_editor", "evaluator")
        )
        for actor in (self.rt.root, editor):
            with self.assertRaises(ValueError):
                self.dispatch(actor, "run_python", code="print(1)", purpose="Unauthorized")
        for name, args in (
            ("review_candidate", {}),
            ("register_candidate", {}),
            ("create_team", {}),
        ):
            with self.assertRaises(ValueError):
                self.dispatch(analyst, name, **args)
        with self.assertRaises(ValueError):
            self.dispatch({**analyst, "role": "evaluator"}, "review_candidate")
        with self.assertRaises(ValueError):
            self.dispatch(analyst, "run_python", code="print(1)", purpose="x", role="evaluator")
        with self.assertRaises(ValueError):
            self.dispatch(
                editor, "write_file", path=str(Path(analyst["workspace"]) / "x.md"), content="x"
            )
        result = self.dispatch(editor, "write_file", path="draft.md", content="Attributed draft")
        self.assertTrue(Path(result["path"]).is_file())
        with self.assertRaises(ValueError):
            self.dispatch(
                analyst,
                "publish_finding",
                text="Unverified",
                references=[],
                claim_status="verified",
            )
        own = self.dispatch(evaluator, "publish_finding", text="My claim", references=[])
        with self.assertRaises(ValueError):
            self.dispatch(
                evaluator,
                "assess_finding",
                finding_id=own["id"],
                status="verified",
                text="Self approval",
                references=[],
            )

    def test_job_records_exact_assignment_and_profile(self):
        team = self.team("data_analyst")
        actor = self.member(team, "data_analyst")
        result = self.dispatch(
            actor,
            "run_python",
            code="print(1)",
            purpose="Synthetic job admission",
            wait_for_seconds=0,
        )
        job = self.rt.store.get("job", result["job_id"])
        self.assertEqual(job["metadata"]["assignment_id"], actor["assignment_id"])
        self.assertEqual(job["metadata"]["role_profile_id"], actor["role_profile_id"])
        old_id = actor["assignment_id"]
        self.dispatch(
            self.rt.root, "send_message", to=actor["id"], text="New calculation", kind="assignment"
        )
        self.assertNotEqual(old_id, actor["assignment_id"])
        self.assertEqual(self.rt.store.get("assignment", old_id)["state"], "superseded")
        self.assertEqual(self.rt.store.get("job", job["id"])["metadata"]["assignment_id"], old_id)

    def test_handover_required_for_retirement_and_slot_reuse(self):
        self.rt.config.max_agents = 2
        team = self.team("data_analyst")
        actor = self.member(team, "data_analyst")
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root,
                "retire_specialist",
                team_id=team["id"],
                agent_id=actor["id"],
                reason="Done",
            )
        handover = {
            "summary": "Partial analysis",
            "results": ["Saved table"],
            "open_questions": ["Missing returns"],
            "unsuccessful_approaches": ["No causal evidence"],
        }
        completed = self.dispatch(actor, "complete_assignment", handover=handover)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(self.dispatch(actor, "complete_assignment", handover=handover), completed)
        with self.assertRaises(ValueError):
            self.dispatch(actor, "run_python", code="print(1)", purpose="Unassigned")
        self.dispatch(
            self.rt.root,
            "retire_specialist",
            team_id=team["id"],
            agent_id=actor["id"],
            reason="Handover preserved",
        )
        self.dispatch(
            self.rt.root,
            "add_specialist",
            team_id=team["id"],
            role="report_editor",
            assignment="Draft report",
            reason="Use saved table",
        )
        self.assertEqual(sum(a["state"] != "closed" for a in self.rt.agents.values()), 2)
        self.rt.store.close()
        self.rt = self.runtime(resume=True, max_agents=2)
        self.assertEqual(self.rt.agents[actor["id"]]["state"], "closed")
        self.assertEqual(
            self.rt.store.get("assignment", completed["id"])["handover"]["open_questions"],
            ["Missing returns"],
        )

    def test_reassign_and_restart_preserve_old_assignment_and_session(self):
        team = self.team("data_analyst")
        actor = self.member(team, "data_analyst")
        old = copy.deepcopy(actor)
        root_focus = self.rt.root["focus"]
        self.dispatch(
            self.rt.root,
            "reassign_team",
            team_id=team["id"],
            direction="New scope",
            goal="Inspect returns",
        )
        self.assertEqual(self.rt.root["focus"], root_focus)
        self.assertEqual(actor["focus_revision"], 2)
        self.assertEqual(
            self.rt.store.get("assignment", old["assignment_id"])["state"], "superseded"
        )
        self.dispatch(
            self.rt.root,
            "restart_agent",
            team_id=team["id"],
            agent_id=actor["id"],
            assignment="Recover current evidence",
        )
        self.assertNotEqual(actor["session_id"], old["session_id"])
        self.assertEqual(actor["epoch"], 1)
        self.assertEqual(
            self.rt.store.get("agent_session", old["session_id"])["agent_id"], actor["id"]
        )

    def test_frozen_profiles_and_assignments_reject_storage_and_sql_changes(self):
        profile = self.profiles[0]
        with self.assertRaises(ValueError):
            self.rt.store.put("role_profile", profile["id"], {**profile, "prompt": "elevate"})
        with self.assertRaises(IntegrityError), Session.begin() as db:
            db.execute(
                update(ResearchObject)
                .where(
                    ResearchObject.run_id == self.run["id"], ResearchObject.kind == "role_profile"
                )
                .values(data={"changed": True})
            )
        self.rt.store.close()
        with patch("dolphinlab.research.roles.ROLES", {"tech_lead": "Changed in code"}):
            self.rt = self.runtime(resume=True)
        self.assertEqual(self.rt.staffing.profile(self.rt.root)["prompt"], profile["prompt"])

    def test_team_creation_rolls_back_all_agents_mail_and_assignments_on_failure(self):
        original = self.rt.store.put
        before = {
            kind: len(self.rt.store.all(kind))
            for kind in ("agent", "assignment", "staffing_change")
        }

        def fail(kind, ident, data):
            if kind == "team":
                raise RuntimeError("Simulated DB failure")
            return original(kind, ident, data)

        with patch.object(self.rt.store, "put", side_effect=fail), self.assertRaises(RuntimeError):
            self.team("data_analyst", "evaluator")
        self.assertEqual(len(self.rt.agents), 1)
        for kind, count in before.items():
            self.assertEqual(len(self.rt.store.all(kind)), count)

    def test_legacy_agents_keep_original_prompts_tools_and_history(self):
        root = self.rt.root
        root["history"] = [{"role": "assistant", "content": "Original saved history"}]
        for field in ("role_profile_id", "session_id", "assignment_id", "assignment_revision"):
            root.pop(field, None)
        self.rt.store.put("agent", root["id"], root)
        self.rt.store.close()
        with Session.begin() as db:
            db.execute(
                delete(ResearchObject).where(
                    ResearchObject.run_id == self.run["id"],
                    ResearchObject.kind.in_(
                        ["role_profile", "assignment", "assignment_revision", "agent_session"]
                    ),
                )
            )
        self.rt = self.runtime(resume=True, legacy=True)
        profile = self.rt.staffing.profile(self.rt.root)
        self.assertEqual(profile["revision"], 1)
        self.assertEqual(profile["prompt"], prompt("tech_lead", 1))
        self.assertEqual(profile["tools"], tools_for("tech_lead", 1))
        self.assertEqual(self.rt.root["history"], root["history"])
        self.assertNotIn("data_analyst", self.rt.staffing.profiles)

    def test_shared_reviewer_requires_own_new_verification_job(self):
        ml = self.team("ml_engineer")
        other = self.team("evaluator")
        author, reviewer = self.member(ml, "ml_engineer"), self.member(other, "evaluator")
        source = self.dispatch(
            author, "run_python", code="pass", purpose="Synthetic candidate", wait_for_seconds=0
        )
        artifact = Path(author["workspace"]) / "submission.csv"
        artifact.write_text("prediction\n1\n", encoding="utf-8")
        candidate = self.dispatch(
            author, "register_candidate", job_id=source["job_id"], submission_path=str(artifact)
        )
        self.assertIsNone(candidate["reviewer_id"])
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root,
                "assign_reviewer",
                candidate_id=candidate["id"],
                reviewer_id=author["id"],
                reason="Self review",
            )
        self.dispatch(
            self.rt.root,
            "assign_reviewer",
            candidate_id=candidate["id"],
            reviewer_id=reviewer["id"],
            reason="Shared independent evaluator",
        )
        self.assertEqual(self.rt.staffing.reviewer_load()[0]["pending"], 1)
        self.assertEqual(
            self.dispatch(reviewer, "job_status", job_id=source["job_id"])["job_id"],
            source["job_id"],
        )
        with self.assertRaises(ValueError):
            self.dispatch(reviewer, "cancel_job", job_id=source["job_id"])
        with self.assertRaises(ValueError):
            self.dispatch(
                reviewer,
                "review_candidate",
                candidate_id=candidate["id"],
                job_id=source["job_id"],
                approved=True,
                conclusion="Wrong owner's job",
            )
        verification = self.dispatch(
            reviewer,
            "run_python",
            code="pass",
            purpose="Synthetic verification",
            wait_for_seconds=0,
        )
        reviewed = self.dispatch(
            reviewer,
            "review_candidate",
            candidate_id=candidate["id"],
            job_id=verification["job_id"],
            approved=True,
            conclusion="Synthetic independent evidence",
        )
        self.assertTrue(reviewed["approved"])
        self.assertEqual(reviewed["reviewer"], reviewer["id"])
        self.assertEqual(self.rt.published["candidate_id"], candidate["id"])

    def test_scripted_model_uses_explicit_composition_and_receipt_on_replay(self):
        call = {
            "id": "create-direction",
            "type": "function",
            "function": {
                "name": "create_team",
                "arguments": json.dumps(
                    {
                        "direction": "Analysis",
                        "goal": "Compare regions",
                        "reason": "Only arithmetic needed",
                        "members": [
                            {"role": "data_analyst", "assignment": "Compute regional totals"}
                        ],
                    }
                ),
            },
        }

        class Scripted:
            async def complete(self, messages, tools):
                return {"role": "assistant", "content": None, "tool_calls": [call]}, {
                    "prompt_tokens": 1
                }

        self.rt.client = Scripted()
        self.rt.saver = MemorySaver()
        self.rt.policy = {"limits": {"max_teams": 6}}
        self.rt.platform_run_id = self.run["id"]
        self.rt.pause_requested = self.rt.finish_requested = False
        self.rt.current_usage = {}
        self.rt.execute_turn = MethodType(PlatformRuntime.execute_turn, self.rt)
        for _ in range(2):
            asyncio.run(self.rt.execute_turn(self.rt.root, self.rt.tools_for(self.rt.root)))
        self.assertEqual(len(self.rt.store.all("team")), 1)
        self.assertEqual(len(self.rt.agents), 2)
        receipt = self.rt.store.receipt(self.rt.root["id"], "0:create-direction")
        self.assertEqual(len(receipt["members"]), 1)

    def test_staffing_effect_rolls_back_when_receipt_cannot_commit(self):
        class Scripted:
            async def complete(self, messages, tools):
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "rollback",
                            "type": "function",
                            "function": {
                                "name": "create_team",
                                "arguments": json.dumps(
                                    {
                                        "direction": "Analysis",
                                        "goal": "Check",
                                        "reason": "Needed",
                                        "members": [
                                            {"role": "data_analyst", "assignment": "Calculate"}
                                        ],
                                    }
                                ),
                            },
                        }
                    ],
                }, {}

        self.rt.client, self.rt.saver = Scripted(), MemorySaver()
        self.rt.policy = {"limits": {"max_teams": 6}}
        self.rt.platform_run_id = self.run["id"]
        self.rt.pause_requested = self.rt.finish_requested = False
        self.rt.current_usage = {}
        original = self.rt.store.save_receipt

        def fail_success(agent, ident, result):
            if "error" not in result:
                raise RuntimeError("Simulated receipt failure")
            return original(agent, ident, result)

        with patch.object(self.rt.store, "save_receipt", side_effect=fail_success):
            asyncio.run(
                PlatformRuntime.execute_turn(self.rt, self.rt.root, self.rt.tools_for(self.rt.root))
            )
        self.assertEqual(self.rt.store.all("team"), [])
        self.assertEqual(len(self.rt.agents), 1)
        self.assertEqual(len(self.rt.store.all("assignment")), 1)

    def test_project_team_ceiling_and_pause_are_enforced_for_dynamic_staffing(self):
        self.rt.policy["limits"]["max_teams"] = 1
        team = self.team("data_analyst")
        with self.assertRaises(ValueError):
            self.team("ml_engineer")
        self.rt.pause_requested = True
        with self.assertRaises(ValueError):
            self.dispatch(
                self.rt.root,
                "add_specialist",
                team_id=team["id"],
                role="evaluator",
                assignment="Review",
                reason="Need independent review",
            )
        self.assertEqual(len(self.rt.agents), 2)
