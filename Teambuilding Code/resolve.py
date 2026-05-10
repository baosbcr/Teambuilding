#!/usr/bin/env python3
"""
resolve.py

Builds the canonical student list starting from the group-membership export
(ground truth for enrollment) and enriching it with survey data from the
Individual Attempts XLSX files.

One combined pass handles all edge cases:

  A  Student in group export + survey for same challenge          -> happy path
  B  Student in group export + survey with corrected ID           -> ID corrected, happy path
  C  Student in group export + survey for a DIFFERENT challenge   -> --cross-challenge lever
  D  Student in group export + surveys for MULTIPLE challenges    -> export wins, first survey's data
  E  Student in group export, NO survey found                     -> --missing lever
  F1 Student NOT in group export, survey is in Late Entries file  -> always kept
  F2 Student NOT in group export, survey in other file            -> --classlist / --dropped levers
  N1 Two students share a name in group export (different IDs)    -> matched by ID; if ID wrong,
                                                                     name lookup returns both ->
                                                                     warn, no auto-correct

ID normalisation is always applied; canonical IDs from the group export are
used as authoritative keys throughout.

Usage:
    python resolve.py group_export.csv --surveys "Learn Exports/Individual Reports"
    python resolve.py group_export.csv --surveys DIR \\
        --missing overflow --cross-challenge survey-overrules \\
        --classlist classlist.csv --dropped exclude \\
        -o students_combined.csv
"""

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict, Counter
from pathlib import Path

MISSING_MODES         = ("keep", "overflow", "skip")
CROSS_CHALLENGE_MODES = ("survey-wins", "joker", "survey-overrules")
DROPPED_MODES         = ("keep", "exclude")

_GROUP_NAME_MAP: dict[str, str] = {
    "challenge a":        "challenge A",
    "challenge b":        "challenge B",
    "challenge c":        "challenge C",
    "challenge d":        "challenge D",
    "challenge overflow": "overflow",
    "overflow":           "overflow",
    "late entries":       "late entry",
    "late entry":         "late entry",
}


# ---------------------------------------------------------------------------
# ID normalisation
# ---------------------------------------------------------------------------

def normalise_id(raw: str) -> str | None:
    """
    Return a canonical lower-case student ID, or None if the input cannot be
    interpreted as an ID (e.g. it is a full name).
    """
    s = raw.strip()
    if "@" in s:
        s = s.split("@")[0]
    s = s.lower()
    if re.fullmatch(r"\d+", s):
        s = "s" + s
    if re.fullmatch(r"[a-z][a-z0-9]+", s):
        return s
    return None


def _norm_name(name: str) -> str:
    """Lowercase + strip + NFC-normalise for reliable name comparison."""
    return unicodedata.normalize("NFC", name.strip().lower())


def _row_canonical_id(row: dict) -> str | None:
    """
    Best canonical ID for one group-export row.
    Prefers sXXXXXX extracted from email; falls back to username.
    """
    email = (row.get("Email Address") or "").strip()
    if "@" in email:
        local = email.split("@")[0].lower()
        nid = normalise_id(local)
        if nid and re.fullmatch(r"s\d+", nid):
            return nid
    username = (row.get("Username") or "").strip()
    if username:
        return normalise_id(username)
    return None


# ---------------------------------------------------------------------------
# Group export loaders
# ---------------------------------------------------------------------------

def load_group_export(path: Path) -> dict[str, str]:
    """Return {normalised_student_id: allocation_category}."""
    mapping: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            group_raw = (row.get("Group Name") or "").strip().lower()
            category  = _GROUP_NAME_MAP.get(group_raw)
            if category is None:
                continue
            username = (row.get("Username") or "").strip()
            if username:
                nid = normalise_id(username)
                if nid:
                    mapping[nid] = category
            email = (row.get("Email Address") or "").strip()
            if "@" in email:
                local = email.split("@")[0].lower()
                nid = normalise_id(local)
                if nid and re.fullmatch(r"s\d+", nid):
                    mapping[nid] = category
    return mapping


