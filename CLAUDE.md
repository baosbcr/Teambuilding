# DTU Teambuilding — Project Context for Claude

## Purpose

Automate formation of diverse project teams for a DTU innovation course. Students self-enrol into one of four challenges (A, B, C, D) plus an Overflow group and a Late Entries group, then fill a survey indicating their studyline and personality type (MBTI). The pipeline reads those survey exports, deduplicates and verifies student records, and assigns students to balanced teams that maximise studyline and personality diversity.

Interfaces: a Flask web app (`app.py`) for browser-based use, and a CLI (`pipeline.py`) for direct execution. Both produce identical output.

---

## Repository Layout

```
Teambuilding/                            <- project root
  CLAUDE.md                              <- this file
  README.md                              <- setup and quick-start guide
  DOCS.md                                <- full reference: all options, edge cases, troubleshooting
  app.py                                 <- Flask web interface (primary entry point)
  requirements.txt                       <- pip dependencies (flask, openpyxl)
  templates/
    index.html                           <- upload form with all levers exposed
  Learn Exports/
    Team Formation Survey Individual Attempts/  <- one XLSX per group (pipeline input)
    Group Exports/                       <- group-membership CSV (pipeline input)
    Classlist Export Students Only/      <- (optional) full classlist CSV from DTU Learn
  Teambuilding Code/                     <- pipeline scripts
    parse_individual.py                  <- XLSX reader utility
    resolve.py                           <- Step 1: group-export-first build + survey match
    form_teams.py                        <- Step 2: team formation algorithm
    pipeline.py                          <- CLI orchestrator (both steps in sequence)
```

---

## Data Sources

### Team Formation Survey Individual Attempts (`Learn Exports/Team Formation Survey Individual Attempts/`)

One XLSX per encompassing group, named after the group (e.g. "Team Formation Survey - Challenge A - Individual Attempts.xlsx"). Each file contains student blocks where:
- The block header row = student's **DTU Learn account name** (authoritative, cannot be wrong)
- Q1 = student's self-reported ID (unreliable — typos, name used instead of ID, wrong format)
- Q2 = studyline selection (single choice)
- Q3 = personality type (MBTI, single choice)

File naming must match a category pattern in `_CATEGORY_PATTERNS` (parse_individual.py) or the pipeline aborts.

### Group Export (`Learn Exports/Group Exports/`)

Single CSV exported post-deadline from DTU Learn. Columns used:
- `Username` — student's Learn username (often the canonical ID, e.g. `s253896`)
- `Email Address` — `sXXXXXX@dtu.dk` format where available (preferred source for canonical ID)
- `First Name`, `Last Name` — student's full name as registered in Learn
- `Group Name` — which challenge group they enrolled in

This file is the **ground truth** for group membership. The pipeline auto-detects the most recently modified CSV in this folder unless `--groups` is specified.

### Classlist Export (`Learn Exports/Classlist Export Students Only/`)

