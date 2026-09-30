"""
explain_schedule.py
=====================
Explainability layer for the reasoning engine. PURE post-processing: it
reads a finished plan and re-checks it against the same rules the solver
used, to describe WHY the plan looks the way it does. The solver
(model_stage8_fixed.py) is never touched or re-run. No DB, no Telegram.

The explanation has two parts:

  1. LEVERAGES USED -- every special mechanism the plan relies on:
       - cross-department offerings (a course taken through the partner
         department's flipped semester),
       - the informal FYP-II prerequisite waiver,
       - the Year-5 credit overload (up to 22 cr),
       - crowd-relief load balancing (the ONLY place load balancing is
         ever claimed -- see solve_schedule.CROWD_RELIEF_ELIGIBLE_COURSES).
  2. COURSES MOVED -- per-course "why here?" for every future course NOT
     at its natural (year, semester) slot. On-track courses produce
     nothing, so the output never gets cluttered.

Public entry point for the bot:  build_explanation_message(result) -> HTML str
where `result` is a feasible solve_schedule_for_student() result carrying
an "explain_context" entry.

HONEST LIMITS (also reflected in the wording):
  - Credit-cap explanations describe THIS plan. Another equally fast plan
    could place the other courses differently, so we say "in this plan".
  - If no rule blocks an earlier slot, the solver's tie-break tiers or the
    crowd-relief pass moved the course. The message says so instead of
    inventing a constraint.
"""

import html as html_lib

from model_stage8_fixed import (
    slot_to_year_sem, natural_slot_for_course, stream_enrollment_floor_slot,
    cap_for_slot, _is_major_course,
)

SEMESTER_TYPES = (1, 2, 3)
MAX_MESSAGE_CHARS = 3900        # Telegram hard limit is 4096; keep headroom
MAX_OCCUPANTS_SHOWN = 4


def _esc(text):
    return html_lib.escape(str(text), quote=False)


# ---------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------

class ExplainContext:
    """Everything the explainer needs, precomputed once."""

    def __init__(self, schedule, courses, completed, now_slot, normal_caps, waived=(), solver_schedule=None):
        self.schedule = dict(schedule)
        # Courses crowd relief moved: {code: slot the solver had chosen}.
        self.relief_from = {}
        if solver_schedule:
            self.relief_from = {c: solver_schedule[c] for c, s in self.schedule.items()
                                if c in solver_schedule and solver_schedule[c] != s}
        self.courses = courses
        self.completed = completed or {}
        self.now_slot = now_slot
        self.caps = normal_caps or {}
        self.waived = set(waived or ())

        self.natural = {c: natural_slot_for_course(i, SEMESTER_TYPES) for c, i in courses.items()}
        self.stream_floor = stream_enrollment_floor_slot(SEMESTER_TYPES)

        # Credit load per FUTURE slot (history is not the solver's business).
        self.load = {}
        self.occupants = {}
        for c, s in self.schedule.items():
            if s > now_slot:
                self.load[s] = self.load.get(s, 0) + courses[c]["credit_hours"]
                self.occupants.setdefault(s, []).append(c)

        # Year-5 overload is "used" in any year-5 slot exceeding its normal cap.
        self.overloaded_slots = sorted(
            (s, load, self.caps[slot_to_year_sem(s, SEMESTER_TYPES)])
            for s, load in self.load.items()
            if slot_to_year_sem(s, SEMESTER_TYPES)[0] == 5
            and load > self.caps.get(slot_to_year_sem(s, SEMESTER_TYPES), 0) > 0
        )
        self.overload_used = bool(self.overloaded_slots)

    # -- small helpers -------------------------------------------------
    def name(self, code):
        return self.courses[code]["name"]

    def retaken(self, code):
        return any(r.get("status") in ("FAILED", "DROPPED") for r in self.completed.get(code, []))

    def failed(self, code):
        return any(r.get("status") == "FAILED" for r in self.completed.get(code, []))

    def is_future(self, code):
        return code in self.schedule and self.schedule[code] > self.now_slot

    def cap(self, slot):
        year, sem = slot_to_year_sem(slot, SEMESTER_TYPES)
        normal = self.caps.get((year, sem), 0)
        override = 22 if self.overload_used else normal
        return cap_for_slot(slot, self.caps, SEMESTER_TYPES, year5_override=override)

    def parities(self, code):
        info = self.courses[code]
        allowed = {info["semester_offered"]}
        if info.get("alt_parity"):
            allowed.add(info["alt_parity"])
        return allowed

    def is_legal_parity(self, code, slot):
        return slot_to_year_sem(slot, SEMESTER_TYPES)[1] in self.parities(code)

    def first_legal_at_or_after(self, code, lo, hi=200):
        for s in range(max(lo, self.now_slot + 1), hi):
            if self.is_legal_parity(code, s):
                return s
        return None


