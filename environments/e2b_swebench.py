import asyncio
import fcntl
import hashlib
import json
import os
import re
import shlex
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from dirhash import dirhash
from dockerfile_parse import DockerfileParser
from e2b import AsyncSandbox, AsyncTemplate, Template
from e2b.exceptions import (
    BuildException,
    RateLimitException,
    SandboxException,
    TemplateException,
)
from e2b.sandbox.commands.command_handle import CommandExitException
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
)

from harbor.environments.base import ExecResult
from harbor.environments.e2b import E2BEnvironment
from harbor.models.trial.paths import EnvironmentPaths


_TEMPLATE_BUILD_SEMAPHORE: asyncio.Semaphore | None = None
_TEMPLATE_BUILD_SEMAPHORE_LIMIT: int | None = None
_TEMPLATE_LOOKUP_CACHE: dict[tuple[str, tuple[str, ...]], str | None] = {}
_TEMPLATE_LOOKUP_LOCKS: dict[tuple[str, tuple[str, ...]], asyncio.Lock] = {}
_TEMPLATE_LOOKUP_CACHE_DIRS: dict[tuple[str, ...], Path | None] = {}
_SANDBOX_CREATE_RATE_LOCK = threading.Lock()
_SANDBOX_CREATE_NEXT_AT = 0.0
E2B_TEMPLATE_BUILD_POLICY_VERSION = "r3"
PI_CODING_AGENT_VERSION = "0.80.6"


class E2BResourceMismatchError(RuntimeError):
    pass


def _template_build_semaphore() -> asyncio.Semaphore:
    global _TEMPLATE_BUILD_SEMAPHORE, _TEMPLATE_BUILD_SEMAPHORE_LIMIT

    try:
        limit = int(os.getenv("E2B_TEMPLATE_BUILD_CONCURRENCY", "20"))
    except ValueError:
        limit = 20
    limit = max(1, limit)

    if (
        _TEMPLATE_BUILD_SEMAPHORE is None
        or _TEMPLATE_BUILD_SEMAPHORE_LIMIT != limit
    ):
        _TEMPLATE_BUILD_SEMAPHORE = asyncio.Semaphore(limit)
        _TEMPLATE_BUILD_SEMAPHORE_LIMIT = limit

    return _TEMPLATE_BUILD_SEMAPHORE


async def _wait_for_sandbox_create_slot() -> None:
    global _SANDBOX_CREATE_NEXT_AT
    try:
        rate_per_sec = float(os.getenv("E2B_SANDBOX_CREATE_RATE_PER_SEC", "4"))
    except ValueError:
        rate_per_sec = 4.0
    minimum_interval = 1.0 / max(0.1, rate_per_sec)
    with _SANDBOX_CREATE_RATE_LOCK:
        now = time.monotonic()
        scheduled_at = max(now, _SANDBOX_CREATE_NEXT_AT)
        _SANDBOX_CREATE_NEXT_AT = scheduled_at + minimum_interval
    delay = scheduled_at - now
    if delay > 0:
        await asyncio.sleep(delay)


class _AsyncFileLock:
    def __init__(self, path: Path | None):
        self.path = path
        self._handle: Any | None = None

    async def __aenter__(self) -> "_AsyncFileLock":
        if self.path is None:
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+", encoding="utf-8")
        except OSError:
            # The process-local asyncio lock still serializes template builds.
            # Disk locking is only needed to coordinate with other processes.
            self._handle = None
            return self
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                await asyncio.sleep(0.2)
            except OSError:
                self._handle.close()
                self._handle = None
                return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _cache_dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".write-test-", dir=path):
            pass
    except OSError:
        return False
    return True


