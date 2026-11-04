"""The two tools, end to end against a real approvals store and a real Slack.

Neither service is the real one, and neither is mocked: both are HTTP servers
this suite boots, and the tools reach them over the wire. So what these tests
assert is what was actually sent — the record that was persisted, and the
message Slack was handed.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import pytest
from arcade_core.errors import ToolExecutionError

from loan_desk import app, approvals
from loan_desk.approvals import Decision, decide, request_approval
from tests import tools_of
from loan_desk.approvals_message import format_amount
from loan_desk.approvals_slack import SlackError, lookup_user_by_email, post_message
from loan_desk.approvals_store import base_url
from tests.approvals.conftest import (
    CAST,
    DANA,
    MORGAN,
    RILEY,
    SAM,
    SLACK_TOKEN,
    STORE_TOKEN,
    SlackState,
    StoreState,
    make_context,
)

ACT_TWO = {
    "action": "approve_loan",
    "resource_id": "LN-2291",
    "amount": 95_000.0,
    "justification": "Eleven years in business, 742 credit score, $1.4M annual revenue.",
}


CATALOG = tools_of(approvals)


class TestDefinition:
    """What Arcade sees when it loads the toolkit: this file's two tools."""

    def test_exposes_exactly_the_two_approval_tools(self) -> None:
        # PascalCase, by arcade-mcp itself, before Arcade ever sees them. Under
        # the one server, MCPApp(name="loan_desk"), these are LoanDesk.RequestApproval
        # and LoanDesk.Decide, and the wire names through a gateway are
        # LoanDesk_RequestApproval and LoanDesk_Decide.
        assert sorted(t.definition.name for t in CATALOG) == ["Decide", "RequestApproval"]

    def test_the_server_is_named_so_arcade_files_it_under_LoanDesk(self) -> None:
        # `arcade deploy` reads this off `initialize` and PascalCases it on
        # underscores. `.env.example` pins ARCADE_TOOLKIT=LoanDesk,
        # and a policy rule keyed on the wrong string matches nothing.
        assert app.name == "loan_desk"
        assert "-" not in app.name

    def test_request_approval_requires_slack_with_exactly_four_scopes(self) -> None:
        tool = next(t for t in CATALOG if t.definition.name == "RequestApproval")
        auth = tool.definition.requirements.authorization
        assert auth is not None
        assert auth.provider_id == "slack"
        # Exactly the four spike #3 exercised. Four rather than three because
        # users:read is a prerequisite for users:read.email and Slack refuses
        # the authorize request outright without it; im:write is what
        # conversations.open needs.
        assert sorted(auth.oauth2.scopes) == [
            "chat:write",
            "im:write",
            "users:read",
            "users:read.email",
        ]

    def test_decide_requires_no_oauth_because_a_refusal_there_would_be_invisible(self) -> None:
        # Arcade evaluates auth requirements before /pre, so an auth refusal
        # fires no hook, writes no audit row and shows nothing on the panel.
        # decide's refusal is the beat #19 exists to show, so its authority
        # check has to be a /pre decision, not a credential one.
        tool = next(t for t in CATALOG if t.definition.name == "Decide")
        assert tool.definition.requirements.authorization is None

    def test_every_tool_declares_the_secrets_it_reads(self) -> None:
        secrets = {
            t.definition.name: sorted(s.key for s in t.definition.requirements.secrets or [])
            for t in CATALOG
        }
        assert secrets == {
            "RequestApproval": ["APPROVALS_STORE_TOKEN", "APP_PUBLIC_HOST"],
            "Decide": ["APPROVALS_STORE_TOKEN", "APP_PUBLIC_HOST"],
        }

    def test_describes_every_tool_and_every_argument(self) -> None:
        for tool in CATALOG:
            assert tool.definition.description, tool.definition.name
            for param in tool.definition.input.parameters:
                assert param.description, f"{tool.definition.name}.{param.name}"

    def test_required_arguments_are_the_signature_the_issue_specifies(self) -> None:
        required = {
            t.definition.name: sorted(p.name for p in t.definition.input.parameters if p.required)
            for t in CATALOG
        }
        assert required == {
            "RequestApproval": ["action", "amount", "justification", "resource_id"],
            "Decide": ["decision", "request_id"],
        }

    def test_no_tool_lets_the_caller_name_the_approver(self) -> None:
        # The agent does not choose who is asked. There is no argument through
        # which it could, which is a stronger statement than a rule that
        # ignores one.
        for tool in CATALOG:
            names = {p.name for p in tool.definition.input.parameters}
            assert not names & {
                "approver",
                "approver_id",
                "approver_email",
                "route_to",
                "requester_id",
                "user_id",
                "actor",
            }, tool.definition.name

    def test_decision_is_an_enum_on_the_wire(self) -> None:
        tool = next(t for t in CATALOG if t.definition.name == "Decide")
        decision = next(p for p in tool.definition.input.parameters if p.name == "decision")
        assert decision.value_schema.enum == ["approved", "denied"]


