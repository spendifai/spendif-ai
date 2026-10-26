#!/usr/bin/env python3
"""A bank statement with invented movements, for the first-use video.

Three months up to yesterday, merchants that do not exist, a salary and the
usual spending; yesterday a 23,40 at a supermarket, the receipt the video
confirms. Same seed, same statement: the video can be made again.

Usage: python tools/demo_video/make_demo_statement.py OUT.csv [YYYY-MM-DD]
(the date is "today", by default the real one).
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta

# Invented names only: no real brand on screen.
SPENDING = [
    ("SUPERMERCATO CENTRALE", 18, 95, 9),
    ("BAR DEL CORSO", 1.2, 6, 14),
    ("FORNO SAN MARCO", 2, 9, 6),
    ("FARMACIA DEL PONTE", 6, 35, 3),
    ("DISTRIBUTORE NORD", 35, 70, 4),
    ("TRATTORIA DA GINO", 22, 60, 3),
    ("LIBRERIA PAGINA", 9, 30, 1),
    ("CINEMA ARCOBALENO", 8, 20, 2),
    ("ABBIGLIAMENTO VIA ROMA", 25, 90, 1),
    ("TELEFONIA MOBILE", 9.99, 9.99, 1),
    ("PALESTRA FORMA", 39, 39, 1),
]


def statement(today: date, seed: int = 7) -> list[tuple[date, str, float]]:
    rng = random.Random(seed)
    start = (today.replace(day=1) - timedelta(days=62)).replace(day=1)
    rows: list[tuple[date, str, float]] = []
    month = start
    while month <= today:
        salary_day = month.replace(day=27)
        if salary_day < today:
            rows.append((salary_day, "STIPENDIO MESE", 1850.00))
        for name, low, high, per_month in SPENDING:
            for _ in range(per_month):
                day = month + timedelta(days=rng.randrange(0, 28))
                if day < today - timedelta(days=1):
                    rows.append((day, name, -round(rng.uniform(low, high), 2)))
        month = (month + timedelta(days=32)).replace(day=1)
    rows.append((today - timedelta(days=1), "SUPERMERCATO CENTRALE", -23.40))
    return sorted(rows)


def main() -> None:
    out = sys.argv[1]
    today = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Data", "Descrizione", "Importo"])
        for day, desc, amount in statement(today):
            w.writerow([day.strftime("%d/%m/%Y"), desc, f"{amount:.2f}".replace(".", ",")])
    print(out)


if __name__ == "__main__":
    main()
