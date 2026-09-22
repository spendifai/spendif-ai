"""Rilevazione dell'ambiente di esecuzione, per cio' che l'utente deve sapere.

Nasce il 2026-09-22 da un caso reale: un pacchetto x64 avviato su Windows ARM
dentro una macchina virtuale. L'app si installa, si avvia e sembra bloccarsi al
primo import, perche' l'inferenza di un modello locale emulata istruzione per
istruzione impiega un tempo che dall'esterno non si distingue da un blocco.
Non c'e' niente da riparare nel motore: c'e' da dirlo.
"""

from __future__ import annotations

import sys

# Costanti di IMAGE_FILE_MACHINE (winnt.h)
_IMAGE_FILE_MACHINE_UNKNOWN = 0x0000
_IMAGE_FILE_MACHINE_ARM64 = 0xAA64


def _verdict(process_machine: int, native_machine: int) -> bool:
    """La regola, separata dalla chiamata a Windows perche' sia verificabile.

    ``process_machine`` vale UNKNOWN quando il processo NON e' emulato; in quel
    caso ``native_machine`` non dice nulla di utile. Emulato su ARM64 significa
    entrambe le condizioni insieme.
    """
    if process_machine == _IMAGE_FILE_MACHINE_UNKNOWN:
        return False
    return native_machine == _IMAGE_FILE_MACHINE_ARM64


def is_emulated_x64_on_arm() -> bool:
    """True se questo processo x64 gira emulato su un Windows ARM64.

    Usa ``IsWow64Process2``, che e' l'unica via corretta: le variabili
    d'ambiente ``PROCESSOR_ARCHITECTURE`` riportano l'architettura *vista dal
    processo*, cioe' AMD64, e quindi non distinguono l'emulazione dal nativo.

    Restituisce False fuori da Windows, su API assenti o a qualunque errore:
    questa funzione informa, non deve mai impedire l'avvio.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        is_wow64_process2 = getattr(kernel32, "IsWow64Process2", None)
        if is_wow64_process2 is None:
            return False  # Windows anteriore alla 10 1511

        process_machine = ctypes.c_ushort()
        native_machine = ctypes.c_ushort()
        ok = is_wow64_process2(
            kernel32.GetCurrentProcess(),
            ctypes.byref(process_machine),
            ctypes.byref(native_machine),
        )
        if not ok:
            return False
        return _verdict(process_machine.value, native_machine.value)
    except Exception:
        return False
