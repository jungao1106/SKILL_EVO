from __future__ import annotations

import sys
import types
import unittest

from agents.claude_sdk_agent import (
    RUNNER_SCRIPT,
    _events_completed,
    _termination_status,
)


def load_runner_namespace() -> dict[str, object]:
    sdk = types.ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = object
    sdk.ClaudeSDKClient = object
    previous = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = sdk
    namespace: dict[str, object] = {"__name__": "claude_runner_test"}
    try:
        exec(compile(RUNNER_SCRIPT, "<claude-runner>", "exec"), namespace)
    finally:
        if previous is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = previous
    return namespace


class ClaudeRunnerFailureClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner_namespace()

    def test_502_result_is_transient_not_completed(self) -> None:
        event = {
            "sdk_message_class": "ResultMessage",
            "is_error": True,
            "api_error_status": 502,
            "result": "SSL UNEXPECTED_EOF; server-side issue, usually temporary",
        }
        self.assertEqual(
            self.runner["_provider_failure_kind"](event),
            "transient",
        )
        self.assertFalse(_events_completed([event]))
        self.assertEqual(_termination_status([event]), "result_error")

    def test_auth_and_nontransient_error_results_fail_closed(self) -> None:
        classify = self.runner["_provider_failure_kind"]
        self.assertEqual(
            classify(
                {
                    "sdk_message_class": "ResultMessage",
                    "is_error": True,
                    "api_error_status": 401,
                }
            ),
            "authentication",
        )
        self.assertEqual(
            classify(
                {
                    "sdk_message_class": "ResultMessage",
                    "is_error": True,
                    "result": "Agent process exited before producing a result",
                }
            ),
            "agent_error",
        )

    def test_successful_result_is_completed(self) -> None:
        event = {
            "sdk_message_class": "ResultMessage",
            "is_error": False,
            "result": "done",
        }
        self.assertIsNone(self.runner["_provider_failure_kind"](event))
        self.assertTrue(_events_completed([event]))
        self.assertEqual(_termination_status([event]), "result_message")

    def test_normal_messages_and_sdk_retry_not_misclassified(self) -> None:
        classify = self.runner["_provider_failure_kind"]
        for event in (
            {
                "sdk_message_class": "AssistantMessage",
                "message": {"content": [{"text": "handle timeout, 502, and rate limit"}]},
            },
            {
                "sdk_message_class": "SystemMessage",
                "subtype": "api_retry",
                "api_error_status": 502,
            },
            {
                "sdk_message_class": "UserMessage",
                "message": {"content": "authentication_failed is a test fixture"},
            },
        ):
            self.assertIsNone(classify(event))

    def test_budget_exhaustion_is_partial_verification_not_provider_infra(self) -> None:
        event = {
            "sdk_message_class": "ResultMessage",
            "is_error": True,
            "subtype": "error_max_turns",
        }
        self.assertEqual(
            self.runner["_provider_failure_kind"](event),
            "budget_exhausted",
        )
        self.assertEqual(_termination_status([event]), "budget_exhausted")


if __name__ == "__main__":
    unittest.main()
