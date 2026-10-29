import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        with patch.object(e2b_swebench, "_cache_dir_is_writable", return_value=False):
            resolved = e2b_swebench._resolve_template_lookup_cache_dir(
                Path("/unwritable/cache")
            )

        self.assertIsNone(resolved)

    def test_file_lock_degrades_when_lock_path_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            blocked_parent = Path(temp_dir) / "blocked"
            blocked_parent.write_text("not a directory", encoding="utf-8")

            async def acquire_lock() -> None:
                async with e2b_swebench._AsyncFileLock(blocked_parent / "lookup.lock"):
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
        imported_go_cache = e2b_swebench._template_resource_identity(
            2,
            8192,
            20480,
            strip_dockerfile_comments=True,
            imported_go_cache=True,
        )
        self.assertNotEqual(base, imported_go_cache)
        self.assertTrue(
            imported_go_cache.endswith(
                f"-{e2b_swebench.E2B_IMPORTED_GO_CACHE_POLICY_VERSION}"
            )
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
        self.assertFalse(
            any(candidate.endswith("deadbeef") for candidate in candidates)
        )

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
            patch.object(
                e2b_swebench.asyncio, "sleep", new_callable=AsyncMock
            ) as sleep,
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

    def test_cancelled_exec_kills_remote_command_before_propagating(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        handle = SimpleNamespace(
            wait=AsyncMock(side_effect=asyncio.CancelledError()),
            kill=AsyncMock(return_value=True),
        )
        commands = SimpleNamespace(run=AsyncMock(return_value=handle))
        environment._sandbox = SimpleNamespace(commands=commands)
        environment._workdir = "/app"
        environment._dockerfile_declared_env = {}
        environment._persistent_env = {}
        environment.default_user = None
        environment.logger = SimpleNamespace(warning=lambda *_args: None)

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(environment.exec("long-running-agent", user="root"))

        handle.kill.assert_awaited_once_with()
        commands.run.assert_awaited_once_with(
            cmd="long-running-agent",
            background=True,
            cwd="/app",
            envs=None,
            timeout=0,
            user="root",
        )

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

    def test_dockerfile_runtime_env_uses_final_stage_and_instruction_snapshot(
        self,
    ) -> None:
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

    def test_imported_image_tmp_go_caches_are_remapped_to_persistent_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                """\
FROM example.invalid/base
ENV TMPDIR=/tmp
ENV GOMODCACHE=$TMPDIR/gomodcache GOCACHE=/tmp/gocache
""",
                encoding="utf-8",
            )

            self.assertEqual(
                e2b_swebench._imported_image_go_cache_paths(
                    dockerfile, "example.invalid/task:latest"
                ),
                {
                    "GOMODCACHE": (
                        "/tmp/gomodcache",
                        "/opt/skills-evo/imported-go-cache-v1/mod",
                    ),
                    "GOCACHE": (
                        "/tmp/gocache",
                        "/opt/skills-evo/imported-go-cache-v1/build",
                    ),
                },
            )

    def test_go_cache_remap_requires_imported_image_and_tmp_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dockerfile = Path(temp_dir) / "Dockerfile"
            dockerfile.write_text(
                """\
FROM example.invalid/base
ENV GOMODCACHE=/var/cache/go-mod GOCACHE=/tmp/../var/go-build
""",
                encoding="utf-8",
            )

            self.assertEqual(
                e2b_swebench._imported_image_go_cache_paths(
                    dockerfile, "example.invalid/task:latest"
                ),
                {},
            )

            dockerfile.write_text(
                "ENV GOMODCACHE=/tmp/go-mod\n",
                encoding="utf-8",
            )
            self.assertEqual(
                e2b_swebench._imported_image_go_cache_paths(dockerfile, None),
                {},
            )

            dockerfile.write_text(
                "ENV GOCACHE=/tmp/go-build\n",
                encoding="utf-8",
            )
            self.assertEqual(
                e2b_swebench._imported_image_go_cache_paths(
                    dockerfile, "example.invalid/task:latest"
                ),
                {},
            )

            dockerfile.write_text(
                "ENV GOMODCACHE=/tmp/go-mod GOCACHE=/var/cache/go-build\n",
                encoding="utf-8",
            )
            self.assertEqual(
                e2b_swebench._imported_image_go_cache_paths(
                    dockerfile, "example.invalid/task:latest"
                ),
                {
                    "GOMODCACHE": (
                        "/tmp/go-mod",
                        "/opt/skills-evo/imported-go-cache-v1/mod",
                    )
                },
            )

    def test_imported_image_template_build_rehydrates_go_cache(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment.logger = SimpleNamespace(info=lambda *_args: None)
        environment.environment_name = "datacurve/go-task"
        environment._template_name = "team/go-task__go-cache-v1"
        environment._imported_go_cache_paths = {
            "GOMODCACHE": (
                "/tmp/gomodcache",
                "/opt/skills-evo/imported-go-cache-v1/mod",
            ),
            "GOCACHE": (
                "/tmp/gocache",
                "/opt/skills-evo/imported-go-cache-v1/build",
            ),
        }
        environment.task_env_config = SimpleNamespace(
            docker_image="example.invalid/task:latest",
            cpus=2,
            memory_mb=8192,
        )
        environment._workdir_from_dockerfile = MagicMock(return_value="/app")
        environment._remember_template_exists = MagicMock()
        builder = MagicMock()
        builder.run_cmd.return_value = builder
        template = MagicMock()
        template.from_image.return_value = builder

        with (
            patch.object(e2b_swebench, "Template", return_value=template),
            patch.object(
                e2b_swebench.AsyncTemplate,
                "build",
                new_callable=AsyncMock,
            ) as build,
            patch.object(
                e2b_swebench,
                "_template_build_semaphore",
                return_value=asyncio.Semaphore(1),
            ),
        ):
            asyncio.run(environment._create_template())

        template.from_image.assert_called_once_with(image="example.invalid/task:latest")
        builder.run_cmd.assert_called_once()
        command = builder.run_cmd.call_args.args[0]
        self.assertIn("go mod download all", command)
        self.assertIn("GOTOOLCHAIN=auto", command)
        self.assertIn("GOTOOLCHAIN=local", command)
        self.assertIn("/opt/skills-evo/imported-go-cache-v1/mod", command)
        self.assertIn("/opt/skills-evo/imported-go-cache-v1/toolchain", command)
        self.assertIn("ln -s", command)
        self.assertIn("policy_version=go-cache-v1", command)
        self.assertIn("restore_go_manifests", command)
        self.assertNotIn("|| true", command)
        self.assertEqual(builder.run_cmd.call_args.kwargs, {"user": "root"})
        build.assert_awaited_once_with(
            template=builder,
            name="team/go-task__go-cache-v1",
            cpu_count=2,
            memory_mb=8192,
        )

    def test_imported_image_without_volatile_go_cache_adds_no_build_layer(
        self,
    ) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment.logger = SimpleNamespace(info=lambda *_args: None)
        environment.environment_name = "datacurve/task"
        environment._template_name = "team/task"
        environment._imported_go_cache_paths = {}
        environment.task_env_config = SimpleNamespace(
            docker_image="example.invalid/task:latest",
            cpus=2,
            memory_mb=8192,
        )
        environment._remember_template_exists = MagicMock()
        builder = MagicMock()
        template = MagicMock()
        template.from_image.return_value = builder

        with (
            patch.object(e2b_swebench, "Template", return_value=template),
            patch.object(
                e2b_swebench.AsyncTemplate,
                "build",
                new_callable=AsyncMock,
            ),
            patch.object(
                e2b_swebench,
                "_template_build_semaphore",
                return_value=asyncio.Semaphore(1),
            ),
        ):
            asyncio.run(environment._create_template())

        builder.run_cmd.assert_not_called()

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
            self.assertEqual(
                environment._dockerfile_declared_env,
                {
                    "PYTHONPATH": "/app/src",
                    "PATH": "/task/bin:/usr/bin",
                },
            )

    def test_restore_dockerfile_env_preflights_go_cache_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            environment_dir = Path(temp_dir)
            (environment_dir / "Dockerfile").write_text(
                """\
FROM example.invalid/base
WORKDIR /app
ENV GOMODCACHE=/tmp/gomodcache GOCACHE=/tmp/gocache GOTOOLCHAIN=auto
""",
                encoding="utf-8",
            )
            environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
            environment.environment_dir = environment_dir
            environment.environment_name = "datacurve/go-task"
            environment.task_env_config = SimpleNamespace(
                docker_image="example.invalid/task:latest",
                allow_internet=False,
            )
            environment._force_allow_internet = False
            environment._persistent_env = {"GOMODCACHE": "/tmp/explicit"}
            environment._imported_go_cache_paths = {
                "GOMODCACHE": (
                    "/tmp/gomodcache",
                    "/opt/skills-evo/imported-go-cache-v1/mod",
                ),
                "GOCACHE": (
                    "/tmp/gocache",
                    "/opt/skills-evo/imported-go-cache-v1/build",
                ),
            }
            commands = SimpleNamespace(
                run=AsyncMock(
                    side_effect=[
                        SimpleNamespace(
                            exit_code=0,
                            stdout="PATH=/usr/bin\nHOME=/root\n",
                        ),
                        SimpleNamespace(exit_code=0, stdout="", stderr=""),
                    ]
                )
            )
            environment._sandbox = SimpleNamespace(commands=commands)

            asyncio.run(environment._restore_dockerfile_runtime_env())

            self.assertEqual(
                environment._persistent_env["GOMODCACHE"],
                "/opt/skills-evo/imported-go-cache-v1/mod",
            )
            self.assertEqual(
                environment._persistent_env["GOCACHE"],
                "/opt/skills-evo/imported-go-cache-v1/build",
            )
            self.assertEqual(
                environment._persistent_env["GOTOOLCHAIN"],
                "local",
            )
            self.assertEqual(environment._persistent_env["GOPROXY"], "off")
            self.assertEqual(environment._persistent_env["GOSUMDB"], "off")
            self.assertTrue(
                environment._persistent_env["PATH"].startswith(
                    "/opt/skills-evo/imported-go-cache-v1/toolchain/bin:"
                )
            )
            preflight = commands.run.await_args_list[1].args[0]
            self.assertIn("GOPROXY=off", preflight)
            self.assertIn("GOSUMDB=off", preflight)
            self.assertIn("GOTOOLCHAIN=local", preflight)
            self.assertNotIn("GOTOOLCHAIN=auto", preflight)
            self.assertIn('test -d "$MOD"\ntest -d "$BUILD"', preflight)
            self.assertNotIn('test -d "$MOD" "$BUILD"', preflight)
            self.assertIn("go mod download all", preflight)
            self.assertIn("require_ready", preflight)
            self.assertIn("restore_go_manifests", preflight)
            self.assertNotIn("|| true", preflight)
            self.assertEqual(commands.run.await_args_list[1].kwargs["timeout"], 300)

    def test_restore_only_remaps_volatile_module_cache(self) -> None:
        for declared_gocache in (None, "/var/cache/go-build"):
            with self.subTest(declared_gocache=declared_gocache):
                with tempfile.TemporaryDirectory() as temp_dir:
                    environment_dir = Path(temp_dir)
                    gocache_env = (
                        f" GOCACHE={declared_gocache}" if declared_gocache else ""
                    )
                    (environment_dir / "Dockerfile").write_text(
                        "FROM example.invalid/base\n"
                        "WORKDIR /app\n"
                        f"ENV GOMODCACHE=/tmp/gomodcache{gocache_env}\n",
                        encoding="utf-8",
                    )
                    environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
                    environment.environment_dir = environment_dir
                    environment.environment_name = "datacurve/go-task"
                    environment.task_env_config = SimpleNamespace(
                        docker_image="example.invalid/task:latest",
                        allow_internet=False,
                    )
                    environment._force_allow_internet = False
                    environment._persistent_env = {}
                    environment._imported_go_cache_paths = (
                        e2b_swebench._imported_image_go_cache_paths(
                            environment_dir / "Dockerfile",
                            environment.task_env_config.docker_image,
                        )
                    )
                    environment._sandbox = SimpleNamespace(
                        commands=SimpleNamespace(
                            run=AsyncMock(
                                side_effect=[
                                    SimpleNamespace(
                                        exit_code=0,
                                        stdout="PATH=/usr/bin\n",
                                    ),
                                    SimpleNamespace(
                                        exit_code=0,
                                        stdout="",
                                        stderr="",
                                    ),
                                ]
                            )
                        )
                    )

                    asyncio.run(environment._restore_dockerfile_runtime_env())

                    self.assertEqual(
                        set(environment._imported_go_cache_paths),
                        {"GOMODCACHE"},
                    )
                    self.assertEqual(
                        environment._persistent_env["GOMODCACHE"],
                        "/opt/skills-evo/imported-go-cache-v1/mod",
                    )
                    if declared_gocache is None:
                        self.assertNotIn("GOCACHE", environment._persistent_env)
                    else:
                        self.assertEqual(
                            environment._persistent_env["GOCACHE"],
                            declared_gocache,
                        )

    def test_restore_dockerfile_env_fails_when_go_cache_preflight_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            environment_dir = Path(temp_dir)
            (environment_dir / "Dockerfile").write_text(
                "WORKDIR /app\nENV GOMODCACHE=/tmp/gomodcache\n",
                encoding="utf-8",
            )
            environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
            environment.environment_dir = environment_dir
            environment.environment_name = "datacurve/go-task"
            environment.task_env_config = SimpleNamespace(
                docker_image="example.invalid/task:latest",
                allow_internet=False,
            )
            environment._force_allow_internet = False
            environment._persistent_env = {}
            environment._imported_go_cache_paths = {
                "GOMODCACHE": (
                    "/tmp/gomodcache",
                    "/opt/skills-evo/imported-go-cache-v1/mod",
                )
            }
            environment._sandbox = SimpleNamespace(
                commands=SimpleNamespace(
                    run=AsyncMock(
                        side_effect=[
                            SimpleNamespace(exit_code=0, stdout="PATH=/usr/bin\n"),
                            SimpleNamespace(exit_code=1, stdout="", stderr="offline"),
                        ]
                    )
                )
            )

            with self.assertRaisesRegex(
                RuntimeError, "Go cache is unavailable for offline execution"
            ) as raised:
                asyncio.run(environment._restore_dockerfile_runtime_env())
            self.assertIn("return_code=1", str(raised.exception))
            self.assertIn("offline", str(raised.exception))

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
        self.assertEqual(environment._dockerfile_declared_env, {})

    def test_exec_exports_only_dockerfile_env_with_per_exec_precedence(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment._dockerfile_declared_env = {
            "PATH": "/opt/venv/bin:/usr/bin",
            "PYTHONPATH": "/app",
            "VIRTUAL_ENV": "/opt/venv",
            "NOVITA_API_KEY": "docker-placeholder",
        }
        environment._persistent_env = {
            "PATH": "/opt/venv/bin:/usr/bin",
            "PYTHONPATH": "/configured/src",
            "VIRTUAL_ENV": "/opt/venv",
            "NOVITA_API_KEY": "provider-secret",
        }
        handle = SimpleNamespace(
            wait=AsyncMock(
                return_value=SimpleNamespace(stdout="", stderr="", exit_code=0)
            ),
            kill=AsyncMock(),
        )
        commands = SimpleNamespace(run=AsyncMock(return_value=handle))
        environment._sandbox = SimpleNamespace(commands=commands)
        environment._workdir = "/app"
        environment.default_user = None

        asyncio.run(
            environment.exec(
                "python -m pytest",
                env={
                    "PATH": "/per-exec/bin",
                    "OPENAI_COMPAT_API_KEY": "per-exec-secret",
                },
            )
        )

        command = commands.run.await_args.kwargs["cmd"]
        self.assertEqual(
            command,
            "\n".join(
                (
                    "export PATH=/per-exec/bin",
                    "export PYTHONPATH=/configured/src",
                    "export VIRTUAL_ENV=/opt/venv",
                    "python -m pytest",
                )
            ),
        )
        self.assertNotIn("NOVITA_API_KEY", command)
        self.assertNotIn("provider-secret", command)
        self.assertNotIn("OPENAI_COMPAT_API_KEY", command)
        self.assertNotIn("per-exec-secret", command)
        self.assertEqual(
            commands.run.await_args.kwargs["envs"],
            {
                "PATH": "/per-exec/bin",
                "PYTHONPATH": "/configured/src",
                "VIRTUAL_ENV": "/opt/venv",
                "NOVITA_API_KEY": "provider-secret",
                "OPENAI_COMPAT_API_KEY": "per-exec-secret",
            },
        )

    def test_exec_keeps_native_dockerfile_command_unchanged(self) -> None:
        environment = object.__new__(e2b_swebench.E2BSwebenchEnvironment)
        environment._dockerfile_declared_env = {}
        environment._persistent_env = {"NOVITA_API_KEY": "provider-secret"}
        handle = SimpleNamespace(
            wait=AsyncMock(
                return_value=SimpleNamespace(stdout="", stderr="", exit_code=0)
            ),
            kill=AsyncMock(),
        )
        commands = SimpleNamespace(run=AsyncMock(return_value=handle))
        environment._sandbox = SimpleNamespace(commands=commands)
        environment._workdir = "/app"
        environment.default_user = None

        asyncio.run(environment.exec("python -m pytest"))

        self.assertEqual(
            commands.run.await_args.kwargs["cmd"],
            "python -m pytest",
        )


if __name__ == "__main__":
    unittest.main()
