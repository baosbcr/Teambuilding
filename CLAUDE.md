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
    <run>/                               <- one folder per course run (e.g. January 2026)
      Team Formation Survey Individual Attempts/  <- one XLSX per group (pipeline input)
      Group Exports/                     <- group-membership CSV (pipeline input)
      Classlist Export All/              <- (optional) full classlist CSV from DTU Learn
  Teambuilding Code/                     <- pipeline scripts
    parse_individual.py                  <- XLSX reader utility
    resolve.py                           <- Step 1: group-export-first build + survey match
    form_teams.py                        <- Step 2: team formation algorithm
    pipeline.py                          <- CLI orchestrator (both steps in sequence)
```

---

## Data Sources

### Team Formation Survey Individual Attempts (`Learn Exports/<run>/Team Formation Survey Individual Attempts/`)

One XLSX per encompassing group, named after the group (e.g. "Team Formation Survey - Challenge A - Individual Attempts.xlsx"). Each file contains student blocks where:
- The block header row = student's **DTU Learn account name** (authoritative, cannot be wrong)
- Q1 = student's self-reported ID — used only as a **matching hint** to join this survey record to the right group export row; the canonical student number is always taken from the group export email (or username). Q1 is unreliable (typos, name typed instead of ID, wrong format) and is stored as `q1_answer` for audit purposes only. Its only genuine value is as a last-resort reference for students whose student number cannot be recovered from either the group export or the classlist, where it may be the correct ID with no independent verification available.
- Q2 = studyline selection (single choice)
- Q3 = personality type (MBTI, single choice)

File naming must match a category pattern in `_CATEGORY_PATTERNS` (parse_individual.py) or the pipeline aborts.

### Group Export (`Learn Exports/<run>/Group Exports/`)

Single CSV exported post-deadline from DTU Learn. Columns used:
- `Username` — student's Learn username (often the canonical ID, e.g. `s253896`)
- `Email Address` — `sXXXXXX@dtu.dk` format where available (preferred source for canonical ID)
- `First Name`, `Last Name` — student's full name as registered in Learn
- `Group Name` — which challenge group they enrolled in

This file is the **ground truth** for group membership. Must be provided explicitly via `--groups`; there is no auto-detection.

### Classlist Export (`Learn Exports/<run>/Classlist Export All/`)

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
- `student_number` (from Q1 — for students in the group export this is a matching hint only; `resolve.py` replaces it with the canonical ID from the group export. For survey-only students with no group export row, it becomes the initial student_number, superseded by classlist email if available.)
- `studyline`, `personality_type` (from Q2/Q3)

Key public functions:
- `process_file(path, category)` — processes one file, returns list of student dicts
- `load_all_surveys(survey_dir)` — reads all XLSX in a directory, returns flat list

Called by `resolve.py`; not invoked directly in the production pipeline. Skips Office lock files (`~$` prefix).

### `resolve.py` — Step 1 (combined build)

Group-export-first: the group export is the canonical student list; survey data is an enrichment layer.

**Key function:** `build_student_list(group_export_rows, survey_records, name_lookup, classlist_ids, cross_challenge, missing_mode, dropped_mode, late_entries, overrides=None)`

`overrides` is an optional `{student_number: assignment}` dict from the interactive assignment review. When a student's ID is in `overrides`, their `allocation_category` is set to the explicit value, bypassing all lever logic. `"skip"` as a value excludes the student entirely. Survey enrichment (`enrich_email_student_numbers`) always runs unchanged.

During the build, each student record is annotated with internal `_case_type`, `_export_challenge`, and `_survey_challenges` metadata fields (ignored by CSV writers via `extrasaction="ignore"`), used by `collect_edge_cases`.

Five internal steps:
1. Build base dict from group export keyed by canonical student ID (all UNKNOWN attributes); tag each with `_case_type="E"` (default: no survey found)
2. Normalise Q1 answers in survey records and resolve them to group export IDs (matching only — Q1 is not the ID source)
3. Enrich base entries with survey data; apply `--cross-challenge` lever for mismatched challenges; tag with `_case_type` (happy/C/D/late-entry-overrules). `late-entry-overrules` is used when a student is moved to late entry by the `--late-entries` lever.
4. Apply `--missing` lever for students in group export with no survey found
5. Apply `--dropped` lever for students in survey but absent from group export (and classlist); tag with `_case_type` (F1/F2/F3/F-no-classlist/unresolvable)
6. Apply `overrides` (if provided): replace `allocation_category` for listed students, remove those with `"skip"`

**Edge cases handled** (all except A/B surface in the interactive assignment review):
- **Case A/B**: Survey matches group export challenge (happy path / ID corrected) — silent; auditable via force-audit list
- **Case C**: Survey for a single different challenge → `--cross-challenge {survey-wins|joker|survey-overrules}` — reviewed in interactive mode
- **Case D**: Surveys across multiple different challenges, none matching the export challenge → export wins, data from first survey — reviewed in interactive mode. If at least one survey matches the export challenge, the student is treated as happy path (Case A/B) and the extra surveys are logged as INFO only.
- **Case E**: In group export, no survey → `--missing {keep|overflow|skip}` — reviewed in interactive mode
- **Case F1**: Not in group export, survey from Late Entries file → always kept — opt-in to interactive review via `audit_f1` checkbox
- **Case F2/F3**: Not in group export, survey from other file → `--classlist` + `--dropped {keep|exclude}` — reviewed in interactive mode
- **N1**: Two students share a name (different IDs) → matched by ID; if ID also wrong, warns and skips auto-correct

Q1 normalisation/correction is always applied (required to match survey records to the right group export row). The resulting canonical student_number is always sourced from the group export, never from Q1. Warnings are always printed.

**`collect_edge_cases(group_export_rows, survey_records, name_lookup, classlist_ids, cross_challenge, missing_mode, dropped_mode, late_entries, audit_f1=False, audit_dropped=False, force_audit_ids=None, username_number_map=None, name_number_map=None)`**

Two-pass function used by the interactive assignment review:
- Pass 1: runs `build_student_list` with `cross_challenge="survey-wins"`, `missing="keep"`, `dropped="keep"` to collect all students with survey data maximally populated for display.
- Pass 2: runs `build_student_list` with real lever settings to derive `auto_assignment` per student (the value pre-selected in the review dropdown). Students absent from pass 2 (excluded by `missing=skip` / `dropped=exclude`) get `auto_assignment="skip"`.
- `audit_dropped`: when `True`, students absent from the classlist are surfaced in the review with a `not-in-classlist` case type and a warning badge, regardless of their normal case type. Requires a classlist.
- `username_number_map`, `name_number_map`: the maps returned by `load_classlist`. Passed to `enrich_email_student_numbers` on the Pass 1 results so `classlist_confirmed` is accurate on the review page. Default `None` (treated as empty — same as no classlist).
- `force_audit_ids`: list of student identifiers (student number, bare digits, or email). Normalised via `normalise_id`. Matched students always appear in the review. Unmatched entries emit `WARNING [force-audit]: '<value>' did not match any student — skipped`.
- Returns a list of dicts with `case_type, student_number, student_name, export_challenge, survey_challenges, studyline, personality_type, q1_answer, id_source, classlist_confirmed, auto_assignment`. `survey_challenges` is a list (can be multiple for Case D).
- F1 cases are excluded unless `audit_f1=True`.
- Happy-path (A/B) students are excluded unless in `force_audit_ids` or `audit_dropped` applies.

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

Browser-based front end wrapping the same pipeline modules. Accepts file uploads (Individual Reports, Group Export, optional classlist), exposes all levers and review modes as form fields, and returns a `teams.zip` containing:
- `teams.csv` — one row per student
- `teams_summary.csv` — per-team diversity stats (if requested)
- `run_log.txt` — full pipeline output with a settings header and log message guide

**Two-stage interactive flow** (when `assignment_mode=interactive`):
1. `/run` — saves uploads to a session dir, calls `collect_edge_cases`, and renders `review_assignments.html` if any edge cases exist. If no edge cases, falls through to the normal automatic run.
2. `/review_assignments` — receives the confirmed assignments from the review form, re-runs `build_student_list` with the `overrides` dict (and `cross_challenge="survey-wins"` to ensure reviewed students get available survey data), then runs team formation. Continues to the ID review page (`/resolve`) or direct download depending on `output_mode`.

Session data for the assignment review is stored in `tempfile.gettempdir()/assign_<uuid>/` and includes the uploaded files, all pipeline params, and the collected edge cases.

**ID confirmation badges on the review page** (`review_assignments.html`): each student row shows one or two badges derived from `id_source` and `classlist_confirmed`:
- **group export** (blue) — `id_source` starts with `export:` (email or username). Always reliable; shown regardless of classlist.
- **classlist** (green) — `classlist_confirmed = True` (classlist name→number map agrees). Shown alongside group export when both apply.
- **Q1 answer** (orange) — only when neither of the above: number came solely from the student's self-typed survey answer, no independent verification. Occurs when no classlist was uploaded, or when classlist name normalisation fails for that student.
- **Q1 answer** (red) — same as orange but `id_source` is `survey:not-in-classlist` or `survey:unresolvable` (student absent from classlist or number could not be parsed at all).

**Settings persistence (localStorage):** All form settings — assignment mode, levers, audit options, team sizes, output mode — are saved in `localStorage` under the `dtutb_` prefix. Settings survive browser close and app restart as long as the same browser and port are used. Reset buttons clear the relevant keys and restore defaults.

Run locally with `python app.py` (serves on `0.0.0.0:5000`). On the Pi, managed by systemd (`teambuilding.service`) and starts automatically on boot. For production, replace Flask's dev server with a WSGI server (e.g. gunicorn).

**User-facing documentation:** `README.md` contains the complete Flask app usage guide — a plain-language walkthrough of every section of the form (input files, team settings, all levers, interactive review, audit options, output modes). **Keep this guide in sync whenever `templates/index.html`, `templates/review_assignments.html`, or the interactive assignment flow changes.** This includes: adding/removing/renaming form fields or levers, changing lever options or defaults, altering the interactive review table columns, and changing the output ZIP contents.

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

| Flag                    | Default      | Description                                           |
|-------------------------|--------------|-------------------------------------------------------|
| `--ideal`               | 8            | Target team size                                      |
| `--min`                 | 7            | Minimum team size                                     |
| `--max`                 | 10           | Maximum team size                                     |
| `--max-groups`          | 25           | Max teams per challenge                               |
| `--w-studyline`         | 1.0          | Weight for studyline diversity in greedy scoring      |
| `--w-personality`       | 1.0          | Weight for personality diversity in greedy scoring    |
| `--seed`                | 42           | Random seed for tie-breaking (reproducibility)        |
| `--missing`             | keep         | Students in group export with no survey               |
| `--cross-challenge`     | survey-wins  | Student filled a survey for a different challenge     |
| `--classlist`           | (none)       | Path to current classlist CSV; enables ghost detection, dropped-student filtering, and `email_student_number` enrichment for non-standard usernames |
| `--dropped`             | keep         | Students with a survey absent from both export and classlist |
| `--late-entries`        | keep         | How to handle late entry students. `keep`: overflow + late entry survey → late entry; challenge-group stays in group. `flex`: all students with late entry survey → late entry (including challenge-group). `discard-survey-only`: students only in the late entry survey (not in export) excluded. `discard-all`: all students with a final late entry allocation excluded. |

**Web app only (Challenge Assignment section):**

| Setting             | Default      | Description                                           |
|---------------------|--------------|-------------------------------------------------------|
| `assignment_mode`   | automatic    | `automatic`: levers decide all edge cases. `interactive`: review page shown before team formation, one row per edge case with a dropdown pre-filled with the auto-suggested assignment. All assignments (all challenges + overflow + late entry + skip) are always available to the auditor regardless of case type — the auditor may have out-of-band information not reflected in the exports. |
| `audit_f1`          | off          | *(interactive only)* Include F1 students (late entry, not in group export) in the review. Off by default since F1 students are always kept anyway. |
| `audit_dropped`     | off          | *(interactive only)* Include students absent from the classlist in the review, regardless of their normal case type. Requires classlist upload. These students appear with a `not-in-classlist` warning badge. |
| `force_audit_ids`   | (empty)      | *(interactive only)* Comma-separated list of student identifiers (student number, bare digits, or email) to always include in the review regardless of case type. Unmatched entries emit `WARNING [force-audit]` in the run log. Settings saved in browser localStorage. |

---

## Output Files

### `students_combined.csv` (intermediate)

One row per student after resolve step. Fields: `student_number, dtu_username, email_student_number, id_source, classlist_confirmed, q1_answer, student_name, allocation_category, studyline, personality_type`

`id_source` — where the canonical `student_number` was extracted from. Values: `export:email` (sXXXXXX from group export email field), `export:username` (group export username field, standard or non-standard), `survey:late-entry`, `survey:in-classlist`, `survey:not-in-classlist`, `survey:no-classlist`, `survey:unresolvable` (student found only in survey).

`classlist_confirmed` — `True` if the classlist name→number map independently returns the same sXXXXXX as the pipeline ID. Used by the interactive review badge logic (see below).

`q1_answer` — the raw value the student typed in survey Q1, stored for audit purposes only. For group export students, Q1 is never the source of `student_number` — the group export email/username is. For survey-only students, classlist email supersedes Q1 when available. Q1 is only a meaningful reference when a student's sXXXXXX cannot be recovered from either the group export or the classlist — at that point it is the auditor's only lead, with no independent verification. Empty for students with no survey.

### `teams.csv`

One row per student. Fields: `team_id, challenge, student_number, dtu_username, email_student_number, id_source, student_name, original_category, studyline, personality_type`

`dtu_username` — populated when the student's canonical ID was derived from their group export email (e.g. `s225007`) but their DTU Learn username is non-standard (e.g. `nipac`). Preserves the username that would otherwise be silently discarded. Empty for all students whose canonical ID already equals their username.

`email_student_number` — only populated for students whose `student_number` is a non-standard DTU username (not matching `s\d+`) and a classlist was provided. Contains the `sXXXXXX` derived from the classlist email for manual verification. Empty for all standard students.

`id_source` — carried through from `students_combined.csv`; see above.

### `teams_summary.csv`

One row per team. Fields: `team_id, challenge, size, unique_studylines, unique_personalities, studyline_diversity, personality_diversity`

Diversity = unique count / team size.

---

## Known Data Quality Issues

- **Malformed Q1 answer**: students type a full name, a typo, or a wrong-format ID in Q1. `resolve.py` normalises and corrects the Q1 value solely to find the matching group export row — the resulting `student_number` still comes from the group export, not Q1. Q1 is only a meaningful fallback for students absent from both the group export and the classlist (i.e. no sXXXXXX is recoverable from either source), where `q1_answer` is the auditor's only lead.
- **Non-standard usernames**: some students have short non-numeric usernames (e.g. `nipac`, `macoda`). These normalise correctly and are matched via the group export username field.
- **Cross-group duplicates**: students who filled more than one challenge survey. Resolved by taking the group export category.
- **Missing survey participants**: students in the group export who never submitted. Handled by `--missing` lever.
- **Late Entries not in Day 1 group export**: the Late Entries group is added later. Late entry students who filled the survey appear in the Individual Reports but not in the Day 1 group export; they are kept as-is with category `late entry`.
- **Late entries students listed under another group in the export**: two sub-cases:
  - *In overflow + late entry survey*: default (`--late-entries=keep`) moves them to `late entry`. With `--late-entries=flex` they are also moved to late entry (no change). With `discard-all` they are excluded.
  - *In a challenge group + late entry survey*: default (`keep`) keeps them in their challenge group. With `--late-entries=flex` they are moved to late entry. With `discard-all` they are excluded from late entry but remain in their challenge group (their allocation is not late entry).
- **Office lock files**: `~$` prefixed XLSX files created by Excel when a file is open. Both `parse_individual.py` and `pipeline.py` skip these automatically.
- **Ambiguous names**: two students sharing the same full name cannot be auto-corrected via name lookup. A WARNING is printed and both are left unchanged.
- **Ghost students**: students enrolled in the course (in the classlist) who never joined a group and never filled any survey. Invisible to the pipeline — flagged only when a classlist is provided via `flag_ghost_students`. In 2026 data: 18 ghosts found with the full classlist export (Role=Student filter applied).

---

## Typical Run (January 2026 data)

Both `--surveys` and `--groups` are always required; there are no default paths.
All commands run from the `Teambuilding Code/` folder.

```
python pipeline.py \
    --surveys "../Learn Exports/January 2026/Team Formation Survey Individual Attempts" \
    --groups  "../Learn Exports/January 2026/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv"
```

```
python pipeline.py \
    --surveys "../Learn Exports/January 2026/Team Formation Survey Individual Attempts" \
    --groups  "../Learn Exports/January 2026/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv" \
    --summary teams_summary.csv --missing overflow --seed 0
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
- 15 Q1 answers corrected via name lookup (to find the right group export row); 4 overflow students moved to late entry (--late-entries=keep); 4 challenge-group students kept in challenge from late entry survey; 10 total late entry students
- Final: 935 students, 100 teams (25 per challenge A–D), all 9–10 members (after flex levelling fix)
- Avg studyline diversity: 0.90-0.96 per challenge; avg personality diversity: 0.85-0.90
- Classlist (full export, Role=Student only): 952 enrolled students; 11 non-standard usernames with recoverable sXXXXXX; 18 ghost students (enrolled but absent from group export and all surveys)
- 8 non-standard username students (macoda, alesu, jcoro, kaswu, dovli, xicsu, mpabo, jhaja) have `@dtu.dk`/`@aqua.dtu.dk` emails — no sXXXXXX recoverable; email_student_number correctly empty for these
