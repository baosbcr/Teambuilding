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
    python resolve.py group_export.csv --surveys "path/to/Team Formation Survey Individual Attempts"
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

# Roles from the DTU Learn full-classlist export.
# "student" → enrolled student, included in classlist_ids and maps.
# All other known roles are skipped for now; add handling here when needed.
_CLASSLIST_ROLES: dict[str, str] = {
    "student":                 "student",
    "student*":                "skip",   # waitlisted / flagged — not yet enrolled
    "test student":            "skip",   # dummy account, never a real participant
    "teacher":                 "skip",
    "course responsible":      "skip",
    "teaching assistant plus": "skip",
}

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


def _parse_classlist_name(raw: str) -> str:
    """
    Convert classlist 'Last, First [Middle...]' format to 'First [Middle...] Last'
    so it can be normalised consistently with group-export and survey names.
    Falls back to the raw string if no comma is present.
    """
    if "," in raw:
        last, _, first = raw.partition(",")
        return f"{first.strip()} {last.strip()}"
    return raw.strip()


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


def _row_email_number(row: dict) -> str | None:
    """
    Extract sXXXXXX from the email field only — no username fallback.
    Checks both 'Email Address' (group export) and 'Email' (classlist export).
    """
    email = (row.get("Email Address") or row.get("Email") or "").strip()
    if "@" in email:
        local = email.split("@")[0].lower()
        nid = normalise_id(local)
        if nid and re.fullmatch(r"s\d+", nid):
            return nid
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


def load_classlist(path: Path) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """
    Return (classlist_ids, username_number_map, name_number_map).

    classlist_ids        — set of canonical student IDs, for ghost/dropped detection.

    username_number_map  — {non_standard_username: sXXXXXX}
                           Only for rows where the username is non-standard (not s\\d+)
                           but a student number can be recovered.  Fallback enrichment
                           when name lookup fails.

    name_number_map      — {normalised_name: sXXXXXX}
                           Primary enrichment source: Name column (DTU Learn, same
                           source as survey block headers) mapped to student number.
                           Email is the primary source of sXXXXXX; if the email does
                           not yield one (redundancy — never assume it always will),
                           falls back to the username when that is already s\\d+.

    Handles both the group-export column layout (Username, Email Address) and
    the classlist-export layout (UserName, Email).
    """
    ids: set[str] = set()
    username_number_map: dict[str, str] = {}
    name_number_map:     dict[str, str] = {}

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_role   = (row.get("Role") or "").strip().lower()
            role_class = _CLASSLIST_ROLES.get(raw_role, "skip" if raw_role else "student")
            if role_class != "student":
                continue

            # Normalise to a single column layout so helpers work uniformly
            adapted = {
                "Email Address": row.get("Email Address") or row.get("Email", ""),
                "Username":      row.get("Username")      or row.get("UserName", ""),
            }
            nid = _row_canonical_id(adapted)
            if nid:
                ids.add(nid)

            username_id = normalise_id((adapted["Username"] or "").strip())
            email_num   = _row_email_number(adapted)

            # Redundancy: if email doesn't yield sXXXXXX, fall back to username
            # when the username itself is already a standard number.
            student_num = email_num
            if student_num is None and username_id and re.fullmatch(r"s\d+", username_id):
                student_num = username_id

            # username_number_map: non-standard username → recoverable student number
            if username_id and student_num and username_id != student_num:
                username_number_map[username_id] = student_num

            # name_number_map: DTU Learn name → student number
            raw_name = (row.get("Name") or "").strip()
            if raw_name and student_num:
                normalised = _norm_name(_parse_classlist_name(raw_name))
                if normalised:
                    name_number_map[normalised] = student_num

    n_u = len(username_number_map)
    print(f"Classlist  : {len(ids)} enrolled students from '{path.name}', "
          f"{len(name_number_map)} name->number mappings built"
          + (f"  ({n_u} via non-standard username)" if n_u else ""))
    return ids, username_number_map, name_number_map