def _flip_usage(ctx, code):
    """If `code` sits in its partner-department (flipped) semester, return
    {dept, sem, home_sem, home_slot}; home_slot is the earliest slot the
    home-parity offering alone would have allowed (None if not applicable)."""
    info = ctx.courses[code]
    slot = ctx.schedule[code]
    alt, home = info.get("alt_parity"), info["semester_offered"]
    _, sem = slot_to_year_sem(slot, SEMESTER_TYPES)
    if not alt or sem != alt or sem == home:
        return None
    nat = ctx.natural[code]
    lo = ctx.now_slot + 1 if (ctx.retaken(code) or nat <= ctx.now_slot) else nat
    home_slot = None
    for s in range(lo, 200):
        if slot_to_year_sem(s, SEMESTER_TYPES)[1] == home:
            home_slot = s
            break
    return {"dept": info.get("alt_parity_department"), "sem": sem,
            "home_sem": home, "home_slot": home_slot}


def _term(slot):
    year, sem = slot_to_year_sem(slot, SEMESTER_TYPES)
    return f"Year {year}, Summer" if sem == 3 else f"Year {year}, Sem {sem}"


# ---------------------------------------------------------------------
# Feature 1: per-course "why here?"
# ---------------------------------------------------------------------

def _root_retake(ctx, code, _seen=None):
    """Follow a delayed prerequisite chain down to the retaken course that
    started it. Returns that course's code, or None if the delay isn't
    rooted in a retake."""
    _seen = _seen if _seen is not None else set()
    if code in _seen or not ctx.is_future(code):
        return None
    _seen.add(code)
    if ctx.retaken(code):
        return code
    if ctx.schedule[code] > ctx.natural[code]:
        late_prereqs = sorted(
            (p for p in ctx.courses[code].get("prereqs", []) if ctx.is_future(p)),
            key=lambda p: -ctx.schedule[p],
        )
        for p in late_prereqs:
            root = _root_retake(ctx, p, _seen)
            if root:
                return root
    return None


def _blocker(ctx, code, s):
    """First rule that stops `code` from sitting in candidate slot `s`
    (checked against the FINAL schedule), or None if nothing blocks it."""
    info = ctx.courses[code]

    # Stream enrollment floor
    if info.get("streams") is not None and s < ctx.stream_floor:
        return {"kind": "stream", "text": "stream courses can't start before Year 4, Sem 2, when stream enrollment happens"}

    # Prerequisites
    late = [(ctx.schedule[p], p) for p in info.get("prereqs", [])
            if p in ctx.schedule and ctx.schedule[p] >= s]
    if late:
        p_slot, p = max(late)
        text = f"waits for {ctx.name(p)} (finishes {_term(p_slot)})"
        root = _root_retake(ctx, p)
        if root == p:
            text = f"waits for {ctx.name(p)}, which you're retaking (finishes {_term(p_slot)})"
        elif root:
            text += f", which is itself waiting on your {ctx.name(root)} retake"
        return {"kind": "prereq", "text": text}

    special = info.get("special_requirement")

    # FYP-II style gate: every major peer strictly before (or same-slot for
    # a peer with the same natural slot) -- mirrors model_stage8_fixed.
    if special == "ALL_STREAM_COURSES":
        c_nat = ctx.natural[code]
        viol = []
        for c2, i2 in ctx.courses.items():
            if c2 == code or c2 not in ctx.schedule or c2 in ctx.waived:
                continue
            if i2.get("special_requirement") == "ALL_COURSES" or not _is_major_course(i2):
                continue
            same = ctx.natural[c2] == c_nat
            bound = ctx.schedule[c2] if same else ctx.schedule[c2] + 1
            if s < bound:
                viol.append((ctx.schedule[c2], c2, same))
        if viol:
            v_slot, v, same = max(viol)
            if same:
                text = (f"runs in the final semester together with {ctx.name(v)} ({_term(v_slot)}), "
                        f"so it can't be earlier than that")
            else:
                text = (f"starts only after all your stream-period courses are finished; "
                        f"the last one is {ctx.name(v)} ({_term(v_slot)})")
            return {"kind": "gate", "text": text}

    # NEE style gate: after everything else
    if special == "ALL_COURSES":
        others = [(sl, c2) for c2, sl in ctx.schedule.items() if c2 != code]
        if others:
            mx_slot, mx = max(others)
            if s < mx_slot:
                return {"kind": "gate",
                        "text": (f"is taken only after every other course is finished; "
                                 f"the last one is {ctx.name(mx)} ({_term(mx_slot)})")}

    # Credit cap (this plan's placement of everyone else)
    load_here = ctx.load.get(s, 0)
    cap = ctx.cap(s)
    if load_here + info["credit_hours"] > cap:
        names = sorted(ctx.name(c2) for c2 in ctx.occupants.get(s, [])
                       if ctx.courses[c2]["credit_hours"] > 0)
        shown = names[:MAX_OCCUPANTS_SHOWN]
        extra = len(names) - len(shown)
        occupants = ", ".join(shown) + (f" and {extra} more" if extra > 0 else "")
        return {"kind": "cap",
                "text": (f"{_term(s)} was full in this plan ({load_here}/{cap} cr: {occupants})")}

    return None