def _template_lookup_cache_dir_candidates(preferred: Path) -> list[Path]:
    candidates = [preferred.expanduser()]
    xdg_cache_home = os.getenv("XDG_CACHE_HOME")
    if xdg_cache_home:
        candidates.append(
            Path(xdg_cache_home).expanduser() / "skill-evo/e2b_template_lookup"
        )
    else:
        try:
            candidates.append(
                Path.home() / ".cache/skill-evo/e2b_template_lookup"
            )
        except RuntimeError:
            pass

    uid = getattr(os, "getuid", lambda: "unknown")()
    candidates.append(
        Path(tempfile.gettempdir()) / f"skill-evo-e2b-template-lookup-{uid}"
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = os.path.abspath(str(candidate))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(candidate)
    return unique


def _resolve_template_lookup_cache_dir(preferred: Path) -> Path | None:
    candidates = _template_lookup_cache_dir_candidates(preferred)
    cache_key = tuple(os.path.abspath(str(path)) for path in candidates)
    if cache_key in _TEMPLATE_LOOKUP_CACHE_DIRS:
        return _TEMPLATE_LOOKUP_CACHE_DIRS[cache_key]

    resolved = next(
        (path for path in candidates if _cache_dir_is_writable(path)),
        None,
    )
    _TEMPLATE_LOOKUP_CACHE_DIRS[cache_key] = resolved
    return resolved


def _safe_template_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.")
    return segment or "swebench-task"


def _template_resource_identity(
    cpus: int,
    memory_mb: int,
    storage_mb: int,
    *,
    strip_dockerfile_comments: bool,
) -> str:
    comment_policy = "strip" if strip_dockerfile_comments else "keep"
    return (
        f"c{cpus}-m{memory_mb}-s{storage_mb}-"
        f"{E2B_TEMPLATE_BUILD_POLICY_VERSION}-{comment_policy}"
    )


def _benchmark_log(message: str) -> None:
    log_path = os.getenv("SKILL_EVO_BENCHMARK_LOG")
    if not log_path:
        return
    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        return


def _strip_dockerfile_comments(content: str) -> str:
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def _normalize_from_image_refs(content: str) -> str:
    lines: list[str] = []
    for line in content.splitlines():
        match = re.match(r"^(\s*FROM\s+(?:--platform=\S+\s+)?)(\S+)(.*)$", line)
        if not match:
            lines.append(line)
            continue

        prefix, image_ref, suffix = match.groups()
        # Docker image repository names must be lowercase. SWE-Gym includes
        # instance ids such as Project-MONAI in generated image names.
        lines.append(f"{prefix}{image_ref.lower()}{suffix}")
    return "\n".join(lines) + "\n"


_DOCKER_ENV_REFERENCE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|"
    r"(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)


def _dockerfile_runtime_env(
    dockerfile_path: Path,
    base_env: dict[str, str],
) -> dict[str, str]:
    """Resolve Dockerfile ENV instructions lost by E2B image imports."""

    resolved = dict(base_env)
    declared: dict[str, str] = {}
    stage_envs: dict[str, dict[str, str]] = {}
    current_stage_keys: list[str] = []
    stage_index = -1
    structure = DockerfileParser(path=str(dockerfile_path)).structure
    for instruction in structure:
        operation = instruction.get("instruction")
        if operation == "FROM":
            tokens = shlex.split(str(instruction.get("value") or ""), posix=True)
            tokens = [token for token in tokens if not token.startswith("--")]
            if not tokens:
                continue
            stage_index += 1
            source = tokens[0]
            inherited = stage_envs.get(source, {})
            declared = dict(inherited)
            resolved = {**base_env, **declared}
            current_stage_keys = [str(stage_index)]
            for index, token in enumerate(tokens[:-1]):
                if token.upper() == "AS":
                    current_stage_keys.append(tokens[index + 1])
                    break
            for key in current_stage_keys:
                stage_envs[key] = dict(declared)
            continue
        if operation != "ENV":
            continue
        tokens = shlex.split(str(instruction.get("value") or ""), posix=True)
        if not tokens:
            continue
        if "=" not in tokens[0]:
            assignments = [(tokens[0], " ".join(tokens[1:]))]
        else:
            assignments = [
                token.split("=", 1)
                for token in tokens
                if "=" in token
            ]
        before_instruction = dict(resolved)
        pending: list[tuple[str, str]] = []
        for key, raw_value in assignments:
            value = _DOCKER_ENV_REFERENCE.sub(
                lambda match: before_instruction.get(
                    match.group("braced") or match.group("plain"),
                    "",
                ),
                raw_value,
            )
            pending.append((key, value))
        for key, value in pending:
            resolved[key] = value
            declared[key] = value
        for stage_key in current_stage_keys:
            stage_envs[stage_key] = dict(declared)
    return declared


def _is_missing_or_unlaunchable_template_error(exc: SandboxException) -> bool:
    message = str(exc).lower()
    if "tag 'default' does not exist" in message:
        return True
    return "template" in message and (
        "not found" in message
        or "does not exist" in message
        or "not exist" in message
        or "missing" in message
    )


PI_TEMPLATE_INSTALL_DOCKERFILE = rf"""
RUN if command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y curl ca-certificates git jq ripgrep; elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl ca-certificates git jq ripgrep nodejs npm bash; elif command -v yum >/dev/null 2>&1; then yum install -y curl ca-certificates git jq ripgrep; fi
RUN set -e; if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash; export NVM_DIR="/root/.nvm"; . "$NVM_DIR/nvm.sh"; nvm install 22; nvm alias default 22; fi; if [ -s /root/.nvm/nvm.sh ]; then . /root/.nvm/nvm.sh; fi; npm install -g @earendil-works/pi-coding-agent@{PI_CODING_AGENT_VERSION}; pi --version
RUN set -e; for bin in node npm npx pi; do BIN_PATH="$(command -v "$bin" 2>/dev/null || true)"; if [ -n "$BIN_PATH" ] && [ "$BIN_PATH" != "/usr/local/bin/$bin" ]; then ln -sf "$BIN_PATH" "/usr/local/bin/$bin"; fi; done
"""


class E2BSwebenchEnvironment(E2BEnvironment):
    """E2B adapter that uses the caller's team namespace for SWE-Bench templates."""

    def __init__(
        self,
        *args: Any,
        template_namespace: str | None = None,
        pi_template_suffix: str | None = None,
        strip_dockerfile_comments: bool = True,
        sandbox_timeout_sec: int | None = None,
        force_allow_internet: bool = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)

        namespace = template_namespace or os.getenv("E2B_TEMPLATE_NAMESPACE")
        namespace = _safe_template_segment(namespace or "anchen1011")
        digest = dirhash(self.environment_dir, "sha256")[:8]

        self._template_namespace = namespace
        self._environment_hash = digest
        self._strip_dockerfile_comments = strip_dockerfile_comments
        self._template_resource_identity = _template_resource_identity(
            self.task_env_config.cpus,
            self.task_env_config.memory_mb,
            self.task_env_config.storage_mb,
            strip_dockerfile_comments=strip_dockerfile_comments,
        )
        self._pi_template_suffix = (
            pi_template_suffix
            if pi_template_suffix is not None
            else os.getenv("E2B_PI_TEMPLATE_SUFFIX", "pi_c6d7003a")
        ).strip("_")
        self._template_name = self._build_template_name()
        self._sandbox_timeout_sec = self._resolve_sandbox_timeout_sec(
            sandbox_timeout_sec
        )
        self._force_allow_internet = force_allow_internet

    def _allow_internet_access(self) -> bool:
        return True if self._force_allow_internet else self.task_env_config.allow_internet

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Execute a command and stop it before propagating cancellation."""
        user = self._resolve_user(user)
        env = self._merge_env(env)
        if not self._sandbox:
            raise RuntimeError("Sandbox not found. Please start the environment first.")

        handle = await self._sandbox.commands.run(
            cmd=command,
            background=True,
            cwd=cwd or self._workdir,
            envs=env,
            timeout=timeout_sec or 0,
            user=str(user) if user is not None else "root",
        )
        try:
            result = await handle.wait()
        except asyncio.CancelledError:
            try:
                await asyncio.shield(handle.kill())
            except Exception as exc:
                self.logger.warning(
                    "Could not stop cancelled E2B command before returning: %s",
                    exc,
                )
            raise
        except CommandExitException as exc:
            result = exc

        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.exit_code,
        )

    def _legacy_template_base(self) -> str:
        return self.environment_name.replace("/", "__").replace(".", "-")

    def _safe_template_base(self) -> str:
        return _safe_template_segment(self.environment_name)

    def _qualified_template_name(self, base: str) -> str:
        return f"{self._template_namespace}/{base}"

    def _build_template_name(self) -> str:
        return self._qualified_template_name(
            f"{self._safe_template_base()}__{self._environment_hash}"
            f"__{self._template_resource_identity}"
        )

    def _pi_template_name(self) -> str | None:
        if not self._pi_template_suffix:
            return None
        legacy_name = f"{self._legacy_template_base()}__{self._environment_hash}"
        return self._qualified_template_name(
            f"{legacy_name}__{self._pi_template_suffix}"
            f"__{self._template_resource_identity}"
        )

    def _candidate_template_names(self) -> list[str]:
        legacy_name = (
            f"{self._legacy_template_base()}__{self._environment_hash}"
            f"__{self._template_resource_identity}"
        )
        candidates: list[str] = []
        pi_template_name = self._pi_template_name()
        if pi_template_name:
            candidates.append(pi_template_name)
        candidates.extend(
            [
                self._qualified_template_name(legacy_name),
                self._build_template_name(),
            ]
        )
        return list(dict.fromkeys(candidates))

    def _template_lookup_cache_key(self) -> tuple[str, tuple[str, ...]]:
        return (self._template_namespace, tuple(self._candidate_template_names()))

    def _template_lookup_cache_file(self) -> Path | None:
        raw_key = "\n".join(self._template_lookup_cache_key()[1])
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        preferred = Path(
            os.getenv("E2B_TEMPLATE_LOOKUP_CACHE_DIR", ".cache/e2b_template_lookup")
        )
        cache_dir = _resolve_template_lookup_cache_dir(preferred)
        return cache_dir / f"{digest}.json" if cache_dir is not None else None

    def _template_lookup_lock_file(self) -> Path | None:
        cache_file = self._template_lookup_cache_file()
        return cache_file.with_suffix(".lock") if cache_file is not None else None

    def _template_lookup_build_lock_file(self) -> Path | None:
        lock_file = self._template_lookup_lock_file()
        return lock_file.with_suffix(".build.lock") if lock_file is not None else None

    def _read_template_lookup_file_cache(self) -> str | None:
        cache_path = self._template_lookup_cache_file()
        if cache_path is None:
            return None
        try:
            if not cache_path.exists():
                return None
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        template_name = data.get("template_name")
        if template_name in self._candidate_template_names():
            return template_name
        return None

    def _remember_template_exists(self, template_name: str) -> None:
        cache_key = self._template_lookup_cache_key()
        _TEMPLATE_LOOKUP_CACHE[cache_key] = template_name
        cache_path = self._template_lookup_cache_file()
        if cache_path is None:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "template_name": template_name,
                        "candidates": self._candidate_template_names(),
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.logger.debug(
                "Failed to write E2B template lookup cache; using memory cache: %s",
                exc,
            )

    def _forget_template_lookup(self) -> None:
        _TEMPLATE_LOOKUP_CACHE.pop(self._template_lookup_cache_key(), None)
        cache_path = self._template_lookup_cache_file()
        if cache_path is None:
            return
        try:
            cache_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.logger.debug(
                "Failed to remove E2B template lookup cache; continuing: %s",
                exc,
            )

    @staticmethod
    def _resolve_sandbox_timeout_sec(value: int | None) -> int:
        raw_value = value if value is not None else os.getenv("E2B_SANDBOX_TIMEOUT_SEC")
        try:
            timeout = int(raw_value) if raw_value is not None else 3600
        except (TypeError, ValueError):
            timeout = 3600
        return max(60, timeout)

    def _dockerfile_content_or_path(self) -> str:
        content = self._environment_definition_path.read_text(encoding="utf-8")
        if self._strip_dockerfile_comments:
            content = _strip_dockerfile_comments(content)
        content = _normalize_from_image_refs(content)
        if self._template_name == self._pi_template_name():
            content = content.rstrip() + "\n\n" + PI_TEMPLATE_INSTALL_DOCKERFILE
        return content

    @retry(
        retry=retry_if_exception_type(
            (BuildException, RateLimitException, TemplateException)
        ),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    async def _create_template(self):
        self.logger.info(
            "E2B template build start: template=%s cpus=%s memory_mb=%s",
            self._template_name,
            self.task_env_config.cpus,
            self.task_env_config.memory_mb,
        )
        _benchmark_log(
            "E2B_TEMPLATE "
            f"task={self.environment_name} "
            f"status=build_start "
            f"template={self._template_name} "
            f"cpus={self.task_env_config.cpus} "
            f"memory_mb={self.task_env_config.memory_mb}"
        )
        if self.task_env_config.docker_image:
            template = Template().from_image(
                image=self.task_env_config.docker_image,
            )
        else:
            template = Template(
                file_context_path=str(Path(self.environment_dir).resolve())
            ).from_dockerfile(
                dockerfile_content_or_path=self._dockerfile_content_or_path(),
            )

        async with _template_build_semaphore():
            await AsyncTemplate.build(
                template=template,
                name=self._template_name,
                cpu_count=self.task_env_config.cpus,
                memory_mb=self.task_env_config.memory_mb,
            )
        self._remember_template_exists(self._template_name)
        self.logger.info("E2B template build done: template=%s", self._template_name)
        _benchmark_log(
            "E2B_TEMPLATE "
            f"task={self.environment_name} "
            f"status=build_done "
            f"template={self._template_name}"
        )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    async def _create_sandbox(self):
        missing_or_unlaunchable: SandboxException | None = None
        candidates = self._candidate_template_names()
        cached_template = (
            _TEMPLATE_LOOKUP_CACHE.get(self._template_lookup_cache_key())
            or self._read_template_lookup_file_cache()
        )
        if cached_template in candidates:
            candidates = [
                cached_template,
                *(candidate for candidate in candidates if candidate != cached_template),
            ]

        for template_name in candidates:
            self._template_name = template_name
            if not await AsyncTemplate.exists(template_name.rsplit("/", 1)[-1]):
                continue
            try:
                await self._create_sandbox_from_current_template()
                self._remember_template_exists(template_name)
                return
            except SandboxException as exc:
                if not _is_missing_or_unlaunchable_template_error(exc):
                    raise
                missing_or_unlaunchable = exc
                self._forget_template_lookup()
                self.logger.debug(
                    "Template %s is not launchable; trying next candidate: %s",
                    template_name,
                    exc,
                )

        self._template_name = self._pi_template_name() or self._build_template_name()
        process_lock = _TEMPLATE_LOOKUP_LOCKS.setdefault(
            self._template_lookup_cache_key(), asyncio.Lock()
        )
        async with process_lock:
            async with _AsyncFileLock(self._template_lookup_build_lock_file()):
                for template_name in self._candidate_template_names():
                    self._template_name = template_name
                    if not await AsyncTemplate.exists(
                        template_name.rsplit("/", 1)[-1]
                    ):
                        continue
                    try:
                        await self._create_sandbox_from_current_template()
                        self._remember_template_exists(template_name)
                        return
                    except SandboxException as exc:
                        if not _is_missing_or_unlaunchable_template_error(exc):
                            raise
                        missing_or_unlaunchable = exc

                self._template_name = (
                    self._pi_template_name() or self._build_template_name()
                )
                self.logger.debug("Creating template %s", self._template_name)
                _benchmark_log(
                    "E2B_TEMPLATE "
                    f"task={self.environment_name} "
                    f"status=miss "
                    f"template={self._template_name}"
                )
                await self._create_template()

        try:
            await self._create_sandbox_from_current_template()
            self._remember_template_exists(self._template_name)
        except SandboxException as exc:
            if missing_or_unlaunchable is not None:
                raise exc from missing_or_unlaunchable
            raise

    async def _create_sandbox_from_current_template(self) -> None:
        metadata = {
            "environment_name": self.environment_name,
            "session_id": self.session_id,
        }
        await _wait_for_sandbox_create_slot()
        self._sandbox = await AsyncSandbox.create(
            template=self._template_name,
            metadata=metadata,
            timeout=self._sandbox_timeout_sec,
            allow_internet_access=self._allow_internet_access(),
        )

    async def _restore_dockerfile_runtime_env(self) -> None:
        if not self.task_env_config.docker_image:
            return
        if not self._sandbox:
            raise RuntimeError("Sandbox not found. Please start the environment first.")
        result = await self._sandbox.commands.run(
            "env",
            user="root",
            timeout=30,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                "Could not read the E2B sandbox environment before restoring "
                "Dockerfile ENV instructions"
            )
        base_env = {}
        for line in str(result.stdout or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            base_env[key] = value
        dockerfile_env = _dockerfile_runtime_env(
            self._environment_definition_path,
            base_env,
        )
        self._persistent_env = {
            **dockerfile_env,
            **self._persistent_env,
        }
        if dockerfile_env:
            _benchmark_log(
                "E2B_ENV_RESTORE "
                f"task={self.environment_name} "
                f"keys={','.join(sorted(dockerfile_env))} "
                "status=ok"
            )

    def _workdir_from_dockerfile(self) -> str | None:
        return next(
            (
                instruction["value"]
                for instruction in reversed(
                    DockerfileParser(
                        path=str(self._environment_definition_path)
                    ).structure
                )
                if instruction.get("instruction") == "WORKDIR"
            ),
            None,
        )

    async def _wait_for_sandbox_ready(self) -> None:
        if not self._sandbox:
            raise RuntimeError("Sandbox not found but was just created.")

        last_error: Exception | None = None
        for _ in range(30):
            try:
                if await self._sandbox.is_running(request_timeout=5):
                    return
            except Exception as exc:  # pragma: no cover - network/provider dependent
                last_error = exc
            await asyncio.sleep(1)

        if last_error is not None:
            raise last_error
        raise RuntimeError("E2B sandbox did not become ready in time.")

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _prepare_runtime_dirs(self) -> None:
        if not self._sandbox:
            raise RuntimeError("Sandbox not found. Please start the environment first.")

        try:
            await self._sandbox.files.make_dir(str(EnvironmentPaths.agent_dir))
            await self._sandbox.files.make_dir(str(EnvironmentPaths.verifier_dir))
        except Exception:
            # E2B can occasionally return a sandbox whose HTTP/2 filesystem channel
            # is not fully ready yet. Reconnect the sandbox client before retrying.
            await self._sandbox.connect(timeout=self._sandbox_timeout_sec)
            raise

    async def _validate_sandbox_resources(self) -> None:
        if not self._sandbox:
            raise E2BResourceMismatchError("Sandbox is unavailable for resource check")
        info = await self._sandbox.get_info()
        required_cpus = int(self.task_env_config.cpus)
        required_memory_mb = int(self.task_env_config.memory_mb)
        required_storage_mb = int(self.task_env_config.storage_mb)
        if info.cpu_count < required_cpus or info.memory_mb < required_memory_mb:
            raise E2BResourceMismatchError(
                "E2B template resource mismatch: "
                f"actual={info.cpu_count}cpu/{info.memory_mb}MB "
                f"required={required_cpus}cpu/{required_memory_mb}MB "
                f"template={self._template_name}"
            )

        disk = await self.exec(
            "if [ -d /app ]; then df -Pm /app; else df -Pm /; fi | tail -n 1 | awk '{print $2}'",
            user="root",
        )
        try:
            actual_storage_mb = int(str(disk.stdout or "").strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise E2BResourceMismatchError(
                f"Could not determine E2B sandbox disk size: {disk.stdout!r}"
            ) from exc
        minimum_storage_mb = max(1, int(required_storage_mb * 0.95))
        if actual_storage_mb < minimum_storage_mb:
            raise E2BResourceMismatchError(
                "E2B sandbox disk is smaller than the task requirement: "
                f"actual={actual_storage_mb}MB required={required_storage_mb}MB "
                f"template={self._template_name}"
            )
        _benchmark_log(
            "E2B_RESOURCE_CHECK "
            f"task={self.environment_name} "
            f"template={self._template_name} "
            f"cpus={info.cpu_count} memory_mb={info.memory_mb} "
            f"storage_mb={actual_storage_mb} status=ok"
        )

    async def _cleanup_sandbox_before_restart(self) -> None:
        if self._sandbox is None:
            return
        self.logger.warning(
            "Discarding an existing E2B sandbox before retrying environment start"
        )
        _benchmark_log(
            "E2B_SANDBOX "
            f"task={self.environment_name} "
            "status=cleanup_before_restart"
        )
        await self.stop(delete=True)

    async def start(self, force_build: bool):
        await self._cleanup_sandbox_before_restart()
        if force_build:
            self._forget_template_lookup()
            self._template_name = self._pi_template_name() or self._build_template_name()
            self.logger.info(
                "E2B template check: force_build=true; building template=%s",
                self._template_name,
            )
            _benchmark_log(
                "E2B_TEMPLATE "
                f"task={self.environment_name} "
                f"status=force_build "
                f"template={self._template_name}"
            )
            process_lock = _TEMPLATE_LOOKUP_LOCKS.setdefault(
                self._template_lookup_cache_key(), asyncio.Lock()
            )
            async with process_lock:
                async with _AsyncFileLock(self._template_lookup_build_lock_file()):
                    await self._create_template()

        candidates = self._candidate_template_names()
        self.logger.info(
            "E2B template launch candidates=%s",
            ", ".join(candidates),
        )
        _benchmark_log(
            "E2B_TEMPLATE "
            f"task={self.environment_name} "
            "status=launch_candidates "
            f"candidates={','.join(candidates)}"
        )

        await self._create_sandbox()

        if not self._sandbox:
            raise RuntimeError(
                "Sandbox not found but was just created. This should never happen."
            )

        await self._wait_for_sandbox_ready()
        await self._restore_dockerfile_runtime_env()
        await self._prepare_runtime_dirs()
        await self._validate_sandbox_resources()

        await self.exec(
            f"chmod 777 {EnvironmentPaths.agent_dir} {EnvironmentPaths.verifier_dir}"
        )
