"""Session recording and deterministic Markdown/JSON report generation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Iterable, Mapping


SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "OBSERVATION")
OUTCOMES = ("success", "failure", "timeout", "aborted")
INSIGHT_BUCKETS = (
    "confusion_points",
    "misread_ui",
    "misunderstood_rules",
    "stuck_candidates",
    "bug_candidates",
    "fun_moments",
    "interest_drops",
    "critical_ux_issues",
)
_SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _json_safe(value: Any) -> Any:
    """Convert common path/collection values before writing JSON."""

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _items(value: Iterable[Any] | str | Path | None) -> list[Any]:
    """Treat a single path/text value as one item instead of iterating characters."""

    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    return list(value)


def _confidence(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be a number between 0 and 1") from exc
    if result < 0 or result > 1:
        raise ValueError("confidence must be a number between 0 and 1")
    return round(result, 3)


def _normalise_severity(value: str | None) -> str:
    severity = (value or "OBSERVATION").strip().upper()
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")
    return severity


def _normalise_outcome(value: str) -> str:
    outcome = _text(value).lower()
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}")
    return outcome


class SessionRecorder:
    """Collect the minimum useful player-perspective evidence for one run."""

    def __init__(
        self,
        *,
        persona: str,
        scenario: str,
        evidence_dir: str | Path,
        adapter: str = "godot",
        black_box: bool = True,
    ) -> None:
        if not _text(persona):
            raise ValueError("persona is required")
        if not _text(scenario):
            raise ValueError("scenario is required")
        self.evidence_dir = Path(evidence_dir).expanduser().resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._started_monotonic = time.monotonic()
        self._finished = False
        self.data: dict[str, Any] = {
            "schema_version": "0.1",
            "skill": "game-test-player",
            "adapter": _text(adapter) or "godot",
            "persona": _text(persona),
            "scenario": _text(scenario),
            "mode": _text(persona),
            "black_box": bool(black_box),
            "started_at": utc_now(),
            "ended_at": None,
            "play_time_s": 0.0,
            "action_count": 0,
            "observation_count": 0,
            "reached_point": "",
            "outcome": None,
            "outcome_reason": "",
            "insights": {bucket: [] for bucket in INSIGHT_BUCKETS},
            "findings": [],
            "reproduction_steps": [],
            "evidence_screenshots": [],
            "observations": [],
            "actions": [],
            "memos": [],
        }

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def action_count(self) -> int:
        return int(self.data["action_count"])

    @property
    def observation_count(self) -> int:
        return int(self.data["observation_count"])

    def _elapsed(self) -> float:
        return round(max(0.0, time.monotonic() - self._started_monotonic), 3)

    def record_observation(
        self,
        screenshot: str | Path | Mapping[str, Any] | None,
        *,
        state_summary: str = "",
        step: int | None = None,
        player_facing_audio: str = "",
    ) -> dict[str, Any]:
        """Record one visible state; screenshot may be absent only on capture failure."""

        if isinstance(screenshot, Mapping):
            evidence = _json_safe(dict(screenshot))
            evidence_path = evidence.get("path")
        elif screenshot is None:
            evidence = None
            evidence_path = None
        else:
            evidence = {"path": str(Path(screenshot).expanduser().resolve())}
            evidence_path = evidence["path"]
        observation = {
            "step": self.observation_count if step is None else int(step),
            "observed_at": utc_now(),
            "elapsed_s": self._elapsed(),
            "screenshot": evidence,
            "state_summary": _text(state_summary),
            "player_facing_audio": _text(player_facing_audio),
        }
        self.data["observations"].append(observation)
        self.data["observation_count"] = len(self.data["observations"])
        if evidence_path:
            evidence_key = str(evidence_path)
            if not any(item.get("path") == evidence_key for item in self.data["evidence_screenshots"]):
                self.data["evidence_screenshots"].append(
                    {"path": evidence_key, "step": observation["step"]}
                )
        return observation

    def record_action(
        self,
        action: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        duration_s: float | None = None,
        result: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Record exactly one adapter operation selected by the player."""

        action_name = _text(action)
        if not action_name:
            raise ValueError("action is required")
        if duration_s is not None and duration_s < 0:
            raise ValueError("duration_s cannot be negative")
        record = {
            "step": self.action_count + 1,
            "selected_at": utc_now(),
            "elapsed_s": self._elapsed(),
            "action": action_name,
            "parameters": _json_safe(dict(parameters or {})),
            "duration_s": round(float(duration_s), 3) if duration_s is not None else None,
            "result": _text(result),
            "error": _text(error),
        }
        self.data["actions"].append(record)
        self.data["action_count"] = len(self.data["actions"])
        return record

    def record_memo(
        self,
        *,
        what_happening: str = "",
        next_goal: str = "",
        action: str = "",
        reason: str = "",
        confidence: float | None = None,
        hesitation: bool = False,
        expected_result: str = "",
        actual_result: str = "",
        misunderstanding: str = "",
        step: int | None = None,
    ) -> dict[str, Any]:
        """Record a compact player thought at an important decision point."""

        memo = {
            "step": self.action_count if step is None else int(step),
            "recorded_at": utc_now(),
            "what_happening": _text(what_happening),
            "next_goal": _text(next_goal),
            "action": _text(action),
            "reason": _text(reason),
            "confidence": _confidence(confidence),
            "hesitation": bool(hesitation),
            "expected_result": _text(expected_result),
            "actual_result": _text(actual_result),
            "misunderstanding": _text(misunderstanding),
        }
        self.data["memos"].append(memo)
        return memo

    def update_last_memo(
        self,
        *,
        actual_result: str | None = None,
        misunderstanding: str | None = None,
    ) -> dict[str, Any] | None:
        """Complete the previous action's result after its next screenshot."""

        if not self.data["memos"]:
            return None
        memo = self.data["memos"][-1]
        if actual_result is not None:
            memo["actual_result"] = _text(actual_result)
        if misunderstanding is not None:
            memo["misunderstanding"] = _text(misunderstanding)
        return memo

    def add_insight(
        self,
        bucket: str,
        text: str,
        *,
        step: int | None = None,
        evidence: Iterable[str | Path] = (),
        severity: str = "OBSERVATION",
    ) -> dict[str, Any]:
        """Add a concise player-perspective insight to one required report bucket."""

        if bucket not in INSIGHT_BUCKETS:
            raise ValueError(f"bucket must be one of {', '.join(INSIGHT_BUCKETS)}")
        item = {
            "text": _text(text),
            "step": int(step) if step is not None else None,
            "severity": _normalise_severity(severity),
            "evidence": [str(Path(path).expanduser().resolve()) for path in _items(evidence)],
        }
        if not item["text"]:
            raise ValueError("insight text is required")
        self.data["insights"][bucket].append(item)
        return item

    def add_finding(
        self,
        *,
        title: str,
        description: str,
        severity: str = "OBSERVATION",
        reproduction: Iterable[str] = (),
        impact: str = "",
        evidence: Iterable[str | Path] = (),
        category: str = "UX",
        step: int | None = None,
    ) -> dict[str, Any]:
        """Add an evidence-backed finding with a stable severity."""

        if not _text(title) or not _text(description):
            raise ValueError("finding title and description are required")
        finding = {
            "id": f"F-{len(self.data['findings']) + 1:03d}",
            "severity": _normalise_severity(severity),
            "category": _text(category) or "UX",
            "title": _text(title),
            "description": _text(description),
            "impact": _text(impact),
            "reproduction": [_text(step_text) for step_text in _items(reproduction) if _text(step_text)],
            "evidence": [str(Path(path).expanduser().resolve()) for path in _items(evidence)],
            "step": int(step) if step is not None else None,
        }
        self.data["findings"].append(finding)
        return finding

    def set_reached_point(self, reached_point: str) -> None:
        self.data["reached_point"] = _text(reached_point)

    def set_reproduction_steps(self, steps: Iterable[str]) -> None:
        self.data["reproduction_steps"] = [_text(step) for step in _items(steps) if _text(step)]

    def finish(
        self,
        outcome: str,
        *,
        reached_point: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Close the session and freeze its outcome metadata."""

        if self._finished:
            return self.data
        self.data["outcome"] = _normalise_outcome(outcome)
        if reached_point is not None:
            self.set_reached_point(reached_point)
        self.data["outcome_reason"] = _text(reason)
        self.data["ended_at"] = utc_now()
        self.data["play_time_s"] = self._elapsed()
        self._finished = True
        return self.data

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible report object."""

        result = _json_safe(deepcopy(self.data))
        result["summary"] = self.summary()
        return result

    def summary(self) -> dict[str, Any]:
        by_severity = {severity: 0 for severity in SEVERITIES}
        for finding in self.data["findings"]:
            by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
        return {
            "action_count": self.action_count,
            "observation_count": self.observation_count,
            "play_time_s": self.data["play_time_s"] if self._finished else self._elapsed(),
            "findings_by_severity": by_severity,
            "insights_recorded": {
                bucket: len(self.data["insights"][bucket]) for bucket in INSIGHT_BUCKETS
            },
        }

    def save_json(self, path: str | Path | None = None) -> Path:
        """Write the machine-readable report and return its absolute path."""

        target = Path(path) if path else self.evidence_dir / "session.json"
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target

    def save_markdown(self, path: str | Path | None = None) -> Path:
        """Write the human-readable report and return its absolute path."""

        target = Path(path) if path else self.evidence_dir / "report.md"
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(self.to_dict()), encoding="utf-8")
        return target


