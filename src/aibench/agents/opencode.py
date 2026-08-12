"""Drive opencode, a production coding agent, instead of a hand-rolled tool loop.

``tool_loop`` is this project's own scaffold, and measured against the model alone it was the
dominant variable rather than a measurement of it: three identical repeats flipped 32.3% of
GLM-5.1's cases, the run-to-run spread was 12.9 points against 0.0 bare, and fixing three
defects inside it moved one model's pass rate by 58 points without touching the model. Every
difficulty number this project published rests on it.

This adapter answers the question that one cannot: how a model does inside a coding agent
people actually use. opencode is driven as a subprocess, and everything that decides what the
run means -- gateway, model, step budget, tool set, permissions -- is generated per run from
this repository's own configs. Nothing is read from the operator's ``~/.config/opencode``,
because a measurement that depends on an unversioned file on one machine cannot be recomputed.

Three findings shape the implementation, all measured rather than assumed:

* **opencode's permission config is not a filesystem boundary.** ``external_directory: deny``
  checks paths only for a hardcoded set of command names. Asked to read a file in this
  repository, ``cat`` was refused while ``grep``, ``head``, ``sed`` and ``find`` each returned
  its contents. An earlier version of this module claimed the opposite, on a probe that
  happened to use only ``cat`` and ``python3 -c`` -- both of which are in the checked set. The
  boundary here is therefore ``sandbox-exec``, which the kernel enforces, with the permission
  rules kept as a cheap second layer. Where no sandbox is available the run records that it
  had none; see ``artifacts["sandboxed"]``.
* **opencode resolves its project root by walking up to the git root.** With the workspace at
  ``runs/<run>/cases/<id>/workspace`` -- inside this repository -- the whole repository counts
  as "the project", so nothing in it is external at all. The workspace is mirrored to a
  temporary directory outside any checkout for that reason.
* **The step budget is real.** ``maxSteps`` stops the loop, which makes a panel that varies
  only the step budget constructible for the first time. It bounds model *turns*, not tool
  calls: one turn can issue several.

Out-of-workspace tool calls are counted into the artifacts regardless of whether they were
refused, because the point is to tell "did not happen" apart from "was not measured".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from aibench.agents.base import AgentAdapter
from aibench.io_util import repo_root
from aibench.models import AgentConfig, AgentRunResult, Case, ModelConfig, StepRecord, UsageRecord

#: Identifiers for the generated config. Fixed rather than derived: they appear in the
#: ``-m provider/model`` argument, and a name that varies per run would make the command line
#: differ between two otherwise identical measurements.
PROVIDER_ID = "aibench"
AGENT_ID = "aibench"

#: opencode reaches its gateway through the Vercel AI SDK's OpenAI-compatible provider, which
#: is the same wire protocol the other adapters speak to the same endpoint.
PROVIDER_NPM = "@ai-sdk/openai-compatible"

DEFAULT_SYSTEM = (
    "You are a coding agent working in the given directory. Fix the defect so the project's "
    "tests pass. Edit files directly. Do not modify the tests."
)

#: Directory entries no agent authored: interpreter and test-runner caches. Counting them as
#: edits would make ``empty_patch`` false for a run that changed nothing but executed pytest.
_IGNORED_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git", ".opencode"}
_IGNORED_SUFFIXES = (".pyc", ".pyo")

#: Programs a coding agent needs to inspect a workspace and run its tests. Deliberately the
#: same set ``tool_loop`` settled on, so switching adapters does not silently change what a
#: benchmarked model is allowed to do. ``python`` is on it and ``python`` can do anything --
#: the containment here is the path rules below plus the fact that nothing of value is
#: reachable from the mirrored workspace, not this list.
DEFAULT_ALLOWED_COMMANDS = (
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "pwd",
    "echo",
    "true",
    "sed",
    "diff",
)

#: Refused after the allowlist, because opencode resolves the *last* matching rule. These are
#: reachable through an allowed program anyway; the value is that a direct attempt is denied
#: and recorded rather than executed.
_DENIED_COMMANDS = ("rm", "curl", "wget", "ssh", "scp", "chmod", "chown", "sudo", "git", "kill")


def workspace_permissions(*, allowed: tuple[str, ...]) -> dict[str, Any]:
    """Permission rules for opencode's tools. A second layer, not the boundary.

    ``external_directory`` defaults to ``ask`` and ``--auto`` approves anything not explicitly
    denied, so omitting it would be the same as allowing it. Denied, it does stop the ``read``
    tool and some bash commands. It is *not* a filesystem boundary: path checking applies to a
    hardcoded set of command names, and ``grep``, ``head``, ``sed`` and ``find`` were each
    measured reading a file outside the workspace and returning it. The kernel-level boundary
    is ``sandbox-exec``; this block is what remains cheap and worth having anyway.

    There are deliberately no ``read``/``edit`` path rules here. They were tried and they are
    worse than nothing: ``bash`` rules match the raw command string, so absolute paths work,
    but ``read``/``edit`` do not match against an absolute pattern. A ``{"*": "deny"}`` fallback
    with allow-rules that never match locks the agent out of its own workspace -- measured as
    every read and every edit refused, the agent unable to fix a two-line defect, and not one
    sign of it in the pass rate.

    The bash allowlist is a separate concern from the boundary: it keeps a benchmarked model
    from running ``rm`` or ``curl`` on the host, which is the posture ``tool_loop`` was
    hardened to. Rules go broad-to-specific because opencode applies the last one that matches.
    """
    bash: dict[str, str] = {"*": "deny"}
    for program in allowed:
        bash[program] = "allow"
        bash[f"{program} *"] = "allow"
    for program in _DENIED_COMMANDS:
        bash[program] = "deny"
        bash[f"{program} *"] = "deny"
    return {
        "external_directory": "deny",
        "bash": bash,
        "webfetch": "deny",
        "websearch": "deny",
    }


def build_config(
    *,
    model: ModelConfig,
    api_key: str,
    base_url: str,
    model_name: str,
    max_steps: int,
    system_prompt: str,
    allowed_commands: tuple[str, ...],
) -> dict[str, Any]:
    """The whole of what opencode is told, derived from this repository's configs.

    Nothing here is read from the operator's machine, so two checkouts at the same commit
    with the same ``.env`` drive opencode identically.
    """
    return {
        "$schema": "https://opencode.ai/config.json",
        # An update mid-sweep would change the instrument between rows of the same matrix.
        "autoupdate": False,
        "share": "disabled",
        # A stray AGENTS.md near the workspace must not become part of the task.
        "instructions": [],
        "provider": {
            PROVIDER_ID: {
                "npm": PROVIDER_NPM,
                "name": PROVIDER_ID,
                "options": {"apiKey": api_key, "baseURL": base_url},
                "models": {model_name: {"name": model_name}},
            }
        },
        "agent": {
            AGENT_ID: {
                "mode": "primary",
                "maxSteps": max_steps,
                "temperature": model.temperature,
                "prompt": system_prompt,
                # Subagents and skills would make the step budget mean something else, and
                # network tools would make the run depend on the open internet. `todowrite`
                # spends steps on bookkeeping -- three of sixteen in a measured run -- and the
                # step budget is the one variable the anchor ladder moves, so a rung capped at
                # one step could spend it writing itself a to-do list.
                "tools": {
                    "webfetch": False,
                    "websearch": False,
                    "task": False,
                    "skill": False,
                    "todowrite": False,
                    "todoread": False,
                },
            }
        },
        "permission": workspace_permissions(allowed=allowed_commands),
    }


def child_environment(staging: Path, config_path: Path) -> dict[str, str]:
    """The environment opencode runs under, with the operator's own settings stripped out.

    ``OPENCODE_CONFIG`` names an *additional* config that opencode merges on top of the global
    scope; it does not replace it. So the operator's ``~/.config/opencode/opencode.jsonc`` --
    their providers, their models, their permissions -- was still in force, which is exactly
    the unversioned per-machine input this adapter claims not to have. Pointing
    ``XDG_CONFIG_HOME`` at the staging directory is what makes the claim true: there is no
    global config to find.

    Inherited ``OPENCODE_*`` variables are dropped for the same reason, and one of them matters
    more than the rest: ``OPENCODE_PERMISSION`` overrides the permission block wholesale, so a
    variable left in a shell could silently reopen everything the config closes.

    PATH is inherited on purpose: the grader runs ``python -m pytest -q``, and without the
    project's virtualenv on PATH the agent cannot run the tests it is graded on. It would lose
    the react-to-test-output axis with nothing in the numbers to show for it.
    """
    # PWD and OLDPWD are dropped along with them. They still name the harness's own directory
    # -- this repository -- and opencode stats what PWD points at during startup, so a child
    # given a different cwd was still reaching for the checkout. Absent, tools fall back to
    # getcwd(), which is the staged workspace.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("OPENCODE_") and k not in {"PWD", "OLDPWD"}
    }
    env.update(
        {
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            # opencode keeps its sessions in one SQLite database. Sharing it across concurrent
            # rows produced "database is locked" and cost the whole row as an infra_error; a
            # matrix runs up to parallel x case_workers of these at once. OPENCODE_DB names the
            # file directly; the data directory moves too, so snapshots and session storage do
            # not carry one case's history into the next.
            "OPENCODE_DB": str(staging / "opencode.db"),
            "XDG_CONFIG_HOME": str(staging / "config"),
            "XDG_DATA_HOME": str(staging / "data"),
            "XDG_CACHE_HOME": str(staging / "cache"),
            "XDG_STATE_HOME": str(staging / "state"),
        }
    )
    return env


def _tool_paths(tool: str, payload: dict[str, Any]) -> list[str]:
    """Filesystem-ish strings an opencode tool call names, for the out-of-workspace check."""
    if tool == "bash":
        return [str(payload.get("command") or "")]
    return [
        str(payload.get(key) or "")
        for key in ("filePath", "path", "pattern", "include")
        if payload.get(key)
    ]


def _escapes_workspace(tool: str, payload: dict[str, Any], workspace: Path) -> bool:
    """True when a tool call names a path that resolves outside the workspace.

    Deliberately independent of opencode's own refusal wording: this counts what was
    *attempted*, so a version that stops denying does not silently read as "clean".

    Three things the first version got wrong, each of which it reported as clean:
    ``../../secret`` was ignored for not starting with ``/``; ``~/secret`` likewise; and a
    prefix test on the raw string called ``/tmp/ws-other/x`` inside ``/tmp/ws``. Tokens are
    expanded and resolved, and containment is asked of the path rather than of the string.
    """
    for text in _tool_paths(tool, payload):
        for raw in text.replace("'", " ").replace('"', " ").replace("=", " ").split():
            token = raw.strip(",;:()")
            if not token or token.startswith("-"):
                continue
            if not (token.startswith(("/", "~", ".")) or "/" in token):
                continue
            candidate = Path(os.path.expanduser(token))
            if not candidate.is_absolute():
                candidate = workspace / candidate
            # Lexical, not filesystem: the path need not exist to have been asked for, and a
            # symlink inside the workspace is the agent's own to follow.
            resolved = Path(os.path.normpath(candidate))
            if not resolved.is_relative_to(workspace):
                return True
    return False


def parse_events(stdout: str, workspace: Path) -> dict[str, Any]:
    """Fold opencode's JSON event stream into usage, steps, and the two counters we audit.

    Per ``step_finish``, ``total`` is that step's whole context including cache reads, so the
    fields are summed rather than taken from the last event -- the last one alone would report
    a single turn as the cost of the run.
    """
    usage = UsageRecord()
    steps: list[StepRecord] = []
    final_text = ""
    denied = 0
    escapes: list[str] = []
    hit_step_limit = False
    session_errors: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # opencode may interleave non-JSON on stdout; a malformed line is not a failed run.
            continue
        kind = event.get("type")
        part = event.get("part") or {}

        if kind == "step_finish":
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            usage.prompt_tokens += _int(tokens.get("input")) + _int(cache.get("read"))
            usage.completion_tokens += _int(tokens.get("output")) + _int(tokens.get("reasoning"))
            usage.total_tokens += _int(tokens.get("total"))
            usage.model_calls += 1
        elif kind == "tool_use":
            tool = str(part.get("tool") or "")
            state = part.get("state") or {}
            status = str(state.get("status") or "")
            payload = state.get("input") or {}
            if status == "error":
                denied += 1
            if isinstance(payload, dict) and _escapes_workspace(tool, payload, workspace):
                escapes.append(f"{tool}:{json.dumps(payload, ensure_ascii=False)[:200]}")
            steps.append(
                StepRecord(
                    step_index=len(steps),
                    action="tool",
                    tool=tool,
                    detail=f"{status}: {json.dumps(payload, ensure_ascii=False)[:200]}",
                )
            )
        elif kind == "text":
            text = str(part.get("text") or "")
            if text.strip():
                final_text = text
            # Prose, and therefore soft. opencode does not print this itself; it instructs the
            # model to say it, as "CRITICAL - MAXIMUM STEPS REACHED", and the model paraphrases
            # in its own casing and wording. Matched case-insensitively for that reason. Missing
            # it costs only the status label -- the run is graded either way.
            if "maximum steps" in text.lower() or "maximum number of steps" in text.lower():
                hit_step_limit = True
        elif kind == "error":
            # A gateway failure -- auth, 429, 5xx, a dropped connection -- arrives here and the
            # CLI then exits non-zero. Without this branch the only surviving signal is the exit
            # code, and an outage that struck after the first step would be scored as a failed
            # attempt: infrastructure charged to the model, in the denominator, never retried.
            detail = event.get("error") or part.get("error") or {}
            session_errors.append(json.dumps(detail, ensure_ascii=False)[:300])

    return {
        "usage": usage,
        "steps": steps,
        "final_text": final_text,
        "tool_errors": denied,
        "out_of_workspace_attempts": escapes,
        "hit_step_limit": hit_step_limit,
        "session_errors": session_errors,
    }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def snapshot(root: Path) -> dict[str, str]:
    """Content hashes of everything an agent could have authored under ``root``."""
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths the agent added, rewrote, or deleted."""
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _mirror_into(source: Path, target: Path) -> None:
    """Make ``target`` hold exactly ``source``'s contents, deletions included.

    ``target`` itself is kept, because the grader was handed that path before the agent ran.

    Symlinks are copied as links rather than followed. Following them raised
    ``IsADirectoryError`` on a link to a directory, and by then the loop above had already
    emptied the target -- the graded workspace was destroyed on the way to reproducing it.
    """
    for entry in target.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_symlink():
            destination.symlink_to(os.readlink(entry))
        elif entry.is_dir():
            shutil.copytree(entry, destination, symlinks=True)
        else:
            shutil.copy2(entry, destination)


