"""Drive pi, a second production coding agent, so a scaffold result is not one CLI's result.

``opencode`` answered "how does a model do inside a coding agent people actually use". With
one such adapter, every answer is also a statement about opencode. pi is a different harness
over the same wire protocol -- MIT, ``@earendil-works/pi-coding-agent``, binary ``pi`` -- so
running the same cases through both separates the model from the scaffold.

Everything that decides what a run means is generated per run from this repository's configs.
Nothing is read from the operator's ``~/.pi/agent``, because a measurement that depends on an
unversioned file on one machine cannot be recomputed.

Four findings shape the implementation, each measured against pi 0.84.3 rather than assumed:

* **pi has no sandbox, and says so.** Its own ``docs/security.md``: "Pi does not include a
  built-in sandbox… Real isolation needs to come from the operating system or a
  virtualization/container boundary." So unlike opencode there is no permission layer to keep
  as a cheap second line -- ``sandbox-exec`` is the only boundary, and a run that did not get
  one records ``artifacts["sandboxed"]: false`` rather than inheriting a guarantee it never
  had.
* **pi has no step budget.** Neither ``pi --help`` nor its settings reference has a turn or
  step cap. Left unenforced, ``budget_axis: steps`` would be a fiction for this adapter, so the
  budget is enforced here: the JSON event stream is read as it arrives, ``turn_end`` events are
  counted, and the process group is killed at the budget. That is a harder stop than opencode's
  -- opencode tells the model it is out of steps and the model closes inside the budget, while
  pi is killed mid-turn -- and the difference is recorded rather than smoothed over.
* **A dead gateway is silent.** Pointed at an unreachable endpoint, pi emitted zero bytes and
  had not exited after five minutes. It looks exactly like budget exhaustion from outside, so
  "timed out having made no model call" is classified as infrastructure. Calling it a timeout
  would charge an outage to the model, keep it in the denominator, and skip the retry.
* **The same usage is reported four times.** ``message_update`` carries a *cumulative* usage,
  ``turn_end`` re-emits the message, and ``agent_end`` re-emits every message in the session.
  Only ``message_end`` for an assistant message is counted; summing anything with a ``usage``
  field inflates the total three- to fourfold and still passes a "tokens are non-zero" check.

Out-of-workspace tool calls are counted into the artifacts whether or not they succeeded,
because the point is to tell "did not happen" apart from "was not measured".
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from aibench.agents.base import AgentAdapter
from aibench.agents.cli_sandbox import (
    SANDBOX_EXEC,
    changed_paths,
    checkout_above,
    escapes_workspace,
    mirror_into,
    protected_root,
    resolve_endpoint,
    sandbox_profile,
    snapshot,
)
from aibench.io_util import repo_root
from aibench.models import AgentConfig, AgentRunResult, Case, ModelConfig, StepRecord, UsageRecord

#: The generated provider's name. Fixed rather than derived: it appears in the ``--provider``
#: argument, and a name that varied per run would make the command line differ between two
#: otherwise identical measurements.
PROVIDER_ID = "aibench"

#: The API key reaches pi through the environment rather than through ``models.json``. pi
#: interpolates ``$NAME`` in that file at request time, so the generated config can name a
#: variable instead of carrying the secret -- which matters because the staging directory
#: survives a hard crash.
API_KEY_VAR = "AIBENCH_PI_API_KEY"

DEFAULT_SYSTEM = (
    "You are a coding agent working in the given directory. Fix the defect so the project's "
    "tests pass. Edit files directly. Do not modify the tests."
)

#: pi's built-ins, minus ``powershell``. Pinned rather than left to pi's own defaults, which
#: are version-dependent: a pi release that adds a tool would otherwise change what a
#: benchmarked model is allowed to do, with nothing in the numbers to show for it.
DEFAULT_TOOLS = ("read", "bash", "edit", "write", "grep", "find", "ls")

#: pi defaults custom models to 128000. Pinned because it is load-bearing in both directions:
#: too small and pi auto-compacts, spending model calls that are not turns and pulling usage
#: and the step budget apart; too large and a long session reaches the provider's real limit
#: as a context-overflow error, which grades as a failure by the model.
DEFAULT_CONTEXT_WINDOW = 128000


def build_models_json(
    *,
    base_url: str,
    model_name: str,
    max_tokens: int,
    temperature: float | None,
    context_window: int,
) -> dict[str, Any]:
    """The provider catalogue pi runs against, generated from this repository's model config.

    ``samplingParams`` is not decoration. pi sends *no* temperature to an OpenAI-compatible
    endpoint unless one appears here, while ``runner.py`` stamps the model config's
    ``temperature`` and ``max_tokens`` into the manifest regardless of what the adapter sent.
    Omitting it publishes a run recorded as ``temperature=0`` that was sampled at whatever the
    gateway defaults to. ``maxTokens`` is here for the same reason: pi's own default is 16384.
    """
    model: dict[str, Any] = {
        "id": model_name,
        "name": model_name,
        "contextWindow": int(context_window),
        "maxTokens": int(max_tokens),
    }
    if temperature is not None:
        # Merged verbatim into every request body after the fields pi sets itself, so these
        # keys win. pi's own docs call it the single source of sampling truth for a model.
        model["samplingParams"] = {"temperature": temperature}
    return {
        "providers": {
            PROVIDER_ID: {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": f"${API_KEY_VAR}",
                "models": [model],
            }
        }
    }


def build_settings_json(*, tools: tuple[str, ...]) -> dict[str, Any]:
    """pi's own settings, generated so the operator's are irrelevant.

    ``defaultProjectTrust: never`` is the one that matters. A case set is untrusted input
    (``docs/REFERENCE.md`` §13), and a case shipping a ``.pi/settings.json`` in its files would
    otherwise be asked about -- and in a non-interactive run, silently resolved by whatever the
    operator's global default happens to be.
    """
    return {
        "defaultProjectTrust": "never",
        "defaultTools": list(tools),
        "quietStartup": True,
        "enableInstallTelemetry": False,
        "enableAnalytics": False,
    }


def child_environment(agent_dir: Path, *, api_key: str) -> dict[str, str]:
    """The environment pi runs under, with the operator's own settings stripped out.

    ``PI_CODING_AGENT_DIR`` moves the whole config directory, so there is no
    ``~/.pi/agent/models.json``, ``settings.json``, ``auth.json`` or ``trust.json`` to find --
    the last of which would otherwise let a saved decision on a parent directory grant trust
    that ``defaultProjectTrust: never`` was set to refuse.

    Inherited ``PI_*`` variables are dropped first, because several of them override exactly
    what this function is setting.

    PATH is inherited on purpose: the grader runs ``python -m pytest -q`` or ``node --test``,
    and without the project's virtualenv and node on PATH the agent cannot run the tests it is
    graded on. It would lose the react-to-test-output axis with nothing in the numbers to show.
    """
    # PWD and OLDPWD still name the harness's own directory -- this repository -- and a child
    # given a different cwd would still have a pointer to the checkout. Absent, tools fall back
    # to getcwd(), which is the staged workspace.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PI_") and k not in {"PWD", "OLDPWD"}
    }
    env.update(
        {
            API_KEY_VAR: api_key,
            "PI_CODING_AGENT_DIR": str(agent_dir),
            "PI_CODING_AGENT_SESSION_DIR": str(agent_dir / "sessions"),
            # No update check, no package refresh, no telemetry ping. An update that landed
            # mid-sweep would change the instrument between one row and the next.
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
        }
    )
    return env


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _assistant_text(message: dict[str, Any]) -> str:
    """The prose an assistant message ends with, ignoring thinking and tool-call blocks."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def parse_events(stdout: str, workspace: Path) -> dict[str, Any]:
    """Fold pi's JSON event stream into usage, steps, and the counters we audit.

    Usage is summed over assistant ``message_end`` events only. pi reports the same numbers on
    ``message_update`` (cumulative), on ``turn_end`` and again on ``agent_end``; a loop that
    added every usage it saw would report three to four times the real cost.

    The field mapping follows pi's own accounting rather than opencode's: for an
    OpenAI-compatible provider pi computes ``input = promptTokens - cacheRead - cacheWrite``
    and takes ``output`` from ``completion_tokens``, which **already includes** reasoning
    tokens. Adding ``reasoning`` on top -- which is what the opencode adapter does, correctly,
    for its own schema -- would roughly double the completion count for a reasoning model.
    """
    usage = UsageRecord()
    steps: list[StepRecord] = []
    step_of_call: dict[str, int] = {}
    final_text = ""
    tool_errors = 0
    escapes: list[str] = []
    turns = 0
    session_errors: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # pi may interleave non-JSON on stdout; a malformed line is not a failed run.
            continue
        kind = event.get("type")

        if kind == "turn_end":
            turns += 1
        elif kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            counts = message.get("usage") or {}
            # Cache reads and writes belong to the prompt side, matching what the opencode
            # adapter reports, so that `prompt + completion == total` for every row.
            usage.prompt_tokens += (
                _int(counts.get("input"))
                + _int(counts.get("cacheRead"))
                + _int(counts.get("cacheWrite"))
            )
            usage.completion_tokens += _int(counts.get("output"))
            # Derived, not copied from `totalTokens`. The two agree on every row pi has
            # produced here, but a gateway that omits the field would otherwise zero the
            # headline token count and the cost estimate built on it, silently.
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
            usage.model_calls += 1
            if (text := _assistant_text(message)).strip():
                final_text = text
            # pi emits a turn even when the provider call failed, and the stop reason is the
            # only place that says so. Without this the run reads as an ordinary short answer.
            stop = str(message.get("stopReason") or "")
            if stop in {"error", "aborted"}:
                detail = message.get("errorMessage") or message.get("error") or stop
                session_errors.append(json.dumps(detail, ensure_ascii=False)[:300])
        elif kind == "tool_execution_start":
            tool = str(event.get("toolName") or "")
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            if escapes_workspace(tool, args, workspace):
                escapes.append(f"{tool}:{json.dumps(args, ensure_ascii=False)[:200]}")
            step_of_call[str(event.get("toolCallId"))] = len(steps)
            steps.append(
                StepRecord(
                    step_index=len(steps),
                    action="tool",
                    tool=tool,
                    detail=f"pending: {json.dumps(args, ensure_ascii=False)[:200]}",
                )
            )
        elif kind == "tool_execution_end":
            failed = bool(event.get("isError"))
            if failed:
                tool_errors += 1
            index = step_of_call.get(str(event.get("toolCallId")))
            if index is not None:
                status = "error" if failed else "ok"
                args_text = (steps[index].detail or "").split(": ", 1)[-1]
                steps[index].detail = f"{status}: {args_text}"
        elif kind == "error":
            session_errors.append(json.dumps(event.get("error") or event, ensure_ascii=False)[:300])

    return {
        "usage": usage,
        "steps": steps,
        "final_text": final_text,
        "tool_errors": tool_errors,
        "out_of_workspace_attempts": escapes,
        "turns": turns,
        "session_errors": session_errors,
    }


