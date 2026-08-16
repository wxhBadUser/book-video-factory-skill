"""Reference-conditioned generation runner boundary.

The production runner itself resolves and passes the concrete image inputs (reference pack).
Two adapters are provided:

- host-imagegen: a machine-readable spec the host ImageGen agent must consume verbatim.
- system-cli (image_gen.py edit): a real multi-image img2img command that transmits the
  reference image bytes to the provider.

Evidence is recorded as generation_attempt_id + reference_inputs + output_image_sha256.
The runner never fabricates a result: without a usable provider credential it fails closed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from book_video_factory.manifests import sha256_file
from book_video_factory.visual_foundation.contracts import VisualFoundationError
from book_video_factory.visual_foundation.resolver import ReferenceInput


class ReferenceRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReferenceGenerationAttempt:
    generation_attempt_id: str
    provider: str
    generation_mode: str
    prompt_sha256: str
    reference_inputs: tuple[dict[str, str], ...]
    output_image_sha256: str | None
    command: tuple[str, ...] = ()
    receipt: str = ""


def _imagegen_cli() -> Path:
    configured = os.environ.get("CODEX_HOME")
    home = Path(configured).expanduser().resolve() if configured else (Path.home() / ".codex").resolve()
    cli = home / "skills/.system/imagegen/scripts/image_gen.py"
    if cli.is_symlink() or not cli.is_file():
        raise ReferenceRunnerError(f"installed ImageGen CLI is missing: {cli}")
    return cli


def _env_has_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _input_dict(item: ReferenceInput) -> dict[str, str]:
    return {"role": item.role, "reference_id": item.reference_id, "image_path": item.image_path, "image_sha256": item.image_sha256}


def reference_inputs_from_pack(reference_pack: Mapping[str, Any]) -> list[dict[str, str]]:
    references = reference_pack.get("references", [])
    if not isinstance(references, list):
        raise ReferenceRunnerError("reference pack references must be an array")
    result: list[dict[str, str]] = []
    for item in references:
        if not isinstance(item, Mapping):
            raise ReferenceRunnerError("reference pack entry must be an object")
        role = str(item.get("role", ""))
        image_path = str(item.get("image_path", ""))
        image_sha256 = str(item.get("image_sha256", ""))
        if not role or not image_path:
            raise ReferenceRunnerError("reference pack entry lacks role or image_path")
        entry = {"role": role, "reference_id": str(item.get("reference_id", "")), "image_path": image_path, "image_sha256": image_sha256}
        meta = item.get("meta")
        if isinstance(meta, Mapping):
            entry["meta"] = dict(meta)
        result.append(entry)
    return result


def verify_reference_inputs_match_pack(actual: Sequence[Mapping[str, Any]], reference_pack: Mapping[str, Any]) -> None:
    """Registration hard gate: actual reference inputs must exactly equal the task reference pack."""
    expected = reference_inputs_from_pack(reference_pack)
    def _subset(entries):
        return [
            {
                "role": str(item.get("role", "")),
                "reference_id": str(item.get("reference_id", "")),
                "image_path": str(item.get("image_path", "")),
                "image_sha256": str(item.get("image_sha256", "")),
            }
            for item in entries
        ]
    actual_list = _subset(actual)
    expected_list = _subset(expected)
    if actual_list != expected_list:
        raise ReferenceRunnerError(
            "actual reference inputs do not match the task reference contract; "
            "refusing to register a scene whose generation did not consume the required references"
        )


def build_cli_edit_runner(
    *,
    reference_pack: Mapping[str, Any],
    prompt: str,
    output_path: str,
    size: str = "2048x1152",
    quality: str = "high",
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Build the real system-cli edit command that transmits the reference images."""
    inputs = reference_inputs_from_pack(reference_pack)
    if not inputs:
        raise ReferenceRunnerError("cli edit requires at least one reference image")
    cli = _imagegen_cli()
    command: list[str] = [sys.executable, str(cli), "edit", "--no-augment"]
    for item in inputs:
        command.extend(["--image", str(item["image_path"])])
    command.extend(["--prompt", prompt, "--size", size, "--quality", quality, "--out", str(output_path)])
    return tuple(command), {item["role"]: item["image_path"] for item in inputs}


