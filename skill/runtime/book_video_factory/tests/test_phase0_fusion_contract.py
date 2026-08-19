from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

FACTORY = Path(__file__).resolve().parents[1]
REPO = FACTORY.parent
SRC = FACTORY / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.contracts import ReleaseProfile  # noqa: E402
from book_video_factory.style_profiles import (  # noqa: E402
    DEFAULT_STYLE_PROFILE_ID,
    available_style_profile_ids,
    load_style_profile,
)


REMOVED_PATHS = (
    "book_video_factory/src/book_video_factory/audio_drama_contracts.py",
    "book_video_factory/src/book_video_factory/voice.py",
    "book_video_factory/src/book_video_factory/gemini_video.py",
    "book_video_factory/src/book_video_factory/cinematic.py",
    "book_video_factory/src/book_video_factory/cinematic_assembly.py",
    "book_video_factory/src/book_video_factory/cinematic_audio.py",
    "book_video_factory/src/book_video_factory/cinematic_qc.py",
    "book_video_factory/src/book_video_factory/cinematic_renderer.py",
    "book_video_factory/scripts/build_audio_drama_voice_plan.py",
    "book_video_factory/scripts/validate_audio_drama_script.py",
    "book_video_factory/scripts/generate_veo_hero_clip.py",
    "book_video_factory/scripts/start_cinematic_book.py",
    "book_video_factory/scripts/build_ren_zhi_jue_xing_preview.py",
    "book_video_factory/config/style_profiles/book-editorial-bilingual-v2.json",
    "book_video_factory/config/style_profiles/paper-collage-explainer-v1.json",
    "book_video_factory/config/style_profiles/cinematic-longform-book-essay-v1.json",
    "book_video_factory/config/style_profiles/cinematic-narrator-essay-v1.json",
    "book_video_factory/config/release_profiles/book-v4-bilingual-3x4.json",
    "book_video_factory/config/release_profiles/book-vox-vertical-9x16-v1.json",
    "book_video_factory/config/release_profiles/book-cinematic-landscape-16x9-v1.json",
    "book_video_factory/config/release_profiles/book-cinematic-narrator-16x9-v1.json",
    "skill/references/immersive-audio-drama.md",
    "skill/references/paper-collage-explainer.md",
)

FORBIDDEN_ACTIVE_TERMS = (
    "script.audio-drama.v1",
    "VoxCPM2",
    "paper-collage-explainer",
    "VOX风格",
    "Veo 3.1",
    "认知觉醒",
)


class PhaseZeroFusionContractTests(unittest.TestCase):
    def test_classic_narrator_hbg_is_the_only_active_style(self) -> None:
        self.assertEqual(DEFAULT_STYLE_PROFILE_ID, "classic-narrator-hbg-v1")
        self.assertEqual(available_style_profile_ids(), ("classic-narrator-hbg-v1",))
        style = load_style_profile(DEFAULT_STYLE_PROFILE_ID)
        self.assertEqual(style.release_profile_id, "book-classic-narrator-hbg-16x9-v1")

    def test_release_profile_uses_minimax_provider_and_static_renderer(self) -> None:
        path = FACTORY / "config/release_profiles/book-classic-narrator-hbg-16x9-v1.json"
        profile = ReleaseProfile.load(path)
        self.assertEqual(profile.payload["script"]["contract"], "script.narrator-essay.v1")
        self.assertEqual(profile.payload["audio"]["provider"], "minimax")
        self.assertEqual(profile.payload["audio"]["timing_source"], "provider")
        self.assertEqual(profile.payload["renderer"], "static_streaming_ffmpeg")
        self.assertEqual(profile.payload["video"]["preview_renderer"], "hbg-hyperframes")
        self.assertEqual(profile.payload["video"]["master_renderer"], "static_streaming_ffmpeg")

    def test_pipeline_contract_declares_single_state_authority(self) -> None:
        path = FACTORY / "config/pipelines/classic-narrator-hbg-v1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["pipeline_id"], "classic-narrator-hbg-v1")
        self.assertEqual(payload["state_authority"], "workflow-gates-manifests")
        self.assertEqual(payload["audio_provider"], "minimax")
        self.assertEqual(payload["timing_source"], "provider")
        self.assertEqual(payload["production_engine"], "vendor/hbg-life-simulation")

    def test_rejected_product_files_are_physically_absent(self) -> None:
        existing = [relative for relative in REMOVED_PATHS if (REPO / relative).exists()]
        self.assertEqual(existing, [], f"rejected product files remain: {existing}")

    def test_current_skill_and_readme_do_not_route_to_rejected_products(self) -> None:
        active_files = [REPO / "README.md", REPO / "skill/SKILL.md"]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in active_files)
        hits = [term for term in FORBIDDEN_ACTIVE_TERMS if term in combined]
        self.assertEqual(hits, [], f"active product docs still route to rejected products: {hits}")

    def test_vendor_engine_is_declared_in_skill(self) -> None:
        text = (REPO / "skill/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("vendor/hbg-life-simulation", text)
        self.assertIn("Edge TTS", text)
        self.assertIn("VTT", text)
        self.assertIn("render_streaming_ffmpeg.mjs", text)


if __name__ == "__main__":
    unittest.main()
