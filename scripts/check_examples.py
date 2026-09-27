"""Check the ClaimsAssist example tests and installed dependency versions."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from importlib import metadata
import io
import json
import os
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def installed_dependencies() -> dict[str, dict[str, object]]:
    """Compare installed packages with the platform-specific requirements."""
    from packaging.requirements import Requirement

    results: dict[str, dict[str, object]] = {}
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            installed = None
        results[requirement.name] = {
            "expected": str(requirement.specifier),
            "installed": installed,
            "match": installed is not None and installed in requirement.specifier,
        }
    if not results:
        raise ValueError("requirements.txt contains no applicable dependencies")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/example-checks.json")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".json":
        parser.error("--output must name a JSON file")
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    from tests.network_support import portable_socketpairs
    report: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "python_matches": sys.version_info[:2] == (3, 12),
        "overall": "FAIL",
        "tests_run": 0,
        "failures": 0,
        "errors": 0,
        "skips": 0,
        "dependencies": {},
    }
    captured = io.StringIO()
    try:
        dependencies = installed_dependencies()
        report["dependencies"] = dependencies
        with TemporaryDirectory(prefix="claimsassist-checks-") as temporary, ExitStack() as stack:
            # Keep the test processes away from the reader's normal AWS profile files.
            environment = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
            environment.update({
                "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src"))),
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_SHARED_CREDENTIALS_FILE": str(Path(temporary) / "credentials"),
                "AWS_CONFIG_FILE": str(Path(temporary) / "config"),
            })
            Path(environment["AWS_SHARED_CREDENTIALS_FILE"]).touch()
            Path(environment["AWS_CONFIG_FILE"]).touch()
            stack.enter_context(patch.dict(os.environ, environment, clear=True))
            # Preserve asyncio private socket pairs without allowing arbitrary connections.
            stack.enter_context(portable_socketpairs())
            # These guards protect this process. They are not an OS network sandbox.
            for name in ("connect", "connect_ex"):
                stack.enter_context(patch.object(socket.socket, name,
                    side_effect=AssertionError("Network access is disabled during example checks")))
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
            result = unittest.TextTestRunner(stream=captured, verbosity=1).run(suite)
        report.update({
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skips": len(result.skipped),
        })
        passed = (report["python_matches"] and result.wasSuccessful() and result.testsRun > 0
                  and not result.skipped and all(d["match"] for d in dependencies.values()))
        report["overall"] = "PASS" if passed else "FAIL"
    except Exception as exc:
        report["errors"] = int(report["errors"]) + 1
        report["check_error"] = f"{type(exc).__name__}: {exc}"
    report["test_output"] = captured.getvalue()
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Could not write the check report: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: report[k] for k in ("overall", "tests_run", "failures", "errors", "skips")}))
    if report["overall"] != "PASS":
        print(f"Inspect {args.output} for the failed checks.", file=sys.stderr)
    return int(report["overall"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