def validate_classlist_edition(
    classlist_ids: set[str],
    export_rows: list[dict],
) -> None:
    """
    Warn if the classlist looks like it belongs to a different course edition.
    A healthy same-edition classlist should cover >= 80% of group export students.
    """
    group_ids = {_row_canonical_id(r) for r in export_rows}
    group_ids.discard(None)
    if not group_ids:
        return
    overlap = len(group_ids & classlist_ids) / len(group_ids)
    if overlap < 0.5:
        print(
            f"WARNING [classlist]: only {overlap:.0%} of group export students found "
            f"in classlist — classlist may be from a different course edition. "
            f"Ghost detection and dropped-student filtering will be unreliable.",
            file=sys.stderr,
        )
    elif overlap < 0.8:
        print(
            f"NOTE [classlist]: {overlap:.0%} of group export students found in "
            f"classlist (expected ≥80%) — verify this is the correct course export.",
            file=sys.stderr,
        )


def enrich_email_student_numbers(
    students: list[dict],
    username_number_map: dict[str, str],
    name_number_map: dict[str, str],
) -> None:
    """
    In-place: add 'email_student_number' to every student record.

    For students whose student_number is non-standard (not s\\d+):
      1. Try name_number_map first — DTU Learn name matched against classlist
         Name+Email, most robust since both sides come from the same source.
      2. Fall back to username_number_map — username matched against classlist
         email, useful if the name lookup fails (encoding edge cases, etc.).
    Standard s\\d+ students always get an empty string — deliberately skipped
    to avoid surfacing spurious mismatches for students already correctly ID'd.

    When no classlist was provided both maps are empty and all fields are blank.
    """
    for s in students:
        snum = s.get("student_number") or ""
        if not re.fullmatch(r"s\d+", snum):
            name  = _norm_name(s.get("student_name") or "")
            found = name_number_map.get(name) or username_number_map.get(snum)
            s["email_student_number"] = found or ""
            s["classlist_confirmed"]  = bool(found)
        else:
            s["email_student_number"] = ""
            name = _norm_name(s.get("student_name") or "")
            classlist_match = name_number_map.get(name)
            s["classlist_confirmed"]  = (classlist_match == snum)


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
    late_entries: str = "keep",
    overrides: dict[str, str] | None = None,
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

        # Preserve the DTU Learn username when it is non-standard (not s\d+) but
        # the canonical ID was derived from the email instead — that username is
        # otherwise silently discarded.  Stored for informational output only;
        # never used as a matching key.
        raw_username = (row.get("Username") or "").strip()
        username_id  = normalise_id(raw_username) if raw_username else None
        dtu_username = (
            username_id
            if username_id and username_id != nid and not re.fullmatch(r"s\d+", username_id)
            else ""
        )

        # Determine which field the canonical ID was extracted from.
        _email_raw = (row.get("Email Address") or "").strip()
        _email_candidate = None
        if "@" in _email_raw:
            _loc = _email_raw.split("@")[0].lower()
            _c   = normalise_id(_loc)
            if _c and re.fullmatch(r"s\d+", _c):
                _email_candidate = _c
        id_source = "export:email" if _email_candidate == nid else "export:username"

        base[nid] = {
            "student_number":      nid,
            "dtu_username":        dtu_username,
            "student_name":        f"{first} {last}".strip(),
            "allocation_category": category,
            "studyline":           "UNKNOWN",
            "personality_type":    "UNKNOWN",
            "id_source":           id_source,
            "q1_answer":           "",
            "_survey_found":       False,
            "_case_type":          "E",
            "_export_challenge":   category,
            "_survey_challenges":  [],
            "_not_in_classlist":   classlist_ids is not None and nid not in classlist_ids,
        }

    # ------------------------------------------------------------------
    # Step 2: resolve canonical IDs in survey records, group by ID
    # ------------------------------------------------------------------
    survey_by_id: dict[str, list[dict]] = defaultdict(list)
    unresolvable: list[dict] = []

    for rec in survey_records:
        raw_id    = (rec.get("student_number") or "").strip()
        rec       = dict(rec)  # work on a copy
        rec["q1_raw"] = raw_id  # original Q1 answer before any normalisation/correction
        nid       = normalise_id(raw_id)

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

        base[nid]["_survey_challenges"] = sorted({r["allocation_category"] for r in records})

        if same:
            # Cases A/B: survey matches group export challenge
            chosen = same[0]
            base[nid]["studyline"]        = chosen["studyline"]
            base[nid]["personality_type"] = chosen["personality_type"]
            base[nid]["q1_answer"]        = chosen.get("q1_raw", "")
            base[nid]["_survey_found"]    = True
            base[nid]["_case_type"]       = "happy"
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

            export_is_challenge = base_cat.startswith("challenge ")
            if survey_cat == "late entry" and (late_entries == "flex" or not export_is_challenge):
                # flex: move all to late entry regardless of export group
                # keep/discard modes: move non-challenge (overflow) students to late entry
                base[nid]["studyline"]           = chosen["studyline"]
                base[nid]["personality_type"]    = chosen["personality_type"]
                base[nid]["allocation_category"] = "late entry"
                base[nid]["q1_answer"]           = chosen.get("q1_raw", "")
                base[nid]["_case_type"]          = "late-entry-overrules"
                print(
                    f"INFO [{nid}]: survey in 'late entry', export '{base_cat}' - "
                    f"moved to late entry (--late-entries={late_entries})",
                    file=sys.stderr,
                )
            elif survey_cat == "late entry" and export_is_challenge:
                # keep/discard modes: confirmed challenge group stays in their challenge
                base[nid]["studyline"]        = chosen["studyline"]
                base[nid]["personality_type"] = chosen["personality_type"]
                base[nid]["q1_answer"]        = chosen.get("q1_raw", "")
                base[nid]["_case_type"]       = "happy"
                print(
                    f"INFO [{nid}]: survey in 'late entry', export '{base_cat}' - "
                    f"kept in {base_cat} (confirmed challenge group)",
                    file=sys.stderr,
                )
            elif cross_challenge == "survey-wins":
                base[nid]["studyline"]        = chosen["studyline"]
                base[nid]["personality_type"] = chosen["personality_type"]
                base[nid]["q1_answer"]        = chosen.get("q1_raw", "")
                base[nid]["_case_type"]       = "C"
                print(
                    f"INFO [{nid}]: survey in '{survey_cat}', export '{base_cat}' - "
                    f"survey data used, category kept (survey-wins)",
                    file=sys.stderr,
                )
            elif cross_challenge == "joker":
                base[nid]["_survey_found"] = False
                base[nid]["_case_type"]    = "C"
                print(
                    f"INFO [{nid}]: survey in '{survey_cat}', export '{base_cat}' - "
                    f"treated as no survey (joker)",
                    file=sys.stderr,
                )
            elif cross_challenge == "survey-overrules":
                base[nid]["studyline"]           = chosen["studyline"]
                base[nid]["personality_type"]    = chosen["personality_type"]
                base[nid]["allocation_category"] = survey_cat
                base[nid]["q1_answer"]           = chosen.get("q1_raw", "")
                base[nid]["_case_type"]          = "C"
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
            base[nid]["q1_answer"]        = chosen.get("q1_raw", "")
            base[nid]["_survey_found"]    = True
            base[nid]["_case_type"]       = "D"
            print(
                f"INFO [{nid}]: surveys in {diff_cats}, none match "
                f"export '{base_cat}' - export wins, data from first survey",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Step 4: apply missing_mode for case E (in export, no survey found)
    # ------------------------------------------------------------------
    final: list[dict] = []
    n_missing  = 0
    n_dropped_export = 0

    for nid, record in base.items():
        r                = dict(record)
        survey_found     = r.pop("_survey_found")
        not_in_classlist = r.get("_not_in_classlist", False)

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

        if not_in_classlist:
            n_dropped_export += 1
            if dropped_mode == "exclude":
                print(
                    f"INFO [dropped]: {nid}  {r['student_name']} - not in classlist, excluded",
                    file=sys.stderr,
                )
                continue
            else:
                print(
                    f"INFO [not in classlist]: {nid}  {r['student_name']} "
                    f"({r['allocation_category']}) - kept (--dropped=keep)",
                    file=sys.stderr,
                )

        final.append(r)

    if n_missing:
        print(
            f"  {n_missing} students from group export with no survey "
            f"(--missing={missing_mode})",
            file=sys.stderr,
        )
    if n_dropped_export:
        print(
            f"  {n_dropped_export} students from group export not in classlist "
            f"(--dropped={dropped_mode})",
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
            # F1: student only in late entry survey, not in group export
            if late_entries in ("discard-survey-only", "discard-all"):
                print(
                    f"INFO [discarded]: {nid}  {name} - late entry (not in export), "
                    f"excluded (--late-entries={late_entries})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"INFO [not in export]: {nid}  {name} - late entry, kept",
                    file=sys.stderr,
                )
                final.append({
                    "student_number":      nid,
                    "dtu_username":        "",
                    "student_name":        name,
                    "allocation_category": "late entry",
                    "studyline":           rec["studyline"],
                    "personality_type":    rec["personality_type"],
                    "id_source":           "survey:late-entry",
                    "q1_answer":           rec.get("q1_raw", rec.get("student_number", "")),
                    "_case_type":          "F1",
                    "_export_challenge":   None,
                    "_survey_challenges":  [survey_cat],
                })
                n_added += 1

        elif classlist_ids is not None:
            if nid in classlist_ids:
                # F2: enrolled but not in group export (added after export)
                print(
                    f"INFO [not in export]: {nid}  {name} - in classlist, kept",
                    file=sys.stderr,
                )
                final.append({
                    "student_number":      nid,
                    "dtu_username":        "",
                    "student_name":        name,
                    "allocation_category": survey_cat,
                    "studyline":           rec["studyline"],
                    "personality_type":    rec["personality_type"],
                    "id_source":           "survey:in-classlist",
                    "q1_answer":           rec.get("q1_raw", rec.get("student_number", "")),
                    "_case_type":          "F2",
                    "_export_challenge":   None,
                    "_survey_challenges":  [survey_cat],
                })
                n_added += 1
            else:
                # F3: not in classlist -> likely dropped
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
                        "dtu_username":        "",
                        "student_name":        name,
                        "allocation_category": survey_cat,
                        "studyline":           rec["studyline"],
                        "personality_type":    rec["personality_type"],
                        "id_source":           "survey:not-in-classlist",
                        "q1_answer":           rec.get("q1_raw", rec.get("student_number", "")),
                        "_case_type":          "F3",
                        "_export_challenge":   None,
                        "_survey_challenges":  [survey_cat],
                    })
                    n_added += 1

        else:
            # No classlist provided: enrollment status unknown
            print(
                f"INFO [not in export]: {nid}  {name}  "
                f"(survey: {survey_cat}) - kept",
                file=sys.stderr,
            )
            final.append({
                "student_number":      nid,
                "dtu_username":        "",
                "student_name":        name,
                "allocation_category": survey_cat,
                "studyline":           rec["studyline"],
                "personality_type":    rec["personality_type"],
                "id_source":           "survey:no-classlist",
                "q1_answer":           rec.get("q1_raw", rec.get("student_number", "")),
                "_case_type":          "F-no-classlist",
                "_export_challenge":   None,
                "_survey_challenges":  [survey_cat],
            })
            n_added += 1

    # Handle survey records whose ID could not be resolved at all
    for rec in unresolvable:
        name   = rec.get("student_name", "")
        raw_id = rec.get("student_number", name)
        unres_cat = rec.get("allocation_category", "UNKNOWN")
        print(
            f"INFO [unresolvable]: '{name}' (Q1: '{raw_id}') - "
            f"kept with survey category",
            file=sys.stderr,
        )
        final.append({
            "student_number":      raw_id,
            "dtu_username":        "",
            "student_name":        name,
            "allocation_category": unres_cat,
            "studyline":           rec.get("studyline",        "UNKNOWN"),
            "personality_type":    rec.get("personality_type", "UNKNOWN"),
            "id_source":           "survey:unresolvable",
            "q1_answer":           rec.get("q1_raw", rec.get("student_number", "")),
            "_case_type":          "unresolvable",
            "_export_challenge":   None,
            "_survey_challenges":  [unres_cat],
        })

    # Step 5b: discard-all — remove all late entry students from final output
    if late_entries == "discard-all":
        kept, n_discarded = [], 0
        for r in final:
            if r.get("allocation_category") == "late entry":
                print(
                    f"INFO [discarded]: {r['student_number']}  {r['student_name']} - "
                    f"late entry, excluded (--late-entries=discard-all)",
                    file=sys.stderr,
                )
                n_discarded += 1
            else:
                kept.append(r)
        if n_discarded:
            print(
                f"  {n_discarded} late entry student(s) excluded (--late-entries=discard-all)",
                file=sys.stderr,
            )
        final = kept

    # Apply interactive overrides: explicit per-student assignment decisions
    if overrides:
        result = []
        for student in final:
            nid = student["student_number"]
            if nid in overrides:
                assignment = overrides[nid]
                if assignment == "skip":
                    continue
                student = dict(student)
                student["allocation_category"] = assignment
            result.append(student)
        return result

    return final


