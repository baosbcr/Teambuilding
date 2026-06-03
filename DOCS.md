# DTU Teambuilding — Full Reference

This document covers all command-line options, edge case behaviour, example runs, console output guide, and troubleshooting. For setup and basic usage see **[README.md](README.md)**.

---

## All Options

Run from the `Teambuilding Code/` folder: `python pipeline.py [options]`

### Input / output

| Option | Default | Description |
|--------|---------|-------------|
| `--reports DIR` | (required) | Folder with Individual Attempts XLSX files |
| `--groups CSV` | (required) | Group-membership export CSV |
| `--classlist CSV` | (none) | Classlist CSV; enables ghost detection (`WARNING [ghost]`), dropped-student filtering (`--dropped`), and `email_student_number` enrichment for students with non-standard DTU usernames |
| `-o / --output PATH` | `teams.csv` | Final team assignment CSV |
| `--summary PATH` | (none) | Per-team diversity statistics CSV |
| `--workdir DIR` | script directory | Directory for `students_combined.csv` |
| `--skip-build CSV` | (none) | Skip Step 1; use pre-built student list directly |

### Edge case levers

| Option | Default | Choices | Description |
|--------|---------|---------|-------------|
| `--missing` | `keep` | `keep` `overflow` `skip` | Students in group export who never filled the survey |
| `--cross-challenge` | `survey-wins` | `survey-wins` `joker` `survey-overrules` | Student filled a survey for a different challenge than their group export |
| `--late-entries` | `keep` | `keep` `flex` `discard-survey-only` `discard-all` | How to handle students with a late entry survey. See edge case B below. |
| `--dropped` | `keep` | `keep` `exclude` | Students absent from the classlist — whether found in the group export, a survey, or both. Since the classlist is exported last, absence from it indicates likely withdrawal. Requires `--classlist`. |

### Team formation

| Option | Default | Description |
|--------|---------|-------------|
| `--ideal` | `8` | Target team size |
| `--min` | `7` | Minimum team size |
| `--max` | `10` | Maximum team size |
| `--max-groups` | `25` | Maximum teams per challenge |
| `--w-studyline` | `1.0` | Weight for studyline diversity in assignment scoring |
| `--w-personality` | `1.0` | Weight for personality diversity in assignment scoring |
| `--seed` | `42` | Random seed — change to get different valid assignments, keep the same to reproduce |

---

## Edge Cases Explained

### A. Student filled a survey for a different challenge than their group export

Happens when a student moved groups after completing the survey.

| `--cross-challenge` value | Behaviour |
|--------------------------|-----------|
| `survey-wins` (default) | Survey answers (studyline, personality) are used; group export decides which challenge they join |
| `joker` | Survey is ignored; student gets UNKNOWN attributes and stays in their export group |
| `survey-overrules` | Survey answers used AND student moves to the challenge they surveyed for |

### B. Student filled the Late Entries survey but is in overflow or a challenge in the export

Late entry is a separate allocation — these students are not pre-assigned to a challenge. After each challenge's teams are formed, late entry students are distributed across them to balance sizes.

Controlled by `--late-entries`:

| `--late-entries` value | Overflow + late entry survey | Challenge group + late entry survey | Late-entry-only (not in export) |
|---|---|---|---|
| `keep` (default) | Moved to late entry | Stays in challenge group; survey used for studyline/personality | Kept as late entry |
| `flex` | Moved to late entry | Also moved to late entry | Kept as late entry |
| `discard-survey-only` | Moved to late entry (unaffected by discard) | Stays in challenge group | Excluded, logged |
| `discard-all` | Moved to late entry, then excluded | Stays in challenge group | Excluded, logged |

### C. Student in group export but never filled any survey

| `--missing` value | Behaviour |
|------------------|-----------|
| `keep` (default) | Student is included with UNKNOWN studyline and personality in their enrolled group |
| `overflow` | Student is moved to the flex/overflow pool |
| `skip` | Student is excluded from team formation entirely |

### D. Student filled a survey but is not in the group export

