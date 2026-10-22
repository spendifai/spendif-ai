"""The shared llama.cpp model pool: one copy per model, one caller at a time."""
from __future__ import annotations

import threading
import time

import pytest

from core.llm_backends import _SharedModelPool


@pytest.fixture
def pool():
    p = _SharedModelPool(idle_release_s=3600)
    yield p
    p.clear()


def _counting_loader():
    calls = []

    def load():
        calls.append(1)
        return object()

    return load, calls


def test_same_key_loads_once(pool):
    load, calls = _counting_loader()
    a = pool.get(("m.gguf", 0, -1), load)["llm"]
    b = pool.get(("m.gguf", 0, -1), load)["llm"]
    assert a is b
    assert len(calls) == 1


def test_different_keys_load_separately(pool):
    load, calls = _counting_loader()
    pool.get(("a.gguf", 0, -1), load)
    pool.get(("b.gguf", 0, -1), load)
    assert len(calls) == 2
    assert len(pool.loaded_keys()) == 2


def test_busy_while_leased(pool):
    load, _ = _counting_loader()
    key = ("m.gguf", 0, -1)
    slot = pool.get(key, load)
    assert not pool.busy(key)
    with slot["lock"]:
        assert pool.busy(key)
    assert not pool.busy(key)


def test_idle_model_released_and_reloaded(pool):
    load, calls = _counting_loader()
    key = ("m.gguf", 0, -1)
    pool.get(key, load)
    assert pool.release_idle(now=time.monotonic() + 7200) == 1
    assert pool.loaded_keys() == []
    pool.get(key, load)
    assert len(calls) == 2


def test_model_in_use_is_never_released(pool):
    load, _ = _counting_loader()
    key = ("m.gguf", 0, -1)
    slot = pool.get(key, load)
    with slot["lock"]:
        assert pool.release_idle(now=time.monotonic() + 7200) == 0
    assert pool.loaded_keys() == [key]


def test_backend_calls_never_overlap(monkeypatch):
    """Two backends on the same model serialise their calls through the lease."""
    import core.llm_backends as lb

    pool = _SharedModelPool(idle_release_s=3600)
    monkeypatch.setattr(lb, "SHARED_MODELS", pool)

    active, peak = [0], [0]
    guard = threading.Lock()

    def make_backend():
        b = lb.LlamaCppBackend.__new__(lb.LlamaCppBackend)
        b._model_path = "/tmp/m.gguf"
        b._pool_key = ("/tmp/m.gguf", 0, -1)
        b._loader = object
        return b

    def call(b):
        with b._lease():
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with guard:
                active[0] -= 1

    backends = [make_backend(), make_backend()]
    threads = [threading.Thread(target=call, args=(backends[i % 2],)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    pool.clear()
    assert peak[0] == 1


def test_backend_shares_the_pooled_model(monkeypatch):
    import core.llm_backends as lb

    pool = _SharedModelPool(idle_release_s=3600)
    monkeypatch.setattr(lb, "SHARED_MODELS", pool)
    load, calls = _counting_loader()

    def make_backend():
        b = lb.LlamaCppBackend.__new__(lb.LlamaCppBackend)
        b._pool_key = ("/tmp/m.gguf", 0, -1)
        b._loader = load
        return b

    a, b = make_backend(), make_backend()
    assert a._llm is b._llm
    assert len(calls) == 1
    pool.clear()


def test_assigned_model_bypasses_the_pool():
    """Test stubs assign _llm directly: no pool, no lock, never busy."""
    import core.llm_backends as lb

    b = lb.LlamaCppBackend.__new__(lb.LlamaCppBackend)
    stub = object()
    b._llm = stub
    assert b._llm is stub
    assert b.is_busy() is False
    with b._lease():
        pass
