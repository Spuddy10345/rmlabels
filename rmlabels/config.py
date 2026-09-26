"""Settings from ~/.config/rmlabels/config.toml. Every key is optional.

    printer = "EPSON_XP_2200_Series"   # CUPS queue; RMLABELS_PRINTER env var still wins
    nudge_x = 0.0                      # printer calibration offset in mm (+ = right)
    nudge_y = 0.0                      #                                   (+ = down)
    out_dir = "~/Downloads"            # where finished sheets are saved

    [presets]                          # text labels: --text @return, or buttons in the web UI
    return = "Return to:\\nFin Jones\\n..."

    [mail]                             # web UI "Check mail" (Proton Bridge IMAP)
    host = "127.0.0.1"
    port = 1143
    user = "you@proton.me"
    keychain_service = "rmlabels-imap" # password: security add-generic-password -s rmlabels-imap -a <user> -w
    mailbox = "Labels"                 # a Proton label/folder your filters fill
    senders = []                       # or/and: only import from these addresses or domains
    days = 7
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("RMLABELS_CONFIG", Path.home() / ".config/rmlabels/config.toml"))
DATA_DIR = Path(os.environ.get("RMLABELS_DATA", Path.home() / ".local/share/rmlabels"))


def load() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            cfg = tomllib.load(f)
    cfg.setdefault("printer", "EPSON_XP_2200_Series")
    if os.environ.get("RMLABELS_PRINTER"):
        cfg["printer"] = os.environ["RMLABELS_PRINTER"]
    cfg.setdefault("nudge_x", 0.0)
    cfg.setdefault("nudge_y", 0.0)
    cfg["out_dir"] = Path(cfg.get("out_dir", "~/Downloads")).expanduser()
    cfg.setdefault("presets", {})
    mail = cfg.setdefault("mail", {})
    mail.setdefault("host", "127.0.0.1")
    mail.setdefault("port", 1143)
    mail.setdefault("keychain_service", "rmlabels-imap")
    mail.setdefault("mailbox", "INBOX")
    mail.setdefault("senders", [])
    mail.setdefault("days", 7)
    return cfg


CONFIG = load()
