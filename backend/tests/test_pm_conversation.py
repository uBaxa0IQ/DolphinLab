"""Deterministic PM and PostgreSQL tests; every model response is local and scripted."""

import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import test_research_storage as storage
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select

from dolphinlab.agents.engine import PlatformRuntime
from dolphinlab.agents.pm_session import PMContextError, PMIntentError
from dolphinlab.agents.project_manager import ProjectManager
from dolphinlab.api.schemas import ControlRun, CreateMessage, CreateProject
from dolphinlab.application.knowledge import KnowledgeService
from dolphinlab.application.messages import MessageService
from dolphinlab.application.pm_workspace import PMWorkspace
from dolphinlab.domain.policy import ConversationPolicy, ProjectPolicy
from dolphinlab.infrastructure.conversation_usage import (
    ConversationBudgetError,
    recover_reservations,
    tracked_conversation,
)
from dolphinlab.infrastructure.database import (
    DATA_ROOT,
    ContextManifest,
    ConversationAccount,
    Directive,
    GoalRevision,
    InputFile,
    Message,
    PMAction,
    ProjectMemory,
    ResearchIteration,
    ResearchMail,
    Run,
    Session,
    WorkspaceSettings,
)
from dolphinlab.infrastructure.llm import APIUnavailable
from dolphinlab.infrastructure.model_clients import ModelClientFactory


def tool(name, arguments, ident="call-1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": ident,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls, self.closed = list(responses), [], False

    async def complete(self, messages, tools=None):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, {"prompt_tokens": 10, "completion_tokens": 5}

    async def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self, responses):
        self.client = FakeClient(responses)

    def create(self, config, run_id=None):
        if run_id:
            raise AssertionError("Conversation must use its own budget")
        return self.client


