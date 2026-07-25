from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aibench.cases import validate_case_set
from aibench.extract.llm_chat_records import (
    extract_case_drafts_from_db,
    resolve_db_url,
)
from aibench.extract.sessions import (
    filter_and_draft,
    load_sessions_from_export,
)
from aibench.io_util import load_json, write_json
from aibench.report import check_summary
from aibench.runner import run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aibench", description="AI Coding Assist Benchmark")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run benchmark")
    p_run.add_argument("--run-config", type=Path, default=None)
    p_run.add_argument("--agent", type=Path, default=None, help="Agent config YAML")
    p_run.add_argument("--model", type=Path, default=None, help="Model config YAML")
    p_run.add_argument("--case-set", type=str, default=None)
    p_run.add_argument("--run-id", type=str, default=None)
    p_run.add_argument("--output-root", type=Path, default=None)

    p_val = sub.add_parser("validate-cases", help="Validate a case set against schema")
    p_val.add_argument("--case-set", type=str, default="seed-v0")

    p_sum = sub.add_parser("check-summary", help="Validate summary.json required keys")
    p_sum.add_argument("run_dir", type=Path)

    p_ext = sub.add_parser(
        "extract-cases",
        help="Build case drafts from a normalized session JSON export",
    )
    p_ext.add_argument("--input", type=Path, required=True, help="sessions export JSON")
    p_ext.add_argument("--output-dir", type=Path, required=True)
    p_ext.add_argument("--max-cases", type=int, default=50)

    p_db = sub.add_parser(
        "extract-from-db",
        help="Extract case drafts from MySQL llm_chat_records (URL via AIBENCH_DB_URL)",
    )
    p_db.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="SQLAlchemy URL; default env AIBENCH_DB_URL (do not commit secrets)",
    )
    p_db.add_argument("--output-dir", type=Path, required=True)
    p_db.add_argument("--limit", type=int, default=300, help="Max rows to scan")
    p_db.add_argument("--max-cases", type=int, default=30)
    p_db.add_argument("--min-messages", type=int, default=3)
    p_db.add_argument("--max-messages", type=int, default=60)
    p_db.add_argument(
        "--all-agents",
        action="store_true",
        help="Do not restrict to User-Agent opencode",
    )
    p_db.add_argument(
        "--require-gold",
        action="store_true",
        help="Only keep drafts that extracted assistant code gold",
    )
    p_db.add_argument("--since", type=str, default=None, help="start_time >= (YYYY-MM-DD)")
    p_db.add_argument("--until", type=str, default=None, help="start_time < (YYYY-MM-DD)")
    p_db.add_argument(
        "--export-raw",
        type=Path,
        default=None,
        help="Optional path to also dump lightweight metadata JSON for audit",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run":
        run_dir = run_benchmark(
            run_config_path=args.run_config,
            agent_config_path=args.agent,
            model_config_path=args.model,
            case_set=args.case_set,
            run_id=args.run_id,
            output_root=args.output_root,
        )
        summary = load_json(run_dir / "summary.json")
        print(f"run_dir={run_dir}")
        print(
            f"success_rate={summary['success_rate']:.3f} "
            f"({summary['success_count']}/{summary['effective_case_count']}) "
            f"tokens={summary['total_tokens']}"
        )
        return 0

    if args.cmd == "validate-cases":
        errors = validate_case_set(args.case_set)
        if errors:
            print("INVALID")
            for e in errors:
                print(f" - {e}")
            return 1
        print(f"OK case_set={args.case_set}")
        return 0

    if args.cmd == "check-summary":
        summary_path = args.run_dir / "summary.json"
        if not summary_path.is_file():
            print(f"missing {summary_path}")
            return 1
        problems = check_summary(load_json(summary_path))
        if problems:
            print("INVALID summary")
            for p in problems:
                print(f" - {p}")
            return 1
        print("OK summary")
        return 0

    if args.cmd == "extract-cases":
        raw = load_json(args.input)
        if isinstance(raw, dict) and "sessions" in raw:
            rows = raw["sessions"]
        elif isinstance(raw, list):
            rows = raw
        else:
            print("input must be a list or {sessions: [...]}")
            return 1
        sessions = load_sessions_from_export(rows)
        drafts = filter_and_draft(sessions, max_cases=args.max_cases)
        out: Path = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        for d in drafts:
            write_json(out / f"{d['case_id']}.json", d)
        print(f"wrote {len(drafts)} drafts -> {out}")
        print(
            "NOTE: drafts require human review, desensitization check, "
            "and grader finalization before use as a published case set."
        )
        return 0

    if args.cmd == "extract-from-db":
        try:
            db_url = resolve_db_url(args.db_url)
        except RuntimeError as e:
            print(str(e))
            return 1
        drafts = extract_case_drafts_from_db(
            db_url,
            limit=args.limit,
            max_cases=args.max_cases,
            min_messages=args.min_messages,
            max_messages=args.max_messages,
            only_opencode=not args.all_agents,
            require_gold=args.require_gold,
            since=args.since,
            until=args.until,
        )
        out: Path = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        for d in drafts:
            # Keep drafts out of published case sets by default directory naming
            write_json(out / f"{d['case_id']}.json", d)
        if args.export_raw:
            meta = [
                {
                    "case_id": d["case_id"],
                    "source_session_id": d["metadata"].get("source_session_id"),
                    "task_type": d["task_type"],
                    "language": d["language"],
                    "has_gold_code": d["metadata"].get("has_gold_code"),
                    "has_context_files": d["metadata"].get("has_context_files"),
                    "prompt_preview": d["prompt"][:200],
                    "start_time": d["metadata"].get("start_time"),
                    "source_model": d["metadata"].get("source_model"),
                }
                for d in drafts
            ]
            write_json(args.export_raw, {"count": len(meta), "items": meta})
        print(f"wrote {len(drafts)} drafts -> {out}")
        print(
            "NOTE: drafts require human review, desensitization check, "
            "and grader finalization before use as a published case set."
        )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
