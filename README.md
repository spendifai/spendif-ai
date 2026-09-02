# Spendif.ai

[![CI](https://github.com/spendifai/spendif-ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/spendifai/spendif-ai/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/spendifai/spendif-ai/graph/badge.svg)](https://codecov.io/gh/spendifai/spendif-ai)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial-orange)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-ff4b4b?logo=streamlit&logoColor=white)](https://streamlit.io)
[![Issues](https://img.shields.io/github/issues/spendifai/spendif-ai)](https://github.com/spendifai/spendif-ai/issues)
[![Last commit](https://img.shields.io/github/last-commit/spendifai/spendif-ai)](https://github.com/spendifai/spendif-ai/commits/main)
[![Support on Patreon](https://img.shields.io/badge/Patreon-buy%20me%20a%20coffee%20☕-F96854?logo=patreon&logoColor=white)](https://patreon.com/drake69)

> 🇮🇹 [Leggi in italiano](README.it.md)

Unified personal finance ledger with a hybrid deterministic + LLM pipeline. Aggregates heterogeneous bank exports (current accounts, credit/debit/prepaid cards, savings accounts) into a single chronological ledger, eliminating double-counting from periodic card settlements and internal transfers. Offline-first; remote LLM backends opt-in with mandatory PII sanitization.

---

> 👋 **End users — looking to install Spendif.ai?**
> Go to the [Getting Started page](https://spendif.ai/getting-started.en.html) for the illustrated install + first-launch guide. This README is for developers and contributors.
>
> On macOS you can also install and stay up to date through Homebrew:
> ```bash
> brew tap spendifai/spendifai
> brew trust --cask spendifai/spendifai/spendifai   # once per machine, Homebrew 6+
> brew install --cask spendifai
> brew update && brew upgrade --cask spendifai     # later releases
> ```
>
> The macOS build is **Apple Silicon only** (M1 or newer). On an Intel Mac?
> [Tell us in GitHub Discussions](https://github.com/spendifai/spendif-ai/discussions) — an Intel build is not
> planned, but one person asking is enough to reconsider.

---

## What it is (technical)

- **Python 3.13 · Streamlit · SQLAlchemy · Pydantic · Pandas**
- **Hybrid pipeline**: deterministic normalizer + LLM classifier + categorizer cascade
- **Multi-backend LLM** with circuit breaker: llama.cpp (default for desktop), Ollama, OpenAI, Claude — direct SDK use, no LangChain
- **Native desktop launcher**: pywebview + PyInstaller → DMG / MSIX / .deb / .rpm
- **17-page Streamlit UI**, full IT+EN i18n (760+ translation keys)

## What's implemented

- **Hybrid pipeline (deterministic + LLM)** — `core/normalizer.py` parses any tabular bank export; `core/classifier.py` infers `DocumentSchema` via LLM; `core/categorizer.py` runs a 4-step cascade (user rules → regex → LLM → fallback)
- **Multi-backend LLM with circuit breaker** — `core/llm_backends.py` factory: llama.cpp, Ollama, OpenAI, Claude. Automatic fallback + quarantine on failure
- **PII sanitization (RF-10)** — IBAN / PAN / fiscal code / owner-name redaction in `core/sanitizer.py`, mandatory before any remote call (`assert_sanitized()` is a precondition, not best-effort)
- **Multi-language taxonomy** — 2-level in DB, 5 languages (it/en/fr/de/es), configurable from the Streamlit UI
- **Card-account reconciliation (RF-03, beta)** — 3-phase algorithm in `core/normalizer.py`: pairs credit-card settlements with the underlying expenses to eliminate double-counting *(edge cases still being refined)*
- **Internal transfer detection (RF-04, beta)** — symbolic-amount + ±7-day window matching, with owner-name permutations to catch "Cognome Nome" exports *(edge cases still being refined)*

## What's coming next

Spendif.ai is in alpha. The features we add next are decided together with the people using the app today: no speculative work, no roadmap dictated from above.

Features under evaluation based on alpha-tester feedback:

- **Cash tracking** — log cash purchases manually, with no bank statement attached
- **Investment performance** — see at a glance how the instruments in your portfolio are doing
- **Companion mobile app** — capture cash on the fly from your phone and sync with the desktop

Would one of these help you? Tell us in [GitHub Discussions](https://github.com/spendifai/spendif-ai/discussions). When enough requests stack up on the same feature, it moves to the top of the queue.

## 👩‍💻 Develop locally

```bash
git clone https://github.com/spendifai/spendif-ai.git
cd spendif-ai
uv sync --extra desktop

# Local LLM (developer choice — the desktop installer handles this automatically):
#   → if you already have Ollama running:   ollama pull gemma3:12b
#   → otherwise: `uv sync` installs llama-cpp-python and the launcher
#     auto-downloads a GGUF model on first run

./start.sh                    # or: streamlit run app.py
```

Prerequisites: **Python 3.13+**, **[uv](https://github.com/astral-sh/uv)**, and either Ollama or nothing (llama.cpp is bundled). Full setup → [CONTRIBUTING.en.md](CONTRIBUTING.en.md).

### Run as a native desktop app from source

```bash
uv run python -m desktop.launcher
```

Opens a pywebview window, downloads an AI model on first run, and starts Streamlit inside the same window. Identical to the bundled DMG/MSIX experience.

## Run tests

```bash
uv run pytest -v                                  # full suite (no LLM mocks)
uv run pytest --cov=. --cov-report=term-missing   # with coverage (target ≥ 90%)
uv run pytest -k "architecture"                   # layer separation gate
uv run pytest -k "security"                       # forbidden patterns + SQL injection
```

Architectural and security tests are mandatory CI gates and must stay green on `main`.

## Architecture

```
ui/  →  services/  →  core/  →  db/  →  SQLite
                ↑       ↑
       async_runner  llm_backends · sanitizer · normalizer · classifier · categorizer
```

UI may import only `services/`; `core/` may not import `db/`; `db/` may not import upward. The coupling gate (`tools/coupling_check.py --strict`) blocks PRs that violate this.

Full diagram and Flow 1 vs Flow 2 → [docs/architecture.md](docs/architecture.md).

## Repository layout

```
sw_artifacts/
├── app.py                  # Streamlit entry point (onboarding gate + 17 pages)
├── core/                   # Pipeline: orchestrator, normalizer, classifier, categorizer, sanitizer, llm_backends
├── services/               # Facade layer for UI; async runner; settings; import
├── ui/                     # Streamlit pages + i18n + widgets
├── db/                     # SQLAlchemy ORM, repository pattern, schema with auto-hash migrations
├── api/                    # FastAPI REST endpoints (optional)
├── desktop/                # Native launcher (pywebview) + splash
├── packaging/              # Build scripts: macos/, windows/, linux/, homebrew/, winget/
├── docker/                 # Containerisation
├── prompts/                # LLM prompt templates (versioned JSON)
├── reports/                # HTML + CSV + XLSX export
├── tests/                  # pytest suite (≥ 90% coverage target)
├── benchmark/              # LLM benchmark suite (multi-provider)
└── docs/                   # User & developer documentation
```

More detail → [docs/developer_guide.en.md](docs/developer_guide.en.md).

## 📚 Documentation

| Topic | Languages |
|---|---|
| Install & first launch | [EN](docs/installazione.en.md) · [IT](docs/installazione.md) |
| User guide (every page) | [EN](docs/guida_utente.en.md) · [IT](docs/guida_utente.md) |
| Reference guide (pipeline, taxonomy, RF-03/04) | [EN](docs/reference_guide.en.md) · [IT](docs/reference_guide.md) |
| Architecture | [EN](docs/architecture.md) · [IT](docs/architecture.it.md) |
| Design decisions | [EN](docs/design_decisions.md) · [IT](docs/design_decisions.it.md) |
| Configuration | [EN](docs/configurazione.en.md) · [IT](docs/configurazione.md) |
| Developer guide | [EN](docs/developer_guide.en.md) · [IT](docs/developer_guide.md) |
| Categorisation guide | [EN](docs/guida_classificazione.en.md) · [IT](docs/guida_classificazione.md) |
| Database schema | [EN](docs/database.en.md) · [IT](docs/database.md) |
| Deployment | [EN](docs/deployment.en.md) · [IT](docs/deployment.md) |
| Release process | [EN](docs/release_process.md) · [IT](docs/release_process.it.md) |
| Desktop build &amp; test loop | [EN](docs/desktop_build_and_test.md) · [IT](docs/desktop_build_and_test.it.md) |
| Contributing | [EN](CONTRIBUTING.en.md) · [IT](CONTRIBUTING.md) |
| Security policy | [EN](SECURITY.md) · [IT](SECURITY.it.md) |
| Changelog | [EN](CHANGELOG.md) · [IT](CHANGELOG.it.md) |

## Contributing

Bug reports, feature ideas and PRs welcome. See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) for the workflow, branch policy, priority framework, and CI gates.

## License

**PolyForm Noncommercial 1.0.0** — see [LICENSE](LICENSE). Free for personal use; commercial use requires a separate licence.

---

### What leaves the machine — be precise

All financial data is stored locally in `~/.spendifai/ledger.db`.

**Local LLM backend (default — llama.cpp, Ollama)**: nothing leaves the machine.

**Remote LLM backend (opt-in — OpenAI, Claude)**: the payload contains
sanitised descriptions **plus** transaction **amounts**, **dates**, and
**column metadata**.

#### Redaction example — categorizer (`core/categorizer.py:303`)

Raw transaction row from the CSV:

```
date:        2026-03-15
description: "BONIFICO da MARIO ROSSI IT60X0542811101000000123456 CAU 12345 STIPENDIO MENSILE"
amount:      1500.00
```

What the remote LLM actually receives:

```json
{
  "amount": "1500.00",
  "description": "BONIFICO da Carlo Brambilla <ACCOUNT_ID> <TX_CODE> STIPENDIO MENSILE"
}
```

What changed:
- `MARIO ROSSI` (configured owner name) → `Carlo Brambilla` (fake from Italian pool, restored after the LLM responds)
- `IT60X...` (IBAN) → `<ACCOUNT_ID>`
- `CAU 12345` (bank transaction code) → `<TX_CODE>`
- `amount` and date metadata: **sent in the clear**

The categorizer prompt instructs the model to "base the decision on the
description, amount, and context" (`prompts/categorizer.json`). Whether
the amount actually changes accuracy in practice has not been benchmarked
against an amount-stripped baseline — the conservative default keeps it
in the payload until measured.

#### Roadmap

Amount + date redaction modes for remote backends (`none` / `buckets` /
`strip`) are on the roadmap — backlog item **AI-55**.
