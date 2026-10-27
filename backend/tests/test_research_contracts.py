import unittest
from uuid import uuid4

from pydantic import ValidationError

from dolphinlab.api.schemas import CreateGoal, CreateIteration
from dolphinlab.domain.research import GoalSpec, request_fingerprint


class ResearchContractsTest(unittest.TestCase):
    def test_empty_question_is_rejected(self):
        for question in ("", "   ", "\n"):
            with self.subTest(question=question), self.assertRaises(ValidationError):
                GoalSpec(question=question)

    def test_task_contains_conditions_without_promoting_open_questions(self):
        goal = GoalSpec(
            question=" Compare regions ",
            definitions={"revenue": "after discounts"},
            constraints=("RUB",),
            expected_outputs=("checked comparison",),
            open_questions=("Are returns included?",),
        )
        self.assertEqual(goal.question, "Compare regions")
        self.assertIn("revenue: after discounts", goal.task_text())
        self.assertIn("not established facts", goal.task_text())
        self.assertEqual(GoalSpec(question="Simple task").task_text(), "Simple task")

    def test_request_ids_do_not_change_content_fingerprint(self):
        body = CreateGoal(client_id=uuid4(), expected_revision=1, goal=GoalSpec(question="A"))
        retry = body.model_copy(update={"client_id": uuid4()})
        self.assertEqual(request_fingerprint(body), request_fingerprint(retry))
        changed = body.model_copy(update={"expected_revision": 2})
        self.assertNotEqual(request_fingerprint(body), request_fingerprint(changed))

    def test_invalid_sequence_and_unknown_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            CreateIteration(client_id=uuid4(), goal_revision_id=uuid4(), expected_sequence=-1)
        with self.assertRaises(ValidationError):
            GoalSpec(question="A", approved=True)

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaises(ValidationError):
            GoalSpec(question="A", schema_version=2)
        self.assertEqual(GoalSpec(question="A").model_dump()["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
