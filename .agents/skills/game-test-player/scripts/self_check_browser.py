"""Real headless Chromium acceptance and fault-injection checks (no mocks)."""
from __future__ import annotations

import argparse
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from scripts.run_browser_journey import run, validate_journey, write_json  # noqa: E402

FIXTURE = SKILL_ROOT / "fixtures" / "browser-journey"
CASES = {"none": None, "start-ignored": "start", "input-ignored": "move-right",
         "failure-ignored": "hit-hazard", "retry-broken": "retry-failure",
         "success-ignored": "reach-goal", "success-retry-broken": "retry-success",
         "stale-render": "move-right", "runtime-error": "move-right"}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check_validation(plan: dict) -> None:
    variants = []
    empty = deepcopy(plan)
    empty["steps"] = []
    variants.append(empty)
    unchecked = deepcopy(plan)
    unchecked["steps"][1]["expect"] = {}
    variants.append(unchecked)
    injection = deepcopy(plan)
    injection["steps"][1]["action"] = {"action": "evaluate", "script": "grant_win()"}
    variants.append(injection)
    nonfinite = deepcopy(plan)
    nonfinite["steps"][2]["action"]["hold_s"] = float("nan")
    variants.append(nonfinite)
    typo = deepcopy(plan)
    typo["steps"][2]["expect"]["pixel"] = []
    variants.append(typo)
    for invalid in variants:
        try:
            validate_journey(invalid)
        except ValueError:
            continue
        raise AssertionError("Unsafe or vacuous journey was accepted")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", choices=tuple(CASES))
    args = parser.parse_args()
    plan = json.loads((FIXTURE / "journey.json").read_text(encoding="utf-8"))
    validate_journey(plan)
    check_validation(plan)
    out = args.out.resolve()
    check(not out.exists() or not any(out.iterdir()), "Choose a new empty --out")
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(FIXTURE)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/index.html"
    results = []
    try:
        cases = [args.case] if args.case else list(CASES)
        for case in cases:
            directory = out / case
            completed = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "run_browser_journey.py"),
                 "--url", f"{url}?fault={case}", "--journey", str(FIXTURE / "journey.json"),
                 "--out", str(directory), "--timeout", "2", "--max-seconds", "45"],
                capture_output=True, text=True, encoding="utf-8", timeout=65)
            code = completed.returncode
            check((directory / "browser-run.json").exists(),
                  f"Runner did not produce evidence: {completed.stderr}")
            session = json.loads((directory / "session.json").read_text(encoding="utf-8"))
            manifest = json.loads((directory / "browser-run.json").read_text(encoding="utf-8"))
            expected = 0 if case == "none" else 1
            check(code == expected, f"{case}: expected exit {expected}, got {code}: {manifest['reason']}")
            check(json.loads(completed.stdout)["exit_code"] == code, "CLI exit metadata mismatch")
            check(session["mode"] == "diagnostic" and session["persona"] != "first_time",
                  "Regression must never be labeled first-time")
            check(session["action_count"] > 0, "No gameplay input recorded")
            check(session["observation_count"] == session["action_count"] + 1, "Evidence step mismatch")
            check(not manifest["cleanup_errors"], "Cleanup/trace failure")
            check((directory / "trace.zip").stat().st_size > 0, "Missing trace")
            for observation in session["observations"]:
                check(Path(observation["screenshot"]["path"]).read_bytes().startswith(b'\x89PNG'), "Missing PNG")
                check((directory / observation["browser_checks"]).exists(), "Missing checks")
            if case == "none":
                check(session["action_count"] == 16 and session["observation_count"] == 17,
                      "Full journey not exercised")
                check(not session["findings"], "Healthy fixture produced false positive")
            else:
                check(session["reached_point"] == CASES[case], f"Wrong detection point for {case}")
                check(bool(session["findings"][0]["reproduction"]), "Missing reproduction")
                last = json.loads((directory / manifest["steps"][-1]["evidence"]).read_text(encoding="utf-8"))
                if case == "stale-render":
                    check(all(c["passed"] for c in last["checks"] if c["kind"] == "text"),
                          "Stale-render test must demonstrate DOM-only false negative")
                    check(any(not c["passed"] for c in last["checks"] if c["kind"] == "pixels"),
                          "Missing actual pixel mismatch")
                if case == "runtime-error":
                    check(any(e["kind"] == "pageerror" for e in last["runtime_errors"]),
                          "Runtime failure was not captured")
            results.append({"case": case, "exit_code": code, "detected_at": session["reached_point"],
                            "actions": session["action_count"], "observations": session["observation_count"]})
            print(f"PASS: {case} ({session['reached_point']})", flush=True)

        if not args.case:
            # An unavailable target must abort explicitly, not become a game failure or pass.
            code = run(plan, url + "/missing", out / "missing-target", timeout_s=2)
            check(code == 2, "HTTP navigation failure must be infrastructure exit 2")
            failed = json.loads((out / "missing-target" / "session.json").read_text(encoding="utf-8"))
            check(failed["outcome"] == "aborted", "Unavailable target incorrectly classified")
            # Runtime budget is also an incomplete run, never successful coverage.
            budget_plan = deepcopy(plan)
            budget_plan["steps"][1]["action"] = {"action": "wait", "seconds": 5}
            code = run(budget_plan, url, out / "budget", timeout_s=2, max_seconds=1)
            check(code == 2, "Time budget incorrectly passed")
            try:
                run(plan, url, out / "none")
            except ValueError:
                pass
            else:
                raise AssertionError("Existing evidence was overwritten")
            print("PASS: missing target, time budget, evidence preservation, manifest validation", flush=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        write_json(out / "acceptance.json", {"cases": results, "expected_cases": len(cases),
                                            "scope": "Injected fixture defects, not real-game discoveries"})
    print(f"Evidence: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
