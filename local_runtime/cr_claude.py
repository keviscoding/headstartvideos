"""Customer-owned Claude Code execution around the protected Frontier workflow.

The workflow keeps its own helpers, prompts, tools, and response formats. This
module gates only its Claude subprocesses; it never makes a generation API call.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar

try:
    from .cr_mcp import WorkflowError
except ImportError:
    from cr_mcp import WorkflowError

GENERAL_MODEL = "claude-sonnet-5-5"
VISUAL_MODEL = "claude-opus-5-5"
MODELS = (GENERAL_MODEL, VISUAL_MODEL)
MINIMUM_VERSION = (2, 1, 268)
_real_run = subprocess.run
_runtime = None
_lock = threading.RLock()
_slots = threading.BoundedSemaphore(2)
HOME = Path(__file__).resolve().parent
_expected_stage = ContextVar("cr_expected_native_stage", default=None)


@contextmanager
def expect_stage(project_id, stage_id):
    """A reviewed recovery may execute only its exact original native request."""
    if not re.fullmatch(r"crv_[a-f0-9]{24}", project_id or "") or not re.fullmatch(r"cc_[a-f0-9]{64}", stage_id or ""):
        raise RuntimeError("Choose a valid reviewed native stage.")
    token = _expected_stage.set((project_id, stage_id))
    try:
        yield
    finally:
        _expected_stage.reset(token)


class ClaudeStageStopped(BaseException):
    """Stop outside the source's retry/fallback handlers after uncertain work."""


def failure_info(stdout="",stderr=""):
    """Classify failures without displaying upstream bodies or credentials."""
    diagnostic=(str(stdout)+" "+str(stderr)).lower()
    if any(x in diagnostic for x in ("usage limit","session limit","weekly limit","rate limit","limit reached","out of extra usage","hit your limit","usage exceeded","rate_limit","too many requests")):
        return "usage_limit","Your Claude account reached its usage limit. Check its reset or usage allowance, then review this saved stage in setup."
    if any(x in diagnostic for x in ("authentication","oauth","not logged in","invalid token","please log in")):
        return "auth_missing","Claude Code needs you to sign in again. Reconnect your Claude account, then review this saved stage in setup."
    if any(x in diagnostic for x in ("model not found","invalid model","not have access to","not available for your","model does not exist")):
        return "model_unavailable","Your Claude account could not use the selected model. Check model access and your saved choices in setup."
    if any(x in diagnostic for x in ("connection error","econnreset","etimedout","econnrefused","overloaded","internal server error","service unavailable")) or re.search(r"api error:\s*5\d\d",diagnostic):
        return "network_interrupted","Claude's connection or service failed. Check its service status and your saved stage in setup before resuming."
    return "cli_failed","Claude could not finish this stage. Its diagnostic is saved privately on this computer. Review recovery in ChannelRecipe."


def private_diagnostic(completed,cfg):
    """Keep a bounded local diagnostic; never send its contents to CR."""
    result={"returncode":completed.returncode}
    for name in ("stdout","stderr"):
        value=str(getattr(completed,name,"") or "")[-12000:]
        # Upstream bodies are untrusted. Remove credential-shaped values and
        # this computer's explicitly configured secrets before saving them.
        for secret in (cfg.get("key"),*(v for k,v in os.environ.items() if k.upper().endswith(("_API_KEY","_AUTH_TOKEN","_ACCESS_TOKEN")))):
            if isinstance(secret,str) and len(secret)>6:value=value.replace(secret,"[redacted]")
        value=re.sub(r"(?i)(bearer\s+)[a-z0-9._\-]+",r"\1[redacted]",value)
        value=re.sub(r"\b(?:sk-ant-|sk-|cr_mcp_)[a-zA-Z0-9_\-]+","[redacted]",value)
        result[name]=value
    return result


def sync_receipts(bridge):
    """Reconcile already finished local stages; this never starts Claude."""
    saved=0
    for path in list((Path(bridge.HOME)/"output/cr-claude-cache").glob("*/*.json")):
        try:
            data=json.loads(path.read_text())
            if data.get("state") not in {"complete_local","failed_local"} or not data.get("receipt"):
                continue
            bridge.finish_stage(data["receipt"])
            data["state"]="complete" if data["state"]=="complete_local" else "failed"
            data.pop("receipt",None)
            _private_write(path,data)
            saved+=1
        except (Exception,ValueError):
            continue
    return saved


