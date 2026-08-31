"""Materialize a case workspace for reproducible evaluation.

Restoration ladder (most portable → richest environment):

1. **inline**   – files embedded in case JSON (always applied as overlays)
2. **snapshot** – directory or tar/zip bundle next to the case set
3. **git**      – clone a repo at a pinned ref (optional sparse/subdir)
4. **setup**    – optional shell commands after files are laid down

Design goal: evaluation time reconstructs a *minimal but sufficient* scene,
not a bit-for-bit clone of the original developer's machine.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aibench.io_util import repo_root, safe_command
from aibench.models import Case, FileBlob


def assert_disposable(path: Path) -> None:
    """Refuse to wipe anything that is, or contains, the checkout.

    ``materialize_workspace`` starts by deleting its target, and the mirroring in the opencode
    adapter empties its target the same way. Neither ever needs to touch the repository, so a
    caller that hands one of them a repo-root-shaped path is a bug — and an expensive one:
    ``docs/SESSION-2026-08-14.md`` §5.4 records a worktree losing every tracked directory
    except ``benchmarks/`` to something in this shape, never located and never fixed.
    """
    target = path.resolve()
    root = repo_root().resolve()
    if target == root or target in root.parents:
        raise ValueError(f"refusing to wipe {target}: it is or contains the checkout at {root}")


@dataclass
class WorkspaceSpec:
    """How to build the case workspace before the agent runs."""

    mode: str = "inline"  # inline | snapshot | git | mixed
    snapshot_path: str | None = None  # relative to the case-set dir; absolute is refused
    git_url: str | None = None
    git_ref: str | None = None  # commit / tag / branch
    git_subdir: str | None = None  # only keep this subdir of the clone
    git_sparse_paths: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # When true, fail hard if snapshot/git materialization fails.
    strict: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> WorkspaceSpec:
        if not d:
            return cls()
        snap = d.get("snapshot") or {}
        git = d.get("git") or {}
        return cls(
            mode=str(d.get("mode") or "inline"),
            snapshot_path=snap.get("path") or d.get("snapshot_path"),
            git_url=git.get("url") or d.get("git_url"),
            git_ref=git.get("ref") or d.get("git_ref"),
            git_subdir=git.get("subdir") or d.get("git_subdir"),
            git_sparse_paths=list(git.get("sparse_paths") or d.get("git_sparse_paths") or []),
            setup_commands=[
                cmd
                for cmd in (d.get("setup_commands") or [])
                if safe_command(str(cmd), field="workspace.setup_commands")
            ],
            env={str(k): str(v) for k, v in (d.get("env") or {}).items()},
            strict=bool(d.get("strict", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "snapshot": {"path": self.snapshot_path} if self.snapshot_path else None,
            "git": {
                "url": self.git_url,
                "ref": self.git_ref,
                "subdir": self.git_subdir,
                "sparse_paths": self.git_sparse_paths,
            }
            if self.git_url
            else None,
            "setup_commands": self.setup_commands,
            "env": self.env,
            "strict": self.strict,
        }


@dataclass
class MaterializeResult:
    workspace: Path
    sources_applied: list[str]
    setup_logs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "sources_applied": self.sources_applied,
            "setup_logs": self.setup_logs,
            "env": self.env,
            "warnings": self.warnings,
        }


def materialize_workspace(
    case: Case,
    workspace: Path,
    *,
    case_set_dir: Path | None = None,
    allow_network: bool = True,
) -> MaterializeResult:
    """Build workspace for one case.

    Order:
      1. wipe workspace
      2. base layer from snapshot and/or git (if configured)
      3. overlay inline case.files (always wins on path conflicts)
      4. run setup_commands
    """
    sources: list[str] = []
    warnings: list[str] = []
    setup_logs: list[str] = []

    assert_disposable(workspace)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    spec = case.workspace
    mode = (spec.mode or "inline").lower()

    if mode in {"snapshot", "mixed"} and spec.snapshot_path:
        try:
            dropped = _apply_snapshot(spec.snapshot_path, workspace, case_set_dir=case_set_dir)
            if dropped:
                # Silently missing files look like a broken snapshot, not a refusal.
                warnings.append(f"snapshot symlinks not copied: {', '.join(sorted(dropped))}")
            sources.append(f"snapshot:{spec.snapshot_path}")
        except Exception as e:
            msg = f"snapshot failed: {e}"
            if spec.strict and mode == "snapshot":
                raise RuntimeError(msg) from e
            warnings.append(msg)

    if mode in {"git", "mixed"} and spec.git_url:
        if not allow_network:
            msg = "git materialization skipped (allow_network=false)"
            if spec.strict and mode == "git":
                raise RuntimeError(msg)
            warnings.append(msg)
        else:
            try:
                _apply_git(
                    workspace,
                    url=spec.git_url,
                    ref=spec.git_ref,
                    subdir=spec.git_subdir,
                    sparse_paths=spec.git_sparse_paths,
                )
                sources.append(f"git:{spec.git_url}@{spec.git_ref or 'HEAD'}")
            except Exception as e:
                msg = f"git failed: {e}"
                if spec.strict and mode == "git":
                    raise RuntimeError(msg) from e
                warnings.append(msg)

    # Inline overlays — always applied last so case author can pin exact bytes.
    if case.files:
        _apply_inline_files(case.files, workspace)
        sources.append(f"inline:{len(case.files)}_files")

    if not sources:
        warnings.append("workspace has no sources; empty dir created")

    for cmd in spec.setup_commands:
        log = _run_setup(cmd, workspace, env=spec.env)
        setup_logs.append(log)
        sources.append(f"setup:{cmd[:60]}")

    return MaterializeResult(
        workspace=workspace,
        sources_applied=sources,
        setup_logs=setup_logs,
        env=dict(spec.env),
        warnings=warnings,
    )


def _apply_inline_files(files: list[FileBlob], workspace: Path) -> None:
    for fb in files:
        rel = _safe_relpath(fb.path)
        path = workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fb.content, encoding="utf-8")


def _resolve_snapshot(snapshot_path: str, case_set_dir: Path | None) -> Path:
    """Resolve a case-supplied snapshot path inside its own case set, and nowhere else.

    This used to return any absolute path that existed, and fell back to `cwd`. Both let a case
    JSON name a directory on the host — `~/.ssh`, a sibling checkout — which `_apply_snapshot`
    copies into the workspace, from where the tool loop's read tool and the llm_judge grader
    send it to the gateway. The snapshot belongs to the case set that shipped it.
    """
    if case_set_dir is None:
        raise FileNotFoundError(f"snapshot {snapshot_path!r} has no case set to resolve against")
    root = case_set_dir.resolve()
    rel = safe_relpath(snapshot_path)
    candidates = [root / rel, root / "snapshots" / rel]
    for c in candidates:
        if c.exists():
            if not c.resolve().is_relative_to(root):
                raise ValueError(f"snapshot escapes its case set: {snapshot_path}")
            return c
    raise FileNotFoundError(
        f"snapshot not found: {snapshot_path} (searched {[str(x) for x in candidates]})"
    )


def _apply_snapshot(
    snapshot_path: str,
    workspace: Path,
    *,
    case_set_dir: Path | None,
) -> list[str]:
    """Lay a snapshot into the workspace. Returns the names of symlinks it dropped."""
    src = _resolve_snapshot(snapshot_path, case_set_dir)
    skipped: list[str] = []

    def drop_symlinks(directory: str, names: list[str]) -> set[str]:
        """A snapshot's symlinks are dropped, not chased and not rebuilt."""
        links = {n for n in names if Path(directory, n).is_symlink()}
        skipped.extend(str(Path(directory, n).relative_to(src)) for n in sorted(links))
        return links

    if src.is_dir():
        # copy tree contents into workspace
        for item in src.iterdir():
            dest = workspace / item.name
            if item.is_symlink():
                skipped.append(item.name)
                continue
            if item.is_dir():
                # Neither followed nor recreated. Following copies the target's contents in;
                # recreating leaves a live link that a later inline-file write walks out of.
                shutil.copytree(item, dest, dirs_exist_ok=True, ignore=drop_symlinks)
            else:
                shutil.copy2(item, dest, follow_symlinks=False)
        return skipped

    name = src.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(src, "r:gz") as tf:
            _safe_extract_tar(tf, workspace)
        return skipped
    if name.endswith(".tar"):
        with tarfile.open(src, "r:") as tf:
            _safe_extract_tar(tf, workspace)
        return skipped
    if name.endswith(".zip"):
        with zipfile.ZipFile(src, "r") as zf:
            _safe_extract_zip(zf, workspace)
        return skipped
    raise ValueError(f"unsupported snapshot format: {src}")


