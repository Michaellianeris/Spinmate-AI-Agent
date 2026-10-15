"""Daily USD -> EUR rate for the cost display, from the ECB reference rates (published once per working day).

Order: EUR_PER_USD in the environment (fixed), else today's ECB rate, else the last rate saved in
WORKSPACE/fx.json, else 0.89. The rate is fetched at most once per day, in the background.
"""
import json
import os
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

from . import simulations as S

ECB_URL = os.environ.get("FX_URL", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml")
FALLBACK = 0.89
CACHE = S.WORKSPACE / "fx.json"
_state = {"rate": None, "date": None, "source": None, "checked": 0.0}
_lock = threading.Lock()


def _fixed():
    try:
        return float(os.environ["EUR_PER_USD"]) if os.environ.get("EUR_PER_USD") else None
    except ValueError:
        return None


def fetch_ecb(timeout=8):
    """(EUR per USD, reference date) from the ECB daily XML."""
    with urllib.request.urlopen(ECB_URL, timeout=timeout, context=ssl.create_default_context()) as r:
        root = ET.fromstring(r.read())
    for cube in root.iter():
        if cube.get("currency") == "USD":
            day = next((c.get("time") for c in root.iter() if c.get("time")), None)
            return 1.0 / float(cube.get("rate")), day          # ECB quotes USD per 1 EUR
    raise ValueError("no USD rate in the ECB file")


def _load_cache():
    try:
        d = json.loads(CACHE.read_text())
        _state.update(rate=float(d["rate"]), date=d.get("date"), source="ECB (saved)", checked=float(d.get("checked", 0)))
    except (OSError, ValueError, KeyError, TypeError):
        pass


def refresh(force=False):
    """Fetch the ECB rate if the last check is older than 12 hours. Never raises."""
    with _lock:
        if _state["rate"] is None:
            _load_cache()
        if not force and time.time() - _state["checked"] < 12 * 3600:
            return
        try:
            rate, day = fetch_ecb()
            _state.update(rate=rate, date=day, source="ECB", checked=time.time())
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"rate": rate, "date": day, "checked": _state["checked"]}))
        except Exception:
            _state["checked"] = time.time() - 11 * 3600     # offline or blocked: try again in an hour


def eur_per_usd():
    fixed = _fixed()
    if fixed:
        return fixed
    if _state["rate"] is None or time.time() - _state["checked"] > 12 * 3600:
        threading.Thread(target=refresh, daemon=True).start()
    return _state["rate"] or FALLBACK


def info():
    fixed = _fixed()
    if fixed:
        return {"eur_per_usd": fixed, "fx_date": None, "fx_source": "EUR_PER_USD (fixed)"}
    return {"eur_per_usd": eur_per_usd(), "fx_date": _state["date"],
            "fx_source": _state["source"] or "default (ECB not reached yet)"}
