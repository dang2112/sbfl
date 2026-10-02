from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from sbfl.model import TestEvidence
from sbfl.trace import FunctionTraceRecorder


_SESSIONS: dict[str, TestEvidence] = {}
_ACTIVE_RECORDERS: dict[str, FunctionTraceRecorder] = {}
_OUTPUT_PATH: Path | None = None
_SOURCE_ROOT: Path | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("sbfl")
    group.addoption(
        "--sbfl-source-root",
        action="store",
        default=os.environ.get("SBFL_SOURCE_ROOT", "src"),
        help="Only trace Python files under this application source root.",
    )
    group.addoption(
        "--sbfl-output",
        action="store",
        default=os.environ.get("SBFL_OUTPUT", ".sbfl/test_evidence.json"),
        help="Output JSON file for per-test coverage and runtime traces.",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _OUTPUT_PATH, _SOURCE_ROOT
    _SESSIONS.clear()
    _ACTIVE_RECORDERS.clear()
    _SOURCE_ROOT = Path(config.getoption("--sbfl-source-root")).resolve()
    _OUTPUT_PATH = Path(config.getoption("--sbfl-output")).resolve()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item):
    assert _SOURCE_ROOT is not None
    recorder = FunctionTraceRecorder(item.nodeid, _SOURCE_ROOT)
    _ACTIVE_RECORDERS[item.nodeid] = recorder

    previous_trace = sys.gettrace()
    sys.settrace(recorder.trace)
    try:
        yield
    finally:
        sys.settrace(previous_trace)
        evidence = recorder.finish()
        _SESSIONS[item.nodeid] = evidence
        _ACTIVE_RECORDERS.pop(item.nodeid, None)


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    # Use an explicit hook implementation so we can record setup failures too.
    if call.when == "call":
        evidence = _SESSIONS.setdefault(item.nodeid, TestEvidence(nodeid=item.nodeid))
        evidence.outcome = "failed" if call.excinfo is not None else "passed"
        if call.excinfo is not None:
            evidence.failure = {
                "type": call.excinfo.typename,
                "message": str(call.excinfo.value),
            }
    elif call.when == "setup" and call.excinfo is not None:
        evidence = _SESSIONS.setdefault(item.nodeid, TestEvidence(nodeid=item.nodeid))
        evidence.outcome = "setup_failed"
        evidence.failure = {
            "type": call.excinfo.typename,
            "message": str(call.excinfo.value),
        }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    assert _OUTPUT_PATH is not None
    assert _SOURCE_ROOT is not None
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source_root": str(_SOURCE_ROOT),
        "pytest_exitstatus": int(exitstatus),
        "tests": [
            evidence.to_dict()
            for evidence in sorted(_SESSIONS.values(), key=lambda item: item.nodeid)
        ],
    }
    _OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
