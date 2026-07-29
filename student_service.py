# student_service.py
from sqlalchemy.orm import sessionmaker
from models import engine, Student, Batch, Stream

Session = sessionmaker(bind=engine)
session = Session()

_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}


def get_student_by_telegram_id(telegram_user_id: str):
    return session.query(Student).filter_by(telegram_user_id=str(telegram_user_id)).first()


def register_or_link_student(telegram_user_id: str, official_student_id: str):
    """
    Registration is create-or-fetch, not lookup-only: a brand new ID creates a
    record on the spot; an existing ID just links this Telegram account to it.
    Blocks a different Telegram account from hijacking an already-linked ID.
    """
    official_student_id = official_student_id.strip()
    telegram_user_id = str(telegram_user_id)

    existing_by_telegram = get_student_by_telegram_id(telegram_user_id)
    if existing_by_telegram and existing_by_telegram.id != official_student_id:
        return False, "This Telegram account is already linked to a different student ID."

    existing_by_id = session.query(Student).filter_by(id=official_student_id).first()
    if existing_by_id:
        if existing_by_id.telegram_user_id and existing_by_id.telegram_user_id != telegram_user_id:
            return False, ("This student ID is already linked to a different Telegram account. "
                            "If this is your ID, please contact the department office to resolve this.")
        existing_by_id.telegram_user_id = telegram_user_id
        session.commit()
        return True, f"Welcome back, {existing_by_id.name}!"

    new_student = Student(id=official_student_id, telegram_user_id=telegram_user_id,
                           name=official_student_id, batch_id=None, stream_id=None, status="ACTIVE")
    session.add(new_student)
    session.commit()
    return True, f"Registered! Your ID {official_student_id} is now linked."


def get_derived_profile(student: Student) -> dict:
    """Handles a student with no batch yet (fresh registration) without crashing."""
    profile = {"year": None, "semester": None, "stream": None}
    if student.batch_id:
        batch = session.query(Batch).filter_by(id=student.batch_id).first()
        if batch:
            profile["year"] = f"{_ORDINAL.get(batch.current_year_level, batch.current_year_level)} year"
            profile["semester"] = f"{_ORDINAL.get(batch.current_semester, batch.current_semester)} semester"
    if student.stream_id:
        stream = session.query(Stream).filter_by(id=student.stream_id).first()
        if stream:
            profile["stream"] = stream.name.split()[0]
    return profile


def needs_batch_semester(profile: dict) -> bool:
    return profile["year"] is None or profile["semester"] is None


def needs_stream(student, profile) -> bool:
    if student.stream_id:
        return False
    year, semester = profile.get("year", ""), profile.get("semester", "")
    return ("4th" in str(year) and "2nd" in str(semester)) or "5th" in str(year)


def set_student_stream(student, stream_name: str):
    batch = session.query(Batch).filter_by(id=student.batch_id).first()
    stream = session.query(Stream).filter(
        Stream.department_id == batch.department_id, Stream.name.ilike(f"{stream_name}%")
    ).first()
    if not stream:
        return False, f"'{stream_name}' isn't recognized. Options: Computer, Communication, Control, Power."
    student.stream_id = stream.id
    session.commit()
    return True, f"Stream set to {stream.name}."


def looks_like_student_id(text: str) -> bool:
    return "/" in text and any(c.isdigit() for c in text)