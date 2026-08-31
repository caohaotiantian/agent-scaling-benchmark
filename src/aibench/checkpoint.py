"""Keep the work a run has already paid for when the run does not finish.

Generation writes nothing until every draft has been through the model. A run killed at
minute ten — by a tool timeout, a dropped connection, a laptop lid — leaves an empty output
directory and bills the same. That happened here: a 10-minute reverse build was cut short and
left nothing on disk, and the whole batch had to be paid for again.

So each case is written the moment it exists, and a line naming the draft it came from is
appended to a journal beside it. A later run reading that journal knows which drafts are
already answered and does not re-ask the model about them. The journal is append-only and
flushed per line, because its whole purpose is to be correct in a process that is about to
die unexpectedly.

Deterministic rejections are journalled too — a draft with no usable before/after pair will
be rejected identically next time, and re-deciding it costs a model call. Transient failures
(a timeout, a 429) are deliberately *not* journalled, so a resumed run retries them.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from aibench.io_util import safe_case_id, write_json

#: Sits beside the cases it describes, so moving the directory keeps the resume information.
JOURNAL_NAME = "_progress.jsonl"


def solution_key(case: dict[str, Any]) -> str | None:
    """Hash of (stub, reference solution) — the pair that decides what a case measures.

    Deliberately *not* the case fingerprint. That hashes the prompt and the tests too, which is
    right for "is this the same case" and wrong for "is this the same defect": reverse
    construction draws its pairs from a trace, and different rows of one session replay the same
    edit. The model then writes a different prompt and different tests for each, so every copy
    gets a distinct fingerprint and `duplicate_fingerprint` sees nothing.

    Measured on `_rev2026`: **134 cases collapse to 66 unique pairs — 68 redundant, 51%**. That
    inflates n and correlates paired outcomes, which is exactly what a McNemar test assumes away.

    Returns ``None`` when the case has no stub or no reference solution to key on; the caller
    treats that as "cannot deduplicate", never as "no duplicates".
    """
    context = (case.get("context") or {}).get("files") or []
    impls = sorted(
        (str(f.get("path") or ""), str(f.get("content") or ""))
        for f in context
        if (f.get("role") or "impl") == "impl"
    )
    gold = sorted(
        (str(g.get("path") or ""), str(g.get("content") or ""))
        for g in (case.get("grader") or {}).get("gold_files") or []
    )
    if not impls or not gold:
        return None
    basis = json.dumps([impls, gold], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class CaseSink:
    """Writes cases as they are produced and remembers which drafts are settled.

    Thread-safe: generation runs on a worker pool, and both the case-id table and the
    written-count cap are shared state that decides whether a paid call happens at all.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        max_cases: int,
        resume: bool = False,
        deduplicate: bool = True,
    ) -> None:
        self._dir = out_dir
        self._max = max_cases
        self._lock = threading.Lock()
        self._journal = out_dir / JOURNAL_NAME
        self._deduplicate = deduplicate
        self.done: set[str] = set()
        self.written_ids: set[str] = set()
        self.written_keys: dict[str, str] = {}
        self.collisions: list[str] = []
        self.duplicates: list[str] = []
        self.written = 0
        self.resumed = 0
        if resume:
            self._load()

    def _load(self) -> None:
        """Read the journal of a previous run. A truncated final line is expected and ignored."""
        if not self._journal.is_file():
            return
        for line in self._journal.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # The last line of a killed run can be half-written. Everything before it is
                # still good, and discarding the batch over one torn line would defeat the point.
                continue
            draft = str(row.get("draft") or "")
            if not draft:
                continue
            self.done.add(draft)
            if row.get("status") == "written":
                cid = str(row.get("case_id") or "")
                # Only count cases still on disk *and* readable: a resumed run must not believe
                # it wrote something the user has since deleted, nor that a file it cannot parse
                # is a finished case. `write_json` is atomic now, so a torn file should no
                # longer occur — but a journal written by an older build still points at ones
                # that do, and the cost of checking is one parse per resumed case.
                if cid and self._is_complete(cid):
                    self.written_ids.add(cid)
                    # Recomputed from the case on disk when the journal predates the field.
                    # Trusting the journal alone silently disabled dedup for every run resumed
                    # from an older checkpoint — the runs most likely to hold duplicates.
                    key = str(row.get("solution_key") or "") or (self._key_on_disk(cid) or "")
                    if key:
                        self.written_keys.setdefault(key, cid)
                    self.written += 1
                    self.resumed += 1

    def _key_on_disk(self, case_id: str) -> str | None:
        try:
            return solution_key(json.loads((self._dir / f"{case_id}.json").read_text("utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            return None

    def _is_complete(self, case_id: str) -> bool:
        path = self._dir / f"{case_id}.json"
        if not path.is_file():
            return False
        try:
            return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
        except (OSError, json.JSONDecodeError):
            return False

    def _append(self, row: dict[str, Any]) -> None:
        with self._journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

    def is_full(self) -> bool:
        with self._lock:
            return self.written >= self._max

    def skip_draft(self, draft_name: str) -> bool:
        """Whether a resumed run has already settled this draft."""
        return draft_name in self.done

    def note_skip(self, draft_name: str, reason: str) -> None:
        """Record a rejection that will repeat, so a resumed run does not pay to rediscover it."""
        with self._lock:
            self.done.add(draft_name)
            self._append({"draft": draft_name, "status": "skipped", "reason": reason[:200]})

    def emit(self, draft_name: str, case: dict[str, Any]) -> str:
        """Write one case now. Returns 'written', 'collision', 'duplicate' or 'full'."""
        with self._lock:
            if self.written >= self._max:
                return "full"
            cid = safe_case_id(str(case.get("case_id") or ""))
            if cid in self.written_ids:
                # The filename is the case_id, so a repeat would silently overwrite its
                # predecessor: a 600-case run once reported 600 and left 575 files.
                self.collisions.append(cid)
                self._append({"draft": draft_name, "status": "collision", "case_id": cid})
                self.done.add(draft_name)
                return "collision"
            key = solution_key(case) if self._deduplicate else None
            if key and key in self.written_keys:
                self.duplicates.append(cid)
                self._append(
                    {
                        "draft": draft_name,
                        "status": "duplicate",
                        "case_id": cid,
                        "duplicate_of": self.written_keys[key],
                        "solution_key": key,
                    }
                )
                self.done.add(draft_name)
                return "duplicate"
            write_json(self._dir / f"{cid}.json", case)
            self.written_ids.add(cid)
            if key:
                self.written_keys[key] = cid
            self.written += 1
            self.done.add(draft_name)
            self._append(
                {"draft": draft_name, "status": "written", "case_id": cid, "solution_key": key}
            )
            return "written"