def explain_course_placements(ctx):
    """Returns a list of {code, name, slot, kind, text}, sorted by slot then
    name, for every future course NOT at its natural slot."""
    out = []
    for code, slot in ctx.schedule.items():
        if slot <= ctx.now_slot:
            continue
        info = ctx.courses[code]
        nat = ctx.natural[code]
        if code in ctx.relief_from:
            out.append({"code": code, "name": info["name"], "slot": slot, "kind": "relief",
                        "text": (f"load balancing: moved from {_term(ctx.relief_from[code])} to even out "
                                 f"the number of courses per semester")})
            continue
        if slot == nat:
            continue

        retaken = ctx.retaken(code)
        prefix = "Retake: " if ctx.failed(code) else ("Postponed earlier: " if retaken else "")

        if slot < nat:
            _, sem = slot_to_year_sem(slot, SEMESTER_TYPES)
            if info.get("alt_parity") and sem == info["alt_parity"] and sem != info["semester_offered"]:
                dept = info.get("alt_parity_department")
                who = f"the {dept} department's" if dept else "the partner department's"
                text = f"taken earlier than normal through {who} offering"
                kind = "flip"
            else:
                text = "taken ahead of its normal semester; nothing required this, an earlier semester simply had room"
                kind = "early"
            out.append({"code": code, "name": info["name"], "slot": slot, "kind": kind, "text": text})
            continue

        # slot > natural: why not earlier?
        lo = ctx.now_slot + 1 if (retaken or nat <= ctx.now_slot) else nat
        candidates = [s for s in range(lo, slot) if ctx.is_legal_parity(code, s)]

        if not candidates:
            flip = _flip_usage(ctx, code)
            if flip and flip["home_slot"] and flip["home_slot"] > slot:
                who = f"the {flip['dept']} department's" if flip["dept"] else "the partner department's"
                text = f"taken in Sem {flip['sem']} through {who} offering (see Leverages above)"
                kind = "flip"
            else:
                text = "this is the earliest semester it's offered after your current one"
                kind = "parity"
        else:
            b = _blocker(ctx, code, candidates[0])
            if b:
                text, kind = b["text"], b["kind"]
            else:
                text = "no rule forced this; the planner's tie-break preferences placed it here"
                kind = "tiebreak"

        if prefix:
            text = prefix.rstrip(": ") + (" — " if candidates else ": ") + text
            text = text[0].upper() + text[1:] if text else text
        out.append({"code": code, "name": info["name"], "slot": slot, "kind": kind, "text": text})

    out.sort(key=lambda e: (e["slot"], e["name"]))
    return out


# ---------------------------------------------------------------------
# Leverages
# ---------------------------------------------------------------------

