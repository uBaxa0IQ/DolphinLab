"""Behavioral tests; scripted clients below exist only in tests, never in production."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from research_config import Config
from research_context import blocks, compact, build_context, archive_old_payloads, prepare_context, refresh_context_status
from research_jobs import Jobs, TERMINAL
from research_runtime import Runtime
from research_store import Store


class Client:
    async def complete(self, messages, tools=None):
        return {"role": "assistant", "content": "Useful compact working note with evidence paths."}, {}

    async def close(self):
        pass


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = self.root / "task.md"
        self.task.write_text("Predict values for the supplied test objects. Save submission.csv.", encoding="utf-8")
        self.config = Config(self.task, self.root / "run", seconds=30, reserve=0, cpus=2, ram_mb=1024,
                             reasoning_effort="low", status_file=None)
        self.runtime = Runtime(self.config, Client())

    async def asyncTearDown(self):
        await self.runtime.jobs.shutdown()
        self.runtime.store.close()
        self.temp.cleanup()

    async def create_team(self):
        return await self.runtime.dispatch(self.runtime.root, "create_team", {
            "direction": "Investigate a general model", "goal": "Produce measured predictions"})

    async def wait_job(self, job_id):
        await asyncio.wait_for(self.runtime.jobs.tasks[job_id], 10)
        return self.runtime.store.get("job", job_id)

    async def test_role_catalog_and_reassignment_preserve_context(self):
        team = await self.create_team()
        leader = self.runtime.agents[team["lead"]]
        evaluator = self.runtime.agents[team["evaluator"]]
        self.assertEqual(evaluator["role"], "evaluator")
        leader["note"] = "Measured finding"
        self.assertEqual({self.runtime.agents[m]["role"] for m in team["members"]},
                         {"team_lead", "ml_engineer", "data_engineer", "evaluator"})
        updated = await self.runtime.dispatch(self.runtime.root, "reassign_team", {
            "team_id": team["id"], "direction": "New hypothesis", "goal": "Refine errors"})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(leader["note"], "Measured finding")
        self.assertTrue(self.runtime.store.pending(leader["id"]))
        with self.assertRaises(ValueError):
            self.runtime.new_agent("invented_role", team["id"], leader["id"], "Do something")

    async def test_mail_is_durable_on_failure_and_new_mail_waits_for_next_turn(self):
        store, agent = self.runtime.store, self.runtime.root
        store.take_mail(agent)
        store.send(agent["id"], "test", "first")
        store.take_mail(agent)
        # A failed model call does not remove the already-delivered user message.
        self.assertIn("first", store.get("agent", agent["id"])["history"][-1]["content"])
        store.send(agent["id"], "test", "second")
        self.assertTrue(store.pending(agent["id"]))
        self.assertNotIn("second", agent["history"][-1]["content"])
        store.take_mail(agent)
        self.assertIn("second", agent["history"][-1]["content"])

    async def test_python_code_is_arbitrary_and_candidate_requires_independent_review(self):
        team = await self.create_team()
        leader = self.runtime.agents[team["lead"]]
        child = await self.runtime.dispatch(leader, "add_specialist", {"team_id": team["id"],
            "role": "ml_engineer", "assignment": "Write your own Python"})
        engineer = self.runtime.agents[child["agent_id"]]
        code = "from pathlib import Path\nPath('submission.csv').write_text('id,value\\n1,4\\n')\nprint(sum(i*i for i in range(10)))\n"
        submitted = await self.runtime.dispatch(engineer, "run_python", {
            "code": code, "purpose": "Arbitrary experiment", "cpus": 1, "ram_mb": 256, "timeout": 5})
        job = await self.wait_job(submitted["job_id"])
        self.assertEqual(job["state"], "succeeded")
        candidate = await self.runtime.dispatch(engineer, "register_candidate", {
            "job_id": job["id"], "submission_path": str(Path(job["folder"]) / "submission.csv"),
            "findings": {"metric": "agent-chosen", "anything": [1, 2, 3]}})
        with self.assertRaises(ValueError):
            self.runtime.publish(candidate["id"])
        evaluator = self.runtime.agents[team["evaluator"]]
        check_code = ("import csv\nfrom pathlib import Path\n"
                      f"rows=list(csv.DictReader(Path({candidate['submission_path']!r}).open()))\n"
                      "assert rows == [{'id':'1','value':'4'}]\nprint('independently checked')\n")
        submitted = await self.runtime.dispatch(evaluator, "run_python", {
            "code": check_code, "purpose": "Independent review", "cpus": 1, "ram_mb": 256})
        check = await self.wait_job(submitted["job_id"])
        reviewed = await self.runtime.dispatch(evaluator, "review_candidate", {
            "candidate_id": candidate["id"], "job_id": check["id"], "approved": True,
            "conclusion": "Own code read frozen output and checked its values/schema"})
        self.assertTrue(reviewed["approved"])
        self.assertEqual((self.root / "submission.csv").read_text(), "id,value\n1,4\n")
        (self.root / "submission.csv").write_text("unreviewed overwrite")
        await self.runtime.job_finished(check)
        self.assertEqual((self.root / "submission.csv").read_text(), "id,value\n1,4\n")
        frozen_bytes = Path(candidate["submission_path"]).read_bytes()
        Path(candidate["submission_path"]).write_text("tampered")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.runtime.publish(candidate["id"])
        Path(candidate["submission_path"]).write_bytes(frozen_bytes)
        blocked = await self.runtime.dispatch(evaluator, "flag_candidate", {
            "candidate_id": candidate["id"], "reason": "New scientific evidence invalidates approval",
            "references": [str(Path(check["folder"]) / "stdout.log")]})
        self.assertEqual(blocked["review_state"], "blocked")
        self.assertIsNone(self.runtime.published)
        self.assertFalse((self.root / "submission.csv").exists())
        self.assertTrue(Path(candidate["submission_path"]).exists())
        self.assertTrue(self.runtime.store.pending(self.runtime.root["id"]))
        self.assertTrue(self.runtime.store.pending(leader["id"]))
        with self.assertRaises(ValueError):
            self.runtime.publish(candidate["id"])

    async def test_timeout_and_cancel_queued_job_release_resources(self):
        team = await self.create_team()
        engineer = self.runtime.agents[team["ml_engineer"]]
        first = await self.runtime.dispatch(engineer, "run_python", {
            "code": "import time; time.sleep(10)", "purpose": "Timeout", "cpus": 2, "ram_mb": 256, "timeout": .4, "wait_for_seconds": 0})
        second = await self.runtime.dispatch(engineer, "run_python", {
            "code": "print('should not run')", "purpose": "Queued cancellation", "cpus": 1, "ram_mb": 256, "wait_for_seconds": 0})
        self.runtime.jobs.cancel(second["job_id"])
        results = await asyncio.gather(self.wait_job(first["job_id"]), self.wait_job(second["job_id"]))
        self.assertEqual([j["state"] for j in results], ["timed_out", "cancelled"])
        self.assertEqual(self.runtime.jobs.used_cpu, 0)

    async def test_compaction_keeps_call_result_pairs_and_full_archive(self):
        agent = self.runtime.root
        agent["history"] = [{"role": "user", "content": "older finding"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "wait", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "latest task"}]
        await compact(agent, Client(), self.runtime.store)
        self.assertEqual(agent["history"], [{"role": "user", "content": "latest task"}])
        self.assertTrue(agent["summary"])
        count = self.runtime.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='context_archive'").fetchone()[0]
        self.assertEqual(count, 1)

    async def test_finalization_rejects_research_but_allows_agent_written_final_code(self):
        team = await self.create_team()
        engineer = self.runtime.agents[team["ml_engineer"]]
        self.config.reserve = 100
        with self.assertRaises(ValueError):
            await self.runtime.dispatch(engineer, "run_python", {
                "code": "print(1)", "purpose": "Research", "kind": "research"})
        job = await self.runtime.dispatch(engineer, "run_python", {
            "code": "print(1)", "purpose": "Final delivery", "kind": "finalization", "cpus": 1, "ram_mb": 256})
        self.assertEqual((await self.wait_job(job["job_id"]))["state"], "succeeded")

    async def test_cannot_write_colleagues_artifacts(self):
        team = await self.create_team()
        engineer = self.runtime.agents[team["ml_engineer"]]
        with self.assertRaises(ValueError):
            await self.runtime.dispatch(engineer, "write_file", {
                "path": str(self.task), "content": "overwrite task"})

    async def test_manager_permissions_and_concrete_required_arguments(self):
        from research_tools import tools_for
        team = await self.create_team()
        for agent in (self.runtime.root, self.runtime.agents[team["lead"]]):
            names = {t["function"]["name"] for t in tools_for(agent["role"])}
            self.assertNotIn("run_python", names)
            self.assertNotIn("write_file", names)
            for name, args in (("run_python", {"code": "print(1)", "purpose": "Forbidden"}),
                               ("write_file", {"path": "code.py", "content": "print(1)"})):
                with self.assertRaises(ValueError):
                    await self.runtime.dispatch(agent, name, args)
        with self.assertRaisesRegex(ValueError, "missing: direction"):
            await self.runtime.dispatch(self.runtime.root, "create_team", {"goal": "Missing question"})
        self.assertEqual(self.runtime.store.all("job"), [])

    async def test_team_lead_owns_soft_time_plan_and_global_deadline_is_immutable(self):
        import time
        team = await self.create_team()
        lead = self.runtime.agents[team["lead"]]
        deadline = self.config.deadline_wall
        self.assertEqual(self.config.reserve, 0)
        initial = self.runtime.timing.view(team)
        self.assertEqual(initial["phase"], "research")
        self.assertEqual(initial["plan"], {})
        result = await self.runtime.dispatch(lead, "plan_team", {
            "team_id": team["id"], "reason": "Measured experiment durations",
            "first_result_in_seconds": 5, "review_in_seconds": 2,
            "finalization_reserve_seconds": 4})
        self.assertEqual(result["plan"]["finalization_reserve_seconds"], 4)
        with self.assertRaises(ValueError):
            await self.runtime.dispatch(self.runtime.root, "plan_team", {
                "team_id": team["id"], "reason": "Wrong owner"})
        await self.runtime.dispatch(self.runtime.root, "allocate_team_time", {
            "team_id": team["id"], "seconds": 10000, "reason": "Extend within total budget"})
        allocated = self.runtime.store.get("team", team["id"])
        self.assertLessEqual(allocated["allocation_deadline_wall"], deadline + .01)
        await self.runtime.dispatch(lead, "plan_team", {
            "team_id": team["id"], "reason": "Explicit handover", "phase": "finalization"})
        self.assertEqual(self.runtime.timing.view(self.runtime.store.get("team", team["id"]))["phase"], "finalization")
        self.assertFalse(self.config.finalizing)
        allocated = self.runtime.store.get("team", team["id"])
        allocated["time_plan"]["review_wall"] = time.time() - 1
        self.runtime.store.put("team", team["id"], allocated)
        self.runtime.timing.tick()
        count = self.runtime.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='team_time_checkpoint'").fetchone()[0]
        self.runtime.timing.previous = 0
        self.runtime.timing.tick()
        self.assertEqual(count, self.runtime.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='team_time_checkpoint'").fetchone()[0])
        self.assertEqual(deadline, self.config.deadline_wall)

    async def test_messages_update_focus_and_journal_preserves_unverified_status(self):
        team = await self.create_team()
        lead, ml = self.runtime.agents[team["lead"]], self.runtime.agents[team["ml_engineer"]]
        await self.runtime.dispatch(lead, "send_message", {
            "to": ml["id"], "kind": "assignment", "text": "Produce first candidate now"})
        self.assertEqual(ml["focus"], "Produce first candidate now")
        await self.runtime.dispatch(ml, "send_message", {
            "to": lead["id"], "kind": "finding", "text": "Possible effect, not independently confirmed",
            "references": [str(self.task)]})
        journal = self.runtime.store.all("team_journal")
        self.assertEqual(len(journal), 1)
        self.assertEqual(journal[0]["claim_status"], "reported")
        self.assertEqual(journal[0]["references"], [str(self.task)])
        self.assertTrue(lead["phase_pending"])

    async def test_partial_artifact_requires_explicit_salvage_and_stays_unapproved(self):
        team = await self.create_team()
        ml = self.runtime.agents[team["ml_engineer"]]
        started = await self.runtime.dispatch(ml, "run_python", {
            "code": "from pathlib import Path\nPath('submission.csv').write_text('x\\n1\\n')\nraise RuntimeError('after writing')",
            "purpose": "Partial output", "cpus": 1, "ram_mb": 256})
        job = await self.wait_job(started["job_id"])
        self.assertEqual(job["state"], "failed")
        args = {"job_id": job["id"], "submission_path": str(Path(job["folder"]) / "submission.csv")}
        with self.assertRaises(ValueError):
            await self.runtime.dispatch(ml, "register_candidate", args)
        candidate = await self.runtime.dispatch(ml, "register_candidate", {**args, "allow_partial_source": True})
        self.assertFalse(candidate["approved"])
        self.assertEqual(candidate["source_job_state"], "failed")
        self.assertIsNone(self.runtime.published)

    async def test_consumed_payload_archival_retains_originals_and_recent_exchange(self):
        agent = self.runtime.root
        source = "print('important source')\n" * 500
        old = [{"role": "assistant", "content": "An unverified interpretation", "tool_calls": [{
            "id": "payload", "type": "function", "function": {"name": "run_python", "arguments": json.dumps({"code": source, "purpose": "Experiment"})}}]},
            {"role": "tool", "tool_call_id": "payload", "content": '{"job_id":"real_job"}'}]
        tail = [{"role": "assistant", "content": "Keep the split protocol"}, {"role": "assistant", "content": "Latest observation"}]
        agent["history"] = old + tail
        archive_old_payloads(agent, self.runtime.store, keep_recent=2, payload_bytes=4000)
        self.assertEqual(agent["history"][-2:], tail)
        self.assertIn("real_job", agent["history"][0]["content"])
        self.assertIn("unverified", agent["history"][0]["content"])
        original = json.loads(Path(agent["last_archive"]).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(original["history"][0]["tool_calls"][0]["function"]["arguments"])["code"], source)
        blocks(agent["history"])

    async def test_manager_board_snapshots_are_archived_and_root_compacts_without_job_events(self):
        agent = self.runtime.root
        snapshot = {"members": [{"note": "large stale board " * 1500}], "id": "team_old"}
        agent["history"] = [{"role": "assistant", "content": "", "tool_calls": [{"id": "board", "type": "function", "function": {
            "name": "inspect_team", "arguments": '{"team_id":"team_old"}'}}]},
            {"role": "tool", "tool_call_id": "board", "content": json.dumps(snapshot)},
            {"role": "assistant", "content": "observation"}, {"role": "assistant", "content": "recent decision"}]
        archive_old_payloads(agent, self.runtime.store, keep_recent=2, payload_bytes=4000)
        self.assertLess(len(json.dumps(agent["history"])), 3000)
        self.assertIn("archived tool result", agent["history"][0]["content"])
        agent["history"] = [{"role": "user", "content": "research evidence " * 9000},
                            {"role": "assistant", "content": "Keep a recent decision"}]
        agent.update(turn_count=16, phase_pending=False)
        self.config.seconds = 120  # Capacity to perform an optional summary, no model requests.
        self.config.deadline_wall += 90
        await prepare_context(agent, self.runtime.state(agent), Client(), self.config, self.runtime.store, [])
        self.assertTrue(agent["summary"])
        self.assertEqual(agent["last_compaction_turn"], 16)
        self.assertLess(agent["context_status"]["history_bytes"], 1000)
        self.assertGreater(agent["last_compaction"]["before_memory_bytes"], agent["last_compaction"]["summary_bytes"])

    async def test_ineffective_summary_does_not_replace_originals(self):
        class Expanding(Client):
            async def complete(self, messages, tools=None):
                return {"role": "assistant", "content": "unhelpful expansion " * 1000}, {}
        agent = self.runtime.root
        original = [{"role": "user", "content": "Exact instruction"}]
        agent["history"] = original.copy()
        with self.assertRaisesRegex(ValueError, "did not reduce"):
            await compact(agent, Expanding(), self.runtime.store)
        self.assertEqual(agent["history"], original)
        self.assertEqual(agent["summary"], "")

    async def test_expanded_policy_does_not_summarize_moderate_active_history(self):
        from unittest.mock import AsyncMock
        agent = self.runtime.root
        original = [{"role": "user", "content": "active research detail " * 2500}]
        agent.update(history=original.copy(), turn_count=20, phase_pending=True)
        self.config.seconds = 120
        self.config.deadline_wall += 90
        client = Client()
        client.complete = AsyncMock()
        await prepare_context(agent, self.runtime.state(agent), client, self.config, self.runtime.store, [])
        client.complete.assert_not_awaited()
        self.assertEqual(agent["history"], original)

    async def test_physical_capacity_guard_overrides_generous_working_policy(self):
        marker = "capacity-bound evidence " * 1000
        class CapacityClient(Client):
            def context_status(self, messages, tools=None):
                return {"needs_compaction": tools is not None and any(m.get("content") == marker for m in messages)}
        agent = self.runtime.root
        agent.update(history=[{"role": "user", "content": marker}], turn_count=1)
        await prepare_context(agent, self.runtime.state(agent), CapacityClient(), self.config, self.runtime.store, [])
        self.assertTrue(agent["summary"])
        self.assertEqual(agent["history"], [])
        self.assertTrue(Path(agent["last_archive"]).is_file())

    async def test_payload_archival_preserves_six_fresh_exchanges(self):
        agent = self.runtime.root
        old = [{"role": "assistant", "content": "", "tool_calls": [{"id": "big", "type": "function", "function": {
            "name": "read_file", "arguments": '{"path":"evidence.txt"}'}}]},
            {"role": "tool", "tool_call_id": "big", "content": json.dumps({"text": "large evidence " * 2000})}]
        recent = [{"role": "assistant", "content": f"Recent exchange {i}"} for i in range(6)]
        agent["history"] = old + recent[:5]
        archive_old_payloads(agent, self.runtime.store)
        self.assertEqual(agent["history"], old + recent[:5])
        agent["history"].append(recent[-1])
        archive_old_payloads(agent, self.runtime.store)
        self.assertEqual(agent["history"][-6:], recent)
        self.assertIn("Previously completed", agent["history"][0]["content"])

    async def test_six_independent_teams_share_one_unchanged_compute_budget(self):
        original_budget = self.config.cpus, self.config.ram_mb
        teams = [await self.runtime.dispatch(self.runtime.root, "create_team", {
            "direction": f"Independent question {i}", "goal": f"Resolve uncertainty {i}"}) for i in range(6)]
        self.assertEqual(len(self.runtime.agents), 25)
        self.assertEqual(len({m for t in teams for m in t["members"]}), 24)
        self.assertEqual((self.config.cpus, self.config.ram_mb), original_budget)
        self.assertEqual(self.runtime.state(self.runtime.root)["team_capacity"]["full_team_slots_free"], 0)
        with self.assertRaises(ValueError):
            await self.create_team()

    async def test_current_context_metrics_are_distinct_from_previous_prompt(self):
        class Measuring(Client):
            def context_status(self, messages, tools=None):
                return {"estimated_input_tokens": len(json.dumps(messages)), "needs_compaction": False}
        agent = self.runtime.root
        agent["history"] = [{"role": "user", "content": "observation " * 5000}]
        agent["last_actual_prompt_tokens"] = 90000
        state = self.runtime.state(agent)
        refresh_context_status(agent, state, Measuring(), [])
        before = agent["context_status"]["estimated_input_tokens"]
        await compact(agent, Client(), self.runtime.store)
        refresh_context_status(agent, state, Measuring(), [])
        self.assertLess(agent["context_status"]["estimated_input_tokens"], before)
        self.assertEqual(agent["context_status"]["last_actual_prompt_tokens"], 90000)

    async def test_independent_teams_and_finish_requires_handover(self):
        first = await self.create_team()
        second = await self.runtime.dispatch(self.runtime.root, "create_team", {
            "direction": "Study validation instability", "goal": "Resolve a distinct uncertainty",
            "scope": "Assessment evidence", "out_of_scope": "Duplicate the baseline", "budget_seconds": 10})
        self.assertEqual(len(self.runtime.agents), 9)
        self.assertTrue(set(first["members"]).isdisjoint(second["members"]))
        plan = await self.runtime.dispatch(self.runtime.root, "plan_portfolio", {
            "strategy": "Keep baseline research and independent validation questions separate; compare evidence at review",
            "next_review_in_seconds": 5})
        self.assertIn("review_wall", plan)
        self.runtime.published = {"candidate_id": "fixture", "path": "fixture", "sha256": "fixture"}
        with self.assertRaisesRegex(ValueError, "still active"):
            await self.runtime.dispatch(self.runtime.root, "finish", {"reason": "One experiment was too slow"})
        self.assertFalse(self.runtime.done)
        for team in (first, second):
            await self.runtime.dispatch(self.runtime.root, "close_team", {
                "team_id": team["id"], "mode": "immediate", "reason": "Collected handover, alternatives assessed"})
        result = await self.runtime.dispatch(self.runtime.root, "finish", {"reason": "No further feasible useful research after handovers"})
        self.assertTrue(result["finished"])
        self.assertTrue(self.runtime.store.all("stop_decision"))

    async def test_completion_wakes_owner_with_logs_without_routine_manager_polling(self):
        team = await self.create_team()
        ml, lead = self.runtime.agents[team["ml_engineer"]], self.runtime.agents[team["lead"]]
        self.runtime.store.take_mail(lead)
        submitted = await self.runtime.dispatch(ml, "run_python", {
            "code": "print('completed evidence')", "purpose": "Completion event", "cpus": 1, "ram_mb": 256, "wait_for_seconds": 0})
        await self.wait_job(submitted["job_id"])
        self.assertFalse(self.runtime.store.pending(lead["id"]))
        self.assertTrue(self.runtime.store.pending(ml["id"]))
        self.runtime.store.take_mail(ml)
        self.assertIn("completed evidence", ml["history"][-1]["content"])
        self.assertIn("stdout.log", ml["history"][-1]["content"])

    async def test_context_has_no_local_input_cap(self):
        agent = self.runtime.root
        large = "research observation " * 15000
        agent["history"] = [{"role": "user", "content": large}]
        messages = build_context(agent, self.runtime.state(agent))
        self.assertIn({"role": "user", "content": large}, messages)
        self.assertGreater(len(json.dumps(messages)), 100000)

    async def test_changing_clock_and_board_do_not_rewrite_stable_conversation_prefix(self):
        agent = self.runtime.root
        agent["history"] = [{"role": "user", "content": "Observed evidence"}]
        first = build_context(agent, {"original_task": "Task", "remaining_seconds": 100})
        second = build_context(agent, {"original_task": "Task", "remaining_seconds": 80})
        self.assertEqual(first[:-1], second[:-1])
        self.assertNotEqual(first[-1], second[-1])

    async def test_single_large_conversation_block_can_be_compacted(self):
        agent = self.runtime.root
        agent["history"] = [{"role": "user", "content": "large artifact " * 3000}]
        await compact(agent, Client(), self.runtime.store)
        self.assertEqual(agent["history"], [])
        self.assertTrue(agent["summary"])

    async def test_actor_has_only_one_turn_and_queues_messages_during_request(self):
        class SlowClient(Client):
            def __init__(self):
                self.started = asyncio.Event()
                self.release = asyncio.Event()
                self.active = self.maximum = self.count = 0
                self.inputs = []

            async def complete(inner, messages, tools=None):
                inner.active += 1
                inner.maximum = max(inner.maximum, inner.active)
                inner.count += 1
                inner.inputs.append(json.dumps(messages))
                inner.started.set()
                if inner.count == 1:
                    await inner.release.wait()
                inner.active -= 1
                return {"role": "assistant", "content": "", "tool_calls": [{
                    "id": f"call_{inner.count}", "type": "function", "function": {
                    "name": "wait", "arguments": '{"seconds":60}'}}]}, {}

        client = SlowClient()
        self.runtime.client = client
        running = asyncio.create_task(self.runtime.run())
        await client.started.wait()
        self.runtime.notify(self.runtime.root["id"], "arrived while request was active")
        self.runtime.notify(self.runtime.root["id"], "second pending message")
        await asyncio.sleep(.15)
        self.assertEqual(client.count, 1)
        client.release.set()
        for _ in range(30):
            if client.count >= 2:
                break
            await asyncio.sleep(.05)
        self.runtime.done = True
        await running
        self.assertEqual(client.maximum, 1)
        self.assertIn("arrived while request was active", client.inputs[1])
        self.assertIn("second pending message", client.inputs[1])

    async def test_production_transport_omits_token_limits_and_sets_low(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from research_llm import ModelClient
        backend = ModelClient.__new__(ModelClient)
        backend.config = self.config
        backend.slots = asyncio.Semaphore(1)
        from collections import deque
        backend.blocked_until = 0.
        backend.disabled_reason = None
        backend.failures = 0
        backend.ratios = deque(maxlen=16)
        backend.window_source = "not confirmed"
        message = SimpleNamespace(content="ok", tool_calls=[], refusal=None,
                                  model_dump=lambda **_: {"role": "assistant", "content": "ok"})
        response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)
        create = AsyncMock(return_value=response)
        backend.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        await backend.complete([{"role": "user", "content": "hello"}])
        sent = create.call_args.kwargs
        self.assertEqual(sent["reasoning_effort"], "low")
        self.assertNotIn("max_tokens", sent)
        self.assertNotIn("max_completion_tokens", sent)
        from research_llm import request_purpose
        self.config.reasoning_effort = "high"
        self.config.request_extra = {"reasoning": {"effort": "high"}}
        purpose = request_purpose.set("compaction")
        try:
            await backend.complete([{"role": "user", "content": "summarize evidence"}])
        finally:
            request_purpose.reset(purpose)
        self.assertEqual(create.call_args.kwargs["reasoning_effort"], "low")
        self.assertEqual(create.call_args.kwargs["extra_body"]["reasoning"]["effort"], "low")
        self.assertEqual(self.config.reasoning_effort, "high")

    async def test_recovery_adopts_live_worker_without_restarting_code_or_clock(self):
        import sys
        crash = self.root / "crash"
        child = '''import asyncio, os, sys
from pathlib import Path
from research_config import Config
from research_runtime import Runtime
class Client: pass
async def main():
    rt = Runtime(Config(Path(sys.argv[1]), Path(sys.argv[2]), seconds=30, cpus=2, ram_mb=1024, status_file=None), Client())
    team = await rt.dispatch(rt.root, "create_team", {"direction":"Recovery", "goal":"Preserve worker"})
    ml = rt.agents[team["ml_engineer"]]
    job = await rt.dispatch(ml, "run_python", {"purpose":"Survive coordinator crash", "code":"from pathlib import Path\\nimport time\\nwith Path('executions.txt').open('a') as f: f.write('once\\\\n')\\ntime.sleep(3)\\nPath('done.txt').write_text('finished')", "cpus":1, "ram_mb":256, "wait_for_seconds":0})
    while not (Path(job["folder"]) / "process.json").exists(): await asyncio.sleep(.02)
    os._exit(77)
asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", child, str(self.task), str(crash),
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        self.assertEqual(process.returncode, 77, (stdout + stderr).decode(errors="replace"))
        resumed = Runtime(Config(self.task, crash, seconds=9000, resume=True, cpus=2, ram_mb=1024, status_file=None), Client())
        try:
            self.assertEqual(resumed.config.seconds, 30)
            original_deadline = resumed.run_meta["started_wall"] + 30
            self.assertEqual(resumed.config.deadline_wall, original_deadline)
            await resumed.jobs.recover(set(resumed.agents))
            job = resumed.store.all("job")[0]
            if job["id"] in resumed.jobs.tasks:
                await asyncio.wait_for(resumed.jobs.tasks[job["id"]], 10)
            final = resumed.store.get("job", job["id"])
            self.assertEqual(final["state"], "succeeded")
            self.assertEqual((Path(job["folder"]) / "executions.txt").read_text().splitlines(), ["once"])
            self.assertEqual((Path(job["folder"]) / "done.txt").read_text(), "finished")
            self.assertEqual(len(resumed.store.all("job")), 1)
        finally:
            await resumed.jobs.shutdown()
            resumed.store.close()

    async def test_api_failures_classify_timeout_retry_and_auth_stop(self):
        from types import SimpleNamespace
        from research_llm import failure_kind, ResponseProblem
        class Error(Exception): pass
        rate = Error("rate limit")
        rate.status_code = 429
        rate.response = SimpleNamespace(headers={"retry-after": "7"})
        failure = failure_kind(rate)
        self.assertTrue(failure.retryable and failure.shared)
        self.assertEqual(failure.retry_after, 7)
        auth = Error("unauthorized")
        auth.status_code = 401
        self.assertFalse(failure_kind(auth).retryable)
        self.assertTrue(failure_kind(auth).shared)
        self.assertTrue(failure_kind(TimeoutError()).retryable)
        self.assertTrue(failure_kind(ResponseProblem("incomplete", "truncated reasoning")).retryable)
        self.assertFalse(failure_kind(Error("Budget has been exceeded")).retryable)

    async def test_short_python_result_is_inline_without_duplicate_completion_mail(self):
        team = await self.create_team()
        ml = self.runtime.agents[team["ml_engineer"]]
        self.runtime.store.take_mail(ml)
        result = await self.runtime.dispatch(ml, "run_python", {
            "code": "print('direct observation')", "purpose": "Short diagnostic", "cpus": 1, "ram_mb": 256})
        self.assertTrue(result["completion_in_tool_result"])
        self.assertEqual(result["state"], "succeeded")
        self.assertIn("direct observation", result["stdout_tail"])
        self.assertTrue(Path(result["script"]).is_file())
        self.assertFalse(self.runtime.store.pending(ml["id"]))

    async def test_inline_observation_timeout_does_not_cancel_execution(self):
        team = await self.create_team()
        ml = self.runtime.agents[team["ml_engineer"]]
        result = await self.runtime.dispatch(ml, "run_python", {
            "code": "import time; time.sleep(.3); print('continued')", "purpose": "Longer than observation",
            "cpus": 1, "ram_mb": 256, "wait_for_seconds": .01})
        final = await self.wait_job(result["job_id"])
        self.assertEqual(final["state"], "succeeded")
        self.assertTrue(self.runtime.store.pending(ml["id"]))

    async def test_literal_edit_repairs_script_and_does_not_mutate_frozen_job(self):
        team = await self.create_team()
        ml = self.runtime.agents[team["ml_engineer"]]
        file = await self.runtime.dispatch(ml, "write_file", {"path": "repair.py", "content": "print(undefined_name)\n"})
        first = await self.runtime.dispatch(ml, "run_script", {
            "path": file["path"], "purpose": "Observe error", "cpus": 1, "ram_mb": 256})
        self.assertEqual(first["state"], "failed")
        await self.runtime.dispatch(ml, "replace_text", {"path": file["path"], "old_text": "undefined_name", "new_text": "42"})
        second = await self.runtime.dispatch(ml, "run_script", {
            "path": file["path"], "purpose": "Verify repair", "cpus": 1, "ram_mb": 256})
        self.assertEqual(second["state"], "succeeded")
        self.assertIn("42", second["stdout_tail"])
        self.assertIn("undefined_name", Path(first["script"]).read_text())
        with self.assertRaises(ValueError):
            await self.runtime.dispatch(self.runtime.agents[team["lead"]], "replace_text", {
                "path": file["path"], "old_text": "42", "new_text": "0"})

    async def test_run_reserves_time_for_shutdown_before_absolute_deadline(self):
        class Slow(Client):
            async def complete(self, messages, tools=None):
                await asyncio.sleep(10)
        rt = Runtime(Config(self.task, self.root / "cleanup", seconds=2, shutdown_seconds=1,
                            status_file=None, model="test"), Slow())
        try:
            await asyncio.wait_for(rt.run(), 3)
            self.assertEqual(rt.run_meta["status"], "expired")
            self.assertGreater(rt.config.remaining, 0)
        finally:
            rt.store.close()

    async def test_live_budget_counts_unknown_usage_and_refuses_overcommitting(self):
        from unittest.mock import AsyncMock, patch
        from scripts.run_budgeted_live import BudgetedClient
        from research_llm import ModelClient, APIUnavailable
        client = BudgetedClient.__new__(BudgetedClient)
        client.budget = 10
        client.spent = client.reserved = 0.
        client.reservation = 6
        client.exhausted = False
        client.admission = asyncio.Semaphore(2)
        client.on_budget = None
        client.endpoint = {"pricing": {"prompt": .01, "completion": .02}}
        with patch.object(ModelClient, "complete", AsyncMock(return_value=({"role": "assistant", "content": "ok"}, {}))) as parent:
            _, usage = await client.complete([])
            self.assertEqual(usage["cost"], 6)
            self.assertEqual(client.spent, 6)
            self.assertEqual(client.reserved, 0)
            with self.assertRaises(APIUnavailable):
                await client.complete([])
            self.assertEqual(parent.await_count, 1)

    async def test_live_budget_accounts_concurrent_inflight_reservations(self):
        from unittest.mock import patch
        from scripts.run_budgeted_live import BudgetedClient
        from research_llm import ModelClient
        client = BudgetedClient.__new__(BudgetedClient)
        client.budget = 20
        client.spent = client.reserved = 0.
        client.reservation = 6
        client.exhausted = False
        client.admission = asyncio.Semaphore(2)
        client.on_budget = None
        peak = 0
        async def respond(*args, **kwargs):
            nonlocal peak
            peak = max(peak, client.spent + client.reserved)
            await asyncio.sleep(.02)
            return {"role": "assistant", "content": "ok"}, {"cost": 2}
        with patch.object(ModelClient, "complete", respond):
            await asyncio.gather(*(client.complete([]) for _ in range(4)))
        self.assertLessEqual(peak, 20)
        self.assertEqual(client.spent, 8)
        self.assertEqual(client.reserved, 0)


if __name__ == "__main__":
    unittest.main()
