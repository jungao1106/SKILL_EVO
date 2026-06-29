import asyncio
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from scripts.run_benchmark import _patch_harbor_runtime


def _docker_compose_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_docker_compose(root: Path) -> None:
    """Ensure Harbor's Docker backend can run `docker compose`.

    The cluster image has Docker but may not expose the Compose v2 plugin through
    the default Docker config. Prefer an existing plugin over mutating system
    paths so SWE-Bench and other runners keep their environment unchanged.
    """
    if shutil.which("docker") is None:
        raise SystemExit("Docker is required for Terminal-Bench docker runs.")
    if _docker_compose_available():
        return

    candidate_configs: list[Path] = []
    if os.getenv("DOCKER_CONFIG"):
        candidate_configs.append(Path(os.environ["DOCKER_CONFIG"]))
    candidate_configs.extend(
        [
            root / ".cache" / "docker_config",
            Path("/tmp/skills_evo_docker_config"),
            Path.home() / ".docker",
        ]
    )

    for config_dir in candidate_configs:
        plugin = config_dir / "cli-plugins" / "docker-compose"
        if plugin.exists() and os.access(plugin, os.X_OK):
            os.environ["DOCKER_CONFIG"] = str(config_dir)
            if _docker_compose_available():
                return

    raise SystemExit(
        "Docker Compose v2 is required for Terminal-Bench docker runs, but "
        "`docker compose version` failed. Install the Compose v2 CLI plugin or "
        "set DOCKER_CONFIG to a directory containing cli-plugins/docker-compose."
    )


def _looks_like_missing_bash(result: Any) -> bool:
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return (
        "exec: \"bash\"" in output
        or "bash: executable file not found" in output
        or "bash: not found" in output
        or "no such file or directory: bash" in output
    )


