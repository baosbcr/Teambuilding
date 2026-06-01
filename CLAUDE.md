# DTU Teambuilding — Project Context for Claude

## Session wrap-up

When the user signals they are done for the session (says goodbye, asks to wrap up, mentions leaving), suggest running `/DtuWrap` before they close Claude. It commits pending work, updates the hours log and weekly status in the Bookkeeping repo, and checks that docs are current.

## Weekly Status Log

Status messages, the task list, and the hours log all live in the **separate Bookkeeping repo** (`baosbcr/Bookkeeping`, local path `../Bookkeeping`). Do not create or edit `weekly_status.md` or `todo.md` here.

`../Bookkeeping/statuses/weekly_status.md` is the running work-session log used to draft status updates for the supervisor. There is no fixed cadence — sessions may happen on any days. **Append a plain-English bullet or two at the end of every working session** — written as if the reader has only seen the Flask app run once or twice. After the user confirms they've submitted the update, **delete all bullet points and start fresh**.

Use `/DtuWrap` to handle this automatically at session end.

---

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

### Classlist Export (`Learn Exports/Classlist Export All/`)

Optional CSV exported from DTU Learn (Classlist → All tab → Export above the list). Columns used:
- `Name` — student full name in `Last, First` format (from DTU Learn — same source as survey block headers)
- `UserName` — student username (note: capital N, differs from group export's `Username`)
- `Email` — typically `sXXXXXX@student.dtu.dk`; used as primary source of student number but not assumed infallible — falls back to username if email does not yield a valid `sXXXXXX`
- `Role` — DTU Learn role; only rows with `Role = "Student"` are processed. Other values (`Student*`, `Teacher`, `Course Responsible`, `Teaching Assistant Plus`, `Test student`) are silently skipped. Files without a `Role` column (old format) are processed as-is for backward compatibility.

`load_classlist` handles both column name layouts (group-export style and classlist-export style) automatically. Returns a 3-tuple and enables three features:
- **Ghost detection**: students in classlist but absent from group export and all surveys → `WARNING [ghost]`
- **Dropped-student filtering**: cross-checks survey-only students against classlist for the `--dropped` lever
- **Email student number enrichment**: builds `name → sXXXXXX` and `username → sXXXXXX` maps used by `enrich_email_student_numbers` to populate the `email_student_number` output column for students with non-standard DTU usernames

`validate_classlist_edition` is called automatically after loading and warns (`WARNING [classlist]`) if fewer than 50% of group export students appear in the classlist, which indicates a wrong course edition. A 50–80% overlap triggers a softer `NOTE [classlist]`.

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

**Additional functions (called after `build_student_list`):**
- `flag_ghost_students(final_students, classlist_ids)` — diffs classlist IDs against the final output and prints `WARNING [ghost]` for any enrolled student absent from both group export and all surveys.
- `enrich_email_student_numbers(students, username_number_map, name_number_map)` — in-place; adds `email_student_number` field to every student record. For students with non-standard canonical IDs (not matching `s\d+`), looks up first by normalised name in `name_number_map` (primary — DTU Learn name matched to classlist email), then falls back to `username_number_map`. Standard `sXXXXXX` students always get an empty string. Both maps are empty when no classlist is provided.

**Helper:** `_parse_classlist_name(raw)` — converts `"Last, First"` classlist name format to `"First Last"` for consistent normalisation with group-export and survey names.

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
| `--classlist`           | (none)       | Path to current classlist CSV; enables ghost detection, dropped-student filtering, and `email_student_number` enrichment for non-standard usernames |
| `--dropped`             | keep         | Students with a survey absent from both export and classlist |
| `--late-entry-overrules`| on           | Students in **overflow** who filled the Late Entries survey → moved to late entry. Students already in a challenge group are **never** moved (their survey provides studyline/personality data only) |

---

## Output Files

### `teams.csv`

One row per student. Fields: `team_id, challenge, student_number, dtu_username, email_student_number, student_name, original_category, studyline, personality_type`

`dtu_username` — populated when the student's canonical ID was derived from their group export email (e.g. `s225007`) but their DTU Learn username is non-standard (e.g. `nipac`). Preserves the username that would otherwise be silently discarded. Empty for all students whose canonical ID already equals their username.

`email_student_number` — only populated for students whose `student_number` is a non-standard DTU username (not matching `s\d+`) and a classlist was provided. Contains the `sXXXXXX` derived from the classlist email for manual verification. Empty for all standard students.

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
- **Late entries students listed under another group in the export**: two sub-cases:
  - *In overflow + late entry survey*: student is on the waiting list with no confirmed challenge. Default (`--late-entry-overrules` on) moves them to `late entry`. Use `--no-late-entry-overrules` to keep them in overflow.
  - *In a challenge group + late entry survey*: student already has a confirmed challenge spot — filling the late entry survey does not forfeit it. They always stay in their challenge group; the late entry survey provides studyline/personality data only.
- **Office lock files**: `~$` prefixed XLSX files created by Excel when a file is open. Both `parse_individual.py` and `pipeline.py` skip these automatically.
- **Ambiguous names**: two students sharing the same full name cannot be auto-corrected via name lookup. A WARNING is printed and both are left unchanged.
- **Ghost students**: students enrolled in the course (in the classlist) who never joined a group and never filled any survey. Invisible to the pipeline — flagged only when a classlist is provided via `flag_ghost_students`. In 2026 data: 18 ghosts found with the full classlist export (Role=Student filter applied).

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
- 42 students in group export with no survey (--missing=keep)
- 15 ID mismatches auto-corrected; 4 late-entry-overrules triggered (overflow only); 4 challenge-group students kept in challenge from late entry survey; 10 total late entry students
- Final: 935 students, 100 teams (25 per challenge A–D), all 9–10 members (after flex levelling fix)
- Avg studyline diversity: 0.90-0.96 per challenge; avg personality diversity: 0.85-0.90
- Classlist (full export, Role=Student only): 952 enrolled students; 11 non-standard usernames with recoverable sXXXXXX; 18 ghost students (enrolled but absent from group export and all surveys)
- 8 non-standard username students (macoda, alesu, jcoro, kaswu, dovli, xicsu, mpabo, jhaja) have `@dtu.dk`/`@aqua.dtu.dk` emails — no sXXXXXX recoverable; email_student_number correctly empty for these
