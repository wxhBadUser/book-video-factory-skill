"""Canonical ordered Caption Contract binding helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


class ContractBindingError(ValueError):
    """A current-release Caption Contract binding is malformed or stale."""


def normalize_contract_bindings(
    bindings: Iterable[Mapping[str, Any]], *, expected_caption_ids: Sequence[str] | None = None
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in bindings:
        if "caption_visual_contract_sha256" in item:
            raise ContractBindingError(
                "legacy caption_visual_contract_sha256 binding keys are forbidden in current releases"
            )
        caption_id = str(item.get("caption_id", "")).strip()
        content_sha256 = str(item.get("content_sha256", "")).strip()
        if not caption_id or not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ContractBindingError(
                "current Caption Contract bindings require caption_id and content_sha256"
            )
        result.append({"caption_id": caption_id, "content_sha256": content_sha256})
    ids = [item["caption_id"] for item in result]
    if len(set(ids)) != len(ids):
        raise ContractBindingError("Caption Contract bindings contain duplicate caption ids")
    if expected_caption_ids is not None and ids != [str(item) for item in expected_caption_ids]:
        raise ContractBindingError("Caption Contract bindings do not match ordered caption ids")
    return result


def aggregate_contract_bindings_sha256(
    bindings: Iterable[Mapping[str, Any]], *, expected_caption_ids: Sequence[str] | None = None
) -> str:
    normalized = normalize_contract_bindings(
        bindings, expected_caption_ids=expected_caption_ids
    )
    if not normalized:
        raise ContractBindingError("Caption Contract bindings must not be empty")
    return hashlib.sha256(
        "|".join(item["content_sha256"] for item in normalized).encode("utf-8")
    ).hexdigest()


__all__ = [
    "ContractBindingError",
    "aggregate_contract_bindings_sha256",
    "normalize_contract_bindings",
]
