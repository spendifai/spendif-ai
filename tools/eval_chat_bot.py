"""Measure the support bot against the question set in tests/chat_bot/questions.json.

Each question is asked in Italian and in English. An answer scores on two axes:
  - page:  it points to the expected page (page key in its sources, or the
           page's menu label in the text);
  - facts: share of the expected facts it contains (any alternative matches).

Not a unit test: the result depends on the knowledge, the retrieval and the
model, and is meant to compare the bot before and after a change.

Usage:
    python tools/eval_chat_bot.py                      # FAQ-only, no model
    python tools/eval_chat_bot.py --db sqlite:///...   # mode from that DB's settings
    python tools/eval_chat_bot.py --out results.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chat_bot.engine import ChatBotEngine

QUESTIONS = ROOT / "tests" / "chat_bot" / "questions.json"
I18N = ROOT / "ui" / "i18n"
LANGS = ("it", "en")


def _page_label(page: str, lang: str) -> str:
    labels = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
    label = labels.get("mixed.title" if page == "mixed" else f"nav.{page}", page)
    return re.sub(r"^[^\w]+", "", label).strip().lower()


def score(answer: str, sources: list[str], page: str | None, facts: list[list[str]], lang: str) -> dict:
    text = answer.lower()
    page_ok = None
    if page:
        shown = [s for s in (sources or []) if "." not in s][:3]  # page links the chat shows
        page_ok = page in shown or _page_label(page, lang) in text
    hits = [any(alt.lower() in text for alt in fact) for fact in facts]
    return {"page_ok": page_ok, "facts_hit": sum(hits), "facts_total": len(facts)}


def run(db_url: str | None) -> dict:
    db_engine = None
    if db_url:
        from db.models import get_engine
        db_engine = get_engine(db_url)
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    results, mode = [], None
    for lang in LANGS:
        bot = ChatBotEngine(db_engine=db_engine, lang=lang)
        mode = bot.mode.value
        for item in questions:
            case = item[lang]
            resp = bot.ask(case["q"])
            s = score(resp.text, resp.sources or [], item["page"], case["facts"], lang)
            results.append({"id": item["id"], "lang": lang, "q": case["q"], "answer": resp.text,
                            "sources": resp.sources or [], **s})
    return {"mode": mode, "results": results}


def summary(report: dict) -> str:
    rows = report["results"]
    lines = [f"mode: {report['mode']}"]
    for lang in LANGS:
        sel = [r for r in rows if r["lang"] == lang]
        paged = [r for r in sel if r["page_ok"] is not None]
        page_rate = sum(r["page_ok"] for r in paged) / len(paged)
        fact_rate = sum(r["facts_hit"] for r in sel) / sum(r["facts_total"] for r in sel)
        full = sum(r["facts_hit"] == r["facts_total"] for r in sel)
        lines.append(f"{lang}: page {page_rate:.0%}  facts {fact_rate:.0%}  "
                     f"all facts {full}/{len(sel)}")
    lines.append("")
    for r in rows:
        mark = "-" if r["page_ok"] is None else ("P" if r["page_ok"] else ".")
        lines.append(f"  {r['id']} {r['lang']} {mark} {r['facts_hit']}/{r['facts_total']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="SQLAlchemy URL of a DB whose settings pick the bot mode")
    ap.add_argument("--out", help="write the full report (answers included) to this JSON file")
    args = ap.parse_args()
    report = run(args.db)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary(report))


if __name__ == "__main__":
    main()
