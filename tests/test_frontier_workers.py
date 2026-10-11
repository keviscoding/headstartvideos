"""Original child processes inherit the owned CR project; media commands stay native."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/"local_runtime"))
import cr_workers


def test_child_rewrite_keeps_original_priority_options_and_arguments(tmp_path):
    command=["nice","-n","10",sys.executable,"-u",str(tmp_path/"timeline.py"),"render","a job with spaces"]
    project="crv_"+"1"*24
    changed=cr_workers.rewrite(command,project,home=tmp_path)
    assert changed[:5]==command[:5]
    assert changed[5:]==[str(tmp_path/"cr_worker.py"),"--project",project,"--",str(tmp_path/"timeline.py"),"render","a job with spaces"]


@pytest.mark.parametrize("command",[["ffmpeg","-i","a.mp4"],["node","render.js"],[sys.executable,"-c","print('plain python')"],
    [sys.executable,"/different/place/timeline.py","render","job"]])
def test_non_workers_are_not_changed(command,tmp_path):
    assert cr_workers.rewrite(command,None,home=tmp_path)==command


def test_generative_worker_cannot_launch_without_a_cr_project(tmp_path):
    with pytest.raises(RuntimeError,match="start this creative work"):
        cr_workers.rewrite([sys.executable,str(tmp_path/"offer.py"),"all","book"],None,home=tmp_path)
