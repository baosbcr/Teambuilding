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

> **Test data included:** The repository already contains exported survey and group files from the 2026 course run, so you can try the pipeline straight away without providing your own data. If you want to run it on a different year's data, simply replace the files in `Learn Exports/Team Formation Survey Individual Attempts/` and `Learn Exports/Group Exports/` with your own exports before running.

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

You will see a line like `Running on http://0.0.0.0:5000`. Open your browser and go to:

**http://localhost:5000**

Upload your files using the form, adjust the levers, and click **Run**. The results download automatically as `teams.zip`, containing `teams.csv`, optionally `teams_summary.csv`, and a `run_log.txt` with a full record of the run.

To stop the server, go back to the terminal and press **Ctrl + C**.

> The server is also reachable from other devices on the same network via `http://<your-computer-ip>:5000`.

---

### 4b. Run via terminal (CLI — no server needed)

In the same terminal, navigate into the `Teambuilding Code` subfolder:

```
cd "Teambuilding Code"
```

Then run the pipeline:

```
python pipeline.py
```

The script reads all XLSX files from `Learn Exports/Team Formation Survey Individual Attempts/` and the group export CSV from `Learn Exports/Group Exports/`, then writes `teams.csv` in the `Teambuilding Code` folder.

For a full run that also produces per-team diversity stats:

```
python pipeline.py --summary teams_summary.csv
```

---

## Repository Layout

```
Teambuilding/                              <- project root
  README.md                                <- this file
  DOCS.md                                  <- full reference: all options, edge cases, troubleshooting
  CLAUDE.md                                <- developer/AI context
  Learn Exports/
    Team Formation Survey Individual Attempts/  <- (YOU PROVIDE) one XLSX per survey group
    Group Exports/                         <- (YOU PROVIDE) group-membership CSV from day 1
    Classlist Export Students Only/        <- (optional) full classlist CSV from DTU Learn
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

Save each file into `Learn Exports/Team Formation Survey Individual Attempts/`.

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

Save the file into `Learn Exports/Group Exports/`. The pipeline auto-detects the most recently modified CSV in that folder.

---

### Classlist Export (optional)

Providing the classlist enables two additional checks:

1. **Ghost detection** — students enrolled in the course but absent from both the group export and all surveys are flagged as `WARNING [ghost]` in the log. They will not appear in `teams.csv` and would otherwise be silently missed.
2. **Dropped-student filtering** — students who filled a survey but are not in the group export can be cross-checked; those absent from the classlist are likely unenrolled and can be excluded with `--dropped exclude`.

**How to export:**
> DTU Learn → **Classlist** → **Students** tab → click the **Export** button just above the student list (not the Export button at the very top of the page) → save the CSV

Save the file into `Learn Exports/Classlist Export Students Only/` and pass it to the pipeline with `--classlist <path>`.

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

The matched and verified student list produced before team formation. Useful for auditing which students got which attributes. Pass with `--skip-build` to rerun team formation without re-reading the XLSX files.

---

For all command-line flags, edge case behaviour, example runs, and troubleshooting see **[DOCS.md](DOCS.md)**.
