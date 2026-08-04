"""
Pure-arithmetic tests for the year-5 cap/horizon helpers -- no ortools
dependency, so these can run anywhere. Run with: pytest test_solver_helpers.py -v
"""
import pytest
from reasoning_solver import _effective_year_level, _slot_parity, _last_valid_slot


# --- _effective_year_level: does the cap engage exactly at year 5, not before/after? ---

def test_effective_year_before_year5():
    # Student in year 3, sem 1. Slot 0 = year3 sem1, slot 3 = year4 sem2 (still <5).
    assert _effective_year_level(3, 1, 0) == 3
    assert _effective_year_level(3, 1, 3) == 4


def test_effective_year_crosses_into_year5():
    # Student in year 4, sem 2 (last semester before year5).
    assert _effective_year_level(4, 2, 0) == 4
    assert _effective_year_level(4, 2, 1) == 5  # next slot is year5 sem1


def test_effective_year_both_semesters_of_year5():
    # Student already in year5 sem1: both remaining slots (sem1, sem2) must read as year5.
    assert _effective_year_level(5, 1, 0) == 5
    assert _effective_year_level(5, 1, 1) == 5


# --- _last_valid_slot: is year 6+ actually excluded? (the bug: it wasn't) ---

def test_last_valid_slot_from_year1():
    # Year1 sem1 -> last legal slot is year5 sem2, slot 9 (10 semesters total, 0-indexed).
    assert _last_valid_slot(1, 1, max_years=5) == 9


def test_last_valid_slot_from_year5_sem1():
    # Only 1 slot left (year5 sem2).
    assert _last_valid_slot(5, 1, max_years=5) == 1


def test_last_valid_slot_from_year5_sem2():
    # Currently in the last legal semester itself.
    assert _last_valid_slot(5, 2, max_years=5) == 0


def test_last_valid_slot_already_past_horizon():
    # Year6 shouldn't exist, but guard against garbage input defensively.
    assert _last_valid_slot(6, 1, max_years=5) < 0


# --- _slot_parity: sanity check, unaffected by the fix but used alongside it ---

def test_slot_parity_alternates():
    assert _slot_parity(1, 0) == 1
    assert _slot_parity(1, 1) == 2
    assert _slot_parity(1, 2) == 1