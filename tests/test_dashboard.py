"""
test_dashboard.py — Empfehlungs-Matrix ohne GUI und ohne Hardware.

Reine Funktion dashboard.collect(): je Zustand die richtige
Empfehlung mit Sprung-Ziel.
"""

from __future__ import annotations

from pico_hsm_tools import dashboard as dash

LABELS = (
    ("user_pin_locked", "PIN gesperrt"),
    ("user_pin_final_try", "Letzter Versuch"),
)
CRITICAL = ("user_pin_locked", "user_pin_final_try")


def test_no_device_short_circuits():
    reco = dash.collect(
        device_present=False, pin_flags=None, chain_intact=None,
    )
    assert len(reco) == 1
    assert reco[0].route is None
    assert "USB" in reco[0].text


def test_all_good_is_empty():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True, "user_pin_locked": False},
        chain_intact=True,
        setup_done={"detect": True, "init": True},
        backup_warnings=[],
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert reco == []


def test_pin_critical_points_to_pin():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True, "user_pin_locked": True},
        chain_intact=True,
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert [(r.route) for r in reco] == ["pin"]
    assert "gesperrt" in reco[0].text


def test_uninitialized_pin_points_to_wizard():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": False},
        chain_intact=True,
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert reco[0].route == "wizard"


def test_broken_chain_points_to_logs():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True},
        chain_intact=False,
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert [(r.route) for r in reco] == ["logs"]


def test_open_setup_points_to_wizard():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True},
        chain_intact=True,
        setup_done={"detect": True, "init": False, "pin": False},
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert reco[0].route == "wizard"
    assert "1/3" in reco[0].text


def test_backup_warnings_point_to_backup():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True},
        chain_intact=True,
        backup_warnings=["noch nie Drill", "Backup 120 Tage alt"],
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert [r.route for r in reco] == ["backup", "backup"]
    assert "Drill" in reco[0].text


def test_priority_order():
    reco = dash.collect(
        device_present=True,
        pin_flags={"user_pin_initialized": True, "user_pin_locked": True},
        chain_intact=False,
        setup_done={"detect": False},
        backup_warnings=["x"],
        critical_pin_flags=CRITICAL,
        pin_labels=LABELS,
    )
    assert [r.route for r in reco] == ["pin", "logs", "wizard", "backup"]
