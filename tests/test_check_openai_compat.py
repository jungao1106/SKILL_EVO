import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from scripts import check_openai_compat


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class CheckOpenAICompatTest(unittest.TestCase):
    def _run(self, body: dict):
        stdout = io.StringIO()
        stderr = io.StringIO()
        env = {
            "LLM_PROVIDER": "macaron",
            "MACARON_API_KEY": "unit-test-key",
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(check_openai_compat, "load_dotenv") as load_dotenv,
            mock.patch.object(
                check_openai_compat,
                "urlopen",
                return_value=FakeResponse(body),
            ) as urlopen,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            return_code = check_openai_compat.main()
        return return_code, stdout.getvalue(), stderr.getvalue(), load_dotenv, urlopen

    def test_macaron_profile_sends_valid_responses_probe(self) -> None:
        result = self._run({"status": "completed", "output_text": "ok"})
        return_code, stdout, stderr, load_dotenv, urlopen = result

        self.assertEqual(return_code, 0)
        self.assertIn("provider=macaron", stdout)
        self.assertEqual(stderr, "")
        load_dotenv.assert_called_once_with(
            check_openai_compat.ROOT / ".env",
            override=False,
        )
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(
            request.full_url,
            "https://pi-api-cn.macaron.xin/v1/responses",
        )
        self.assertEqual(payload["model"], "glm-5.2")
        self.assertGreaterEqual(payload["max_output_tokens"], 256)
        self.assertIs(payload["store"], False)

    def test_http_200_incomplete_response_is_failure(self) -> None:
        result = self._run(
            {
                "status": "incomplete",
                "output_text": "partial text must not count",
            }
        )
        return_code, stdout, stderr, _, _ = result

        self.assertEqual(return_code, 1)
        self.assertNotIn("OK", stdout)
        self.assertIn("did not complete", stderr)

    def test_completed_response_without_text_is_failure(self) -> None:
        return_code, stdout, stderr, _, _ = self._run(
            {"status": "completed", "output": []}
        )

        self.assertEqual(return_code, 1)
        self.assertNotIn("OK", stdout)
        self.assertIn("no output text", stderr)

    def test_reasoning_text_does_not_count_as_output_text(self) -> None:
        body = {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "reasoning_text", "text": "internal"}],
                }
            ],
        }
        return_code, stdout, stderr, _, _ = self._run(body)

        self.assertEqual(return_code, 1)
        self.assertNotIn("OK", stdout)
        self.assertIn("no output text", stderr)

    def test_nested_responses_output_text_is_accepted(self) -> None:
        body = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
        }
        return_code, stdout, stderr, _, _ = self._run(body)

        self.assertEqual(return_code, 0)
        self.assertIn("text='ok'", stdout)
        self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
