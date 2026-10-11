"""Completion receipts follow Frontier's published layout, not just its work cache."""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"local_runtime"))
import cr_studio


def test_published_video_is_first_and_private_cache_is_excluded(tmp_path):
    job=tmp_path/"_work"/"title"
    published=tmp_path/"日本語 title"
    job.mkdir(parents=True)
    published.mkdir()
    video=published/"日本語 title.mp4"
    video.write_bytes(b"published video")
    (published/"日本語.srt").write_text("captions")
    (job/"cr-project.json").write_text("private project marker")
    (job/".cr-finish-pending.json").write_text("private receipt")
    for i in range(25):
        (job/f"scene_{i:02}.png").write_bytes(b"working image")
    engine=SimpleNamespace(final_video=lambda _:video,main_dir=lambda _:published)
    artifacts=cr_studio.output_artifacts(engine,job)
    assert len(artifacts)==20
    assert artifacts[0]=={"name":"video.mp4","sha256":hashlib.sha256(b"published video").hexdigest(),"media_type":"video/mp4"}
    assert any(a["name"].endswith(".srt") for a in artifacts)
    assert not any("cr-project" in a["name"] or "cr-finish" in a["name"] for a in artifacts)
    assert len({a["name"] for a in artifacts})==len(artifacts)


def test_old_layout_does_not_duplicate_video(tmp_path):
    video=tmp_path/"video.mp4"
    video.write_bytes(b"legacy video")
    engine=SimpleNamespace(final_video=lambda _:video,main_dir=lambda _:tmp_path)
    artifacts=cr_studio.output_artifacts(engine,tmp_path)
    assert len(artifacts)==1 and artifacts[0]["name"]=="video.mp4"
