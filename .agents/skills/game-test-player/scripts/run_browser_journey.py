"""Replay an explicit browser regression journey, saving evidence even on failure.

This is diagnostic regression coverage, never a first-time or human UX playtest.
Exit 0: all declared checks passed; 1: detected mismatch/runtime error;
2: invalid configuration, unavailable browser, or incomplete evidence.
"""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import sys
import time
from urllib.parse import urlparse

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from scripts.session import SessionRecorder, utc_now  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def number(value, low: float, high: float) -> bool:
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def fields(obj, allowed: set, required: set) -> None:
    require(isinstance(obj, dict), "Expected an object")
    require(not (set(obj) - allowed), f"Unknown fields: {set(obj) - allowed}")
    require(required <= set(obj), f"Missing fields: {required - set(obj)}")


def validate_journey(plan: dict) -> None:
    fields(plan, {"name", "viewport", "steps"}, {"name", "viewport", "steps"})
    require(isinstance(plan["name"], str) and bool(plan["name"].strip()), "name required")
    fields(plan["viewport"], {"width", "height"}, {"width", "height"})
    require(all(type(n) is int and 100 <= n <= 2048 for n in plan["viewport"].values()),
            "viewport dimensions must be integers in 100..2048")
    steps = plan["steps"]
    require(isinstance(steps, list) and 2 <= len(steps) <= 200,
            "A journey needs an initial observation and 1..199 actions")
    names = set()
    for index, step in enumerate(steps):
        fields(step, {"name", "phase", "action", "expect"}, {"name", "phase", "expect"})
        require(isinstance(step["name"], str) and step["name"] and step["name"] not in names,
                "step names must be nonempty and unique")
        names.add(step["name"])
        require(isinstance(step["phase"], str) and bool(step["phase"]), "phase required")
        require(("action" in step) == (index > 0), "Only step 0 may omit action")
        if index:
            action = step["action"]
            require(isinstance(action, dict), "action must be an object")
            operation = action.get("action")
            options = {"press_key": ({"action", "key", "hold_s"}, {"action", "key"}),
                       "key_down": ({"action", "key"}, {"action", "key"}),
                       "key_up": ({"action", "key"}, {"action", "key"}),
                       "mouse_click": ({"action", "x", "y", "button"}, {"action", "x", "y"}),
                       "wait": ({"action", "seconds"}, {"action", "seconds"})}
            require(isinstance(operation, str) and operation in options, "Unsupported action")
            fields(action, *options[operation])
            if "key" in action:
                require(isinstance(action["key"], str) and bool(action["key"]), "key required")
            if operation == "press_key":
                require(number(action.get("hold_s", 0.08), 0, 5), "hold_s must be in 0..5")
            if operation == "wait":
                require(number(action["seconds"], 0, 5), "seconds must be in 0..5")
            if operation == "mouse_click":
                require(number(action["x"], 0, plan["viewport"]["width"] - 1)
                        and number(action["y"], 0, plan["viewport"]["height"] - 1),
                        "Click must be inside viewport")
                require(action.get("button", "left") in ("left", "right", "middle"), "Invalid button")
        checks = step["expect"]
        fields(checks, {"text", "pixels"}, set())
        require(bool(checks.get("text") or checks.get("pixels")), "Every step needs checks")
        for check in checks.get("text", []):
            fields(check, {"selector", "equals"}, {"selector", "equals"})
            require(isinstance(check["selector"], str) and bool(check["selector"]), "selector required")
            require(isinstance(check["equals"], str) and bool(check["equals"]), "expected text required")
        for check in checks.get("pixels", []):
            keys = {"selector", "x", "y", "width", "height", "rgb", "tolerance", "min_ratio"}
            fields(check, keys, keys - {"tolerance", "min_ratio"})
            require(isinstance(check["selector"], str) and bool(check["selector"]), "selector required")
            require(all(type(check[k]) is int and 0 <= check[k] <= 2048 for k in ("x", "y")), "Invalid pixel origin")
            require(all(type(check[k]) is int and 1 <= check[k] <= 2048 for k in ("width", "height")), "Invalid pixel size")
            require(isinstance(check["rgb"], list) and len(check["rgb"]) == 3
                    and all(type(n) is int and 0 <= n <= 255 for n in check["rgb"]), "Invalid RGB")
            require(number(check.get("tolerance", 8), 0, 64), "Invalid color tolerance")
            require(number(check.get("min_ratio", 0.95), 0.01, 1), "Invalid color ratio")


