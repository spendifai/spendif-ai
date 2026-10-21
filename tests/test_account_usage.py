"""How the person uses an account, in words they can answer.

The banking types (credit card, debit card, prepaid, savings...) asked for
knowledge most people do not have, and the old column defaulted to
bank_account for every account, so it said nothing. Two answers replace it:
"payments" (ci paghi) and "in_and_out" (ci entrano e escono soldi). The answer
decides only when an import asks about the direction of the amounts.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def session():
    from db.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_usage_is_written_and_read_back(session):
    from db import repository

    repository.create_account(session, "amex", usage="payments")
    session.commit()
    assert repository.get_account_usage(session, "amex") == "payments"


def test_an_account_never_described_has_no_usage(session):
    from db import repository

    repository.create_account(session, "conto", account_type="bank_account")
    session.commit()
    assert repository.get_account_usage(session, "conto") is None
    assert repository.get_account_usage(session, None) is None
    assert repository.get_account_usage(session, "mai visto") is None


def test_an_account_seen_only_in_the_movements_can_be_described(session):
    from db import repository

    repository.set_account_usage(session, "satispay", "payments")
    session.commit()
    assert repository.get_account_usage(session, "satispay") == "payments"


def test_renaming_keeps_or_changes_the_usage(session):
    from db import repository

    acc = repository.create_account(session, "carta", usage="payments")
    session.commit()
    repository.rename_account(session, acc.id, "carta nuova")
    session.commit()
    assert repository.get_account_usage(session, "carta nuova") == "payments"
    repository.rename_account(session, acc.id, "carta nuova", new_usage="in_and_out")
    session.commit()
    assert repository.get_account_usage(session, "carta nuova") == "in_and_out"


def test_the_migration_adds_an_empty_column_to_an_existing_database(tmp_path):
    """The old account_type is not carried over: it was bank_account for all."""
    from db.models import _migrate_add_account_usage

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY, name VARCHAR(256), "
                          "account_type VARCHAR(32))"))
        conn.execute(text("INSERT INTO account (name, account_type) VALUES ('amex', 'bank_account')"))
        conn.commit()

    _migrate_add_account_usage(engine)
    _migrate_add_account_usage(engine)  # idempotent

    with engine.connect() as conn:
        assert conn.execute(text("SELECT usage FROM account")).scalar() is None
