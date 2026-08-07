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

import json
import threading
from pathlib import Path
from typing import Any

from aibench.io_util import write_json

#: Sits beside the cases it describes, so moving the directory keeps the resume information.
JOURNAL_NAME = "_progress.jsonl"


class CaseSink:
    """Writes cases as they are produced and remembers which drafts are settled.

    Thread-safe: generation runs on a worker pool, and both the case-id table and the
    written-count cap are shared state that decides whether a paid call happens at all.
    """

    def __init__(self, out_dir: Path, *, max_cases: int, resume: bool = False) -> None:
        self._dir = out_dir
        self._max = max_cases
        self._lock = threading.Lock()
        self._journal = out_dir / JOURNAL_NAME
        self.done: set[str] = set()
        self.written_ids: set[str] = set()
        self.collisions: list[str] = []
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
                # Only count cases still on disk: a resumed run must not believe it wrote
                # something the user has since deleted.
                if cid and (self._dir / f"{cid}.json").is_file():
                    self.written_ids.add(cid)
                    self.written += 1
                    self.resumed += 1

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
        """Write one case now. Returns 'written', 'collision' or 'full'."""
        with self._lock:
            if self.written >= self._max:
                return "full"
            cid = str(case.get("case_id") or "")
            if cid in self.written_ids:
                # The filename is the case_id, so a repeat would silently overwrite its
                # predecessor: a 600-case run once reported 600 and left 575 files.
                self.collisions.append(cid)
                self._append({"draft": draft_name, "status": "collision", "case_id": cid})
                self.done.add(draft_name)
                return "collision"
            write_json(self._dir / f"{cid}.json", case)
            self.written_ids.add(cid)
            self.written += 1
            self.done.add(draft_name)
            self._append({"draft": draft_name, "status": "written", "case_id": cid})
            return "written"