def _run_streaming(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout_s: float,
    max_turns: int,
) -> tuple[str, str, int | None, bool, bool]:
    """Run ``command`` to completion, to the turn budget, or to the deadline.

    Returns ``(stdout, stderr, returncode, timed_out, hit_step_limit)``.

    Streaming rather than one ``communicate()`` because the turn budget can only be enforced
    while the run is happening -- pi has no flag for it. Three properties that a single
    ``communicate()`` gave for free have to be kept by hand:

    * **stderr is drained concurrently.** Reading stdout line by line while stderr is an
      undrained pipe deadlocks the child as soon as that buffer fills, and the case then
      files as a wall-clock timeout -- a wrong number reached through a green code path.
    * **The process gets its own session, and the group is killed.** pi's bash tool spawns
      children -- a pytest run, a node process -- and killing only the process it launched
      leaves them writing into the staging directory the adapter is about to delete.
    * **stdin is /dev/null.** pi reads a non-TTY stdin to EOF and folds it into the initial
      message, so the harness's own stdin would become part of the benchmarked task.
    """
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state = {"timed_out": False}
    hit_step_limit = False

    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # pi is Node; a JSON string carrying a lone surrogate reaches this pipe as invalid
        # UTF-8. Strict decoding raised out of the read loop, past the `finally` that only
        # cancels the timer -- measured: the deadline was gone and pi outlived a 3s budget
        # by 61s, still writing into the staging directory the adapter then deletes.
        errors="replace",
        bufsize=1,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    ) as proc:

        def kill() -> None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()

        def on_deadline() -> None:
            state["timed_out"] = True
            kill()

        drain = threading.Thread(
            target=lambda: stderr_chunks.append(proc.stderr.read() if proc.stderr else ""),
            daemon=True,
        )
        drain.start()
        timer = threading.Timer(timeout_s, on_deadline)
        timer.start()
        try:
            turns = 0
            for line in proc.stdout or ():
                stdout_chunks.append(line)
                # Cheap reject before parsing: the stream is mostly `message_update` deltas.
                if '"turn_end"' not in line:
                    continue
                try:
                    if json.loads(line).get("type") != "turn_end":
                        continue
                except json.JSONDecodeError:
                    continue
                turns += 1
                if 0 < max_turns <= turns:
                    hit_step_limit = True
                    kill()
                    break
        except BaseException:
            # Including KeyboardInterrupt: `start_new_session=True` means a terminal Ctrl-C
            # never reaches pi, so without this it and its pytest children are orphaned.
            kill()
            raise
        finally:
            timer.cancel()
        proc.wait()
        drain.join(timeout=10)

    return (
        "".join(stdout_chunks),
        "".join(stderr_chunks),
        proc.returncode,
        state["timed_out"],
        hit_step_limit,
    )