- **From the Late Entries XLSX:** always kept as a late entry (they joined after the export was taken)
- **From any other XLSX, no `--classlist`:** kept with their survey category
- **From any other XLSX, with `--classlist`:**
  - In classlist → enrolled but not yet in a group, kept
  - Not in classlist → likely dropped; `--dropped keep` keeps them, `--dropped exclude` removes them

### E. Two students share the same full name

The system uses student ID as the primary matching key, so two students with the same name are naturally distinct entries. If one of them also typed a wrong ID in the survey AND the name lookup returns two candidates, the system warns and leaves their ID unchanged — no auto-correction is attempted for ambiguous names.

---

## Challenge Assignment Review (Web App)

The web app (`app.py`) has a **Challenge Assignment** section that lets you choose how the edge cases described above are handled before team formation runs.

### Automatic mode (default)

The levers (cross-challenge, missing, dropped, late-entries) decide every case silently. Expand *Lever settings* to configure them. This is equivalent to running `pipeline.py` with the corresponding flags.

### Interactive review mode

After uploading, a review page appears showing every ambiguous case. Each row contains:

| Column | Description |
|--------|-------------|
| Student | Name, canonical ID, and ID source badges |
| Case | The edge case type — one of the situations described in Edge Cases Explained above, shown with a short label |
| Export group | Which challenge group the student enrolled in (from the group export) |
| Survey group(s) | Which challenge(s) the student filled surveys for (can be multiple) |
| Studyline / Personality | From survey; UNKNOWN if no survey found |
| Q1 answer | Raw value from Q1 — audit reference only; not a source of the student number |
| Assignment | Dropdown with the auto-suggested value pre-selected |

All possible assignments (Challenge A–D, Overflow, Late entry, Skip) are always available in the dropdown regardless of case type — the auditor may have out-of-band information (student emails, teacher overrides) not reflected in the exports.

Dropdowns changed from the auto-suggestion are highlighted in red. Submitting the form re-runs the build step with the explicit assignments applied, then proceeds to team formation and (optionally) the student-number ID review.

### Audit options

Available under *Audit options* in the interactive mode section:

- **Audit late-entry students**: include students who appear only in the Late Entries survey (not in the group export) in the review. Off by default since they are always kept anyway — useful if you want to manually verify them.
- **Audit potentially unenrolled students**: include students absent from the classlist in the review, regardless of their normal case type. These students are clearly marked with a "not in classlist" warning badge. Requires classlist upload.
- **Specific students to always audit**: a tag input accepting student numbers (`s253896`, `253896`) or emails (`s253896@dtu.dk`). These students always appear in the review even if their assignment is unambiguous. Useful when you have received direct contact from a student or teacher. Unmatched entries are logged as `WARNING [force-audit]` in the run log.

### Settings persistence

All form settings (assignment mode, levers, audit options, team sizes, output mode) are saved in the browser's `localStorage` under the `dtutb_` key prefix. They survive browser close and app restart. Use the reset buttons to revert individual sections or all assignment settings to defaults.

---

## Understanding the Console Output

The pipeline prints informational messages during a run. Key patterns:

```
WARNING [Name]: Q1 ID 'xxx' corrected to 'yyy'
```
A student typed the wrong ID in Q1; it was corrected from the group export.

```
INFO [Name]: Q1 'Full Name' -> 's253672' via name lookup
```
A student typed their name instead of their ID; resolved from the group export.

```
INFO [sXXXXX]: survey in 'late entry', export 'overflow' - moved to late entry
```
Student filled the Late Entries survey and was moved to late entry — they will be distributed across challenge teams after the main formation to balance group sizes.

```
INFO [discarded]: sXXXXX  Name - late entry (not in export), excluded
```
Student was excluded from team formation because `--late-entries` is set to `discard-survey-only` or `discard-all`.

```
INFO [not in classlist]: sXXXXX  Name (challenge A) - kept (--dropped=keep)
```
Student is in the group export but absent from the classlist — potentially withdrew after the export was taken. Kept by default; use `--dropped exclude` to remove them.

