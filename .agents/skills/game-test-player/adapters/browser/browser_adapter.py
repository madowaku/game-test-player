"""Ordinary browser input and rendered screenshots; no game-state injection."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.session import utc_now


class BrowserAdapter:
    def __init__(self, url: str, evidence_dir: Path, viewport: dict, timeout_ms: float):
        self.url = url
        self.evidence_dir = evidence_dir
        self.viewport = viewport
        self.timeout_ms = timeout_ms
        self.runtime = self.browser = self.context = self.page = None
        self.held_keys: set[str] = set()
        self.errors: list[dict] = []
        self.step = 0

    def _error(self, kind: str, message: str) -> None:
        if len(self.errors) < 200:
            self.errors.append({"step": self.step, "observed_at": utc_now(),
                                "kind": kind, "message": message[:4000]})

    def launch_game(self) -> dict:
        self.runtime = sync_playwright().start()
        self.browser = self.runtime.chromium.launch(headless=True, timeout=self.timeout_ms)
        self.context = self.browser.new_context(
            viewport=self.viewport, device_scale_factor=1, locale="en-US",
            reduced_motion="reduce", service_workers="block")
        self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        self.page.on("pageerror", lambda error: self._error("pageerror", str(error)))
        self.page.on("console", lambda message: self._error("console", message.text)
                     if message.type == "error" else None)
        self.page.on("crash", lambda _: self._error("crash", "Browser page crashed"))
        self.page.on("dialog", lambda dialog: (self._error("dialog", dialog.message), dialog.dismiss()))
        response = self.page.goto(self.url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"Game navigation returned HTTP {response.status}")
        return {"url": self.url, "browser": self.browser.version, "headless": True,
                "viewport": self.viewport, "device_scale_factor": 1}

    def press_key(self, key: str, hold_s: float = 0.08) -> None:
        # Use a real down/up pair, with release even when a wait fails.
        self.key_down(key)
        try:
            self.wait(hold_s)
        finally:
            self.key_up(key)

    def key_down(self, key: str) -> None:
        self.page.keyboard.down(key)
        self.held_keys.add(key)

    def key_up(self, key: str) -> None:
        self.page.keyboard.up(key)
        self.held_keys.discard(key)

    def mouse_click(self, x: float, y: float, button: str = "left") -> None:
        self.page.mouse.click(x, y, button=button)

    def wait(self, seconds: float) -> None:
        # Playwright pumps browser events while waiting (unlike time.sleep).
        self.page.wait_for_timeout(seconds * 1000)

    def capture_screenshot(self, path: Path) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False, timeout=self.timeout_ms)
        return {"path": str(path), **self.viewport, "captured_at": utc_now()}

    def terminate_game(self) -> list[str]:
        """Attempt every cleanup operation; return artifact/cleanup errors."""
        failures = []
        operations = []
        if self.page is not None:
            operations.extend((f"release {key}", lambda key=key: self.key_up(key))
                              for key in list(self.held_keys))
        if self.context is not None:
            operations.append(("trace", lambda: self.context.tracing.stop(
                path=str(self.evidence_dir / "trace.zip"))))
            operations.append(("context", self.context.close))
        if self.browser is not None:
            operations.append(("browser", self.browser.close))
        if self.runtime is not None:
            operations.append(("playwright", self.runtime.stop))
        for name, operation in operations:
            try:
                operation()
            except Exception as exc:
                failures.append(f"{name}: {exc}")
        return failures
