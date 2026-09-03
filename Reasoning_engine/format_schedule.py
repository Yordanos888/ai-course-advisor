"""
format_schedule.py
====================
Turns solve_schedule.py's result dicts into Telegram-ready text, in the
same Markdown style the existing orchestrator.py/bot.py already use
(*bold* course codes, bullet/summer emoji markers).

Replaces the OLD orchestrator.py's _format_plan_lines / _format_recovery_result
/ _format_stream_comparison. Key differences from those, and why:

  - No DB session needed at format time. solve_schedule.py already
    resolves course code/name/credit_hours into plain dicts, so this
    file only ever touches result dicts, never the database.

  - The old _slot_to_year_sem assumed 2 semester-types per year (its
    formula: `(year-1)*2 + (sem-1)`), which silently mis-renders any
    plan touching a summer slot -- exactly the case that matters most
    (the Internship). solve_schedule.py's output is already correctly
    converted (via model_stage8_fixed.slot_to_year_sem, 3 semester-types)
    before it ever reaches this file, so there's no conversion bug
    surface here at all -- this file only ever prints (year, sem) pairs
    it's handed.

  - Infeasible results are no longer a single "reason" string -- they
    carry a status (RETAKE_LIMIT_EXCEEDED / CAPSTONE_UNSCHEDULABLE /
    plain INFEASIBLE) plus a violations list, each rendered with
    different, more specific guidance.

  - A feasible result can still legitimately exceed the 5-year policy
    window (per the explicit design decision in Stage 8: always return
    a real plan, flagged honestly, rather than a bare "no solution").
    That flag is surfaced prominently, not buried.

DESIGN CHOICE WORTH FLAGGING: the stream-comparison view is INTENTIONALLY
condensed (headline per stream: graduation timeline + credit-hour-based
plan length, not a full term-by-term dump for all 4 streams). A full
real curriculum plan can span 10+ terms and 60+ courses; four of those
side by side would very likely blow past Telegram's ~4096-character
message limit. Once the student picks a stream, format_schedule_result()
gives the full breakdown for just that one.
"""

from solve_schedule import ALL_STREAMS


def _format_term_line(year, sem, courses, show_credits=True):
    if show_credits:
        code_strs = [f"*{c['code']}* ({c['credit_hours']} cr)" for c in courses]
    else:
        code_strs = [f"*{c['code']}*" for c in courses]
    tag = "☀️" if sem == 3 else "•"
    label = f"Sem 3 (Summer)" if sem == 3 else f"Sem {sem}"
    return f"{tag} Year {year}, {label}: " + ", ".join(code_strs)


def _format_graduation_line(result):
    grad = result["graduation"]
    line = f"🎓 Expected graduation: Year {grad['year']}, Semester {grad['semester']}"
    if result["exceeds_5_year_policy"]:
        line += "\n⚠️ This plan takes longer than the standard 5-year timeline."
    return line


def _format_warnings(warnings):
    if not warnings:
        return []
    lines = ["", "ℹ️ _Notes:_"]
    for w in warnings:
        lines.append(f"  - {w}")
    return lines


def _format_infeasible(result, header):
    lines = [header]
    status = result.get("status")
    violations = result.get("violations") or []

    if status == "RETAKE_LIMIT_EXCEEDED":
        lines.append("You've used all your allowed attempts for at least one required course:")
        for v in violations:
            lines.append(f"  - {v}")
        lines.append("\nPlease contact the department office to discuss your options.")
    elif status == "CAPSTONE_UNSCHEDULABLE":
        lines.append("A final requirement (e.g. the National Exit Exam) couldn't be placed within the planning window:")
        for v in violations:
            lines.append(f"  - {v}")
    else:
        lines.append("No valid path could be found with the information provided.")
        for v in violations:
            lines.append(f"  - {v}")

    lines.extend(_format_warnings(result.get("warnings")))
    return "\n".join(lines)


def format_schedule_result(result, header="🎯 *Your Course Plan*", infeasible_header="❌ *No Feasible Plan Found*"):
    """Full term-by-term rendering for ONE student's plan (a single,
    already-decided stream)."""
    if not result["feasible"]:
        return _format_infeasible(result, infeasible_header)

    lines = [header, _format_graduation_line(result), ""]
    for year, sem in sorted(result["plan_by_term"].keys()):
        lines.append(_format_term_line(year, sem, result["plan_by_term"][(year, sem)]))
    lines.extend(_format_warnings(result.get("warnings")))
    return "\n".join(lines)


def format_stream_comparison(comparisons, stream_order=None, preview_terms=2):
    """
    Condensed comparison across streams (see module docstring for why).
    preview_terms: how many of the SOONEST upcoming terms to preview per
    stream, so the student gets a taste of what's next without a full dump.
    """
    stream_order = stream_order or ALL_STREAMS
    lines = [
        "⚖️ *Stream Recovery Comparison*",
        "You haven't selected a stream yet -- here's how each one looks:\n",
    ]

    for stream in stream_order:
        r = comparisons.get(stream)
        if r is None:
            continue

        if not r["feasible"]:
            lines.append(f"❌ *{stream}* — no feasible plan ({r.get('status', 'INFEASIBLE')})")
            lines.append("")
            continue

        grad = r["graduation"]
        grad_note = f"Year {grad['year']}, Sem {grad['semester']}"
        if r["exceeds_5_year_policy"]:
            grad_note += " ⚠️"
        total_courses = sum(len(v) for v in r["plan_by_term"].values())
        lines.append(f"🛠️ *{stream}* — graduates {grad_note}  ({total_courses} courses remaining)")

        upcoming_terms = sorted(r["plan_by_term"].keys())[:preview_terms]
        for year, sem in upcoming_terms:
            lines.append("   " + _format_term_line(year, sem, r["plan_by_term"][(year, sem)], show_credits=False))
        lines.append("")

    lines.append("_Reply with your chosen stream to see the full plan._")
    return "\n".join(lines)