```
INFO [dropped]: sXXXXX  Name - not in classlist, excluded
```
Student was absent from the classlist and excluded because `--dropped=exclude`.

```
INFO [sXXXXX]: survey in 'challenge A', export 'challenge D' - survey data used, category kept
```
Cross-challenge case — survey answers used, export group kept (default `survey-wins`).

```
INFO [missing]: sXXXXX  Name  (challenge A - UNKNOWN)
```
Student in group export with no survey found.

```
WARNING [ghost]: sXXXXX - enrolled but no group or survey found
```
Student is in the classlist but absent from the group export and all surveys. They enrolled in the course but never joined a group or filled the survey. They will **not** appear in `teams.csv`. Only printed when `--classlist` is provided.

```
WARNING [force-audit]: '<value>' did not match any student — skipped
```
A student identifier added to the *Specific students to always audit* field in the web app could not be matched to any student in the group export or survey records. Check the spelling and format (student number, bare digits, or email). Only printed in web app interactive mode.

---

## Example Runs

All commands run from the `Teambuilding Code/` folder.

**Basic run:**
```
python pipeline.py \
    --reports "../Learn Exports/January 2026/Team Formation Survey Individual Attempts" \
    --groups  "../Learn Exports/January 2026/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv"
```

**With per-team stats:**
```
python pipeline.py --reports <dir> --groups <csv> --summary teams_summary.csv
```

**Exclude students who never surveyed; specific seed:**
```
python pipeline.py --reports <dir> --groups <csv> --missing skip --seed 0 --summary teams_summary.csv
```

**Move survey-skippers to overflow pool (flex):**
```
python pipeline.py --reports <dir> --groups <csv> --missing overflow
```

**Rerun team formation without re-parsing XLSX files:**
```
python pipeline.py --skip-build students_combined.csv --seed 99
```

**Flag ghost students and exclude likely-dropped students (needs classlist):**
```
python pipeline.py --reports <dir> --groups <csv> \
    --classlist "../Learn Exports/January 2026/Classlist Export All/classlist.csv" \
    --dropped exclude
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `No survey files found in '...'` | Wrong `--reports` path, or folder is empty | Check path; confirm XLSX files are in the folder |
| `Could not detect category for '...'` | XLSX filename doesn't contain a recognisable group name | Rename file to include "Challenge A", "Overflow", or "Late entries"; or check `_CATEGORY_PATTERNS` in `parse_individual.py` |
| `--groups is required` | `--groups` flag was not passed | Provide the path to the group export CSV with `--groups <path>` |
| `--reports is required` | `--reports` flag was not passed | Provide the path to the Individual Attempts folder with `--reports <dir>` |
| `ModuleNotFoundError: No module named 'openpyxl'` | Library not installed | `pip install openpyxl` |
| `ModuleNotFoundError: No module named 'flask'` | Library not installed | `pip install flask` |
| `SyntaxError` or `TypeError` on startup | Python < 3.10 | Upgrade: `python --version` to check; install 3.10+ from python.org |
| Unexpected student counts | Group export was taken before the enrollment deadline | Re-export group membership after the deadline has passed |
| Students with `UNKNOWN` attributes | They didn't fill the survey, or their survey couldn't be matched | Check the `INFO [missing]` and `WARNING` lines in the console output |
| Teams are not reproducible between runs | Different `--seed` values | Always pass the same `--seed` explicitly (e.g. `--seed 42`) |

---

## Reference: 2026 Run

| Metric | Value |
|--------|-------|
| Group export students | 929 (A=200, B=198, C=198, D=198, Overflow=135) |
| Survey responses | ~900 raw, ~893 unique |
| ID corrections applied | 15 |
| Late-entry overrides | 8 (overflow/challenge → late entry) |
| Students with no survey | 42 (kept with UNKNOWN, default) |
| Final student count | 935 |
| Teams | 100 (25 per challenge A–D) |
| Team sizes | 7–10 members |
| Avg studyline diversity | 0.90–0.99 per challenge |
| Avg personality diversity | 0.82–0.93 per challenge |
