# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, flash, session as flask_session
from functools import wraps
from sqlalchemy.orm import sessionmaker
from models import (
    engine, Course, Prerequisite, CampusRule, Stream, Department, 
    CourseStream, CommonCourse
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_ece_advisor_key")

Session = sessionmaker(bind=engine)

# -----------------------------------------------------------------------------
# AUTHENTICATION DECORATOR & ROUTING (Preserved)
# -----------------------------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not flask_session.get('is_admin'):
            flash("Authentication Required: Please log in to access admin privileges.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        expected_user = os.environ.get("ADMIN_USER", "admin")
        expected_pass = os.environ.get("ADMIN_PASS", "admin123")
        
        if username == expected_user and password == expected_pass:
            flask_session['is_admin'] = True
            flask_session['username'] = username
            flash("Welcome back, Administrator!", "success")
            return redirect(url_for('index'))
        else:
            flash("Invalid credentials. Please try again.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    flask_session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('index'))


# -----------------------------------------------------------------------------
# CORE SCOPE HELPER
# -----------------------------------------------------------------------------
def get_course_scopes(session, course_id):
    course = session.query(Course).filter_by(id=course_id).first()
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
# MAIN DASHBOARD (No Curriculum Filter)
# -----------------------------------------------------------------------------
@app.route('/')
def index():
    session = Session()
    try:
        # Fetch all courses unconditionally
        courses = session.query(Course).order_by(Course.year_level, Course.semester_offered).all()
        
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
                'stream_scope': stream_scope,
                'special_requirement': course.special_requirement
            })
            
        return render_template('index.html', courses=enriched_courses)
    finally:
        session.close()


# -----------------------------------------------------------------------------
# COURSE MANIPULATION (No Curriculum Parameter Handling)
# -----------------------------------------------------------------------------
@app.route('/course/add', methods=['GET', 'POST'])
@app.route('/course/edit/<int:course_id>', methods=['GET', 'POST'])
@admin_required
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

            primary_dept_id = selected_dept_ids[0] if selected_dept_ids else None
            primary_stream_id = selected_stream_ids[0] if len(selected_stream_ids) == 1 else None

            if not course:
                course = Course(
                    course_code=course_code,
                    name=course_name,
                    credit_hours=credit_hours,
                    year_level=year_level,
                    semester_offered=semester_offered,
                    department_id=primary_dept_id,
                    stream_id=primary_stream_id,
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

            session.query(CommonCourse).filter_by(course_id=course.id).delete()
            for dept_id in selected_dept_ids:
                if dept_id != primary_dept_id:
                    session.add(CommonCourse(course_id=course.id, shared_with_department_id=dept_id))

            session.query(CourseStream).filter_by(course_id=course.id).delete()
            if len(selected_stream_ids) > 1:
                for stream_id in selected_stream_ids:
                    session.add(CourseStream(course_id=course.id, stream_id=stream_id))

            session.commit()
            flash(f"Course {course_code} updated successfully!", "success")
            return redirect(url_for('index'))

        return render_template(
            'edit_course.html', 
            course=course, 
            streams=streams, 
            departments=departments,
            current_dept_ids=current_dept_ids,
            current_stream_ids=current_stream_ids
        )
    finally:
        session.close()


@app.route('/course/retire/<int:course_id>', methods=['POST'])
@admin_required
def retire_course(course_id):
    session = Session()
    try:
        course = session.query(Course).filter_by(id=course_id).first()
        if course:
            code = course.course_code
            session.query(Prerequisite).filter((Prerequisite.course_id == course_id) | (Prerequisite.prerequisite_course_id == course_id)).delete()
            session.query(CourseStream).filter_by(course_id=course_id).delete()
            session.query(CommonCourse).filter_by(course_id=course_id).delete()
            session.delete(course)
            session.commit()
            flash(f"Course {code} retired successfully.", "warning")
    except Exception as e:
        session.rollback()
        flash(str(e), "danger")
    finally:
        session.close()
    return redirect(url_for('index'))


@app.route('/prerequisites', methods=['GET', 'POST'])
@admin_required
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
            selected_prereq_ids = [int(x) for x in request.form.getlist('prerequisite_course_ids')]
            selected_stream_ids = [int(x) for x in request.form.getlist('applicable_stream_ids')]
            prereq_note = request.form.get('note', '').strip()

            if not selected_prereq_ids:
                flash("Please choose at least one prerequisite course.", "warning")
                return redirect(url_for('manage_prerequisites'))

            for prereq_id in selected_prereq_ids:
                if course_id == prereq_id:
                    continue 

                if not selected_stream_ids:
                    existing = session.query(Prerequisite).filter_by(
                        course_id=course_id, 
                        prerequisite_course_id=prereq_id,
                        applicable_stream_id=None
                    ).first()
                    if existing:
                        existing.note = prereq_note if prereq_note else None
                    else:
                        session.add(Prerequisite(
                            course_id=course_id, prerequisite_course_id=prereq_id,
                            applicable_stream_id=None, note=prereq_note if prereq_note else None
                        ))
                else:
                    for stream_id in selected_stream_ids:
                        existing = session.query(Prerequisite).filter_by(
                            course_id=course_id, prerequisite_course_id=prereq_id,
                            applicable_stream_id=stream_id
                        ).first()
                        if existing:
                            existing.note = prereq_note if prereq_note else None
                        else:
                            session.add(Prerequisite(
                                course_id=course_id, prerequisite_course_id=prereq_id,
                                applicable_stream_id=stream_id, note=prereq_note if prereq_note else None
                            ))
            
            session.commit()
            flash("Prerequisite connections updated successfully!", "success")
            return redirect(url_for('manage_prerequisites'))

        return render_template(
            'prerequisites.html', 
            courses=courses, streams=streams, prereqs=prereq_mappings, 
            course_map=course_map, stream_map=stream_map
        )
    finally:
        session.close()


@app.route('/prerequisites/delete/<int:prereq_link_id>', methods=['POST'])
@admin_required
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


@app.route('/campus-rules', methods=['GET', 'POST'])
@app.route('/campus_rules', methods=['GET', 'POST'])
@admin_required
def campus_rules():
    session = Session()
    try:
        if request.method == 'POST':
            rule_id = int(request.form['rule_id'])
            new_value = float(request.form['rule_value'])
            
            rule_to_update = session.query(CampusRule).filter_by(id=rule_id).first()
            if rule_to_update:
                rule_to_update.rule_value = new_value
                session.commit()
                flash(f"Rule '{rule_to_update.rule_key}' updated to {new_value} successfully!", "success")
            return redirect(url_for('campus_rules'))

        all_rules = session.query(CampusRule).all()
        if not all_rules:
            default_rules = [
                CampusRule(rule_key="MAX_CREDITS_REGULAR", rule_value=22.0, description="Maximum standard credit load allowed per semester."),
                CampusRule(rule_key="MAX_CREDITS_PROBATION", rule_value=12.0, description="Restricted credit limit for probation profiles."),
                CampusRule(rule_key="ALLOW_ADVISOR_OVERRIDES", rule_value=1.0, description="Allows override capabilities (1.0 = true).")
            ]
            session.add_all(default_rules)
            session.commit()
            all_rules = session.query(CampusRule).all()

        return render_template('rules.html', rules=all_rules)
    finally:
        session.close()


if __name__ == '__main__':
    app.run(debug=True, port=5001)