@unittest.skipUnless(storage.TEST_URL, "Set TEST_DATABASE_URL to a disposable PostgreSQL database")
class ConversationTest(unittest.TestCase):
    # Reuse setup helpers without inheriting and rerunning the stage-one test cases.
    reset = storage.ResearchStorageTest.reset
    new_goal = storage.ResearchStorageTest.new_goal
    new_iteration = storage.ResearchStorageTest.new_iteration
    start = storage.ResearchStorageTest.start
    assert_status = storage.ResearchStorageTest.assert_status

    @classmethod
    def setUpClass(cls):
        storage.ResearchStorageTest.setUpClass()

    def setUp(self):
        storage.ResearchStorageTest.setUp(self)
        self.messages, self.workspace, self.knowledge = (
            MessageService(),
            PMWorkspace(),
            KnowledgeService(),
        )
        self.input_id = self.add_input("region,amount\nA,100000\nB,60000\nA,80000\nB,90000\n")

    def add_input(self, content, name="sales.csv", project_id=None):
        project_id = project_id or self.pid
        ident = str(uuid4())
        raw = content.encode("utf-8")
        folder = DATA_ROOT / "projects" / project_id / "inputs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ident).write_bytes(raw)
        with Session.begin() as db:
            db.add(
                InputFile(
                    id=ident,
                    project_id=project_id,
                    name=name,
                    stored_name=ident,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size=len(raw),
                )
            )
        return ident

    def message(self, content="Какова сумма?", intent="question", project_id=None):
        record = self.messages.save_message(
            project_id or self.pid, CreateMessage(content=content, intent=intent, client_id=uuid4())
        )
        with Session() as db:
            return db.get(Message, record["id"])

    def configure(self, budget=10):
        with Session.begin() as db:
            if not db.get(WorkspaceSettings, "local"):
                db.add(
                    WorkspaceSettings(
                        id="local",
                        base_url="http://fake.invalid/v1",
                        model="fake",
                        encrypted_api_key="fake-not-a-key",
                        pricing={},
                    )
                )
            db.get(WorkspaceSettings, "local").pricing = {
                "currency": "TEST",
                "input_per_million": 1000,
                "output_per_million": 1000,
                "max_input_tokens": 100,
                "max_output_tokens": 100,
            }
        self.projects.update_project_settings(
            self.pid,
            ProjectPolicy(
                conversation=ConversationPolicy(api_budget=budget),
            ),
        )

    def count(self, model):
        with Session() as db:
            return db.scalar(select(func.count()).select_from(model))

    def handle(self, source, responses, saver=None):
        factory = FakeFactory(responses)
        manager = ProjectManager(
            saver or MemorySaver(),
            factory,
            connection_provider=lambda: {
                "model": "fake",
                "base_url": "http://fake.invalid/v1",
                "api_key": "fake-not-a-key",
            },
        )
        asyncio.run(manager.handle(source))
        return factory

    def query(self, source, **arguments):
        return self.workspace.dispatch(
            source.id,
            "query_data",
            {
                "input_id": self.input_id,
                "operation": "aggregate",
                "metric": "sum",
                "column": "amount",
                **arguments,
            },
        )

    def test_question_calculates_without_research_and_replays(self):
        source = self.message()
        first = self.query(source)
        self.assertEqual(first["groups"][0]["value"], "330000")
        self.assertEqual(self.query(source), first)
        self.assertFalse(first["independently_verified"])
        self.assertEqual(self.count(PMAction), 1)
        self.assertEqual(self.count(Run), 0)
        self.assertEqual(self.count(ResearchIteration), 0)
        self.assertEqual(self.count(GoalRevision), 1)

    def test_auto_is_default_and_retry_keeps_original_request_after_routing(self):
        body = CreateMessage(content="Какова сумма?", client_id=uuid4())
        source = self.messages.save_message(self.pid, body)
        self.assertEqual(source["intent"], "auto")
        args = {"intent": "question", "reason": "Пользователь просит сумму по таблице"}
        result = self.workspace.dispatch(source["id"], "route_message", args)
        self.assertEqual(self.workspace.dispatch(source["id"], "route_message", args), result)
        retried = self.messages.save_message(self.pid, body)
        self.assertEqual(retried["intent"], "question")
        self.assertEqual(retried["requested_intent"], "auto")
        self.assertEqual(retried["intent_reason"], args["reason"])
        changed = self.workspace.dispatch(
            source["id"],
            "route_message",
            {
                "intent": "followup",
                "reason": "Передумал",
            },
        )
        self.assertIn("error", changed)
        self.assertEqual(self.count(ResearchIteration), 0)

    def test_auto_question_model_turn_calculates_without_research(self):
        self.configure()
        source = self.message(intent="auto")
        factory = self.handle(
            source,
            [
                tool("route_message", {"intent": "question", "reason": "Нужен расчёт"}),
                tool(
                    "query_data",
                    {
                        "input_id": self.input_id,
                        "operation": "aggregate",
                        "metric": "sum",
                        "column": "amount",
                    },
                    "query",
                ),
                {"role": "assistant", "content": "Сумма — 330000."},
            ],
        )
        self.assertEqual(len(factory.client.calls), 3)
        facts = json.loads(factory.client.calls[-1][1]["content"].split("\n", 1)[1])
        self.assertEqual(facts["source_message"]["intent"], "question")
        self.assertEqual(self.count(Run), 0)
        self.assertEqual(self.count(ResearchIteration), 0)
        self.assertEqual(self.count(Directive), 0)

    def test_auto_clarifies_and_next_message_can_prepare_followup(self):
        self.configure()
        saver = MemorySaver()
        source = self.message("А можно проверить иначе?", "auto")
        self.handle(
            source,
            [
                tool(
                    "route_message", {"intent": "clarification", "reason": "Неясен объём проверки"}
                ),
                {"role": "assistant", "content": "Подготовить новую итерацию с проверкой скидок?"},
            ],
            saver,
        )
        self.assertEqual(self.count(ResearchIteration), 0)
        self.assertIn(
            "error",
            self.workspace.dispatch(
                source.id,
                "prepare_followup",
                {
                    "goal": {"question": "Discounts"},
                },
            ),
        )
        reply = self.message("Да, подготовь", "auto")
        factory = self.handle(
            reply,
            [
                tool(
                    "route_message",
                    {"intent": "followup", "reason": "Подтвердил предложенную проверку"},
                ),
                tool(
                    "prepare_followup",
                    {"goal": {"question": "Проверить влияние скидок"}},
                    "prepare",
                ),
                {"role": "assistant", "content": "Итерация подготовлена."},
            ],
            saver,
        )
        self.assertTrue(
            any(
                "Подготовить новую итерацию" in (item.get("content") or "")
                for item in factory.client.calls[0]
            )
        )
        self.assertEqual(self.count(ResearchIteration), 1)
        self.assertEqual(self.count(Run), 0)

    def test_auto_instruction_targets_only_pinned_run(self):
        self.configure()
        first = self.start()
        source = self.message("Исключи amount из текущего обучения", "auto")
        self.workspace.dispatch(
            source.id,
            "route_message",
            {
                "intent": "instruction",
                "reason": "Изменение текущего запуска",
            },
        )
        result = self.workspace.dispatch(
            source.id, "send_to_tech_lead", {"instruction": source.content}
        )
        self.assertEqual(result["run_id"], first["id"])
        self.runs.control_run(self.pid, first["id"], ControlRun(action="stop"))
        second = self.start()
        late = self.message("Сравни другой алгоритм", "auto")
        self.runs.control_run(self.pid, second["id"], ControlRun(action="stop"))
        self.start()
        failed = self.workspace.dispatch(
            late.id,
            "route_message",
            {
                "intent": "instruction",
                "reason": "Изменение завершившегося запуска",
            },
        )
        self.assertIn("error", failed)
        clarified = self.workspace.dispatch(
            late.id,
            "route_message",
            {
                "intent": "clarification",
                "reason": "Адресат завершился, уточняем продолжение",
            },
        )
        self.assertEqual(clarified["intent"], "clarification")
        self.assertEqual(self.count(Directive), 1)

    def test_manual_modes_cannot_be_rerouted_and_auto_does_not_invent_run(self):
        for intent in ("question", "instruction", "followup"):
            source = self.message(intent=intent)
            result = self.workspace.dispatch(
                source.id,
                "route_message",
                {
                    "intent": "followup",
                    "reason": "Переопределить выбор пользователя",
                },
            )
            self.assertIn("error", result)
        source = self.message("Измени текущий запуск", "auto")
        result = self.workspace.dispatch(
            source.id,
            "route_message",
            {
                "intent": "instruction",
                "reason": "Хочет изменить текущий запуск",
            },
        )
        self.assertIn("error", result)
        self.assertEqual(self.count(Run), 0)
        self.assertEqual(self.count(ResearchIteration), 0)

    def test_auto_requires_routing_before_final_model_answer(self):
        self.configure()
        source = self.message(intent="auto")
        with self.assertRaises(PMIntentError):
            self.handle(source, [{"role": "assistant", "content": "Произвольный ответ"}])
        self.assertEqual(self.count(Message), 1)
        self.assertEqual(self.count(Run), 0)

    def test_question_cannot_create_iteration_or_directive(self):
        source = self.message("Подготовь полное исследование")
        for name, args in (
            ("prepare_followup", {"goal": {"question": "New"}}),
            ("send_to_tech_lead", {"instruction": "Change"}),
        ):
            self.assertIn("error", self.workspace.dispatch(source.id, name, args))
        self.assertEqual(self.count(ResearchIteration), 0)
        self.assertEqual(self.count(Directive), 0)
        self.assertEqual(self.count(GoalRevision), 1)

    def test_followup_is_prepared_once_without_starting(self):
        source = self.message("Проверь скидки", "followup")
        arguments = {"goal": {"question": "Investigate discounts", "constraints": ["RUB"]}}
        first = self.workspace.dispatch(source.id, "prepare_followup", arguments)
        self.assertEqual(first["state"], "prepared")
        self.assertEqual(self.workspace.dispatch(source.id, "prepare_followup", arguments), first)
        self.assertEqual(self.count(ResearchIteration), 1)
        self.assertEqual(self.count(GoalRevision), 2)
        self.assertEqual(self.count(Run), 0)

    def test_stale_followup_does_not_rewrite_goal(self):
        source = self.message("Next", "followup")
        self.research.create_goal(self.pid, self.new_goal())
        result = self.workspace.dispatch(
            source.id, "prepare_followup", {"goal": {"question": "Stale"}}
        )
        self.assertIn("error", result)
        self.assertEqual(self.count(GoalRevision), 2)
        self.assertEqual(self.count(ResearchIteration), 0)

    def test_pinned_input_versions_and_cross_project_access(self):
        source = self.message()
        replacement = self.add_input("region,amount\nC,1\n")
        self.assertEqual(self.query(source)["groups"][0]["value"], "330000")
        self.assertIn("error", self.query(source, input_id=replacement))
        other = self.projects.create_project(CreateProject(name="Other"))
        foreign = self.add_input("region,amount\nX,1\n", project_id=other["id"])
        self.assertIn("error", self.query(source, input_id=foreign))

    def test_filters_groups_and_empty_values_are_explicit(self):
        self.input_id = self.add_input("region,amount\nA,10.5\nA,\nB,3\n")
        source = self.message()
        result = self.query(source, group_by=["region"])
        self.assertEqual(result["groups"][0]["value"], "10.5")
        self.assertEqual(result["excluded_empty_values"], 1)
        result = self.query(source, filters=[{"column": "region", "operator": "eq", "value": "B"}])
        self.assertEqual(result["matched_rows"], 1)
        self.assertEqual(result["groups"][0]["value"], "3")

    def test_invalid_numbers_hash_changes_and_code_are_rejected(self):
        self.input_id = self.add_input("region,amount\nA,NaN\n")
        source = self.message()
        self.assertIn("error", self.query(source))
        self.assertIn("error", self.query(source, code="import os"))
        (DATA_ROOT / "projects" / self.pid / "inputs" / self.input_id).write_text("changed")
        self.assertIn("error", self.query(source, operation="schema"))

    def test_query_limit_and_empty_result(self):
        source = self.message()
        for index in range(4):
            self.query(
                source, filters=[{"column": "region", "operator": "eq", "value": str(index)}]
            )
        self.assertIn("error", self.query(source))
        other = self.message()
        result = self.query(
            other, filters=[{"column": "region", "operator": "eq", "value": "missing"}]
        )
        self.assertEqual(result["groups"][0]["value"], None)

    def test_user_memory_requires_quote_and_does_not_gain_verification(self):
        source = self.message("Выручку считаем после скидок")
        self.assertIn(
            "error",
            self.workspace.dispatch(
                source.id,
                "remember",
                {
                    "text": "Invented",
                    "kind": "user_statement",
                    "source_quote": "Other quote",
                },
            ),
        )
        good = self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "Выручка после скидок",
                "kind": "user_statement",
                "source_quote": "Выручку считаем после скидок",
                "scope": "project",
            },
        )
        self.assertEqual(good["status"], "user_stated")
        self.assertEqual(self.knowledge.list_memory(self.pid)[0]["source_quote"], source.content)
        self.assertIn(
            "error",
            self.workspace.dispatch(
                source.id,
                "remember",
                {
                    "text": "Claim",
                    "kind": "hypothesis",
                    "status": "verified",
                },
            ),
        )

    def test_observation_has_query_provenance_and_becomes_inapplicable(self):
        source = self.message()
        query = self.query(source)
        saved = self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "Общая сумма 330000",
                "kind": "observation",
                "query_id": query["action_id"],
                "scope": "project",
            },
        )
        self.assertNotIn("error", saved)
        self.add_input("region,amount\nA,1\n")
        self.assertEqual(self.knowledge.list_memory(self.pid), [])
        historical = self.knowledge.list_memory(self.pid, include_stale=True)
        self.assertFalse(historical[0]["current"])
        self.assertEqual(historical[0]["query_id"], query["action_id"])

    def test_supersession_retains_history_and_new_context(self):
        first = self.message("Правило A")
        saved = self.workspace.dispatch(
            first.id,
            "remember",
            {
                "text": "Rule A",
                "kind": "user_statement",
                "source_quote": first.content,
                "scope": "project",
            },
        )
        old_context = self.knowledge.context(first.id)
        second = self.message("Правило B")
        self.workspace.dispatch(
            second.id,
            "remember",
            {
                "text": "Rule B",
                "kind": "user_statement",
                "source_quote": second.content,
                "scope": "project",
                "supersedes_id": saved["memory_id"],
            },
        )
        self.assertEqual(self.knowledge.list_memory(self.pid)[0]["text"], "Rule B")
        with Session() as db:
            old = db.get(ContextManifest, old_context["context_manifest_id"])
            self.assertEqual(old.snapshot["memory"][0]["text"], "Rule A")
        self.assertTrue(self.knowledge.list_memory(self.pid, include_stale=True)[1]["superseded"])

    def test_bound_goal_and_current_goal_remain_distinct(self):
        source = self.message()
        new = self.research.create_goal(self.pid, self.new_goal())
        facts = self.knowledge.context(source.id)
        self.assertEqual(facts["bound_goal"]["id"], self.goal["id"])
        self.assertEqual(facts["current_goal"]["id"], new["id"])

    def test_directive_never_moves_to_a_future_run(self):
        self.configure()
        first = self.start()
        source = self.message("Change current run", "instruction")
        self.runs.control_run(self.pid, first["id"], ControlRun(action="stop"))
        second = self.start()
        result = self.workspace.dispatch(source.id, "send_to_tech_lead", {"instruction": "Change"})
        self.assertIn("error", result)
        with Session() as db:
            directive = db.scalar(select(Directive).where(Directive.message_id == source.id))
            self.assertEqual(directive.run_id, first["id"])
            self.assertNotEqual(directive.run_id, second["id"])

    def test_control_tick_skips_unbound_directive(self):
        self.configure()
        run = self.start()
        source = self.message("Instruction", "instruction")
        with Session.begin() as db:
            directive = db.scalar(select(Directive).where(Directive.message_id == source.id))
            directive.state, directive.run_id = "queued_for_tech_lead", None
        runtime = SimpleNamespace(
            last_control=0,
            platform_run_id=run["id"],
            policy={"limits": {"api_budget": None}},
            budget_warned=False,
            pause_requested=False,
            current_usage={},
            published=None,
            root={"id": "root"},
            config=SimpleNamespace(seconds=100, remaining=100),
            snapshot=lambda: {},
        )
        asyncio.run(PlatformRuntime.control_tick(runtime))
        self.assertEqual(self.count(ResearchMail), 0)

    def test_fake_model_answers_data_question_and_records_evidence(self):
        self.configure()
        source = self.message()
        factory = self.handle(
            source,
            [
                tool(
                    "query_data",
                    {
                        "input_id": self.input_id,
                        "operation": "aggregate",
                        "metric": "sum",
                        "column": "amount",
                    },
                ),
                {"role": "assistant", "content": "Сумма 330000 по sales.csv."},
            ],
        )
        history = self.messages.list_messages(self.pid)
        self.assertEqual(history[-1]["reply_to_id"], source.id)
        self.assertEqual(history[-1]["evidence"][0]["result"]["groups"][0]["value"], "330000")
        self.assertTrue(factory.client.closed)
        self.assertEqual(len(factory.client.calls), 2)
        self.assertEqual(self.count(Run), 0)
        self.assertEqual(self.count(ResearchIteration), 0)

    def test_fake_model_cannot_bypass_question_mode(self):
        self.configure()
        source = self.message()
        factory = self.handle(
            source,
            [
                tool("prepare_followup", {"goal": {"question": "Changed"}}),
                {"role": "assistant", "content": "Нужен режим продолжения."},
            ],
        )
        self.assertEqual(self.count(ResearchIteration), 0)
        self.assertIn('"error"', factory.client.calls[1][-1]["content"])

    def test_fresh_agent_context_recovers_saved_memory(self):
        self.configure()
        source = self.message("Единица RUB")
        self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "Unit RUB",
                "kind": "user_statement",
                "source_quote": "Единица RUB",
                "scope": "project",
            },
        )
        next_source = self.message("Какая единица?")
        factory = self.handle(
            next_source, [{"role": "assistant", "content": "RUB"}], saver=MemorySaver()
        )
        self.assertIn("Unit RUB", factory.client.calls[0][1]["content"])

    def test_answer_recovery_does_not_call_model_again(self):
        self.configure()
        source = self.message()
        self.handle(source, [{"role": "assistant", "content": "Done"}])
        factory = self.handle(source, [])
        self.assertEqual(factory.client.calls, [])
        self.assertEqual(self.count(Message), 2)

    def test_zero_budget_blocks_model_before_call(self):
        source = self.message()
        factory = FakeFactory([{"role": "assistant", "content": "Must not be called"}])
        manager = ProjectManager(
            MemorySaver(), factory, connection_provider=lambda: {"model": "fake"}
        )
        with self.assertRaises(ConversationBudgetError):
            asyncio.run(manager.handle(source))
        self.assertEqual(factory.client.calls, [])
        self.assertTrue(factory.client.closed)

    def test_unknown_cost_and_recovery_are_conservative(self):
        self.configure()
        self.message()
        client = tracked_conversation(FakeClient([RuntimeError("Disconnected")]), self.pid)
        with self.assertRaises(RuntimeError):
            asyncio.run(client.complete([], []))
        with Session() as db:
            usage = db.get(ConversationAccount, self.pid).usage
            self.assertAlmostEqual(usage["accounted_cost"], 0.22)
            self.assertEqual(usage["unknown_requests"], 1)
            self.assertEqual(usage["pending_requests"], 0)
        recovered = recover_reservations(
            {"accounted_cost": 1, "reserved_cost": 2, "pending_requests": 1}
        )
        self.assertEqual(recovered["accounted_cost"], 3)
        self.assertEqual(recovered["requests"], 1)
        self.assertEqual(recover_reservations(recovered), recovered)

    def test_message_request_cannot_change_intent_on_retry(self):
        ident = uuid4()
        first = CreateMessage(content="Question", client_id=ident)
        self.messages.save_message(self.pid, first)
        self.assert_status(
            409,
            lambda: self.messages.save_message(
                self.pid, CreateMessage(content="Question", client_id=ident, intent="followup")
            ),
        )

    def test_new_run_has_saved_project_memory(self):
        self.configure()
        source = self.message("Выручка в RUB")
        self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "Revenue in RUB",
                "kind": "user_statement",
                "source_quote": source.content,
                "scope": "project",
            },
        )
        run = self.start()
        with Session() as db:
            snapshot = db.get(Run, run["id"]).project_context
            self.assertEqual(snapshot["memory"][0]["text"], "Revenue in RUB")

    def test_delete_project_removes_conversation_dependencies(self):
        source = self.message("A fact")
        self.query(source)
        self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "A fact",
                "kind": "user_statement",
                "source_quote": source.content,
            },
        )
        self.knowledge.context(source.id)
        self.projects.delete_project(self.pid)
        for model in (PMAction, ProjectMemory, ContextManifest, ConversationAccount):
            self.assertEqual(self.count(model), 0)

    def test_followup_inherits_unmodified_definitions_and_constraints(self):
        self.research.create_goal(
            self.pid,
            self.new_goal(
                goal=storage.GoalSpec(
                    question="Current",
                    definitions={"Revenue": "after discounts"},
                    constraints=("RUB",),
                )
            ),
        )
        source = self.message("Продолжи", "followup")
        result = self.workspace.dispatch(
            source.id, "prepare_followup", {"goal": {"question": "Next"}}
        )
        with Session() as db:
            goal = db.get(GoalRevision, result["goal_revision_id"])
            self.assertEqual(goal.spec["definitions"], {"Revenue": "after discounts"})
            self.assertEqual(goal.spec["constraints"], ["RUB"])

    def test_context_cannot_read_foreign_project_binding(self):
        other = self.projects.create_project(CreateProject(name="Other"))
        source = self.message()
        with Session.begin() as db:
            record = db.get(Message, source.id)
            record.context_binding = {
                **record.context_binding,
                "goal_revision_id": other["current_goal"]["id"],
            }
        self.assertIsNone(self.knowledge.context(source.id)["bound_goal"])

    def test_concurrent_budget_reservations_do_not_overspend(self):
        self.configure(budget=0.3)
        self.message()

        class DelayedClient(FakeClient):
            async def complete(self, *args):
                await asyncio.sleep(0.02)
                return await super().complete(*args)

        clients = [
            tracked_conversation(DelayedClient([{"content": "ok"}]), self.pid) for _ in range(2)
        ]

        async def run():
            return await asyncio.gather(
                *(client.complete([], []) for client in clients), return_exceptions=True
            )

        results = asyncio.run(run())
        self.assertEqual(sum(isinstance(result, ConversationBudgetError) for result in results), 1)
        self.assertEqual(sum(len(client.calls) for client in clients), 1)

    def test_conversation_shares_run_slots_but_bills_its_own_account(self):
        self.configure()
        self.message()
        run = self.start()
        factory = ModelClientFactory()
        config = SimpleNamespace(base_url="http://fake.invalid/v1", model="fake")
        with patch(
            "dolphinlab.infrastructure.model_clients.ModelClient",
            lambda _: FakeClient([{"content": "ok"}]),
        ):
            client = tracked_conversation(factory.create_conversation(config, self.pid), self.pid)
            asyncio.run(client.complete([], []))
        self.assertIn(run["id"], factory.run_slots)
        with Session() as db:
            self.assertEqual(db.get(Run, run["id"]).usage, {})
            self.assertEqual(db.get(ConversationAccount, self.pid).usage["requests"], 1)

    def test_model_loop_is_bounded(self):
        self.configure()
        source = self.message()
        factory = FakeFactory([tool("project_status", {}, f"call-{index}") for index in range(8)])
        manager = ProjectManager(
            MemorySaver(), factory, connection_provider=lambda: {"model": "fake"}
        )
        with self.assertRaises(APIUnavailable):
            asyncio.run(manager.handle(source))
        self.assertEqual(len(factory.client.calls), 8)
        self.assertTrue(factory.client.closed)

    def test_previous_run_and_goal_memory_are_explicit_historical_sources(self):
        self.configure()
        source = self.message("Original condition")
        self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": "Original condition",
                "kind": "user_statement",
                "source_quote": source.content,
            },
        )
        previous = self.start()
        self.runs.control_run(self.pid, previous["id"], ControlRun(action="stop"))
        followup = self.message("Next question", "followup")
        prepared = self.workspace.dispatch(
            followup.id, "prepare_followup", {"goal": {"question": "Next"}}
        )
        run = self.start(storage.StartRun(iteration_id=prepared["iteration_id"]))
        with Session() as db:
            context = db.get(Run, run["id"]).project_context
            self.assertEqual(context["previous_runs"][0]["id"], previous["id"])
            self.assertFalse(context["historical_memory"][0]["current"])
            self.assertEqual(context["historical_memory"][0]["status"], "user_stated")

    def test_migration_keeps_legacy_directive_unbound(self):
        source = self.message("Old pending instruction", "instruction")
        with Session.begin() as db:
            db.scalar(
                select(Directive).where(Directive.message_id == source.id)
            ).state = "awaiting_pm"
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        command.downgrade(config, "0003")
        command.upgrade(config, "head")
        with Session() as db:
            message = db.get(Message, source.id)
            directive = db.scalar(select(Directive).where(Directive.message_id == source.id))
            self.assertEqual(message.content, "Old pending instruction")
            self.assertEqual(message.intent, "instruction")
            self.assertIsNone(directive.run_id)
            self.assertEqual(directive.state, "needs_target")

    def test_tabular_formats_and_resource_limits(self):
        self.input_id = self.add_input("region;amount\nA;1.25\nB;2.75\n")
        self.assertEqual(self.query(self.message(), delimiter=";")["groups"][0]["value"], "4.00")
        self.input_id = self.add_input("region\tamount\nA\t2\n", name="sales.tsv")
        self.assertEqual(self.query(self.message())["groups"][0]["value"], "2")
        self.input_id = self.add_input("region,amount\nA,1\nB,2\nC,3\n")
        with patch("dolphinlab.infrastructure.tabular_questions.MAX_ROWS", 2):
            self.assertIn("error", self.query(self.message()))
        with patch("dolphinlab.infrastructure.tabular_questions.MAX_BYTES", 8):
            self.assertIn("error", self.query(self.message()))

    def test_task_context_is_compact_index_with_full_snapshot_available(self):
        from dolphinlab.agents.session import ResearchSession

        self.configure()
        text = "L" * 3500
        source = self.message(text)
        self.workspace.dispatch(
            source.id,
            "remember",
            {
                "text": text,
                "kind": "user_statement",
                "source_quote": text,
                "scope": "project",
            },
        )
        run = self.start()
        with Session() as db:
            task = ResearchSession(None, None).materialize(db.get(Run, run["id"]), None)
        self.assertNotIn(text, task.read_text(encoding="utf-8"))
        snapshot = json.loads((task.parent / "project-context.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["memory"][0]["text"], text)

    def test_recent_messages_are_recoverable_without_summary_promotion(self):
        first = self.message("Выручка в RUB")
        second = self.message("Уточнение")
        context = self.knowledge.context(second.id)
        self.assertEqual(context["recent_user_messages"][0]["id"], first.id)
        self.assertEqual(context["memory"], [])

    def test_known_capacity_overflow_preserves_source_without_calling_model(self):
        self.configure()
        source = self.message("Keep this original question")
        factory = FakeFactory([])
        factory.client.context_status = lambda *args: {"needs_compaction": True}
        manager = ProjectManager(
            MemorySaver(), factory, connection_provider=lambda: {"model": "fake"}
        )
        with self.assertRaises(PMContextError):
            asyncio.run(manager.handle(source))
        self.assertEqual(factory.client.calls, [])
        self.assertTrue(factory.client.closed)
        with Session() as db:
            self.assertEqual(db.get(Message, source.id).content, "Keep this original question")


if __name__ == "__main__":
    unittest.main()
