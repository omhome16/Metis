"""Frontend QA for the Metis SPA (Playwright, headless Chromium).

Run: uv run python scripts/frontend_qa.py [base_url]
Exits non-zero on console errors or broken main flows.

Auth-aware: registers (or logs into) a dedicated QA account via the API and
injects the JWT before the app boots, so the run exercises the product views
rather than the login screen. Works in every METIS_AUTH_MODE.
"""

import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
OUT = pathlib.Path("/tmp/metis-qa")
OUT.mkdir(parents=True, exist_ok=True)

QA_EMAIL = "qa@metis.local"
QA_PASSWORD = "qa-password-long-enough"

errors: list[str] = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"[shot] {name}.png")


def api_call(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def qa_token() -> str | None:
    """A JWT for the QA account — register it, or log in if it already exists."""
    try:
        return api_call("/api/v1/auth/register", {"email": QA_EMAIL, "password": QA_PASSWORD})[
            "token"
        ]
    except urllib.error.HTTPError as e:
        if e.code == 409:
            try:
                return api_call("/api/v1/auth/login", {"email": QA_EMAIL, "password": QA_PASSWORD})[
                    "token"
                ]
            except urllib.error.HTTPError:
                return None
        if e.code == 400:  # accounts disabled (token/none mode) — nothing to do
            return None
        raise


def main():
    token = qa_token()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on(
            "console",
            lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
        )
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        # Inject the session before any app script runs (users mode).
        if token:
            page.add_init_script(f"localStorage.setItem('metis.jwt', {json.dumps(token)});")

        # 1. home / overview
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_selector("#app", timeout=15000)
        assert page.locator(".brand-word").inner_text().strip() == "METIS", "brand missing"
        print("[ok] app shell: brand present")

        if token:
            page.wait_for_selector(".home-hero h1", timeout=15000)
            print("[ok] signed in — overview rendered")
        else:
            # token/none mode: the overview shows directly
            page.wait_for_selector(".home-hero, .auth-card", timeout=15000)
            print("[ok] overview (no-accounts mode)")
        shot(page, "01-home")

        # 2. create a vault if none exists, then open the first one
        if page.locator(".vault-card").count() == 0:
            page.locator(".section-head button", has_text="New vault").click()
            page.fill(".modal input.input", "QA Vault")
            page.locator(".modal-foot .btn-primary").click()
            page.wait_for_selector(".vault-card", timeout=10000)
            print("[ok] vault created through the UI")
        page.locator(".vault-card").first.click()
        page.wait_for_selector(".docs-wrap", timeout=10000)
        assert page.locator(".dropzone").count() == 1, "dropzone missing"
        print("[ok] vault documents: dropzone present")
        shot(page, "02-documents")

        # 3. chat view (same vault, ask tab)
        page.evaluate(
            "location.hash = location.hash.replace(/(documents|graph|ask|contradictions)$/, 'ask')"
        )
        page.wait_for_selector(".composer-box textarea", timeout=10000)
        print("[ok] chat: composer present")
        assert page.locator(".ask-head button", has_text="New conversation").count() == 1
        shot(page, "03-ask")

        # 4. graph view (canvas paints; may be empty without data)
        page.evaluate(
            "location.hash = location.hash.replace(/(documents|graph|ask|contradictions)$/, 'graph')"
        )
        page.wait_for_selector(".graph-canvas-holder canvas", timeout=10000)
        time.sleep(1.5)  # first paint of the force layout
        print("[ok] graph: canvas mounted")
        shot(page, "04-graph")

        # 5. contradictions tab
        page.evaluate(
            "location.hash = location.hash.replace(/(documents|graph|ask|contradictions)$/, 'contradictions')"
        )
        page.wait_for_selector(".report-head", timeout=10000)
        print("[ok] contradictions: report head present")
        shot(page, "05-contradictions")

        # 6. settings
        page.evaluate("location.hash = '#/settings'")
        page.wait_for_selector(".settings-view, .settings-wrap", timeout=10000)
        print("[ok] settings render")
        shot(page, "06-settings")

        browser.close()

    if errors:
        print(f"\nFAILED — {len(errors)} console/page error(s):")
        for e in errors[:20]:
            print(f"  - {e}")
        raise SystemExit(1)
    print("\nAll frontend QA checks passed.")


if __name__ == "__main__":
    main()
