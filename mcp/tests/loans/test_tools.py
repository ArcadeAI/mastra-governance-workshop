"""The four tools, end to end through the real API."""

import pytest
from arcade_core.errors import ToolExecutionError

from loan_desk import loans
from loan_desk.loans import LoanStatus, approve_loan, deny_loan, get_loan, search_loans
from tests import tools_of
from tests.loans.conftest import DANA, RILEY


CATALOG = tools_of(loans)


class TestDefinition:
    """What Arcade sees when it loads the toolkit: this file's four tools."""

    def test_exposes_exactly_the_four_loan_tools(self) -> None:
        names = sorted(t.definition.name for t in CATALOG)
        assert names == ["ApproveLoan", "DenyLoan", "GetLoan", "SearchLoans"]

    def test_every_tool_requires_the_two_secrets_and_no_oauth(self) -> None:
        # Since 2026-10-17: the gateway names the actor, the secrets name the project and the book.
        for tool in CATALOG:
            assert tool.definition.requirements.authorization is None, tool.definition.name
            secrets = [s.key for s in tool.definition.requirements.secrets or []]
            assert secrets == ["APP_PUBLIC_HOST", "LOAN_SERVICE_TOKEN"], tool.definition.name

    def test_describes_every_tool_and_every_argument(self) -> None:
        for tool in CATALOG:
            assert tool.definition.description, tool.definition.name
            for param in tool.definition.input.parameters:
                assert param.description, f"{tool.definition.name}.{param.name}"

    def test_required_arguments_match_the_previous_surface(self) -> None:
        required = {
            t.definition.name: sorted(p.name for p in t.definition.input.parameters if p.required)
            for t in CATALOG
        }
        assert required == {
            "SearchLoans": [],
            "GetLoan": ["loan_id"],
            "ApproveLoan": ["amount", "loan_id"],
            "DenyLoan": ["loan_id", "reason"],
        }

    def test_carries_the_previous_surface_annotations_as_behavior(self) -> None:
        # #30's readOnlyHint / destructiveHint / idempotentHint / openWorldHint,
        # as arcade-mcp Behavior. Dropped once already; this keeps them.
        behavior = {
            t.definition.name: t.definition.metadata.behavior.model_dump(exclude={"operations"})
            for t in CATALOG
        }
        read = {"read_only": True, "destructive": False, "idempotent": True, "open_world": False}
        write = {"read_only": False, "destructive": False, "idempotent": False, "open_world": False}
        assert behavior == {
            "SearchLoans": read,
            "GetLoan": read,
            "ApproveLoan": write,
            "DenyLoan": write,
        }

    def test_status_is_an_enum_on_the_wire(self) -> None:
        tool = next(t for t in CATALOG if t.definition.name == "SearchLoans")
        status = next(p for p in tool.definition.input.parameters if p.name == "status")
        assert status.value_schema.enum == ["pending", "approved", "denied"]


class TestSearchLoans:
    async def test_returns_plausible_surrounding_loans_with_no_filter(self, as_dana) -> None:
        body = await search_loans(as_dana)
        assert body["count"] > 4
        assert "LN-2291" in [loan["loan_id"] for loan in body["loans"]]

    async def test_honours_the_filters(self, as_dana) -> None:
        body = await search_loans(
            as_dana, status=LoanStatus.PENDING, min_amount=90_000, max_amount=100_000
        )
        assert [loan["loan_id"] for loan in body["loans"]] == ["LN-2291"]
        assert body["loans"][0]["amount"] == 95_000


class TestGetLoan:
    async def test_returns_the_full_record_unredacted(self, as_dana) -> None:
        loan = await get_loan(as_dana, loan_id="LN-2291")

        assert loan["borrower_name"] == "Northwind Bakery LLC"
        assert loan["amount"] == 95_000
        # Acts 3 and 4 depend on all of this arriving intact. Whatever the post
        # hook does to it, it does downstream of here.
        assert len(loan["bank_account_number"]) == 16
        assert loan["tax_id"][2] == "-"
        assert "approve_loan" in loan["underwriter_notes"]
        assert "[REDACTED]" not in str(loan)

    async def test_errors_on_an_unknown_loan_naming_it(self, as_dana) -> None:
        with pytest.raises(ToolExecutionError, match="LN-0000"):
            await get_loan(as_dana, loan_id="LN-0000")


class TestIdentity:
    async def test_a_call_without_a_signed_in_user_fails(self, as_nobody) -> None:
        with pytest.raises(ToolExecutionError, match="who is calling"):
            await search_loans(as_nobody)

    async def test_the_tool_has_no_way_to_say_who_is_acting(self) -> None:
        # The actor is whoever the gateway signed in. No tool takes one as an argument.
        for tool in CATALOG:
            names = {p.name for p in tool.definition.input.parameters}
            assert not names & {"actor", "user_id", "decided_by", "email"}, tool.definition.name


class TestDecisions:
    async def test_approving_twice_is_visible_and_attributed(self, as_dana, as_riley) -> None:
        first = await approve_loan(as_dana, loan_id="LN-2292", amount=15_500)
        assert first["status"] == "approved"
        assert [d["decided_by"] for d in first["decisions"]] == [DANA]

        second = await approve_loan(as_riley, loan_id="LN-2292", amount=9_000)
        assert [d["amount"] for d in second["decisions"]] == [15_500, 9_000]
        assert [d["decided_by"] for d in second["decisions"]] == [DANA, RILEY]

    async def test_deny_records_the_reason_verbatim(self, as_dana) -> None:
        reason = "Collateral appraisal is more than twelve months old."
        loan = await deny_loan(as_dana, loan_id="LN-2299", reason=reason)

        assert loan["status"] == "denied"
        assert loan["decisions"][-1] == {
            "decision": "denied",
            "amount": None,
            "reason": reason,
            "decided_by": DANA,
            "decided_at": loan["decisions"][-1]["decided_at"],
        }

    async def test_the_loan_book_is_the_only_state(self, as_dana, as_riley) -> None:
        await deny_loan(as_dana, loan_id="LN-2299", reason="Test decision persists across actors.")
        loan = await get_loan(as_riley, loan_id="LN-2299")
        assert loan["status"] == "denied"

    async def test_an_unknown_loan_is_an_error(self, as_dana) -> None:
        with pytest.raises(ToolExecutionError, match="LN-0000"):
            await approve_loan(as_dana, loan_id="LN-0000", amount=1)
