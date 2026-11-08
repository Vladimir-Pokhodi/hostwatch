"""Scrubbing of recorded AdminVPS pages for test fixtures (`hostwatch record`).

The core replaces secrets, emails, IPs and ids it knows. A WHMCS page also holds things
only this plugin can spot: service ids in links, the SolusVM server id in a script,
hostnames, sums and the CSRF form token. Scripts and styles are dropped altogether
(chat widgets put the client's name and hashes there), except the one the plugin reads.
"""

from __future__ import annotations

import re

from hostwatch.core.record import MASK, Scrubber

# Whole blocks the plugin never reads.
SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
DROPPED = (
    re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<svg\b[^>]*>.*?</svg\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<noscript\b[^>]*>.*?</noscript\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<(?:link|meta)\b[^>]*>", re.IGNORECASE),
)
KEPT_SCRIPT = "vserverid"
BLANK_LINES = re.compile(r"\n\s*\n+")
SPACES = re.compile(r"[ \t]+")
INDENT = re.compile(r"\n ")
# `token` of WHMCS forms; any other value that looks like a session or form hash.
TOKEN_INPUT = re.compile(r'(name=["\']token["\'][^>]*?value=["\'])[^"\']*', re.IGNORECASE)
TOKEN_VALUE = re.compile(r'(value=["\'])[^"\']*(["\'][^>]*?name=["\']token["\'])', re.IGNORECASE)
HEX_HASH = re.compile(r"\b[0-9a-f]{32,}\b")
VUE_ID = re.compile(r"\bmc_[0-9a-z]{16,}\b")
# Ids: `productdetails&amp;id=731904`, `vserviceid=731904`, `var vserverid = 642511;`.
LINK_ID = re.compile(r"(?i)(?<![a-z])(?:id|serviceid|vserviceid|invoiceid)=(\d+)")
VSERVER_ID = re.compile(r"\bvserverid\s*=\s*['\"]?(\d+)")
# The hostname a service was ordered with, under the product in the services list:
# `<strong>Product</strong><br/><a href="http://host" target="_blank">host</a>`.
HOSTNAME = re.compile(
    r'</strong>\s*<br\s*/?>\s*<a href="https?://([^"/]+)/?"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
# Sums in rubles and DataTables sort keys of prices.
RUBLES = re.compile(r"(\d[\d ]*(?:[.,]\d{1,2})?)(\s*(?:руб|₽))")
SORT_KEY = re.compile(r'(data-order=")(\d+(?:\.\d+)?)(")')


def scrub_page(text: str, scrubber: Scrubber) -> str:
    """Runs on every recorded answer before the core's scrubbing; JSON passes through."""
    if "<html" not in text[:5000].lower():
        return text
    text = SCRIPT.sub(lambda m: m.group() if KEPT_SCRIPT in m.group() else "", text)
    for pattern in DROPPED:
        text = pattern.sub("", text)
    # Indentation is a third of a WHMCS page; HTML does not need it.
    text = INDENT.sub("\n", SPACES.sub(" ", text))
    text = BLANK_LINES.sub("\n", text)
    text = TOKEN_INPUT.sub(lambda m: m.group(1) + MASK, text)
    text = TOKEN_VALUE.sub(lambda m: m.group(1) + MASK + m.group(2), text)
    text = HEX_HASH.sub(MASK, text)
    text = VUE_ID.sub("mc_0000000000000000", text)
    for found in HOSTNAME.finditer(text):
        for name in {found.group(1), found.group(2)}:
            scrubber.label("hostname", name)
    for pattern in (LINK_ID, VSERVER_ID):
        for found in pattern.finditer(text):
            scrubber.fake_id(found.group(1))
    text = RUBLES.sub(lambda m: f"{_money(scrubber, m.group(1))}{m.group(2)}", text)
    return SORT_KEY.sub(lambda m: m.group(1) + _money(scrubber, m.group(2)) + m.group(3), text)


def _money(scrubber: Scrubber, real: str) -> str:
    normal = real.replace(" ", "").replace(",", ".")
    return str(scrubber.fake_money(normal))
