"""Boots the real workshop loan service.

The four tools are stateless clients, so the only honest test drives them
through the real API: `service/` (uvicorn) on a port of its own, against the
database `TEST_DATABASE_URL` names (the portal's tables must exist there), with
a throwaway attendee whose loan book the tools reach by its token. The actor is
whoever the context says the gateway signed in (`user_id`); since 2026-10-17 no
identity provider is involved. Without `TEST_DATABASE_URL` these tests skip.
"""

import asyncio
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from arcade_core.schema import ToolContext, ToolSecretItem

from loan_desk.loans import APP_HOST_SECRET, LOAN_BOOK_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_DIR = Path(os.environ.get("TEST_LOAN_SERVICE_DIR", REPO_ROOT.parent / "workshop-portal" / "service")).expanduser().resolve()

DANA = "alice@example.test"
RILEY = "charlie@example.test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# The port is released before the child binds it, so another process can take
# it in between; CI lost that race once (#9). A boot that says EADDRINUSE is
# retried on a new port, as `app-test/child.ts::retryOnPortRace` does.
_BOOT_ATTEMPTS = 5
_BOOT_TIMEOUT_S = 20.0
# Between SIGTERM and SIGKILL, as `app-test/supervise.ts` allows.
_STOP_GRACE_S = 3.0


def _lost_port_race(text: str) -> bool:
    return "EADDRINUSE" in text or "address already in use" in text.lower()


def _drain(stream) -> list[str]:
    """Reads a piped stream as it arrives, so a noisy child never blocks on a full pipe."""
    chunks: list[str] = []

    def pump() -> None:
        for line in iter(stream.readline, b""):
            chunks.append(line.decode(errors="replace"))

    threading.Thread(target=pump, daemon=True).start()
    return chunks


def _stop(child: subprocess.Popen) -> None:
    """Stops the child's whole process group: SIGTERM, then SIGKILL after a grace period (#9).

    The child leads its own group (`start_new_session=True`), so the signal
    reaches anything it started too, the way `app-test/supervise.ts` stops a
    harness child.
    """
    # ESRCH when the group is empty. EPERM too, on macOS, when all that is left
    # of it is the child's own unreaped zombie. Either way nothing is running.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        child.wait(timeout=_STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    # Whatever is left: the child, if it ignored SIGTERM, and anything it started.
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    child.wait()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; the toolkit tests drive the real loan service")
    return url


@pytest.fixture
def loan_book(database_url: str) -> dict[str, str]:
    """A throwaway attendee in the portal's tables whose book the tools reach, removed afterwards."""
    import asyncpg

    token = f"wp_test_{uuid.uuid4().hex}"

    async def make() -> tuple[str, str]:
        conn = await asyncpg.connect(database_url)
        wid = await conn.fetchval(
            "insert into workshops (slug, title, join_code) values ($1, 'loan tool test', $2) returning id",
            f"tool-test-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex[:6].upper(),
        )
        aid = await conn.fetchval(
            "insert into attendees (workshop_id, email, loan_service_token) values ($1, $2, $3) returning id", wid, DANA, token
        )
        await conn.close()
        return str(wid), str(aid)

    async def drop(wid: str, aid: str) -> None:
        conn = await asyncpg.connect(database_url)
        for table in ("loan_decisions", "loans", "loan_books"):
            await conn.execute(f"delete from {table} where attendee_id = $1::uuid", aid)
        await conn.execute("delete from workshops where id = $1::uuid", wid)
        await conn.close()

    wid, aid = asyncio.run(make())
    yield {"token": token, "attendee_id": aid}
    asyncio.run(drop(wid, aid))


@pytest.fixture(scope="session")
def loan_app_host(database_url: str) -> str:
    child, port = _boot_service(database_url)
    yield f"localhost:{port}"
    _stop(child)


def _boot_service(database_url: str) -> tuple[subprocess.Popen, int]:
    """Starts the loan service on a free port and waits for `/bank/health`, retrying a lost port race."""
    if not (SERVICE_DIR / "loan_service" / "main.py").is_file():
        pytest.fail("Loan service source is missing. Set TEST_LOAN_SERVICE_DIR to workshop-portal/service (see mcp/LOANS.md).")
    for _ in range(_BOOT_ATTEMPTS):
        port = _free_port()
        env = {**os.environ, "DATABASE_URL": database_url, "LOAN_SERVICE_ADMIN_TOKEN": "test"}
        child = subprocess.Popen(
            ["uv", "run", "uvicorn", "loan_service.main:app", "--port", str(port), "--log-level", "warning"],
            cwd=SERVICE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
        )
        stderr = _drain(child.stderr)
        try:
            if _wait_healthy(child, port, stderr):
                return child, port
        except BaseException:
            _stop(child)
            raise
        _stop(child)
    raise RuntimeError(f"loan service lost the port race {_BOOT_ATTEMPTS} times in a row")


def _wait_healthy(child: subprocess.Popen, port: int, stderr: list[str]) -> bool:
    """True once the child answers; False if it lost the port race; raises otherwise."""
    deadline = time.time() + _BOOT_TIMEOUT_S
    while True:
        if _lost_port_race("".join(stderr)):
            return False
        if child.poll() is not None:
            # Give the pipe a moment to deliver the child's last words.
            time.sleep(0.05)
            output = "".join(stderr)
            if _lost_port_race(output):
                return False
            raise RuntimeError(f"loan service exited: {output}")
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/bank/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        if time.time() > deadline:
            raise RuntimeError(f"loan service did not come up: {''.join(stderr)}")
        time.sleep(0.05)


def make_context(loan_app_host: str, book_token: str, actor: str | None) -> ToolContext:
    """What the Arcade engine hands a tool at runtime: who the gateway signed in, and the two secrets."""
    return ToolContext(
        secrets=[
            ToolSecretItem(key=APP_HOST_SECRET, value=loan_app_host),
            ToolSecretItem(key=LOAN_BOOK_SECRET, value=book_token),
        ],
        user_id=actor,
    )


@pytest.fixture
def as_dana(loan_app_host: str, loan_book: dict[str, str]) -> ToolContext:
    return make_context(loan_app_host, loan_book["token"], DANA)


@pytest.fixture
def as_riley(loan_app_host: str, loan_book: dict[str, str]) -> ToolContext:
    return make_context(loan_app_host, loan_book["token"], RILEY)


@pytest.fixture
def as_nobody(loan_app_host: str, loan_book: dict[str, str]) -> ToolContext:
    """A gateway that did not sign anyone in: no user id at all."""
    return make_context(loan_app_host, loan_book["token"], None)
