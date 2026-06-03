#!/usr/bin/env python3
"""Flask web interface for the DTU Teambuilding pipeline."""

import contextlib
import csv
import datetime
import io
import json
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from flask import Flask, render_template, request, send_file

sys.path.insert(0, str(Path(__file__).parent / "Teambuilding Code"))
import parse_individual as _parse
import resolve as _resolve
import form_teams as _form

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB upload limit


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_form_params(form) -> dict:
    """Extract all form fields into a params dict."""
    return {
        "cross_challenge":      form.get("cross_challenge", "survey-wins"),
        "missing_mode":         form.get("missing", "keep"),
        "dropped_mode":         form.get("dropped", "keep"),
        "late_entries":         form.get("late_entries", "keep"),
        "ideal":                int(form.get("ideal", 8)),
        "team_min":             int(form.get("min", 7)),
        "team_max":             int(form.get("max", 10)),
        "max_groups":           int(form.get("max_groups", 25)),
        "w_studyline":          float(form.get("w_studyline", 1.0)),
        "w_personality":        float(form.get("w_personality", 1.0)),
        "seed":                 int(form.get("seed", 42)),
        "include_summary":      "summary" in form,
        "output_mode":          form.get("output_mode", "auto"),
        "id_fallback":          form.get("id_fallback", "username"),
        "assignment_mode":      form.get("assignment_mode", "automatic"),
        "audit_f1":             "audit_f1" in form,
        "audit_dropped":        "audit_dropped" in form,
        "force_audit_ids":      json.loads(form.get("force_audit_ids", "[]")),
    }


def _save_uploads(req, dest_dir: Path) -> tuple[Path, Path, Path | None]:
    """
    Save uploaded files to dest_dir.
    Returns (surveys_dir, group_path, classlist_path).
    """
    surveys_dir = dest_dir / "surveys"
    surveys_dir.mkdir(exist_ok=True)

    report_files = req.files.getlist("surveys")
    saved = [f for f in report_files if f.filename]
    if not saved:
        raise ValueError("Please upload at least one Team Formation Survey Individual Attempts file.")
    for f in saved:
        f.save(surveys_dir / f.filename)

    group_file = req.files.get("groups")
    if not group_file or not group_file.filename:
        raise ValueError("Please upload the Group Export CSV.")
    group_path = dest_dir / group_file.filename
    group_file.save(group_path)

    classlist_path = None
    classlist_file = req.files.get("classlist")
    if classlist_file and classlist_file.filename:
        classlist_path = dest_dir / classlist_file.filename
        classlist_file.save(classlist_path)

    return surveys_dir, group_path, classlist_path


