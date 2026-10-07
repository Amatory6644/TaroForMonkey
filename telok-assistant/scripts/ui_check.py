from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
out = root / "artifacts" / "screenshots"
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe", headless=True
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8481", wait_until="networkidle")
    page.get_by_role("heading", name="Редакция Telok").wait_for()
    page.screenshot(path=str(out / "overview-desktop.png"), full_page=True)
    page.locator("[data-tab=studio]").click()
    page.locator("[data-action=video]").click()
    page.get_by_role("heading", name="Сцена 1", exact=True).wait_for()
    page.screenshot(path=str(out / "video-studio.png"), full_page=True)
    page.locator("#close-modal").click()
    page.get_by_role("button", name="Открыть ↗").first.click()
    page.locator("#modal").wait_for(state="visible")
    page.screenshot(path=str(out / "material-review.png"), full_page=True)
    page.locator("#close-modal").click()
    page.locator("[data-tab=brand]").click()
    page.locator("#f-description").wait_for()
    page.screenshot(path=str(out / "brand.png"), full_page=True)
    page.locator("[data-tab=plan]").click()
    page.locator(".calendar").wait_for()
    page.screenshot(path=str(out / "plan.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[data-tab=overview]").click()
    page.screenshot(path=str(out / "overview-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), (
        "Mobile horizontal overflow"
    )
    assert not errors, errors
    browser.close()
print("UI: desktop/mobile, studio preview, brand, calendar passed; zero JavaScript page errors.")
