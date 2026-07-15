import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from environments import e2b_swebench


class TemplateLookupCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        e2b_swebench._TEMPLATE_LOOKUP_CACHE_DIRS.clear()
        e2b_swebench._SANDBOX_CREATE_NEXT_AT = 0.0

    def tearDown(self) -> None:
        e2b_swebench._TEMPLATE_LOOKUP_CACHE_DIRS.clear()
        e2b_swebench._SANDBOX_CREATE_NEXT_AT = 0.0

    def test_unwritable_preferred_cache_uses_xdg_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blocked = root / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            xdg_cache = root / "xdg-cache"

            with patch.dict(os.environ, {"XDG_CACHE_HOME": str(xdg_cache)}):
                resolved = e2b_swebench._resolve_template_lookup_cache_dir(blocked)

            expected = xdg_cache / "skill-evo/e2b_template_lookup"
            self.assertEqual(resolved, expected)
            self.assertTrue(expected.is_dir())

    def test_cache_resolution_can_fall_back_to_memory(self) -> None:
        with patch.object(
            e2b_swebench, "_cache_dir_is_writable", return_value=False
        ):
            resolved = e2b_swebench._resolve_template_lookup_cache_dir(
                Path("/unwritable/cache")
            )

        self.assertIsNone(resolved)

    def test_file_lock_degrades_when_lock_path_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            blocked_parent = Path(temp_dir) / "blocked"
            blocked_parent.write_text("not a directory", encoding="utf-8")

            async def acquire_lock() -> None:
                async with e2b_swebench._AsyncFileLock(
                    blocked_parent / "lookup.lock"
                ):
                    pass

            asyncio.run(acquire_lock())

    def test_template_identity_is_resource_and_build_policy_specific(self) -> None:
        base = e2b_swebench._template_resource_identity(
            2, 8192, 20480, strip_dockerfile_comments=True
        )
        self.assertNotEqual(
            base,
            e2b_swebench._template_resource_identity(
                1, 8192, 20480, strip_dockerfile_comments=True
            ),
        )
        self.assertNotEqual(
            base,
            e2b_swebench._template_resource_identity(
                2, 8192, 10240, strip_dockerfile_comments=True
            ),
        )
        self.assertNotEqual(
            base,
            e2b_swebench._template_resource_identity(
                2, 8192, 20480, strip_dockerfile_comments=False
            ),
        )

        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment._template_namespace = "team"
        environment.environment_name = "datacurve/task"
        environment._environment_hash = "deadbeef"
        environment._pi_template_suffix = ""
        environment._template_resource_identity = base
        candidates = environment._candidate_template_names()
        self.assertTrue(candidates)
        self.assertTrue(all(base in candidate for candidate in candidates))
        self.assertFalse(any(candidate.endswith("deadbeef") for candidate in candidates))

    def test_runtime_resource_check_validates_cpu_memory_and_disk(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment._sandbox = SimpleNamespace(
            get_info=AsyncMock(
                return_value=SimpleNamespace(cpu_count=2, memory_mb=8192)
            )
        )
        environment.task_env_config = SimpleNamespace(
            cpus=2, memory_mb=8192, storage_mb=20480
        )
        environment._template_name = "team/task__c2-m8192-s20480"
        environment.environment_name = "task"
        environment.exec = AsyncMock(
            return_value=SimpleNamespace(stdout="20480\n", return_code=0)
        )

        asyncio.run(environment._validate_sandbox_resources())

        environment._sandbox.get_info = AsyncMock(
            return_value=SimpleNamespace(cpu_count=1, memory_mb=8192)
        )
        with self.assertRaises(e2b_swebench.E2BResourceMismatchError):
            asyncio.run(environment._validate_sandbox_resources())

    def test_file_lock_degrades_when_flock_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "lookup.lock"

            async def acquire_lock() -> None:
                with patch.object(
                    e2b_swebench.fcntl,
                    "flock",
                    side_effect=OSError("locking unsupported"),
                ):
                    async with e2b_swebench._AsyncFileLock(lock_path):
                        pass

            asyncio.run(acquire_lock())

    def test_sandbox_create_rate_slots_are_process_wide_and_loop_agnostic(self) -> None:
        async def reserve_two_slots() -> None:
            await asyncio.gather(
                e2b_swebench._wait_for_sandbox_create_slot(),
                e2b_swebench._wait_for_sandbox_create_slot(),
            )

        with (
            patch.dict(os.environ, {"E2B_SANDBOX_CREATE_RATE_PER_SEC": "4"}),
            patch.object(e2b_swebench.time, "monotonic", return_value=100.0),
            patch.object(e2b_swebench.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            asyncio.run(reserve_two_slots())
            asyncio.run(e2b_swebench._wait_for_sandbox_create_slot())

        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.25, 0.5],
        )

    def test_environment_start_retry_deletes_previous_sandbox_handle(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        previous_sandbox = SimpleNamespace(kill=AsyncMock())
        environment._sandbox = previous_sandbox
        environment.environment_name = "datacurve/task"
        environment.logger = SimpleNamespace(warning=lambda *_args: None)

        asyncio.run(environment._cleanup_sandbox_before_restart())

        previous_sandbox.kill.assert_awaited_once()
        self.assertIsNone(environment._sandbox)

    def test_dockerfile_runtime_env_resolves_against_base_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                """\
FROM example.invalid/base
ENV DENO_DIR=/deno-cache
ENV PATH=\"/root/go/bin:${PATH}\"
ENV FIRST=one SECOND=\"two words\"
ENV LEGACY legacy value
""",
                encoding="utf-8",
            )

            self.assertEqual(
                e2b_swebench._dockerfile_runtime_env(
                    dockerfile,
                    {"PATH": "/usr/local/bin:/usr/bin"},
                ),
                {
                    "DENO_DIR": "/deno-cache",
                    "PATH": "/root/go/bin:/usr/local/bin:/usr/bin",
                    "FIRST": "one",
                    "SECOND": "two words",
                    "LEGACY": "legacy value",
                },
            )

    def test_dockerfile_runtime_env_uses_final_stage_and_instruction_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                """\
FROM example.invalid/base AS builder
ENV FROM_BUILDER=yes PATH=/builder:$PATH
FROM example.invalid/runtime
ENV A=old
ENV A=new B=$A
ENV FINAL=yes
""",
                encoding="utf-8",
            )

            self.assertEqual(
                e2b_swebench._dockerfile_runtime_env(
                    dockerfile,
                    {"PATH": "/usr/bin"},
                ),
                {"A": "new", "B": "old", "FINAL": "yes"},
            )

    def test_dockerfile_runtime_env_inherits_named_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                """\
FROM example.invalid/base AS builder
ENV BUILDER_ENV=preserved
FROM builder
ENV FINAL_ENV=yes
""",
                encoding="utf-8",
            )

            self.assertEqual(
                e2b_swebench._dockerfile_runtime_env(dockerfile, {}),
                {"BUILDER_ENV": "preserved", "FINAL_ENV": "yes"},
            )

    def test_restore_dockerfile_env_keeps_explicit_runtime_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            environment_dir = Path(temp_dir)
            (environment_dir / "Dockerfile").write_text(
                "ENV PYTHONPATH=/app/src\nENV PATH=/task/bin:$PATH\n",
                encoding="utf-8",
            )
            environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
            environment.environment_dir = environment_dir
            environment.environment_name = "datacurve/task"
            environment.task_env_config = SimpleNamespace(docker_image="image:tag")
            environment._persistent_env = {"PYTHONPATH": "/explicit"}
            environment._sandbox = SimpleNamespace(
                commands=SimpleNamespace(
                    run=AsyncMock(
                        return_value=SimpleNamespace(
                            exit_code=0,
                            stdout="PATH=/usr/bin\nHOME=/root\n",
                        )
                    )
                )
            )

            asyncio.run(environment._restore_dockerfile_runtime_env())

            self.assertEqual(
                environment._persistent_env,
                {
                    "PYTHONPATH": "/explicit",
                    "PATH": "/task/bin:/usr/bin",
                },
            )

    def test_restore_dockerfile_env_skips_native_dockerfile_templates(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        commands = SimpleNamespace(run=AsyncMock())
        environment._sandbox = SimpleNamespace(commands=commands)
        environment.task_env_config = SimpleNamespace(docker_image=None)
        environment._persistent_env = {"PATH": "/already/from/dockerfile"}

        asyncio.run(environment._restore_dockerfile_runtime_env())

        commands.run.assert_not_awaited()
        self.assertEqual(
            environment._persistent_env,
            {"PATH": "/already/from/dockerfile"},
        )


if __name__ == "__main__":
    unittest.main()
