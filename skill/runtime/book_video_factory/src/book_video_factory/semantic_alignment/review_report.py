"""Per-shot A/V semantic-alignment review report (Part 7).

Turns a committed ``SCENE_REVIEW_DECISION`` document (plus optional vision
evidence) into a human-facing report in two formats:

* a structured JSON (``av-shot-review.v1``) that downstream automation can read,
* an HTML table that a human reviewer can open and scan shot-by-shot.

Both formats surface the same per-shot facts: the semantic / reality / identity
verdicts, whether the shot carries authoritative vision evidence, the vision
verdict and provider, and whether the decision is a legacy mark. The point is
that a reader can see, for every shot, *why* it was allowed to advance -- or
that it was allowed only as historical legacy.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "av-shot-review.v1"

_REQUIRED_DECISION_FIELDS = {
    "task_id", "semantic_review_status", "reality_review_status",
    "identity_review_status", "note",
}
_OPTIONAL_DECISION_FIELDS = {"vision_evidence", "legacy_pass"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _vision_summary(evidence: Mapping[str, Any] | None, legacy_pass: bool) -> dict[str, Any]:
    if evidence is None:
        return {
            "vision_bound": False,
            "legacy_pass": legacy_pass,
            "vision_provider": None,
            "vision_verdict": None,
            "reviewed_pixels": None,
        }
    return {
        "vision_bound": True,
        "legacy_pass": bool(evidence.get("legacy_pass", False)),
        "vision_provider": evidence.get("vision_provider"),
        "vision_verdict": evidence.get("parity_verdict"),
        "reviewed_pixels": bool(evidence.get("reviewed_pixels", False)),
    }


def build_review_report(
    decision_doc: Mapping[str, Any],
    *,
    evidence_doc: Mapping[str, Any] | None = None,
    proposition_modes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the structured per-shot review report.

    ``proposition_modes`` is an optional ``task_id -> mode`` map (typically
    produced by the ``classify_proposition`` planner) so the report can show the
    Literal / Symbolic / Abstract intent next to each verdict.
    """

    if not isinstance(decision_doc, Mapping) or decision_doc.get("schema_version") != "scene-review-decision.v1":
        raise ValueError("decision document must be a scene-review-decision.v1 object")
    decisions = decision_doc.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decision document has no decisions list")

    evidences: dict[str, Any] = {}
    if isinstance(evidence_doc, Mapping) and evidence_doc.get("schema_version") == "vision-evidence.v1":
        evidences = dict(evidence_doc.get("reviews", {}))

    shots: list[dict[str, Any]] = []
    vision_bound = 0
    legacy = 0
    unbound = 0
    for item in decisions:
        if not isinstance(item, dict):
            raise ValueError("scene review decision item is invalid")
        keys = set(item)
        if not _REQUIRED_DECISION_FIELDS <= keys or not keys <= (_REQUIRED_DECISION_FIELDS | _OPTIONAL_DECISION_FIELDS):
            raise ValueError("scene review decision item fields are invalid")
        task_id = str(item["task_id"])
        raw_evidence = item.get("vision_evidence")
        legacy_pass = bool(item.get("legacy_pass", False))
        summary = _vision_summary(raw_evidence if isinstance(raw_evidence, Mapping) else None, legacy_pass)
        if summary["vision_bound"]:
            vision_bound += 1
        elif summary["legacy_pass"]:
            legacy += 1
        else:
            unbound += 1
        shots.append({
            "task_id": task_id,
            "semantic_review_status": item.get("semantic_review_status"),
            "reality_review_status": item.get("reality_review_status"),
            "identity_review_status": item.get("identity_review_status"),
            "note": item.get("note"),
            "proposition_mode": (proposition_modes or {}).get(task_id),
            **summary,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "release_id": decision_doc.get("release_id"),
        "shot_count": len(shots),
        "vision_bound_count": vision_bound,
        "legacy_count": legacy,
        "unbound_count": unbound,
        "shots": shots,
    }


def render_review_report_html(report: Mapping[str, Any], *, title: str = "A/V 语义对齐逐镜审查报告") -> str:
    """Render the per-shot report as a self-contained HTML table."""

    shots = report.get("shots", [])
    rows: list[str] = []
    for shot in shots:
        mode = shot.get("proposition_mode")
        if shot.get("vision_bound"):
            vision_cell = (
                f"已绑定 · {html.escape(str(shot.get('vision_provider') or ''))} · "
                f"判定 {html.escape(str(shot.get('vision_verdict') or ''))}"
            )
        elif shot.get("legacy_pass"):
            vision_cell = "历史遗留 (legacy_pass=true)"
        else:
            vision_cell = '<span class="unbound">未绑定视觉证据</span>'
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(shot.get('task_id')))}</td>"
            f"<td>{html.escape(str(shot.get('semantic_review_status') or ''))}</td>"
            f"<td>{html.escape(str(shot.get('reality_review_status') or ''))}</td>"
            f"<td>{html.escape(str(shot.get('identity_review_status') or ''))}</td>"
            f"<td>{html.escape(str(mode) if mode else '—')}</td>"
            f"<td>{vision_cell}</td>"
            f"<td>{html.escape(str(shot.get('note') or ''))}</td>"
            "</tr>"
        )

    summary_line = (
        f"镜头总数 {report.get('shot_count')} · "
        f"视觉证据绑定 {report.get('vision_bound_count')} · "
        f"历史遗留 {report.get('legacy_count')} · "
        f"未绑定 {report.get('unbound_count')}"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }}
h1 {{ font-size: 20px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
th {{ background: #f5f5f5; }}
tr:nth-child(even) {{ background: #fafafa; }}
.unbound {{ color: #b00020; font-weight: 600; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<div class="meta">Release {html.escape(str(report.get('release_id') or ''))} · 生成于 {html.escape(str(report.get('generated_at') or ''))}</div>
<div class="meta">{html.escape(summary_line)}</div>
<table>
<thead><tr>
<th>镜头</th><th>语义</th><th>真实</th><th>身份</th><th>命题模式</th><th>视觉证据</th><th>备注</th>
</tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""


def write_review_report(
    decision_path: Path,
    *,
    json_path: Path,
    html_path: Path | None = None,
    evidence_path: Path | None = None,
    proposition_modes: Mapping[str, str] | None = None,
    title: str = "A/V 语义对齐逐镜审查报告",
) -> dict[str, Any]:
    """Read a decision artifact and write the JSON (+ optional HTML) report."""

    decision_doc = json.loads(Path(decision_path).read_text(encoding="utf-8"))
    evidence_doc = None
    if evidence_path is not None and Path(evidence_path).is_file():
        evidence_doc = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    report = build_review_report(
        decision_doc, evidence_doc=evidence_doc, proposition_modes=proposition_modes
    )
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if html_path is not None:
        html_path = Path(html_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_review_report_html(report, title=title), encoding="utf-8")
    return report
