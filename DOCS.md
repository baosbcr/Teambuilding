# DTU Teambuilding — Full Reference

This document covers all command-line options, edge case behaviour, example runs, console output guide, and troubleshooting. For setup and basic usage see **[README.md](README.md)**.

---

## All Options

Run from the `Teambuilding Code/` folder: `python pipeline.py [options]`

### Input / output

| Option | Default | Description |
|--------|---------|-------------|
| `--reports DIR` | `../Learn Exports/Team Formation Survey Individual Attempts` | Folder with Individual Attempts XLSX files |
| `--groups CSV` | auto-detect newest in `../Learn Exports/Group Exports` | Group-membership export CSV |
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
| `--late-entry-overrules` | on | `--no-late-entry-overrules` to disable | Overflow students who filled the Late Entries survey → moved to late entry pool. Challenge-group students are never moved regardless of this flag. |
| `--dropped` | `keep` | `keep` `exclude` | Students with a survey but not in group export or classlist (needs `--classlist`) |

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

Two sub-cases depending on whether the student has a confirmed challenge group:

**In overflow + late entry survey** (no confirmed challenge):
- **Default (`--late-entry-overrules` on):** moved to the late entry pool
- **`--no-late-entry-overrules`:** kept in overflow

**In a challenge group + late entry survey** (confirmed challenge spot):
- Always kept in their challenge group regardless of `--late-entry-overrules`
- The late entry survey provides studyline/personality data only; it does not forfeit their spot

This lever is independent of `--cross-challenge` and takes priority over it for late-entry surveys on overflow students.

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
Late-entry-overrules triggered.

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

---

## Example Runs

All commands run from the `Teambuilding Code/` folder.

**Basic run (all defaults):**
```
python pipeline.py
```

**With per-team stats:**
```
python pipeline.py --summary teams_summary.csv
```

**Exclude students who never surveyed; specific seed:**
```
python pipeline.py --missing skip --seed 0 --summary teams_summary.csv
```

**Move survey-skippers to overflow pool (flex):**
```
python pipeline.py --missing overflow
```

**Rerun team formation without re-parsing XLSX files:**
```
python pipeline.py --skip-build students_combined.csv --seed 99
```

**Use a specific group export file:**
```
python pipeline.py --groups "../Learn Exports/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv"
```

**Flag ghost students and exclude likely-dropped students (needs classlist):**
```
python pipeline.py --classlist "../Learn Exports/Classlist Export Students Only/classlist.csv" --dropped exclude
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `No survey files found in '...'` | Wrong `--reports` path, or folder is empty | Check path; confirm XLSX files are in the folder |
| `Could not detect category for '...'` | XLSX filename doesn't contain a recognisable group name | Rename file to include "Challenge A", "Overflow", or "Late entries"; or check `_CATEGORY_PATTERNS` in `parse_individual.py` |
| `No group export CSV found in '...'` | `Learn Exports/Group Exports/` is empty | Add the exported CSV or use `--groups <path>` |
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
