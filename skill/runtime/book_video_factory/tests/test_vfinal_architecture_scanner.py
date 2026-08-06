from __future__ import annotations
import importlib.util,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("scanner",ROOT/"scripts/verify_vfinal_architecture.py"); scanner=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(scanner)

class VFinalArchitectureScannerTests(unittest.TestCase):
 def test_current_repository_has_no_critical_findings(self):
  report=scanner.scan_repository(ROOT); self.assertEqual(report["critical_count"],0,report["findings"])
 def test_detects_private_image_client_and_duplicate_ffmpeg(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp); path=root/"book_video_factory/src/book_video_factory/render_stage"; path.mkdir(parents=True)
   (path/"bad.py").write_text('import requests\nrequests.post("x")\nimport subprocess\nsubprocess.run(["ffmpeg","-i","x"])\n')
   findings=scanner.scan_repository(root)["findings"]; ids={item["check_id"] for item in findings}
   self.assertIn("private_image_client",ids); self.assertIn("duplicate_media_engine",ids)
if __name__=="__main__": unittest.main()
