#!/usr/bin/env python3
"""Flask web interface for the DTU Teambuilding pipeline."""

import contextlib
import csv
import datetime
import io
import sys
import tempfile
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


@app.route("/run", methods=["POST"])
def run():
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            reports_dir = tmpdir / "reports"
            reports_dir.mkdir()

            # Team Formation Survey Individual Attempts (multiple XLSX)
            report_files = request.files.getlist("reports")
            saved = [f for f in report_files if f.filename]
            if not saved:
                return render_template("index.html", error="Please upload at least one Team Formation Survey Individual Attempts file."), 400
            for f in saved:
                f.save(reports_dir / f.filename)

            # Group Export CSV
            group_file = request.files.get("groups")
            if not group_file or not group_file.filename:
                return render_template("index.html", error="Please upload the Group Export CSV."), 400
            group_path = tmpdir / group_file.filename
            group_file.save(group_path)

            # Optional classlist
            classlist_path = None
            classlist_file = request.files.get("classlist")
            if classlist_file and classlist_file.filename:
                classlist_path = tmpdir / classlist_file.filename
                classlist_file.save(classlist_path)

            # Levers from form
            cross_challenge      = request.form.get("cross_challenge", "survey-wins")
            missing_mode         = request.form.get("missing", "keep")
            dropped_mode         = request.form.get("dropped", "keep")
            late_entry_overrules = "late_entry_overrules" in request.form
            ideal      = int(request.form.get("ideal", 8))
            team_min   = int(request.form.get("min", 7))
            team_max   = int(request.form.get("max", 10))
            max_groups = int(request.form.get("max_groups", 25))
            w_studyline   = float(request.form.get("w_studyline", 1.0))
            w_personality = float(request.form.get("w_personality", 1.0))
            seed          = int(request.form.get("seed", 42))
            include_summary = "summary" in request.form

            # Build log header with run settings and message guide
            _cross_desc = {
                "survey-wins":     "survey answers used, student stays in their group export challenge",
                "joker":           "survey ignored, student gets UNKNOWN attributes in their group export challenge",
                "survey-overrules":"survey answers used AND student moves to the challenge they surveyed for",
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
                f"  late-entry-overrules={late_entry_overrules}\n"
                f"    Student in OVERFLOW who filled the Late Entries survey: "
                + ("moved to the late entry pool.\n" if late_entry_overrules
                   else "kept in overflow.\n") +
                f"    Student in a CHALLENGE group who filled the Late Entries survey:\n"
                f"    always kept in their challenge (survey used for attributes only).\n\n" +

                f"LOG MESSAGE GUIDE\n"
                f"  WARNING [Name]: Q1 ID 's12345' corrected to 's67890'\n"
                f"    The student typed the wrong ID in the survey. Corrected automatically\n"
                f"    using the group export.\n\n"
                f"  INFO [Name]: Q1 'Full Name' -> 's253672' via name lookup\n"
                f"    The student typed their full name instead of their student ID.\n"
                f"    Resolved from the group export by name match.\n\n"
                f"  INFO [sXXXXX]: survey in 'late entry', export 'overflow' - moved to late entry\n"
                f"    Late-entry-overrules triggered. Student moved to the late entry pool.\n\n"
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
                f"{'='*60}\n\n"
            )

            # Run pipeline, capturing all output into the log
            with contextlib.redirect_stdout(log_buf), contextlib.redirect_stderr(log_buf):

                # Step 1: build student list
                export_rows   = _resolve.load_group_export_rows(group_path)
                name_lookup   = _resolve.build_name_lookup(export_rows)
                classlist_ids, username_number_map, name_number_map = (
                    _resolve.load_classlist(classlist_path) if classlist_path else (None, {}, {})
                )
                if classlist_ids is not None:
                    _resolve.validate_classlist_edition(classlist_ids, export_rows)
                if classlist_ids is None:
                    print(
                        "NOTE: No classlist uploaded.\n"
                        "  - Ghost detection is DISABLED: students who enrolled in the course\n"
                        "    but never joined a group and never filled any survey will not be\n"
                        "    flagged and will be silently absent from teams.csv.\n"
                        "  - Dropped-student filtering (--dropped lever) is DISABLED: all\n"
                        "    survey-only students are kept regardless of enrolment status.\n"
                        "  See the 'Classlist Export' section in README.md for how to obtain\n"
                        "  this file, then upload it using the Classlist field on the form.\n",
                    )
                survey_records = _parse.load_all_surveys(reports_dir)
                students = _resolve.build_student_list(
                    group_export_rows    = export_rows,
                    survey_records       = survey_records,
                    name_lookup          = name_lookup,
                    classlist_ids        = classlist_ids,
                    cross_challenge      = cross_challenge,
                    missing_mode         = missing_mode,
                    dropped_mode         = dropped_mode,
                    late_entry_overrules = late_entry_overrules,
                )

                _resolve.enrich_email_student_numbers(students, username_number_map, name_number_map)

                if classlist_ids is not None:
                    _resolve.flag_ghost_students(students, classlist_ids)

                combined_path = tmpdir / "students_combined.csv"
                fieldnames = ["student_number", "dtu_username", "email_student_number",
                              "student_name", "allocation_category", "studyline", "personality_type"]
                with open(combined_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(students)

                # Step 2: form teams
                cfg = _form.Config(
                    ideal=ideal, team_min=team_min, team_max=team_max,
                    max_groups=max_groups, w_studyline=w_studyline,
                    w_personality=w_personality, seed=seed,
                )
                cfg.validate()
                teams_path   = tmpdir / "teams.csv"
                summary_path = tmpdir / "teams_summary.csv" if include_summary else None
                _form.run(cfg, combined_path, teams_path, summary_path)

            # Package results into a zip
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(teams_path, "teams.csv")
                if include_summary and summary_path and summary_path.exists():
                    zf.write(summary_path, "teams_summary.csv")
                zf.writestr("run_log.txt", log_buf.getvalue())
            zip_buf.seek(0)

            response = send_file(
                zip_buf,
                mimetype="application/zip",
                as_attachment=True,
                download_name="teams.zip",
            )
            response.set_cookie("download_ready", "1", max_age=10, samesite="Lax")
            return response

    except SystemExit as e:
        return render_template("index.html", error=str(e)), 500
    except Exception as e:
        return render_template("index.html", error=f"Unexpected error: {e}"), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
