# student_service.py
from sqlalchemy.orm import sessionmaker
from models import engine, Student, Batch, Stream

Session = sessionmaker(bind=engine)
session = Session()

_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


def get_student_by_telegram_id(telegram_user_id: str):
    """Returns the linked Student record for this Telegram user, or None if unlinked."""
    return session.query(Student).filter_by(telegram_user_id=str(telegram_user_id)).first()


def link_telegram_account(telegram_user_id: str, official_student_id: str):
    """
    Links a Telegram user to a real student record by their official student ID
    (e.g. 'ETS/1234/14'). Returns (success: bool, message: str).
    """
    student = session.query(Student).filter_by(id=official_student_id.strip()).first()
    if not student:
        return False, f"No student found with ID '{official_student_id}'. Please double-check it."

    existing = get_student_by_telegram_id(telegram_user_id)
    if existing and existing.id != student.id:
        return False, "This Telegram account is already linked to a different student ID."

    student.telegram_user_id = str(telegram_user_id)
    session.commit()
    return True, f"Linked successfully to {student.name} ({student.id})."


def get_derived_profile(student: Student) -> dict:
    """
    Builds the profile dict expected by intent_router.py -- but sourced entirely
    from real database records. Year and semester come from the student's batch
    and are NEVER self-reported (a student can't misstate their own official
    standing). Stream comes from students.stream_id if already chosen.
    """
    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    year = f"{_ORDINAL.get(batch.current_year_level, batch.current_year_level)} year" if batch else None
    semester = f"{_ORDINAL.get(batch.current_semester, batch.current_semester)} semester" if batch else None

    stream_name = None
    if student.stream_id:
        stream = session.query(Stream).filter_by(id=student.stream_id).first()
        stream_name = stream.name.split()[0] if stream else None

    return {"year": year, "semester": semester, "stream": stream_name}


def set_student_stream(student: Student, stream_name: str):
    """
    Permanently records a student's stream choice -- this is a genuine one-time
    enrollment fact, not per-session chat state, so it's written directly to
    students.stream_id rather than cached anywhere temporary.
    """
    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    stream = session.query(Stream).filter(
        Stream.department_id == batch.department_id,
        Stream.name.ilike(f"{stream_name}%")
    ).first()

    if not stream:
        return False, f"'{stream_name}' isn't a recognized stream. Options: Computer, Communication, Control, Power."

    student.stream_id = stream.id
    session.commit()
    return True, f"Stream set to {stream.name}."


def looks_like_student_id(text: str) -> bool:
    """Quick sanity check before attempting a link, so an unrelated first
    message ('hi') isn't mistaken for a failed ID lookup."""
    return "/" in text and any(c.isdigit() for c in text)