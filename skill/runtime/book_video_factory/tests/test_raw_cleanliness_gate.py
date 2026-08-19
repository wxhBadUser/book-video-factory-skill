# -*- coding: utf-8 -*-
"""Regression: the previous G061 raw (burned subtitle) must be classified B."""
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "raw_cleanliness"
sys.path.insert(0, str(FIXTURES))
from raw_cleanliness import classify_raw_cleanliness

OLD_G061 = FIXTURES / "SCENE_CAPTION_GROUP_G061.png"
CLEAN_G050 = FIXTURES / "SCENE_CAPTION_GROUP_G050.png"

def test_g061_burned_subtitle_is_contaminated():
    if not OLD_G061.is_file():
        import pytest; pytest.skip("old G061 raw not present")
    result = classify_raw_cleanliness(OLD_G061)
    # The prior gate returned A_CLEAN for this burned-subtitle raw. It must
    # never be certified clean again; it is flagged B or VERIFY for manual
    # visual confirmation.
    assert result["verdict"] != "A_CLEAN", result
    assert result["reasons"], result

if __name__ == "__main__":
    for p in (OLD_G061,):
        if p.is_file():
            print(p.name, classify_raw_cleanliness(p))
