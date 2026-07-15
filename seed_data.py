from sqlalchemy.orm import sessionmaker
from models import engine, Department, Stream, CampusRule

# 1. Bind a Session lifecycle to our existing database engine
Session = sessionmaker(bind=engine)
session = Session()

def seed_foundational_data():
    print("Starting data seeding process...")

    # --- 2. Seed Academic Departments ---
    ece_dept = Department(name="Electrical and Computer Engineering", code="ECE")
    se_dept = Department(name="Software Engineering", code="SE")
    eme_dept = Department(name="Electromechanincal Engineering", code="EME")
    
    session.add_all([ece_dept, se_dept, eme_dept])
    session.flush() # Flush pushes records to the DB to generate IDs without committing yet

    print(f"✅ Seeded Departments: {ece_dept.code} (ID: {ece_dept.id}), {se_dept.code} (ID: {se_dept.id}), {eme_dept.code} (ID: {eme_dept.id}) ")

    # --- 3. Seed ECE Streams ---
    # Mapping the four distinct structural paths available to ECE students in Year 4 Sem 2
    streams = [
        Stream(department_id=ece_dept.id, name="Computer Engineering"),
        Stream(department_id=ece_dept.id, name="Communication Engineering"),
        Stream(department_id=ece_dept.id, name="Control Engineering"),
        Stream(department_id=ece_dept.id, name="Power Engineering")
    ]
    session.add_all(streams)
    print("✅ Seeded ECE Streams: Computer, Communication, Control, Power")

    # --- 4. Seed Global & Scoped Campus Rules ---
    rules = [
        # General maximum load limit for lower-year engineering semesters
        CampusRule(
            rule_key="max_credit_hours_regular",
            rule_value=20.0, 
            department_id=None,
            year_level=None,
            description="Maximum regular load limit allowed for engineering cohorts during a standard semester."
        ),
        # Year 5 specific rule: Overload up to 22 credits allowed ONLY if graduation timeline is guaranteed
        CampusRule(
            rule_key="max_credit_hours_overload",
            rule_value=22.0,
            department_id=ece_dept.id,
            year_level=5,
            description="Maximum credit overload capacity allowed explicitly for Year 5 students to guarantee graduation."
        ),
        # Rule 9: Strict terminal cap tracking for course attempts before dismissals trigger
        CampusRule(
            rule_key="max_retake_attempts",
            rule_value=3.0,
            department_id=None,
            year_level=None,
            description="Maximum allowed graded attempts (FAILED or PASSED) per unique course before a terminal DISMISSED status is triggered."
        )
    ]
    session.add_all(rules)
    print("✅ Seeded Scoped Campus Rule constraints.")

    # --- 5. Commit Transaction cleanly ---
    try:
        session.commit()
        print("\n🎉 Seed data successfully committed to academic_records.db!")
    except Exception as e:
        session.rollback()
        print(f"❌ Error during seeding, rolled back changes: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed_foundational_data()