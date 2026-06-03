# DTU Teambuilding — Team Formation Pipeline

Automatically assigns students to diverse project teams for the DTU innovation course. Starting from the group-membership export (ground truth for enrollment) and the individual survey exports (studyline and personality answers), the pipeline produces a `teams.csv` ready to share with students — with zero manual intervention.

For the full reference (all flags, edge case behaviour, troubleshooting) see **[DOCS.md](DOCS.md)**.

---

## Getting Started

### 1. Download the repository

**Option A — Download as ZIP (easiest):**
1. Go to the repository page on GitHub.
2. Click the green **Code** button → **Download ZIP**.
3. Extract the ZIP to a folder of your choice (e.g. your Desktop).

**Option B — Git clone (if you have Git installed):**
1. Open a terminal (see step 2 below for how to do that).
2. Navigate to where you want the folder, then run:
   ```
   git clone <repository-url>
   ```

> **Sample data included:** The repository contains exported survey and group files from multiple course runs under `Learn Exports/<run>/`. Each run folder follows the same structure. You must always pass `--surveys` and `--groups` explicitly when using the CLI — there are no default paths.

---

### 2. Open a terminal in the project folder

You need a terminal (command line) to install dependencies and run the pipeline. Here is how to open one directly inside the project folder:

**Windows:**
1. Open File Explorer and navigate into the extracted/cloned folder (the one containing `app.py`).
2. Click the address bar at the top, type `cmd`, and press **Enter**. A Command Prompt window opens already pointing at that folder.
   - Alternatively: hold **Shift** and right-click an empty area in the folder → **Open PowerShell window here** (or **Open in Terminal** on Windows 11).

**Mac:**
1. Open Finder and navigate into the project folder.
2. Right-click (or Control-click) the folder → **New Terminal at Folder**.
   - Or open **Terminal** from Applications → Utilities, then drag the folder onto the Terminal window and press **Enter**.

**Linux:**
1. Right-click inside the project folder in your file manager → **Open Terminal Here** (exact label varies by distro).

Once the terminal is open you should see the folder path in the prompt, e.g. `C:\Users\you\Desktop\Teambuilding_task>`.

---

### 3. Install dependencies

In the terminal you just opened, run:

```
pip install -r requirements.txt
```

This installs `flask` and `openpyxl`. Everything else is Python standard library. You only need to do this once.

> If `pip` is not recognised, try `pip3` instead. If Python itself is missing, install it from https://www.python.org (3.10 or later).

---

### 4a. Run via Flask (browser interface — recommended)

From the terminal opened in the project root (the folder containing `app.py`), run:

```
python app.py
```

You will see a line like `Running on http://0.0.0.0:5000`. Open your browser and go to **http://localhost:5000**. To stop the server later, press **Ctrl + C** in the terminal.

> The server is also reachable from other devices on the same network via `http://<your-computer-ip>:5000`.

> **Settings are saved in your browser.** All form values are automatically restored the next time you open the app. Use the reset buttons to revert individual sections to defaults.

---

#### Input Files

The form has three file upload fields.

**Team Formation Survey Individual Attempts** *(required)*
The XLSX files exported from DTU Learn — one per survey group (Challenge A, B, C, D, Overflow, Late entries). Select all files at once using Ctrl+click (Windows) or Cmd+click (Mac).

**Group Export** *(required)*
The CSV exported from DTU Learn → Groups, after the enrollment deadline. This is the ground truth for which challenge each student enrolled in.

**Classlist** *(optional but strongly recommended)*
The full student list exported from DTU Learn → Classlist. Providing it unlocks three features:
- **Ghost detection** — flags students who enrolled in the course but never joined a group and never filled any survey. Without the classlist these students are silently absent from the output.
- **Dropped-student filtering** — lets the pipeline distinguish students who likely withdrew (absent from the classlist) from valid late additions.
- **Student number enrichment** — for students with non-standard DTU usernames (short alphabetic accounts), the pipeline looks up their `sXXXXXX` number from their classlist email and adds it to the output for manual verification.

If you submit without a classlist, a warning appears asking you to confirm. It is safe to proceed, but the three features above will be unavailable.

---

#### Team Settings

| Setting | Default | What it does |
|---------|---------|--------------|
| Target size | 8 | The size the algorithm aims for when deciding how many teams to create. |
| Minimum size | 7 | No team will be formed with fewer than this many students. |
| Maximum size | 10 | No team will grow beyond this size. |
| Max teams per challenge | 25 | Hard cap on the number of teams per challenge group. |
| Random seed | 42 | Controls tie-breaking during assignment. The same seed always gives the same output — useful for reproducing a previous run. Change it to explore alternative valid assignments. |

Under **Advanced — diversity weights** you can adjust how much the algorithm values studyline diversity vs personality type diversity when building teams. Both default to 1.0 (equal weight). Increase one to prioritise it more strongly.

---

#### Challenge Assignment

This section controls how students who don't fit cleanly into the normal flow are handled before teams are formed.