def execute_reference_conditioned(
    *,
    reference_pack: Mapping[str, Any],
    prompt: str,
    output_path: str,
    provider: str = "system-cli",
    mode: str = "img2img_edit",
    approved_assets: Sequence[Mapping[str, Any]] | None = None,
) -> ReferenceGenerationAttempt:
    """Execute the strongest available mode (CLI edit = real multi-reference img2img).

    Fails closed if OPENAI_API_KEY is not configured; no fabricated result is possible.
    When ``approved_assets`` (the workspace VISUAL_ASSET_MANIFEST.json assets) is
    provided, every reference must map to a registered, hash-matching approved
    asset (P0-8: no unvetted reference reaches a provider).
    """
    if provider not in {"system-cli", "host-imagegen"}:
        raise ReferenceRunnerError(f"unsupported reference-conditioned provider: {provider}")
    inputs = tuple(_input_dict(ReferenceInput(**{**item, "image_sha256": str(item.get("image_sha256", ""))})) for item in reference_inputs_from_pack(reference_pack))
    if approved_assets is not None:
        # Match on the reference IMAGE (path+hash), not reference_id: style_master /
        # previous_scene references carry non-asset ids (STYLE_MASTER_*, scene ids),
        # so a task_id keyed lookup would falsely reject them. Every reference image
        # must be a registered manifest asset with a matching hash (P0-8).
        by_image = {
            (str(a.get("path")), str(a.get("sha256"))): a
            for a in approved_assets if isinstance(a, Mapping)
        }
        for item in inputs:
            if (item["image_path"], item["image_sha256"]) not in by_image:
                raise ReferenceRunnerError(
                    f"reference {item['reference_id']!r} image {item['image_path']} is not a "
                    "registered VISUAL_ASSET_MANIFEST asset with matching hash; refusing to "
                    "send an unvetted image to the provider"
                )
    attempt_id = uuid.uuid4().hex
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    inputs = tuple(_input_dict(ReferenceInput(**{**item, "image_sha256": str(item.get("image_sha256", ""))})) for item in reference_inputs_from_pack(reference_pack))
    if provider == "host-imagegen":
        return ReferenceGenerationAttempt(
            generation_attempt_id=attempt_id,
            provider="host-imagegen",
            generation_mode="reference_conditioned",
            prompt_sha256=prompt_sha,
            reference_inputs=inputs,
            output_image_sha256=None,
            command=(),
            receipt="host-imagegen spec: agent must attach reference images in role order; no provider receipt available",
        )
    if not _env_has_key():
        raise ReferenceRunnerError(
            "blocked_by_missing_provider_credential: OPENAI_API_KEY is not set; "
            "cannot run real reference-conditioned generation (system-cli edit lane)"
        )
    command, _role_map = build_cli_edit_runner(reference_pack=reference_pack, prompt=prompt, output_path=output_path)
    completed = subprocess.run(list(command), capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise ReferenceRunnerError(
            f"reference-conditioned generation failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    target = Path(output_path).expanduser().resolve()
    if target.is_symlink() or not target.is_file() or target.stat().st_size < 1:
        raise ReferenceRunnerError("reference-conditioned generation produced no output file")
    return ReferenceGenerationAttempt(
        generation_attempt_id=attempt_id,
        provider="system-cli",
        generation_mode=mode,
        prompt_sha256=prompt_sha,
        reference_inputs=inputs,
        output_image_sha256=sha256_file(target),
        command=command,
        receipt="system-cli edit transmitted the reference images as multipart inputs; no provider-issued call id (honest)",
    )


def build_final_generation_prompt(task: Mapping[str, Any], reference_pack: Mapping[str, Any]) -> tuple[str, str]:
    """Final generation request boundary.

    Applies production-hardening rules that need the RESOLVED reference pack:
    1. Life-stage lock: every identity reference becomes an explicit apparent-age invariant.
    2. Zero-character participant rule: empty/environment scenes explicitly forbid visible humans.
    Returns (final_prompt, prompt_sha256).
    """
    base = str(task.get("prompt", "")).strip()
    blocks = [base]
    identities = [
        item for item in reference_pack.get("references", [])
        if isinstance(item, Mapping) and item.get("role") == "identity_master"
    ]
    for item in identities:
        meta = item.get("meta") if isinstance(item.get("meta"), Mapping) else {}
        cid = str(meta.get("character_id", "") or item.get("reference_id", "")).strip()
        life_stage = str(meta.get("life_stage", "")).strip()
        age = str(meta.get("apparent_age_range", "")).strip()
        if age:
            blocks.append(
                f"LIFE-STAGE LOCK: {cid} apparent age: {age}. "
                f"Do not age {cid} beyond the supplied {life_stage or 'current'}-stage identity reference; "
                "do not convert him/her into a different life stage."
            )
    participant = task.get("participant_constraint")
    if not identities and isinstance(participant, Mapping) and participant.get("expected_narrative_character_count") == 0 and participant.get("allow_unlisted_narrative_characters") is False:
        blocks.append(
            "No visible human figure. No pedestrian. No silhouette. No foreground or background character."
        )
    final = "\n".join([b for b in blocks if b])
    return final, hashlib.sha256(final.encode("utf-8")).hexdigest()
