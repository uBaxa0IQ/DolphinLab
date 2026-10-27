import unittest
from uuid import uuid4

from pydantic import ValidationError

from dolphinlab.api.schemas import CreateGoal, CreateIteration
from dolphinlab.domain.research import GoalSpec, request_fingerprint


class ResearchContractsTest(unittest.TestCase):
    def test_nested_goal_json_string_is_validated_as_the_same_structured_goal(self):
        import json

        from dolphinlab.domain.conversation import FollowupSpec

        goal = {"question": "Use prior evidence", "prior_results": []}
        self.assertEqual(FollowupSpec(goal=goal), FollowupSpec(goal=json.dumps(goal)))
        with self.assertRaises(ValidationError):
            FollowupSpec(goal=json.dumps({**goal, "unrecognized_permission": True}))
        with self.assertRaises(ValidationError):
            FollowupSpec(goal="[]")

    def test_report_coverage_uses_explicit_unique_ids_without_confusing_c1_with_c10(self):
        from dolphinlab.research.results import uncovered_criteria

        criteria = ["C1: Monthly totals", "C2: Growth"]
        self.assertEqual(
            uncovered_criteria(criteria, [{"name": "C1: Recomputed from CSV"}]), [criteria[1]]
        )
        self.assertEqual(uncovered_criteria(criteria, [{"name": "C10: Another check"}]), criteria)
        self.assertEqual(
            uncovered_criteria(
                criteria, [{"name": "C1: Recomputed"}, {"name": "C2: Denominator checked"}]
            ),
            [],
        )
        ambiguous = ["C1: Monthly", "C1: Returns", "Unkeyed requirement"]
        self.assertEqual(uncovered_criteria(ambiguous, [{"name": "C1: Monthly"}]), ambiguous[1:])

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
