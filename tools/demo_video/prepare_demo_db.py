#!/usr/bin/env python3
"""A demo database for the first-use video: empty, onboarding done, Italian,
the local model given on the command line. Nothing of the user's.

Usage: python tools/demo_video/prepare_demo_db.py DB_PATH MODEL.gguf [LANG]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine  # noqa: E402

from db.models import create_tables  # noqa: E402
from services.settings_service import SettingsService  # noqa: E402


def main() -> None:
    db, model = Path(sys.argv[1]), sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "it"
    if db.exists():
        db.unlink()
    cfg = SettingsService(create_tables(create_engine(f"sqlite:///{db}")))
    cfg.set_bulk({
        "onboarding_done": "true",
        "ui_language": lang,
        "description_language": lang,
        "llm_backend": "local_llama_cpp",
        "llama_cpp_model_path": model,
        "guided_welcome_seen": "true",
        "owner_names": "Mario Rossi",     # invented: the import asks for one
    })
    cfg.apply_default_taxonomy(lang)    # the categories in the video's language, as the onboarding does
    print(db)


if __name__ == "__main__":
    main()
