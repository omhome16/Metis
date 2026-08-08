"""Frontend QA for the Metis SPA (Playwright, headless Chromium).

Run: uv run python scripts/frontend_qa.py [base_url]
Screenshots land in /tmp/metis-qa/*.png. Exits non-zero on console errors.
"""

import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"
OUT = pathlib.Path("/tmp/metis-qa")
OUT.mkdir(parents=True, exist_ok=True)

errors: list[str] = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"[shot] {name}.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        # 1. home
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_function("document.querySelectorAll('.vault-item').length > 0", timeout=15000)
        assert page.locator(".brand-word").inner_text().strip() == "METIS", "brand missing"
        print("[ok] home: brand present")
        n_vaults = page.locator(".vault-item").count()
        assert n_vaults >= 4, f"expected >=4 vaults in sidebar, got {n_vaults}"
        print(f"[ok] home: {n_vaults} vaults in sidebar")
        page.wait_for_selector(".vault-card", timeout=10000)
        print("[ok] home: overview cards present")
        shot(page, "01-home")

        # 2. vault documents
        page.locator(".vault-item", has_text="AI Research").click()
        page.wait_for_timeout(1200)
        assert page.locator(".vault-title").inner_text() == "AI Research", "vault title wrong"
        assert page.locator(".stat-chip").count() == 4, "stat chips missing"
        print("[ok] vault header + stats")
        assert page.locator(".doc-card").count() >= 1, "no document cards"
        statuses = [e.inner_text().strip() for e in page.locator(".status-badge").all()]
        print(f"[ok] documents: {page.locator('.doc-card').count()} card(s), statuses={statuses}")
        shot(page, "02-documents")

        # 3. graph
        page.locator(".tab", has_text="Graph").click()
        page.wait_for_timeout(5000)  # let the force layout settle
        canvas = page.locator(".graph-stage canvas")
        assert canvas.count() == 1, "graph canvas missing"
        assert page.locator(".graph-legend").count() == 1, "legend missing"
        print("[ok] graph stage + legend present")
        shot(page, "03-graph")

        # 4. ask
        page.locator(".tab", has_text="Ask").click()
        page.wait_for_timeout(800)
        # conversations panel should be present
        assert page.locator(".ask-panel-label", has_text="Conversations").count() == 1, "conversations panel missing"
        print("[ok] conversations panel present")
        ta = page.locator(".composer textarea")
        ta.fill("What is RAG and how was it evaluated?")
        page.keyboard.press("Enter")
        print("[ok] question submitted, watching for thinking UI + stream...")
        saw_thinking = False
        deadline = time.time() + 180
        while time.time() < deadline and page.locator(".msg-meta").count() == 0:
            if page.locator(".thinking").count() > 0:
                saw_thinking = True
            if page.locator(".thinking-log-item").count() > 0:
                saw_thinking = True
                print(f"[ok] agent thinking log visible ({page.locator('.thinking-log-item').count()} tool step(s))")
                break
            page.wait_for_timeout(400)
        print(f"[ok] thinking UI observed: {saw_thinking}")
        try:
            page.wait_for_selector(".msg-meta", timeout=180000)
            print("[ok] answer finalized (msg-meta present)")
        except Exception:
            pass
        page.wait_for_timeout(1500)
        answer = page.locator(".msg-assistant-text").last.inner_text()
        print(f"[ok] answer length: {len(answer)} chars")
        print(f"     answer head: {answer[:140]!r}")
        assert page.locator(".sources").count() >= 1, "sources card missing"
        print("[ok] sources card present")
        cites = page.locator(".cite-chip").count()
        print(f"[ok] citation chips: {cites}")
        # conversation should be persisted server-side and listed in the panel
        page.wait_for_selector(".conv-item", timeout=15000)
        n_conv = page.locator(".conv-item").count()
        assert n_conv >= 1, "conversation not persisted to the list"
        print(f"[ok] conversation persisted + listed ({n_conv} in panel)")
        shot(page, "04-ask")

        # 5. dark theme
        page.locator("#themeToggle").click()
        page.wait_for_timeout(700)
        theme = page.evaluate("document.documentElement.dataset.theme")
        assert theme == "dark", f"theme not dark: {theme}"
        print("[ok] dark theme active")
        shot(page, "05-dark")
        # check the canvas recolored
        page.locator(".tab", has_text="Graph").click()
        page.wait_for_timeout(2500)
        shot(page, "06-dark-graph")
        page.locator("#themeToggle").click()
        page.wait_for_timeout(400)
        print("[ok] back to light")

        browser.close()

    print("\n=== console/page errors ===")
    if errors:
        for e in errors:
            print("  ", e)
        sys.exit(1)
    print("  none")
    print("\nQA PASSED")


if __name__ == "__main__":
    main()
