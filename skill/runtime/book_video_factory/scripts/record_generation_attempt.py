#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401
from book_video_factory.production_visuals import GenerationAttemptError, record_generation_attempt


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a fail-closed production image generation attempt")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--outcome", choices=("success", "failed", "rejected", "regenerated"), required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint-class", choices=("host-imagegen", "system-cli", "openai-compatible"), required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--final", type=Path)
    parser.add_argument("--machine-rejection-reason")
    parser.add_argument("--human-rejection-reason")
    args = parser.parse_args()
    try:
        result = record_generation_attempt(
            args.project,
            job_id=args.job_id,
            outcome=args.outcome,
            provider=args.provider,
            model=args.model,
            endpoint_class=args.endpoint_class,
            concurrency=args.concurrency,
            source=args.source,
            final=args.final,
            machine_rejection_reason=args.machine_rejection_reason,
            human_rejection_reason=args.human_rejection_reason,
        )
    except GenerationAttemptError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "recorded",
        "attempt": result.attempt,
        "ledger": str(result.ledger_path),
        "prompts": str(result.prompts_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
