"""Pull label attachments out of email via Proton Mail Bridge's local IMAP.

Only messages in the configured mailbox (a Proton label/folder your filters
fill), from the last `days` days, and, if `senders` is set, from one of
those addresses/domains are looked at. Each message is handled once: its
Message-ID is remembered even if it had no usable attachment.
The password is read from the macOS Keychain, never from the config file.
"""
from __future__ import annotations

import datetime as dt
import email
import imaplib
import logging
import subprocess
from email import policy
from email.utils import parseaddr

from . import store
from .config import CONFIG
from .inputs import is_supported

log = logging.getLogger("rmlabels.mail")


class MailNotConfigured(Exception):
    pass


def password(cfg) -> str:
    try:
        return subprocess.run(["security", "find-generic-password", "-s", cfg["keychain_service"],
                               "-a", cfg["user"], "-w"], capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        raise MailNotConfigured(f"No Keychain item '{cfg['keychain_service']}' for {cfg['user']}. Add it with:\n"
                                f"security add-generic-password -s {cfg['keychain_service']} -a {cfg['user']} -w")


def sender_allowed(from_header: str, senders: list[str]) -> bool:
    if not senders:
        return True
    addr = parseaddr(from_header)[1].lower()
    return any(addr == s.lower() or addr.endswith("@" + s.lower().lstrip("@")) or addr.endswith("." + s.lower().lstrip("@"))
               for s in senders)


def fetch_into_queue(db, cfg=None) -> list[str]:
    """Queue every new label attachment. Returns the filenames added."""
    cfg = cfg or CONFIG["mail"]
    if not cfg.get("user"):
        raise MailNotConfigured("Set [mail] user = \"…\" in ~/.config/rmlabels/config.toml")
    since = (dt.date.today() - dt.timedelta(days=int(cfg["days"]))).strftime("%d-%b-%Y")
    added = []
    with imaplib.IMAP4(cfg["host"], int(cfg["port"])) as imap:
        imap.login(cfg["user"], password(cfg))
        typ, _ = imap.select(f'"{cfg["mailbox"]}"', readonly=True)
        if typ != "OK":
            raise MailNotConfigured(f"Mailbox {cfg['mailbox']!r} not found")
        _, data = imap.search(None, "SINCE", since)
        for num in data[0].split():
            _, hdr = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])")
            head = email.message_from_bytes(hdr[0][1], policy=policy.default)
            mid = (head["Message-ID"] or f"no-id:{cfg['mailbox']}:{num.decode()}").strip()
            if store.mail_seen(db, mid):
                continue
            if sender_allowed(head["From"] or "", cfg["senders"]):
                _, body = imap.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(body[0][1], policy=policy.default)
                for part in msg.iter_attachments():
                    name = part.get_filename() or ""
                    if is_supported(name):
                        _, new = store.add_file(db, name, part.get_content(), "mail")
                        if new:
                            added.append(name)
            store.mark_mail_seen(db, mid, str(head["Subject"] or ""))
    log.info("mail: %d new label file(s)", len(added))
    return added
