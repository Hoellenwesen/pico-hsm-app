"""gui.tabs — ein Tab pro CLI-Kommandogruppe (Konzept §8), alle ausgebaut.
"""

from gui.tabs.backup_tab import BackupTab
from gui.tabs.dkek_tab import DkekTab
from gui.tabs.firmware_tab import FirmwareTab
from gui.tabs.keys_tab import KeysTab
from gui.tabs.logs_tab import LogsTab
from gui.tabs.pin_tab import PinTab
from gui.tabs.setup_tab import SetupTab
from gui.tabs.status_tab import StatusTab
from gui.tabs.wizard_tab import WizardTab

__all__ = [
    "StatusTab",
    "SetupTab",
    "PinTab",
    "DkekTab",
    "KeysTab",
    "BackupTab",
    "FirmwareTab",
    "LogsTab",
    "WizardTab",
]
