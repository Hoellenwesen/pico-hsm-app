"""test_device_mode.py — Modus-Sensor + CLI-Gates + GUI-Gating (ohne Hardware).

Alle Sensoren gemockt: BOOTSEL via is_secure_boot_enabled, Normal via
get_token, picotool-Verfügbarkeit via check_picotool_available.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pico_hsm_tools import device_mode as dm
from pico_hsm_tools.device_mode import DeviceMode


def _patch(monkeypatch, *, bootsel=False, token=False, picotool=True):
    import pico_hsm_tools.flash_core as fc
    import pico_hsm_tools.pkcs11_session as ps

    if picotool:
        monkeypatch.setattr(fc, "check_picotool_available", lambda: None)
    else:
        def _missing() -> None:
            raise fc.FlashError("picotool fehlt")
        monkeypatch.setattr(fc, "check_picotool_available", _missing)

    if bootsel:
        monkeypatch.setattr(fc, "is_secure_boot_enabled", lambda: True)
    else:
        def _no_board() -> bool:
            raise fc.FlashError("kein Board")
        monkeypatch.setattr(fc, "is_secure_boot_enabled", _no_board)

    if token:
        monkeypatch.setattr(ps, "get_token", lambda *a, **k: object())
        monkeypatch.setattr(
            ps, "list_tokens",
            lambda *a, **k: [{"label": "T", "model": "M", "serial": "SN1"}],
        )
    else:
        def _no_token(*a, **k):
            raise Exception("kein Token")
        monkeypatch.setattr(ps, "get_token", _no_token)
        monkeypatch.setattr(ps, "list_tokens", lambda *a, **k: [])


def test_detect_bootsel(monkeypatch):
    _patch(monkeypatch, bootsel=True, token=False)
    assert dm.detect().mode == DeviceMode.BOOTSEL


def test_detect_normal(monkeypatch):
    _patch(monkeypatch, bootsel=False, token=True)
    state = dm.detect()
    assert state.mode == DeviceMode.NORMAL
    assert state.token_present is True


def test_detect_no_device(monkeypatch):
    _patch(monkeypatch, bootsel=False, token=False)
    state = dm.detect()
    assert state.mode == DeviceMode.KEIN_GERAET


def test_detect_bootsel_wins_over_token(monkeypatch):
    _patch(monkeypatch, bootsel=True, token=True)
    assert dm.detect().mode == DeviceMode.BOOTSEL


def test_route_modes_matrix():
    assert dm.ROUTE_MODES["pin"] == {DeviceMode.NORMAL}
    assert dm.ROUTE_MODES["keys"] == {DeviceMode.NORMAL}
    assert dm.ROUTE_MODES["dkek"] == {DeviceMode.NORMAL}
    assert DeviceMode.BOOTSEL in dm.ROUTE_MODES["firmware"]
    assert DeviceMode.NORMAL in dm.ROUTE_MODES["firmware"]
    assert DeviceMode.NORMAL in dm.ROUTE_MODES["setup"]
    assert DeviceMode.BOOTSEL in dm.ROUTE_MODES["setup"]
    assert len(dm.ROUTE_MODES["backup"]) == 3
    assert len(dm.ROUTE_MODES["logs"]) == 3  # rein lesend, immer nutzbar
    assert len(dm.ROUTE_MODES["wizard"]) == 3  # Einstieg, Schritte prüfen selbst


def test_require_bootsel_blocks_in_normal(monkeypatch):
    from cli.commands import _common as com

    _patch(monkeypatch, bootsel=False, token=True)

    class Ctx:
        def __init__(self):
            self.msg = ""
        def fail(self, msg, exit_code=2):
            self.msg = msg

    @com.require_bootsel
    def cmd(ctx):
        return "ran"

    ctx = Ctx()
    assert cmd(ctx) is None
    assert "BOOTSEL" in ctx.msg


def test_require_bootsel_passes_without_hardware(monkeypatch):
    from cli.commands import _common as com

    _patch(monkeypatch, bootsel=False, token=False)

    class Ctx:
        def fail(self, msg, exit_code=2):
            raise AssertionError("darf ohne Hardware nicht blockieren")

    @com.require_bootsel
    def cmd(ctx):
        return "ran"

    assert cmd(Ctx()) == "ran"


def test_require_normal_blocks_in_bootsel(monkeypatch):
    from cli.commands import _common as com

    _patch(monkeypatch, bootsel=True, token=False)

    class Ctx:
        def __init__(self):
            self.msg = ""
        def fail(self, msg, exit_code=2):
            self.msg = msg

    @com.require_normal
    def cmd(ctx):
        return "ran"

    ctx = Ctx()
    assert cmd(ctx) is None
    assert "Normal" in ctx.msg


def test_setup_tab_sections_gate_per_mode(qapp, monkeypatch):
    _patch(monkeypatch, bootsel=False, token=False)
    from gui.tabs.setup_tab import SetupTab

    tab = SetupTab()
    try:
        tab.apply_device_mode(DeviceMode.BOOTSEL)
        assert tab.otpTable.isEnabled()
        assert not tab.ptcSwitch.isEnabled()
        assert not tab.dynoptsApplyButton.isEnabled()

        tab.apply_device_mode(DeviceMode.NORMAL)
        assert not tab.otpTable.isEnabled()
        assert tab.ptcSwitch.isEnabled()
        assert tab.dynoptsApplyButton.isEnabled()

        tab.apply_device_mode(DeviceMode.KEIN_GERAET)
        assert not tab.otpTable.isEnabled()
        assert not tab.ptcSwitch.isEnabled()
        assert not tab.setupRefreshButton.isEnabled()
    finally:
        tab.close()


def test_firmware_flash_gated_to_bootsel(qapp):
    from gui.tabs.firmware_tab import FirmwareTab

    tab = FirmwareTab()
    try:
        tab.apply_device_mode(DeviceMode.BOOTSEL)
        assert tab.flashButton.isEnabled()
        tab.apply_device_mode(DeviceMode.NORMAL)
        assert not tab.flashButton.isEnabled()
        # Preflight/Audit bleiben modusfrei nutzbar:
        assert tab.preflightButton.isEnabled()
    finally:
        tab.close()


def test_main_window_applies_mode_to_nav(qapp, monkeypatch):
    _patch(monkeypatch, bootsel=False, token=False)
    from gui.main_window import MainWindow

    window = MainWindow()
    try:
        window._poller.stop()
        window._apply_mode(dm.DeviceState(
            mode=DeviceMode.BOOTSEL, picotool_ok=True,
            token_present=False, detail="test",
        ))
        assert window.device_state.mode == DeviceMode.BOOTSEL
        pin_nav = window.navigationInterface.widget("pin")
        assert pin_nav is not None and not pin_nav.isEnabled()
        backup_nav = window.navigationInterface.widget("backup")
        assert backup_nav is not None and backup_nav.isEnabled()
    finally:
        window._poller.stop()
        window.close()