#: Apple deprecated the binary but it still works, and it is the only kernel-level file
#: boundary available here without a container.
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


def sandbox_profile(protected: Path, *, readable: tuple[Path, ...]) -> str:
    """A seatbelt profile that hides ``protected`` from the process and its descendants.

    Narrow on purpose. The threat is one specific thing -- the agent reading the case's own
    gold solution out of this repository -- so the profile denies that subtree and leaves the
    rest of the machine alone. An allowlist sandbox would be stronger and would also have to
    enumerate every path a Python or Node toolchain touches, which is how a sandbox ends up
    disabled in practice.

    ``readable`` are subtrees inside ``protected`` that must stay reachable: the virtualenv,
    because the grader's command is ``python -m pytest -q`` and an agent that cannot run the
    tests is a differently-shaped instrument.
    """
    lines = [
        "(version 1)",
        "(allow default)",
        # Contents, not metadata. Denying `file-read*` also denies `lstat`, and opencode stats
        # the repository on the way up its own PATH -- it exited before reaching the gateway
        # with `EPERM: operation not permitted, lstat`. Metadata reveals nothing that matters
        # here; the gold solution is bytes inside a file.
        f'(deny file-read-data file-write* (subpath "{protected}"))',
    ]
    lines += [f'(allow file-read-data (subpath "{path}"))' for path in readable]
    return "\n".join(lines) + "\n"


