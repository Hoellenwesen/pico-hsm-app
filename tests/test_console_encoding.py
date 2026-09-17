"""
test_console_encoding.py — Regressionstest für F1 (UnicodeEncodeError).

Die Windows-Konsole spricht cp1252: Jedes nicht-ASCII-Zeichen in einer
.py-Datei muss cp1252-kodierbar sein, sonst crasht `ctx.echo` zur
Laufzeit (F1: U+2192 Pfeil in `backup list` — derselbe Crash, der
früher schon die Emoji betraf). Prüft alle App-Verzeichnisse.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("cli", "pico_hsm_tools", "gui", "tests")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for sub in SCANNED:
        files.extend(sorted((REPO_ROOT / sub).rglob("*.py")))
    return files


def test_scan_finds_all_sources():
    """Fängt versehentlich leere/verschobene Scans ab."""
    assert len(_python_files()) > 40


def test_all_non_ascii_is_cp1252_encodable():
    bad: dict[str, set[str]] = {}
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        for ch in text:
            if ord(ch) > 127:
                try:
                    ch.encode("cp1252")
                except UnicodeEncodeError:
                    bad.setdefault(
                        str(path.relative_to(REPO_ROOT)), set(),
                    ).add(f"U+{ord(ch):04X}")
    assert not bad, f"nicht cp1252-fähige Zeichen: {bad}"
