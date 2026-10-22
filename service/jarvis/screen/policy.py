"""What Jarvis may click on its own, and what needs the user first.

A click is consequential when the control (or the dialog around it, or the goal itself) is about sending,
paying, deleting, publishing, granting access or similar: Do mode then asks for that one click, with the
ring on the target, and silence is a no. The word lists are the union of VYRSE's desktop and Android
lists (MIT, © 2026 VYRSE contributors; navigator.py _RISKY, PhoneControl.java SENSITIVE @ 98adf06) with
additions, in English, Russian and Portuguese.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

CONSEQUENTIAL = re.compile(
    r"\b(delete|remove|erase|send|pay|payment|buy|purchase|order|place order|checkout|post|publish|submit|sign out|"
    r"log out|logout|unsubscribe|subscribe|format|reset|uninstall|install|discard|transfer|confirm|share|reply all|"
    r"accept|allow|grant|authori[sz]e|approve|revoke|empty trash|make public|call|dial|forward|archive all|"
    r"удал\w*|стер\w*|отправ\w*|оплат\w*|куп\w*|заказ\w*|оформ\w*|опублик\w*|выйти|выход|отпис\w*|подпис\w*|сброс\w*|"
    r"установ\w*|переда\w*|подтверд\w*|подели\w*|приня\w*|разреш\w*|позвон\w*|"
    r"excluir|apagar|remover|enviar|pagar|comprar|encomendar|finalizar|publicar|sair|cancelar assinatura|assinar|"
    r"redefinir|instalar|desinstalar|descartar|transferir|confirmar|compartilhar|aceitar|permitir|autorizar|ligar)\b",
    re.I | re.U)

# Sites and pages Jarvis never drives on its own (it can still point at things there).
BLOCKED_HOSTS = re.compile(r"(bank|banco|банк|paypal|stripe|wise\.com|revolut|coinbase|binance|kraken|"
                           r"accounts\.google\.com/.*security|myaccount\.google\.com/security)", re.I)

KNOWN_SITES = {"gmail": "https://mail.google.com/", "calendar": "https://calendar.google.com/",
               "drive": "https://drive.google.com/", "youtube": "https://www.youtube.com/",
               "github": "https://github.com/", "telegram": "https://web.telegram.org/",
               "whatsapp": "https://web.whatsapp.com/", "outlook": "https://outlook.live.com/",
               "docs": "https://docs.google.com/", "sheets": "https://sheets.google.com/"}


def consequential(*texts: str) -> bool:
    return any(t and CONSEQUENTIAL.search(t) for t in texts)


def registrable(host: str) -> str:
    parts = [p for p in host.lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def url_allowed(url: str, goal: str, current_url: str = "") -> bool:
    """A site the model suggests opening: one of the known sites, the current site, or one the user named."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    if not host or urlparse(url).scheme != "https" or BLOCKED_HOSTS.search(url):
        return False
    if any(urlparse(u).hostname == host for u in KNOWN_SITES.values()):
        return True
    if current_url and registrable(urlparse(current_url).hostname or "") == registrable(host):
        return True
    return registrable(host) in goal.lower() or host.lower() in goal.lower()
