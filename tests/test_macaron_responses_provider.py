import argparse
import ast
import os
import unittest
from unittest import mock

from agents.pi_agent import _uses_reasoning_proxy
from providers import resolve_provider
from scripts import run_benchmark
from scripts import run_skill_evo_eval_only
from scripts import run_skill_evo_verified
from scripts import run_swegym_skill_evo_loop


SECRET = "unit-test-provider-secret"


def _args(provider: str = "marcron") -> argparse.Namespace:
    return argparse.Namespace(
        provider=provider,
        provider_base_url=None,
        provider_model=None,
        provider_api_key=SECRET,
        provider_api=None,
        dataset="example/dataset@1",
        concurrency=1,
        max_retries=1,
        retry_min_wait_sec=1.0,
        retry_max_wait_sec=2.0,
        retry_include=None,
        retry_exclude=None,
        agent_timeout_sec=None,
        agent_setup_timeout_sec=60.0,
        e2b_sandbox_timeout_sec=600,
        verifier_buffer_sec=60.0,
        override_cpus=1,
        override_memory_mb=4096,
        override_storage_mb=10240,
        model_context_window=None,
        model_max_tokens=None,
        result_only=False,
        force_build=False,
        keep_sandboxes=False,
        n_tasks=None,
        include_task_name=None,
        task_names_file=None,
    )


class MacaronResponsesProviderTest(unittest.TestCase):
    def test_profile_defaults_and_alias(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            canonical = resolve_provider("macaron")
            alias = resolve_provider("marcron")

        self.assertEqual(alias, canonical)
        self.assertEqual(canonical.default_base_url, "https://pi-api-cn.macaron.xin/v1")
        self.assertEqual(canonical.default_model, "glm-5.2")
        self.assertEqual(canonical.default_provider_api, "openai-responses")
        self.assertEqual(canonical.default_context_window, 200000)

    def test_responses_never_uses_completions_reasoning_proxy(self) -> None:
        base_url = "https://pi-api-cn.macaron.xin/v1"
        self.assertFalse(_uses_reasoning_proxy(base_url, "openai-responses"))
        self.assertTrue(_uses_reasoning_proxy(base_url, "openai-completions"))
        self.assertFalse(
            _uses_reasoning_proxy("https://router.example/v1", "openai-completions")
        )

    def test_evolution_entrypoints_materialize_macaron_child_env(self) -> None:
        configure_functions = (
            run_swegym_skill_evo_loop.configure_provider,
            run_skill_evo_verified.configure_provider,
            run_skill_evo_eval_only.configure_provider,
        )
        for configure in configure_functions:
            with self.subTest(module=configure.__module__):
                args = _args()
                env: dict[str, str] = {}
                with mock.patch.dict(os.environ, {}, clear=True):
                    provider = configure(args, env)

                self.assertEqual(provider.name, "macaron")
                self.assertEqual(args.provider, "macaron")
                self.assertIsNone(args.provider_api_key)
                self.assertEqual(env["LLM_PROVIDER"], "macaron")
                self.assertEqual(env["MACARON_API_KEY"], SECRET)
                self.assertEqual(
                    env["MACARON_BASE_URL"],
                    "https://pi-api-cn.macaron.xin/v1",
                )
                self.assertEqual(env["MACARON_MODEL"], "glm-5.2")
                self.assertEqual(env["MACARON_API"], "openai-responses")
                self.assertEqual(env["MACARON_CONTEXT_WINDOW"], "200000")

    def test_generated_benchmark_argv_never_contains_provider_key(self) -> None:
        cases = []
        for module in (run_skill_evo_verified, run_skill_evo_eval_only):
            args = _args()
            env: dict[str, str] = {}
            with mock.patch.dict(os.environ, {}, clear=True):
                provider = module.configure_provider(args, env)
            if module is run_skill_evo_verified:
                command = module.benchmark_command(
                    args,
                    provider=provider,
                    job_name="test-job",
                    use_skills=True,
                )
            else:
                command = module.benchmark_command(
                    args,
                    provider=provider,
                    job_name="test-job",
                )
            cases.append((module.__name__, command, env))

        args = _args()
        env = {}
        with mock.patch.dict(os.environ, {}, clear=True):
            provider = run_swegym_skill_evo_loop.configure_provider(args, env)
        command = run_swegym_skill_evo_loop.benchmark_command(
            args,
            provider=provider,
            dataset="example/dataset@1",
            benchmark_name="swe-gym",
            job_name="test-job",
            use_skills=False,
        )
        cases.append((run_swegym_skill_evo_loop.__name__, command, env))

        for module_name, command, child_env in cases:
            with self.subTest(module=module_name):
                self.assertNotIn("--provider-api-key", command)
                self.assertNotIn(SECRET, command)
                self.assertEqual(child_env["MACARON_API_KEY"], SECRET)
                self.assertIn("openai-responses", command)

    def test_benchmark_uses_macaron_context_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            provider = resolve_provider("macaron")
            context_window = run_benchmark._provider_int_env(
                provider,
                "CONTEXT_WINDOW",
                "128000",
            )
        self.assertEqual(context_window, 200000)

    def test_swegym_validation_candidate_decisions_are_loaded_from_disk(self) -> None:
        source = run_swegym_skill_evo_loop.Path(
            run_swegym_skill_evo_loop.__file__
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "materialize_failure_candidate_augmented_pack"
        ]
        self.assertEqual(len(calls), 1)
        promotion_keyword = next(
            keyword
            for keyword in calls[0].keywords
            if keyword.arg == "promotion_decisions"
        )
        self.assertIsInstance(promotion_keyword.value, ast.Call)
        self.assertIsInstance(promotion_keyword.value.func, ast.Name)
        self.assertEqual(promotion_keyword.value.func.id, "read_jsonl")


if __name__ == "__main__":
    unittest.main()
