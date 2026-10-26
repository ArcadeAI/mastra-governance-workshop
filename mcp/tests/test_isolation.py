"""`mcp/` is the one `arcade deploy`, and nothing else in the repo leans on it.

Two directions, and both matter:

  * nothing under the app reaches *into* `mcp/`, so it ships with `arcade deploy`
    and never with the app's image or workspaces;
  * nothing in its *runtime* reaches out of it, so it can be lifted into another
    repo whole.

The tests do reach out — they read the cross-language routing cases under
`gate/schema/contract/`, deliberately, because agreement with the
TypeScript router is the thing worth checking.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = REPO_ROOT / "mcp"
RUNTIME = TOOLKIT / "loan_desk"

#: Everything a copy of the repo does not need in order to answer "does
#: deleting this directory break anything". Skipping these is what keeps the
#: copy under a second instead of minutes.

#: The web app's own directories. It lives at the repo root since #3, so a
#: sweep of `apps/` and `packages/` alone would no longer read it.
APP_DIRS = ("app", "components", "lib", "scripts", "app-test")


def _typescript_trees(root: Path) -> list[Path]:
    """Every directory holding a service's or package's TypeScript."""
    return [root / "gate", root / "api", root / "auth", *(root / name for name in APP_DIRS)]


def _sources(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    skip = {"node_modules", ".venv", "__pycache__", ".next", "dist", ".git"}
    return [
        path
        for path in root.rglob("*")
        if path.suffix in suffixes
        and path.is_file()
        and not any(part in skip for part in path.parts)
    ]


class TestNothingReachesIn:
    def test_no_typescript_imports_this_toolkit(self) -> None:
        # An import or a require, not a mention: a comment naming the toolkit
        # is documentation and survives its deletion intact.
        reaches_in = re.compile(r"""(?:from|import|require\()\s*['"][^'"]*mcp/""")
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for directory in _typescript_trees(REPO_ROOT)
            for path in _sources(directory, (".ts", ".tsx"))
            if reaches_in.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], offenders

    def test_it_is_not_a_bun_workspace(self) -> None:
        # Deleting a workspace member breaks `bun install`. This is not one.
        manifest = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        assert not any("mcp" in pattern for pattern in manifest["workspaces"])

    def test_it_is_not_in_the_app_image(self) -> None:
        # It ships with `arcade deploy`, not in the app's container; an image
        # that copied it would make deleting the directory a failed build.
        ignored = [
            line.strip()
            for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert "mcp" in ignored, ignored
        copies = [
            line
            for line in (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if re.match(r"\s*(COPY|ADD)\b", line, re.IGNORECASE) and "mcp" in line
        ]
        assert copies == [], copies

    def test_it_carries_no_package_manifest_the_workspace_could_pick_up(self) -> None:
        assert not (TOOLKIT / "package.json").exists()


class TestNothingReachesOut:
    def test_the_runtime_imports_only_itself_and_its_declared_dependencies(self) -> None:
        allowed_prefixes = (
            "loan_desk",
            "arcade_core",
            "arcade_mcp_server",
            "httpx",
        )
        stdlib = {"__future__", "enum", "os", "math", "dataclasses", "typing", "sys", "json", "re", "base64", "urllib.parse", "types"}
        for path in _sources(RUNTIME, (".py",)):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped.startswith(("import ", "from ")):
                    continue
                module = stripped.split()[1]
                assert module in stdlib or module.startswith(allowed_prefixes), (
                    f"{path.relative_to(REPO_ROOT)}: {stripped}"
                )

    def test_the_runtime_reads_no_file_outside_this_directory(self) -> None:
        # A fixture path into packages/ would make the deployed toolkit depend
        # on a repo layout it does not ship with.
        for path in _sources(RUNTIME, (".py",)):
            source = path.read_text(encoding="utf-8")
            assert "parents[" not in source, path.relative_to(REPO_ROOT)
            assert "open(" not in source, path.relative_to(REPO_ROOT)

