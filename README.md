# DTU Teambuilding — Team Formation Pipeline

Automatically assigns students to diverse project teams for the DTU innovation course. Starting from the group-membership export (ground truth for enrollment) and the individual survey exports (studyline and personality answers), the pipeline produces a `teams.csv` ready to share with students — with zero manual intervention.

---

## Prerequisites

**Python 3.10 or later**

```bash
python --version   # must be 3.10+
```

**One external library** (everything else is standard library):

```bash
pip install openpyxl
```

---

## Repository Layout

```
Teambuilding/                              <- project root
  README.md                                <- this file
  CLAUDE.md                                <- developer/AI context
  Learn Exports/
    Individual Reports/                    <- (YOU PROVIDE) one XLSX per survey group
    Group Exports/                         <- (YOU PROVIDE) group-membership CSV
    Summary Reports/                       <- test/mock data only, not used in production
  Teambuilding Code/
    pipeline.py                            <- run this to produce teams
    resolve.py                             <- Step 1: build canonical student list
    parse_individual.py                    <- XLSX reader utility (called by resolve.py)
    form_teams.py                          <- Step 2: team formation algorithm
    mock_students.py                       <- synthetic test data generator, NOT production
    students_combined.csv                  <- (auto-generated) intermediate student list
    teams.csv                              <- (auto-generated) final team assignments
    teams_summary.csv                      <- (auto-generated) per-team diversity stats
```

---

## Step 1 — Provide the Input Files

### Individual Reports (required)

One XLSX file per survey group, exported from DTU Learn.

**How to export each file:**
> DTU Learn → Surveys → [Survey name] → Results → Export → **Individual Attempts**

Save each file into `Learn Exports/Individual Reports/`.

Expected files (one per group):
- `... Challenge A ...`
- `... Challenge B ...`
- `... Challenge C ...`
- `... Challenge D ...`
- `... Overflow ...`
- `... Late entries ...` (if applicable)

**Important:** Do not rename files arbitrarily. The pipeline auto-detects the group from the filename using pattern matching (e.g. "Challenge A", "Overflow", "Late entries"). If a file cannot be matched, the pipeline will abort with a clear error message.

Office lock files (`~$...`) left by Excel are automatically ignored.

---

### Group Membership Export (required)

One CSV file exported from DTU Learn **after the enrollment deadline**. This is the ground truth for which challenge each student belongs to.

**How to export:**
> DTU Learn → Groups → Day 1 - Challenge Selection → (gear/actions menu) → **Export members** → CSV

Save the file into `Learn Exports/Group Exports/`.

The pipeline auto-detects the most recently modified CSV in that folder. If you have multiple exports, either delete old ones or pass `--groups <path>` to specify explicitly.

---

### Classlist Export (optional)

Only needed if you want to **exclude students who unenrolled after filling the survey** (i.e. students present in the survey but absent from both the group export and the classlist are treated as dropped).

**How to export:**
> DTU Learn → Classlist → Export → CSV

Pass to the pipeline with `--classlist <path>`.

Without this file, survey-only students are kept with their survey category (safe default).

---

## Step 2 — Run the Pipeline

```bash
cd "Teambuilding Code"
python pipeline.py
```

This reads everything from the default locations and writes `teams.csv` in the same folder.

**Recommended for a full run:**

```bash
python pipeline.py --summary teams_summary.csv
```

Writes both `teams.csv` (one row per student) and `teams_summary.csv` (one row per team, with diversity stats).

---

## Output Files

### `teams.csv` — one row per student

| Column | Example | Notes |
|--------|---------|-------|
| `team_id` | `A-01` | Challenge letter + team number |
| `challenge` | `A` | Final challenge assignment |
| `student_number` | `s253896` | Canonical student ID |
| `student_name` | `Maria Jensen` | From DTU Learn account |
| `original_category` | `challenge A` | Category before flex placement |
| `studyline` | `Biotechnology` | From survey (or UNKNOWN) |
| `personality_type` | `INFJ` | MBTI type from survey (or UNKNOWN) |

### `teams_summary.csv` — one row per team (with `--summary`)

| Column | Notes |
|--------|-------|
| `team_id` | e.g. `A-01` |
| `challenge` | `A`–`D` |
| `size` | Number of students |
| `unique_studylines` | Count of distinct studylines |
| `unique_personalities` | Count of distinct MBTI types |
| `studyline_diversity` | unique / size (0.0–1.0) |
| `personality_diversity` | unique / size (0.0–1.0) |

