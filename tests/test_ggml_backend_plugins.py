"""Registering the accelerator plugins before a model is built.

WHY IT EXISTS
    The wheels of the inference library are moving to our own builds, made with
    the backends as plugins loaded at runtime rather than linked at build time.
    That is what lets one package carry the processor backend and Vulkan side
    by side: with link-time backends, adding an accelerator means shipping a
    second wheel, and that is how the CUDA one comes to weigh 460 MB while the
    Vulkan plugin weighs four.

    The catch is where the library looks for those plugins: next to the
    executable. For us the executable is python, so it finds none, and a model
    that should run on the graphics card does not start at all. Pointing it at
    the library's own directory is the whole fix, and it has to happen before
    the first model is built: a backend registered afterwards is a backend the
    model was never offered.

    The call is added now, while the shipped wheels still link their backends,
    so the wheels can change underneath without a second change in the product.
"""

from __future__ import annotations

import pytest

from core import llm_backends


@pytest.fixture(autouse=True)
def unloaded():
    """The module remembers it has loaded. Each case starts from scratch."""
    saved = llm_backends._BACKENDS_LOADED
    llm_backends._BACKENDS_LOADED = False
    yield
    llm_backends._BACKENDS_LOADED = saved


def test_it_reports_the_devices_that_end_up_registered():
    devices = llm_backends.load_ggml_backend_plugins()

    assert isinstance(devices, list)
    # Whatever else a machine has, the processor is always there.
    assert any(d.upper() == "CPU" for d in devices), devices


def test_it_loads_once_and_not_on_every_model(monkeypatch):
    """A model is built per phase and per file; loading each time is waste."""
    calls: list[int] = []
    monkeypatch.setattr(
        llm_backends, "_registered_devices", lambda: calls.append(1) or ["CPU"]
    )

    llm_backends.load_ggml_backend_plugins()
    first = len(calls)
    llm_backends.load_ggml_backend_plugins()
    llm_backends.load_ggml_backend_plugins()

    assert llm_backends._BACKENDS_LOADED is True
    assert len(calls) > first, "the second call should still report the devices"


def test_a_library_that_cannot_be_asked_does_not_stop_a_model(monkeypatch):
    """Never the reason a model fails to load.

    The plugins are an optimisation on today's wheels and a requirement on
    tomorrow's, but in neither case is raising from here an improvement over
    letting the model try.
    """
    import builtins

    real_import = builtins.__import__

    def _refuse(name, *args, **kwargs):
        if name.startswith("llama_cpp"):
            raise ImportError("no inference library here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _refuse)
    monkeypatch.setattr(llm_backends, "_registered_devices", lambda: [])

    assert llm_backends.load_ggml_backend_plugins() == []
    assert llm_backends._BACKENDS_LOADED is True


def test_the_plugins_are_registered_before_the_model_is_built():
    """Order is the whole point, so it is asserted rather than assumed."""
    import inspect

    source = inspect.getsource(llm_backends.LlamaCppBackend)
    load_at = source.find("load_ggml_backend_plugins()")
    model_at = source.find("self._llm = Llama(")

    assert load_at != -1, "the plugins are never registered"
    assert model_at != -1
    assert load_at < model_at, (
        "the model is built before the accelerators are registered, so it is "
        "offered only the ones that were linked at build time"
    )
