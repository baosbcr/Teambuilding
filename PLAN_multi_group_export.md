# Plan: Multi-File Group Export Support

## Problem

DTU Learn can export group membership in separate CSV files — e.g. one for Challenge A,
one for B/C/D — rather than a single AllGroups CSV. This happens when the course admin
exports groups in batches (different challenges at different times). The pipeline currently
accepts exactly one group export CSV and will mis-classify all students from the missing
challenge as F2/F3 survey-only cases if their challenge's CSV isn't provided.

### Observed in August 2025 data

Two separate group export files:
- `Day 1 - Challenge A Selection_AllGroups_20260603081122.csv` — 47 students, Challenge A only
- `Day 1 - Challenge B, C & D Selection_AllGroups_20260515081257.csv` — 486 students, B/C/D

Combined: 533 students. **No Overflow group in either file** — needs investigation:
did the August 2025 run not have an Overflow group, or was it not yet exported?

---

## Data Already Organised

The `Learn Exports/` directory now contains three run folders, each with the same subdirectory
layout. The default CLI paths still point at the root `Learn Exports/` folders (unchanged,
for the "active" run). Historical runs are accessed by passing `--surveys`/`--groups` explicitly.

```
Learn Exports/
  January 2026/
    Classlist Export All/
    Group Exports/
    Summary Reports/
    Team Formation Survey Individual Attempts/
  June 2026/
    Classlist Export All/
    Group Exports/            ← single AllGroups CSV (825 students, A/B/C/D/Overflow)
    Team Formation Survey Individual Attempts/
  August 2025/
    Classlist Export All/
    Group Exports/            ← TWO CSVs (A=47, B/C/D=486); no Overflow
    Team Formation Survey Individual Attempts/
```

> **Note:** January 2026 data is still in the root `Learn Exports/` subdirs (not yet moved).
> The migration should happen before running the pipeline on a new active run, to keep the
> root clean. Not a blocker for the multi-file feature itself.

---

## Code Changes

### 1. `resolve.py`

**`load_group_export_rows(paths)`** — accept `Path | list[Path]`, concatenate all rows:
```python
def load_group_export_rows(paths):          # Path | list[Path]
    if isinstance(paths, Path):
        paths = [paths]
    rows = []
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows.extend(csv.DictReader(f))
    return rows
```
No deduplication needed: if a student appears in two files the second occurrence wins —
same as current in-file duplicate behaviour, and correct for the split-export case.

**`load_group_export(paths)`** — same treatment: wrap the existing loop in `for path in paths`.

**Optional validation** — after loading merged rows, warn if any standard challenge
(A, B, C, D) is absent from the `_GROUP_NAME_MAP` keys found in the rows. This catches
"exported B/C/D but forgot A" before team formation silently mis-classifies all Challenge A
students:
```
WARNING [group-export]: challenge group 'Challenge A' absent from the group export — 
all Challenge A survey respondents will appear as F-type (not in export) students.
```

---

### 2. `pipeline.py`

**`step_build(group_export: Path | list[Path], ...)`** — update type hint; update the
logging to print all file names when multiple:
```
# single file:
Group export : 825 students in 1 file — Day 1 - Challenge Selection_AllGroups_...csv
# multiple:
Group export : 533 students in 2 files — Day 1 - Challenge A Selection_...csv, Day 1 - Challenge B, C & D Selection_...csv
```

**`--groups` argument** — change from `metavar="CSV"` (single implicit) to `nargs="+"`,
and accept files **or** a directory:
```
python pipeline.py --groups groupA.csv groupBCD.csv          # explicit files
python pipeline.py --groups "Learn Exports/August 2025/Group Exports/"  # directory
```

Parsing logic:
```python
if args.groups:
    group_export = []
    for g in args.groups:
        p = Path(g)
        if p.is_dir():
            group_export.extend(sorted(p.glob("*.csv")))
        else:
            group_export.append(p)
    if not group_export:
        sys.exit("--groups: no CSV files found.")
else:
    group_dir = Path(__file__).parent.parent / "Learn Exports" / "Group Exports"
    group_export = sorted(group_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not group_export:
        sys.exit(f"No group export CSV found in '{group_dir}'. Specify one with --groups.")
    if len(group_export) == 1:
        print(f"Auto-detected group export: {group_export[0].name}")
    else:
        print(f"Auto-detected {len(group_export)} group export files: "
              f"{', '.join(p.name for p in group_export)}")
```

---

### 3. `app.py`

**`_save_uploads`** — use `getlist("groups")` to accept multiple files; return `list[Path]`:
```python
group_files = req.files.getlist("groups")
if not group_files or not any(f.filename for f in group_files):
    raise ValueError("Please upload the Group Export CSV.")
group_paths = []
for f in group_files:
    if f.filename:
        p = dest_dir / f.filename
        f.save(p)
        group_paths.append(p)
return surveys_dir, group_paths, classlist_path
```

**`_run_pipeline_from_files(group_paths: list[Path], ...)`** — rename parameter from
`group_path` to `group_paths` throughout; all calls to `load_group_export_rows` already
accept a list after the resolve.py change.

**`/run` route — interactive session** — store all filenames and copy all files:
```python
for gp in group_paths:
    shutil.copy(gp, session_dir / gp.name)
json.dump({"group_filenames": [gp.name for gp in group_paths], ...}, f)
```

**`/review_assignments` route** — reconstruct list from session:
```python
group_filenames = data["group_filenames"]
group_paths = [session_dir / fn for fn in group_filenames]
```
Rename key `group_filename` → `group_filenames` (sessions are ephemeral; no migration needed).

---

### 4. `templates/index.html`

Add `multiple` to the group export input; update the helper text:
```html
<input type="file" name="groups" accept=".csv" multiple required>
<small>One or more group export CSVs — select all at once if DTU Learn split the export by challenge (Ctrl/Cmd+click).</small>
```

---

## Testing Plan

1. **Single file — June 2026** (`Group Exports/` has one AllGroups CSV, 825 students).
   Verify output matches pre-change behaviour (regression).

2. **Split export — August 2025** (two CSVs: A=47, B/C/D=486; no Overflow).
   - Verify 533 students loaded from both files combined.
   - Verify the "missing challenge" warning fires for Overflow (and optionally Challenge A
     if it was missing — in this dataset it's present so no warning expected for A).
   - Investigate whether the missing Overflow is expected for this run.

3. **Web app** — upload two group CSV files via the browser; confirm both appear in the
   run log header.

4. **Interactive review with split export** — run interactive mode; confirm session
   persists correctly through the review step with multiple group files.

---

## Open Questions (August 2025)

- No Overflow group in either CSV — did the August 2025 run not have an Overflow group,
  or was it not yet exported when the CSVs were downloaded?
- The B/C/D export is dated May 15 and the A export June 3. Was A simply exported later,
  or is there a reason the exports are from different days?
