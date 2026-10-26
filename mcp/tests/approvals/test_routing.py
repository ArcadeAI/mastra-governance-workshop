"""Routing, against the same rows the TypeScript router is checked against.

`gate/schema/contract/approver-routing-cases.json` is the contract.
`gate/engine/test/approver-router.test.ts` loads it too. Agreement
between the two implementations is therefore tested, not argued: a row added
there is checked here on the next run, and a row that stops matching one side
fails on that side.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from loan_desk.approvals_routing import Subject, route_approval

CASES_PATH = (
    Path(__file__).resolve().parents[3]
    / "gate"
    / "schema"
    / "contract"
    / "approver-routing-cases.json"
)

CASES: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))

SUBJECTS: dict[str, Subject] = {
    key: Subject.from_dict(raw) for key, raw in CASES["subjects"].items()
}

# JSON cannot hold a non-finite number, so the file spells them.
NON_FINITE = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


def roster(keys: list[str]) -> list[Subject]:
    return [SUBJECTS[key] for key in keys]


def amount_of(raw: float | str) -> float:
    return NON_FINITE[raw] if isinstance(raw, str) else float(raw)


def test_the_shared_case_file_is_actually_loaded() -> None:
    # A case file that failed to load would make every parametrised test below
    # vacuous — the same failure as a policy rule that matches nothing, which
    # is indistinguishable from a rule that permits.
    assert CASES_PATH.is_file(), CASES_PATH
    assert len(CASES["cases"]) > 0
    assert len(CASES["invalid_amounts"]) > 0


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_routes_exactly_as_the_typescript_does(case: dict[str, Any]) -> None:
    result = route_approval(case["amount"], case["requester"], roster(case["roster"]))

    assert result.required_clearance == case["amount"]
    assert [s.user_id for s in result.candidates] == case["candidates"]

    if case["approver"] is None:
        assert result.outcome == "no_eligible_approver"
        assert result.approver is None
    else:
        assert result.outcome == "routed"
        assert result.approver is not None
        assert result.approver.user_id == case["approver"]
        # The approver is always the head of the candidate list.
        assert result.approver is result.candidates[0]


@pytest.mark.parametrize("case", CASES["invalid_amounts"], ids=lambda c: c["name"])
def test_invalid_amounts_raise_rather_than_reporting_no_eligible_approver(
    case: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        route_approval(
            amount_of(case["amount"]),
            SUBJECTS["dana"].user_id,
            roster(CASES["cast"]),
        )


class TestPurity:
    def test_does_not_mutate_the_roster_it_is_given(self) -> None:
        given = roster(CASES["cast"])
        before = list(given)
        route_approval(95_000, SUBJECTS["dana"].user_id, given)
        assert given == before

    def test_returns_the_same_answer_for_the_same_inputs(self) -> None:
        first = route_approval(95_000, SUBJECTS["dana"].user_id, roster(CASES["cast"]))
        second = route_approval(95_000, SUBJECTS["dana"].user_id, roster(CASES["cast"]))
        assert first == second

    def test_returns_the_rosters_own_subjects_not_copies(self) -> None:
        result = route_approval(95_000, SUBJECTS["dana"].user_id, roster(CASES["cast"]))
        assert result.approver is SUBJECTS["riley"]
