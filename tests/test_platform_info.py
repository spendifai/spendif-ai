"""La rilevazione dell'emulazione x64 su Windows ARM.

Nasce da un caso reale del 2026-09-22: un pacchetto x64 in una macchina
virtuale Windows ARM, dove l'inferenza locale e' cosi' lenta da sembrare un
blocco. La regola si verifica qui senza Windows; la chiamata a IsWow64Process2
no, e resta coperta solo dall'uso.
"""

import sys

from core.platform_info import (
    _IMAGE_FILE_MACHINE_ARM64,
    _IMAGE_FILE_MACHINE_UNKNOWN,
    _verdict,
    is_emulated_x64_on_arm,
)

_AMD64 = 0x8664


def test_un_processo_non_emulato_non_e_emulato():
    # UNKNOWN nel primo campo e' il modo in cui Windows dice "nativo":
    # cio' che c'e' nel secondo campo a quel punto non conta.
    assert _verdict(_IMAGE_FILE_MACHINE_UNKNOWN, _IMAGE_FILE_MACHINE_ARM64) is False
    assert _verdict(_IMAGE_FILE_MACHINE_UNKNOWN, _AMD64) is False


def test_x64_emulato_su_arm64_e_il_caso_che_ci_interessa():
    assert _verdict(_AMD64, _IMAGE_FILE_MACHINE_ARM64) is True


def test_emulazione_su_una_macchina_non_arm_non_ci_riguarda():
    # Emulato si', ma non su ARM64: l'avviso parlerebbe di una cosa falsa.
    assert _verdict(_AMD64, _AMD64) is False


def test_fuori_da_windows_risponde_sempre_no(monkeypatch):
    # La funzione informa e non deve mai impedire l'avvio: fuori da Windows
    # esce prima di toccare ctypes, che li' non avrebbe windll.
    monkeypatch.setattr(sys, "platform", "darwin")
    assert is_emulated_x64_on_arm() is False
