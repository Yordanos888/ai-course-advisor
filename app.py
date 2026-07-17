# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy.orm import sessionmaker
from models import (
    engine, Course, Prerequisite, CampusRule, Stream, Department, 
    CourseStream, CommonCourse, CurriculumVersion
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_ece_advisor_key")

# Setup SQLAlchemy Session Factory
Session = sessionmaker(bind=engine)

# Helper to resolve descriptive scopes for display
def get_course_scopes(session, course_id):
    course = session.query(Course).filter_by(id=course_id).first()
    
    # 1. Resolve Department Scopes (Handling NULL = College-wide)
    shared_depts = session.query(CommonCourse).filter_by(course_id=course_id).all()
    if course.department_id is None:
        dept_str = "Common (College-wide)"
    else:
        own_dept = session.query(Department).filter_by(id=course.department_id).first()
        dept_codes = [own_dept.code] if own_dept else []
        if shared_depts:
            dept_ids = [d.shared_with_department_id for d in shared_depts]
            shared_codes = [d.code for d in session.query(Department).filter(Department.id.in_(dept_ids)).all()]
            dept_codes.extend(shared_codes)
        dept_str = f"({', '.join(dept_codes)})" if dept_codes else "Unknown"

    # 2. Resolve Stream Scopes
    shared_streams = session.query(CourseStream).filter_by(course_id=course_id).all()
    if course.stream_id:
        stream_ids = {course.stream_id}
    else:
        stream_ids = {s.stream_id for s in shared_streams}
        
    if not stream_ids:
        stream_str = "Common (All Streams)"
    else:
        stream_names = [s.name.replace(" Engineering", "") for s in session.query(Stream).filter(Stream.id.in_(stream_ids)).all()]
        stream_str = ", ".join(stream_names)

    return dept_str, stream_str


# -----------------------------------------------------------------------------
# Route: Dashboard (Home)
# -----------------------------------------------------------------------------
@app.route('/')
def index():
    session = Session()
    try:
        courses = session.query(Course).order_by(Course.year_level, Course.semester_offered).all()
        
        # Build enriched course list
        enriched_courses = []
        for course in courses:
            dept_scope, stream_scope = get_course_scopes(session, course.id)
            enriched_courses.append({
                'id': course.id,
                'course_code': course.course_code,
                'name': course.name,
                'credit_hours': course.credit_hours,
                'year_level': course.year_level,
                'semester_offered': course.semester_offered,
                'is_droppable': course.is_droppable,
                'dept_scope': dept_scope,
                'stream_scope': stream_scope
            })
            
        return render_template('index.html', courses=enriched_courses)
    finally:
        session.close()


# -----------------------------------------------------------------------------
# Route: Add / Edit Course
# -----------------------------------------------------------------------------
@app.route('/course/add', methods=['GET', 'POST'])
@app.route('/course/edit/<int:course_id>', methods=['GET', 'POST'])
def edit_course(course_id=None):
    session = Session()
    try:
        course = None
        current_dept_ids = []
        current_stream_ids = []
        
        if course_id:
            course = session.query(Course).filter_by(id=course_id).first()
            if not course:
                flash(f"Course ID {course_id} not found.", "danger")
                return redirect(url_for('index'))
            
            # Get current associations
            current_dept_ids = [d.shared_with_department_id for d in session.query(CommonCourse).filter_by(course_id=course_id).all()]
            if course.department_id:
                current_dept_ids.append(course.department_id)
            
            current_stream_ids = [s.stream_id for s in session.query(CourseStream).filter_by(course_id=course_id).all()]
            if course.stream_id:
                current_stream_ids.append(course.stream_id)

        streams = session.query(Stream).all()
        departments = session.query(Department).all()

        if request.method == 'POST':
            course_code = request.form['course_code'].strip()
            course_name = request.form['course_name'].strip()
            credit_hours = int(request.form['credit_hours'])
            year_level = int(request.form['year_level'])
            semester_offered = int(request.form['semester_offered'])
            special_requirement = request.form.get('special_requirement', '').strip()
            is_droppable = 'is_droppable' in request.form
            
            selected_dept_ids = [int(x) for x in request.form.getlist('department_ids')]
            selected_stream_ids = [int(x) for x in request.form.getlist('stream_ids')]

            # If no department selected, it defaults to college-wide (None)
            primary_dept_id = selected_dept_ids[0] if selected_dept_ids else None
            primary_stream_id = selected_stream_ids[0] if len(selected_stream_ids) == 1 else None

            # Get an active curriculum version ID from DB to prevent SQLite Integrity Errors[cite: 16]
            active_cv = session.query(CurriculumVersion).filter_by(is_active=True).first()
            if not active_cv:
                # Fallback to creating a default curriculum if none exists
                active_cv = CurriculumVersion(version_name="ECE-Curriculum-2022", is_active=True)
                session.add(active_cv)
                session.flush()

            if not course:
                course = Course(
                    course_code=course_code,
                    name=course_name,
                    credit_hours=credit_hours,
                    year_level=year_level,
                    semester_offered=semester_offered,
                    department_id=primary_dept_id,
                    stream_id=primary_stream_id,
                    curriculum_version_id=active_cv.id, # FIX: Solves NOT NULL constraint![cite: 16]
                    special_requirement=special_requirement if special_requirement else None,
                    is_droppable=is_droppable
                )
                session.add(course)
                session.flush()
            else:
                course.course_code = course_code
                course.name = course_name
                course.credit_hours = credit_hours
                course.year_level = year_level
                course.semester_offered = semester_offered
                course.department_id = primary_dept_id
                course.stream_id = primary_stream_id
                course.special_requirement = special_requirement if special_requirement else None
                course.is_droppable = is_droppable

            # Update Common departments (Cross-Department mappings)
            session.query(CommonCourse).filter_by(course_id=course.id).delete()
            for dept_id in selected_dept_ids:
                if dept_id != primary_dept_id:
                    session.add(CommonCourse(course_id=course.id, shared_with_department_id=dept_id))

            # Update Course-Stream mappings
            session.query(CourseStream).filter_by(course_id=course.id).delete()
            if len(selected_stream_ids) > 1:
                for stream_id in selected_stream_ids:
                    session.add(CourseStream(course_id=course.id, stream_id=stream_id))

            session.commit()
            flash(f"Course {course_code} processed successfully!", "success")
            return redirect(url_for('index'))

        return render_template(
            'edit_course.html', 
            course=course, 
            streams=streams, 
            departments=departments,
            current_dept_ids=current_dept_ids,
            current_stream_ids=current_stream_ids
        )
    except Exception as e:
        session.rollback()
        flash(f"An error occurred: {str(e)}", "danger")
        return redirect(url_for('index'))
    finally:
        session.close()


# -----------------------------------------------------------------------------
# Route: Retire / Delete Course Safely
# -----------------------------------------------------------------------------
@app.route('/course/retire/<int:course_id>', methods=['POST'])
def retire_course(course_id):
    session = Session()
    try:
        course = session.query(Course).filter_by(id=course_id).first()
        if course:
            code = course.course_code
            # Delete related dependency associations first to satisfy foreign keys
            session.query(Prerequisite).filter((Prerequisite.course_id == course_id) | (Prerequisite.prerequisite_course_id == course_id)).delete()
            session.query(CourseStream).filter_by(course_id=course_id).delete()
            session.query(CommonCourse).filter_by(course_id=course_id).delete()
            
            session.delete(course)
            session.commit()
            flash(f"Course {code} has been retired and deleted from records successfully.", "warning")
        else:
            flash("Course not found.", "danger")
    except Exception as e:
        session.rollback()
        flash(f"Error during retirement: {str(e)}", "danger")
    finally:
        session.close()
    return redirect(url_for('index'))


# -----------------------------------------------------------------------------
# Route: Manage Prerequisites
# -----------------------------------------------------------------------------
@app.route('/prerequisites', methods=['GET', 'POST'])
def manage_prerequisites():
    session = Session()
    try:
        courses = session.query(Course).order_by(Course.course_code).all()
        streams = session.query(Stream).all()
        prereq_mappings = session.query(Prerequisite).all()
        
        course_map = {c.id: c for c in courses}
        stream_map = {s.id: s.name for s in streams}

        if request.method == 'POST':
            course_id = int(request.form['course_id'])
            # Support selecting multiple prerequisite courses at once[cite: 16]
            selected_prereq_ids = [int(x) for x in request.form.getlist('prerequisite_course_ids')]
            selected_stream_ids = [int(x) for x in request.form.getlist('applicable_stream_ids')]

            if not selected_prereq_ids:
                flash("Please choose at least one prerequisite course.", "warning")
                return redirect(url_for('manage_prerequisites'))

            for prereq_id in selected_prereq_ids:
                if course_id == prereq_id:
                    continue # Skip self-prerequisite links

                if not selected_stream_ids:
                    # Global link (All Streams)[cite: 16]
                    existing = session.query(Prerequisite).filter_by(
                        course_id=course_id, 
                        prerequisite_course_id=prereq_id,
                        applicable_stream_id=None
                    ).first()
                    if not existing:
                        session.add(Prerequisite(
                            course_id=course_id,
                            prerequisite_course_id=prereq_id,
                            applicable_stream_id=None
                        ))
                else:
                    # Specific stream limits only[cite: 16]
                    for stream_id in selected_stream_ids:
                        existing = session.query(Prerequisite).filter_by(
                            course_id=course_id, 
                            prerequisite_course_id=prereq_id,
                            applicable_stream_id=stream_id
                        ).first()
                        if not existing:
                            session.add(Prerequisite(
                                course_id=course_id,
                                prerequisite_course_id=prereq_id,
                                applicable_stream_id=stream_id
                            ))
            
            session.commit()
            flash("Prerequisite connections updated successfully!", "success")
            return redirect(url_for('manage_prerequisites'))

        return render_template(
            'prerequisites.html', 
            courses=courses, 
            streams=streams, 
            prereqs=prereq_mappings, 
            course_map=course_map,
            stream_map=stream_map
        )
    finally:
        session.close()


@app.route('/prerequisites/delete/<int:prereq_link_id>', methods=['POST'])
def delete_prerequisite(prereq_link_id):
    session = Session()
    try:
        link = session.query(Prerequisite).filter_by(id=prereq_link_id).first()
        if link:
            session.delete(link)
            session.commit()
            flash("Link removed.", "info")
    except Exception as e:
        session.rollback()
        flash(str(e), "danger")
    finally:
        session.close()
    return redirect(url_for('manage_prerequisites'))


if __name__ == '__main__':
    app.run(debug=True, port=5001)