def load_group_export_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_name_lookup(group_export_rows: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """
    Return {normalised_full_name: [(canonical_id, allocation_category), ...]}

    Multiple entries for one name means two students share a name; the caller
    should treat these as ambiguous and skip auto-correction.
    """
    lookup: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in group_export_rows:
        group_raw = (row.get("Group Name") or "").strip().lower()
        category  = _GROUP_NAME_MAP.get(group_raw)
        if category is None:
            continue
        nid = _row_canonical_id(row)
        if nid is None:
            continue
        first = (row.get("First Name") or "").strip()
        last  = (row.get("Last Name")  or "").strip()
        full  = _norm_name(f"{first} {last}")
        if full:
            lookup[full].append((nid, category))
    return dict(lookup)


def load_classlist(path: Path) -> set[str]:
    """
    Return the set of canonical student IDs from a DTU Learn classlist CSV.
    The classlist can use the same column layout as the group export
    (Username, Email Address) or any CSV that _row_canonical_id can parse.
    """
    ids: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            nid = _row_canonical_id(row)
            if nid:
                ids.add(nid)
    print(f"Classlist  : {len(ids)} enrolled students from '{path.name}'")
    return ids


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_student_list(
    group_export_rows: list[dict],
    survey_records: list[dict],
    name_lookup: dict[str, list[tuple[str, str]]],
    classlist_ids: set[str] | None = None,
    cross_challenge: str = "survey-wins",
    missing_mode: str = "keep",
    dropped_mode: str = "keep",
    late_entry_overrules: bool = True,
) -> list[dict]:
    """
    Build the canonical student list, group-export-first.

    Returns one dict per student with fields:
        student_number, student_name, allocation_category,
        studyline, personality_type
    """

    # ------------------------------------------------------------------
    # Step 1: base dict from group export (UNKNOWN survey attributes)
    # ------------------------------------------------------------------
    base: dict[str, dict] = {}
    seen_export: set[str] = set()

    for row in group_export_rows:
        group_raw = (row.get("Group Name") or "").strip().lower()
        category  = _GROUP_NAME_MAP.get(group_raw)
        if category is None:
            continue
        nid = _row_canonical_id(row)
        if nid is None:
            print(
                f"WARNING [group export]: no extractable ID for "
                f"'{row.get('First Name','')} {row.get('Last Name','')}' - skipped",
                file=sys.stderr,
            )
            continue
        if nid in seen_export:
            continue
        seen_export.add(nid)
        first = (row.get("First Name") or "").strip()
        last  = (row.get("Last Name")  or "").strip()
        base[nid] = {
            "student_number":      nid,
            "student_name":        f"{first} {last}".strip(),
            "allocation_category": category,
            "studyline":           "UNKNOWN",
            "personality_type":    "UNKNOWN",
            "_survey_found":       False,
        }

    # ------------------------------------------------------------------
    # Step 2: resolve canonical IDs in survey records, group by ID
    # ------------------------------------------------------------------
    survey_by_id: dict[str, list[dict]] = defaultdict(list)
    unresolvable: list[dict] = []

    for rec in survey_records:
        raw_id    = (rec.get("student_number") or "").strip()
        nid       = normalise_id(raw_id)
        rec       = dict(rec)  # work on a copy

        if nid is None:
            # Try name lookup as fallback
            norm = _norm_name(rec.get("student_name") or "")
            entries = name_lookup.get(norm)
            if not entries:
                print(
                    f"WARNING [{rec.get('student_name','')}]: "
                    f"Q1 '{raw_id}' is not a valid ID and name not in group export",
                    file=sys.stderr,
                )
                unresolvable.append(rec)
                continue
            if len(entries) > 1:
                ids = [e[0] for e in entries]
                print(
                    f"WARNING [{rec.get('student_name','')}]: name matches "
                    f"{len(entries)} students {ids} - cannot auto-correct",
                    file=sys.stderr,
                )
                unresolvable.append(rec)
                continue
            canonical_id, _ = entries[0]
            print(
                f"INFO [{rec.get('student_name','')}]: "
                f"Q1 '{raw_id}' -> '{canonical_id}' via name lookup",
                file=sys.stderr,
            )
            rec["student_number"] = canonical_id
            nid = canonical_id
        else:
            # ID is parseable - check against name lookup for correction
            norm = _norm_name(rec.get("student_name") or "")
            entries = name_lookup.get(norm)
            if entries and len(entries) == 1:
                canonical_id, _ = entries[0]
                if nid != canonical_id:
                    print(
                        f"WARNING [{rec.get('student_name','')}]: "
                        f"Q1 ID '{nid}' corrected to '{canonical_id}'",
                        file=sys.stderr,
                    )
                    rec["student_number"] = canonical_id
                    nid = canonical_id

        survey_by_id[nid].append(rec)

    # ------------------------------------------------------------------
    # Step 3: enrich base entries with survey data (cases A/B/C/D)
    # ------------------------------------------------------------------
    for nid, records in survey_by_id.items():
        if nid not in base:
            continue  # case F - handled in step 5

        base_cat   = base[nid]["allocation_category"]
        same       = [r for r in records if r["allocation_category"] == base_cat]
        diff       = [r for r in records if r["allocation_category"] != base_cat]
        diff_cats  = sorted({r["allocation_category"] for r in diff})

        if same:
            # Cases A/B: survey matches group export challenge
            chosen = same[0]
            base[nid]["studyline"]        = chosen["studyline"]
            base[nid]["personality_type"] = chosen["personality_type"]
            base[nid]["_survey_found"]    = True
            if diff:
                print(
                    f"INFO [{nid}]: also filled surveys in {diff_cats} - "
                    f"group export category '{base_cat}' kept",
                    file=sys.stderr,
                )

        elif len(diff_cats) == 1:
            # Case C: exactly one cross-challenge survey
            survey_cat = diff[0]["allocation_category"]
            chosen     = diff[0]
            base[nid]["_survey_found"] = True

            if survey_cat == "late entry" and late_entry_overrules:
                # Student filled the Late Entries survey: per the course announcement,
                # this means they missed the main deadline and are on the waiting list.
                # Override their group export category regardless of --cross-challenge.
                base[nid]["studyline"]           = chosen["studyline"]
                base[nid]["personality_type"]    = chosen["personality_type"]
                base[nid]["allocation_category"] = "late entry"
                print(
                    f"INFO [{nid}]: survey in 'late entry', export '{base_cat}' - "
                    f"moved to late entry (--late-entry-overrules)",
                    file=sys.stderr,
                )
            elif cross_challenge == "survey-wins":
                base[nid]["studyline"]        = chosen["studyline"]
                base[nid]["personality_type"] = chosen["personality_type"]
                print(
                    f"INFO [{nid}]: survey in '{survey_cat}', export '{base_cat}' - "
                    f"survey data used, category kept (survey-wins)",
                    file=sys.stderr,
                )
            elif cross_challenge == "joker":
                # Keep UNKNOWN attributes; treat as if no useful survey found
                base[nid]["_survey_found"] = False
                print(
                    f"INFO [{nid}]: survey in '{survey_cat}', export '{base_cat}' - "
                    f"treated as no survey (joker)",
                    file=sys.stderr,
                )
            elif cross_challenge == "survey-overrules":
                base[nid]["studyline"]           = chosen["studyline"]
                base[nid]["personality_type"]    = chosen["personality_type"]
                base[nid]["allocation_category"] = survey_cat
                print(
                    f"INFO [{nid}]: survey in '{survey_cat}', export '{base_cat}' - "
                    f"moved to survey challenge (survey-overrules)",
                    file=sys.stderr,
                )

        else:
            # Case D: surveys in multiple different challenges, none matching export
            chosen = diff[0]
            base[nid]["studyline"]        = chosen["studyline"]
            base[nid]["personality_type"] = chosen["personality_type"]
            base[nid]["_survey_found"]    = True
            print(
                f"INFO [{nid}]: surveys in {diff_cats}, none match "
                f"export '{base_cat}' - export wins, data from first survey",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Step 4: apply missing_mode for case E (in export, no survey found)
    # ------------------------------------------------------------------
    final: list[dict] = []
    n_missing = 0

    for nid, record in base.items():
        r            = dict(record)
        survey_found = r.pop("_survey_found")

        if not survey_found:
            n_missing += 1
            if missing_mode == "skip":
                print(
                    f"INFO [missing]: {nid}  {r['student_name']} - excluded",
                    file=sys.stderr,
                )
                continue
            elif missing_mode == "overflow":
                print(
                    f"INFO [missing]: {nid}  {r['student_name']}  "
                    f"({r['allocation_category']} -> overflow)",
                    file=sys.stderr,
                )
                r["allocation_category"] = "overflow"
            else:
                print(
                    f"INFO [missing]: {nid}  {r['student_name']}  "
                    f"({r['allocation_category']} - UNKNOWN)",
                    file=sys.stderr,
                )

        final.append(r)

    if n_missing:
        print(
            f"  {n_missing} students from group export with no survey "
            f"(--missing={missing_mode})",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # Step 5: handle case F (in survey, not in group export)
    # ------------------------------------------------------------------
    n_added = 0
    for nid, records in survey_by_id.items():
        if nid in base:
            continue  # already handled
        rec        = records[0]
        survey_cat = rec["allocation_category"]
        name       = rec.get("student_name", "")

        if survey_cat == "late entry":
            # F1: always keep late entries
            print(
                f"INFO [not in export]: {nid}  {name} - late entry, kept",
                file=sys.stderr,
            )
            final.append({
                "student_number":      nid,
                "student_name":        name,
                "allocation_category": "late entry",
                "studyline":           rec["studyline"],
                "personality_type":    rec["personality_type"],
            })
            n_added += 1

        elif classlist_ids is not None:
            if nid in classlist_ids:
                # Enrolled but not in group export (added after export)
                print(
                    f"INFO [not in export]: {nid}  {name} - in classlist, kept",
                    file=sys.stderr,
                )
                final.append({
                    "student_number":      nid,
                    "student_name":        name,
                    "allocation_category": survey_cat,
                    "studyline":           rec["studyline"],
                    "personality_type":    rec["personality_type"],
                })
                n_added += 1
            else:
                # F2/F3: not in classlist -> likely dropped
                if dropped_mode == "exclude":
                    print(
                        f"INFO [dropped]: {nid}  {name} - not in classlist, excluded",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"INFO [dropped?]: {nid}  {name} - not in classlist, "
                        f"kept (--dropped=keep)",
                        file=sys.stderr,
                    )
                    final.append({
                        "student_number":      nid,
                        "student_name":        name,
                        "allocation_category": survey_cat,
                        "studyline":           rec["studyline"],
                        "personality_type":    rec["personality_type"],
                    })
                    n_added += 1

        else:
            # No classlist provided: keep with survey category (default)
            print(
                f"INFO [not in export]: {nid}  {name}  "
                f"(survey: {survey_cat}) - kept",
                file=sys.stderr,
            )
            final.append({
                "student_number":      nid,
                "student_name":        name,
                "allocation_category": survey_cat,
                "studyline":           rec["studyline"],
                "personality_type":    rec["personality_type"],
            })
            n_added += 1

    # Handle survey records whose ID could not be resolved at all
    for rec in unresolvable:
        name   = rec.get("student_name", "")
        raw_id = rec.get("student_number", name)
        print(
            f"INFO [unresolvable]: '{name}' (Q1: '{raw_id}') - "
            f"kept with survey category",
            file=sys.stderr,
        )
        final.append({
            "student_number":      raw_id,
            "student_name":        name,
            "allocation_category": rec.get("allocation_category", "UNKNOWN"),
            "studyline":           rec.get("studyline",           "UNKNOWN"),
            "personality_type":    rec.get("personality_type",    "UNKNOWN"),
        })

    return final


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("group_export_csv",
                    help="Learn group-membership export CSV (post-deadline snapshot)")
    ap.add_argument("--surveys", metavar="DIR", required=True,
                    help="Folder containing Individual Attempts XLSX files")
    ap.add_argument("-o", "--output", default="students_combined.csv",
                    help="Output path (default: students_combined.csv)")
    ap.add_argument(
        "--missing", choices=MISSING_MODES, default="keep",
        help=(
            "Students in group export who never filled the survey. "
            "'keep' (default): add with UNKNOWN attributes. "
            "'overflow': move to flex pool. "
            "'skip': exclude entirely."
        ),
    )
    ap.add_argument(
        "--cross-challenge", choices=CROSS_CHALLENGE_MODES, default="survey-wins",
        dest="cross_challenge",
        help=(
            "Student filled a survey for a challenge different from their group export. "
            "'survey-wins' (default): use survey data, keep export category. "
            "'joker': treat as no survey (UNKNOWN data, export category). "
            "'survey-overrules': use survey data AND move to survey's challenge."
        ),
    )
    ap.add_argument(
        "--classlist", metavar="CSV",
        help="Latest enrolled-student classlist from DTU Learn. "
             "Used to distinguish dropped students (F2/F3) from enrolled ones.",
    )
    ap.add_argument(
        "--dropped", choices=DROPPED_MODES, default="keep",
        help=(
            "Students with a survey but absent from both group export and classlist. "
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
    args = ap.parse_args()

    export_path  = Path(args.group_export_csv)
    surveys_dir  = Path(args.surveys)
    out_path     = Path(args.output)
    classlist_path = Path(args.classlist) if args.classlist else None

    for p in [export_path, surveys_dir]:
        if not p.exists():
            sys.exit(f"Not found: {p}")
    if classlist_path and not classlist_path.exists():
        sys.exit(f"Classlist not found: {classlist_path}")

    print("Loading group export ...")
    export_rows  = load_group_export_rows(export_path)
    name_lookup  = build_name_lookup(export_rows)
    n_export     = sum(
        1 for r in export_rows
        if _GROUP_NAME_MAP.get((r.get("Group Name") or "").strip().lower())
    )
    print(f"  {n_export} students in recognised groups, "
          f"{len(name_lookup)} distinct names")

    classlist_ids = load_classlist(classlist_path) if classlist_path else None

    import parse_individual as _parse
    print(f"\nLoading surveys from '{surveys_dir}' ...")
    survey_records = _parse.load_all_surveys(surveys_dir)
    print(f"  {len(survey_records)} total survey records")

    print(f"\nBuilding canonical student list ...")
    students = build_student_list(
        group_export_rows    = export_rows,
        survey_records       = survey_records,
        name_lookup          = name_lookup,
        classlist_ids        = classlist_ids,
        cross_challenge      = args.cross_challenge,
        missing_mode         = args.missing,
        dropped_mode         = args.dropped,
        late_entry_overrules = args.late_entry_overrules,
    )

    print(f"\nTotal: {len(students)} students")
    for cat, n in sorted(Counter(s["allocation_category"] for s in students).items()):
        print(f"  {cat}: {n}")

    fieldnames = ["student_number", "student_name", "allocation_category",
                  "studyline", "personality_type"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(students)
    print(f"\nWritten -> {out_path}")


if __name__ == "__main__":
    main()
