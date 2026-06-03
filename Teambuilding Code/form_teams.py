#!/usr/bin/env python3
"""
form_teams.py

Form diverse project teams from a resolved student CSV.

Constraints
-----------
- Teams have between --min and --max members (defaults 7-10).
- Students in challenge A/B/C/D must form teams within that challenge.
- Students in 'overflow' and 'late entry' (flex students) are either:
    (a) used to top up existing undersized teams, or
    (b) formed into new teams assigned to the challenge with the fewest groups.
- Every student ends up in a challenge A-D team.
- Teams per challenge <= --max-groups.

Levers
------
  --ideal         Target team size (default 8)
  --min           Minimum team size (default 7)
  --max           Maximum team size (default 10)
  --max-groups    Maximum teams per challenge (default 25)
  --w-studyline   Weight for studyline diversity  (default 1.0)
  --w-personality Weight for personality diversity (default 1.0)
  --seed          Random seed for reproducible results (default 42)

Algorithm - Phase 1 (mandatory students)
-----------------------------------------
For each challenge A-D:
  1. Compute k = clamp(round(n/ideal), ceil(n/max), min(floor(n/min), max_groups)).
     If n < min, k=1 and the team will be supplemented from the flex pool.
  2. Distribute n students across k teams, targeting equal sizes, using a
     round-robin draft that picks the highest marginal-diversity-gain student
     for the current team in each round.

Algorithm - Phase 2 (flex students)
-------------------------------------
  1. Fill teams below --ideal size from the flex pool, prioritising challenges
     with the fewest total students (fewest students = greatest need).
  2. Form new teams from remaining flex students, assigning each to the
     challenge currently with the fewest total students (ties broken alphabetically).
  3. Any residual flex students (fewer than --min) are distributed one-by-one
     to the team with the most room, again prioritising fewest-students challenges.

Diversity score and marginal gain
----------------------------------
  diversity(team) = w_sl * |unique studylines| / |team|
                  + w_p  * |unique personalities| / |team|

  marginal_gain(student -> team) = w_sl * (studyline not in team)
                                 + w_p  * (personality not in team)

Usage:
    python form_teams.py students_resolved.csv
    python form_teams.py students_resolved.csv -o teams.csv --ideal 9 --max-groups 22
    python form_teams.py students_resolved.csv --w-studyline 2.0 --w-personality 1.0
"""

import argparse
import csv
import math
import re
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

CHALLENGES = ("A", "B", "C", "D")

# Maps allocation_category values -> challenge letter (or None for flex)
_CAT_TO_CHALLENGE: dict[str, str | None] = {
    "challenge A": "A",
    "challenge B": "B",
    "challenge C": "C",
    "challenge D": "D",
    "overflow":    None,
    "late entry":  None,
}


@dataclass
class Config:
    ideal:       int   = 8
    team_min:    int   = 7
    team_max:    int   = 10
    max_groups:  int   = 25
    w_studyline: float = 1.0
    w_personality: float = 1.0
    seed:        int   = 42

    @property
    def weights(self) -> dict[str, float]:
        return {"studyline": self.w_studyline, "personality": self.w_personality}

    def validate(self) -> None:
        if not (self.team_min <= self.ideal <= self.team_max):
            sys.exit(
                f"--ideal ({self.ideal}) must be between "
                f"--min ({self.team_min}) and --max ({self.team_max})"
            )
        if self.team_min < 1:
            sys.exit("--min must be >= 1")
        if self.max_groups < 1:
            sys.exit("--max-groups must be >= 1")


# ---------------------------------------------------------------------------
# Diversity helpers
# ---------------------------------------------------------------------------

def marginal_gain(student: dict, team: list[dict], weights: dict) -> float:
    """Weighted count of attributes the student would bring new to the team."""
    sl_set = {s["studyline"]       for s in team}
    p_set  = {s["personality_type"] for s in team}
    sl_new = student["studyline"]        not in sl_set
    p_new  = student["personality_type"] not in p_set
    return weights["studyline"] * sl_new + weights["personality"] * p_new


def diversity_score(team: list[dict], weights: dict) -> float:
    if not team:
        return 0.0
    n  = len(team)
    sl = len({s["studyline"]        for s in team})
    p  = len({s["personality_type"] for s in team})
    return weights["studyline"] * sl / n + weights["personality"] * p / n


