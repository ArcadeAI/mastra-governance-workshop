"""One `arcade deploy`, one toolkit, every tool.

See `app.py` for the server and the name, and one file per tool family:
`loans.py`, `approvals.py` (with `approvals_message.py`, `approvals_routing.py`,
`approvals_slack.py`, `approvals_store.py`). Importing this
package imports them, which is what registers their `@app.tool` functions.
"""

from __future__ import annotations

from loan_desk.app import app
from loan_desk import approvals as approvals  # noqa: F401  (registers its tools)
from loan_desk import loans as loans  # noqa: F401

__all__ = ["app", "approvals", "loans"]