class TestTheChannelAnnouncement:
    """The record's `slack_channel`: the room sees every request on every route; nobody decides there."""

    async def test_with_a_channel_configured_the_request_is_posted_there_too(
        self, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(make_context(store.host, DANA.user_id), **ACT_TWO)
        assert [p["channel"] for p in slack.posted] == ["#loan-desk"]
        announcement = slack.posted[0]
        # Names the requester, the loan, the amount and the link; the approver is told who.
        assert DANA.display_name in announcement["text"]
        assert ACT_TWO["resource_id"] in announcement["text"]
        assert format_amount(ACT_TWO["amount"]) in announcement["text"]
        assert result["approval_url"] in announcement["text"]
        # And no buttons: the decision is made signed in, on the approval page.
        assert all(block["type"] != "actions" for block in announcement["blocks"])
        assert "button" not in json.dumps(announcement["blocks"])
        assert result["slack_channel"] == "#loan-desk"
        assert result["slack_channel_ts"]

    @pytest.mark.parametrize("route", ["email", "facilitator"])
    async def test_an_email_or_facilitator_route_announces_but_sends_no_dm(
        self, as_dana, store: StoreState, slack: SlackState, route: str
    ) -> None:
        # The store sends the mail, or the queue is the dashboard; the room still sees it.
        store.channel = route
        result = await request_approval(as_dana, **ACT_TWO)
        assert [p["channel"] for p in slack.posted] == ["#loan-desk"]
        assert len(store.records) == 1
        assert result["notice"] == route
        assert result["slack_channel"] == "#loan-desk"
        assert "slack_message_ts" not in result

    @pytest.mark.parametrize("route", ["email", "facilitator"])
    async def test_without_a_workshop_channel_those_routes_touch_slack_not_at_all(
        self, as_dana, store: StoreState, slack: SlackState, route: str
    ) -> None:
        store.channel = route
        store.slack_channel = None
        result = await request_approval(as_dana, **ACT_TWO)
        assert slack.posted == [] and slack.calls == []
        assert len(store.records) == 1
        assert result["notice"] == route
        assert "slack_channel" not in result and "slack_message_ts" not in result

    async def test_a_channel_that_refuses_is_reported_and_the_request_still_stands(
        self, store: StoreState, slack: SlackState
    ) -> None:
        # The approvers' copy went (the store posted it); the room's copy did not. Not an
        # error for the caller: the approvers have been told, and retrying would duplicate.
        slack.fail_with = "not_in_channel"
        slack.fail_on = "chat.postMessage:1"
        result = await request_approval(make_context(store.host, DANA.user_id), **ACT_TWO)
        assert result["notice"] == "slack" and result["approvers_post_ts"]
        assert result["slack_channel_error"] == "not_in_channel"
        assert len(store.records) == 1


class TestRequestApproval:
    async def test_the_headline_case_routes_to_riley_and_returns_who_was_asked(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_dana, **ACT_TWO)

        assert result["approver"] == RILEY.user_id
        assert result["approver_display_name"] == RILEY.display_name
        assert result["request_id"].startswith("apr_")
        assert result["status"] == "pending"
        # Michael was sufficient and deliberately not chosen.
        assert result["candidate_approvers"] == [RILEY.user_id, MORGAN.user_id]
        assert result["required_clearance"] == 95_000.0

    async def test_persists_requester_routed_approver_scope_and_creation_time(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_dana, **ACT_TWO)

        assert len(store.created) == 1
        written = store.created[0]
        assert written["requester_id"] == DANA.user_id
        assert written["approver_id"] == RILEY.user_id
        assert written["candidate_approver_ids"] == [RILEY.user_id, MORGAN.user_id]
        # The scope of what was asked for: the action, the resource, the amount.
        assert written["action"] == "approve_loan"
        assert written["resource_id"] == "LN-2291"
        assert written["amount"] == 95_000.0
        assert written["required_clearance"] == 95_000.0
        assert written["justification"] == ACT_TWO["justification"]

        record = store.records[result["request_id"]]
        assert record["created_at"] == "2026-09-09T12:00:00.000Z"
        assert record["status"] == "pending"

    async def test_the_request_id_comes_from_the_store_not_from_the_toolkit(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # An id minted here would be an id the caller could predict.
        result = await request_approval(as_dana, **ACT_TWO)
        assert "id" not in store.created[0]
        assert result["request_id"] in store.records

    async def test_the_agents_own_identity_is_who_the_request_is_from(
        self, store: StoreState, slack: SlackState
    ) -> None:
        # Not an argument. context.user_id is Arcade's, set outside the model.
        await request_approval(make_context(store.host, SAM.user_id), **ACT_TWO)
        assert store.created[0]["requester_id"] == SAM.user_id

    async def test_the_requester_is_excluded_even_when_they_could_approve_it(
        self, as_riley, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_riley, **ACT_TWO)
        assert result["approver"] == MORGAN.user_id
        assert RILEY.user_id not in result["candidate_approvers"]

    async def test_nobody_eligible_records_nothing_and_messages_nobody(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        with pytest.raises(ToolExecutionError, match="Nobody holds authority"):
            await request_approval(as_dana, **{**ACT_TWO, "amount": 9_000_000.0})

        # Not a quiet escalation to the highest authority, and not a request
        # the agent can believe it made.
        assert store.created == []
        assert slack.posted == []

    async def test_a_negative_amount_is_refused_rather_than_routed(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        with pytest.raises(ToolExecutionError, match="not an amount"):
            await request_approval(as_dana, **{**ACT_TWO, "amount": -1.0})
        assert store.created == []

    async def test_a_store_that_will_not_record_means_nobody_is_messaged(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        store.fail_with = 503
        with pytest.raises(ToolExecutionError):
            await request_approval(as_dana, **ACT_TWO)
        assert slack.posted == []


class TestTheSlackMessage:
    async def test_lookup_uses_get_query_encoding_and_bearer_header(
        self, slack: SlackState
    ) -> None:
        email = "riley+approvals@example.test"
        slack.users[email] = "U_PLUS"

        assert await lookup_user_by_email(SLACK_TOKEN, email) == "U_PLUS"

        request = slack.requests[-1]
        assert request["method"] == "GET"
        assert request["path"] == "/api/users.lookupByEmail"
        assert parse_qs(request["query"], keep_blank_values=True) == {"email": [email]}
        assert request["query"].count("&") == 0
        assert "%2B" in request["query"].upper()
        assert request["body"] == b""
        assert request["authorization"] == f"Bearer {SLACK_TOKEN}"
        assert request["content_type"] is None
        assert "token" not in request["query"].lower()

    async def test_the_one_post_keeps_method_and_payload(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        await request_approval(as_dana, **ACT_TWO)

        # The room's copy only. No lookup and no DM since 2026-10-17, and since
        # that night the approvers' copy is the store's post as the bot.
        assert [(request["method"], request["path"]) for request in slack.requests] == [
            ("POST", "/api/chat.postMessage"),
        ]
        assert json.loads(slack.requests[0]["body"]) == slack.posted[0]
        assert all(
            request["content_type"] == "application/json; charset=utf-8"
            for request in slack.requests
        )
        assert all(request["authorization"] == f"Bearer {SLACK_TOKEN}" for request in slack.requests)

    async def test_posts_as_the_requester_using_her_own_delegated_token(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        await request_approval(as_dana, **ACT_TWO)
        # Every call carried Alice's user token. There is no bot token in this
        # repo, and the post renders under her name (spike #3).
        assert set(slack.seen_tokens) == {SLACK_TOKEN}

    async def test_bob_requesting_for_charlie_uses_the_routed_email_and_bobs_token(
        self, store: StoreState, slack: SlackState
    ) -> None:
        # This mirrors the observed new-persona run locally: Bob is the
        # requester, Charlie is the deterministic minimum-sufficient recipient
        # named in the post, and Slack receives Bob's delegated user token.
        bob_token = "xoxp-bob-token"
        await request_approval(
            make_context(store.host, SAM.user_id, slack_token=bob_token), **ACT_TWO
        )

        assert RILEY.display_name in slack.posted[0]["text"]
        assert slack.looked_up_emails == []
        assert set(slack.seen_tokens) == {bob_token}

    async def test_the_approvers_copy_is_the_stores_post_and_the_requester_never_posts_there(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_dana, **ACT_TWO)

        # The requester's token never reaches the approvers' channel (she is not a
        # member) and no DM is opened: the store posted there as the bot and said so.
        assert not any(p["channel"].startswith("D_") for p in slack.posted)
        assert all(p["channel"] != "#approvals" for p in slack.posted)
        assert result["approvers_channel"] == "#approvals"
        assert result["approvers_post_ts"] == "1700000000.000100"
        assert result["notice"] == "slack"

    async def test_the_approvals_channel_falls_back_to_approvals(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # An older store that does not stamp the channel still names where the approvers sit.
        store.approvals_channel = None
        result = await request_approval(as_dana, **ACT_TWO)
        assert result["approvers_channel"] == "#approvals"

    async def test_only_chat_post_message_is_called(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        await request_approval(as_dana, **ACT_TWO)
        assert slack.calls == ["chat.postMessage"]

    async def test_with_self_approval_on_the_requester_posts_both_copies_with_her_token(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # The attendees sit in the approvers' channel, so the store leaves the
        # approvers' copy to the requester's own tool: approvers' channel first,
        # then the room, both with her delegated token, no bot and no DM.
        store.self_approval = True
        result = await request_approval(as_dana, **ACT_TWO)
        assert [p["channel"] for p in slack.posted] == ["#approvals", "#loan-desk"]
        assert set(slack.seen_tokens) == {SLACK_TOKEN}
        assert RILEY.display_name in slack.posted[0]["text"]
        assert any(block["type"] == "actions" for block in slack.posted[0]["blocks"])
        assert all(block["type"] != "actions" for block in slack.posted[1]["blocks"])
        assert result["approvers_channel"] == "#approvals" and result["approvers_post_ts"]
        assert result["slack_channel_ts"]

    async def test_with_self_approval_on_a_refused_approvers_post_is_an_error_and_nothing_else_is_posted(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        store.self_approval = True
        slack.fail_on = "chat.postMessage:1"
        slack.fail_with = "not_in_channel"
        with pytest.raises(ToolExecutionError, match="not_in_channel") as raised:
            await request_approval(as_dana, **ACT_TWO)
        assert "Slack method chat.postMessage failed" in str(raised.value)
        assert "Do not retry" in str(raised.value)
        assert slack.posted == []
        assert len(store.created) == 1

    async def test_a_failed_approvers_post_is_an_error_and_nothing_else_is_posted(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # The store recorded the request but its post to the approvers' channel failed:
        # nobody has been told, so the agent must not read this as "requested".
        store.approvers_post_error = "not_in_channel"
        with pytest.raises(ToolExecutionError, match="not_in_channel") as raised:
            await request_approval(as_dana, **ACT_TWO)
        message = str(raised.value)
        assert "recorded and routed" in message
        assert "notice was not delivered" in message
        assert "Do not retry" in message
        assert RILEY.display_name in message
        assert "#approvals" in message
        assert slack.posted == []
        assert len(store.created) == 1

    async def test_the_posted_payload_is_block_kit_and_states_everything(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_dana, **ACT_TWO)
        posted = slack.posted[0]

        assert posted["blocks"], "blocks is what makes it Block Kit rather than text"
        assert posted["text"], "without text the post arrives as a silent, empty push"
        rendered = json.dumps(posted["blocks"])
        for expected in (
            DANA.display_name,          # requester
            "approve_loan",             # action
            "LN-2291",                  # resource
            "$95,000.00",               # amount
            "approval authority",       # the rule tripped
            ACT_TWO["justification"],   # justification
            result["request_id"],
        ):
            assert expected in rendered, expected

    async def test_names_the_rule_when_the_control_plane_names_it(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        store.rule = {
            "id": "act2_amount_exceeds_clearance",
            "description": "An approval above the caller's authority is blocked.",
        }
        await request_approval(as_dana, **ACT_TWO)
        rendered = json.dumps(slack.posted[0]["blocks"])
        assert "act2_amount_exceeds_clearance" in rendered
        assert "An approval above the caller's authority is blocked." in rendered

    async def test_states_the_authority_that_was_exceeded_when_it_does_not(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # store.rule is None by default. The message must still say what was
        # tripped — a blank there is an approver who has to go and ask.
        await request_approval(as_dana, **ACT_TWO)
        rendered = json.dumps(slack.posted[0]["blocks"])
        assert "exceeds Alice's approval authority of $50,000.00" in rendered

    async def test_the_link_is_the_approval_page_for_this_request_id_alone(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        result = await request_approval(as_dana, **ACT_TWO)
        # On the app's own host since #6: the store and the approval page are
        # both the app, behind the one `APP_PUBLIC_HOST` secret. The room's copy
        # carries it as a link in the text, never as a button.
        assert result["approval_url"] == f"{base_url(store.host)}/approvals/{result['request_id']}"
        assert result["approval_url"] in slack.posted[0]["text"]
        assert all(block["type"] != "actions" for block in slack.posted[0]["blocks"])

    @pytest.mark.parametrize("smell", ["token", "signature", "sig=", "hmac", "jwt", "secret"])
    async def test_nothing_that_went_to_slack_carries_authority(
        self, as_dana, store: StoreState, slack: SlackState, smell: str
    ) -> None:
        # The requester can read the post she sent. Possession of this message is
        # not permission; authorization happens at click time, in #19.
        await request_approval(as_dana, **ACT_TWO)
        assert smell not in json.dumps(slack.posted[0]).lower()

    async def test_a_room_copy_that_fails_is_reported_and_the_request_stands(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # The approvers have been told by the store; the room's copy is a courtesy.
        # An agent told "failed" here would retry and create a second request.
        slack.fail_on = "chat.postMessage"
        slack.fail_with = "invalid_arguments"
        result = await request_approval(as_dana, **ACT_TWO)
        assert result["slack_channel_error"] == "invalid_arguments"
        assert result["approvers_post_ts"]
        assert len(store.created) == 1
        assert slack.calls == ["chat.postMessage"]

    async def test_slack_diagnostic_keeps_safe_detail_but_never_echoes_secrets_or_payload(
        self, slack: SlackState
    ) -> None:
        slack.fail_on = "chat.postMessage"
        slack.fail_with = "invalid_arguments"
        slack.fail_detail = {
            "needed": "chat:write",
            "provided": "users:read,users:read.email",
            "token": SLACK_TOKEN,
            "user": "U_RILEY",
            "channel": "D_RILEY",
            "request_body": ACT_TWO,
        }

        with pytest.raises(SlackError) as raised:
            await post_message(SLACK_TOKEN, "#loan-desk", "approve_loan LN-2291", [])

        diagnostic = raised.value.diagnostic_message()
        assert "detail=needed=chat:write; provided=users:read,users:read.email" in diagnostic
        for forbidden in (SLACK_TOKEN, "U_RILEY", "D_RILEY", "LN-2291", "approve_loan"):
            assert forbidden not in diagnostic

    async def test_a_slack_200_that_says_ok_false_is_a_failure_not_a_delivery(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # The trap: Slack refuses with HTTP 200. A client reading the status
        # code would report a message that was never sent.
        slack.fail_with = "missing_scope"
        result = await request_approval(as_dana, **ACT_TWO)
        assert result["slack_channel_error"] == "missing_scope"
        assert "slack_channel_ts" not in result


class TestDecide:
    async def test_records_the_outcome_against_the_request(
        self, as_dana, as_riley, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)

        record = await decide(
            as_riley,
            request_id=created["request_id"],
            decision=Decision.APPROVED,
            note="Coverage checks out.",
        )

        assert record["id"] == created["request_id"]
        assert record["status"] == "approved"
        assert record["note"] == "Coverage checks out."
        assert record["decided_at"] == "2026-09-09T12:05:00.000Z"

    async def test_records_who_decided_from_arcades_identity_not_an_argument(
        self, as_dana, as_riley, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)
        await decide(as_riley, request_id=created["request_id"], decision=Decision.DENIED)

        assert store.decisions[-1]["decided_by"] == RILEY.user_id
        assert store.decisions[-1]["decision"] == "denied"
        assert store.decisions[-1]["note"] is None

    async def test_records_a_denial_as_readily_as_an_approval(
        self, as_dana, as_riley, store: StoreState, slack: SlackState
    ) -> None:
        created = await request_approval(as_dana, **ACT_TWO)
        record = await decide(
            as_riley, request_id=created["request_id"], decision=Decision.DENIED, note="No."
        )
        assert record["status"] == "denied"

    async def test_it_does_not_check_authority_itself(
        self, as_dana, store: StoreState, slack: SlackState
    ) -> None:
        # Alice deciding her own request is exactly what separation of duties
        # forbids — and this tool does not stop her, on purpose. The control is
        # a /pre decision on LoanDesk.Decide, outside the tool and outside the
        # model, enforced in #19. A check here as well would put the same rule
        # in two places and let one of them drift.
        created = await request_approval(as_dana, **ACT_TWO)
        record = await decide(
            as_dana, request_id=created["request_id"], decision=Decision.APPROVED
        )
        assert record["status"] == "approved"
        assert store.decisions[-1]["decided_by"] == DANA.user_id

    async def test_an_unknown_request_is_an_error_naming_it(
        self, as_riley, store: StoreState
    ) -> None:
        with pytest.raises(ToolExecutionError, match="apr_nosuchthing"):
            await decide(
                as_riley, request_id="apr_nosuchthing", decision=Decision.APPROVED
            )


class TestTheStoreIsNotOpenToTheInternet:
    async def test_a_call_without_the_shared_token_is_refused(
        self, store: StoreState, slack: SlackState
    ) -> None:
        # Anyone who could write to the approvals store could manufacture the
        # request a human then acts on.
        context = make_context(store.host, DANA.user_id)
        context.secrets = [
            s if s.key != "APPROVALS_STORE_TOKEN" else type(s)(key=s.key, value="wrong")
            for s in context.secrets or []
        ]
        with pytest.raises(ToolExecutionError):
            await request_approval(context, **ACT_TWO)
        assert store.created == []

    def test_the_fixture_store_actually_checks_it(self) -> None:
        # Guards the test above from passing for the wrong reason.
        assert STORE_TOKEN
        assert len(CAST) == 4