Optional CSV exported from DTU Learn (Classlist → Students tab → Export above the list). Columns used:
- `UserName` — student username (note: capital N, differs from group export's `Username`)
- `Email` — `sXXXXXX@student.dtu.dk` format (differs from group export's `Email Address`)

`load_classlist` handles both column layouts automatically. Enables two features:
- **Ghost detection**: students in classlist but absent from group export and all surveys → `WARNING [ghost]`
- **Dropped-student filtering**: cross-checks survey-only students against classlist for the `--dropped` lever

---

## Script Roles

### `parse_individual.py` — XLSX reader utility

Reads Individual Attempts XLSX files, splits them into per-student blocks, extracts:
- `student_name` (from Learn block header — always reliable)
- `student_number` (from Q1 — may be wrong; resolve.py corrects it)
- `studyline`, `personality_type` (from Q2/Q3)

Key public functions:
- `process_file(path, category)` — processes one file, returns list of student dicts
- `load_all_surveys(survey_dir)` — reads all XLSX in a directory, returns flat list

Called by `resolve.py`; not invoked directly in the production pipeline. Skips Office lock files (`~$` prefix).

### `resolve.py` — Step 1 (combined build)

Group-export-first: the group export is the canonical student list; survey data is an enrichment layer.

**Key function:** `build_student_list(group_export_rows, survey_records, name_lookup, classlist_ids, cross_challenge, missing_mode, dropped_mode)`

Five internal steps:
1. Build base dict from group export keyed by canonical student ID (all UNKNOWN attributes)
2. Resolve canonical IDs in survey records (normalise, correct via name lookup)
3. Enrich base entries with survey data; apply `--cross-challenge` lever for mismatched challenges
4. Apply `--missing` lever for students in group export with no survey found
5. Apply `--dropped` lever for students in survey but absent from group export (and classlist)

**Edge cases handled:**
- **Case A/B**: Survey matches group export challenge (happy path / ID corrected)
- **Case C**: Survey for a single different challenge → `--cross-challenge {survey-wins|joker|survey-overrules}`
- **Case D**: Surveys across multiple different challenges → export wins, data from first survey
- **Case E**: In group export, no survey → `--missing {keep|overflow|skip}`
- **Case F1**: Not in group export, survey from Late Entries file → always kept
- **Case F2/F3**: Not in group export, survey from other file → `--classlist` + `--dropped {keep|exclude}`
- **N1**: Two students share a name (different IDs) → matched by ID; if ID also wrong, warns and skips auto-correct

ID correction is always applied (it is required for correct matching). Warnings are always printed.

**Additional function:** `flag_ghost_students(final_students, classlist_ids)` — called after `build_student_list` when a classlist is provided; diffs classlist IDs against the final output and prints `WARNING [ghost]` for any enrolled student absent from both group export and all surveys.

### `form_teams.py` — Step 2

Greedy team formation with snake-draft assignment.

**Algorithm summary:**
1. For each challenge group, compute `k` teams: `clamp(round(n/ideal), ceil(n/max), min(floor(n/min), max_groups))`
2. Sort students by diversity contribution (most unique attributes first)
3. Assign via snake-draft (alternating direction each round) using marginal gain: `w_studyline*(studyline not in team) + w_personality*(personality not in team)`
4. Place flex students (overflow + late entries) in three phases:
   - Phase 2a: top up under-ideal teams, prioritising challenge with fewest groups
   - Phase 2b: form new flex teams, same priority
   - Phase 2c: distribute remaining students one-by-one into teams with room

### `pipeline.py` — CLI Orchestrator

Runs Steps 1-2 in sequence. Shortcut: `--skip-build CSV` (supply a pre-built student list, skips step 1).

### `app.py` — Flask Web Interface

Browser-based front end wrapping the same pipeline modules. Accepts file uploads (Individual Reports, Group Export, optional classlist), exposes all levers as form fields, and returns a `teams.zip` containing:
- `teams.csv` — one row per student
- `teams_summary.csv` — per-team diversity stats (if requested)
- `run_log.txt` — full pipeline output with a settings header and log message guide

Run locally with `python app.py` (serves on `0.0.0.0:5000`). On the Pi, managed by systemd (`teambuilding.service`) and starts automatically on boot. For production, replace Flask's dev server with a WSGI server (e.g. gunicorn).

---

## Student ID Normalisation

Canonical form: lowercase, no suffix, `s` prefix required for numeric IDs.

| Raw input          | Normalised      | Rule                        |
|--------------------|-----------------|-----------------------------|
| `S253896`          | `s253896`       | uppercase prefix            |
| `253501`           | `s253501`       | missing `s` prefix          |
| `s253422@dtu.dk`   | `s253422`       | strip email suffix          |
| `nipac`            | `nipac`         | non-standard username — OK  |
| `Luna Pacheco`     | `None`          | full name — unresolvable    |

Canonical ID priority: `sXXXXXX` extracted from email > username field.

---

## Configuration Levers

All levers available on both `form_teams.py` (direct) and `pipeline.py` (end-to-end):

| Flag                | Default      | Description                                           |
|---------------------|--------------|-------------------------------------------------------|
| `--ideal`           | 8            | Target team size                                      |
| `--min`             | 7            | Minimum team size                                     |
| `--max`             | 10           | Maximum team size                                     |
| `--max-groups`      | 25           | Max teams per challenge                               |
| `--w-studyline`     | 1.0          | Weight for studyline diversity in greedy scoring      |
| `--w-personality`   | 1.0          | Weight for personality diversity in greedy scoring    |
| `--seed`            | 42           | Random seed for tie-breaking (reproducibility)        |
| `--missing`         | keep         | Students in group export with no survey               |
| `--cross-challenge` | survey-wins  | Student filled a survey for a different challenge     |
| `--classlist`           | (none)       | Path to current classlist CSV; enables ghost detection (enrolled but absent everywhere) and dropped-student filtering |
| `--dropped`             | keep         | Students with a survey absent from both export and classlist |
| `--late-entry-overrules`| on           | Students with Late Entries survey but overflow/challenge in export → moved to late entry |

---

## Output Files

### `teams.csv`

One row per student. Fields: `team_id, challenge, student_number, student_name, original_category, studyline, personality_type`

### `teams_summary.csv`

One row per team. Fields: `team_id, challenge, size, unique_studylines, unique_personalities, studyline_diversity, personality_diversity`

Diversity = unique count / team size.

---

## Known Data Quality Issues

- **Name used as Q1 ID**: some students type their full name in Q1 instead of their student ID. `resolve.py` detects this (normalise_id returns None), corrects it if the name is in the group export, and warns if not.
- **Wrong ID typed**: common — typos, wrong format, copied incorrectly. Always auto-corrected via name lookup against the group export.
- **Non-standard usernames**: some students have short non-numeric usernames (e.g. `nipac`, `macoda`). These normalise correctly and are matched via the group export username field.
- **Cross-group duplicates**: students who filled more than one challenge survey. Resolved by taking the group export category.
- **Missing survey participants**: students in the group export who never submitted. Handled by `--missing` lever.
- **Late Entries not in Day 1 group export**: the Late Entries group is added later. Late entry students who filled the survey appear in the Individual Reports but not in the Day 1 group export; they are kept as-is with category `late entry`.
- **Late entries students listed under another group in the export**: per the course announcement, a student who filled the Late Entries survey is on the waiting list and their group export entry (overflow or a challenge) is a staging artefact. Default (`--late-entry-overrules` on) moves them to `late entry`. Use `--no-late-entry-overrules` to keep their group export category instead.
- **Office lock files**: `~$` prefixed XLSX files created by Excel when a file is open. Both `parse_individual.py` and `pipeline.py` skip these automatically.
- **Ambiguous names**: two students sharing the same full name cannot be auto-corrected via name lookup. A WARNING is printed and both are left unchanged.
- **Ghost students**: students enrolled in the course (in the classlist) who never joined a group and never filled any survey. Invisible to the pipeline — flagged only when a classlist is provided via `flag_ghost_students`. In 2026 data: 2 ghosts found (`s112544`, `s194963`).

---

## Typical Run (2026 data)

```
python pipeline.py
```

Uses defaults: reads all XLSX from `../Learn Exports/Team Formation Survey Individual Attempts/`, auto-detects the most recent group export CSV from `../Learn Exports/Group Exports/`, writes `teams.csv` and optionally `teams_summary.csv`.

```
python pipeline.py --summary teams_summary.csv --missing overflow --seed 0
```

With per-team stats, overflow treatment for survey-skippers, and a different random seed.

```
python pipeline.py --skip-build "Teambuilding Code/students_combined.csv"
```

Skip step 1 and go straight to team formation with a pre-built student list.

---

## 2026 Run Statistics (reference)

- Group export: 929 students (A=200, B=198, C=198, D=198, Overflow=135)
- Survey: ~900 raw rows, ~893 unique after normalisation
- ~55 students in group export who did not fill the survey (varies by correction run)
- 15 ID mismatches auto-corrected; 8 late-entry-overrules triggered; 14 total late entry students
- Final: 935 students, 100 teams (25 per challenge A–D), all 7–10 members
- Avg studyline diversity: 0.90-0.99 per challenge; avg personality diversity: 0.82-0.93
