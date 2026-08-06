from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RUNTIME = ROOT / "runtime/book_video_factory"

FORBIDDEN = (
    "script.audio-drama.v1",
    "VoxCPM2",
    "paper-collage-explainer",
    "VOX风格",
    "Veo 3.1",
    "认知觉醒",
)


class ActiveSkillPackageTests(unittest.TestCase):
    def test_skill_exposes_only_classic_narrator_hbg_pipeline(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("拆解《<书名>》", skill)
        self.assertIn("script.narrator-essay.v1", skill)
        self.assertIn("Edge TTS", skill)
        self.assertIn("VTT", skill)
        self.assertIn("vendor/hbg-life-simulation", skill)
        self.assertIn("render_streaming_ffmpeg.mjs", skill)
        self.assertIn("local_master_review", skill)
        self.assertIn("ImageGen", skill)
        for term in FORBIDDEN:
            with self.subTest(term=term):
                self.assertNotIn(term, skill)

    def test_bundled_runtime_contains_only_current_contracts(self) -> None:
        required = [
            "src/book_video_factory/book_research.py",
            "src/book_video_factory/narrator_essay_contracts.py",
            "src/book_video_factory/manifests.py",
            "src/book_video_factory/gates.py",
            "scripts/init_project.py",
            "scripts/workflow.py",
            "config/style_profiles/classic-narrator-hbg-v1.json",
            "config/release_profiles/book-classic-narrator-hbg-16x9-v1.json",
        ]
        removed = [
            "src/book_video_factory/audio_drama_contracts.py",
            "src/book_video_factory/voice.py",
            "src/book_video_factory/gemini_video.py",
            "src/book_video_factory/cinematic_renderer.py",
            "scripts/start_cinematic_book.py",
            "scripts/generate_veo_hero_clip.py",
        ]
        for relative in required:
            with self.subTest(required=relative):
                self.assertTrue((RUNTIME / relative).is_file(), relative)
        for relative in removed:
            with self.subTest(removed=relative):
                self.assertFalse((RUNTIME / relative).exists(), relative)

        styles = sorted((RUNTIME / "config/style_profiles").glob("*.json"))
        releases = sorted((RUNTIME / "config/release_profiles").glob("*.json"))
        self.assertEqual([path.stem for path in styles], ["classic-narrator-hbg-v1"])
        self.assertEqual(
            [path.stem for path in releases], ["book-classic-narrator-hbg-16x9-v1", "book-classic-narrator-hbg-9x16-v1"]
        )

    def test_skill_routes_script_generation_through_v2_evidence_chain(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = (ROOT / "references/script-workflow-v2.md").read_text(encoding="utf-8")
        acquisition = (ROOT / "references/fulltext-acquisition.md").read_text(encoding="utf-8")
        combined = skill + "\n" + workflow + "\n" + acquisition

        for marker in (
            "中文全文证据",
            "references/script-workflow-v2.md",
            "references/fulltext-acquisition.md",
            "找中文全文",
            "全文→口播拆解",
            "三路线竞争",
            "对抗审查",
            "去AI味",
            "人工审核",
            "15项评分≥72",
            "事实可靠性=5",
            "失实风险清单",
            "SOURCE.md",
            "SHA-256",
            "乱序",
            "SCRIPT_RELEASE.md",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)
    def test_skill_routes_phase_one_through_formal_content_package(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references/content-brain.md").read_text(encoding="utf-8")
        combined = skill + "\n" + reference

        self.assertIn("build_content_package.py", combined)
        self.assertIn("--validate-only", combined)
        self.assertIn("SCRIPT_RELEASE.md", combined)
        self.assertIn("SCRIPT_AUDIT.md", combined)
        self.assertIn("SCRIPT_LOCK.json", combined)
        self.assertIn("Level C", combined)
        self.assertIn("placeholder", combined.lower())
        self.assertIn("Phase 1", combined)
        self.assertIn("Phase 2", combined)
        self.assertIn("不得生成音频、图片或视频", combined)

    def test_repository_readmes_name_the_phase_one_boundary(self) -> None:
        combined = (REPO / "README.md").read_text(encoding="utf-8") + "\n" + (
            RUNTIME / "README.md"
        ).read_text(encoding="utf-8")

        self.assertIn("build_content_package.py", combined)
        self.assertIn("CONTENT_PACKAGE_MANIFEST.json", combined)
        self.assertIn("机器锁定不等于人工批准", combined)
        self.assertIn("Phase 1 不生成音频、图片或视频", combined)

    def test_bundled_runtime_contains_phase_one_content_compiler(self) -> None:
        required = [
            "src/book_video_factory/research_validation.py",
            "src/book_video_factory/editorial_validation.py",
            "src/book_video_factory/script_metrics.py",
            "src/book_video_factory/content_package.py",
            "scripts/build_content_package.py",
            "tests/fixtures/phase1_valid_package/SOURCE_MANIFEST.json",
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((RUNTIME / relative).is_file(), relative)


    def test_skill_routes_approved_phase_two_handoff_through_bridge(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references/content-brain.md").read_text(encoding="utf-8")
        combined = skill + "\n" + reference
        self.assertIn("build_hbg_bridge.py", combined)
        self.assertIn("HBG_BRIDGE_INPUT", combined)
        self.assertIn("脚本批准", combined)
        self.assertIn("SCRIPT_SOURCE.md", combined)
        self.assertIn("STORYBOARD_BASE.json", combined)
        self.assertIn("Phase 2 不生成音频、图片或视频", combined)

    def test_bundled_runtime_contains_phase_two_bridge(self) -> None:
        required = [
            "src/book_video_factory/hbg_bridge/contracts.py",
            "src/book_video_factory/hbg_bridge/provenance.py",
            "src/book_video_factory/hbg_bridge/script_export.py",
            "src/book_video_factory/hbg_bridge/project_spec.py",
            "src/book_video_factory/hbg_bridge/character_export.py",
            "src/book_video_factory/hbg_bridge/storyboard_export.py",
            "src/book_video_factory/hbg_bridge/runner.py",
            "src/book_video_factory/hbg_bridge/compiler.py",
            "scripts/build_hbg_bridge.py",
            "schemas/hbg_bridge_input.v1.schema.json",
            "schemas/hbg_bridge_manifest.v1.schema.json",
            "schemas/project_spec.book.v2.schema.json",
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((RUNTIME / relative).is_file(), relative)

    def test_skill_routes_phase_three_through_real_host_imagegen_and_human_review(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_path = ROOT / "references/visual-stage.md"
        self.assertTrue(reference_path.is_file())
        combined = skill + "\n" + reference_path.read_text(encoding="utf-8")
        for marker in (
            "build_visual_stage.py",
            "register_visual_asset.py",
            "build_visual_review.py",
            "approve_visual_stage.py",
            "host ImageGen",
            "make_contact_sheet.sh",
            "VISUAL_REVIEW_REPORT.json",
            "ANCHOR_APPROVAL.json",
        ):
            self.assertIn(marker, combined)
        for prohibition in (
            "不得伪造 tool call ID",
            "不得伪造输出 Hash",
            "不得伪造图片文件",
            "不得伪造人工批准",
            "参考图不得复制为生产资产",
        ):
            self.assertIn(prohibition, combined)

    def test_bundled_runtime_contains_phase_three_visual_stage(self) -> None:
        required = [
            "src/book_video_factory/reference_visuals/catalog.py",
            "src/book_video_factory/visual_stage/contracts.py",
            "src/book_video_factory/visual_stage/prompts.py",
            "src/book_video_factory/visual_stage/compiler.py",
            "src/book_video_factory/visual_stage/asset_registry.py",
            "src/book_video_factory/visual_stage/review.py",
            "src/book_video_factory/visual_stage/approval.py",
            "scripts/build_visual_stage.py",
            "scripts/register_visual_asset.py",
            "scripts/build_visual_review.py",
            "scripts/approve_visual_stage.py",
            "schemas/visual_stage_input.v1.schema.json",
            "schemas/book_visual_profile.v1.schema.json",
            "schemas/visual_task.v1.schema.json",
            "schemas/visual_asset_manifest.v1.schema.json",
            "schemas/visual_review_report.v1.schema.json",
            "schemas/visual_anchor_approval.v1.schema.json",
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((RUNTIME / relative).is_file(), relative)

    def test_skill_routes_phase_four_through_real_edge_tts_and_two_pass_storyboard(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_path = ROOT / "references/audio-stage.md"
        self.assertTrue(reference_path.is_file())
        combined = skill + "\n" + reference_path.read_text(encoding="utf-8")
        for marker in (
            "run_audio_stage.py generate",
            "run_audio_stage.py finalize",
            "run_audio_stage.py status",
            "AUDIO_PRELIMINARY_MANIFEST.json",
            "STORYBOARD_AUDIO_PLAN.json",
            "AUDIO_STAGE_MANIFEST.json",
            "Edge TTS",
            "VTT",
            "awaiting_audio_storyboard_plan",
            "ready_for_image_task_planning",
        ):
            self.assertIn(marker, combined)
        for prohibition in (
            "不得伪造音频",
            "不得伪造 VTT",
            "不得使用估算时间轴",
            "不得在没有 Edge TTS 时返回成功",
            "Phase 4 不生成图片、HyperFrames、FFmpeg 成片或最终 MP4",
        ):
            self.assertIn(prohibition, combined)

    def test_bundled_runtime_contains_phase_four_audio_stage(self) -> None:
        required = [
            "src/book_video_factory/audio_stage/contracts.py",
            "src/book_video_factory/audio_stage/pronunciation.py",
            "src/book_video_factory/audio_stage/hbg_adapter.py",
            "src/book_video_factory/audio_stage/media_probe.py",
            "src/book_video_factory/audio_stage/captions.py",
            "src/book_video_factory/audio_stage/storyboard_plan.py",
            "src/book_video_factory/audio_stage/compiler.py",
            "src/book_video_factory/audio_stage/status.py",
            "scripts/run_audio_stage.py",
            "schemas/audio_stage_input.v1.schema.json",
            "schemas/pronunciation_lexicon.v1.schema.json",
            "schemas/storyboard_audio_plan.v1.schema.json",
            "schemas/caption_bindings.v1.schema.json",
            "schemas/audio_stage_manifest.v1.schema.json",
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((RUNTIME / relative).is_file(), relative)

    def test_upstream_lock_declares_pristine_vendor(self) -> None:
        lock = json.loads(
            (REPO / "vendor/hbg-life-simulation/UPSTREAM_LOCK.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(lock["local_patches"], [])
        self.assertEqual(
            lock["commit"],
            "63aa262d88f18c6058b205c2dd582cf909b219a4",
        )


if __name__ == "__main__":
    unittest.main()
