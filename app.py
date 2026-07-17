# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy.orm import sessionmaker
from models import engine, Course, Prerequisite, CampusRule, Stream, Department, CourseStream

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_ece_advisor_key")

# Setup SQLAlchemy Session Factory
Session = sessionmaker(bind=engine)

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Cleanly close database sessions after each request to prevent locks."""
    # We will close the session within individual routes or globally if desired.
    pass


# -----------------------------------------------------------------------------
# Route: Dashboard (Home)
# -----------------------------------------------------------------------------
@app.route('/')
def index():
    session = Session()
    try:
        courses = session.query(Course).order_by(Course.year_level, Course.semester_offered).all()
        streams = {s.id: s.name for s in session.query(Stream).all()}
        departments = {d.id: d.name for d in session.query(Department).all()}
        return render_template('index.html', courses=courses, streams=streams, departments=departments)
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
        if course_id:
            course = session.query(Course).filter_by(id=course_id).first()
            if not course:
                flash(f"Course ID {course_id} not found.", "danger")
                return redirect(url_for('index'))

        streams = session.query(Stream).all()
        departments = session.query(Department).all()

        if request.method == 'POST':
            course_code = request.form['course_code'].strip()
            course_name = request.form['course_name'].strip()
            credit_hours = int(request.form['credit_hours'])
            year_level = int(request.form['year_level'])
            semester_offered = int(request.form['semester_offered'])
            department_id = int(request.form['department_id'])
            
            # Stream ID can be null (Common Course)
            stream_form = request.form['stream_id']
            stream_id = int(stream_form) if stream_form else None
            
            is_droppable = 'is_droppable' in request.form

            if not course:
                # Create a new course instance
                course = Course(
                    course_code=course_code,
                    course_name=course_name,
                    credit_hours=credit_hours,
                    year_level=year_level,
                    semester_offered=semester_offered,
                    department_id=department_id,
                    stream_id=stream_id,
                    is_droppable=is_droppable
                )
                session.add(course)
                flash(f"Course {course_code} created successfully!", "success")
            else:
                # Update existing
                course.course_code = course_code
                course.course_name = course_name
                course.credit_hours = credit_hours
                course.year_level = year_level
                course.semester_offered = semester_offered
                course.department_id = department_id
                course.stream_id = stream_id
                course.is_droppable = is_droppable
                flash(f"Course {course_code} updated successfully!", "success")

            session.commit()
            return redirect(url_for('index'))

        return render_template('edit_course.html', course=course, streams=streams, departments=departments)
    except Exception as e:
        session.rollback()
        flash(f"An error occurred: {str(e)}", "danger")
        return redirect(url_for('index'))
    finally:
        session.close()


# -----------------------------------------------------------------------------
# Route: Manage Prerequisites
# -----------------------------------------------------------------------------
@app.route('/prerequisites', methods=['GET', 'POST'])
def manage_prerequisites():
    session = Session()
    try:
        courses = session.query(Course).order_by(Course.course_code).all()
        streams = session.query(Stream).all()
        
        # Load all existing prerequisite mappings
        prereq_mappings = session.query(Prerequisite).all()
        
        # Build lookup tables for easier UI parsing
        course_map = {c.id: c for c in courses}
        stream_map = {s.id: s.name for s in streams}

        if request.method == 'POST':
            # Handle creating a new link
            course_id = int(request.form['course_id'])
            prereq_id = int(request.form['prerequisite_course_id'])
            stream_cond = request.form['applicable_stream_id']
            applicable_stream_id = int(stream_cond) if stream_cond else None

            if course_id == prereq_id:
                flash("A course cannot be a prerequisite of itself!", "danger")
            else:
                # Check for existing link to prevent duplicates
                existing = session.query(Prerequisite).filter_by(
                    course_id=course_id, 
                    prerequisite_course_id=prereq_id,
                    applicable_stream_id=applicable_stream_id
                ).first()
                
                if existing:
                    flash("This prerequisite relationship already exists.", "warning")
                else:
                    new_prereq = Prerequisite(
                        course_id=course_id,
                        prerequisite_course_id=prereq_id,
                        applicable_stream_id=applicable_stream_id
                    )
                    session.add(new_prereq)
                    session.commit()
                    flash("Prerequisite relationship established successfully!", "success")
            
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
            flash("Prerequisite rule removed.", "info")
        else:
            flash("Prerequisite link not found.", "danger")
    except Exception as e:
        session.rollback()
        flash(f"Error deleting: {str(e)}", "danger")
    finally:
        session.close()
    return redirect(url_for('manage_prerequisites'))


# -----------------------------------------------------------------------------
# Route: Manage Campus Rules
# -----------------------------------------------------------------------------
@app.route('/rules', methods=['GET', 'POST'])
def manage_rules():
    session = Session()
    try:
        rules = session.query(CampusRule).all()
        departments = session.query(Department).all()

        if request.method == 'POST':
            rule_id = int(request.form['rule_id'])
            new_val = float(request.form['rule_value'])
            
            rule_obj = session.query(CampusRule).filter_by(id=rule_id).first()
            if rule_obj:
                old_val = rule_obj.rule_value
                rule_obj.rule_value = new_val
                session.commit()
                flash(f"Updated '{rule_obj.rule_key}': {old_val} ➡️ {new_val}", "success")
            else:
                flash("Rule not found.", "danger")
            return redirect(url_for('manage_rules'))

        return render_template('rules.html', rules=rules, departments=departments)
    finally:
        session.close()


if __name__ == '__main__':
    print("🚀 Starting AI-Course Advisor V2 Web Administration Portal...")
    app.run(debug=True, port=5001)