"""Keep new video attempts separate; explicit resumes bind the saved folder."""
from contextlib import contextmanager
import json
from pathlib import Path
import re


def owned_slug(engine, project_id):
    if not hasattr(engine, "list_jobs"):
        return None
    matches = []
    for job in engine.list_jobs():
        try:
            if json.loads((Path(job) / "cr-project.json").read_text(encoding="utf-8")).get("id") == project_id:
                matches.append(Path(job).name)
        except (OSError, ValueError, AttributeError):
            pass
    if len(matches) > 1:
        raise RuntimeError("This project has conflicting saved folders. Review its local records before resuming.")
    return matches[0] if matches else None


# A new attempt's folder: the title's slug and 8 characters of its project. It was base[:85] + 24 characters, and a
# video's deepest files went past Windows' 260-character limit (cr_paths). Saved attempts keep their saved names.
ATTEMPT_BASE, ATTEMPT_ID = 46, 8


def new_slug(base, identity):
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(base)).strip("-_") or "video"
    return base[:ATTEMPT_BASE].rstrip("-_") + "--" + identity[-ATTEMPT_ID:]


def reserve_output(engine, job, spec):
    """A legacy title folder without an owner marker must also be preserved."""
    if not spec.get("cr_attempt_slug") or (job / "published.json").exists():
        return
    title = Path(engine.youtube_filename(spec["title"])).stem
    folder = title
    if (job.parent.parent / folder).exists():
        # another video of this title is there: this one's own 8 characters, not its whole folder name (Windows' limit)
        folder = f"{title} ({job.name[-ATTEMPT_ID:]})"
        if (job.parent.parent / folder).exists():
            raise RuntimeError("This attempt's output folder already exists. Its files were preserved.")
    import cr_claude
    cr_claude._private_write(job / "published.json", {"folder":folder})


@contextmanager
def bind(engine, spec):
    slug = spec.get("cr_attempt_slug")
    if not slug:
        yield
        return
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,140}", slug) or slug != spec.get("slug"):
        raise RuntimeError("Use this video's saved attempt folder.")
    original = engine.job_dir
    title = spec["title"]
    def job_dir(requested, style=""):
        if requested != title:
            return original(requested, style)
        existing = engine.find_job(slug)
        return existing or (Path(engine.OUTPUT_ROOT) / engine.style_folder(style) / engine.WORK_DIR / slug)
    engine.job_dir = job_dir
    try:
        yield
    finally:
        engine.job_dir = original
