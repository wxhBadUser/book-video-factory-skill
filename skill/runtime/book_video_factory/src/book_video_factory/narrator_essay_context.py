"""narrator-essay 内容上下文强制加载器。

终结"案例知识放在 docs 里供参考"的状态：每次 narrator-essay 内容阶段运行时，
本加载器强制校验必读知识存在、实际读取文件、计算 SHA-256、按角色返回必读清单，
并对缺失 fail closed、对相近案例不足发结构化 warning。

底层执行模块（ffmpeg/asr/hash/状态机/成本）经 CONTEXT_FREE_ROLES 路由，
不加载任何案例长文——它们只读结构化合同。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "narrator_essay_context_manifest.json"

# 执行角色：只读结构化合同，不加载案例长文。
CONTEXT_FREE_ROLES = {"ffmpeg", "asr", "hash", "state_machine", "cost"}
# 可读取对照稿（approved_script/comparative_review）的角色；writer/routing 隔离禁读。
CONTEXT_COMPARISON_ROLES = {"editor", "review"}

# 案例目录名 → 叙事发动机（来自 CROSS_CASE_MATRIX / 结构标注）。
CASE_ENGINE = {
    "01_BEL_AMI": "antihero_ascent",
    "02_STRAIT_IS_THE_GATE": "psychological_mystery",
    "03_JANE_EYRE": "first_person_growth",
    "04_THE_LITTLE_PRINCE": "allegory_decoding",
    "05_MADAME_BOVARY": "desire_escalation",
    "06_THE_MOON_AND_SIXPENCE": "moral_debate",
    "07_WUTHERING_HEIGHTS_VALIDATION": "generational_cycle_and_repair",
}

# 主播人格 → 叙事发动机（近似路由）。
PERSONA_ENGINE = {
    "antihero_social_satire": "antihero_ascent",
    "psychological_tragedy": "psychological_mystery",
    "first_person_growth": "first_person_growth",
    "allegory": "allegory_decoding",
    "desire_crash": "desire_escalation",
    "moral_debate": "moral_debate",
    "gothic_generational_trauma": "generational_cycle_and_repair",
    "social_mechanism": "social_mechanism",
    # 中文人格名也支持（manifest persona_routing 的值）。
    "辛辣看客": "antihero_ascent",
    "克制侦探": "psychological_mystery",
    "主角化讲述者": "first_person_growth",
    "温柔陪伴者": "allegory_decoding",
    "先理解后审判": "desire_escalation",
    "辩论主持人": "moral_debate",
    "阴郁故事讲述者": "generational_cycle_and_repair",
    "冷静取证者": "social_mechanism",
}

# 相邻发动机：相近案例扩展（用于凑够 2—3 个）。
ADJACENT_ENGINE = {
    "antihero_ascent": ["social_mechanism", "desire_escalation"],
    "social_mechanism": ["antihero_ascent"],
    "psychological_mystery": ["generational_cycle_and_repair"],
    "first_person_growth": ["psychological_mystery"],
    "allegory_decoding": [],
    "desire_escalation": ["antihero_ascent", "moral_debate"],
    "moral_debate": ["desire_escalation", "first_person_growth"],
    "generational_cycle_and_repair": ["psychological_mystery", "moral_debate"],
}


class ContextMissingError(RuntimeError):
    """必读知识缺失时抛出；narrator-essay 流程必须 fail closed。"""


@dataclass
class NarratorEssayContext:
    agent_role: str
    book_title: str | None
    narrative_engine: str | None
    required_files: list[str]
    loaded_files: list[str]
    missing_files: list[str]
    similar_cases: list[str]
    warnings: list[str]
    file_hashes: dict[str, str]
    speed_contract: dict[str, Any]
    quality_threshold: dict[str, Any]
    reference_cases: list[dict[str, Any]]
    comparison_files: list[str] = field(default_factory=list)
    status: str = "pass"

    def to_report(self) -> dict[str, Any]:
        return {
            "agent_role": self.agent_role,
            "book_title": self.book_title,
            "narrative_engine": self.narrative_engine,
            "required_files": self.required_files,
            "loaded_files": self.loaded_files,
            "missing_files": self.missing_files,
            "similar_cases": self.similar_cases,
            "warnings": self.warnings,
            "file_hashes": self.file_hashes,
            "comparison_files": self.comparison_files,
            "status": self.status,
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _required_global(manifest: dict[str, Any]) -> list[str]:
    """所有内容角色都必读的全局知识文件。"""
    files: list[str] = list(manifest["gold_standard"]["files"])
    files += manifest["evals"]["files"]
    files.append(manifest["agent_routing"]["file"])
    return files


def _required_cases(manifest: dict[str, Any]) -> list[str]:
    """内容角色必读的案例结构标注 + 索引（transcript 不计入必读）。"""
    files = [manifest["reference_cases"]["index"]]
    for case in manifest["reference_cases"]["cases"]:
        files.append(f"{case['dir']}/CASE_METADATA.md")
        files.append(f"{case['dir']}/STRUCTURAL_ANNOTATION.md")
    return files


def _transcripts(manifest: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for c in manifest["reference_cases"]["cases"]:
        tf = c.get("transcript_file", "TRANSCRIPT_RAW.md")
        files.append(f"{c['dir']}/{tf}")
    return files


def _comparison_files(manifest: dict[str, Any]) -> list[str]:
    """对照稿（approved_script/comparative_review）；仅 editor/review 角色加载。"""
    files: list[str] = []
    for c in manifest["reference_cases"]["cases"]:
        comp = c.get("comparison_files")
        if not comp:
            continue
        for key in ("approved_script", "comparative_review"):
            if key in comp:
                files.append(f"{c['dir']}/{comp[key]}")
    return files


def _resolve(base: Path, rel: str) -> Path:
    return (base / rel).resolve()


def _similar_cases(manifest: dict[str, Any], engine: str | None) -> list[str]:
    """按发动机选相近案例：先精确匹配，再用相邻发动机补到 2—3 个。"""
    if not engine:
        return []
    case_by_engine: dict[str, str] = {}
    for case in manifest["reference_cases"]["cases"]:
        dirname = Path(case["dir"]).name
        eng = CASE_ENGINE.get(dirname)
        if eng:
            case_by_engine[eng] = case["id"]
    selected: list[str] = []
    if engine in case_by_engine:
        selected.append(case_by_engine[engine])
    for adj in ADJACENT_ENGINE.get(engine, []):
        if adj in case_by_engine and case_by_engine[adj] not in selected:
            selected.append(case_by_engine[adj])
        if len(selected) >= 3:
            break
    return selected


def load_context(
    factory_root: Path,
    *,
    project_root: Path | None = None,
    book_persona: str | None = None,
    narrative_engine: str | None = None,
    agent_role: str = "writer",
    book_title: str | None = None,
) -> NarratorEssayContext:
    """加载并校验 narrator-essay 内容上下文。

    factory_root: book_video_factory 目录（含 config/manifest）。
    project_root: docs/ 所在项目根；默认 factory_root.parent。
    缺失必读知识 → ContextMissingError（fail closed）。
    """
    docs_base = (project_root or factory_root.parent).resolve()
    manifest_path = (factory_root / "config" / MANIFEST_FILENAME).resolve()
    if not manifest_path.exists():
        raise ContextMissingError(f"manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    engine = narrative_engine
    if engine is None and book_persona is not None:
        engine = PERSONA_ENGINE.get(book_persona)
    similar = _similar_cases(manifest, engine)

    is_execution = agent_role in CONTEXT_FREE_ROLES
    required: list[str] = []
    loaded: list[str] = []
    missing: list[str] = []
    hashes: dict[str, str] = {}
    warnings: list[str] = []

    if is_execution:
        # 执行角色：不加载任何案例长文，只返回空上下文（只读结构化合同）。
        return NarratorEssayContext(
            agent_role=agent_role,
            book_title=book_title,
            narrative_engine=engine,
            required_files=[],
            loaded_files=[],
            missing_files=[],
            similar_cases=[],
            warnings=["execution_role_loads_no_case_long_form"],
            file_hashes={},
            speed_contract=manifest.get("speed_contract_cpm", {}),
            quality_threshold=manifest.get("quality_threshold", {}),
            reference_cases=[],
            status="pass",
        )

    required = _required_global(manifest) + _required_cases(manifest)
    # 写作/路线/编辑角色额外加载 transcript（可选，缺失不阻塞）。
    optional: list[str] = []
    if agent_role in {"routing", "writer", "editor"}:
        optional = _transcripts(manifest)
    # 对照稿（approved_script/comparative_review）仅 editor/review 可读；writer 隔离禁读。
    comparison_optional: list[str] = []
    if agent_role in CONTEXT_COMPARISON_ROLES:
        comparison_optional = _comparison_files(manifest)

    for rel in required:
        p = _resolve(docs_base, rel)
        if p.exists():
            loaded.append(rel)
            hashes[rel] = _sha256(p)
        else:
            missing.append(rel)
    for rel in optional:
        p = _resolve(docs_base, rel)
        if p.exists():
            loaded.append(rel)
            hashes[rel] = _sha256(p)
        else:
            warnings.append(f"optional_transcript_missing: {rel}")
    comparison_loaded: list[str] = []
    for rel in comparison_optional:
        p = _resolve(docs_base, rel)
        if p.exists():
            loaded.append(rel)
            hashes[rel] = _sha256(p)
            comparison_loaded.append(rel)
        else:
            warnings.append(f"comparison_file_missing: {rel}")

    if missing:
        # fail closed：必读知识缺失。
        missing_groups = set()
        for m in missing:
            if "gold_standard" in m:
                missing_groups.add("gold_standard")
            elif "reference_cases" in m:
                missing_groups.add("reference_cases")
            elif "evals" in m:
                missing_groups.add("evals")
            elif "agent_routing" in m:
                missing_groups.add("agent_routing")
        raise ContextMissingError(
            f"required context missing: {sorted(missing_groups)}; files={missing}"
        )

    if len(similar) < 2:
        warnings.append(
            f"fewer_than_two_similar_cases: engine={engine} similar={similar}"
        )

    return NarratorEssayContext(
        agent_role=agent_role,
        book_title=book_title,
        narrative_engine=engine,
        required_files=required,
        loaded_files=loaded,
        missing_files=[],
        similar_cases=similar,
        warnings=warnings,
        file_hashes=hashes,
        speed_contract=manifest.get("speed_contract_cpm", {}),
        quality_threshold=manifest.get("quality_threshold", {}),
        reference_cases=manifest["reference_cases"]["cases"],
        comparison_files=comparison_loaded,
        status="pass",
    )


def write_context_report(ctx: NarratorEssayContext, output_path: Path) -> Path:
    """把上下文报告写到磁盘，证明运行时实际读取。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(ctx.to_report(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(output_path)
    return output_path