def clean_environment(source=None):
    """Use the customer's local login, never inherited API/cloud credentials."""
    env = dict(os.environ if source is None else source)
    for name in list(env):
        upper = name.upper()
        if (upper.startswith(("ANTHROPIC_", "CLAUDE_CODE_OAUTH_", "CLAUDE_CODE_USE_",
                              "AWS_ACCESS_", "AWS_SECRET_", "AWS_SESSION_", "AZURE_"))
                or upper.endswith(("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN"))
                or upper in {"CLAUDECODE", "GOOGLE_APPLICATION_CREDENTIALS",
                             "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDE_CODE_PLUGIN_DIRS",
                             "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "MAX_THINKING_TOKENS",
                             "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"}):
            env.pop(name, None)
    # Do not repeat a timed-out request inside the CLI either. Rejected-request
    # handling remains Claude Code's own behavior, within this one authorization.
    env["CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES"] = "0"
    return env


def find_executable():
    found = shutil.which("claude")
    if found:
        return str(Path(found).resolve())
    candidates = [Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                  Path("/usr/local/bin/claude")]
    if os.name == "nt":
        candidates += [Path(os.getenv("USERPROFILE", str(Path.home()))) / ".local/bin/claude.exe",
                       Path(os.getenv("APPDATA", "")) / "npm/claude.cmd"]
    return next((str(p.resolve()) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def status(executable=None, *, run=None, environment=None):
    """Read-only installation/login check. Return no identity or credentials."""
    exe = executable or find_executable()
    result = {"installed": bool(exe), "authenticated": False, "ready": False,
              "version": "", "auth_method": "none", "api_provider": "", "subscription_type": ""}
    if not exe:
        result["message"] = "Install Claude Code, then sign in with your Claude account."
        return result
    invoke = run or _real_run
    env = clean_environment(environment)
    try:
        version = invoke([exe, "--version"], capture_output=True, text=True, env=env, timeout=20)
        match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version.stdout or "")
        if version.returncode or not match:
            raise ValueError("version")
        result["version"] = match.group(0)
        if tuple(map(int, match.groups())) < MINIMUM_VERSION:
            result["message"] = "Update Claude Code to version 2.1.268 or newer."
            return result
        auth = invoke([exe, "--setting-sources", "", "auth", "status"],
                      capture_output=True, text=True, env=env, timeout=20)
        data = json.loads(auth.stdout or "{}")
        result["account_fingerprint"] = hashlib.sha256(str(data.get("email") or data.get("accountUuid") or "unknown-local-account").encode()).hexdigest()
        method = data.get("authMethod", "none")
        result["auth_method"] = method if method in {"none", "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"} else "unknown"
        result["api_provider"] = "firstParty" if data.get("apiProvider") == "firstParty" else "other"
        subscription = data.get("subscriptionType", "")
        result["subscription_type"] = subscription if subscription in {"free", "pro", "max", "team", "enterprise"} else ""
        result["authenticated"] = bool(auth.returncode == 0 and data.get("loggedIn") is True
                                       and method == "claude.ai" and data.get("apiProvider") == "firstParty")
        result["ready"] = result["authenticated"]
        import cr_allowance
        result["allowance"] = cr_allowance.status(HOME, result["account_fingerprint"])
        result["message"] = ("Your local Claude account is connected. Its usage limits apply."
                             if result["ready"] else "Sign in to Claude Code with your Claude subscription. API and cloud billing are not used in this mode.")
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        result["message"] = "Claude Code could not confirm its local login. Open it and sign in again."
    return result


def select_profile(general_model=GENERAL_MODEL, visual_model=VISUAL_MODEL):
    if general_model not in MODELS or visual_model not in MODELS:
        raise ValueError("Choose a supported Claude Sonnet 5.5 or Opus 5.5 model.")
    return {"brain_mode": "claudecode", "general_model": general_model, "visual_model": visual_model}


def preflight(settings=None):
    cfg = settings or {}
    profile = select_profile(cfg.get("general_model", cfg.get("claude_general_model", GENERAL_MODEL)),
                             cfg.get("visual_model", cfg.get("claude_visual_model", VISUAL_MODEL)))
    local = status()
    return {**local, **profile, "claude_installed": local["installed"],
            "claude_authenticated": local["authenticated"], "api_provider": local["api_provider"]}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _option(args, name, default=""):
    for i, value in enumerate(args):
        if value == name and i + 1 < len(args):
            return args[i + 1]
        if isinstance(value, str) and value.startswith(name + "="):
            return value[len(name) + 1:]
    return default


def _replace_option(args, name, value):
    result = list(args)
    for i, entry in enumerate(result):
        if entry == name and i + 1 < len(result):
            result[i + 1] = value
            return result
        if entry.startswith(name + "="):
            result[i] = name + "=" + value
            return result
    return result + [name, value]


def _source_context():
    """Read original call metadata without replacing or copying its helpers."""
    frame = inspect.currentframe()
    maximum, origin = None, "claude"
    try:
        frame = frame.f_back
        for _ in range(18):
            if frame is None:
                break
            filename = Path(frame.f_code.co_filename).name
            if filename in {"make_video.py", "folio.py", "threed.py", "reflearn.py"}:
                origin = filename.removesuffix(".py") + ":" + frame.f_code.co_name
                candidate = frame.f_locals.get("max_tokens")
                if maximum is None and isinstance(candidate, int) and not isinstance(candidate, bool):
                    maximum = min(32768, max(128, candidate))
            frame = frame.f_back
    finally:
        del frame
    return maximum, origin


def _read_inputs(prompt, args):
    # The original Read-based helpers put absolute image paths on their own
    # lines. Include file contents so a new render at the same path is new work.
    hashes = []
    if "Read" in _option(args, "--tools").split(","):
        for line in str(prompt).splitlines():
            if line.startswith("- "):
                path = Path(line[2:])
                if path.is_file():
                    hashes.append([str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest()])
    return hashes


def _private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd,name=tempfile.mkstemp(dir=path.parent,prefix="."+path.name+".",suffix=".tmp")
    temporary=Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        # Windows refuses to replace a file another process has open (Studio reading a video's progress.json):
        # PermissionError [WinError 5]. It is free again a moment later.
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _result(stdout, output_format):
    """Validate complete JSON/stream results without retaining reasoning logs."""
    if output_format == "text":
        if not str(stdout).strip():
            raise ValueError("empty_result")
        return {}, stdout
    if output_format == "stream-json":
        rows = [json.loads(line) for line in str(stdout).splitlines() if line.strip().startswith("{")]
        data = next((row for row in reversed(rows) if row.get("type") == "result"), None)
    else:
        data = json.loads(stdout)
        if isinstance(data, list):
            data = next((row for row in reversed(data) if isinstance(row, dict) and row.get("type") == "result"), None)
    if (not isinstance(data, dict) or data.get("is_error") or not str(data.get("result", "")).strip()
            or data.get("subtype", "success") != "success"):
        raise ValueError("incomplete_result")
    # The original Folio parser consumes the final result line, and 3D consumes
    # the result object. Both retain usage, but transient assistant/thinking
    # events never need to be stored or sent back to the caller.
    filtered = {key: data[key] for key in ("type", "subtype", "is_error", "result", "usage", "total_cost_usd", "duration_ms", "num_turns") if key in data}
    rendered = json.dumps(filtered, ensure_ascii=False) + ("\n" if output_format == "stream-json" else "")
    return data.get("usage") or {}, rendered


# A long answer through Claude Code: the source's API budget (3D director and review: 32k) is for the answer alone,
# but Claude Code's reasoning shares one message's output with it. Past Claude Code's default limit the answer went on
# in further messages and only the last part came back: a 3D review of 25 scenes lost every fix that way.
LONG_ANSWER_TOKENS = 16000          # a call asking for this much answer
LONG_OUTPUT_TOKENS = 128000         # may use this much output in one message (Claude Code caps it at the model's)
CONTINUED = "Output token limit hit"


def _stream_rows(stdout):
    rows = []
    for line in str(stdout).splitlines():
        if line.strip().startswith("{"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _texts(content):
    if isinstance(content, str):
        return [content]
    return [str(c.get("text", "")) for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]


def _whole_answer(stdout):
    """Claude Code's stream as the json result the source reads, with an answer continued past the output limit
    joined back together (its final result holds only the last part)."""
    rows = _stream_rows(stdout)
    data = next((row for row in reversed(rows) if row.get("type") == "result"), None)
    if not isinstance(data, dict):
        # no result (a failed run): what it said, without the stream's routine rate-limit and system events
        return "\n".join(line for line in str(stdout).splitlines()
                         if not any(row is not None and row.get("type") in ("rate_limit_event", "system")
                                    for row in _stream_rows(line) or [None]))
    parts, seen, continued = [], set(), False
    for row in rows:
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if row.get("type") == "user":
            content = message.get("content")
            if any(text.startswith(CONTINUED) for text in _texts(content)):
                continued = True                     # the same answer goes on in the next message
            elif isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                                   for c in content):
                parts, continued = [], False         # a tool's result: the answer starts after it
        elif row.get("type") == "assistant":
            for text in _texts(message.get("content")):
                key = (message.get("id"), text)
                if text and key not in seen:
                    seen.add(key)
                    parts.append(text)
    if continued and parts:
        data = dict(data, result="".join(parts))
    return json.dumps(data, ensure_ascii=False)


class Runtime:
    def __init__(self, engine, bridge, executable, real_run):
        self.engine, self.bridge, self.executable, self.real_run = engine, bridge, executable, real_run

    def handles(self, args):
        return (isinstance(args, (list, tuple)) and bool(args)
                and os.path.normcase(os.path.realpath(str(args[0]))) == os.path.normcase(os.path.realpath(self.executable)))

    def run(self, args, *positional, **kwargs):
        if not self.handles(args):
            return self.real_run(args, *positional, **kwargs)
        with _slots:
            return self.execute(args,*positional,**kwargs)

    def execute(self,args,*positional,**kwargs):
        if positional or kwargs.get("shell") or kwargs.get("input") is None:
            raise ClaudeStageStopped("This Claude stage has an unsupported execution contract. No request was started.")
        cfg = self.bridge.load_settings()
        if cfg.get("brain_mode") != "claudecode" or not cfg.get("costs_accepted"):
            raise ClaudeStageStopped("Connect Claude Code and review its usage in ChannelRecipe setup before starting this stage.")
        project = self.bridge.current_project() if hasattr(self.bridge, "current_project") else self.bridge._project
        project_id = project.get("id") if isinstance(project, dict) else None
        if not project_id:
            raise ClaudeStageStopped("Start this job through ChannelRecipe before running its workflow.")
        local = status(self.executable, run=self.real_run, environment=kwargs.get("env"))
        if not local["ready"]:
            raise ClaudeStageStopped(local["message"])
        requested = _option(args, "--model", GENERAL_MODEL)
        role = "visual" if "opus" in requested.lower() else "general"
        tools = _option(args, "--tools")
        kind = "research" if any(tool in tools.split(",") for tool in ("WebSearch", "WebFetch")) else "vision" if ("Read" in tools.split(",") or _option(args, "--input-format") == "stream-json") else "text"
        maximum, origin = _source_context()
        source_contract = {"args": list(args[1:]), "input": kwargs["input"],
                           "max_tokens": maximum, "read_files": _read_inputs(kwargs["input"], args)}
        fingerprint = _digest(source_contract)
        stage_id = "cc_" + fingerprint
        expected = _expected_stage.get()
        if expected is not None and expected != (project_id, stage_id):
            raise ClaudeStageStopped("The recovered request changed. No replacement stage was authorized or executed.")
        try:
            installed_version=json.loads((Path(self.bridge.HOME)/"cr-release.json").read_text())["version"]
        except (OSError,ValueError,KeyError):
            installed_version=cfg.get("local_version","5.3-admin.80")
        cache = Path(self.bridge.HOME) / "output" / "cr-claude-cache" / project_id / (stage_id + ".json")
        try:
            saved = json.loads(cache.read_text())
        except (OSError, ValueError):
            saved = {}
        stage = {"project_id": project_id, "stage_id": stage_id, "input_fingerprint": fingerprint,
                 "kind": kind, "model_role": role, "requested_model": requested,
                 "local_version": installed_version,
                 "brain_mode": "claudecode"}
        try:
            # A completion receipt lost in transit can be submitted again. This
            # only records the already cached result; it never invokes Claude.
            if saved.get("state") in {"complete_local","failed_local"}:
                self.bridge.finish_stage(saved["receipt"])
        except Exception:
            raise ClaudeStageStopped("ChannelRecipe could not authorize this stage. Your work is saved; reconnect before resuming.") from None
        try:
            approval = self.bridge.authorize_stage(stage)
        except WorkflowError as error:
            raise ClaudeStageStopped("ChannelRecipe stopped this stage: " + error.detail
                                     + " No new Claude request was made.") from None
        except Exception:
            raise ClaudeStageStopped("ChannelRecipe could not authorize this stage. Your work is saved; reconnect before resuming.") from None
        if approval.get("reuse") and not approval.get("execute"):
            if saved.get("stdout") is not None and approval.get("result_fingerprint") == saved.get("result_fingerprint"):
                return subprocess.CompletedProcess(args, 0, saved["stdout"], "")
            raise ClaudeStageStopped("This stage already completed, but its local result is unavailable. Review recovery in ChannelRecipe.")
        token = approval.get("authorization_token")
        selected = approval.get("selected_model")
        if approval.get("execute") is not True or not token or selected not in MODELS:
            raise ClaudeStageStopped("This stage stopped or is already running. Review its status in ChannelRecipe before resuming.")
        command = _replace_option(args, "--model", selected)
        command = _replace_option(command, "--setting-sources", "")
        # Source helpers use --strict-mcp-config and deliberately expose no MCP
        # tools. The runner calls the authenticated CR MCP before/after them.
        command = _replace_option(command, "--settings", '{"apiKeyHelper":""}')
        env = clean_environment(kwargs.get("env"))
        if maximum is not None and maximum >= LONG_ANSWER_TOKENS:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(LONG_OUTPUT_TOKENS)
        streamed = _option(args, "--output-format", "text") == "json"
        if streamed:
            # Every message, so an answer continued past the output limit comes back whole (_whole_answer).
            command = _replace_option(command, "--output-format", "stream-json") + ["--verbose"]
        # Execution evidence is captured only on the actual authorized CLI
        # branch. Completed-cache returns above never acquire new provenance.
        workflow = _verification_workflow(self.bridge.HOME)
        execution_binding = {"native_account_fingerprint": local.get("account_fingerprint"),
            "profile_identity": _verification_identity(cfg, local, root=self.bridge.HOME),
            "model_role": role, "selected_model": selected,
            "workflow_version": workflow.get("workflow"), "engine_source_sha256": workflow.get("source")}
        # Original native helpers do not apply their API max_tokens argument
        # to Claude Code. Keep its native reasoning/output behavior intact.
        options = {**kwargs, "env": env}
        _private_write(cache, {"state": "running", "input_fingerprint": fingerprint})
        started = time.monotonic()
        receipt = {"project_id": project_id, "stage_id": stage_id, "authorization_token": token,
                   "status": "failed", "error_code": "cli_failed", "usage": {}}
        failure_code="cli_failed"
        failure_message="Claude could not finish this stage. Review recovery in ChannelRecipe."
        completed=None
        try:
            completed = self.real_run(command, **options)
            if streamed and completed is not None:
                # The stream lists rate-limit events on every run: classify a failure by its result alone.
                completed = subprocess.CompletedProcess(getattr(completed, "args", command), completed.returncode,
                                                        _whole_answer(getattr(completed, "stdout", "") or ""),
                                                        getattr(completed, "stderr", ""))
            failure_code,failure_message=failure_info(getattr(completed,"stdout",""),getattr(completed,"stderr",""))
            if completed.returncode:
                raise ValueError("cli_failed")
            usage, output = _result(completed.stdout or "", _option(args, "--output-format", "text"))
            safe_usage = {name: int(usage[name]) for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                          if isinstance(usage.get(name), (int, float)) and not isinstance(usage[name], bool) and usage[name] >= 0}
            safe_usage.update({"duration_seconds": time.monotonic() - started, "reported": bool(usage)})
            output_digest = hashlib.sha256(output.encode()).hexdigest()
            # A changed/unknown identity does not invalidate a completed native
            # result. It merely makes that result ineligible as a revision seed.
            try:
                after_local = status(self.executable, run=self.real_run, environment=kwargs.get("env"))
                after_cfg = self.bridge.load_settings()
                after_workflow = _verification_workflow(self.bridge.HOME)
                after_binding = {"native_account_fingerprint": after_local.get("account_fingerprint"),
                    "profile_identity": _verification_identity(after_cfg, after_local, root=self.bridge.HOME),
                    "model_role": role, "selected_model": selected,
                    "workflow_version": after_workflow.get("workflow"), "engine_source_sha256": after_workflow.get("source")}
                if (not after_local.get("ready") or execution_binding != after_binding
                        or not execution_binding["native_account_fingerprint"]
                        or not workflow or cfg.get("brain_mode") != "claudecode"):
                    execution_binding = None
            except (OSError, ValueError, TypeError, RuntimeError):
                execution_binding = None
            receipt.update(status="complete", result_fingerprint=output_digest, usage=safe_usage, error_code="")
            _private_write(cache, {"state": "complete_local", "stdout": output, "result_fingerprint": output_digest,
                                   "input_fingerprint": fingerprint, "receipt": receipt, "origin": origin,
                                   "execution_binding": execution_binding})
            self.bridge.finish_stage(receipt)
            _private_write(cache, {"state": "complete", "stdout": output, "result_fingerprint": output_digest,
                                   "input_fingerprint": fingerprint, "origin": origin, "execution_binding": execution_binding})
            return subprocess.CompletedProcess(args, 0, output, "")
        except BaseException as error:
            if receipt["status"] == "complete":
                raise ClaudeStageStopped("Claude finished this stage, but ChannelRecipe could not save its receipt. Reconnect and resume to recover the cached result.") from None
            receipt["error_code"] = "timeout" if isinstance(error, subprocess.TimeoutExpired) else "cancelled" if isinstance(error, (KeyboardInterrupt, SystemExit)) else failure_code
            if receipt["error_code"] == "usage_limit" and completed is not None:
                import cr_allowance
                allowance = cr_allowance.record(self.bridge.HOME, local.get("account_fingerprint"),
                    str(getattr(completed, "stdout", "")) + " " + str(getattr(completed, "stderr", "")))
                if allowance:
                    failure_message = allowance["message"]
            receipt["usage"] = {"duration_seconds": time.monotonic() - started, "reported": False}
            failed={"state":"failed_local","input_fingerprint":fingerprint,"error_code":receipt["error_code"],"receipt":receipt,
                "diagnostic":private_diagnostic(completed,cfg) if completed is not None else {"exception_type":type(error).__name__}}
            _private_write(cache,failed)
            try:
                self.bridge.finish_stage(receipt)
                failed.update(state="failed")
                failed.pop("receipt",None)
                _private_write(cache,failed)
            except Exception:
                pass
            if receipt["error_code"]=="timeout":
                failure_message="Claude took too long to finish this stage. Check your account usage and saved work in setup before resuming."
            raise ClaudeStageStopped(failure_message+" It will not run again automatically.") from None


def install(engine, bridge):
    """Install only inside the isolated local runner; leave source helpers intact."""
    global _runtime
    cfg = bridge.load_settings()
    profile = select_profile(cfg.get("general_model", cfg.get("claude_general_model", GENERAL_MODEL)),
                             cfg.get("visual_model", cfg.get("claude_visual_model", VISUAL_MODEL)))
    executable = find_executable()
    engine.CLAUDE_PROVIDER = engine.SCRIPT_PROVIDER = "claudecode"
    engine.CLAUDE_FALLBACK = ""
    engine._HANDOFF_DIR = ""
    # Keep the source's generic-model marker. Substituting the selected model
    # here would turn a general Opus choice into a specialist routing request.
    if not hasattr(engine,"_cr_original_cc_model"):
        engine._cr_original_cc_model=getattr(engine,"CC_MODEL","sonnet")
    engine.CC_MODEL = engine._cr_original_cc_model
    engine._claude_exe = lambda: executable
    # Every fallback endpoint is unavailable in this mode, including when a
    # local .env contains API credentials for a different workflow.
    engine._claude_endpoint = lambda provider: None
    if not executable:
        return {**profile, "ready":False}
    if hasattr(engine, "_DEAD_PROVIDERS"):
        engine._DEAD_PROVIDERS.clear()
    with _lock:
        _runtime = Runtime(engine, bridge, executable, _real_run)
        subprocess.run = _runtime.run
    return {"brain_mode": "claudecode", "general_model": profile["general_model"], "visual_model": profile["visual_model"]}


def _verification_workflow(root=None):
    try:
        release = json.loads((Path(root or HOME)/"cr-release.json").read_text())
        workflow, source = release["workflow_version"], release["source_sha256"]
        if not isinstance(workflow, str) or not workflow or not re.fullmatch(r"[a-f0-9]{64}", source):
            return {}
        return {"workflow":workflow, "source":source}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _verification_identity(settings, local, *, root=None):
    workflow = _verification_workflow(root)
    return _verification_identity_for_workflow(settings, local, workflow)


def _verification_identity_for_workflow(settings, local, workflow):
    return _digest({"general":settings.get("general_model"), "visual":settings.get("visual_model"),
        "brain_mode":settings.get("brain_mode"), "workflow":workflow,
        "release":None if workflow else settings.get("local_version"), "claude_version":local.get("version"),
        "auth":local.get("auth_method"), "provider":local.get("api_provider"),"account":local.get("account_fingerprint")})


def _verification_marker(settings, local, *, root=None):
    try:
        marker = json.loads((Path(root or HOME)/".cr-claude-verified.json").read_text())
        verified_at = marker.get("verified_at", 0)
        if (not local.get("ready") or not isinstance(verified_at, (int, float))
                or not 0 <= time.time()-verified_at < 86400):
            return None
        if marker.get("identity") == _verification_identity(settings, local, root=root):
            return marker
        # A product-name-only migration must not spend another model probe.
        # Prove the old marker against its exact saved contract and the current
        # account, models and CLI; only the two reviewed names share this source.
        previous, current = marker.get("workflow"), _verification_workflow(root)
        names = {"7b0bef7c7ec842ceb21abf01367533a784a41c4727ebd218e03318e1048d4ed8",
                 "25efdf9e2e0d26007e0a0b7564ab7ae01075c53bae1047b6eb4ae323ef37237e"}
        source = "e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"
        if (marker.get("schema_version") == 2 and isinstance(previous, dict)
                and previous.get("source") == current.get("source") == source
                and isinstance(previous.get("workflow"), str) and isinstance(current.get("workflow"), str)
                and hashlib.sha256(previous["workflow"].encode()).hexdigest() in names
                and hashlib.sha256(current["workflow"].encode()).hexdigest() in names
                and marker.get("identity") == _verification_identity_for_workflow(settings, local, previous)):
            return marker
        # Older adapters bound the check to cfg.local_version. Only migrate an
        # exact saved-profile match from this reviewed, unchanged engine family.
        # Other workflows, sources and future releases need their own checks.
        known_workflow = {"workflow":"frontier-5.3-claudecode.1",
            "source":"e379dc601672282040a2adbf30f7bf7f7d34fcdf38154e5f6a24ec6c2528cadf"}
        legacy_releases = {"5.3-admin.8", "5.3-admin.9", "5.3-admin.10",
                           "5.3-admin.11", "5.3-admin.12", "5.3-admin.13"}
        if (marker.get("schema_version") is None and settings.get("local_version") in legacy_releases
                and _verification_workflow(root) == known_workflow):
            legacy_identity = _digest({"general":settings.get("general_model"), "visual":settings.get("visual_model"),
                "version":settings.get("local_version"), "claude_version":local.get("version"),
                "auth":local.get("auth_method"), "provider":local.get("api_provider"),
                "account":local.get("account_fingerprint")})
            if marker.get("identity") == legacy_identity:
                return marker
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def models_verified(settings, local, *, root=None):
    return _verification_marker(settings, local, root=root) is not None


def preserve_model_verification(settings, local, *, root=None):
    """Persist a valid check before a setup action changes its adapter version."""
    marker = _verification_marker(settings, local, root=root)
    if marker is None:
        return False
    _private_write(Path(root or HOME)/".cr-claude-verified.json", {
        "schema_version":2, "identity":_verification_identity(settings, local, root=root),
        "workflow":_verification_workflow(root), "verified_at":marker["verified_at"]})
    return True


def _finish_model_verification(bridge, probe, verification, profile, previous_identity=None):
    """Close saved setup work; replay metadata only after a lost connection."""
    if (not isinstance(probe, dict) or not isinstance(probe.get("identity"), str)
            or not probe.get("complete") or probe.get("identity") not in
            {verification["identity"], previous_identity}):
        return True
    project_id = probe.get("project_id", "")
    if not isinstance(project_id, str) or not re.fullmatch(r"crv_[a-f0-9]{24}", project_id):
        return True
    if probe.get("completion_saved"):
        return True
    marker = Path(bridge.HOME)/".cr-claude-probe.json"
    report_dir = Path(bridge.HOME)/"output/cr-claude-checks"/project_id
    pending_file = report_dir/".cr-finish-pending.json"
    try:
        pending = dict(probe)
        pending["identity"] = verification["identity"]
        if not pending.get("finish_receipt"):
            report = {"schema_version":1, "general_model":profile["general_model"],
                "visual_model":profile["visual_model"], "workflow":verification["workflow"],
                "verified_at":verification["verified_at"]}
            report_path = report_dir/"model-verification.json"
            _private_write(report_path, report)
            pending["finish_receipt"] = {"project_id":project_id, "status":"done", "error_code":"",
                "artifacts":[{"name":report_path.name, "sha256":hashlib.sha256(report_path.read_bytes()).hexdigest(),
                              "media_type":"application/json"}]}
        _private_write(marker, pending)
        _private_write(pending_file, pending["finish_receipt"])
        bridge.finish_project(pending["finish_receipt"])
        _private_write(report_dir/".cr-finish.json",pending["finish_receipt"])
        pending_file.unlink(missing_ok=True)
        pending.pop("finish_receipt", None)
        pending["completion_saved"] = True
        _private_write(marker, pending)
        return True
    except Exception:
        # Model access already passed. A missing completion receipt must not
        # turn that success into another paid model check.
        return False


def verify_models(engine, bridge, proof):
    cfg = bridge.load_settings()
    profile = select_profile(cfg.get("general_model",GENERAL_MODEL),cfg.get("visual_model",VISUAL_MODEL))
    cfg = {**cfg, **profile}
    local = status()
    marker = Path(bridge.HOME)/".cr-claude-probe.json"
    identity = _verification_identity(cfg,local,root=bridge.HOME)
    try:
        previous = json.loads(marker.read_text())
    except (OSError,ValueError):
        previous = {}
    verified = _verification_marker(cfg,local,root=bridge.HOME)
    if verified is not None and preserve_model_verification(cfg,local,root=bridge.HOME):
        migrated = {"identity":identity, "workflow":_verification_workflow(bridge.HOME),
                    "verified_at":verified["verified_at"]}
        saved = _finish_model_verification(bridge,previous,migrated,profile,verified["identity"])
        return {"verified":True,"reused":True,"completion_pending":not saved,
                "message":"Both selected Claude models already passed their connection checks." +
                    (" Reconnect CR to sync their saved completion record." if not saved else "")}
    if not local["ready"] or not cfg.get("costs_accepted"):
        raise RuntimeError("Sign in to Claude Code and acknowledge its usage before checking models.")
    project = bridge.begin_project("Claude model connection checks","custom",
        previous.get("project_id") if previous.get("identity")==identity and not previous.get("complete") else None,
        proof={**proof,**profile,"models_verified":False,"purpose":"setup"},purpose="setup")
    _private_write(marker,{"identity":identity,"project_id":project["id"]})
    install(engine,bridge)
    # Tiny access checks must not load the studio's interactive creative guide.
    # cwd is deliberately outside the request fingerprint, preserving recovery
    # of checks already reserved by an older adapter.
    with tempfile.TemporaryDirectory(prefix="cr-model-check-") as check_cwd:
        for role in ("general","visual"):
            # The request identifies its original role; CR substitutes the pinned
            # account model. This also verifies swapped or identical model choices.
            requested=GENERAL_MODEL if role=="general" else VISUAL_MODEL
            command=[find_executable(),"--print","--model",requested,"--output-format","json",
                "--tools","","--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',
                "--max-turns","1","--no-session-persistence"]
            result=subprocess.run(command,input="Connection check: reply exactly OK.",capture_output=True,text=True,
                                  env=clean_environment(),timeout=120,cwd=check_cwd)
            _,text=_result(result.stdout,"json")
            if json.loads(text).get("result","").strip() != "OK":
                raise ClaudeStageStopped("Claude did not complete its model check. Review the saved check before retrying.")
    completed_probe = {"identity":identity,"project_id":project["id"],"complete":True}
    _private_write(marker,completed_probe)
    bridge._project=None
    current = bridge.load_settings()
    current_mode = current.get("brain_mode")
    current = {**current, **select_profile(current.get("general_model",GENERAL_MODEL),
                                          current.get("visual_model",VISUAL_MODEL))}
    refreshed_local = status()
    if (current_mode != cfg.get("brain_mode") or not refreshed_local.get("ready")
            or _verification_identity(current,refreshed_local,root=bridge.HOME) != identity):
        raise RuntimeError("Your Claude account, models or workflow changed during these checks. Save the current choices and verify them again.")
    verification = {"schema_version":2,"identity":identity,
                    "workflow":_verification_workflow(bridge.HOME),"verified_at":time.time()}
    _private_write(Path(bridge.HOME)/".cr-claude-verified.json",verification)
    saved = _finish_model_verification(bridge,completed_probe,verification,profile)
    return {"verified":True,"reused":False,"completion_pending":not saved,
            "message":"Both selected Claude models passed. Their checks used your Claude Code account." +
                (" Reconnect CR to sync their saved completion record." if not saved else "")}


def uninstall():
    """Restore the process delegation; used by isolated runner shutdown/tests."""
    global _runtime
    with _lock:
        if _runtime is not None and subprocess.run == _runtime.run:
            subprocess.run = _real_run
        _runtime = None
