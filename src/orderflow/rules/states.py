from enum import Enum


class State(str, Enum):
    """Zulässige Zustände (Kap. 7.5). HALT_NEW_ENTRIES wird zusätzlich als Flag geführt."""
    IDLE = "IDLE"
    ARMED = "ARMED"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    COOLDOWN = "COOLDOWN"
    DONE_FOR_SESSION = "DONE_FOR_SESSION"


HALT_NEW_ENTRIES = "HALT_NEW_ENTRIES"
