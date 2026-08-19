#!/usr/bin/env python3
"""Execute reference-conditioned generation for one resolved task reference pack.

Lane host-imagegen: prints a machine-readable spec the host ImageGen agent must consume
verbatim (role-ordered image inputs). Lane system-cli: runs the real multi-image edit
channel (image_gen.py edit) which transmits the reference image bytes; fails closed when
OPENAI_API_KEY is missing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.visual_foundation import (
    ReferenceRunnerError,
    execute_reference_conditioned,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one reference-conditioned generation attempt")
    parser.add_argument("--reference-pack", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", choices=("system-cli", "host-imagegen"), default="system-cli")
    parser.add_argument("--mode", default="img2img_edit")
    args = parser.parse_args()
    try:
        pack = json.loads(args.reference_pack.read_text(encoding="utf-8"))
        attempt = execute_reference_conditioned(
            reference_pack=pack,
            prompt=args.prompt,
            output_path=str(args.output),
            provider=args.provider,
            mode=args.mode,
        )
    except (ReferenceRunnerError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "attempted",
        "generation_attempt_id": attempt.generation_attempt_id,
        "provider": attempt.provider,
        "generation_mode": attempt.generation_mode,
        "prompt_sha256": attempt.prompt_sha256,
        "reference_inputs": [dict(item) for item in attempt.reference_inputs],
        "output_image_sha256": attempt.output_image_sha256,
        "command": list(attempt.command),
        "receipt": attempt.receipt,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
