from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aibench.ablation import run_ablation
from aibench.cases import load_schema_validator, validate_case_set
from aibench.env_config import load_dotenv
from aibench.export_results import export_ablation_csv, export_ablation_xlsx
from aibench.extract.filter_rules import rule_filter_draft
from aibench.extract.generate_case import generate_case_with_llm, heuristic_case_from_draft
from aibench.extract.llm_chat_records import (
    extract_case_drafts_from_db,
    resolve_db_url,
)
from aibench.extract.llm_soft_filter import llm_soft_filter_draft
from aibench.extract.sessions import (
    filter_and_draft,
    load_sessions_from_export,
)
from aibench.extract.snapshot_skeleton import build_snapshots_for_case_set
from aibench.io_util import load_json, write_json
from aibench.promote import promote_cases
from aibench.report import check_summary
from aibench.runner import run_benchmark
from aibench.secrets_scan import scan_case_dir


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
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
    p_val.add_argument("--case-set", type=str, default="auto-v0")

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
    p_db.add_argument("--db-url", type=str, default=None)
    p_db.add_argument("--output-dir", type=Path, required=True)
    p_db.add_argument("--limit", type=int, default=300)
    p_db.add_argument("--max-cases", type=int, default=30)
    p_db.add_argument("--min-messages", type=int, default=3)
    p_db.add_argument("--max-messages", type=int, default=60)
    p_db.add_argument("--all-agents", action="store_true")
    p_db.add_argument("--require-gold", action="store_true")
    p_db.add_argument("--since", type=str, default=None)
    p_db.add_argument("--until", type=str, default=None)
    p_db.add_argument("--export-raw", type=Path, default=None)

    p_fil = sub.add_parser("filter-drafts", help="Rule-filter case drafts (kept vs dropped)")
    p_fil.add_argument("--input-dir", type=Path, required=True)
    p_fil.add_argument("--output-dir", type=Path, required=True)
    p_fil.add_argument("--dropped-dir", type=Path, default=None)
    p_fil.add_argument("--report", type=Path, default=None)
    p_fil.add_argument(
        "--llm-soft",
        action="store_true",
        help="After rules keep, also apply LLM soft filter (requires OPENAI_*)",
    )

    p_gen = sub.add_parser("generate-cases", help="Promote drafts to schema cases")
    p_gen.add_argument("--input-dir", type=Path, required=True)
    p_gen.add_argument("--output-dir", type=Path, required=True)
    p_gen.add_argument(
        "--heuristic-only",
        action="store_true",
        help="Do not call LLM; normalize drafts only",
    )
    p_gen.add_argument("--max-cases", type=int, default=50)
    p_gen.add_argument("--filter", action="store_true", help="Apply rule filter before generate")
    p_gen.add_argument(
        "--secrets-scan",
        action="store_true",
        help="Scan generated cases for secrets and write report next to output",
    )

    p_abl = sub.add_parser("ablation", help="Run agent/model matrix and aggregate tables")
    p_abl.add_argument("--matrix", type=Path, required=True)
    p_abl.add_argument("--output-root", type=Path, default=None)
    p_abl.add_argument("--case-set", type=str, default=None)
    p_abl.add_argument(
        "--allow-weak-grader",
        action="store_true",
        help="Do not strip weak_grader=true cases (default: strip)",
    )
    p_abl.add_argument("--parallel", type=int, default=1, help="Parallel run workers")
    p_abl.add_argument(
        "--baseline-experiment",
        type=str,
        default=None,
        help="Experiment name used as baseline for relative lift",
    )
    p_abl.add_argument(
        "--export-csv",
        action="store_true",
        help="Also write ablation_overview.csv",
    )
    p_abl.add_argument(
        "--export-xlsx",
        action="store_true",
        help="Also write ablation_overview.xlsx (needs openpyxl)",
    )

    p_pro = sub.add_parser(
        "promote",
        help="Human-gated promote candidate cases to published set (e.g. prod-v0)",
    )
    p_pro.add_argument("--from-set", default="auto-v0")
    p_pro.add_argument("--to-set", default="prod-v0")
    p_pro.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="Case id to promote (repeatable). If omitted, all gated candidates.",
    )
    p_pro.add_argument("--allow-non-script", action="store_true")
    p_pro.add_argument("--allow-secrets", action="store_true")
    p_pro.add_argument("--dry-run", action="store_true")
    p_pro.add_argument("--report", type=Path, default=None)

    p_sec = sub.add_parser("secrets-scan", help="Scan a case directory for likely secrets")
    p_sec.add_argument("--case-set", type=str, default=None)
    p_sec.add_argument("--input-dir", type=Path, default=None)
    p_sec.add_argument("--report", type=Path, default=None)

    p_snap = sub.add_parser(
        "snapshot-skeleton",
        help="Materialize snapshots/<case_id>/ from context.files and set workspace.mode=mixed",
    )
    p_snap.add_argument("--case-set", type=str, required=True)

    p_exp = sub.add_parser("export-ablation", help="Export ablation_summary to CSV/XLSX")
    p_exp.add_argument("--ablation-dir", type=Path, required=True)
    p_exp.add_argument("--csv", action="store_true", default=True)
    p_exp.add_argument("--xlsx", action="store_true")

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
            f"tokens={summary['total_tokens']} cost={summary.get('total_cost')}"
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
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        for d in drafts:
            write_json(out / f"{d['case_id']}.json", d)
        if args.export_raw:
            meta = [
                {
                    "case_id": d["case_id"],
                    "source_session_id": d["metadata"].get("source_session_id"),
                    "task_type": d["task_type"],
                    "language": d["language"],
                    "has_gold_code": d["metadata"].get("has_gold_code"),
                    "prompt_preview": d["prompt"][:200],
                }
                for d in drafts
            ]
            write_json(args.export_raw, {"count": len(meta), "items": meta})
        print(f"wrote {len(drafts)} drafts -> {out}")
        return 0

    if args.cmd == "filter-drafts":
        inp: Path = args.input_dir
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        drop_dir = args.dropped_dir
        if drop_dir:
            drop_dir.mkdir(parents=True, exist_ok=True)
        report_rows = []
        kept = dropped = 0
        for path in sorted(inp.glob("*.json")):
            draft = load_json(path)
            dec = rule_filter_draft(draft)
            if dec.keep and args.llm_soft:
                soft = llm_soft_filter_draft(draft)
                row = {
                    "file": path.name,
                    "case_id": draft.get("case_id"),
                    "rule": dec.to_dict(),
                    "llm": soft.to_dict(),
                }
                keep = soft.keep
            else:
                row = {"file": path.name, "case_id": draft.get("case_id"), **dec.to_dict()}
                keep = dec.keep
            report_rows.append(row)
            if keep:
                write_json(out / path.name, draft)
                kept += 1
            else:
                dropped += 1
                if drop_dir:
                    write_json(drop_dir / path.name, {**draft, "_filter": row})
        if args.report:
            write_json(args.report, {"kept": kept, "dropped": dropped, "items": report_rows})
        print(f"filter kept={kept} dropped={dropped} -> {out}")
        return 0

    if args.cmd == "generate-cases":
        inp = args.input_dir
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        validator = load_schema_validator()
        n_ok = 0
        for path in sorted(inp.glob("*.json")):
            if n_ok >= args.max_cases:
                break
            draft = load_json(path)
            if args.filter:
                if not rule_filter_draft(draft).keep:
                    continue
            try:
                if args.heuristic_only:
                    case = heuristic_case_from_draft(draft)
                else:
                    last_err: Exception | None = None
                    case = None
                    for attempt in range(2):
                        try:
                            case = generate_case_with_llm(draft)
                            break
                        except Exception as e:  # noqa: BLE001
                            last_err = e
                            print(f"LLM generate attempt {attempt+1} failed for {path.name}: {e}")
                    if case is None:
                        print(f"fallback heuristic for {path.name}: {last_err}")
                        case = heuristic_case_from_draft(draft)
                errors = sorted(validator.iter_errors(case), key=lambda e: list(e.path))
                if errors:
                    print(f"skip invalid {path.name}: {errors[0].message}")
                    continue
                write_json(out / f"{case['case_id']}.json", case)
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"skip {path.name}: {e}")
        print(f"generated {n_ok} cases -> {out}")
        if args.secrets_scan and n_ok:
            rep = scan_case_dir(out)
            write_json(out / "_secrets_scan.json", rep)
            print(f"secrets_scan findings={rep['finding_count']} clean={rep['clean']}")
        return 0 if n_ok > 0 else 1

    if args.cmd == "ablation":
        abl_dir = run_ablation(
            args.matrix,
            output_root=args.output_root,
            case_set_override=args.case_set,
            allow_weak_grader=args.allow_weak_grader,
            parallel=args.parallel,
            baseline_experiment=args.baseline_experiment,
        )
        print(f"ablation_dir={abl_dir}")
        print(f"report={abl_dir / 'ablation_report.md'}")
        if args.export_csv:
            p = export_ablation_csv(abl_dir)
            print(f"csv={p}")
        if args.export_xlsx:
            try:
                p = export_ablation_xlsx(abl_dir)
                print(f"xlsx={p}")
            except RuntimeError as e:
                print(f"xlsx skipped: {e}")
        return 0

    if args.cmd == "promote":
        report = promote_cases(
            source_set=args.from_set,
            dest_set=args.to_set,
            case_ids=args.case_id,
            require_script=not args.allow_non_script,
            allow_secrets=args.allow_secrets,
            dry_run=args.dry_run,
        )
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["promoted_count"] or args.dry_run else 1

    if args.cmd == "secrets-scan":
        if args.input_dir:
            directory = args.input_dir
        elif args.case_set:
            from aibench.cases import case_set_dir

            directory = case_set_dir(args.case_set)
        else:
            print("provide --case-set or --input-dir")
            return 1
        rep = scan_case_dir(directory)
        if args.report:
            write_json(args.report, rep)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["clean"] else 2

    if args.cmd == "snapshot-skeleton":
        rep = build_snapshots_for_case_set(args.case_set)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "export-ablation":
        if args.csv:
            print(export_ablation_csv(args.ablation_dir))
        if args.xlsx:
            print(export_ablation_xlsx(args.ablation_dir))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