def _run_to_completion(
    command: list[str], *, env: dict[str, str], cwd: Path, timeout_s: float
) -> tuple[str, str, int | None, bool]:
    """Run ``command`` to completion or to the deadline, taking its descendants with it.

    ``subprocess.run(timeout=...)`` kills the process it launched and nothing below it.
    opencode's shell tool spawns its own children -- a pytest run, a node process -- and those
    survived, kept writing into the staging directory, and were still writing when the adapter
    deleted it. Its own session plus a group kill closes that.
    """
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        # The harness's own working directory is this repository. Inheriting it would start the
        # agent inside the checkout that holds every case's gold solution, make its relative
        # paths resolve there, and -- once the sandbox hides that directory -- stop opencode
        # from starting at all.
        cwd=cwd,
        # opencode reads a non-TTY stdin to EOF and appends it to the prompt, so the harness's
        # own stdin would become part of the benchmarked task.
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            return stdout, stderr, proc.returncode, False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            stdout, stderr = proc.communicate()
            return stdout, stderr, None, True


def _checkout_above(path: Path) -> Path | None:
    """The nearest ancestor that is a git checkout, if any."""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


class OpenCodeAgent(AgentAdapter):
    """A real coding agent, driven as a subprocess and confined to a mirrored workspace."""

    def __init__(self, agent_config: AgentConfig, model_config: ModelConfig) -> None:
        super().__init__(agent_config, model_config)
        opts = self.agent_config.options
        self.binary = str(opts.get("binary") or "opencode")
        self.system_prompt = str(opts.get("system_prompt") or DEFAULT_SYSTEM)
        configured = opts.get("allowed_commands")
        self.allowed_commands = tuple(configured) if configured else DEFAULT_ALLOWED_COMMANDS
        #: Caps the run independently of the run config, for a panel member that must stay
        #: cheap regardless of which run yaml drives it.
        self.max_steps_cap = opts.get("max_steps")

    def _resolve_endpoint(self) -> tuple[str, str, str] | str:
        """``(api_key, base_url, model_name)`` or the reason it cannot be assembled."""
        model = self.model_config
        api_key_env = model.api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(api_key_env) or os.environ.get("AIBENCH_API_KEY")
        if not api_key:
            return f"missing API key env: {api_key_env} or AIBENCH_API_KEY"
        base_url = (
            model.base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("AIBENCH_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        # Config wins over env, so a multi-model matrix cannot silently run one model under
        # several labels -- the defect that made an earlier ablation compare a model to itself.
        model_name = model.model or os.environ.get("OPENAI_MODEL")
        if not model_name:
            return "no model name in model config or OPENAI_MODEL"
        return api_key, base_url, model_name

    def _wrap_in_sandbox(self, command: list[str], staging: Path) -> tuple[list[str], bool]:
        """Put a kernel-enforced boundary around the run, and say whether it went on.

        opencode's own ``external_directory: deny`` is not one. It checks paths only for a
        hardcoded set of command names: measured against this repository, ``cat`` was refused
        while ``grep``, ``head``, ``sed`` and ``find`` all read the file and returned its
        contents. The rule is worth keeping -- it costs nothing and catches some -- but a
        benchmark cannot rest on it, because what it protects is the gold solution of the case
        being solved.

        Returns the possibly-wrapped command and whether the boundary was actually applied, so
        a run on a platform without ``sandbox-exec`` records that it had none rather than
        inheriting a guarantee it never got.
        """
        if not bool(self.agent_config.options.get("sandbox", True)):
            return command, False
        if not _SANDBOX_EXEC.is_file():
            return command, False
        repo = repo_root()
        profile = staging / "sandbox.sb"
        profile.write_text(
            sandbox_profile(repo, readable=(repo / ".venv",)),
            encoding="utf-8",
        )
        return [str(_SANDBOX_EXEC), "-f", str(profile), *command], True

    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        resolved = self._resolve_endpoint()
        if isinstance(resolved, str):
            return AgentRunResult(
                status="infra_error",
                error_message=resolved,
                wall_time_s=time.perf_counter() - t0,
            )
        api_key, base_url, model_name = resolved
        # Checked here rather than left to the exec: under the sandbox wrapper a missing binary
        # surfaces as the wrapper's own exit code, which says nothing about what is wrong.
        if shutil.which(self.binary) is None and not Path(self.binary).is_file():
            return AgentRunResult(
                status="infra_error",
                error_message=f"opencode binary not found: {self.binary}",
                wall_time_s=time.perf_counter() - t0,
            )
        steps_budget = int(self.max_steps_cap or max_steps)

        # Resolved, because opencode reports and matches the canonical path: on macOS mkdtemp
        # hands back /var/folders/... for a directory it calls /private/var/folders/..., and a
        # rule written against the unresolved form matches nothing it ever sees.
        staging = Path(tempfile.mkdtemp(prefix="aibench-opencode-")).resolve()
        try:
            checkout = _checkout_above(staging)
            if checkout is not None:
                # Inside a checkout, opencode would treat that repository as the project and
                # `external_directory` would match nothing -- the exact hole this staging
                # directory exists to close. Refuse rather than measure without a boundary.
                return AgentRunResult(
                    status="infra_error",
                    error_message=(
                        f"staging dir {staging} sits inside the git checkout {checkout}; "
                        "opencode would treat that repository as the project root and the "
                        "workspace boundary would not hold. Set TMPDIR outside any checkout."
                    ),
                    wall_time_s=time.perf_counter() - t0,
                )
            staged = staging / "workspace"
            shutil.copytree(workspace, staged, symlinks=True)
            before = snapshot(staged)

            config = build_config(
                model=self.model_config,
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_steps=steps_budget,
                system_prompt=self.system_prompt,
                allowed_commands=self.allowed_commands,
            )
            config_path = staging / "opencode.json"
            config_path.write_text(json.dumps(config, indent=1), encoding="utf-8")

            env = child_environment(staging, config_path)
            command = [
                self.binary,
                "run",
                "--dir",
                str(staged),
                "--format",
                "json",
                "--auto",
                # Without --pure, opencode loads external plugins and hangs indefinitely under
                # a generated config; measured at 150s with no event emitted.
                "--pure",
                "--agent",
                AGENT_ID,
                "-m",
                f"{PROVIDER_ID}/{model_name}",
                self._task_message(case),
            ]
            command, sandboxed = self._wrap_in_sandbox(command, staging)

            try:
                stdout, stderr, returncode, timed_out = _run_to_completion(
                    command, env=env, cwd=staged, timeout_s=max_wall_time_s
                )
            except OSError as e:
                return AgentRunResult(
                    status="infra_error",
                    error_message=f"could not launch {self.binary}: {e}",
                    wall_time_s=time.perf_counter() - t0,
                )

            parsed = parse_events(stdout, staged)
            after = snapshot(staged)
            written = changed_paths(before, after)
            # Mirrored back even on timeout: a partial edit is what the agent produced, and
            # grading it is the honest answer rather than discarding the attempt.
            _mirror_into(staged, workspace)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        usage: UsageRecord = parsed["usage"]
        artifacts = {
            "files_written": written,
            "final_message": parsed["final_text"][:2000],
            "exit_code": returncode,
            "tool_errors": parsed["tool_errors"],
            "out_of_workspace_attempts": parsed["out_of_workspace_attempts"],
            # Recorded rather than assumed: a reader of these artifacts must be able to tell a
            # run that was confined from one that merely was not observed leaving.
            "sandboxed": sandboxed,
            "session_errors": parsed["session_errors"],
            "stderr": (stderr or "")[-1000:],
        }
        wall = time.perf_counter() - t0

        # Order matters, and every branch here decides whether a row is charged to the model or
        # excluded as infrastructure. Only `infra_error` is excluded and retried (runner.py);
        # everything else is graded and counted, so a misrouted gateway failure reads as
        # weakness in the published rate.
        errors = parsed["session_errors"]
        if timed_out and usage.model_calls == 0:
            # Nothing came back at all. A wrong base_url or a dead gateway hangs exactly like
            # this, and calling it a timeout would report an outage as budget exhaustion --
            # measured against an unreachable endpoint, the process produced no output and had
            # not exited after 150s. "Timeout" is only defensible once the gateway answered.
            status, error = "infra_error", "opencode produced no model call before the deadline"
        elif timed_out:
            status, error = "timeout", "max_wall_time exceeded"
        elif errors:
            # The CLI reports the gateway failure and then exits non-zero. It may also recover
            # and finish the task, but an exit code of 0 is the only evidence of that.
            status = "infra_error" if returncode != 0 else "completed"
            error = f"opencode reported {len(errors)} session error(s): {errors[0]}"
        elif returncode != 0 and usage.model_calls == 0:
            # A launch or configuration failure, not an attempt at the task.
            status, error = "infra_error", f"opencode exited {returncode} without any model call"
        elif parsed["hit_step_limit"]:
            status, error = "timeout", "max_steps exceeded"
        elif returncode != 0:
            status, error = "failed", f"opencode exited {returncode}"
        else:
            status, error = "completed", None

        return AgentRunResult(
            status=status,
            artifacts=artifacts,
            usage=usage,
            steps=parsed["steps"],
            wall_time_s=wall,
            error_message=error,
            empty_patch=not written,
        )

    def _task_message(self, case: Case) -> str:
        """The task, without saying where the answer is.

        Only the prompt: the files are in the working directory and a coding agent's job is to
        find them. Pasting them in is what makes the single-turn adapters measure something
        else.
        """
        return (
            f"{case.prompt}\n\nThe project is in your working directory. Fix it so the tests pass."
        )
