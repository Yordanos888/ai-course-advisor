# academic_intake.py
import re
import datetime
from sqlalchemy.orm import sessionmaker
from models import engine, Batch, CurriculumVersion

Session = sessionmaker(bind=engine)
session = Session()


def parse_year_semester(text_reply: str):
    """Extracts (year_ordinal, semester_ordinal) e.g. ('4th', '2nd') from free text."""
    text_lower = text_reply.lower()
    digit_to_ordinal = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th", "5": "5th"}
    year = semester = None

    year_match = re.search(r'\b(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth)\s*[- ]?\s*year\b', text_lower)
    if year_match:
        m = {"1st": "1st", "first": "1st", "2nd": "2nd", "second": "2nd", "3rd": "3rd", "third": "3rd",
             "4th": "4th", "fourth": "4th", "5th": "5th", "fifth": "5th"}
        year = m[year_match.group(1)]
    else:
        nm = re.search(r'\byear\s*(\d)\b', text_lower)
        if nm:
            year = digit_to_ordinal[nm.group(1)]

    sem_match = re.search(r'\b(1st|first|2nd|second)\s*[- ]?\s*sem(?:ester)?\b', text_lower)
    if sem_match:
        m = {"1st": "1st", "first": "1st", "2nd": "2nd", "second": "2nd"}
        semester = m[sem_match.group(1)]
    else:
        nm = re.search(r'\bsem(?:ester)?\s*(1|2)\b', text_lower)
        if nm:
            semester = "1st" if nm.group(1) == "1" else "2nd"

    return year, semester


def assign_batch_from_reply(student, text_reply: str, department_id: int):
    """
    Find-or-create the Batch matching a self-reported year/semester, and assign
    it to the student. entry_year is approximated (current calendar year minus
    year_level - 1) since it isn't asked directly -- fine for now since nothing
    downstream depends on its precision yet; worth revisiting once Phase 6
    (retake-direction logic) needs it.
    """
    year_ord, sem_ord = parse_year_semester(text_reply)
    if not year_ord or not sem_ord:
        return False, "I couldn't understand your year and semester. Please reply like '4th year 2nd semester'."

    year_num = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}[year_ord]
    sem_num = 1 if sem_ord == "1st" else 2
    approx_entry_year = datetime.date.today().year - (year_num - 1)

    batch = session.query(Batch).filter_by(
        department_id=department_id, current_year_level=year_num, current_semester=sem_num
    ).first()
    if not batch:
        cv = session.query(CurriculumVersion).filter_by(department_id=department_id, is_active=True).first()
        batch = Batch(department_id=department_id, entry_year=approx_entry_year,
                       current_year_level=year_num, current_semester=sem_num,
                       curriculum_version_id=cv.id)
        session.add(batch)
        session.flush()

    student.batch_id = batch.id
    session.commit()
    return True, f"Got it -- {year_ord} year, {sem_ord} semester."