def _patch_docker_environment() -> None:
    from harbor.environments.docker.docker import DockerEnvironment

    original_is_mounted = DockerEnvironment.is_mounted.fget
    if not getattr(original_is_mounted, "_skills_evo_tb2_unmounted_patch", False):

        def docker_logs_require_cp(self: Any) -> bool:
            return False

        docker_logs_require_cp._skills_evo_tb2_unmounted_patch = True  # type: ignore[attr-defined]
        DockerEnvironment.is_mounted = property(docker_logs_require_cp)

    original_start = DockerEnvironment.start
    if not getattr(original_start, "_skills_evo_tb2_host_log_perms_patch", False):

        async def start_with_host_log_permissions(
            self: Any, force_build: bool
        ) -> None:
            for path in (
                self.trial_paths.agent_dir,
                self.trial_paths.verifier_dir,
                self.trial_paths.artifacts_dir,
            ):
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o777)
            await original_start(self, force_build)

        start_with_host_log_permissions._skills_evo_tb2_host_log_perms_patch = True  # type: ignore[attr-defined]
        DockerEnvironment.start = start_with_host_log_permissions

    original_run_compose = DockerEnvironment._run_docker_compose_command
    if not getattr(original_run_compose, "_skills_evo_tb2_log_perm_refresh_patch", False):

        async def run_compose_with_log_permission_refresh(
            self: Any,
            command: list[str],
            check: bool = True,
            timeout_sec: int | None = None,
        ) -> Any:
            result = await original_run_compose(
                self,
                command=command,
                check=check,
                timeout_sec=timeout_sec,
            )
            if command and command[0] == "cp":
                for path in (
                    self.trial_paths.agent_dir,
                    self.trial_paths.verifier_dir,
                    self.trial_paths.artifacts_dir,
                ):
                    if path.exists():
                        path.chmod(0o777)
                        for child in path.rglob("*"):
                            try:
                                child.chmod(0o666 if child.is_file() else 0o777)
                            except OSError:
                                pass
            return result

        run_compose_with_log_permission_refresh._skills_evo_tb2_log_perm_refresh_patch = True  # type: ignore[attr-defined]
        DockerEnvironment._run_docker_compose_command = run_compose_with_log_permission_refresh

    original_download_dir = DockerEnvironment.download_dir
    if not getattr(original_download_dir, "_skills_evo_tb2_no_chown_patch", False):

        async def download_dir_without_container_chown(
            self: Any, source_dir: str, target_dir: Path | str
        ) -> None:
            Path(target_dir).mkdir(parents=True, exist_ok=True)
            await self._run_docker_compose_command(
                [
                    "cp",
                    f"main:{source_dir}/.",
                    str(target_dir),
                ],
                check=True,
            )

        download_dir_without_container_chown._skills_evo_tb2_no_chown_patch = True  # type: ignore[attr-defined]
        DockerEnvironment.download_dir = download_dir_without_container_chown

    original_exec = DockerEnvironment.exec
    if getattr(original_exec, "_skills_evo_tb2_shell_fallback_patch", False):
        return

    def _read_text(path: Path) -> str | None:
        if not path.exists():
            return None
        return path.read_text(errors="replace")

    async def exec_with_shell_fallback(
        self: Any,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> Any:
        from harbor.environments.base import ExecResult

        if "/logs/verifier/test-stdout.txt" in command:
            verifier_dir = self.trial_paths.verifier_dir
            verifier_dir.mkdir(parents=True, exist_ok=True)
            try:
                verifier_dir.chmod(0o777)
            except OSError:
                pass
            try:
                self.trial_paths.test_stdout_path.touch(exist_ok=True)
                self.trial_paths.test_stdout_path.chmod(0o666)
            except OSError:
                pass

        resolved_user = self._resolve_user(user)
        merged_env = self._merge_env(env)
        exec_id = uuid.uuid4().hex
        remote_dir = f"/tmp/harbor-exec-{exec_id}"
        local_dir = Path(tempfile.mkdtemp(prefix=f"harbor-exec-{exec_id}-"))
        stdout_path = local_dir / "stdout"
        stderr_path = local_dir / "stderr"
        rc_path = local_dir / "rc"
        done_path = local_dir / "done"

        script_path = f"{remote_dir}/command.sh"
        script_delimiter = f"HARBOR_TB2_EXEC_SCRIPT_{exec_id}"
        wrapped_command = (
            f"mkdir -p {shlex.quote(remote_dir)}\n"
            f"cat > {shlex.quote(script_path)} <<'{script_delimiter}'\n"
            f"{command}\n"
            f"{script_delimiter}\n"
            f"chmod +x {shlex.quote(script_path)}\n"
            f"bash {shlex.quote(script_path)} > {shlex.quote(remote_dir)}/stdout "
            f"2> {shlex.quote(remote_dir)}/stderr\n"
            "__harbor_rc=$?\n"
            f"printf '%s\\n' \"$__harbor_rc\" > {shlex.quote(remote_dir)}/rc\n"
            f"touch {shlex.quote(remote_dir)}/done\n"
            "exit 0\n"
        )

        exec_command = ["exec", "-T"]
        if cwd:
            exec_command.extend(["-w", cwd])
        if merged_env:
            for key, value in merged_env.items():
                exec_command.extend(["-e", f"{key}={value}"])
        if resolved_user is not None:
            exec_command.extend(["-u", str(resolved_user)])
        exec_command.extend(["main", "bash", "-c", wrapped_command])
        result = await self._run_docker_compose_command(
            exec_command,
            check=False,
            timeout_sec=timeout_sec,
        )
        if result.return_code != 0 and _looks_like_missing_bash(result):
            sh_wrapped_command = wrapped_command.replace("set -o pipefail;", "")
            exec_command = ["exec", "-T"]
            if cwd:
                exec_command.extend(["-w", cwd])
            if merged_env:
                for key, value in merged_env.items():
                    exec_command.extend(["-e", f"{key}={value}"])
            if resolved_user is not None:
                exec_command.extend(["-u", str(resolved_user)])
            exec_command.extend(["main", "sh", "-lc", sh_wrapped_command])
            result = await self._run_docker_compose_command(
                exec_command,
                check=False,
                timeout_sec=timeout_sec,
            )

        if result.return_code != 0:
            return result

        effective_timeout_sec = timeout_sec or int(
            os.getenv("TERMINAL_BENCH_EXEC_TIMEOUT_SEC", "1800")
        )
        start_time = time.monotonic()
        while True:
            await self._run_docker_compose_command(
                ["cp", f"main:{remote_dir}/done", str(done_path)],
                check=False,
            )
            if done_path.exists():
                break
            if time.monotonic() - start_time > effective_timeout_sec:
                return ExecResult(
                    stdout=_read_text(stdout_path),
                    stderr=(
                        (_read_text(stderr_path) or "")
                        + f"\nCommand timed out after {effective_timeout_sec} seconds"
                    ),
                    return_code=124,
                )
            await asyncio.sleep(0.5)

        await self._run_docker_compose_command(
            ["cp", f"main:{remote_dir}/stdout", str(stdout_path)],
            check=False,
        )
        await self._run_docker_compose_command(
            ["cp", f"main:{remote_dir}/stderr", str(stderr_path)],
            check=False,
        )
        await self._run_docker_compose_command(
            ["cp", f"main:{remote_dir}/rc", str(rc_path)],
            check=False,
        )

        try:
            return_code = int((_read_text(rc_path) or "1").strip())
        except ValueError:
            return_code = 1

        return ExecResult(
            stdout=_read_text(stdout_path),
            stderr=_read_text(stderr_path),
            return_code=return_code,
        )

    exec_with_shell_fallback._skills_evo_tb2_shell_fallback_patch = True  # type: ignore[attr-defined]
    DockerEnvironment.exec = exec_with_shell_fallback


def _patch_verifier_diagnostics() -> None:
    from harbor.verifier.verifier import RewardFileNotFoundError, Verifier

    original_verify = Verifier.verify
    if getattr(original_verify, "_skills_evo_tb2_diagnostics_patch", False):
        return

    async def verify_with_diagnostics(self: Any) -> Any:
        try:
            return await original_verify(self)
        except RewardFileNotFoundError:
            try:
                probe = await self._environment.exec(
                    command=(
                        "echo '[harbor-tb2] verifier diagnostics'; "
                        "echo '[harbor-tb2] /logs/verifier'; "
                        "ls -la /logs/verifier 2>&1 || true; "
                        "echo '[harbor-tb2] /tests'; "
                        "ls -la /tests 2>&1 || true; "
                        "echo '[harbor-tb2] test stdout'; "
                        "sed -n '1,200p' /logs/verifier/test-stdout.txt 2>&1 || true"
                    ),
                    user="root",
                )
                self._trial_paths.test_stdout_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )
                with self._trial_paths.test_stdout_path.open("a") as handle:
                    handle.write("\n[harbor-tb2] reward file was not produced.\n")
                    handle.write(f"[harbor-tb2] diagnostic command: {shlex.quote(probe.stdout or '')}\n")
                    if probe.stderr:
                        handle.write(f"[harbor-tb2] diagnostic stderr: {probe.stderr}\n")
                    handle.write(f"[harbor-tb2] diagnostic return_code: {probe.return_code}\n")
            except Exception:
                pass
            raise

    verify_with_diagnostics._skills_evo_tb2_diagnostics_patch = True  # type: ignore[attr-defined]
    Verifier.verify = verify_with_diagnostics


def patch_terminal_bench_runtime(*, result_only: bool = False) -> None:
    _patch_harbor_runtime(result_only=result_only)
    _patch_docker_environment()
    _patch_verifier_diagnostics()