def _run_pipeline_from_files(
    surveys_dir: Path,
    group_path: Path,
    classlist_path: Path | None,
    params: dict,
    workdir: Path,
    overrides: dict | None = None,
):
    """
    Run both pipeline steps from already-saved files.
    Returns (all_teams, log_text, teams_path, summary_path, include_summary).

    When overrides is provided (interactive assignment re-run), cross_challenge is
    forced to survey-wins so confirmed students always get available survey data.
    """
    cross_challenge = params["cross_challenge"]
    missing_mode    = params["missing_mode"]
    dropped_mode    = params["dropped_mode"]
    late_entries    = params["late_entries"]
    ideal           = params["ideal"]
    team_min             = params["team_min"]
    team_max             = params["team_max"]
    max_groups           = params["max_groups"]
    w_studyline          = params["w_studyline"]
    w_personality        = params["w_personality"]
    seed                 = params["seed"]
    include_summary      = params["include_summary"]

    _cross_desc = {
        "survey-wins":      "survey answers used, student stays in their group export challenge",
        "joker":            "survey ignored, student gets UNKNOWN attributes in their group export challenge",
        "survey-overrules": "survey answers used AND student moves to the challenge they surveyed for",
    }
    _missing_desc = {
        "keep":     "included with UNKNOWN studyline and personality in their enrolled group",
        "overflow": "moved to the flex/overflow pool",
        "skip":     "excluded from team formation entirely",
    }
    _dropped_desc = {
        "keep":    "included with their survey category",
        "exclude": "removed from the output",
    }

    log_buf = io.StringIO()
    log_buf.write(
        f"DTU Team Formation — Run Log\n"
        f"Date : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'='*60}\n\n"

        f"RUN SETTINGS\n"
        f"  Team sizes  : target {ideal}, min {team_min}, max {team_max}, "
        f"max {max_groups} teams per challenge\n"
        f"  Diversity   : studyline weight {w_studyline}, personality weight {w_personality}\n"
        f"  Seed        : {seed}  "
        f"(use the same seed to reproduce identical assignments)\n\n"

        f"EDGE CASE LEVERS\n"
        f"  cross-challenge={cross_challenge}\n"
        f"    Student filled a survey for a different challenge than their group export:\n"
        f"    {_cross_desc[cross_challenge]}.\n\n"
        f"  missing={missing_mode}\n"
        f"    Student in the group export who never filled any survey:\n"
        f"    {_missing_desc[missing_mode]}.\n\n"
        f"  dropped={dropped_mode}\n"
        f"    Student with a survey but absent from the group export (and classlist):\n"
        f"    {_dropped_desc[dropped_mode]}.\n\n"
        f"  late-entries={late_entries}\n"
        + {
            "keep":                "    Overflow + late entry survey: moved to late entry.\n"
                                   "    Challenge group + late entry survey: kept in challenge.\n"
                                   "    Late-entry-only students (not in export): kept.\n",
            "flex":                "    All students with a late entry survey moved to late entry.\n"
                                   "    Late-entry-only students (not in export): kept.\n",
            "discard-survey-only": "    Students only in the late entry survey (not in export): excluded.\n"
                                   "    Others unaffected.\n",
            "discard-all":         "    All students with a final late entry allocation excluded.\n",
        }[late_entries] + "\n"
        + (f"  assignment-mode=interactive\n"
           f"    Challenge assignments reviewed and confirmed manually before team formation.\n\n"
           if overrides is not None else "")
        +
        f"LOG MESSAGE GUIDE\n"
        f"  WARNING [Name]: Q1 ID 's12345' corrected to 's67890'\n"
        f"    The student typed the wrong ID in the survey. Corrected automatically\n"
        f"    using the group export.\n\n"
        f"  INFO [Name]: Q1 'Full Name' -> 's253672' via name lookup\n"
        f"    The student typed their full name instead of their student ID.\n"
        f"    Resolved from the group export by name match.\n\n"
        f"  INFO [sXXXXX]: survey in 'late entry', export 'overflow' - moved to late entry\n"
        f"    Student moved to late entry — will be distributed across challenge teams to balance numbers.\n\n"
        f"  INFO [sXXXXX]: survey in 'challenge A', export 'challenge D' - survey data used, category kept\n"
        f"    Cross-challenge case (survey-wins): survey answers used, student stays\n"
        f"    in their group export challenge.\n\n"
        f"  INFO [missing]: sXXXXX  Name  (challenge A - UNKNOWN)\n"
        f"    Student found in the group export but no survey matched them.\n"
        f"    Included with UNKNOWN attributes per missing={missing_mode}.\n\n"
        f"  INFO [not in export]: sXXXXX  Name - late entry, kept\n"
        f"    Student appears in the Late Entries survey but not in the group export.\n"
        f"    Kept as a late entry (registered after the export was taken).\n\n"
        f"  WARNING [ghost]: sXXXXX - enrolled but no group or survey found\n"
        f"    Student is in the classlist but absent from the group export and all\n"
        f"    surveys. They enrolled in the course but never joined a group or filled\n"
        f"    the survey. They will NOT appear in teams.csv.\n\n"
        f"  WARNING [force-audit]: '<value>' did not match any student — skipped\n"
        f"    A student ID or email added to the force-audit list could not be matched\n"
        f"    to any student in the group export or survey records.\n\n"
        f"{'='*60}\n\n"
    )

    # In the interactive re-run, use survey-wins so confirmed students always
    # get available survey data rather than UNKNOWN from the joker setting.
    effective_cross_challenge = "survey-wins" if overrides is not None else cross_challenge

    with contextlib.redirect_stdout(log_buf), contextlib.redirect_stderr(log_buf):
        export_rows = _resolve.load_group_export_rows(group_path)
        name_lookup = _resolve.build_name_lookup(export_rows)
        classlist_ids, username_number_map, name_number_map = (
            _resolve.load_classlist(classlist_path) if classlist_path else (None, {}, {})
        )
        if classlist_ids is not None:
            _resolve.validate_classlist_edition(classlist_ids, export_rows)
        if classlist_ids is None:
            print(
                "NOTE: No classlist uploaded.\n"
                "  - Ghost detection is DISABLED.\n"
                "  - Dropped-student filtering (--dropped lever) is DISABLED.\n"
                "  - email_student_number enrichment is DISABLED: students with non-standard\n"
                "    DTU usernames are fully resolved via the group export (identity and group\n"
                "    assignment) but their sXXXXXX cannot be recovered without the classlist.\n",
            )
        survey_records = _parse.load_all_surveys(surveys_dir)
        students = _resolve.build_student_list(
            group_export_rows    = export_rows,
            survey_records       = survey_records,
            name_lookup          = name_lookup,
            classlist_ids        = classlist_ids,
            cross_challenge      = effective_cross_challenge,
            missing_mode         = missing_mode,
            dropped_mode         = dropped_mode,
            late_entries         = late_entries,
            overrides            = overrides,
        )
        _resolve.enrich_email_student_numbers(students, username_number_map, name_number_map)
        if classlist_ids is not None:
            _resolve.flag_ghost_students(students, classlist_ids)

        combined_path = workdir / "students_combined.csv"
        fieldnames = ["student_number", "dtu_username", "email_student_number",
                      "id_source", "classlist_confirmed", "q1_answer",
                      "student_name", "allocation_category", "studyline", "personality_type"]
        with open(combined_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(students)

        cfg = _form.Config(
            ideal=ideal, team_min=team_min, team_max=team_max,
            max_groups=max_groups, w_studyline=w_studyline,
            w_personality=w_personality, seed=seed,
        )
        cfg.validate()
        teams_path   = workdir / "teams.csv"
        summary_path = workdir / "teams_summary.csv" if include_summary else None
        all_teams = _form.run(cfg, combined_path, teams_path, summary_path)

    return all_teams, log_buf.getvalue(), teams_path, summary_path, include_summary


def _run_pipeline(req, tmpdir: Path):
    """Thin wrapper: save uploads, parse params, run full pipeline."""
    params = _parse_form_params(req.form)
    surveys_dir, group_path, classlist_path = _save_uploads(req, tmpdir)
    return _run_pipeline_from_files(surveys_dir, group_path, classlist_path, params, tmpdir)


def _build_zip(teams_path, summary_path, final_path, log_text):
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(teams_path, "teams.csv")
        if summary_path and summary_path.exists():
            zf.write(summary_path, "teams_summary.csv")
        if final_path and final_path.exists():
            zf.write(final_path, "teams_final.csv")
        zf.writestr("run_log.txt", log_text)
    zip_buf.seek(0)
    return zip_buf


def _finish_run(all_teams, log_text, teams_path, summary_path, include_summary,
                workdir, output_mode, id_fallback, cleanup_dir=None):
    """
    Shared final step: either show the ID review page or build the zip download.
    cleanup_dir is removed after a successful auto download.
    """
    if output_mode == "interactive":
        resolvable, unresolvable = _form.collect_nonstandard(all_teams)
        if resolvable or unresolvable:
            tmp_key     = str(uuid.uuid4())
            session_dir = Path(tempfile.gettempdir()) / f"teams_{tmp_key}"
            session_dir.mkdir()
            with open(session_dir / "session.json", "w", encoding="utf-8") as f:
                json.dump({
                    "all_teams":       all_teams,
                    "log":             log_text,
                    "include_summary": include_summary,
                }, f)
            shutil.copy(teams_path, session_dir / "teams.csv")
            if summary_path and summary_path.exists():
                shutil.copy(summary_path, session_dir / "teams_summary.csv")
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            return render_template(
                "resolve.html",
                resolvable=resolvable,
                unresolvable=unresolvable,
                tmp_key=tmp_key,
            )

    # Auto path (or interactive with no non-standard IDs)
    final_path = workdir / "teams_final.csv"
    decisions  = _form.write_final_teams(all_teams, final_path, id_fallback=id_fallback)
    if decisions:
        log_text += "\nFINAL OUTPUT RESOLUTION\n" + "\n".join(decisions) + "\n"

    zip_buf = _build_zip(teams_path, summary_path, final_path, log_text)
    if cleanup_dir:
        shutil.rmtree(cleanup_dir, ignore_errors=True)

    response = send_file(zip_buf, mimetype="application/zip",
                         as_attachment=True, download_name="teams.zip")
    response.set_cookie("download_ready", "1", max_age=10, samesite="Lax")
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/run", methods=["POST"])
def run():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        params = _parse_form_params(request.form)
        surveys_dir, group_path, classlist_path = _save_uploads(request, tmpdir)
    except (ValueError, SystemExit) as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return render_template("index.html", error=str(e)), 400
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return render_template("index.html", error=f"Unexpected error: {e}"), 500

    # --- Interactive assignment review ---
    if params["assignment_mode"] == "interactive":
        try:
            _discard = io.StringIO()
            with contextlib.redirect_stdout(_discard), contextlib.redirect_stderr(_discard):
                export_rows    = _resolve.load_group_export_rows(group_path)
                name_lookup    = _resolve.build_name_lookup(export_rows)
                classlist_ids, username_number_map, name_number_map = (
                    _resolve.load_classlist(classlist_path) if classlist_path else (None, {}, {})
                )
                survey_records = _parse.load_all_surveys(surveys_dir)
                edge_cases = _resolve.collect_edge_cases(
                    group_export_rows    = export_rows,
                    survey_records       = survey_records,
                    name_lookup          = name_lookup,
                    classlist_ids        = classlist_ids,
                    cross_challenge      = params["cross_challenge"],
                    missing_mode         = params["missing_mode"],
                    dropped_mode         = params["dropped_mode"],
                    late_entries         = params["late_entries"],
                    audit_f1             = params["audit_f1"],
                    audit_dropped        = params["audit_dropped"],
                    force_audit_ids      = params["force_audit_ids"],
                    username_number_map  = username_number_map,
                    name_number_map      = name_number_map,
                )
        except (ValueError, SystemExit) as e:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return render_template("index.html", error=str(e)), 400
        except Exception as e:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return render_template("index.html", error=f"Unexpected error: {e}"), 500

        if edge_cases:
            tmp_key     = str(uuid.uuid4())
            session_dir = Path(tempfile.gettempdir()) / f"assign_{tmp_key}"
            session_dir.mkdir()
            shutil.copytree(surveys_dir, session_dir / "surveys")
            shutil.copy(group_path, session_dir / group_path.name)
            if classlist_path:
                shutil.copy(classlist_path, session_dir / classlist_path.name)
            with open(session_dir / "session.json", "w", encoding="utf-8") as f:
                json.dump({
                    "params":              params,
                    "edge_cases":          edge_cases,
                    "group_filename":      group_path.name,
                    "classlist_filename":  classlist_path.name if classlist_path else None,
                }, f)
            shutil.rmtree(tmpdir, ignore_errors=True)
            return render_template(
                "review_assignments.html",
                edge_cases=edge_cases,
                tmp_key=tmp_key,
            )
        # No edge cases — fall through to normal run

    # --- Normal run (automatic, or interactive with no edge cases) ---
    try:
        all_teams, log_text, teams_path, summary_path, include_summary = (
            _run_pipeline_from_files(surveys_dir, group_path, classlist_path, params, tmpdir)
        )
    except (ValueError, SystemExit) as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return render_template("index.html", error=str(e)), 400
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return render_template("index.html", error=f"Unexpected error: {e}"), 500

    return _finish_run(
        all_teams, log_text, teams_path, summary_path, include_summary,
        workdir=tmpdir,
        output_mode=params["output_mode"],
        id_fallback=params["id_fallback"],
        cleanup_dir=tmpdir,
    )


@app.route("/review_assignments", methods=["POST"])
def review_assignments():
    tmp_key     = request.form.get("tmp_key", "")
    session_dir = Path(tempfile.gettempdir()) / f"assign_{tmp_key}"
    if not session_dir.is_dir():
        return render_template(
            "index.html",
            error="Session expired or not found. Please re-run the pipeline."
        ), 400

    try:
        with open(session_dir / "session.json", encoding="utf-8") as f:
            data = json.load(f)
        params             = data["params"]
        group_filename     = data["group_filename"]
        classlist_filename = data.get("classlist_filename")

        # Build overrides dict: form fields named assign_<student_number>
        overrides = {}
        for key, value in request.form.items():
            if key.startswith("assign_"):
                overrides[key[7:]] = value.strip()

        surveys_dir    = session_dir / "surveys"
        group_path     = session_dir / group_filename
        classlist_path = (session_dir / classlist_filename) if classlist_filename else None

        all_teams, log_text, teams_path, summary_path, include_summary = (
            _run_pipeline_from_files(
                surveys_dir, group_path, classlist_path, params,
                session_dir, overrides=overrides,
            )
        )
    except (ValueError, SystemExit) as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return render_template("index.html", error=str(e)), 400
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        return render_template("index.html", error=f"Unexpected error: {e}"), 500

    return _finish_run(
        all_teams, log_text, teams_path, summary_path, include_summary,
        workdir=session_dir,
        output_mode=params.get("output_mode", "auto"),
        id_fallback=params.get("id_fallback", "username"),
        cleanup_dir=session_dir,
    )


@app.route("/resolve", methods=["POST"])
def resolve():
    tmp_key     = request.form.get("tmp_key", "")
    session_dir = Path(tempfile.gettempdir()) / f"teams_{tmp_key}"
    if not session_dir.is_dir():
        return render_template(
            "index.html",
            error="Session expired or not found. Please re-run the pipeline."
        ), 400

    try:
        with open(session_dir / "session.json", encoding="utf-8") as f:
            data = json.load(f)
        all_teams       = data["all_teams"]
        log_text        = data["log"]
        include_summary = data["include_summary"]

        resolved_ids = {}
        for key, value in request.form.items():
            if key.startswith("id_"):
                resolved_ids[key[3:]] = value.strip()

        final_path = session_dir / "teams_final.csv"
        decisions  = _form.write_final_teams(all_teams, final_path, resolved_ids=resolved_ids)
        if decisions:
            log_text += "\nFINAL OUTPUT RESOLUTION\n" + "\n".join(decisions) + "\n"

        teams_path   = session_dir / "teams.csv"
        summary_path = session_dir / "teams_summary.csv" if include_summary else None
        zip_buf = _build_zip(teams_path, summary_path, final_path, log_text)

    except Exception:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise

    # Session dir kept alive so user can re-submit corrections; cleaned by OS on reboot.
    response = send_file(zip_buf, mimetype="application/zip",
                         as_attachment=True, download_name="teams.zip")
    response.set_cookie("download_ready", "1", max_age=10, samesite="Lax")
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
