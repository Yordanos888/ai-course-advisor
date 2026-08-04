"""
prerequisite_graph.py

Phase 6, Step 1: loads the full prerequisite structure into a NetworkX
DiGraph, one time, so downstream Phase 6 logic (the CP-SAT solver, downstream
impact tracing) works against an in-memory graph instead of querying the
database per-course.

Edge direction: prerequisite_course_id -> course_id
  (i.e. an edge points FROM the prerequisite TO the course that requires it,
  so "descendants of X" = "everything that becomes unreachable if X isn't passed")

Each node carries the course's own attributes (code, name, year_level,
semester_offered, credit_hours, special_requirement, is_droppable, and its
own stream scope). Each edge carries `applicable_streams`: None means the
prerequisite applies to a student in ANY stream; otherwise a set of stream_ids
it's restricted to.

IMPORTANT: a single (course, prerequisite) pair can have MULTIPLE rows in the
database differing only by applicable_stream_id (e.g. Integrated Design
Project requiring "Introduction to Control System" as two separate rows --
one scoped to Control, one scoped to Power). A plain DiGraph only supports one
edge per node pair, so these rows are MERGED into a single edge whose
applicable_streams is the union of all the rows' stream scopes.
"""
import networkx as nx
from sqlalchemy.orm import sessionmaker
from models import engine, Course, Prerequisite, CourseStream

Session = sessionmaker(bind=engine)


def _course_stream_membership(session, course, course_streams_by_course):
    """None = common to all streams; otherwise a set of stream_ids."""
    if course.stream_id:
        return {course.stream_id}
    entries = course_streams_by_course.get(course.id)
    if entries:
        return set(entries)
    return None


def load_prerequisite_graph() -> nx.DiGraph:
    session = Session()
    try:
        graph = nx.DiGraph()

        courses = session.query(Course).all()
        course_streams_by_course = {}
        for cs in session.query(CourseStream).all():
            course_streams_by_course.setdefault(cs.course_id, []).append(cs.stream_id)

        for c in courses:
            own_streams = _course_stream_membership(session, c, course_streams_by_course)
            graph.add_node(
                c.id,
                course_code=c.course_code,
                name=c.name,
                year_level=c.year_level,
                semester_offered=c.semester_offered,
                credit_hours=c.credit_hours,
                stream_scope=own_streams,  # None = common to all streams
                special_requirement=c.special_requirement,
                is_droppable=c.is_droppable,
            )

        # Merge rows sharing the same (course_id, prerequisite_course_id) pair
        # into a single edge with the UNION of their applicable_streams.
        edge_stream_union = {}   # (prereq_id, course_id) -> None (all) or set(stream_ids)
        for p in session.query(Prerequisite).all():
            key = (p.prerequisite_course_id, p.course_id)
            if key not in edge_stream_union:
                edge_stream_union[key] = set() if p.applicable_stream_id is not None else None
            if edge_stream_union[key] is None:
                continue  # already "applies to all" -- nothing can narrow that
            if p.applicable_stream_id is None:
                edge_stream_union[key] = None  # any row with no restriction makes the whole edge unrestricted
            else:
                edge_stream_union[key].add(p.applicable_stream_id)

        for (prereq_id, course_id), applicable_streams in edge_stream_union.items():
            graph.add_edge(prereq_id, course_id, applicable_streams=applicable_streams)

        return graph
    finally:
        session.close()


def validate_acyclic(graph: nx.DiGraph):
    """Returns (True, None) if the graph is a valid DAG, or (False, cycle_description)
    if a circular prerequisite chain was found -- this should be run once after
    every load, especially after hand-entered curriculum data changes."""
    if nx.is_directed_acyclic_graph(graph):
        return True, None
    cycle_edges = nx.find_cycle(graph)
    cycle_codes = [
        f"{graph.nodes[edge[0]]['course_code']} -> {graph.nodes[edge[1]]['course_code']}"
        for edge in cycle_edges
    ]
    return False, cycle_codes


def get_downstream_impact(graph: nx.DiGraph, course_id: int):
    """
    Given a course node id, returns every downstream course that becomes
    unreachable if this course isn't passed, each annotated with WHICH
    streams the block actually applies to. Replaces the ad hoc BFS that used
    to live in query_api.py, now built on the loaded graph instead of live
    per-course database queries -- same correctness guarantees (intersecting
    the prerequisite edge's stream restriction with the downstream course's
    own stream scope), but O(1) graph lookups instead of repeated queries.
    """
    if course_id not in graph:
        return []

    restriction = {course_id: None}
    order = list(nx.topological_sort(graph.subgraph(nx.descendants(graph, course_id) | {course_id})))

    results = []
    for node in order:
        if node == course_id:
            continue
        combined = set()
        combined_is_all = False
        any_predecessor_reached = False
        for pred in graph.predecessors(node):
            if pred not in restriction:
                continue
            any_predecessor_reached = True
            edge_data = graph[pred][node]
            edge_restriction = edge_data['applicable_streams']
            pred_restriction = restriction[pred]

            if pred_restriction is None and edge_restriction is None:
                combined_is_all = True
            elif pred_restriction is None:
                combined |= edge_restriction
            elif edge_restriction is None:
                combined |= pred_restriction
            else:
                combined |= (pred_restriction & edge_restriction)

        if not any_predecessor_reached:
            continue

        node_restriction = None if combined_is_all else (combined if combined else set())
        own_scope = graph.nodes[node]['stream_scope']
        if node_restriction is None:
            final = own_scope
        elif own_scope is None:
            final = node_restriction
        else:
            final = node_restriction & own_scope

        restriction[node] = final
        results.append({
            "course_id": node,
            "course_code": graph.nodes[node]['course_code'],
            "name": graph.nodes[node]['name'],
            "applicable_streams": final,
        })

    return results


if __name__ == "__main__":
    g = load_prerequisite_graph()
    print(f"Loaded graph: {g.number_of_nodes()} courses, {g.number_of_edges()} prerequisite edges")
    ok, cycle_info = validate_acyclic(g)
    print("Acyclic?", ok, "" if ok else f"-- CYCLE FOUND: {cycle_info}")