class PiAgent(AgentAdapter):
    """A real coding agent, driven as a subprocess and confined to a mirrored workspace."""

    def __init__(self, agent_config: AgentConfig, model_config: ModelConfig) -> None:
        super().__init__(agent_config, model_config)
        opts = self.agent_config.options
        self.binary = str(opts.get("binary") or "pi")
        self.system_prompt = str(opts.get("system_prompt") or DEFAULT_SYSTEM)
        configured = opts.get("tools")
        self.tools = tuple(configured) if configured else DEFAULT_TOOLS
        self.context_window = int(opts.get("context_window") or DEFAULT_CONTEXT_WINDOW)
        #: Caps the run independently of the run config, for a panel member that must stay
        #: cheap regardless of which run yaml drives it. `is None` rather than `or`, so a
        #: configured `max_steps: 0` reaches the budget rather than being read as "unset" --
        #: where, like any non-positive budget, it means "do not enforce a turn cap".
        self.max_steps_cap = opts.get("max_steps")

    def _version_mismatch(self) -> str | None:
        """Refuse a scaffold version other than the one the configs pin.

        A different scaffold version is a different instrument. pi ships roughly weekly, so
        this is the difference between a comparable number and a number nobody can place.
        """
        expected = self.agent_config.options.get("expected_version")
        if not expected:
            return None
        from aibench.preflight import pi_version

        found = pi_version(self.binary)
        if found is None:
            return f"could not read `{self.binary} --version`; this config pins {expected}"
        if found != str(expected):
            return (
                f"pi {found} is not the pinned {expected}. A different scaffold version is a "
                f"different instrument; set `options.expected_version` in the agent config if "
                f"the change is intended."
            )
        return None

    def _sandbox_refusal(self) -> str | None:
        """Refuse to measure with no filesystem boundary, unless that is asked for explicitly.

        pi documents that it has no sandbox of its own, so there is nothing here to fall back
        to: without ``sandbox-exec`` the agent can read the case's own answer key out of this
        checkout, and two calibration runs on disk show an agent walking to
        ``benchmarks/ai_coding/cases/`` and reading its own case JSON.
        """
        if not bool(self.agent_config.options.get("sandbox", True)):
            return None  # explicitly disabled in the config, which is a recorded choice
        if SANDBOX_EXEC.is_file() or os.environ.get("AIBENCH_ALLOW_UNSANDBOXED") == "1":
            return None
        return (
            "no filesystem boundary available on this platform (sandbox-exec is macOS-only), "
            "and pi has no sandbox of its own, so the agent could read the case's own answer "
            "key. Set AIBENCH_ALLOW_UNSANDBOXED=1 to measure anyway and have the run record "
            "sandboxed=false, or set `options.sandbox: false` in the agent config."
        )

    def _wrap_in_sandbox(self, command: list[str], staging: Path) -> tuple[list[str], bool]:
        """Put a kernel-enforced boundary around the run, and say whether it went on.

        Returns the possibly-wrapped command and whether the boundary was actually applied, so
        a run on a platform without ``sandbox-exec`` records that it had none rather than
        inheriting a guarantee it never got.
        """
        if not bool(self.agent_config.options.get("sandbox", True)):
            return command, False
        if not SANDBOX_EXEC.is_file():
            return command, False
        # The whole checkout, not just this module's subtree: the git object store above
        # it serves the case's gold solution to anyone who asks `git show`.
        protected = protected_root()
        profile = staging / "sandbox.sb"
        profile.write_text(
            sandbox_profile(protected, readable=(repo_root() / ".venv",)), encoding="utf-8"
        )
        return [str(SANDBOX_EXEC), "-f", str(profile), *command], True

    def run(
        self,
        case: Case,
        workspace: Path,
        *,
        max_steps: int,
        max_wall_time_s: float,
    ) -> AgentRunResult:
        t0 = time.perf_counter()
        resolved = resolve_endpoint(self.model_config)
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
                error_message=f"pi binary not found: {self.binary}",
                wall_time_s=time.perf_counter() - t0,
            )
        if mismatch := self._version_mismatch():
            return AgentRunResult(
                status="infra_error",
                error_message=mismatch,
                wall_time_s=time.perf_counter() - t0,
            )
        if refusal := self._sandbox_refusal():
            return AgentRunResult(
                status="infra_error",
                error_message=refusal,
                wall_time_s=time.perf_counter() - t0,
            )
        steps_budget = int(self.max_steps_cap if self.max_steps_cap is not None else max_steps)

        # Resolved, because macOS mkdtemp hands back /var/folders/... for a directory it calls
        # /private/var/folders/..., and a sandbox rule written against the unresolved form
        # matches nothing the kernel ever sees.
        staging = Path(tempfile.mkdtemp(prefix="aibench-pi-")).resolve()
        try:
            checkout = checkout_above(staging)
            if checkout is not None:
                # pi discovers AGENTS.md and CLAUDE.md by walking every ancestor. Inside a
                # checkout it would read this repository's own instructions as context for the
                # benchmarked task -- and `--no-context-files` is one flag away from being the
                # only thing preventing it. Refuse rather than measure that.
                return AgentRunResult(
                    status="infra_error",
                    error_message=(
                        f"staging dir {staging} sits inside the git checkout {checkout}; pi "
                        "walks every ancestor for context files and the workspace boundary "
                        "would not hold. Set TMPDIR outside any checkout."
                    ),
                    wall_time_s=time.perf_counter() - t0,
                )
            staged = staging / "workspace"
            shutil.copytree(workspace, staged, symlinks=True)
            before = snapshot(staged)

            agent_dir = staging / "pi-agent"
            agent_dir.mkdir()
            (agent_dir / "models.json").write_text(
                json.dumps(
                    build_models_json(
                        base_url=base_url,
                        model_name=model_name,
                        max_tokens=self.model_config.max_tokens,
                        temperature=self.model_config.temperature,
                        context_window=self.context_window,
                    ),
                    indent=1,
                ),
                encoding="utf-8",
            )
            (agent_dir / "settings.json").write_text(
                json.dumps(build_settings_json(tools=self.tools), indent=1), encoding="utf-8"
            )

            env = child_environment(agent_dir, api_key=api_key)
            command = [
                self.binary,
                "--provider",
                PROVIDER_ID,
                "--model",
                model_name,
                # Non-interactive: process the prompt and exit. Also suppresses the project
                # trust prompt, which would otherwise hang a headless run forever.
                "--print",
                "--mode",
                "json",
                "--tools",
                ",".join(self.tools),
                "--system-prompt",
                self.system_prompt,
                # Nothing of the operator's, and nothing of the case's, gets to change what pi
                # is. `--no-context-files` is the one that matters for the measurement: pi
                # loads AGENTS.md and CLAUDE.md regardless of project trust otherwise.
                "--no-session",
                "--no-context-files",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-themes",
                "--no-approve",
                "--offline",
                # Ends option parsing: a prompt that begins with a dash is a task, not a flag.
                "--",
                self._task_message(case),
            ]
            command, sandboxed = self._wrap_in_sandbox(command, staging)

            try:
                stdout, stderr, returncode, timed_out, hit_step_limit = _run_streaming(
                    command,
                    env=env,
                    cwd=staged,
                    timeout_s=max_wall_time_s,
                    max_turns=steps_budget,
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
            # Mirrored back even on a kill: a partial edit is what the agent produced, and
            # grading it is the honest answer rather than discarding the attempt.
            mirror_into(staged, workspace)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        usage: UsageRecord = parsed["usage"]
        artifacts = {
            "files_written": written,
            "final_message": parsed["final_text"][:2000],
            "exit_code": returncode,
            "tool_errors": parsed["tool_errors"],
            "out_of_workspace_attempts": parsed["out_of_workspace_attempts"],
            "turns": parsed["turns"],
            # Recorded rather than assumed: a reader of these artifacts must be able to tell a
            # run that was confined from one that merely was not observed leaving.
            "sandboxed": sandboxed,
            "session_errors": parsed["session_errors"],
            "stderr": (stderr or "")[-1000:],
        }
        wall = time.perf_counter() - t0

        # Order matters, and every branch decides whether a row is charged to the model or
        # excluded as infrastructure. Only `infra_error` is excluded and retried (runner.py);
        # everything else is graded and counted, so a misrouted gateway failure would read as
        # weakness in the published rate.
        errors = parsed["session_errors"]
        if timed_out and usage.model_calls == 0:
            # Nothing came back at all. Measured against an unreachable endpoint, pi produced
            # no output and had not exited after five minutes -- indistinguishable from budget
            # exhaustion out here. "Timeout" is only defensible once the gateway answered.
            status, error = "infra_error", "pi produced no model call before the deadline"
        elif timed_out:
            status, error = "timeout", "max_wall_time exceeded"
        elif errors and usage.model_calls == len(errors):
            # Every turn failed at the provider. Checked above the step limit -- a gateway that
            # errors on each turn runs to the budget and is then killed here, which would
            # otherwise file an outage as budget exhaustion, keep it in the denominator, and
            # skip the retry. opencode orders these the same way.
            status, error = (
                "infra_error",
                f"pi reported {len(errors)} session error(s): {errors[0]}",
            )
        elif hit_step_limit:
            # Checked before the exit code: the non-zero code is this adapter's own SIGKILL.
            status, error = "timeout", "max_steps exceeded"
        elif errors:
            # Some turns failed and some did not. pi may have recovered and finished the task;
            # an exit code of 0 is the only evidence of that.
            status = "infra_error" if returncode != 0 else "completed"
            error = f"pi reported {len(errors)} session error(s): {errors[0]}"
        elif returncode != 0 and usage.model_calls == 0:
            # A launch or configuration failure, not an attempt at the task.
            status, error = "infra_error", f"pi exited {returncode} without any model call"
        elif returncode != 0:
            status, error = "failed", f"pi exited {returncode}"
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