def inspect_screen(page, expected: dict, screenshot: Path, timeout_ms: float) -> list[dict]:
    """Read player-facing text and PNG pixels only; no evaluate/hidden state."""
    from PIL import Image

    results = []
    for check in expected.get("text", []):
        locator = page.locator(check["selector"])
        actual = None
        if locator.count() == 1 and locator.is_visible():
            actual = locator.inner_text(timeout=timeout_ms).strip()
        results.append({"kind": "text", "expected": check, "actual": actual,
                        "passed": actual == check["equals"]})
    with Image.open(screenshot) as image:
        image = image.convert("RGB")
        for check in expected.get("pixels", []):
            locator = page.locator(check["selector"])
            box = (locator.bounding_box(timeout=timeout_ms)
                   if locator.count() == 1 and locator.is_visible() else None)
            ratio = 0.0
            if box:
                left, top = round(box["x"] + check["x"]), round(box["y"] + check["y"])
                right, bottom = left + check["width"], top + check["height"]
                if (0 <= left < right <= image.width and 0 <= top < bottom <= image.height
                        and check["x"] + check["width"] <= box["width"]
                        and check["y"] + check["height"] <= box["height"]):
                    pixels = image.crop((left, top, right, bottom)).getdata()
                    count = sum(all(abs(a - b) <= check.get("tolerance", 8)
                                    for a, b in zip(pixel, check["rgb"])) for pixel in pixels)
                    ratio = count / (check["width"] * check["height"])
            results.append({"kind": "pixels", "expected": check, "actual_ratio": ratio,
                            "passed": ratio >= check.get("min_ratio", 0.95)})
    return results


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(plan: dict, url: str, out: Path, *, timeout_s: float = 3, max_seconds: float = 90) -> int:
    validate_journey(plan)
    require(number(timeout_s, 0.1, 60), "timeout must be in 0.1..60 seconds")
    require(number(max_seconds, 0.1, 3600), "max_seconds must be in 0.1..3600")
    require(urlparse(url).scheme in ("http", "https") and bool(urlparse(url).hostname),
            "Use an http(s) game URL")
    out = out.resolve()
    require(not out.exists() or not any(out.iterdir()), "Evidence directory must be empty; choose a new --out")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "journey.json", plan)
    recorder = SessionRecorder(persona="regression", scenario=plan["name"],
                               evidence_dir=out, adapter="browser", black_box=False)
    recorder.data["mode"] = "diagnostic"
    recorder.data["scope"] = "Scripted browser regression; not first-time, hardware, or human fun validation"
    manifest = {"schema_version": "browser-journey-1", "url": url,
                "journey_sha256": hashlib.sha256((out / "journey.json").read_bytes()).hexdigest(),
                "python": platform.python_version(), "platform": platform.platform(),
                "timeout_s": timeout_s, "max_seconds": max_seconds, "started_at": utc_now(),
                "steps": [], "cleanup_errors": []}
    adapter = None
    code, reason, reached = 2, "Runner did not complete", "launch"
    replay = [f"Launch {url}"]
    recorder.set_reproduction_steps(replay)
    deadline = time.monotonic() + max_seconds
    try:
        from adapters.browser.browser_adapter import BrowserAdapter

        manifest["dependencies"] = {name: version(name) for name in ("playwright", "pillow")}
        adapter = BrowserAdapter(url, out, plan["viewport"], min(timeout_s, max_seconds) * 1000)
        manifest["launch"] = adapter.launch_game()
        for index, step in enumerate(plan["steps"]):
            reached = step["name"]
            adapter.step = index
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Journey time budget exhausted")
            adapter.timeout_ms = min(timeout_s, remaining) * 1000
            adapter.page.set_default_timeout(adapter.timeout_ms)
            action_error = ""
            if index:
                action = dict(step["action"])
                operation = action.pop("action")
                replay.append(json.dumps(step["action"], sort_keys=True))
                recorder.set_reproduction_steps(replay)
                start = time.monotonic()
                try:
                    duration = action.get("seconds", action.get("hold_s", 0.08))
                    if duration >= remaining:
                        raise TimeoutError("Action exceeds remaining time budget")
                    getattr(adapter, operation)(**action)
                except Exception as exc:
                    action_error = str(exc)
                recorder.record_action(operation, action, duration_s=time.monotonic() - start,
                                       result="error" if action_error else "ok", error=action_error)
            screenshot_path = out / "screenshots" / f"step-{index:03d}.png"
            step_deadline = min(deadline, time.monotonic() + timeout_s)
            checks, screenshot, capture_error = [], None, ""
            while True:
                # Keep the last complete observation when the polling budget ends.
                # A near-zero screenshot timeout would turn a real mismatch into
                # a spurious infrastructure failure on the final poll.
                if checks and time.monotonic() >= step_deadline:
                    break
                try:
                    adapter.timeout_ms = min(timeout_s, max_seconds) * 1000
                    screenshot = adapter.capture_screenshot(screenshot_path)
                    checks = inspect_screen(adapter.page, step["expect"], screenshot_path, adapter.timeout_ms)
                except Exception as exc:
                    capture_error = str(exc)
                    break
                if (action_error or adapter.errors or all(c["passed"] for c in checks)
                        or time.monotonic() >= step_deadline):
                    break
                adapter.wait(min(0.05, max(0, step_deadline - time.monotonic())))
            passed = bool(checks) and all(c["passed"] for c in checks)
            passed = passed and not (action_error or capture_error or adapter.errors)
            detail = {"step": index, "name": reached, "phase": step["phase"], "passed": passed,
                      "checks": checks, "action_error": action_error, "capture_error": capture_error,
                      "runtime_errors": list(adapter.errors), "observed_at": utc_now()}
            write_json(out / f"step-{index:03d}.json", detail)
            observation = recorder.record_observation(screenshot, step=index,
                state_summary=f"{reached}: {'matched' if passed else 'NOT verified'}")
            observation["browser_checks"] = f"step-{index:03d}.json"
            manifest["steps"].append({"step": index, "name": reached, "phase": step["phase"],
                                      "passed": passed, "evidence": f"step-{index:03d}.json"})
            recorder.save_json()
            if not passed:
                code = 2 if capture_error or action_error else 1
                failed = [c for c in checks if not c["passed"]]
                reason = (f"Checkpoint {reached} failed: " + json.dumps({"mismatches": failed,
                          "action_error": action_error, "capture_error": capture_error,
                          "runtime_errors": adapter.errors}, ensure_ascii=False))
                recorder.add_finding(title=f"Journey stopped at {reached}", description=reason,
                    severity="HIGH", category="browser-regression" if code == 1 else "infrastructure",
                    reproduction=replay, impact="Declared journey could not be verified; inspect evidence.",
                    evidence=[screenshot_path] if screenshot else [], step=index)
                break
        else:
            require(recorder.action_count > 0 and recorder.observation_count == len(plan["steps"]),
                    "Incomplete journey cannot pass")
            code, reason = 0, "All declared rendered-state checkpoints passed"
    except Exception as exc:
        code, reason = 2, f"{type(exc).__name__}: {exc}"
        recorder.add_finding(title="Browser journey unavailable", description=reason,
            severity="HIGH", category="infrastructure", reproduction=replay,
            impact="Coverage incomplete; this is not a gameplay pass.")
    finally:
        if adapter is not None:
            manifest["cleanup_errors"] = adapter.terminate_game()
            write_json(out / "browser-errors.json", adapter.errors)
            # Errors emitted after the final checkpoint still invalidate a pass.
            if code == 0 and adapter.errors:
                code, reason = 1, f"Late browser runtime error: {adapter.errors}"
                recorder.add_finding(title="Late browser runtime error", description=reason,
                    severity="HIGH", category="browser-regression", reproduction=replay)
            if code == 0 and manifest["cleanup_errors"]:
                code, reason = 2, f"Incomplete evidence/cleanup: {manifest['cleanup_errors']}"
        manifest.update(exit_code=code, reason=reason, ended_at=utc_now())
        write_json(out / "browser-run.json", manifest)
        recorder.finish({0: "success", 1: "failure", 2: "aborted"}[code],
                        reached_point=reached, reason=reason)
        recorder.save_json()
        report = recorder.save_markdown()
        report.write_text(
            "> Diagnostic regression only. Not first-time, hardware, or human fun validation.\n\n"
            + report.read_text(encoding="utf-8"), encoding="utf-8")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--journey", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=3)
    parser.add_argument("--max-seconds", type=float, default=90)
    args = parser.parse_args()
    try:
        plan = json.loads(args.journey.read_text(encoding="utf-8"))
        code = run(plan, args.url, args.out, timeout_s=args.timeout, max_seconds=args.max_seconds)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Invalid journey/configuration: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"exit_code": code, "evidence": str(args.out.resolve())}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
