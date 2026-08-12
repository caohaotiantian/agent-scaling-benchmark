from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aibench.ablation import run_ablation
from aibench.calibrate import (
    DEFAULT_MIN_RPB,
    DEFAULT_P_MAX,
    DEFAULT_P_MIN,
    SelectionPolicy,
    calibrate_case_set,
    load_anchor_panel,
    parse_tier_quota,
    select_cases,
)
from aibench.cases import load_schema_validator, validate_case_set
from aibench.env_config import load_dotenv, openai_settings
from aibench.export_bundle import DEFAULT_MAX_VERBATIM, export_bundle
from aibench.export_results import export_ablation_csv, export_ablation_xlsx
from aibench.extract.filter_rules import rule_filter_draft
from aibench.extract.generate_case import generate_case_with_llm, heuristic_case_from_draft
from aibench.extract.llm_chat_records import (
    extract_case_drafts_from_db,
    resolve_db_url,
)
from aibench.extract.llm_soft_filter import llm_soft_filter_draft
from aibench.extract.reverse_case import (
    chat_json,
    iter_file_versions,
    reverse_case_from_versions,
)
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
from aibench.stats import mcnemar_sample_size, observed_discordance
from aibench.tiers import TIER_ORDER


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
    p_run.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel case workers (default: run-config case_workers or 1)",
    )

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
    p_db.add_argument(
        "--require-edits",
        action="store_true",
        help="Only traces that actually edited code. Reverse construction needs the before and "
        "after of a real fix, and sampling by recency alone yields almost none: 150 recent rows "
        "produced 1 usable pair against 21 with this predicate.",
    )
    p_db.add_argument(
        "--require-usable-pair",
        action="store_true",
        help="Only write drafts reverse construction can build from, by the same predicate "
        "`generate-cases --reverse` applies. Of the 3,312 drafts in the current pool, 153 "
        "qualify — 2,977 carry no before/after pair at all — so --max-cases otherwise buys "
        "material that can never become a case. Off by default because this command also feeds "
        "the forward generator, which needs no pairs.",
    )
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
    p_gen.add_argument("--max-cases", type=int, default=50, help="Maximum cases to write")
    p_gen.add_argument(
        "--oversample",
        type=float,
        default=1.5,
        help="Drafts to attempt per case wanted (default 1.5). About a quarter of drafts are "
        "skipped downstream, so some oversampling is needed to hit --max-cases; every extra "
        "draft is a paid generation, so the factor is explicit rather than baked in.",
    )
    p_gen.add_argument("--filter", action="store_true", help="Apply rule filter before generate")
    p_gen.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel generation workers (LLM/heuristic)",
    )
    p_gen.add_argument(
        "--secrets-scan",
        action="store_true",
        help="Scan generated cases for secrets and write report next to output",
    )
    p_gen.add_argument(
        "--audit",
        action="store_true",
        help="Run validity audit and annotate metadata after generate",
    )
    p_gen.add_argument(
        "--tier",
        type=str,
        default=None,
        choices=list(TIER_ORDER),
        help="Force a target tier for every draft (default: the tier its trace suggests)",
    )
    p_gen.add_argument(
        "--reverse",
        action="store_true",
        help="Reverse-construct: the stub is the file as the trace found it and the reference "
        "solution is the file as the trace left it, so the defect is a real one. The model only "
        "writes the tests, which the stub-fail and solvability gates then verify. Needs drafts "
        "carrying metadata.file_versions (extract-from-db --require-edits).",
    )
    p_gen.add_argument(
        "--resume",
        action="store_true",
        help="Continue a run that did not finish: drafts already written or already rejected "
        "for a reason that will repeat are not sent to the model again.",
    )
    p_gen.add_argument(
        "--min-tier",
        type=str,
        default=None,
        choices=list(TIER_ORDER),
        help="Drop generated cases that settle below this tier",
    )

    p_exp = sub.add_parser(
        "export-bundle",
        help="Export a shareable case bundle outside the repo, with provenance gated by machine",
    )
    p_exp.add_argument("--from-set", type=str, required=True)
    p_exp.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Any path, deliberately not a case-set name: the bundle is meant to leave the "
        "repository rather than sit one `git add` away from its history",
    )
    p_exp.add_argument(
        "--allow-production-derived",
        action="store_true",
        help="Include reverse-constructed cases, which carry source from real repositories "
        "verbatim (measured 0%%-91.2%% over the 31 shipped cases). Every other gate still "
        "applies — this records that shipping production source is intended, and the MANIFEST "
        "names each source and its measured share. Note that the verbatim gate never applies to "
        "reverse-constructed cases in the first place, so four gates bear on them, not five.",
    )
    p_exp.add_argument(
        "--drafts-dir",
        type=Path,
        default=None,
        help="The private drafts this set was generated from. Every case is checked line by "
        # Percent signs are doubled because argparse runs this through %-formatting; a bare
        # one makes `export-bundle -h` die with "not enough arguments for format string".
        "line against them; over a 575-case build the LLM path overlapped 1.7%% and the "
        "heuristic fallback 100%%, because it deep-copies the draft.",
    )
    p_exp.add_argument("--max-verbatim", type=float, default=DEFAULT_MAX_VERBATIM)
    p_exp.add_argument(
        "--no-require-audit",
        action="store_true",
        help="Export cases whose audit did not pass (default: audit must pass)",
    )
    p_exp.add_argument("--dry-run", action="store_true")

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
    p_pro.add_argument(
        "--require-audit",
        action="store_true",
        help="Require validity audit ok (stub-fail + no contamination)",
    )
    p_pro.add_argument("--dry-run", action="store_true")
    p_pro.add_argument("--report", type=Path, default=None)

    p_aud = sub.add_parser(
        "audit-cases",
        help="Scientific validity audit: stub-fail, contamination, difficulty, dedup",
    )
    p_aud.add_argument("--case-set", type=str, required=True)
    p_aud.add_argument("--report", type=Path, default=None)
    p_aud.add_argument(
        "--annotate",
        action="store_true",
        help="Write difficulty/fingerprint/validity into case metadata",
    )
    p_aud.add_argument(
        "--llm-disclosure-check",
        action="store_true",
        help="Second-pass LLM review for paraphrased defect disclosure the patterns miss "
        "(warn only; costs one call per non-T1 case)",
    )
    p_aud.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit 2 if any case fails error-level gates",
    )

    p_sec = sub.add_parser("secrets-scan", help="Scan a case directory for likely secrets")
    p_sec.add_argument("--case-set", type=str, default=None)
    p_sec.add_argument("--input-dir", type=Path, default=None)
    p_sec.add_argument("--report", type=Path, default=None)

    p_snap = sub.add_parser(
        "snapshot-skeleton",
        help="Materialize snapshots/<case_id>/ from context.files and set workspace.mode=mixed",
    )
    p_snap.add_argument("--case-set", type=str, required=True)

    p_cal = sub.add_parser(
        "calibrate-cases",
        help="Run an anchor panel over a case set and measure each case's discrimination",
    )
    p_cal.add_argument("--case-set", type=str, required=True)
    p_cal.add_argument(
        "--anchors",
        type=Path,
        default=Path("configs/runs/anchor-panel.yaml"),
        help="YAML with an `anchors:` list of {name, agent_config, model_config, run_config}",
    )
    p_cal.add_argument("--repeats", type=int, default=3, help="Independent runs per anchor")
    p_cal.add_argument("--output-root", type=Path, default=None)
    p_cal.add_argument("--workers", type=int, default=None, help="Cases in flight per pass")
    p_cal.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Anchor passes run concurrently. Total concurrency against the gateway is "
        "roughly --parallel x --workers; measured headroom on this gateway is ~16.",
    )
    p_cal.add_argument("--p-max", type=float, default=DEFAULT_P_MAX)
    p_cal.add_argument("--p-min", type=float, default=DEFAULT_P_MIN)
    p_cal.add_argument("--min-rpb", type=float, default=DEFAULT_MIN_RPB)
    p_cal.add_argument(
        "--reuse-from",
        type=Path,
        default=None,
        help="Earlier calibration.json. Cases whose content and anchor panel are unchanged "
        "keep their previous result instead of being re-run.",
    )
    p_cal.add_argument(
        "--allow-unfit-anchors",
        action="store_true",
        help="Calibrate even when a panel member cannot exercise a tier present in the set. "
        "The spread it produces is not a measurement of that capability.",
    )

    p_sel = sub.add_parser(
        "select-cases",
        help="Build a case set from the discriminative cases a calibration kept",
    )
    p_sel.add_argument("--calibration", type=Path, required=True, help="calibration.json")
    p_sel.add_argument("--from-set", type=str, required=True)
    p_sel.add_argument("--to-set", type=str, required=True)
    p_sel.add_argument("--max-cases", type=int, default=None)
    p_sel.add_argument(
        "--tier-quota",
        type=str,
        default=None,
        help="Per-tier share of the selected set, e.g. T2=0.3,T3=0.4,T4=0.3. Without it, "
        "selection ranks purely by discrimination and can land entirely in one tier.",
    )
    p_sel.add_argument(
        "--difficulty-quota",
        type=str,
        default=None,
        help="Per-band share of the selected set by measured p_hat, e.g. "
        "easy=0.15,mid=0.70,hard=0.15. Bands: hard <0.2, mid 0.2-0.8, easy >0.8. "
        "Thresholds alone cannot shape a distribution — they drop the unusable and then rank "
        # Doubled for the same reason as --drafts-dir below: argparse %-formats help text.
        "the rest by discrimination, which is how a selected set still ran 39%% easy. A band "
        "with too few cases is reported as a shortfall, never back-filled from another band.",
    )
    p_sel.add_argument("--dry-run", action="store_true")

    p_plan = sub.add_parser(
        "plan-sample-size",
        help="How many cases a paired comparison needs to detect a given difference",
    )
    p_plan.add_argument(
        "--delta",
        type=float,
        required=True,
        help="Success-rate difference to detect, in percentage points (e.g. 10)",
    )
    p_plan.add_argument(
        "--discordance",
        type=float,
        default=None,
        help="Expected %% of cases the two configs disagree on. Omit with --from-ablation "
        "to measure it from a previous run.",
    )
    p_plan.add_argument(
        "--from-ablation",
        type=Path,
        default=None,
        help="ablation_summary.json to read the observed discordance from",
    )
    p_plan.add_argument("--alpha", type=float, default=0.05)
    p_plan.add_argument("--power", type=float, default=0.8)

    p_comp = sub.add_parser(
        "compose-cases",
        help="Build T4 retrieval cases by planting verified cases among unrelated files",
    )
    p_comp.add_argument("--from-set", type=str, required=True)
    p_comp.add_argument("--to-set", type=str, required=True)
    p_comp.add_argument("--target-files", type=int, default=6, help="Files per composed case")
    p_comp.add_argument("--donors-per-case", type=int, default=3)
    p_comp.add_argument(
        "--donor-set",
        type=str,
        default=None,
        help="Where distractor files come from (default: --from-set). Hosts should be the "
        "cases calibration kept; donors only need to be plausible code, so drawing both "
        "from the curated set starves composition.",
    )
    p_comp.add_argument("--max-cases", type=int, default=None)

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
            case_workers=args.workers,
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
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        written = 0

        def _persist(d: dict[str, Any]) -> None:
            # Written as each draft is built rather than after the whole scan: a pull of
            # several thousand traces runs for many minutes, and a run killed part-way used to
            # leave an empty directory with nothing to show for the time.
            nonlocal written
            write_json(out / f"{d['case_id']}.json", d)
            written += 1
            if written % 100 == 0:
                print(f"  {written} drafts written", flush=True)

        drafts = extract_case_drafts_from_db(
            db_url,
            on_draft=_persist,
            limit=args.limit,
            max_cases=args.max_cases,
            min_messages=args.min_messages,
            max_messages=args.max_messages,
            only_opencode=not args.all_agents,
            require_gold=args.require_gold,
            require_edits=args.require_edits,
            require_usable_pair=args.require_usable_pair,
            since=args.since,
            until=args.until,
        )
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
        from aibench.checkpoint import CaseSink
        from aibench.parallel_util import parallel_map

        inp = args.input_dir
        out = args.output_dir
        out.mkdir(parents=True, exist_ok=True)
        validator = load_schema_validator()
        # `--max-cases` bounds what is WRITTEN; without bounding the slice too, every draft in
        # the directory was generated and paid for and only the first N kept. A 600-case ask
        # against 810 drafts billed all 810. Oversampling is right — about a quarter of drafts
        # are skipped downstream — but the factor has to be explicit and visible.
        attempts = max(int(args.max_cases * args.oversample), args.max_cases)
        paths = sorted(inp.glob("*.json"))[:attempts]
        # `--reverse` rejects a draft before the model is called when it carries no before/after
        # pair a one-file workspace could import, so most of these cost nothing. Saying "every
        # draft is a paid generation" there would overstate the bill by more than an order of
        # magnitude, and this line exists precisely so the bill is not a surprise.
        billing = (
            "only drafts carrying an importable before/after pair are paid generations"
            if args.reverse
            else "every draft here is a paid generation"
        )
        print(
            f"generating from {len(paths)} draft(s) to write at most {args.max_cases} case(s) "
            f"(oversample x{args.oversample}); {billing}",
            flush=True,
        )

        min_tier_rank = TIER_ORDER.index(args.min_tier) if args.min_tier else -1

        sink = CaseSink(out, max_cases=args.max_cases, resume=args.resume)
        if sink.resumed:
            print(
                f"resuming: {sink.resumed} case(s) already written and "
                f"{len(sink.done)} draft(s) already settled; those are not re-generated",
                flush=True,
            )

        reverse_chat = None
        if args.reverse:
            # The extraction predicate lets a draft through when `configs/grading-env.yaml` says
            # its imports resolve. If that promise is unmet, the case is built, paid for, and
            # then fails at grading — where a missing package is indistinguishable from a hard
            # task. Check before the first model call, not after the last.
            from aibench.grading_env import unsatisfied_promises

            if missing := unsatisfied_promises():
                print(
                    "configs/grading-env.yaml promises packages this interpreter cannot import: "
                    f"{', '.join(missing)}.\n"
                    "Install them (`uv sync --extra grading`) or remove them from the manifest. "
                    "Generating against an unmet promise produces cases that fail at grading."
                )
                return 1
            settings = openai_settings()
            if not all((settings["api_key"], settings["base_url"], settings["model"])):
                print("OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL required for --reverse")
                return 1
            reverse_chat = chat_json(settings)

        tier_counts_seen: dict[str, int] = {}

        def _keep(path: Path, case: dict[str, Any], label: str) -> dict[str, Any] | None:
            """Write the case now. A run killed later keeps everything it already paid for."""
            status = sink.emit(path.name, case)
            if status == "full":
                return None
            if status == "collision":
                print(f"skip {path.name}: case_id {case['case_id']} already written", flush=True)
                return None
            settled = (case.get("metadata") or {}).get("tier") or "unset"
            tier_counts_seen[settled] = tier_counts_seen.get(settled, 0) + 1
            print(f"  {label} {case['case_id']} <- {path.name}", flush=True)
            return case

        def _gen_one(path: Path) -> dict[str, Any] | None:
            # Checked before the draft is even read: past --max-cases every further model call
            # is money spent on a case that cannot be written.
            if sink.is_full() or sink.skip_draft(path.name):
                return None
            draft = load_json(path)
            if args.filter and not rule_filter_draft(draft).keep:
                sink.note_skip(path.name, "rule_filter")
                return None
            if args.reverse:
                versions = iter_file_versions(draft)
                if not versions:
                    sink.note_skip(path.name, "no_importable_file_versions")
                    return None
                last: Exception | None = None
                for fv in versions[:2]:
                    try:
                        case = reverse_case_from_versions(fv, draft=draft, chat=reverse_chat)
                    except Exception as e:
                        last = e
                        continue
                    # Same reason the ordinary path prints: a run of minute-long model calls
                    # with no output is indistinguishable from a hung command.
                    return _keep(
                        path, case, f"[reverse] ({fv.get('path')}, {fv.get('edits')} edit(s))"
                    )
                print(f"skip {path.name}: reverse construction failed: {last}", flush=True)
                # Not journalled: the last failure may have been a timeout, and a resumed run
                # should try again rather than inherit a verdict a retry might overturn.
                return None
            try:
                if args.heuristic_only:
                    case = heuristic_case_from_draft(draft, tier=args.tier)
                else:
                    last_err: Exception | None = None
                    case = None
                    for _attempt in range(2):
                        try:
                            case = generate_case_with_llm(draft, tier=args.tier)
                            break
                        except Exception as e:
                            last_err = e
                    if case is None:
                        print(f"fallback heuristic for {path.name}: {last_err}")
                        case = heuristic_case_from_draft(draft, tier=args.tier)
                errors = sorted(validator.iter_errors(case), key=lambda e: list(e.path))
                if errors:
                    print(f"skip invalid {path.name}: {errors[0].message}")
                    sink.note_skip(path.name, "schema")
                    return None
                grader = case.get("grader") or {}
                if grader.get("mode") == "script" and not grader.get("gold_files"):
                    # The LLM path refuses this, but the heuristic fallback cannot invent a
                    # reference solution and was quietly producing what the LLM path had just
                    # rejected: all 21 unverifiable cases in a 126-case build came from it.
                    print(f"skip {path.name}: no reference solution, solvability unverifiable")
                    sink.note_skip(path.name, "no_reference_solution")
                    return None
                settled = (case.get("metadata") or {}).get("tier")
                if min_tier_rank >= 0 and (
                    not settled or TIER_ORDER.index(settled) < min_tier_rank
                ):
                    print(f"skip {path.name}: settled at {settled or 'none'} < {args.min_tier}")
                    sink.note_skip(path.name, "below_min_tier")
                    return None
                # LLM generation can take minutes per draft; without this the command looks hung.
                return _keep(path, case, f"[{settled or 'untiered'}]")
            except Exception as e:
                print(f"skip {path.name}: {e}")
                return None

        parallel_map(_gen_one, paths, workers=args.workers)
        n_ok = sink.written
        tier_counts = dict(sorted(tier_counts_seen.items()))
        collisions = sink.collisions
        print(f"generated {n_ok} cases -> {out}")
        if collisions:
            shown = ", ".join(sorted(set(collisions))[:5])
            print(
                f"WARNING: dropped {len(collisions)} case(s) whose case_id was already taken "
                f"({len(set(collisions))} distinct: {shown}...). The generator produced the "
                "same id for different drafts; the first one written wins."
            )
        if tier_counts:
            print(
                "tier distribution: "
                + ", ".join(f"{k}={v}" for k, v in sorted(tier_counts.items()))
            )
        if n_ok == 0 and args.min_tier:
            print(
                f"Nothing settled at {args.min_tier} or above (see the skip lines). A case only "
                "reaches T3+ when it really carries hidden tests and a reference solution — "
                "lower --min-tier, or drop --heuristic-only so the LLM can produce them."
            )
        if args.secrets_scan and n_ok:
            rep = scan_case_dir(out)
            write_json(out / "_secrets_scan.json", rep)
            print(f"secrets_scan findings={rep['finding_count']} clean={rep['clean']}")
        if args.audit and n_ok:
            from aibench.cases import Case
            from aibench.validity import annotate_case_metadata, audit_case

            # case set name = directory name if under cases/
            set_name = out.name
            for p in sorted(out.glob("*.json")):
                if p.name.startswith("_"):
                    continue
                case_obj = Case.from_dict(load_json(p))
                report = audit_case(case_obj, case_set=set_name)
                annotate_case_metadata(p, report)
            print(f"audited {n_ok} cases")
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
            require_audit=args.require_audit,
            dry_run=args.dry_run,
        )
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["promoted_count"] or args.dry_run else 1

    if args.cmd == "audit-cases":
        from aibench.models import Case
        from aibench.validity import annotate_case_metadata, audit_case, audit_case_set

        rep = audit_case_set(args.case_set, llm_disclosure_check=args.llm_disclosure_check)
        if args.annotate:
            from aibench.cases import case_set_dir

            base = case_set_dir(args.case_set)
            for item in rep.get("reports") or []:
                # rebuild report object for annotate
                p = base / f"{item['case_id']}.json"
                if not p.is_file():
                    # try any file with matching id
                    matches = list(base.glob("*.json"))
                    p = next(
                        (x for x in matches if load_json(x).get("case_id") == item["case_id"]),
                        None,
                    )
                if p and p.is_file():
                    from aibench.validity import CaseValidityReport, ValidityIssue

                    issues = [
                        ValidityIssue(i["code"], i["severity"], i["message"])
                        for i in item.get("issues") or []
                    ]
                    r = CaseValidityReport(
                        case_id=item["case_id"],
                        ok=item["ok"],
                        issues=issues,
                        difficulty=item.get("difficulty"),
                        fingerprint=item.get("fingerprint"),
                        checks=item.get("checks") or {},
                    )
                    annotate_case_metadata(p, r)
        if args.report:
            write_json(args.report, rep)
        print(
            f"audit case_set={args.case_set} passed={rep['passed']}/{rep['total']} "
            f"failed={rep['failed']} "
            f"uncollectable_stub={rep.get('uncollectable_stub', 0)} "
            f"uncollectable_reference={rep.get('uncollectable_reference', 0)} "
            f"fingerprint={rep.get('content_fingerprint')}"
        )
        if args.fail_on_error and rep["failed"] > 0:
            return 2
        return 0

    if args.cmd == "export-bundle":
        try:
            manifest = export_bundle(
                source_set=args.from_set,
                output_dir=args.output_dir,
                drafts_dir=args.drafts_dir,
                max_verbatim=args.max_verbatim,
                require_audit=not args.no_require_audit,
                dry_run=args.dry_run,
                allow_production_derived=args.allow_production_derived,
            )
        except (FileNotFoundError, ValueError) as e:
            print(str(e))
            return 1
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        if not args.drafts_dir:
            print(
                "WARNING: no --drafts-dir, so nothing checked how much of these cases is "
                "copied verbatim from private source material.",
                file=sys.stderr,
            )
        return 0

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

    if args.cmd == "calibrate-cases":
        if not args.anchors.is_file():
            print(
                f"anchor panel not found: {args.anchors}\n"
                "Pass --anchors <file>, or copy configs/runs/anchor-panel.yaml and edit it."
            )
            return 1
        try:
            anchors, _panel = load_anchor_panel(args.anchors)
        except ValueError as e:
            print(str(e))
            return 1
        if validate_case_set(args.case_set):
            print(f"case set {args.case_set!r} is missing or invalid; run validate-cases first")
            return 1
        cal_dir, report = calibrate_case_set(
            args.case_set,
            anchors,
            repeats=args.repeats,
            output_root=args.output_root,
            policy=SelectionPolicy(p_max=args.p_max, p_min=args.p_min, min_rpb=args.min_rpb),
            case_workers=args.workers,
            reuse_from=args.reuse_from,
            parallel=args.parallel,
            allow_unfit_anchors=args.allow_unfit_anchors,
        )
        print(f"calibration_dir={cal_dir}")
        if report.get("reused_case_count"):
            print(
                f"reused={report['reused_case_count']} "
                f"recalibrated={report['recalibrated_case_count']}"
            )
        print(f"report={cal_dir / 'calibration_report.md'}")
        print(
            f"kept={report['kept_count']}/{report['total_cases']} "
            f"p_hat={report['kept_p_hat_distribution']}"
        )
        return 0 if report["kept_count"] else 1

    if args.cmd == "select-cases":
        if not args.calibration.is_file():
            print(
                f"calibration file not found: {args.calibration}\n"
                "Run `aibench calibrate-cases --case-set <set>` first; it writes "
                "runs/calibration_<timestamp>/calibration.json."
            )
            return 1
        try:
            report = select_cases(
                load_json(args.calibration),
                source_set=args.from_set,
                dest_set=args.to_set,
                max_cases=args.max_cases,
                tier_quota=parse_tier_quota(args.tier_quota),
                difficulty_quota=parse_tier_quota(args.difficulty_quota),
                dry_run=args.dry_run,
            )
        except (FileNotFoundError, ValueError) as e:
            print(str(e))
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        dq = report.get("difficulty_quota") or {}
        short = dq.get("shortfall") or {}
        tier_short = dq.get("tier_shortfall") or {}
        if short or tier_short:
            detail = ", ".join(f"{b} short by {n}" for b, n in sorted(short.items()))
            if tier_short:
                detail += f"; tier shortfall per band {tier_short}"
            # stderr, so a shortfall cannot corrupt the JSON on stdout exactly when a
            # consumer most needs to parse it.
            print(
                f"WARNING: the pool could not fill every quota ({detail}). "
                "The set does not have the shape you asked for; calibrate more cases rather "
                "than reading the result as if the target were met.",
                file=sys.stderr,
            )
        if not report["selected_count"]:
            print(
                "\nNo case was selected. Either calibration kept nothing (see its report's "
                "reasons column) or --from-set does not match the calibrated set."
            )
        return 0 if report["selected_count"] else 1

    if args.cmd == "compose-cases":
        from aibench.cases import case_set_dir
        from aibench.compose import compose_case_set, load_verified_cases
        from aibench.extract.tier_shaping import settle_tier

        src = case_set_dir(args.from_set)
        if not src.is_dir():
            print(f"source case set not found: {src}")
            return 1
        pool = None
        if args.donor_set:
            donor_dir = case_set_dir(args.donor_set)
            if not donor_dir.is_dir():
                print(f"donor case set not found: {donor_dir}")
                return 1
            pool = load_verified_cases(donor_dir)
        composed = compose_case_set(
            load_verified_cases(src),
            target_files=args.target_files,
            donors_per_case=args.donors_per_case,
            donor_pool=pool,
        )
        dest = case_set_dir(args.to_set)
        dest.mkdir(parents=True, exist_ok=True)
        schema = load_schema_validator()
        tiers: dict[str, int] = {}
        written = 0
        for case in composed:
            if args.max_cases is not None and written >= args.max_cases:
                break
            settled, _ = settle_tier(case, "T4")
            errors = sorted(schema.iter_errors(case), key=lambda e: list(e.path))
            if errors:
                print(f"skip {case['case_id']}: {errors[0].message}")
                continue
            write_json(dest / f"{case['case_id']}.json", case)
            tiers[settled or "unset"] = tiers.get(settled or "unset", 0) + 1
            written += 1
        print(f"composed {written} cases -> {dest}")
        if tiers:
            print("tier distribution: " + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
        return 0 if written else 1

    if args.cmd == "plan-sample-size":
        discordance = args.discordance / 100.0 if args.discordance is not None else None
        if discordance is None and args.from_ablation:
            observed = observed_discordance(
                load_json(args.from_ablation).get("pairwise_comparisons") or []
            )
            if observed is None:
                print(f"no pairwise comparisons in {args.from_ablation}; pass --discordance")
                return 1
            discordance = observed
            print(f"observed discordance from {args.from_ablation}: {discordance * 100:.1f}%")
        if discordance is None:
            print("provide --discordance PCT, or --from-ablation to measure it")
            return 1
        try:
            plan = mcnemar_sample_size(
                delta=args.delta / 100.0,
                discordance=discordance,
                alpha=args.alpha,
                power=args.power,
            )
        except ValueError as e:
            print(str(e))
            return 1
        print(
            f"To detect a {args.delta:g}pp difference at alpha={args.alpha}, power={args.power}, "
            f"with {discordance * 100:.1f}% discordance:\n"
            f"  required cases          {plan['required_cases']}\n"
            f"  expected discordant     {plan['expected_discordant_pairs']}\n"
            "Only discordant cases carry information. Measure --discordance from a real "
            "ablation (--from-ablation) rather than guessing: it drives the answer as much "
            "as the effect size does."
        )
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
