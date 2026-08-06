"""Phase 1 测试：narrator-essay 内容上下文强制加载。

契约（来自 docs/agent_routing/AGENT_CONTEXT_ROUTING.md 与
config/narrator_essay_context_manifest.json）：
- 缺少 Gold Standard 时 narrator-essay 启动 fail closed；
- 上下文 manifest 必须记录实际加载的案例文件路径 + SHA-256；
- 当前书型相近案例少于 2 个时给出结构化 warning（不阻塞）；
- 底层执行模块（ffmpeg/asr/hash/状态机）不加载案例长文。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.narrator_essay_context import (  # noqa: E402
    CONTEXT_FREE_ROLES,
    ContextMissingError,
    NarratorEssayContext,
    load_context,
)


def _manifest_copy(tmp: Path) -> Path:
    """临时 factory_root：含 config/manifest，但 docs 在其 parent（真实项目根）。"""
    cfg = tmp / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "narrator_essay_context_manifest.json").write_text(
        (ROOT / "config" / "narrator_essay_context_manifest.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    return tmp


class NarratorEssayContextTests(unittest.TestCase):
    def test_load_context_returns_loaded_file_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent)
            self.assertIsInstance(ctx, NarratorEssayContext)
            self.assertIn(
                "docs/gold_standard/BOOK_NARRATION_GOLD_STANDARD.md",
                ctx.loaded_files,
            )
            annotations = [
                p for p in ctx.loaded_files if p.endswith("STRUCTURAL_ANNOTATION.md")
            ]
            self.assertGreaterEqual(len(annotations), 7)
            self.assertEqual(ctx.status, "pass")
            self.assertEqual(ctx.missing_files, [])

    def test_missing_gold_standard_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_factory, tempfile.TemporaryDirectory() as tmp_proj:
            factory_root = _manifest_copy(Path(tmp_factory))
            # project_root 指向空目录 → docs/ 不存在 → Gold Standard 缺失
            with self.assertRaises(ContextMissingError) as cm:
                load_context(
                    factory_root=factory_root, project_root=Path(tmp_proj)
                )
            self.assertIn("gold_standard", str(cm.exception).lower())

    def test_fewer_than_two_similar_cases_warns_not_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(
                factory_root=factory_root,
                project_root=ROOT.parent,
                book_persona="nonexistent_persona",
            )
            self.assertEqual(ctx.status, "pass")  # 不阻塞
            self.assertTrue(
                any("similar" in w.lower() or "相近" in w for w in ctx.warnings),
                f"expected similar-case warning, got {ctx.warnings}",
            )

    def test_known_engine_finds_similar_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(
                factory_root=factory_root,
                project_root=ROOT.parent,
                narrative_engine="generational_cycle_and_repair",
            )
            self.assertIn("wuthering_heights", ctx.similar_cases)

    def test_seven_reference_cases_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent)
            self.assertEqual(len(ctx.reference_cases), 7)

    def test_file_hashes_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent)
            self.assertTrue(ctx.file_hashes)
            key = "docs/gold_standard/BOOK_NARRATION_GOLD_STANDARD.md"
            self.assertIn(key, ctx.file_hashes)
            self.assertEqual(len(ctx.file_hashes[key]), 64)  # sha256 hex

    def test_speed_and_quality_threshold_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent)
            self.assertEqual(ctx.speed_contract["average"], [220, 245])
            self.assertEqual(ctx.quality_threshold["total_min"], 66)
            self.assertEqual(ctx.quality_threshold["fact_reliability_must"], 5)

    def test_execution_roles_load_no_case_long_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            for role in CONTEXT_FREE_ROLES:
                ctx = load_context(
                    factory_root=factory_root,
                    project_root=ROOT.parent,
                    agent_role=role,
                )
                # 执行角色不加载案例 transcript/annotation 长文
                long_form = [
                    p
                    for p in ctx.loaded_files
                    if "TRANSCRIPT_RAW" in p or "STRUCTURAL_ANNOTATION" in p
                ]
                self.assertEqual(long_form, [], f"role {role} loaded case long-form: {long_form}")

    def test_wuthering_heights_loads_reference_transcript_not_raw(self) -> None:
        """呼啸山庄无 TRANSCRIPT_RAW.md；应加载 REFERENCE_CREATOR_TRANSCRIPT.md，不产生 missing 警告。"""
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent, agent_role="writer")
            wh_transcript = [
                p for p in ctx.loaded_files
                if "07_WUTHERING_HEIGHTS_VALIDATION" in p and "REFERENCE_CREATOR_TRANSCRIPT" in p
            ]
            self.assertTrue(wh_transcript, "呼啸山庄 reference_transcript 未加载")
            # 不应有呼啸山庄的 optional_transcript_missing 警告
            wh_missing = [
                w for w in ctx.warnings
                if "07_WUTHERING_HEIGHTS_VALIDATION" in w and "TRANSCRIPT_RAW" in w
            ]
            self.assertEqual(wh_missing, [], f"错误的 missing 警告: {wh_missing}")

    def test_writer_role_isolated_from_approved_script(self) -> None:
        """writer 角色禁读 USER_APPROVED_OPTIMIZED_SCRIPT（隔离防复述）。"""
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent, agent_role="writer")
            approved = [
                p for p in ctx.loaded_files if "USER_APPROVED_OPTIMIZED_SCRIPT" in p
            ]
            self.assertEqual(approved, [], f"writer 不应加载 approved_script: {approved}")
            self.assertEqual(ctx.comparison_files, [])

    def test_editor_role_loads_comparison_files(self) -> None:
        """editor/review 角色可读取对照稿（approved_script + comparative_review）。"""
        with tempfile.TemporaryDirectory() as tmp:
            factory_root = _manifest_copy(Path(tmp))
            ctx = load_context(factory_root=factory_root, project_root=ROOT.parent, agent_role="editor")
            self.assertIn(
                "docs/reference_cases/07_WUTHERING_HEIGHTS_VALIDATION/USER_APPROVED_OPTIMIZED_SCRIPT.md",
                ctx.comparison_files,
            )
            self.assertIn(
                "docs/reference_cases/07_WUTHERING_HEIGHTS_VALIDATION/COMPARATIVE_REVIEW.md",
                ctx.comparison_files,
            )
            # 对照文件 Hash 已记录
            for cf in ctx.comparison_files:
                self.assertIn(cf, ctx.file_hashes)
                self.assertEqual(len(ctx.file_hashes[cf]), 64)


if __name__ == "__main__":
    unittest.main()
