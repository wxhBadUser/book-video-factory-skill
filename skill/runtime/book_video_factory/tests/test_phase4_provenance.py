from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from book_video_factory.audio_stage.provenance import tool_provenance


def test_edge_tts_provenance_uses_active_python_interpreter() -> None:
    calls=[]
    def completed(command, **_kwargs):
        calls.append(command)
        return mock.Mock(returncode=0, stdout="v1\n", stderr="")
    with mock.patch("book_video_factory.audio_stage.provenance.subprocess.run",side_effect=completed):
        report=tool_provenance(external_edge_service_exercised=False)
    assert report["edge_tts"] == "v1"
    assert [sys.executable,"-m","edge_tts","--version"] in calls
