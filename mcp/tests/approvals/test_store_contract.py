"""The approvals store contract, driven end to end over real HTTP.

`mcp/APPROVALS.md` writes this contract out in Markdown, because
whoever builds #19 works in TypeScript and should not have to read Python to
build against it. This file is that prose with a runnable counterpart: every
endpoint, the one record shape, the 401 on each, and the 404s.

It uses a plain HTTP client rather than `approvals/store.py`, deliberately.
`GET /api/approvals/{id}` is the read #19's page is built on and **nothing in this
toolkit calls it** — a Python client nobody uses would be dead code in a
deployed worker. Driving the contract directly is how it gets exercised without
inventing a caller for it, and it also means these assertions are about the
wire rather than about our client's opinion of the wire.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from loan_desk.approvals import Decision, decide, request_approval
from tests.approvals.conftest import RECORD_FIELDS, STORE_TOKEN, DANA, RILEY, SlackState, StoreState

ACT_TWO = {
    "action": "approve_loan",
    "resource_id": "LN-2291",
    "amount": 95_000.0,
    "justification": "Eleven years in business, 742 credit score, $1.4M annual revenue.",
}

ENDPOINTS = [
    ("GET", "/api/approvals/roster"),
    ("GET", "/api/approvals/apr_000000000001"),
    ("POST", "/api/approvals"),
    ("POST", "/api/approvals/apr_000000000001/decision"),
]


def call(store: StoreState, method: str, path: str, *, token: str | None = STORE_TOKEN,
         json: dict[str, Any] | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return httpx.request(
        method, f"http://{store.host}{path}", headers=headers, json=json, timeout=10.0
    )


class TestAuthorization:
    @pytest.mark.parametrize(("method", "path"), ENDPOINTS, ids=lambda v: str(v))
    def test_every_endpoint_refuses_a_call_without_the_bearer(
        self, store: StoreState, method: str, path: str
    ) -> None:
        # Without this, anyone on the internet could manufacture the approval
        # request a human then acts on — or read one they were never sent.
        assert call(store, method, path, token=None, json={}).status_code == 401

    @pytest.mark.parametrize(("method", "path"), ENDPOINTS, ids=lambda v: str(v))
    def test_every_endpoint_refuses_the_wrong_bearer(
        self, store: StoreState, method: str, path: str
    ) -> None:
        assert call(store, method, path, token="not-the-token", json={}).status_code == 401


class TestRoster:
    def test_returns_every_subject_the_control_plane_knows(self, store: StoreState) -> None:
        response = call(store, "GET", "/api/approvals/roster")
        assert response.status_code == 200
        subjects = response.json()["subjects"]
        assert {s["user_id"] for s in subjects} >= {DANA.user_id, RILEY.user_id}
        for subject in subjects:
            assert {"user_id", "display_name", "role", "clearance"} <= set(subject)


class TestTheRecordShape:
    """One shape, from every endpoint that returns a request."""

    async def test_the_created_record_is_complete(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)
        record = store.records[created["request_id"]]
        assert set(record) == set(RECORD_FIELDS)

    async def test_the_read_record_is_complete_and_identical_to_the_created_one(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)

        response = call(store, "GET", f"/api/approvals/{created['request_id']}")
        assert response.status_code == 200
        record = response.json()["request"]

        assert set(record) == set(RECORD_FIELDS)
        # A page that can render the read is a page that can render the write.
        assert record == store.records[created["request_id"]]

    async def test_the_read_carries_everything_19s_page_has_to_show(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # The link carries an opaque id and nothing else, so this one response
        # is all #19 has to build the page from.
        store.rule = {
            "id": "act2_amount_exceeds_clearance",
            "description": "An approval above the caller's authority is blocked.",
        }
        created = await request_approval(as_dana, **ACT_TWO)

        record = call(store, "GET", f"/api/approvals/{created['request_id']}").json()["request"]

        assert record["requester_id"] == DANA.user_id
        assert record["requester_display_name"] == DANA.display_name
        assert record["approver_id"] == RILEY.user_id
        assert record["approver_display_name"] == RILEY.display_name
        assert record["candidate_approver_ids"][0] == RILEY.user_id
        assert record["action"] == "approve_loan"
        assert record["resource_id"] == "LN-2291"
        assert record["amount"] == 95_000.0
        assert record["required_clearance"] == 95_000.0
        assert record["rule"]["id"] == "act2_amount_exceeds_clearance"
        assert record["justification"] == ACT_TWO["justification"]
        assert record["status"] == "pending"
        assert record["created_at"].endswith("Z")
        assert record["decided_at"] is None
        assert record["decided_by"] is None
        assert record["note"] is None

    async def test_a_rule_the_control_plane_cannot_name_is_null_not_absent(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # An absent key and a key set to null serialise differently once this
        # round-trips through SQLite and JSON; the contract says null.
        created = await request_approval(as_dana, **ACT_TWO)
        record = call(store, "GET", f"/api/approvals/{created['request_id']}").json()["request"]
        assert "rule" in record
        assert record["rule"] is None

    async def test_the_decided_record_is_the_same_shape_with_the_outcome_filled_in(
        self, as_dana, as_riley, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)
        await decide(
            as_riley,
            request_id=created["request_id"],
            decision=Decision.APPROVED,
            note="Coverage checks out.",
        )

        record = call(store, "GET", f"/api/approvals/{created['request_id']}").json()["request"]

        assert set(record) == set(RECORD_FIELDS)
        # No separate `decision` field: once decided, `status` is the decision.
        # Two fields carrying one fact is two fields that can disagree.
        assert "decision" not in record
        assert record["status"] == "approved"
        assert record["decided_by"] == RILEY.user_id
        assert record["note"] == "Coverage checks out."
        assert record["decided_at"].endswith("Z")
        # Everything the page showed while pending is still there.
        assert record["action"] == "approve_loan"
        assert record["amount"] == 95_000.0


class TestUnknownRequests:
    def test_reading_an_unknown_id_is_a_404_that_names_it(self, store: StoreState) -> None:
        response = call(store, "GET", "/api/approvals/apr_nosuchthing")
        assert response.status_code == 404
        assert "apr_nosuchthing" in response.json()["error"]

    def test_deciding_an_unknown_id_is_a_404_that_names_it(self, store: StoreState) -> None:
        response = call(
            store,
            "POST",
            "/api/approvals/apr_nosuchthing/decision",
            json={"decision": "approved", "note": None, "decided_by": RILEY.user_id},
        )
        assert response.status_code == 404
        assert "apr_nosuchthing" in response.json()["error"]

class TestTheReadIsNotAuthorization:
    async def test_the_read_takes_no_viewer_and_answers_every_caller_alike(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        """A 200 here says the request exists, never that you may act on it.

        The requester can read the DM she sent, so anyone holding the link
        reaches this endpoint. The contract has nowhere to put a viewer — no
        parameter, no header beyond the store's own bearer — which is what
        makes that structural rather than a promise. Whether the person looking
        may *decide* is a `/pre` decision on `LoanDesk.Decide`, settled at
        click time in #19.
        """
        created = await request_approval(as_dana, **ACT_TWO)
        path = f"/api/approvals/{created['request_id']}"

        first = call(store, "GET", path)
        second = call(store, "GET", path)

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        # Nothing in the record names who asked for it, because nothing could.
        assert "viewer" not in first.text and "viewed_by" not in first.text
