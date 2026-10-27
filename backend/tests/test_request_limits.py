"""Request admission must prevent paid calls, including through compaction."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from dolphinlab.infrastructure.llm import APIUnavailable, ModelClient, ResponseProblem
from dolphinlab.infrastructure.usage import Pricing
from dolphinlab.research.config import Config


class RequestLimitsTest(unittest.TestCase):
    def test_executor_package_versions_are_serialized_in_agent_context(self):
        from dolphinlab.research.context import build_context

        agent = {"role": "data_analyst", "history": []}
        messages = build_context(
            agent,
            {
                "original_task": "Analyse sales",
                "allowed_stack": {"pandas": "2.2.3"},
                "role_prompt": "Analyst",
            },
        )
        self.assertIn('"pandas": "2.2.3"', messages[0]["content"])

    def client(self, **options):
        config = Config(
            Path("task.md"),
            Path("runtime"),
            api_key="test-not-real",
            model="fake",
            base_url="https://example.invalid",
            **options,
        )
        client = ModelClient(config)
        create = AsyncMock(
            return_value=SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="OK", tool_calls=None),
                    )
                ],
            )
        )
        client.client.chat.completions.create = create
        create.return_value.choices[0].message.model_dump = lambda **_: {"content": "OK"}
        return client, create

    def test_output_ceiling_is_sent_and_legacy_has_no_cap(self):
        async def scenario():
            for options, expected in (({}, None), ({"request_output_ceiling": 8192}, 8192)):
                client, create = self.client(**options)
                try:
                    await client.complete([{"role": "user", "content": "test"}])
                    self.assertEqual(create.call_args.kwargs.get("max_tokens"), expected)
                finally:
                    await client.close()

        asyncio.run(scenario())

    def test_oversized_input_tools_and_multimodal_never_reach_provider(self):
        async def scenario():
            client, create = self.client(request_input_ceiling=5000)
            try:
                cases = [
                    ([{"role": "user", "content": "я" * 400}], None),
                    ([{"role": "user", "content": "ok"}], [{"description": "x" * 1000}]),
                    ([{"role": "user", "content": [{"type": "image_url"}]}], None),
                ]
                for messages, tools in cases:
                    with self.assertRaises(APIUnavailable):
                        await client.complete(messages, tools)
                create.assert_not_called()
                status = client.context_status(cases[0][0])
                self.assertTrue(status["needs_compaction"])
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_truncated_tool_generation_is_not_executed(self):
        async def scenario():
            client, create = self.client(request_output_ceiling=32)
            response = create.return_value
            response.choices[0].finish_reason = "length"
            response.choices[0].message.model_dump = lambda **_: {"content": "unfinished"}
            try:
                with self.assertRaises(ResponseProblem) as error:
                    await client.complete([{"role": "user", "content": "test"}])
                self.assertEqual(error.exception.category, "incomplete")
            finally:
                await client.close()

        asyncio.run(scenario())

    def test_bounded_tariff_reserves_every_full_request(self):
        tariff = Pricing(
            currency="RUB",
            input_per_million=110,
            output_per_million=360,
            max_input_tokens=65536,
            max_output_tokens=8192,
            enforce_request_limits=True,
        )
        self.assertAlmostEqual(tariff.reservation, 11.173888)

    def test_reported_cost_requires_matching_explicit_currency_and_finite_amount(self):
        tariff = Pricing(
            currency="RUB",
            input_per_million=110,
            output_per_million=360,
            max_input_tokens=65536,
            max_output_tokens=8192,
        )
        self.assertIsNone(tariff.reported_cost({"cost": 0.1}))
        tariff.provider_cost_currency = "USD"
        self.assertIsNone(tariff.reported_cost({"cost": 0.1}))
        tariff.provider_cost_currency = "RUB"
        for value in (-1, True, "0.1", float("nan"), float("inf"), None):
            with self.subTest(value=value):
                self.assertIsNone(tariff.reported_cost({"cost": value}))
        self.assertEqual(tariff.reported_cost({"cost": 0}), 0)
        self.assertEqual(tariff.reported_cost({"cost": 0.1}), 0.1)


if __name__ == "__main__":
    unittest.main()
