# Lizenzhinweis (pico-hsm-app)

## Eigener Code

Der in diesem Repository enthaltene, selbst geschriebene Code steht
unter der **MIT License** (siehe `LICENSE`).

## Kombiniertes Werk

Die App nutzt `PySide6-Fluent-Widgets` (**GPLv3**). Dadurch gilt für ein
weitergegebenes Gesamtwerk (z.B. ein Binary): Es ist zu den Bedingungen
der GPLv3 zu behandeln — insbesondere muss der vollständige Quellcode
(`Corresponding Source`) verfügbar gemacht werden. Dieses Repository
(öffentlich auf GitHub) erfüllt genau das; bei Weitergabe zusätzlich
die Lizenztexte der untenstehenden Pakete beilegen (liegen jeweils im
installierten venv unter `Lib/site-packages/<paket>-*/`).

Bei reinem Eigengebrauch (keine Weitergabe) entsteht daraus keine
Handlungspflicht.

## Abhängigkeiten (Laufzeit, Stand 2026-09, verifiziert aus Paket-Metadaten)

| Paket | Version (getestet) | Lizenz |
|---|---|---|
| `click` | 8.5.0 | BSD-3-Clause |
| `python-pkcs11` | 0.10.0 | MIT |
| `pyotp` | 2.10.0 | MIT |
| `pyscard` | 2.3.1 | LGPLv2+ |
| `PySide6` (+ Essentials/Addons) | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (Wahl) |
| `PySide6-Fluent-Widgets` | 1.11.3 | GPLv3 |

## Abhängigkeiten (Entwicklung/Tests)

| Paket | Version (getestet) | Lizenz |
|---|---|---|
| `pytest` | 9.1.1 | MIT |
| `pytest-qt` | 4.5.0 | MIT |

## Externe Programme (keine Python-Pakete, eigene Lizenzen beachten)

`picotool` (Raspberry Pi), OpenSC-Toolchain (`opensc-pkcs11`,
`pkcs11-tool`, `sc-hsm-tool`), `age`/`age-keygen` (FiloSottile), `ssss`
(B. Drewery). Jeweilige Lizenz den Upstream-Repos entnehmen; Details
zur Beschaffung siehe README, Abschnitt 2.
