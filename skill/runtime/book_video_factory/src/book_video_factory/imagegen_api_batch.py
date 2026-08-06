from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class ImageGenBatchError(RuntimeError):
    """The optional installed ImageGen CLI batch route cannot run safely."""


@dataclass(frozen=True)
class ImageGenBatchResult:
    returncode: int
    stdout: str
    stderr: str
    command: tuple[str, ...]


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ImageGenBatchError(f"{name} is required in the current process environment")
    return value


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _system_cli() -> Path:
    configured = os.environ.get("CODEX_HOME")
    codex_home = Path(configured).expanduser().resolve() if configured else (Path.home() / ".codex").resolve()
    cli = codex_home / "skills/.system/imagegen/scripts/image_gen.py"
    if cli.is_symlink() or not cli.is_file():
        raise ImageGenBatchError(f"installed system image_gen CLI is missing: {cli}")
    return cli


def run_imagegen_api_batch(
    input_path: Path,
    out_dir: Path,
    *,
    concurrency: int = 5,
) -> ImageGenBatchResult:
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 10:
        raise ImageGenBatchError("concurrency must be an integer from 1 through 10")
    key = _required_environment("OPENAI_API_KEY")
    base_url = _required_environment("OPENAI_BASE_URL")
    cli = _system_cli()
    source = input_path.expanduser().resolve()
    if source.is_symlink() or not source.is_file():
        raise ImageGenBatchError("batch input JSONL is missing or symlinked")
    destination = out_dir.expanduser().resolve()
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise ImageGenBatchError("batch output path is not a safe directory")
    destination.mkdir(parents=True, exist_ok=True)
    command = (
        sys.executable,
        str(cli),
        "generate-batch",
        "--input",
        str(source),
        "--out-dir",
        str(destination),
        "--concurrency",
        str(concurrency),
        "--no-augment",
    )
    environment = os.environ.copy()
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    secrets = (key, base_url)
    stdout = _redact(completed.stdout or "", secrets)
    stderr = _redact(completed.stderr or "", secrets)
    if completed.returncode != 0:
        detail = (stderr.strip() or stdout.strip() or "installed ImageGen CLI returned no diagnostic")
        raise ImageGenBatchError(f"installed ImageGen CLI failed with {completed.returncode}: {detail}")
    return ImageGenBatchResult(completed.returncode, stdout, stderr, command)
