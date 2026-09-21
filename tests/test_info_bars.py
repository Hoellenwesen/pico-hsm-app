"""
test_info_bars.py — InfoBar-Aufräumen ohne GUI-Crash.

Regression: Nutzer schließt InfoBar per X (C++-Objekt gelöscht),
nächster Refresh rief bar.close() auf totem Wrapper ->
RuntimeError aus libshiboken. close_info_bars() ist tolerant.
"""

from __future__ import annotations

from gui.session_helpers import close_info_bars


class _DeadBar:
    """Bar mit gelöschtem C++-Objekt (wirft wie libshiboken)."""

    def __init__(self):
        self.closed = False

    def close(self):
        raise RuntimeError(
            "libshiboken: Internal C++ object (InfoBar) already deleted."
        )


class _LiveBar:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_close_info_bars_tolerates_dead_bars():
    dead = _DeadBar()
    live = _LiveBar()
    bars = [dead, live]
    close_info_bars(bars)  # darf nicht werfen
    assert bars == []
    assert live.closed is True


def test_close_info_bars_empty():
    bars: list = []
    close_info_bars(bars)
    assert bars == []


def test_wizard_clear_bars_tolerates_dead_bar(qapp, qtbot, monkeypatch, tmp_path):
    """End-to-End am Melder-Fall: Wizard-DKEK-Abfrage nach X-Klick."""
    from gui.tabs import wizard_tab as wiz_mod

    monkeypatch.setattr(wiz_mod, "STATE_FILE", tmp_path / "setup_state.json")
    tab = wiz_mod.WizardTab()
    qtbot.addWidget(tab)
    try:
        dead = _DeadBar()
        tab._info_bars.append(dead)  # type: ignore[arg-type]
        tab._clear_bars()  # früher: RuntimeError (Traceback des Users)
        assert tab._info_bars == []
    finally:
        tab.close()