### `students_combined.csv` — intermediate (auto-generated)

The matched and verified student list produced before team formation. Inspect this to audit which students got which attributes. Pass with `--skip-build` to rerun team formation without re-reading the XLSX files.

---

## All Options

### Input / output

| Option | Default | Description |
|--------|---------|-------------|
| `--reports DIR` | `../Learn Exports/Individual Reports` | Folder with Individual Attempts XLSX files |
| `--groups CSV` | auto-detect newest in `../Learn Exports/Group Exports` | Group-membership export CSV |
| `--classlist CSV` | (none) | Classlist CSV for dropped-student detection |
| `-o / --output PATH` | `teams.csv` | Final team assignment CSV |
| `--summary PATH` | (none) | Per-team diversity statistics CSV |
| `--workdir DIR` | script directory | Directory for `students_combined.csv` |
| `--skip-build CSV` | (none) | Skip Step 1; use pre-built student list directly |

### Edge case levers

| Option | Default | Choices | Description |
|--------|---------|---------|-------------|
| `--missing` | `keep` | `keep` `overflow` `skip` | Students in group export who never filled the survey |
| `--cross-challenge` | `survey-wins` | `survey-wins` `joker` `survey-overrules` | Student filled a survey for a different challenge than their group export |
| `--late-entry-overrules` | on | `--no-late-entry-overrules` to disable | Students whose export says overflow/challenge X but filled the Late Entries survey |
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

Per the course announcement, filling the Late Entries survey means the student missed the main deadline and is on the waiting list. Their export entry (overflow or a challenge) is a staging artefact.

- **Default (`--late-entry-overrules` on):** they are moved to the late entry pool
- **`--no-late-entry-overrules`:** they stay in their group export category

This lever is independent of `--cross-challenge` and takes priority over it for late-entry surveys.

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

The pipeline prints informational messages to stderr during a run. Key patterns:

```
WARNING [Name]: Q1 ID 'xxx' corrected to 'yyy'
```
A student typed the wrong ID in Q1; it was corrected from the group export.

```
INFO [Name]: Q1 'Name' -> 's253672' via name lookup
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

---

## Example Runs

**Basic run (all defaults):**
```bash
python pipeline.py
```

**With per-team stats:**
```bash
python pipeline.py --summary teams_summary.csv
```

**Exclude students who never surveyed; specific seed:**
```bash
python pipeline.py --missing skip --seed 0 --summary teams_summary.csv
```

**Move survey-skippers to overflow pool (flex):**
```bash
python pipeline.py --missing overflow
```

**Rerun team formation without re-parsing XLSX files:**
```bash
python pipeline.py --skip-build students_combined.csv --seed 99
```

**Use a specific group export file:**
```bash
python pipeline.py --groups "Learn Exports/Group Exports/Day 1 - Challenge Selection_AllGroups_20260506105143.csv"
```

**Exclude likely-dropped students (needs classlist):**
```bash
python pipeline.py --classlist classlist.csv --dropped exclude
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `No survey files found in '...'` | Wrong `--reports` path, or folder is empty | Check path; confirm XLSX files are in the folder |
| `Could not detect category for '...'` | XLSX filename doesn't contain a recognisable group name | Rename file to include "Challenge A", "Overflow", or "Late entries"; or check `_CATEGORY_PATTERNS` in `parse_individual.py` |
| `No group export CSV found in '...'` | `Learn Exports/Group Exports/` is empty | Add the exported CSV or use `--groups <path>` |
| `ModuleNotFoundError: No module named 'openpyxl'` | Library not installed | `pip install openpyxl` |
| `SyntaxError` or `TypeError` on startup | Python < 3.10 | Upgrade: `python --version` to check; install 3.10+ |
| Unexpected student counts | Group export was taken before the enrollment deadline | Re-export group membership after the deadline has passed |
| Students with `UNKNOWN` attributes | They didn't fill the survey, or their survey couldn't be matched | Check the `INFO [missing]` and `WARNING` lines in the console output |
| Teams are not reproducible between runs | Different `--seed` values | Always use the same `--seed` to reproduce, or fix it explicitly (e.g. `--seed 42`) |

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
