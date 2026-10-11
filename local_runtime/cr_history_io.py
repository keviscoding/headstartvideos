"""Keep the owner's history animation code; adapt only its provider I/O.

The original function imports requests locally and checks a Kie key through
``__import__('os')``. A function-local import scope lets it keep its selection,
prompts, caches and scene handling without changing either module globally.
Provider generation/downloads go through cr_media's durable task receipts.
"""
from __future__ import annotations

import builtins
import functools
import threading
import types
from collections.abc import Mapping
from pathlib import Path

import cr_media


class _Environment(Mapping):
    def __init__(self, original):
        self.original = original

    def __getitem__(self, name):
        if name == "KIE_API_KEY":
            # This is a readiness signal inside the original function only.
            # Its Authorization header never leaves our scoped transport.
            if cr_media.active("motion"):
                # An explicitly selected provider is checked when a worker
                # actually needs I/O. Missing credentials then stop with setup
                # guidance instead of silently disabling requested animation;
                # existing cached clips still need no account validation.
                return "channelrecipe-local-motion-account"
            with cr_media.native_motion_account():
                provider = cr_media.profile("motion")["provider"]
                key_env = cr_media.PROVIDERS[provider]["key_env"]
                connected = bool(cr_media.os.getenv(key_env, "").strip())
            return "channelrecipe-local-motion-account" if connected else ""
        return self.original[name]

    def __iter__(self):
        return iter(dict.fromkeys([*self.original, "KIE_API_KEY"]))

    def __len__(self):
        return len(set(self.original) | {"KIE_API_KEY"})


class _ModuleProxy:
    def __init__(self, original, **overrides):
        self.original = original
        self.overrides = overrides

    def __getattr__(self, name):
        return self.overrides[name] if name in self.overrides else getattr(self.original, name)


class _Transport:
    """Translate the original create/poll/download handshake into one task.

    Compatibility handles exist only for this invocation. Actual provider IDs,
    generation status and output hashes remain owned by cr_media.run.
    """
    CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"

    def __init__(self, engine, log):
        self.engine = engine
        self.log = log
        self.worker = threading.local()
        self.lock = threading.Lock()
        self.completed = {}

    def upload(self, path):
        path = Path(path)
        with cr_media.native_motion_account():
            url = cr_media.upload(path, task="motion")
        self.worker.source, self.worker.url = path, url
        return url

    def post(self, url, *, json, **kwargs):
        inputs = json.get("input") or {}
        if (url != self.CREATE_URL or not getattr(self.worker, "source", None)
                or inputs.get("image_url") != self.worker.url):
            raise cr_media.AccountStop("The history animation provider request could not be matched to its saved picture.")
        dest = self.worker.source.with_suffix(".anim.mp4")
        with cr_media.native_motion_account():
            # The owner function already composes and bounds the scene prompt.
            # motion substitutes the selected model/size/duration only.
            cr_media.motion(self.worker.url, inputs["prompt"], dest, log=self.log)
        with self.lock:
            handle = "cr-history-" + str(len(self.completed) + 1)
            self.completed[handle] = dest
        return types.SimpleNamespace(json=lambda: {"code": 200, "data": {"taskId": handle}})

    def poll(self, handle, **kwargs):
        with self.lock:
            if handle not in self.completed:
                raise cr_media.AccountStop("The history animation completion could not be matched to its saved task.")
        return "cr-history-file:" + handle

    def get(self, url, **kwargs):
        prefix = "cr-history-file:"
        with self.lock:
            path = self.completed.get(url[len(prefix):]) if isinstance(url, str) and url.startswith(prefix) else None
        if path is None:
            raise cr_media.AccountStop("The history animation download could not be matched to its saved file.")
        # cr_media already downloaded and verified the existing task. Give the
        # original function its bytes without another transfer or generation.
        return types.SimpleNamespace(content=path.read_bytes())

    def engine_proxy(self):
        return _ModuleProxy(self.engine, _kie_upload_image=self.upload, _kie_poll=self.poll)


def install(history_module):
    """Execute the original animation function with invocation-local I/O."""
    original = history_module.animate_paintings
    if getattr(original, "__cr_history_original__", None) is not None:
        return original

    @functools.wraps(original)
    def animate_paintings(engine_arg, job, shots, cfg, log=print):
        transport = _Transport(engine_arg, log)
        # Change only the provider choices the user authorized. Leave every
        # selection, timing, cache and art-direction field to the owner code.
        routed_cfg = cfg
        if cfg is not None:
            with cr_media.native_motion_account():
                provider = cr_media.profile("motion")["provider"]
            routed_cfg = {**cfg, "model": cr_media.PROVIDERS[provider]["models"]["motion"], "resolution": "720p"}
        scope = dict(original.__globals__)
        inherited = scope.get("__builtins__", builtins)
        local_builtins = dict(inherited if isinstance(inherited, dict) else vars(inherited))
        native_import = local_builtins["__import__"]

        def scoped_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name == "requests":
                return transport
            if level == 0 and name == "os":
                os_module = native_import(name, globals, locals, fromlist, level)
                return _ModuleProxy(os_module, environ=_Environment(os_module.environ))
            return native_import(name, globals, locals, fromlist, level)

        local_builtins["__import__"] = scoped_import
        scope["__builtins__"] = local_builtins
        run_original = types.FunctionType(original.__code__, scope, original.__name__, original.__defaults__, original.__closure__)
        run_original.__kwdefaults__ = original.__kwdefaults__
        return run_original(transport.engine_proxy(), job, shots, routed_cfg, log=log)

    # The bundle smoke check can prove the original creative code is retained.
    animate_paintings.__cr_history_original__ = original
    animate_paintings._cr_original = original
    history_module.animate_paintings = animate_paintings
    return animate_paintings
