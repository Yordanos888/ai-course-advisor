# profile_parser.py
import re

def parse_profile_answer(text):
    """Extracts year/semester/stream from a free-text reply to the STATUS_CHECK prompt."""
    text_lower = text.lower()
    year = semester = stream = None
    digit_to_ordinal = {"1": "1st", "2": "2nd", "3": "3rd", "4": "4th", "5": "5th"}

    year_match = re.search(
        r'\b(1st|first|2nd|second|3rd|third|4th|fourth|5th|fifth)\s*[- ]?\s*year\b', text_lower)
    if year_match:
        ordinal_map = {"1st": "1st", "first": "1st", "2nd": "2nd", "second": "2nd",
                        "3rd": "3rd", "third": "3rd", "4th": "4th", "fourth": "4th",
                        "5th": "5th", "fifth": "5th"}
        year = f"{ordinal_map[year_match.group(1)]} year"
    else:
        num_year_match = re.search(r'\byear\s*(\d)\b', text_lower)
        if num_year_match:
            year = f"{digit_to_ordinal[num_year_match.group(1)]} year"

    sem_match = re.search(
        r'\b(1st|first|2nd|second)\s*[- ]?\s*sem(?:ester)?\b', text_lower)
    if sem_match:
        sem_ordinal_map = {"1st": "1st", "first": "1st", "2nd": "2nd", "second": "2nd"}
        semester = f"{sem_ordinal_map[sem_match.group(1)]} semester"
    else:
        num_match = re.search(r'\bsem(?:ester)?\s*(1|2)\b', text_lower)
        if num_match:
            semester = "1st semester" if num_match.group(1) == "1" else "2nd semester"

    for s in ["computer", "communication", "control", "power"]:
        if re.search(rf'\b{s}\b', text_lower):
            stream = s.capitalize()
            break

    return {"year": year, "semester": semester, "stream": stream}