# ---------------------------------------------------------------------------
# Core assignment
# ---------------------------------------------------------------------------

def compute_k(n: int, cfg: Config) -> int:
    """Number of teams to form for n mandatory students in one challenge."""
    if n == 0:
        return 0
    if n < cfg.team_min:
        return 1  # one undersized team; will be supplemented from flex pool
    k_lo = math.ceil(n / cfg.team_max)          # minimum teams so nobody exceeds max
    k_hi = min(n // cfg.team_min, cfg.max_groups) # maximum teams respecting min size
    k_target = round(n / cfg.ideal)
    return max(k_lo, min(k_hi, k_target))


def assign_to_k_teams(
    students: list[dict],
    k: int,
    cfg: Config,
    rng: random.Random,
) -> list[list[dict]]:
    """
    Assign students to exactly k teams, balancing sizes and maximising diversity.

    Uses a round-robin draft: in each round every team picks the student from
    the remaining pool who maximises its marginal diversity gain (tie-broken
    randomly).  The draft order alternates direction each round (snake draft)
    so no team systematically gets first pick.
    """
    if k <= 0 or not students:
        return []

    n = len(students)
    base, extra = divmod(n, k)
    # target_sizes[i] = how many students team i should receive
    target_sizes = [base + (1 if i < extra else 0) for i in range(k)]

    teams: list[list[dict]] = [[] for _ in range(k)]
    pool  = list(students)
    rng.shuffle(pool)

    max_rounds = max(target_sizes) if target_sizes else 0
    for round_num in range(max_rounds):
        # Snake order: forward on even rounds, reversed on odd
        order = list(range(k)) if round_num % 2 == 0 else list(range(k - 1, -1, -1))
        for i in order:
            if len(teams[i]) >= target_sizes[i] or not pool:
                continue
            # Pick student with highest marginal gain; random tie-break
            best_idx = max(
                range(len(pool)),
                key=lambda j: (
                    marginal_gain(pool[j], teams[i], cfg.weights),
                    rng.random(),
                ),
            )
            teams[i].append(pool.pop(best_idx))

    # Safety: place any remaining students (rounding edge cases)
    for s in pool:
        smallest = min(range(k), key=lambda i: len(teams[i]))
        teams[smallest].append(s)

    return teams


# ---------------------------------------------------------------------------
# Flex-student placement
# ---------------------------------------------------------------------------

def _challenge_by_fewest_students(
    all_teams: dict[str, list[list[dict]]],
    exclude_at_max: bool,
    cfg: Config,
) -> str | None:
    """Return the challenge letter with the fewest total students."""
    eligible = [
        ch for ch in CHALLENGES
        if not exclude_at_max or len(all_teams[ch]) < cfg.max_groups
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda ch: (sum(len(t) for t in all_teams[ch]), ch))


def place_flex_students(
    flex_pool: list[dict],
    all_teams: dict[str, list[list[dict]]],
    cfg: Config,
    rng: random.Random,
) -> None:
    """
    Assign overflow and late-entry students to challenge teams in-place.

    Mutates all_teams.
    """
    if not flex_pool:
        return

    rng.shuffle(flex_pool)

    # -----------------------------------------------------------------------
    # Phase 2a: top up under-ideal teams, challenges with fewer groups first
    # -----------------------------------------------------------------------
    challenges_sorted = sorted(CHALLENGES, key=lambda ch: (sum(len(t) for t in all_teams[ch]), ch))
    for ch in challenges_sorted:
        for team in all_teams[ch]:
            while len(team) < cfg.ideal and flex_pool:
                best_idx = max(
                    range(len(flex_pool)),
                    key=lambda j: (
                        marginal_gain(flex_pool[j], team, cfg.weights),
                        rng.random(),
                    ),
                )
                team.append(flex_pool.pop(best_idx))

    if not flex_pool:
        return

    # -----------------------------------------------------------------------
    # Phase 2b: form new teams from remaining flex students
    # -----------------------------------------------------------------------
    while flex_pool:
        target_ch = _challenge_by_fewest_students(all_teams, exclude_at_max=True, cfg=cfg)
        if target_ch is None:
            # All challenges at max_groups - fall through to phase 2c
            break

        if len(flex_pool) < cfg.team_min:
            # Too few for a full new team - fall through to phase 2c
            break

        # Take ideal students for the new team; if the remainder would be
        # stranded (< team_min), pull them into this team too (up to team_max).
        take = cfg.ideal
        leftover = len(flex_pool) - take
        if 0 < leftover < cfg.team_min:
            take = min(len(flex_pool), cfg.team_max)

        batch = flex_pool[:take]
        flex_pool = flex_pool[take:]

        # Within the batch, still use the diversity-maximising assignment
        new_team = assign_to_k_teams(batch, 1, cfg, rng)[0]
        all_teams[target_ch].append(new_team)

    if not flex_pool:
        return

    # -----------------------------------------------------------------------
    # Phase 2c: distribute residual students (< team_min) one by one
    # into existing teams that still have room, fewest-groups challenge first
    # -----------------------------------------------------------------------
    for s in flex_pool:
        placed = False
        for ch in sorted(CHALLENGES, key=lambda c: (sum(len(t) for t in all_teams[c]), c)):
            for team in sorted(all_teams[ch], key=len):
                if len(team) < cfg.team_max:
                    team.append(s)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            print(
                f"WARNING: could not place {s['student_number']} "
                f"- all teams are at maximum size {cfg.team_max}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_students(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_teams(
    all_teams: dict[str, list[list[dict]]],
    out_path: Path,
    summary_path: Path | None = None,
) -> None:
    """Write team assignments CSV and optional summary CSV."""
    fieldnames = [
        "team_id", "challenge", "student_number", "dtu_username",
        "email_student_number", "id_source", "student_name", "original_category",
        "studyline", "personality_type",
    ]
    rows = []
    for ch in CHALLENGES:
        for t_idx, team in enumerate(all_teams[ch], start=1):
            tid = f"{ch}{t_idx:02d}"
            for student in team:
                rows.append({
                    "team_id":              tid,
                    "challenge":            ch,
                    "student_number":       student["student_number"],
                    "dtu_username":         student.get("dtu_username", ""),
                    "email_student_number": student.get("email_student_number", ""),
                    "id_source":            student.get("id_source", ""),
                    "student_name":         student.get("student_name", ""),
                    "original_category":    student.get("allocation_category", ""),
                    "studyline":            student["studyline"],
                    "personality_type":     student["personality_type"],
                })

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    if summary_path:
        _write_summary(all_teams, summary_path)


def collect_nonstandard(
    all_teams: dict[str, list[list[dict]]],
) -> tuple[list[dict], list[dict]]:
    """
    Return (resolvable, unresolvable) lists of non-standard identifier cases.

    resolvable   — student_number is sXXXXXX but a non-standard dtu_username also
                   exists; the sXXXXXX is already the correct output value, but the
                   overseer should see and confirm the mapping.
    unresolvable — student_number is not sXXXXXX; no guaranteed student number is
                   available; overseer must confirm the fallback or enter one manually.

    Each entry includes a 'source' field explaining where the proposed value came from.
    """
    resolvable: list[dict] = []
    unresolvable: list[dict] = []
    for ch in CHALLENGES:
        for t_idx, team in enumerate(all_teams[ch], start=1):
            tid = f"{ch}{t_idx:02d}"
            for s in team:
                sid      = s["student_number"]
                dtu_u    = s.get("dtu_username", "")
                email_n  = s.get("email_student_number", "")
                name     = s.get("student_name", "")
                id_src   = s.get("id_source", "")
                standard = bool(re.fullmatch(r"s\d+", sid))
                entry = {
                    "name":         name,
                    "pipeline_id":  sid,
                    "dtu_username": dtu_u,
                    "team_id":      tid,
                    "challenge":    ch,
                }
                if not standard:
                    entry["proposed"]   = email_n if email_n else sid
                    entry["q1_answer"]  = s.get("q1_answer", "")
                    _id_badge_map = {
                        "export:email":            "export_email",
                        "export:username":         "export_username",
                        "survey:late-entry":       "survey_other",
                        "survey:in-classlist":     "survey_other",
                        "survey:not-in-classlist": "survey_dropped",
                        "survey:no-classlist":     "survey_unverified",
                        "survey:unresolvable":     "survey_unverified",
                    }
                    entry["id_badge_key"]       = _id_badge_map.get(id_src, "export_username")
                    entry["proposed_badge_key"] = "classlist_email" if email_n else entry["id_badge_key"]
                    unresolvable.append(entry)
                elif dtu_u:
                    entry["proposed"]          = sid
                    entry["q1_answer"]         = s.get("q1_answer", "")
                    # classlist_confirmed may be bool (in-memory) or string "True"/"False" (from CSV)
                    classlist_ok               = s.get("classlist_confirmed") in (True, "True")
                    entry["id_badge_key"]      = "export_email"
                    entry["proposed_badge_key"]= "classlist_email" if classlist_ok else "export_email"
                    resolvable.append(entry)
    return resolvable, unresolvable


def write_final_teams(
    all_teams: dict[str, list[list[dict]]],
    out_path: Path,
    resolved_ids: dict[str, str] | None = None,
    id_fallback: str = "username",
) -> list[str]:
    """
    Write Name, Student Number, Group CSV (clean final output).

    resolved_ids maps pipeline student_number -> confirmed value (from interactive mode).
    Returns a list of human-readable resolution decision strings for logging.
    """
    _FALLBACK_VALUES = {"username", "blank", "flag"}
    if id_fallback not in _FALLBACK_VALUES:
        id_fallback = "username"

    decisions: list[str] = []
    rows: list[dict] = []

    for ch in CHALLENGES:
        for t_idx, team in enumerate(all_teams[ch], start=1):
            tid = f"{ch}{t_idx:02d}"
            for s in team:
                sid     = s["student_number"]
                name    = s.get("student_name", "")
                email_n = s.get("email_student_number", "")

                if resolved_ids and sid in resolved_ids:
                    resolved = resolved_ids[sid]
                    decisions.append(f"  {sid} ({name}) -> '{resolved}'  [interactive]")
                elif re.fullmatch(r"s\d+", sid):
                    resolved = sid  # standard — no log entry needed
                elif email_n:
                    resolved = email_n
                    decisions.append(f"  {sid} ({name}) -> '{resolved}'  [email_student_number]")
                elif id_fallback == "blank":
                    resolved = ""
                    decisions.append(f"  {sid} ({name}) -> ''  [blank fallback - no sXXXXXX found]")
                elif id_fallback == "flag":
                    resolved = f"UNRESOLVED:{sid}"
                    decisions.append(f"  {sid} ({name}) -> '{resolved}'  [flagged - no sXXXXXX found]")
                else:  # username (default)
                    resolved = sid
                    decisions.append(f"  {sid} ({name}) -> '{resolved}'  [username fallback - no sXXXXXX found]")

                rows.append({"Name": name, "Student Number": resolved, "Group": tid})

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Name", "Student Number", "Group"])
        writer.writeheader()
        writer.writerows(rows)

    return decisions


def _write_summary(
    all_teams: dict[str, list[list[dict]]],
    path: Path,
) -> None:
    fieldnames = [
        "team_id", "challenge", "size",
        "unique_studylines", "unique_personalities",
        "studyline_diversity", "personality_diversity",
    ]
    rows = []
    dummy_weights = {"studyline": 1.0, "personality": 1.0}
    for ch in CHALLENGES:
        for t_idx, team in enumerate(all_teams[ch], start=1):
            tid = f"{ch}{t_idx:02d}"
            sl = len({s["studyline"]        for s in team})
            p  = len({s["personality_type"] for s in team})
            n  = len(team)
            rows.append({
                "team_id":               tid,
                "challenge":             ch,
                "size":                  n,
                "unique_studylines":     sl,
                "unique_personalities":  p,
                "studyline_diversity":   f"{sl/n:.2f}" if n else "0.00",
                "personality_diversity": f"{p/n:.2f}"  if n else "0.00",
            })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(all_teams: dict[str, list[list[dict]]], cfg: Config) -> None:
    total_students = 0
    total_teams    = 0
    print()
    print("=" * 60)
    print("  TEAM FORMATION SUMMARY")
    print("=" * 60)
    for ch in CHALLENGES:
        teams = all_teams[ch]
        n_students = sum(len(t) for t in teams)
        total_students += n_students
        total_teams    += len(teams)
        sizes = sorted(len(t) for t in teams)
        avg_sl = (
            sum(len({s["studyline"]        for s in t}) / len(t) for t in teams) / len(teams)
            if teams else 0
        )
        avg_p = (
            sum(len({s["personality_type"] for s in t}) / len(t) for t in teams) / len(teams)
            if teams else 0
        )
        print(
            f"  Challenge {ch}:  {len(teams):2d} teams  |  "
            f"{n_students:3d} students  |  "
            f"sizes {min(sizes, default=0)}-{max(sizes, default=0)}  |  "
            f"avg diversity SL={avg_sl:.2f} P={avg_p:.2f}"
        )
    print("-" * 60)
    print(f"  Total:     {total_teams:2d} teams  |  {total_students:3d} students")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(cfg: Config, in_path: Path, out_path: Path, summary_path: Path | None) -> None:
    cfg.validate()
    rng = random.Random(cfg.seed)

    students = load_students(in_path)
    print(f"Loaded {len(students)} students from {in_path.name}")

    challenge_students: dict[str, list[dict]] = defaultdict(list)
    flex_students: list[dict] = []
    skipped: list[dict] = []

    for s in students:
        cat = (s.get("allocation_category") or "").strip()
        ch  = _CAT_TO_CHALLENGE.get(cat)
        if ch is not None:
            challenge_students[ch].append(s)
        elif ch is None and cat in ("overflow", "late entry"):
            flex_students.append(s)
        else:
            print(
                f"WARNING: unknown category '{cat}' for {s['student_number']} - skipped",
                file=sys.stderr,
            )
            skipped.append(s)

    print(f"  Mandatory: A={len(challenge_students['A'])}  "
          f"B={len(challenge_students['B'])}  "
          f"C={len(challenge_students['C'])}  "
          f"D={len(challenge_students['D'])}")
    print(f"  Flex pool: {len(flex_students)} "
          f"(overflow + late entry)")
    if skipped:
        print(f"  Skipped:   {len(skipped)} (unknown category)")

    # Phase 1 - form mandatory teams
    all_teams: dict[str, list[list[dict]]] = {}
    for ch in CHALLENGES:
        students_ch = challenge_students[ch]
        k = compute_k(len(students_ch), cfg)
        teams = assign_to_k_teams(students_ch, k, cfg, rng)
        all_teams[ch] = teams
        sizes = sorted(len(t) for t in teams)
        print(
            f"  Challenge {ch}: {len(students_ch):3d} students -> "
            f"{k:2d} teams  (sizes {min(sizes, default=0)}-{max(sizes, default=0)})"
        )

    # Phase 2 - place flex students
    if flex_students:
        print(f"Placing {len(flex_students)} flex students ...")
        place_flex_students(flex_students, all_teams, cfg, rng)
        for ch in CHALLENGES:
            after = sum(len(t) for t in all_teams[ch])
            print(f"  Challenge {ch}: now {after} students in {len(all_teams[ch])} teams")

    print_summary(all_teams, cfg)

    write_teams(all_teams, out_path, summary_path)
    print(f"Team assignments -> {out_path}")
    if summary_path:
        print(f"Team summary     -> {summary_path}")
    return all_teams


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "input",
        help="Resolved student CSV (output of resolve.py or parse_individual.py)",
    )
    ap.add_argument(
        "-o", "--output",
        default="teams.csv",
        help="Output CSV for team assignments (default: teams.csv)",
    )
    ap.add_argument(
        "--summary",
        metavar="PATH",
        help="Optional path for per-team summary CSV",
    )

    # Levers
    ap.add_argument("--ideal",       type=int,   default=8,    help="Target team size (default 8)")
    ap.add_argument("--min",         type=int,   default=7,    help="Minimum team size (default 7)")
    ap.add_argument("--max",         type=int,   default=10,   help="Maximum team size (default 10)")
    ap.add_argument("--max-groups",  type=int,   default=25,   help="Max teams per challenge (default 25)")
    ap.add_argument("--w-studyline", type=float, default=1.0,  help="Studyline diversity weight (default 1.0)")
    ap.add_argument("--w-personality", type=float, default=1.0, help="Personality diversity weight (default 1.0)")
    ap.add_argument("--seed",        type=int,   default=42,   help="Random seed (default 42)")

    args = ap.parse_args()

    in_path      = Path(args.input)
    out_path     = Path(args.output)
    summary_path = Path(args.summary) if args.summary else None

    if not in_path.exists():
        sys.exit(f"Input file not found: {in_path}")

    cfg = Config(
        ideal        = args.ideal,
        team_min     = args.min,
        team_max     = args.max,
        max_groups   = args.max_groups,
        w_studyline  = args.w_studyline,
        w_personality= args.w_personality,
        seed         = args.seed,
    )

    run(cfg, in_path, out_path, summary_path)


if __name__ == "__main__":
    main()