def _md(value: Any, fallback: str = "Not recorded") -> str:
    text = _text(value).replace("\r", " ").replace("\n", " ")
    return text or fallback


def _bullet_list(items: list[Mapping[str, Any]], *, empty: str = "None recorded.") -> list[str]:
    if not items:
        return [empty]
    lines: list[str] = []
    for item in items:
        severity = item.get("severity")
        prefix = f"[{severity}] " if severity else ""
        step = f" (step {item['step']})" if item.get("step") is not None else ""
        evidence = item.get("evidence") or []
        evidence_text = f" — evidence: {', '.join(f'`{path}`' for path in evidence)}" if evidence else ""
        lines.append(f"- {prefix}{_md(item.get('text'))}{step}{evidence_text}")
    return lines


def render_markdown(data: Mapping[str, Any]) -> str:
    """Render a report while keeping every required category visible."""

    insights = data.get("insights") or {}
    findings = sorted(
        data.get("findings") or [],
        key=lambda item: (_SEVERITY_ORDER.get(str(item.get("severity")), 99), str(item.get("id", ""))),
    )
    lines = [
        "# Game Test Player Report",
        "",
        f"- Persona: `{_md(data.get('persona'))}`",
        f"- Scenario: `{_md(data.get('scenario'))}`",
        f"- Adapter: `{_md(data.get('adapter'))}`",
        f"- Play time: `{data.get('play_time_s', 0)} s`",
        f"- Actions: `{data.get('action_count', 0)}`",
        f"- Observations: `{data.get('observation_count', 0)}`",
        f"- Reached point: {_md(data.get('reached_point'))}",
        f"- Outcome: `{_md(data.get('outcome'))}`",
        f"- Black-box policy: `{bool(data.get('black_box', False))}`",
        "- Severity: `BLOCKER > HIGH > MEDIUM > LOW > OBSERVATION`",
        f"- Outcome reason: {_md(data.get('outcome_reason'))}",
        "",
        "## Player perspective",
        "",
    ]
    bucket_titles = {
        "confusion_points": "Confusion points",
        "misread_ui": "Misread UI",
        "misunderstood_rules": "Misunderstood rules",
        "stuck_candidates": "Progress-blocking candidates",
        "bug_candidates": "Bug candidates",
        "fun_moments": "Fun moments",
        "interest_drops": "Interest drops",
        "critical_ux_issues": "Critical UX issues",
    }
    for bucket in INSIGHT_BUCKETS:
        lines.append(f"### {bucket_titles[bucket]}")
        lines.extend(_bullet_list(list(insights.get(bucket) or [])))
        lines.append("")

    lines.extend(["## Findings", ""])
    if not findings:
        lines.append("No findings recorded.")
    else:
        for finding in findings:
            severity = _md(finding.get("severity"), "OBSERVATION")
            lines.extend(
                [
                    f"### `{severity}` { _md(finding.get('id'), 'F-???') } — {_md(finding.get('title'))}",
                    "",
                    _md(finding.get("description")),
                    "",
                    f"- Category: `{_md(finding.get('category'))}`",
                    f"- Impact: {_md(finding.get('impact'))}",
                ]
            )
            reproduction = finding.get("reproduction") or []
            lines.append("- Reproduction:")
            if reproduction:
                lines.extend(f"  {index}. {_md(step)}" for index, step in enumerate(reproduction, 1))
            else:
                lines.append("  Not recorded.")
            evidence = finding.get("evidence") or []
            lines.append("- Evidence:")
            lines.extend(f"  - `{path}`" for path in evidence) if evidence else lines.append("  None recorded.")
            lines.append("")

    lines.extend(["## Reproduction steps", ""])
    reproduction_steps = data.get("reproduction_steps") or []
    if reproduction_steps:
        lines.extend(f"{index}. {_md(step)}" for index, step in enumerate(reproduction_steps, 1))
    else:
        lines.append("Not recorded.")

    lines.extend(["", "## Evidence screenshots", ""])
    evidence = data.get("evidence_screenshots") or []
    if evidence:
        for item in evidence:
            path = item.get("path") if isinstance(item, Mapping) else item
            step = item.get("step") if isinstance(item, Mapping) else None
            suffix = f" (step {step})" if step is not None else ""
            lines.append(f"- `{path}`{suffix}")
    else:
        lines.append("No screenshots recorded.")

    lines.extend(["", "## Decision memos", ""])
    memos = data.get("memos") or []
    if memos:
        lines.append("| Step | What seemed to happen | Next goal | Action | Confidence | Hesitated | Expected → actual | Misunderstanding |")
        lines.append("| ---: | --- | --- | --- | ---: | :---: | --- | --- |")
        for memo in memos:
            expected = _md(memo.get("expected_result"), "—")
            actual = _md(memo.get("actual_result"), "—")
            lines.append(
                "| {step} | {happening} | {goal} | {action} | {confidence} | {hesitation} | {expected} → {actual} | {misunderstanding} |".format(
                    step=memo.get("step", "—"),
                    happening=_md(memo.get("what_happening"), "—").replace("|", "\\|"),
                    goal=_md(memo.get("next_goal"), "—").replace("|", "\\|"),
                    action=_md(memo.get("action"), "—").replace("|", "\\|"),
                    confidence="—" if memo.get("confidence") is None else memo.get("confidence"),
                    hesitation="yes" if memo.get("hesitation") else "no",
                    expected=expected.replace("|", "\\|"),
                    actual=actual.replace("|", "\\|"),
                    misunderstanding=_md(memo.get("misunderstanding"), "—").replace("|", "\\|"),
                )
            )
    else:
        lines.append("No decision memos recorded.")

    lines.extend(["", "## Action timeline", ""])
    actions = data.get("actions") or []
    if actions:
        lines.append("| Step | Action | Parameters | Result | Error |")
        lines.append("| ---: | --- | --- | --- | --- |")
        for action in actions:
            parameters = json.dumps(action.get("parameters") or {}, ensure_ascii=False, sort_keys=True)
            lines.append(
                f"| {action.get('step', '—')} | `{_md(action.get('action'), '—')}` | `{parameters.replace('`', '\\`')}` | {_md(action.get('result'), '—')} | {_md(action.get('error'), '—')} |"
            )
    else:
        lines.append("No actions recorded.")

    lines.extend(
        [
            "",
            "## Machine-readable summary",
            "",
            "```json",
            json.dumps(data.get("summary") or {}, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def load_report(path: str | Path) -> dict[str, Any]:
    """Load a session JSON report for a renderer or post-processing tool."""

    source = Path(path).expanduser().resolve()
    return json.loads(source.read_text(encoding="utf-8"))


__all__ = [
    "INSIGHT_BUCKETS",
    "OUTCOMES",
    "SEVERITIES",
    "SessionRecorder",
    "load_report",
    "render_markdown",
    "utc_now",
]
