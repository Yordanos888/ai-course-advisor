"""
web_app.py
==========
Student-facing web platform for the ECE Academic Advisor.

It is a thin JSON API + a static single-page front-end (``web/``) that reuses
the SAME engines the Telegram bot uses (query_api, orchestrator /
solve_schedule, explain_schedule, grade_calculator), so both platforms always
give identical answers. The existing admin panel (app.py, port 5001) is
untouched.

Run:  python web_app.py   ->  http://localhost:5002
"""
import os
import re
import html as html_lib

# The shared modules use a relative SQLite path ("academic_records.db"),
# so always run from this file's directory regardless of the caller's cwd.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, request, send_from_directory, session as flask_session
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from models import (
    engine, Student, Course, Stream, Department, CommonCourse, UserFeedback,
)
import query_api
import grade_calculator as gc
from orchestrator import process_reasoning_request_full
from explain_schedule import build_explanation_message

app = Flask(__name__, static_folder="web", static_url_path="")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_only_change_me_web_advisor")

Session = sessionmaker(bind=engine)

STREAMS = ["Computer", "Communication", "Control", "Power"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def err(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def telegram_html_to_web(text: str) -> str:
    """The bot formatters emit Telegram-flavoured HTML (<b>,<i>,<u>,<code>
    + escaped text + newlines). That subset is already safe to render; we only
    turn newlines into <br> and bullets into nicer markup."""
    return (text or "").replace("\r", "").replace("\n", "<br>")


def current_student_id():
    return flask_session.get("student_id")


def student_payload(db, student):
    stream = None
    if student.stream_id:
        s = db.query(Stream).filter_by(id=student.stream_id).first()
        stream = s.name.split()[0] if s else None
    year = sem = None
    if student.batch_id:
        from models import Batch
        b = db.query(Batch).filter_by(id=student.batch_id).first()
        if b:
            year, sem = b.current_year_level, b.current_semester
    return {"id": student.id, "name": student.name, "year": year,
            "semester": sem, "stream": stream}


def serialize_plan(result):
    """solve_schedule result dict (tuple-keyed) -> JSON friendly dict."""
    if result is None:
        return None
    out = {
        "feasible": bool(result.get("feasible")),
        "status": result.get("status"),
        "violations": result.get("violations") or [],
        "warnings": result.get("warnings") or [],
    }
    if not out["feasible"]:
        return out
    terms = []
    for (y, s) in sorted(result["plan_by_term"].keys()):
        courses = result["plan_by_term"][(y, s)]
        terms.append({
            "year": y, "semester": s,
            "label": "Summer Term" if s == 3 else f"Semester {s}",
            "total_credits": sum(c["credit_hours"] for c in courses),
            "courses": [{
                "code": c.get("code"), "name": c["name"],
                "credit_hours": c["credit_hours"],
                "cross_dept_note": c.get("cross_dept_note"),
            } for c in courses],
        })
    out.update({
        "graduation": result["graduation"],
        "exceeds_5_year_policy": bool(result.get("exceeds_5_year_policy")),
        "waivers": [w.get("name") for w in (result.get("fyp2_prereq_waivers") or [])],
        "terms": terms,
        "total_courses": sum(len(t["courses"]) for t in terms),
        "total_credits": sum(t["total_credits"] for t in terms),
    })
    try:
        out["explanation_html"] = telegram_html_to_web(build_explanation_message(result))
    except Exception:
        out["explanation_html"] = None
    return out


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------------------------------------------------------------------------
# accounts (same model as the bot: student ID = identity)
# ---------------------------------------------------------------------------
@app.route("/api/me")
def me():
    sid = current_student_id()
    if not sid:
        return jsonify({"ok": True, "student": None})
    db = Session()
    try:
        st = db.query(Student).filter_by(id=sid).first()
        if not st:
            flask_session.clear()
            return jsonify({"ok": True, "student": None})
        return jsonify({"ok": True, "student": student_payload(db, st)})
    finally:
        db.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    raw = (data.get("student_id") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}/\d{1,5}/\d{1,3}", raw):
        return err("Please enter a valid student ID, e.g. ETS/1444/13.")
    db = Session()
    try:
        st = db.query(Student).filter(func.upper(Student.id) == raw).first()
        created = False
        if not st:
            st = Student(id=raw, name=raw, status="ACTIVE")
            db.add(st)
            db.commit()
            created = True
        flask_session["student_id"] = st.id
        flask_session.permanent = True
        return jsonify({"ok": True, "created": created,
                        "student": student_payload(db, st)})
    finally:
        db.close()


@app.route("/api/logout", methods=["POST"])
def logout():
    flask_session.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# catalog & lookups
# ---------------------------------------------------------------------------
@app.route("/api/meta")
def meta():
    return jsonify({
        "ok": True, "streams": STREAMS,
        "grades": [{"grade": g, "points": p} for g, p in gc.GRADE_POINTS.items()],
    })


@app.route("/api/courses")
def courses():
    db = Session()
    try:
        cross = {cc.course_id for cc in db.query(CommonCourse).all()}
        rows = []
        for c in db.query(Course).order_by(Course.year_level,
                                           Course.semester_offered,
                                           Course.course_code).all():
            scope = query_api._resolve_course_stream_scope(db, c)
            streaming = query_api._stream_scope_is_relevant(c)
            if not streaming or scope is None:
                streams = None
            else:
                streams = sorted(
                    db.query(Stream).filter_by(id=i).first().name.replace(" Engineering", "")
                    for i in scope)
            rows.append({
                "code": c.course_code, "name": c.name,
                "credit_hours": c.credit_hours,
                "year": c.year_level, "semester": c.semester_offered,
                "streams": streams,               # None => common to everyone
                "streaming_period": streaming,
                "college_wide": c.department_id is None,
                "cross_department": c.id in cross,
            })
        return jsonify({"ok": True, "courses": rows})
    finally:
        db.close()


def _lookup(fn):
    q = (request.args.get("q") or "").strip()
    if not q:
        return err("Please provide a course code or name.")
    return jsonify({"ok": True, "html": telegram_html_to_web(fn(q))})


@app.route("/api/lookup/course")
def lookup_course():
    return _lookup(query_api.get_course_details_formatted)


@app.route("/api/lookup/dependants")
def lookup_dependants():
    return _lookup(query_api.get_dependant_courses_formatted)


@app.route("/api/lookup/downstream")
def lookup_downstream():
    return _lookup(query_api.get_downstream_impact_formatted)


@app.route("/api/lookup/cross-department")
def lookup_cross_department():
    return jsonify({"ok": True, "html": telegram_html_to_web(query_api.get_cross_department_formatted())})


@app.route("/api/lookup/cross-stream")
def lookup_cross_stream():
    return jsonify({"ok": True, "html": telegram_html_to_web(query_api.get_cross_stream_formatted())})


@app.route("/api/lookup/semester")
def lookup_semester():
    year, sem = request.args.get("year", ""), request.args.get("sem", "")
    stream = request.args.get("stream") or None
    return jsonify({"ok": True, "html": telegram_html_to_web(
        query_api.get_semester_courses_formatted(year, sem, stream))})


# ---------------------------------------------------------------------------
# course planning
# ---------------------------------------------------------------------------
@app.route("/api/plan", methods=["POST"])
def plan():
    sid = current_student_id()
    if not sid:
        return err("Please sign in with your student ID first.", 401)
    d = request.get_json(silent=True) or {}
    try:
        year, sem = int(d.get("year")), int(d.get("semester"))
    except (TypeError, ValueError):
        return err("Year and semester are required.")
    if year not in range(1, 6) or sem not in (1, 2, 3) or (sem == 3 and year != 4):
        return err("That year/semester combination doesn't exist (Year 4 alone has a 3rd term).")
    stream = d.get("stream") or None
    if stream and stream not in STREAMS:
        return err("Unknown stream.")
    if (year > 4 or (year == 4 and sem >= 2)) and not stream:
        stream = None  # undecided -> comparison, same as the bot

    failed, added, dropped = (list(d.get(k) or []) for k in ("failed", "added", "dropped"))

    db = Session()
    try:
        st = db.query(Student).filter_by(id=sid).first()
    finally:
        db.close()
    if not st:
        return err("Account not found, please sign in again.", 401)

    try:
        _text, raw = process_reasoning_request_full(
            st, year, sem, stream, failed, added, dropped)
    except Exception as e:  # noqa
        app.logger.exception("planning failed")
        return err(f"The planner hit an error: {e}", 500)

    if stream:
        return jsonify({"ok": True, "mode": "single", "stream": stream,
                        "plan": serialize_plan(raw)})
    comparison = []
    for name in STREAMS:
        r = raw.get(name)
        if r is None:
            continue
        comparison.append({"stream": name, "plan": serialize_plan(r)})
    return jsonify({"ok": True, "mode": "comparison", "streams": comparison})


# ---------------------------------------------------------------------------
# GPA tools
# ---------------------------------------------------------------------------
@app.route("/api/gpa/semester-courses")
def gpa_semester_courses():
    try:
        year, sem = int(request.args["year"]), int(request.args["sem"])
    except (KeyError, ValueError):
        return err("Year and semester are required.")
    if not gc.validate_year_sem(year, sem):
        return err("That semester doesn't exist.")
    stream = request.args.get("stream") or None
    db = Session()
    try:
        return jsonify({"ok": True,
                        "courses": gc.get_semester_courses(db, year, sem, stream)})
    finally:
        db.close()


@app.route("/api/gpa/sgpa", methods=["POST"])
def gpa_sgpa():
    d = request.get_json(silent=True) or {}
    pairs = []
    for row in d.get("courses") or []:
        try:
            pairs.append((int(row["credit_hours"]), row["grade"]))
        except (KeyError, ValueError, TypeError):
            return err("Each course needs credit hours and a grade.")
    try:
        return jsonify({"ok": True, "sgpa": gc.compute_sgpa(pairs),
                        "credits": sum(p[0] for p in pairs)})
    except ValueError as e:
        return err(str(e))


@app.route("/api/gpa/forecast", methods=["POST"])
def gpa_forecast():
    """Required-SGPA (goal) or CGPA projection (what-if), same maths as /gpa."""
    d = request.get_json(silent=True) or {}
    try:
        cy, cs = int(d["year"]), int(d["semester"])
        ey, es = int(d["end_year"]), int(d["end_semester"])
        cgpa = float(d["cgpa"])
    except (KeyError, ValueError, TypeError):
        return err("Please fill in every field.")
    if not (0.0 <= cgpa <= 4.0):
        return err("CGPA must be between 0.00 and 4.00.")
    stream = d.get("stream") or None
    mode = d.get("mode", "goal")

    db = Session()
    try:
        try:
            window = gc.semesters_in_window(cy, cs, ey, es)
            before = gc.semesters_before(cy, cs)
        except ValueError as e:
            return err(str(e))
        if (gc._is_streaming_period(cy, cs) or gc._is_streaming_period(ey, es)) and not stream:
            return err("Please pick your stream — your range includes stream-specific semesters.")

        def course_credits(codes):
            total = 0
            for code in codes or []:
                c = db.query(Course).filter(Course.course_code.ilike(code)).first()
                if c:
                    total += c.credit_hours
            return total

        normal_prev = sum(gc.semester_total_credits(db, y, s, stream) for (y, s) in before)
        prev_credits = max(0, normal_prev - course_credits(d.get("past_dropped"))
                           + course_credits(d.get("past_added")))
        if d.get("prev_credits_override") not in (None, ""):
            prev_credits = int(d["prev_credits_override"])

        oob = d.get("oob_credits") or {}
        overrides = d.get("overrides") or {}
        credits_by_sem = []
        for (y, s) in window:
            token = gc.format_semester_token(y, s)
            if gc.is_out_of_batch(y, s):
                cr = int(oob.get(token) or 0)
                if cr <= 0:
                    return err(f"Enter the credit hours you expect in {token} (beyond the 5-year curriculum).")
            elif overrides.get(token) not in (None, ""):
                cr = int(overrides[token])
            else:
                cr = gc.semester_total_credits(db, y, s, stream)
            credits_by_sem.append({"token": token, "year": y, "semester": s,
                                   "credits": cr,
                                   "out_of_batch": gc.is_out_of_batch(y, s)})
        credits = [c["credits"] for c in credits_by_sem]
        if sum(credits) <= 0:
            return err("Total future credit hours must be greater than zero.")

        resp = {"ok": True, "prev_credits": prev_credits, "semesters": credits_by_sem,
                "current_cgpa": round(cgpa, 2)}
        if mode == "goal":
            try:
                goal = float(d["goal_cgpa"])
            except (KeyError, ValueError, TypeError):
                return err("Enter your goal CGPA.")
            if not (0.0 <= goal <= 4.0):
                return err("Goal CGPA must be between 0.00 and 4.00.")
            r = gc.compute_required_sgpa(prev_credits, cgpa, credits, goal)
            resp.update({"mode": "goal", "goal_cgpa": goal, **r})
        else:
            try:
                sgpas = [float(x) for x in d.get("sgpas") or []]
                proj = gc.compute_cgpa_projection(prev_credits, cgpa, credits, sgpas)
            except (ValueError, TypeError) as e:
                return err(str(e))
            for row, c in zip(proj, credits_by_sem):
                row["token"] = c["token"]
            resp.update({"mode": "whatif", "projection": proj})
        return jsonify(resp)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# feedback
# ---------------------------------------------------------------------------
@app.route("/api/feedback", methods=["POST"])
def feedback():
    sid = current_student_id()
    if not sid:
        return err("Please sign in with your student ID first.", 401)
    d = request.get_json(silent=True) or {}
    try:
        rating = int(d.get("rating"))
    except (TypeError, ValueError):
        return err("Choose a rating from 1 to 5.")
    if rating not in range(1, 6):
        return err("Choose a rating from 1 to 5.")
    comment = (d.get("comment") or "").strip()[:1000] or None
    db = Session()
    try:
        db.add(UserFeedback(student_id=sid, bot_response_context="web",
                            rating=rating, comments=comment))
        db.commit()
        return jsonify({"ok": True})
    except Exception:
        db.rollback()
        return err("Could not save feedback.", 500)
    finally:
        db.close()


if __name__ == "__main__":
    app.run(debug=True, port=5002)
