#!/usr/bin/env python3
"""Record the first-use video (documents/03_marketing/10_video_primo_uso.md).

Drives the app with Playwright on a demo database and records the window:
the import from the start page and the guided flow, the expense confirmation
with a receipt in hand, the Home. Each scene's start is written to
scenes.json, so the edit (make_video.py) knows where to cut and where the
subtitles go.

Usage: python record.py APP_URL STATEMENT.csv OUT_DIR [LANG]
The app must already run on APP_URL, on a demo database in LANG with the
onboarding done (prepare_demo_db.py).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

# The page at 1:1 in a 1920x1080 window: scaling it up made the text too
# big for the frame (founder, 2026-10-26).
VIEWPORT = {"width": 1920, "height": 1080}

# What the user says to the expense confirmation, per language.
SPOKEN = {
    "it": {"expense": "ieri 23 e 40", "yes": "sì", "account": "Conto corrente"},
    "en": {"expense": "yesterday 23.40", "yes": "yes", "account": "Current account"},
}
LOCALES = {"it": "it-IT", "en": "en-GB"}


def _labels(lang: str) -> dict[str, str]:
    """The app's own strings in *lang*, so the clicks follow the interface
    instead of a copy of it.

    Args:
        lang: the language code of the demo database ("it", "en").
    """
    i18n = Path(__file__).resolve().parents[2] / "ui" / "i18n" / f"{lang}.json"
    return json.loads(i18n.read_text(encoding="utf-8"))


def _click(page: Page, text: str) -> None:
    """Click the first button named *text*.

    Args:
        page: the recorded page.
        text: the button's label, exactly as shown.
    """
    page.get_by_role("button", name=text).first.click()


def _settle(page: Page, seconds: float = 1.5) -> None:
    """Streamlit reruns after each action: wait for it to stop running.

    Args:
        page: the recorded page.
        seconds: how long the settled screen stays still afterwards.
    """
    time.sleep(0.3)
    page.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=600_000)
    time.sleep(seconds)


def _say(page: Page, text: str, hold: float = 2.5) -> None:
    """Type an answer in the chat as a person would, and send it.

    Args:
        page: the recorded page.
        text: the answer.
        hold: seconds the reply stays on screen before the next action.
    """
    box = page.get_by_test_id("stChatInputTextArea")
    box.click()
    box.type(text, delay=110)
    if box.input_value() != text:       # a rerun of the page ate part of it
        box.fill(text)
    box.press("Enter")
    _settle(page, hold)


def main() -> None:
    url, statement, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    lang = sys.argv[4] if len(sys.argv) > 4 else "it"
    labels, spoken = _labels(lang), SPOKEN[lang]
    # The word alone: the emoji and the dash around it do not match as text.
    completed = re.sub(r"^\W+|\W+$", "", labels["upload.completed_tx"].split("{")[0])
    out.mkdir(parents=True, exist_ok=True)
    scenes: dict[str, float] = {}
    with sync_playwright() as pw:
        browser = pw.webkit.launch()      # the engine of the desktop window on macOS
        ctx = browser.new_context(viewport=VIEWPORT, record_video_dir=str(out), record_video_size=VIEWPORT,
                                  locale=LOCALES[lang], color_scheme="dark")
        page = ctx.new_page()
        t0 = time.monotonic()
        mark = lambda name: scenes.__setitem__(name, round(time.monotonic() - t0, 2))  # noqa: E731

        page.goto(url)
        _settle(page, 0.5)
        mark("intro")
        time.sleep(5.0)                   # the start page, still, under the intro line

        # 1. The import, from the start page of an empty registry: be guided,
        # name the account, on to the file.
        mark("import")
        _click(page, labels["start.guided.btn"])
        _settle(page)
        if page.get_by_role("button", name=labels["guided.welcome_ok"]).count():
            _click(page, labels["guided.welcome_ok"])
            _settle(page)
        account_box = page.get_by_label(labels["guided.checklist.add_account_label"])
        if account_box.count():
            account_box.first.type(spoken["account"], delay=90)
            _click(page, labels["guided.checklist.add_account_btn"])
            _settle(page)
        while not page.locator("input[type='file']").count():
            _click(page, labels["guided.next"])
            _settle(page)
        page.locator("input[type='file']").set_input_files(str(statement))
        _settle(page)
        _click(page, labels["upload.process_btn"])
        _settle(page, 1.0)
        # Inside the guided flow its own bar takes the place of "Continue".
        page.wait_for_selector(f"text={completed}", timeout=900_000)
        _settle(page, 3.0)
        mark("import_done")

        # 2. A receipt in hand, up to the tick.
        page.goto(url)
        _settle(page)
        mark("receipt")
        _click(page, labels["nav.expense_confirm"])
        _settle(page, 3.0)                # the page warms its voice up: let it finish first
        _say(page, spoken["expense"])
        _say(page, spoken["yes"], hold=4.0)
        mark("receipt_done")

        # 3. Where the money goes: the chart in full view, still for five
        # seconds (founder, 2026-10-26), then slowly down to the categories.
        mark("home")
        _click(page, labels["nav.home"])
        _settle(page, 1.5)
        page.mouse.move(VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.6)   # over the page, not the menu
        page.mouse.wheel(0, 300)
        time.sleep(5.0)
        for _ in range(3):
            page.mouse.wheel(0, 180)
            time.sleep(1.2)
        time.sleep(1.5)
        mark("end")

        video = page.video.path()
        ctx.close()
        browser.close()
    Path(video).rename(out / "raw.webm")
    (out / "scenes.json").write_text(json.dumps(scenes, indent=2) + "\n", encoding="utf-8")
    print(out / "raw.webm")


if __name__ == "__main__":
    main()