def collect_leverages(ctx, entries, waivers):
    """Every special mechanism this plan relies on, as display-ready dicts."""
    out = {"flips": [], "waivers": [], "overloads": [], "balanced": []}

    for code in ctx.schedule:
        if not ctx.is_future(code):
            continue
        f = _flip_usage(ctx, code)
        if f:
            out["flips"].append({"code": code, "name": ctx.name(code), "slot": ctx.schedule[code], **f})
    out["flips"].sort(key=lambda x: (x["slot"], x["name"]))

    out["waivers"] = [w["name"] for w in (waivers or [])]

    for s, load, normal in ctx.overloaded_slots:
        out["overloads"].append({"slot": s, "load": load, "normal": normal})

    out["balanced"] = [e for e in entries if e["kind"] == "relief"]
    return out


def _has_any(lev):
    return any(lev[k] for k in lev)


def _format_leverages(lev):
    lines = ["⚡ <b><u>Leverages used in your plan</u></b>"]

    if lev["flips"]:
        lines += ["", "🔀 <b>Cross-department offering</b>"]
        for f in lev["flips"]:
            who = _esc(f["dept"]) if f["dept"] else "partner"
            line = (f"• <b>{_esc(f['name'])}</b> → <u>{_term(f['slot'])}</u>, "
                    f"taken with the <b>{who}</b> department")
            if f["home_slot"] and f["home_slot"] > f["slot"]:
                line += (f"\n   <i>Waiting for the home Sem {f['home_sem']} offering would have "
                         f"pushed it to {_term(f['home_slot'])}.</i>")
            lines.append(line)

    if lev["waivers"]:
        names = [f"<b>{_esc(n)}</b>" for n in lev["waivers"]]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        lines += ["", "📝 <b>Prerequisite waiver for Final Year Project II</b>",
                  f"• {joined} {'is' if len(names) == 1 else 'are'} not required to be finished first.",
                  "   <i>An informal departmental accommodation, not a written rule — "
                  "confirm it with the department office.</i>"]

    if lev["overloads"]:
        lines += ["", "📈 <b>Year 5 credit overload</b>"]
        for o in lev["overloads"]:
            lines.append(f"• <u>{_term(o['slot'])}</u>: <b>{o['load']} cr</b> "
                         f"<i>(normal {o['normal']}, +{o['load'] - o['normal']}; maximum 22)</i>")

    if lev["balanced"]:
        lines += ["", "⚖️ <b>Load balancing</b>"]
        for e in lev["balanced"]:
            lines.append(f"• <b>{_esc(e['name'])}</b> → <u>{_term(e['slot'])}</u> "
                         f"<i>({_esc(e['text'])})</i>")
    return lines


def _format_moved(entries, limit):
    lines = ["🧩 <b><u>Courses moved from their normal semester</u></b>"]
    for e in entries[:limit]:
        lines.append(f"\n• <b>{_esc(e['name'])}</b> → <u>{_term(e['slot'])}</u>\n"
                     f"   <i>{_esc(e['text'][0].upper() + e['text'][1:])}.</i>")
    hidden = len(entries) - limit
    if hidden > 0:
        lines.append(f"\n…and {hidden} more.")
    return lines


def build_explanation_message(result):
    """HTML message explaining ONE feasible solve result. Safe to send as a
    separate Telegram message (kept under MAX_MESSAGE_CHARS)."""
    ec = (result or {}).get("explain_context")
    if not result or not result.get("feasible") or not ec:
        return "ℹ️ There's no plan to explain yet."

    ctx = ExplainContext(ec["schedule"], ec["courses"], ec["completed"],
                          ec["now_slot"], ec["normal_caps"], ec.get("waived", ()),
                          ec.get("solver_schedule"))
    entries = explain_course_placements(ctx)
    lev = collect_leverages(ctx, entries, result.get("fyp2_prereq_waivers"))

    header = ["🧭 <b>Why this plan?</b>", ""]

    if not entries and not _has_any(lev):
        return "\n".join(header + ["✅ Your plan follows the standard timeline. "
                                   "No courses were moved and no special rules were needed."])

    # Balance-only entries are already shown under "Load balancing".
    moved = [e for e in entries if e["kind"] != "relief"]

    lev_lines = _format_leverages(lev) if _has_any(lev) else []
    limit = len(moved)
    while True:
        parts = header + lev_lines
        if moved:
            parts += ([""] if lev_lines else []) + _format_moved(moved, limit)
        msg = "\n".join(parts).rstrip()
        if len(msg) <= MAX_MESSAGE_CHARS or limit <= 1:
            return msg
        limit -= 1
