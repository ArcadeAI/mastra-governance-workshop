"""What the model is told about each tool carries no behaviour.

`DESIGN.md` → No model-side controls: the system prompt and **every tool
description** carry no behavioural instruction in either direction — nothing
about confirming, refusing, escalating, retrying, caution or irreversibility.
Measured on #14: one "irreversible, no undo" line made Claude ask permission,
and `/pre` never fired.

Read off `app._catalog`, which is the definition `arcade deploy` publishes, and
not off a copy. The gateway stand-in keeps its own copy of these sentences, and
until #8 the TypeScript guard read that copy: it never checked these at all,
and this file still said "there is no undo". The same vocabulary is barred in
`mcp/tests/approvals/test_descriptions.py` and in `app-test/behaviour.ts`;
change all three together.
"""

import re

from loan_desk import app, loans
from tests import tools_of

BARRED = re.compile(
    r"\b(escalat\w*|refus\w*|retr(?:y|ies|ying)|confirm\w*|caution\w*|irreversib\w*|undo"
    r"|only then|and stop|wait for|always|use this only|you (?:should|must|do not|can))\b",
    re.IGNORECASE,
)


def _sentences():
    """Every string of this toolkit's that reaches the model, named by where it sits."""
    for tool in tools_of(loans):
        definition = tool.definition
        yield definition.name, definition.description
        for param in definition.input.parameters:
            yield f"{definition.name}.{param.name}", param.description
        if definition.output is not None:
            yield f"{definition.name} -> output", definition.output.description
    yield "app.instructions", app.instructions


def test_every_description_is_checked():
    # Four tools, so a catalog that silently stopped loading cannot pass by
    # having nothing to check.
    names = {where for where, _ in _sentences() if "." not in where and "->" not in where}
    assert names == {"SearchLoans", "GetLoan", "ApproveLoan", "DenyLoan"}


def test_no_description_instructs_the_model():
    offending = {
        where: match.group(0)
        for where, text in _sentences()
        if text and (match := BARRED.search(text))
    }
    assert offending == {}
