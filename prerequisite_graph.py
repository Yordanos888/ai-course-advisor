"""
prerequisite_graph.py
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

def load_prerequisite_graph(session=None) -> nx.DiGraph:
    local_session = False
    if session is None:
        session = Session()
        local_session = True
        
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
                stream_scope=own_streams, 
                special_requirement=c.special_requirement,
                is_droppable=c.is_droppable,
            )

        edge_stream_union = {} 
        for p in session.query(Prerequisite).all():
            key = (p.prerequisite_course_id, p.course_id)
            if key not in edge_stream_union:
                edge_stream_union[key] = set() if p.applicable_stream_id is not None else None
            if edge_stream_union[key] is None:
                continue 
            if p.applicable_stream_id is None:
                edge_stream_union[key] = None 
            else:
                edge_stream_union[key].add(p.applicable_stream_id)

        for (prereq_id, course_id), applicable_streams in edge_stream_union.items():
            graph.add_edge(prereq_id, course_id, applicable_streams=applicable_streams)

        return graph
    finally:
        if local_session:
            session.close()

def validate_acyclic(graph: nx.DiGraph):
    if nx.is_directed_acyclic_graph(graph):
        return True, None
    cycle_edges = nx.find_cycle(graph)
    cycle_codes = [
        f"{graph.nodes[edge[0]]['course_code']} -> {graph.nodes[edge[1]]['course_code']}"
        for edge in cycle_edges
    ]
    return False, cycle_codes

def get_downstream_impact(graph: nx.DiGraph, course_id: int):
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