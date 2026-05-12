#!/usr/bin/env python3
"""Flask web interface for the DTU Teambuilding pipeline."""

import csv
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

            # Individual Reports (multiple XLSX)
            report_files = request.files.getlist("reports")
            saved = [f for f in report_files if f.filename]
            if not saved:
                return render_template("index.html", error="Please upload at least one Individual Report file."), 400
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

            # Step 1: build student list
            export_rows   = _resolve.load_group_export_rows(group_path)
            name_lookup   = _resolve.build_name_lookup(export_rows)
            classlist_ids = _resolve.load_classlist(classlist_path) if classlist_path else None
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

            combined_path = tmpdir / "students_combined.csv"
            fieldnames = ["student_number", "student_name", "allocation_category",
                          "studyline", "personality_type"]
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
