#!/usr/bin/env python3
"""
pipeline.py

End-to-end team formation pipeline.  Runs two steps in sequence:

  1. build   - start from the group-membership export (ground truth), load
               Individual Attempts XLSX files, match survey data to each
               student, handle all edge cases -> students_combined.csv
  2. form    - assign students to diverse teams -> teams.csv

All intermediate files are written to --workdir (default: same folder as
the script).  Only the final team-assignment CSV and optional summary are
written to --output / --summary.

Usage:
    python pipeline.py \\
        --reports  "Learn Exports/Individual Reports" \\
        --groups   "Learn Exports/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv" \\
        --output   teams.csv \\
        --summary  teams_summary.csv

    # Adjust team formation levers:
    python pipeline.py ... --ideal 9 --min 7 --max 10 --max-groups 22 \\
                           --w-studyline 2.0 --w-personality 1.0 --seed 0

    # Cross-challenge survey handling:
    python pipeline.py ... --cross-challenge survey-overrules

    # Identify and exclude dropped students using the current classlist:
    python pipeline.py ... --classlist classlist.csv --dropped exclude

    # Handle students who enrolled but never filled the survey:
    python pipeline.py ... --missing overflow   # move them to flex pool
    python pipeline.py ... --missing skip       # exclude them entirely

    # Skip the build step and use a pre-built student list:
    python pipeline.py ... --skip-build students_combined.csv
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import parse_individual as _parse
import resolve          as _resolve
import form_teams       as _form


def step_build(
    reports_dir: Path,
    group_export: Path,
    workdir: Path,
    classlist: Path | None,
    cross_challenge: str,
    missing_mode: str,
    dropped_mode: str,
    late_entry_overrules: bool = True,
) -> Path:
    """
    Build the canonical student list starting from the group export,
    enriching with survey data from Individual Reports.
    """
    out_path = workdir / "students_combined.csv"

    print(f"\n{'='*60}")
    print(f"  STEP 1: BUILD STUDENT LIST")
    print(f"  cross-challenge={cross_challenge}  missing={missing_mode}  "
          f"dropped={dropped_mode}  late-entry-overrules={late_entry_overrules}")
    print(f"{'='*60}")

    export_rows = _resolve.load_group_export_rows(group_export)
    name_lookup = _resolve.build_name_lookup(export_rows)
    n_in_export = sum(
        1 for r in export_rows
        if _resolve._GROUP_NAME_MAP.get((r.get("Group Name") or "").strip().lower())
    )
    print(f"Group export : {n_in_export} students in recognised groups, "
          f"{len(name_lookup)} distinct names")

    classlist_ids = _resolve.load_classlist(classlist) if classlist else None

    print(f"\nSurvey files in '{reports_dir}':")
    survey_records = _parse.load_all_surveys(reports_dir)
    print(f"  {len(survey_records)} total survey records")

    print(f"\nMatching ...")
    students = _resolve.build_student_list(
        group_export_rows    = export_rows,
        survey_records       = survey_records,
        name_lookup          = name_lookup,
        classlist_ids        = classlist_ids,
        cross_challenge      = cross_challenge,
        missing_mode         = missing_mode,
        dropped_mode         = dropped_mode,
        late_entry_overrules = late_entry_overrules,
    )

    print(f"\nTotal : {len(students)} students")
    for cat, n in sorted(Counter(s["allocation_category"] for s in students).items()):
        print(f"  {cat}: {n}")

    fieldnames = ["student_number", "student_name", "allocation_category",
                  "studyline", "personality_type"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(students)

    print(f"Written -> {out_path}")
    return out_path


def step_form(
    combined_csv: Path,
    cfg: _form.Config,
    out_path: Path,
    summary_path: Path | None,
) -> None:
    """Form diverse teams."""
    print(f"\n{'='*60}")
    print(f"  STEP 2: FORM TEAMS")
    print(f"{'='*60}")
    print(
        f"Settings: ideal={cfg.ideal}  min={cfg.team_min}  max={cfg.team_max}  "
        f"max-groups={cfg.max_groups}  "
        f"w_studyline={cfg.w_studyline}  w_personality={cfg.w_personality}  "
        f"seed={cfg.seed}"
    )
    _form.run(cfg, combined_csv, out_path, summary_path)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --- Input sources ---
    ap.add_argument(
        "--reports",
        default=str(Path(__file__).parent.parent / "Learn Exports" / "Individual Reports"),
        metavar="DIR",
        help="Folder containing Individual Attempts XLSX files "
             "(default: ../Learn Exports/Individual Reports)",
    )
    ap.add_argument(
        "--groups",
        default=None,
        metavar="CSV",
        help="Path to the Learn group-membership export CSV. "
             "If omitted, the script searches ../Learn Exports/Group Exports/ "
             "for the most-recently-modified CSV.",
    )
    ap.add_argument(
        "--classlist",
        default=None,
        metavar="CSV",
        help="Latest enrolled-student classlist exported from DTU Learn. "
             "Used to distinguish dropped students from enrolled ones "
             "when a survey record has no matching group export entry.",
    )

    # --- Output ---
    ap.add_argument(
        "-o", "--output",
        default="teams.csv",
        help="Final team-assignment CSV (default: teams.csv)",
    )
    ap.add_argument(
        "--summary",
        metavar="PATH",
        help="Optional per-team statistics CSV",
    )
    ap.add_argument(
        "--workdir",
        default=str(Path(__file__).parent),
        metavar="DIR",
        help="Directory for intermediate files (default: script directory)",
    )

    # --- Build-step levers ---
    ap.add_argument(
        "--cross-challenge",
        choices=_resolve.CROSS_CHALLENGE_MODES,
        default="survey-wins",
        dest="cross_challenge",
        help=(
            "Student filled a survey for a different challenge than their group export. "
            "'survey-wins' (default): use survey data, keep export category. "
            "'joker': treat as no survey (UNKNOWN data, export category). "
            "'survey-overrules': use survey data AND move to survey's challenge."
        ),
    )
    ap.add_argument(
        "--missing",
        choices=_resolve.MISSING_MODES,
        default="keep",
        help=(
            "Students in group export who never filled the survey. "
            "'keep' (default): add with UNKNOWN attributes in their enrolled group. "
            "'overflow': move to flex pool. "
            "'skip': exclude entirely."
        ),
    )
    ap.add_argument(
        "--dropped",
        choices=_resolve.DROPPED_MODES,
        default="keep",
        help=(
            "Students with a survey but absent from both group export and classlist. "
            "Only relevant when --classlist is provided. "
            "'keep' (default): include with their survey category. "
            "'exclude': remove from output."
        ),
    )
    ap.add_argument(
        "--late-entry-overrules", action=argparse.BooleanOptionalAction, default=True,
        dest="late_entry_overrules",
        help=(
            "Students whose group export shows overflow/challenge X but who filled "
            "the Late Entries survey are moved to 'late entry' category (default: on). "
            "Use --no-late-entry-overrules to keep their group export category instead."
        ),
    )

    # --- Team-formation levers ---
    ap.add_argument("--ideal",         type=int,   default=8,   help="Target team size (default 8)")
    ap.add_argument("--min",           type=int,   default=7,   help="Minimum team size (default 7)")
    ap.add_argument("--max",           type=int,   default=10,  help="Maximum team size (default 10)")
    ap.add_argument("--max-groups",    type=int,   default=25,  help="Max teams per challenge (default 25)")
    ap.add_argument("--w-studyline",   type=float, default=1.0, help="Studyline diversity weight (default 1.0)")
    ap.add_argument("--w-personality", type=float, default=1.0, help="Personality diversity weight (default 1.0)")
    ap.add_argument("--seed",          type=int,   default=42,  help="Random seed (default 42)")

    # --- Control flow ---
    ap.add_argument(
        "--skip-build",
        metavar="CSV",
        help="Skip step 1 and use this pre-built student list CSV instead",
    )

    args = ap.parse_args()

    workdir      = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path     = Path(args.output)
    summary_path = Path(args.summary) if args.summary else None
    classlist    = Path(args.classlist) if args.classlist else None

    cfg = _form.Config(
        ideal         = args.ideal,
        team_min      = args.min,
        team_max      = args.max,
        max_groups    = args.max_groups,
        w_studyline   = args.w_studyline,
        w_personality = args.w_personality,
        seed          = args.seed,
    )
    cfg.validate()

    # --- Step 1: build ---
    if args.skip_build:
        combined_csv = Path(args.skip_build)
        if not combined_csv.exists():
            sys.exit(f"--skip-build file not found: {combined_csv}")
        print(f"Skipping step 1; using {combined_csv}")
    else:
        reports_dir = Path(args.reports)
        if not reports_dir.is_dir():
            sys.exit(f"--reports directory not found: {reports_dir}")

        if args.groups:
            group_export = Path(args.groups)
        else:
            group_dir = Path(__file__).parent.parent / "Learn Exports" / "Group Exports"
            csvs = sorted(group_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not csvs:
                sys.exit(
                    f"No group export CSV found in '{group_dir}'. "
                    "Specify one with --groups."
                )
            group_export = csvs[0]
            print(f"Auto-detected group export: {group_export.name}")

        if not group_export.exists():
            sys.exit(f"--groups file not found: {group_export}")

        combined_csv = step_build(
            reports_dir          = reports_dir,
            group_export         = group_export,
            workdir              = workdir,
            classlist            = classlist,
            cross_challenge      = args.cross_challenge,
            missing_mode         = args.missing,
            dropped_mode         = args.dropped,
            late_entry_overrules = args.late_entry_overrules,
        )

    # --- Step 2: form teams ---
    step_form(combined_csv, cfg, out_path, summary_path)

    print(f"\nDone.  Team assignments written to '{out_path}'.")


if __name__ == "__main__":
    main()