def safe_relpath(path: str) -> str:
    """Confine a case-supplied path to the workspace.

    Case paths are generated text, so they are untrusted. `workspace / "/home/code/x.py"`
    resolves to the absolute path and writes outside the workspace, which is why this lives in
    one place rather than being re-derived at each call site.
    """
    rel = path.replace("\\", "/").lstrip("/")
    parts = Path(rel).parts
    if ".." in parts:
        raise ValueError(f"path escapes workspace: {path}")
    return str(Path(*parts)) if parts else "unnamed"


_safe_relpath = safe_relpath


def confined_path(base: Path, path: str) -> Path:
    """`safe_relpath`, then resolve, so a symlink cannot carry the join out of `base`.

    `safe_relpath` works on text and cannot see the filesystem. A workspace holding
    `vendor -> /somewhere` turns `vendor/x` into a path outside the workspace while every
    component name stays relative, which made a gold-file comparison a read oracle for the host
    and let a directory symlink hide files from the interference scan.

    `resolve()` is not strict, so this works for a path about to be created: the components that
    exist are resolved and the rest is appended.
    """
    target = (base / safe_relpath(path)).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError(f"path escapes workspace: {path}")
    return target


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract, refusing any member that could write outside ``dest``.

    Checking resolved member paths is not enough on its own. A tar may carry a symlink or a
    hard link whose *target* points outside the destination; the link's own path passes the
    prefix test, and a later member written through it lands wherever the link points. Python
    only started refusing that by default in 3.14, and this project's floor is 3.11.
    """
    dest = dest.resolve()
    for member in tf.getmembers():
        member_path = (dest / member.name).resolve()
        if not member_path.is_relative_to(dest):
            raise RuntimeError(f"tar member escapes workspace: {member.name}")
        if member.issym() or member.islnk():
            target = (member_path.parent / member.linkname).resolve()
            if not target.is_relative_to(dest):
                raise RuntimeError(
                    f"tar member links outside workspace: {member.name} -> {member.linkname}"
                )
    tf.extractall(dest, filter="data")


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for info in zf.infolist():
        member_path = (dest / info.filename).resolve()
        if not member_path.is_relative_to(dest):
            raise RuntimeError(f"zip member escapes workspace: {info.filename}")
    zf.extractall(dest)


def _apply_git(
    workspace: Path,
    *,
    url: str,
    ref: str | None,
    subdir: str | None,
    sparse_paths: list[str],
) -> None:
    tmp = workspace.parent / f".git_clone_{workspace.name}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        clone_cmd = ["git", "clone", "--depth", "1"]
        if ref:
            clone_cmd += ["--branch", ref]
        # Note: --branch works for branch/tag; for raw commit we fetch after.
        clone_cmd += [url, str(tmp / "repo")]
        r = subprocess.run(clone_cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            # fallback: full clone then checkout commit
            shutil.rmtree(tmp / "repo", ignore_errors=True)
            r2 = subprocess.run(
                ["git", "clone", url, str(tmp / "repo")],
                capture_output=True,
                text=True,
                check=False,
            )
            if r2.returncode != 0:
                raise RuntimeError(r.stderr or r2.stderr or "git clone failed")
            if ref:
                r3 = subprocess.run(
                    ["git", "checkout", ref],
                    cwd=tmp / "repo",
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if r3.returncode != 0:
                    raise RuntimeError(r3.stderr or f"git checkout {ref} failed")

        repo_root = tmp / "repo"
        if sparse_paths:
            # best-effort sparse checkout for already cloned repo
            subprocess.run(
                ["git", "sparse-checkout", "init", "--cone"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set", *sparse_paths],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

        src = confined_path(repo_root, subdir) if subdir else repo_root
        if not src.exists():
            raise FileNotFoundError(f"git subdir not found: {subdir}")

        # copy into workspace (exclude .git). Same rule as snapshots: drop links,
        # do not follow them onto the host.
        def drop_symlinks(directory: str, names: list[str]) -> set[str]:
            return {n for n in names if Path(directory, n).is_symlink()}

        for item in src.iterdir():
            if item.name == ".git":
                continue
            dest = workspace / item.name
            if item.is_symlink():
                continue
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True, ignore=drop_symlinks)
            else:
                shutil.copy2(item, dest, follow_symlinks=False)

        # record pin for audit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        pin = (head.stdout or "").strip()
        if pin:
            (workspace / ".aibench_git_pin").write_text(
                f"url={url}\nref={ref or ''}\nresolved={pin}\nsubdir={subdir or ''}\n",
                encoding="utf-8",
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_setup(cmd: str, workspace: Path, *, env: dict[str, str]) -> str:
    from aibench.grading import _grader_env

    # Same boundary as the grader, and for the same reason: `setup_commands` is a case-supplied
    # list that used to reach a shell with the caller's whole environment. It runs *earlier*
    # than the grader — during materialization — so `audit-cases` executed it too.
    argv = safe_command(cmd, field="workspace.setup_commands")
    full_env = _grader_env()
    full_env.update(env)
    try:
        proc = subprocess.run(
            argv,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300,
            env=full_env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"$ {cmd}\n[timeout]"
    out = (proc.stdout or "")[-2000:]
    err = (proc.stderr or "")[-2000:]
    return f"$ {cmd}\nexit={proc.returncode}\n{out}{err}".strip()
