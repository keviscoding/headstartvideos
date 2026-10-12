"""Publish Frontier's original queue items with their owned CR project attached."""
from __future__ import annotations

import re
from types import FunctionType

from flask import request


def submit(studio, payload, project_id, *, before_publish=None):
    """Keep original validation and JOBS, but hide new specs until fully owned.

    Frontier's dispatcher remains alive between jobs. Merely suppressing its
    startup function cannot keep it from consuming an item appended to QUEUE.
    A private globals dictionary stages only this invocation's queue items;
    all other original globals, including JOBS and LOCK, remain shared.
    """
    if not re.fullmatch(r"crv_[a-f0-9]{24}", str(project_id)):
        raise ValueError("Use a valid saved CR project.")
    original = studio.app.view_functions["api_run"]
    staged = []
    globals_ = {**original.__globals__, "QUEUE": staged, "_ensure_dispatcher": lambda: None}
    capture = FunctionType(original.__code__, globals_, original.__name__,
                           original.__defaults__, original.__closure__)
    capture.__kwdefaults__ = original.__kwdefaults__
    cached_json = request._cached_json
    request._cached_json = (payload, payload)
    try:
        response = studio.app.make_response(capture())
    finally:
        request._cached_json = cached_json
    if not 200 <= response.status_code < 300:
        return response
    if not staged:
        raise RuntimeError("Frontier did not construct a queued job.")
    for spec in staged:
        spec["cr_project_id"] = project_id
    if before_publish is not None:
        before_publish()
    with studio.LOCK:
        studio.QUEUE.extend(staged)
    studio._ensure_dispatcher()
    return response