def flag_ghost_students(final_students: list[dict], classlist_ids: set[str]) -> list[str]:
    """
    Compare the finished student list against the classlist and warn about
    students who are enrolled (classlist) but absent from both the group
    export and all surveys — they would be silently missing from teams.csv.
    Returns sorted list of ghost IDs.
    """
    final_ids = {s["student_number"] for s in final_students}
    ghosts = sorted(classlist_ids - final_ids)
    if ghosts:
        print(
            f"\n  {len(ghosts)} enrolled student(s) in classlist but absent from "
            f"group export and all surveys:",
            file=sys.stderr,
        )
        for gid in ghosts:
            print(
                f"  WARNING [ghost]: {gid} - enrolled but no group or survey found",
                file=sys.stderr,
            )
    return ghosts


def collect_edge_cases(
    group_export_rows: list[dict],
    survey_records: list[dict],
    name_lookup: dict[str, list[tuple[str, str]]],
    classlist_ids: set[str] | None,
    cross_challenge: str = "survey-wins",
    missing_mode: str = "keep",
    dropped_mode: str = "keep",
    late_entries: str = "keep",
    audit_f1: bool = False,
    audit_dropped: bool = False,
    force_audit_ids: list[str] | None = None,
) -> list[dict]:
    """
    Return a list of edge-case dicts for the interactive assignment review page.

    Two-pass approach:
      Pass 1 — build with missing/dropped=keep and cross_challenge=survey-wins so
               every student appears and survey data is always populated for display.
      Pass 2 — build with the real lever settings to derive auto_assignment
               (the value that will be pre-selected in each dropdown).

    Each returned dict contains:
        case_type, student_number, student_name, export_challenge,
        survey_challenges, studyline, personality_type, q1_answer,
        id_source, classlist_confirmed, auto_assignment

    Students in force_audit_ids always appear even if they are on the happy path.
    Unmatched force-audit entries emit WARNING [force-audit] to stderr.
    """
    shared_kwargs = dict(
        group_export_rows = group_export_rows,
        survey_records    = survey_records,
        name_lookup       = name_lookup,
        classlist_ids     = classlist_ids,
        late_entries      = late_entries,
    )

    # Pass 1: ensure all students are present; maximise survey data for display
    all_students = build_student_list(
        **shared_kwargs,
        cross_challenge = "survey-wins",
        missing_mode    = "keep",
        dropped_mode    = "keep",
    )

    # Pass 2: real lever settings → derive auto_assignment per student
    auto_students = build_student_list(
        **shared_kwargs,
        cross_challenge = cross_challenge,
        missing_mode    = missing_mode,
        dropped_mode    = dropped_mode,
    )
    auto_map = {s["student_number"]: s["allocation_category"] for s in auto_students}
    # Students absent from auto_map were excluded by the levers (missing=skip / dropped=exclude)

    # Normalise force-audit IDs: accept student number, bare digits, or email
    force_id_map: dict[str, str] = {}  # normalised_id -> raw input string
    for raw in (force_audit_ids or []):
        nid = normalise_id(raw)
        if nid:
            force_id_map[nid] = raw

    silent_types: set[str] = {"happy"}
    if not audit_f1:
        silent_types.add("F1")

    edge_cases: list[dict] = []
    matched_force_ids: set[str] = set()

    for s in all_students:
        case_type        = s.get("_case_type", "happy")
        nid              = s["student_number"]
        not_in_classlist = s.get("_not_in_classlist", False)
        is_edge          = case_type not in silent_types
        is_forced        = nid in force_id_map
        is_dropped_audit = audit_dropped and not_in_classlist

        if not is_edge and not is_forced and not is_dropped_audit:
            continue

        if is_forced:
            matched_force_ids.add(nid)

        # Assign display case_type for silent students surfaced by audit flags
        if not is_edge:
            if is_dropped_audit:
                case_type = "not-in-classlist"
            elif is_forced:
                case_type = "force-audit"

        edge_cases.append({
            "case_type":          case_type,
            "student_number":     nid,
            "student_name":       s["student_name"],
            "export_challenge":   s.get("_export_challenge"),
            "survey_challenges":  s.get("_survey_challenges", []),
            "studyline":          s.get("studyline",        "UNKNOWN"),
            "personality_type":   s.get("personality_type", "UNKNOWN"),
            "q1_answer":          s.get("q1_answer", ""),
            "id_source":          s.get("id_source", ""),
            "classlist_confirmed": s.get("classlist_confirmed", False),
            "not_in_classlist":   not_in_classlist,
            "auto_assignment":    auto_map.get(nid, "skip"),
        })

    # Warn about force-audit IDs that matched nothing in the data
    all_ids = {s["student_number"] for s in all_students}
    for nid, raw in force_id_map.items():
        if nid not in matched_force_ids and nid not in all_ids:
            print(
                f"WARNING [force-audit]: '{raw}' did not match any student — skipped",
                file=sys.stderr,
            )

    return edge_cases


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
        "--late-entries", default="keep",
        choices=["keep", "flex", "discard-survey-only", "discard-all"],
        dest="late_entries",
        help=(
            "How to handle late entry students. "
            "'keep' (default): overflow + late entry survey → late entry; "
            "challenge-group stays in their group. "
            "'flex': all students with a late entry survey → late entry. "
            "'discard-survey-only': students only in the late entry survey excluded. "
            "'discard-all': all students with a final late entry allocation excluded."
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

    classlist_ids, username_number_map, name_number_map = (
        load_classlist(classlist_path) if classlist_path else (None, {}, {})
    )

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
        late_entries         = args.late_entries,
    )

    print(f"\nTotal: {len(students)} students")
    for cat, n in sorted(Counter(s["allocation_category"] for s in students).items()):
        print(f"  {cat}: {n}")

    enrich_email_student_numbers(students, username_number_map, name_number_map)

    if classlist_ids is not None:
        flag_ghost_students(students, classlist_ids)

    fieldnames = ["student_number", "dtu_username", "email_student_number",
                  "id_source", "classlist_confirmed", "q1_answer",
                  "student_name", "allocation_category", "studyline", "personality_type"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(students)
    print(f"\nWritten -> {out_path}")


if __name__ == "__main__":
    main()
