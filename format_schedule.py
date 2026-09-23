"""
format_schedule.py
====================
Turns solve_schedule.py's result dicts into Telegram-ready text using
HTML parse mode (not Markdown -- Telegram's legacy Markdown has no
underline support at all, and MarkdownV2's escaping rules are much more
error-prone than HTML's tiny escape set, since course names/notes are
free-form text coming from the database).

DESIGN CHOICES PER EXPLICIT FEEDBACK:
  - Course PLAN listings show NAMES ONLY, no course codes -- codes add
    lookup value for commands like /course, but clutter a plan a student
    is meant to just read through. (Other commands like /course and
    /semester keep codes, since those ARE lookup-oriented -- see
    query_api.py.)
  - Real numbered lists per term, not a single comma-joined line -- a
    term with 5+ courses read as one run-on sentence was hard to scan.
  - Bold term headers (with the semester underlined for emphasis),
    spacing between terms, and a clearly bolded graduation line.

No DB session needed here -- solve_schedule.py already resolves
code/name/credit_hours into plain dicts before this file ever sees them.
"""

import html as html_lib
from solve_schedule import ALL_STREAMS


def _esc(text):
    """HTML-escape any free-form text (course names, warnings) before
    interpolating it into a message -- cheap insurance against a name
    that happens to contain &, <, or >."""
    return html_lib.escape(str(text))


def _format_term_block(year, sem, courses):
    label = "Summer Term" if sem == 3 else f"Semester {sem}"
    tag = "☀️" if sem == 3 else "📖"
    lines = [f"{tag} <b><u>Year {year}, {label}</u></b>"]
    for i, c in enumerate(courses, 1):
        line = f"{i}. {_esc(c['name'])} <i>({c['credit_hours']} cr)</i>"
        if c.get("cross_dept_note"):
            line += f" <b>[with {_esc(c['cross_dept_note'])} department]</b>"
        lines.append(line)
    return "\n".join(lines)


def _format_graduation_line(result):
    grad = result["graduation"]
    line = f"🎓 <b>Expected Graduation:</b> Year {grad['year']}, Semester {grad['semester']}"
    if result["exceeds_5_year_policy"]:
        line += "\n⚠️ <b>This plan takes longer than the standard 5-year timeline.</b>"
    return line


def _format_warnings(warnings):
    if not warnings:
        return []
    lines = ["", "ℹ️ <b>Notes:</b>"]
    for w in warnings:
        lines.append(f"• {_esc(w)}")
    return lines


def _format_fyp2_waiver_note(waivers):
    """Explains a FYP2_PREREQ_WAIVER escalation to the student. This is
    NOT a formal curriculum rule -- it's an informal accommodation the
    department applies in practice, only when needed to keep a student
    on the 5-year timeline -- so it must always be called out explicitly
    and never blended in with ordinary notes/warnings, and the student
    is pointed to the department office rather than left to assume it's
    guaranteed."""
    if not waivers:
        return []
    names = [_esc(w["name"]) for w in waivers]
    if len(names) == 1:
        names_str = names[0]
    else:
        names_str = ", ".join(names[:-1]) + " and " + names[-1]
    lines = [
        "",
        "📌 <b>Practical exception applied:</b>",
        (
            f"To keep you on the standard 5-year timeline, this plan does NOT require "
            f"{names_str} to be finished before your Final Year Project II -- this is a "
            f"practical accommodation the department sometimes allows, not a formal "
            f"curriculum rule, so please confirm it with the department office before "
            f"relying on it."
        ),
    ]
    return lines


def _format_infeasible(result, header):
    lines = [header, ""]
    status = result.get("status")
    violations = result.get("violations") or []

    if status == "RETAKE_LIMIT_EXCEEDED":
        lines.append("You've used all your allowed attempts for at least one required course:")
        for v in violations:
            lines.append(f"• {_esc(v)}")
        lines.append("\nPlease contact the department office to discuss your options.")
    elif status == "CAPSTONE_UNSCHEDULABLE":
        lines.append("A final requirement (e.g. the National Exit Exam) couldn't be placed within the planning window:")
        for v in violations:
            lines.append(f"• {_esc(v)}")
    else:
        lines.append("No valid path could be found with the information provided.")
        for v in violations:
            lines.append(f"• {_esc(v)}")

    lines.extend(_format_warnings(result.get("warnings")))
    return "\n".join(lines)


def format_schedule_result(result, header="🎯 <b>Your Course Plan</b>", infeasible_header="❌ <b>No Feasible Plan Found</b>"):
    """Full term-by-term rendering for ONE student's plan (a single,
    already-decided stream)."""
    if not result["feasible"]:
        return _format_infeasible(result, infeasible_header)

    lines = [header, "", _format_graduation_line(result), ""]
    lines_extra = _format_fyp2_waiver_note(result.get("fyp2_prereq_waivers"))
    if lines_extra:
        lines.extend(lines_extra)
        lines.append("")
    for year, sem in sorted(result["plan_by_term"].keys()):
        lines.append(_format_term_block(year, sem, result["plan_by_term"][(year, sem)]))
        lines.append("")
    lines.extend(_format_warnings(result.get("warnings")))
    return "\n".join(lines).rstrip()


def format_stream_comparison(comparisons, stream_order=None):
    """
    Condensed comparison across streams: timeline only, NO course listing
    at all -- a full term-by-term dump for all 4 streams risks a huge,
    unreadable message on a real curriculum, and the student hasn't
    committed to a stream yet anyway. Once they pick one,
    format_schedule_result() gives the full breakdown.
    """
    stream_order = stream_order or ALL_STREAMS
    lines = [
        "⚖️ <b>Stream Recovery Comparison</b>",
        "You haven't selected a stream yet — here's how each one looks:",
        "",
    ]

    for stream in stream_order:
        r = comparisons.get(stream)
        if r is None:
            continue

        if not r["feasible"]:
            lines.append(f"❌ <b>{_esc(stream)}</b> — no feasible plan ({_esc(r.get('status', 'INFEASIBLE'))})")
            lines.append("")
            continue

        grad = r["graduation"]
        grad_note = f"Year {grad['year']}, Sem {grad['semester']}"
        if r["exceeds_5_year_policy"]:
            grad_note += " ⚠️"
        waiver_note = " 📌" if r.get("fyp2_prereq_waivers") else ""
        remaining_terms = len(r["plan_by_term"])
        total_courses = sum(len(v) for v in r["plan_by_term"].values())
        lines.append(
            f"🛠️ <b>{_esc(stream)}</b>\n"
            f"    🎓 Graduates: {grad_note}{waiver_note}\n"
            f"    <i>{remaining_terms} term(s) remaining, {total_courses} course(s) total</i>"
        )
        lines.append("")

    if any((comparisons.get(s) or {}).get("fyp2_prereq_waivers") for s in stream_order):
        lines.append(
            "📌 <i>This stream's timeline relies on a practical departmental "
            "exception, not a formal rule -- see the full plan for details.</i>"
        )
        lines.append("")

    lines.append("<i>Reply with your chosen stream (e.g. \"Control\") to see the full plan.</i>")
    return "\n".join(lines)
