"""The one server, and the one toolkit.

`MCPApp(name="loan_desk")` is what `arcade deploy` reads off `initialize` and files the
deployment as, and `loan_desk` PascalCased — `LoanDesk` — is the toolkit name Arcade
prefixes every tool with: `LoanDesk_SearchLoans`, `LoanDesk_RequestApproval`.
`ARCADE_TOOLKIT` in `.env` and the `$TOOLKIT` placeholder in
`governance.json` both mean this name, so a rule keyed on `LoanDesk.ApproveLoan`
matches the call Arcade makes.

The tools live one file each next to this one — `loans.py`, `approvals.py` and its
helpers — and each does `from loan_desk.app import app` and
decorates with `@app.tool`. Importing `loan_desk` (`__init__.py`) imports them all, so
`server.py` has every tool registered before `app.run()`.

The name is underscores only, per MCPApp: a hyphen is rejected at construction, and a
hyphenated toolkit could not form a parseable Arcade tool name anyway (measured in spike #2).
"""

from __future__ import annotations

from arcade_mcp_server import MCPApp

app = MCPApp(
    name="loan_desk",
    version="1.0.0",
    instructions=(
        "Loan desk for a B2B software company. Loan applications are identified by IDs of "
        "the form LN-0000: search_loans finds requests, get_loan reads one in full, "
        "approve_loan and deny_loan record a decision on one. request_approval "
        "records a request for one person's approval of an action and notifies the "
        "approver it routes to; decide records that person's answer."
    ),
)

__all__ = ["app"]