##### Automatic mode (default)

The pipeline resolves every ambiguous case silently according to the four levers below. Expand **Edge case settings** to configure them.

| Lever | Default | What it controls |
|-------|---------|-----------------|
| **Cross-challenge survey** | survey-wins | A student filled a survey for a *different* challenge than they enrolled in — e.g. they moved groups after completing the survey. `survey-wins` uses their survey answers (studyline, personality) but keeps them in their enrolled challenge. `joker` ignores the survey and assigns UNKNOWN attributes. `survey-overrules` uses the survey answers *and* moves them to the challenge they surveyed for. |
| **Students with no survey** | keep | Students who appear in the group export but never filled any survey. `keep` includes them with UNKNOWN studyline and personality. `overflow` moves them to the flex pool. `skip` excludes them entirely. |
| **Students with a survey but not in the export** | keep | Students who submitted a survey but have no matching group export row — likely withdrew from the course after submitting. `keep` includes them. `exclude` removes them. Only meaningfully distinguishable from valid late additions when a classlist is uploaded. |
| **Late entries** | keep | Controls what happens to students who filled the Late Entries survey. `keep` moves overflow students with a late entry survey to the late entry pool (challenge-group students stay in their challenge). `flex` moves everyone with a late entry survey to late entry, including challenge-group students. `discard-survey-only` excludes students found only in the late entry survey (not in the group export). `discard-all` excludes all students who end up with a late entry allocation. |

Late entry students are not pre-assigned to a challenge — after challenge teams are formed they are distributed across all challenges to balance team sizes.

##### Interactive review mode

Instead of the levers deciding silently, you get a review page before teams are formed. It shows every student whose assignment was ambiguous, one row per student, with their full context:

- **Student** — name, canonical ID, and one or two confirmation badges. **group export** (blue) means the number came from the group export — always reliable. **classlist** (green) means the classlist independently confirms it. Both together is the strongest signal. **Q1 answer** (orange) only appears when neither source is available — the number came solely from the student's self-typed survey answer with no independent check. Q1 answer in red means the student is also absent from the classlist (likely unenrolled or a name normalisation edge case).
- **Case** — why this student is being reviewed (e.g. cross-challenge survey, no survey, not in group export).
- **Export group** — the challenge they enrolled in according to the group export.
- **Survey group(s)** — which challenge(s) they filled surveys for.
- **Studyline / Personality** — from their survey; UNKNOWN if no survey was found.
- **Q1 answer** — the raw ID value they typed in the survey, shown for reference only.
- **Assignment** — a dropdown pre-filled with the auto-suggested assignment. Change it if needed.

All assignments — Challenge A through D, Overflow, Late entry, and Skip (exclude) — are always available in the dropdown regardless of case type. You may have information the pipeline doesn't, such as direct contact from a student or a teacher override.

Dropdowns you have changed are highlighted in red so it is easy to see what you have overridden. Click **Confirm assignments → Form teams** when done.

**Audit options** (optional, for extra scrutiny):

- *Include late-entry students (not in group export) in the review* — late entry students are always kept anyway, but turning this on lets you verify them manually.
- *Include potentially unenrolled students in the review* — surfaces students who are absent from the classlist, regardless of their normal case type, marked with a warning badge. Requires a classlist upload.
- *Specific students to always audit* — type a student number (`s253896`, `253896`) or email (`s253896@dtu.dk`) and press Enter to add it as a tag. That student will always appear in the review regardless of their case type. Useful when you have received direct contact from a specific student or teacher.

---

#### Output

**Include per-team diversity summary** — adds `teams_summary.csv` to the download, with one row per team showing team size, number of unique studylines, number of unique personality types, and diversity scores (unique count ÷ team size).

**Final output mode** — controls how the clean `teams_final.csv` (Name, Student Number, Group) is produced:
- *Automatic* — student numbers are resolved by rule and all decisions are logged in `run_log.txt`.
- *Interactive* — you are shown a review page for any students whose number could not be resolved with certainty, before the final file is generated.

**Fallback for unresolvable student numbers** *(automatic mode only)* — what to write in the Student Number column for students where no `sXXXXXX` can be found anywhere. Choices: use their DTU username as-is (default), leave it blank, or write `UNRESOLVED:<username>` as an explicit flag.

---

#### What you get

Clicking **Run Pipeline & Download Results** starts the pipeline. A spinner appears while it runs (up to ~90 seconds). When done, `teams.zip` downloads automatically, containing:

| File | Contents |
|------|----------|
| `teams.csv` | Full team assignments — one row per student with team ID, challenge, student number, name, studyline, personality, and the original group they enrolled in. |
| `teams_final.csv` | Clean version for sharing — Name, Student Number, Group only. |
| `teams_summary.csv` | Per-team diversity stats (only if the summary option was checked). |
| `run_log.txt` | Complete log of the run, including the settings used, every warning and info message, and a guide to reading the log messages. |

---

### 4b. Run via terminal (CLI — for advanced use)

In the same terminal, navigate into the `Teambuilding Code` subfolder:

```
cd "Teambuilding Code"
```

Then run the pipeline, pointing it at your input files:

```
python pipeline.py \
    --surveys "../Learn Exports/<run>/Team Formation Survey Individual Attempts" \
    --groups  "../Learn Exports/<run>/Group Exports/<filename>.csv"
```

Replace `<run>` with the course run folder (e.g. `January 2026`) and `<filename>` with the actual CSV name. The script writes `teams.csv` in the `Teambuilding Code` folder.

For a full run that also produces per-team diversity stats:

```
python pipeline.py --surveys <dir> --groups <csv> --summary teams_summary.csv
```

---

## Repository Layout

```
Teambuilding/                              <- project root
  README.md                                <- this file
  DOCS.md                                  <- full reference: all options, edge cases, troubleshooting
  CLAUDE.md                                <- developer/AI context
  Learn Exports/
    <run>/                                 <- one folder per course run (e.g. January 2026)
      Team Formation Survey Individual Attempts/  <- (YOU PROVIDE) one XLSX per survey group
      Group Exports/                       <- (YOU PROVIDE) group-membership CSV from day 1
      Classlist Export All/                <- (optional) full classlist CSV from DTU Learn
  Teambuilding Code/
    pipeline.py                            <- run this to produce teams
    resolve.py                             <- Step 1: build canonical student list
    parse_individual.py                    <- XLSX reader utility (called by resolve.py)
    form_teams.py                          <- Step 2: team formation algorithm
    students_combined.csv                  <- (auto-generated) intermediate student list
    teams.csv                              <- (auto-generated) final team assignments
    teams_summary.csv                      <- (auto-generated) per-team diversity stats
```

---

## Providing Input Files

> **Naming note:** Both input files come from DTU Learn exports. This guide uses **survey** as shorthand for the Individual Attempts exports (found under Surveys in Learn) and **group export** for the group-membership export (found under Groups).

### Team Formation Survey Individual Attempts (required)

One XLSX file per survey group, exported from DTU Learn.

**How to export each file:**
> DTU Learn → Surveys → [Survey name] → Results → Export → **Individual Attempts**

Save each file into your run folder, e.g. `Learn Exports/January 2026/Team Formation Survey Individual Attempts/`.

Expected files (one per group):
- `... Challenge A ...`
- `... Challenge B ...`
- `... Challenge C ...`
- `... Challenge D ...`
- `... Overflow ...`
- `... Late entries ...` (if applicable)

**Important:** Do not rename files arbitrarily. The pipeline auto-detects the group from the filename using pattern matching. If a file cannot be matched, the pipeline will abort with a clear error message.

Office lock files (`~$...`) left by Excel are automatically ignored.

---

### Group Membership Export (required)

One CSV file exported from DTU Learn **after the enrollment deadline**.

**How to export:**
> DTU Learn → Groups → Day 1 - Challenge Selection → (gear/actions menu) → **Export members** → CSV

Save the file into your run folder, e.g. `Learn Exports/January 2026/Group Exports/`, and pass its path to `--groups`.

---

### Classlist Export (optional)

Providing the classlist enables three additional features:

1. **Ghost detection** — students enrolled in the course but absent from both the group export and all surveys are flagged as `WARNING [ghost]` in the log. They will not appear in `teams.csv` and would otherwise be silently missed.
2. **Dropped-student filtering** — students who filled a survey but are not in the group export can be cross-checked; those absent from the classlist are likely unenrolled and can be excluded with `--dropped exclude`.
3. **Student number enrichment** — students with non-standard DTU usernames (e.g. short alphabetic accounts) get an `email_student_number` column in `teams.csv` with the `sXXXXXX` derived from their classlist email, for manual verification.

**How to export:**
> DTU Learn → **Classlist** → **Students** tab → click the **Export** button just above the student list (not the Export button at the very top of the page) → save the CSV

Save the file into your run folder, e.g. `Learn Exports/January 2026/Classlist Export All/`, and pass its path to `--classlist <path>`.

---

## Output Files

### `teams.csv` — one row per student

| Column | Example | Notes |
|--------|---------|-------|
| `team_id` | `A-01` | Challenge letter + team number |
| `challenge` | `A` | Final challenge assignment |
| `student_number` | `s253896` | Canonical student ID |
| `dtu_username` | `nipac` | DTU Learn username when it was replaced by an email-derived ID; empty if username equals the canonical ID |
| `email_student_number` | `s253896` | `sXXXXXX` from classlist email — only populated for non-standard usernames when a classlist was provided; empty otherwise |
| `student_name` | `Maria Jensen` | From DTU Learn account |
| `original_category` | `challenge A` | Challenge group from the group export, before any late-entry redistribution |
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

The matched and verified student list produced before team formation. Useful for auditing which students got which attributes. Pass with `--skip-build` to rerun team formation without re-reading the XLSX files.

---

For all command-line flags, edge case behaviour, example runs, and troubleshooting see **[DOCS.md](DOCS.md)**.
