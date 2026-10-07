from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
out = root / "artifacts/screenshots"
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8481/assistant", wait_until="networkidle")
    page.locator("#project option").first.wait_for(state="attached")
    page.screenshot(path=str(out / "assistant-desktop.png"), full_page=True)
    import json

    task = json.loads((root / "artifacts/validation/production-final-task.json").read_text(encoding="utf-8"))
    page.evaluate("id => show(id)", task["id"])
    page.locator("#result .scene").first.wait_for()
    page.screenshot(path=str(out / "assistant-result.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(out / "assistant-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Mobile overflow"
    assert not errors, errors
    browser.close()
print("Assistant desktop/mobile/result: no JavaScript errors or horizontal overflow")
