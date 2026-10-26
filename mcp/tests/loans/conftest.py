"""Boots the real loan module and a stand-in identity provider.

The four tools are stateless clients, so the only honest test drives them
through the real API: `bun scripts/loans.ts`, the app's loan module on a port
of its own and laid out under `/bank` exactly as the app serves it (#5), with a
fresh `loans.db` in a temp directory. Tokens are validated against a tiny
fake issuer shaped like Okta's: a discovery document, and the userinfo
endpoint it names answering for two known bearer tokens. Userinfo is the one
identity endpoint the API ever calls, and it finds it through discovery.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from arcade_core.schema import ToolAuthorizationContext, ToolContext, ToolSecretItem

from loan_desk.loans import APP_HOST_SECRET

REPO_ROOT = Path(__file__).resolve().parents[3]
LOAN_APP_ENTRYPOINT = REPO_ROOT / "scripts" / "loans.ts"

DANA = "alice@example.test"
RILEY = "charlie@example.test"
TOKENS = {"tok-dana": DANA, "tok-riley": RILEY}


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


class _Userinfo(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        issuer = f"http://127.0.0.1:{self.server.server_port}/oauth2/default"
        if self.path == "/oauth2/default/.well-known/openid-configuration":
            body = json.dumps({"issuer": issuer, "userinfo_endpoint": f"{issuer}/v1/userinfo"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        email = TOKENS.get(token)
        if self.path != "/oauth2/default/v1/userinfo" or email is None:
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"sub": email, "email": email, "email_verified": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="session")
def idp_port() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Userinfo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()


@pytest.fixture(scope="session")
def loan_app_host(idp_port: int) -> str:
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("bun is not installed; the toolkit tests drive the real loan module")

    tmp = Path(tempfile.mkdtemp(prefix="cg-loan-toolkit-"))
    child, port = _boot_loan_app(bun, tmp, idp_port)

    yield f"localhost:{port}"

    _stop(child)
    shutil.rmtree(tmp, ignore_errors=True)


def _boot_loan_app(bun: str, tmp: Path, idp_port: int) -> tuple[subprocess.Popen, int]:
    """Starts the loan module on a free port and waits for `/bank/health`.

    In a session of its own, so `_stop` can take down its process group. A
    child that lost its port says EADDRINUSE and exits (or keeps running,
    listening nowhere); either way it is stopped and started again on a new
    port, and a lost attempt leaves nothing running.
    """
    for _ in range(_BOOT_ATTEMPTS):
        port = _free_port()
        env = {
            **os.environ,
            "PORT": str(port),
            "LOANS_DB_PATH": str(tmp / "loans.db"),
            "IDP_ISSUER": f"http://127.0.0.1:{idp_port}/oauth2/default",
        }
        child = subprocess.Popen(
            [bun, str(LOAN_APP_ENTRYPOINT)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stderr = _drain(child.stderr)
        try:
            if _wait_healthy(child, port, stderr):
                return child, port
        except BaseException:
            _stop(child)
            raise
        _stop(child)
    raise RuntimeError(f"loan-app lost the port race {_BOOT_ATTEMPTS} times in a row")


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
            raise RuntimeError(f"loan-app exited: {output}")
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/bank/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        if time.time() > deadline:
            raise RuntimeError(f"loan-app did not come up: {''.join(stderr)}")
        time.sleep(0.05)


def make_context(loan_app_host: str, token: str) -> ToolContext:
    """What the Arcade engine hands a tool at runtime: a token and a secret."""
    return ToolContext(
        authorization=ToolAuthorizationContext(token=token),
        secrets=[ToolSecretItem(key=APP_HOST_SECRET, value=loan_app_host)],
        user_id=TOKENS.get(token),
    )


@pytest.fixture
def as_dana(loan_app_host: str) -> ToolContext:
    return make_context(loan_app_host, "tok-dana")


@pytest.fixture
def as_riley(loan_app_host: str) -> ToolContext:
    return make_context(loan_app_host, "tok-riley")


@pytest.fixture
def as_nobody(loan_app_host: str) -> ToolContext:
    return make_context(loan_app_host, "tok-forged")
