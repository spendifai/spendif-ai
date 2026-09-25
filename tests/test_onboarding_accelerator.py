"""The first run should not quietly choose the slow way round.

WHY IT EXISTS
    The onboarding wrote "0" layers on the accelerator, flat, with a note
    saying the user could opt in through Settings. Nothing told them the
    setting existed. On a machine with a working accelerator the first import
    therefore ran on the processor, which is the difference between minutes
    and hours, and for somebody trying the application for the first time it
    is the difference between slow and abandoned.

    Meanwhile the five per-phase defaults all said -1. The two halves of the
    same configuration disagreed, so the flat 0 was not even prudent: it was
    only confusing.

    Two lines above it, the context window is not flat: it is computed by
    asking the model file, in one place, which was the fix for the release
    where a fixed 4096 sat below the size of our own prompts and every import
    failed. This applies that reasoning to the accelerator.

WHAT IS ASKED, AND OF WHOM
    The library, not the machine. Having a graphics card and being able to use
    it are different facts, and the second one is a property of how the
    inference library was built. Setting layers on a card the build cannot
    address is how a model fails to load at all, which this project has
    already shipped twice.
"""

from __future__ import annotations

import pytest

from core import runtime_info
from services import llm_service


@pytest.fixture()
def devices(monkeypatch):
    """Control what the inference library reports having registered."""

    def _set(names):
        monkeypatch.setattr(runtime_info, "_ggml_devices", lambda: names)

    return _set


def test_an_accelerator_the_library_can_address_gets_every_layer(devices):
    devices(["MTL0", "CPU"])
    assert llm_service.recommended_gpu_layers() == -1


def test_a_build_that_only_registered_the_processor_stays_on_it(devices):
    """Answering yes wrongly costs a model that will not load at all."""
    devices(["CPU"])
    assert llm_service.recommended_gpu_layers() == 0


def test_a_library_that_registered_nothing_stays_on_the_processor(devices):
    """An empty list is the answer when a user reports no model will load."""
    devices([])
    assert llm_service.recommended_gpu_layers() == 0


def test_the_name_of_the_card_is_not_what_decides(devices):
    """CUDA, Metal, Vulkan, or something not invented yet: all the same here.

    The check is "something other than the processor", not a list of names to
    keep up to date.
    """
    for name in ("CUDA0", "MTL0", "Vulkan0", "SYCL0", "something-new"):
        devices([name, "CPU"])
        assert llm_service.recommended_gpu_layers() == -1, name


def test_the_onboarding_asks_instead_of_assuming():
    """The flat zero is gone from the first-run configuration."""
    import inspect

    from ui import onboarding_page

    source = inspect.getsource(onboarding_page)
    assert "recommended_gpu_layers" in source
    assert '"llama_cpp_n_gpu_layers":  "0"' not in source, (
        "the first run still writes a flat zero"
    )


def test_the_context_is_still_computed_and_not_fixed():
    """The other half of the same first-run configuration, kept honest.

    A fixed context below the size of our own prompts is what made every
    import fail in a published release. This asserts the computation is still
    what decides, next to the accelerator now doing the same.
    """
    import inspect

    from ui import onboarding_page

    source = inspect.getsource(onboarding_page)
    assert "recommended_llama_cpp_context" in source
    assert '"llama_cpp_n_ctx":         "4096"' not in source
