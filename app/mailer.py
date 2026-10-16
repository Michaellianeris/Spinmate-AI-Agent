"""Accounts and the welcome email: logo, a greeting with the user's name and the PDF user guide.

Order: GMAIL_REFRESH_TOKEN set -> Gmail API over HTTPS; SMTP_HOST set -> SMTP (STARTTLS on 587, SSL on 465);
otherwise the SpinMate mail relay (relay/Code.gs, HTTPS, sends from the project's address with both PDFs), so a
fresh install needs no mail settings. MAIL_RELAY=off turns the relay off; then the email is written to
WORKSPACE/outbox/*.eml.
"""
import html
import json
import os
import re
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import simulations as S

WEB = S.REPO / "web"
LOGO = WEB / "spinmate-lockup.png"
GUIDE = WEB / "spinmate-guide.pdf"
TEMPLATE = WEB / "welcome-email.html"
# the project's mail relay (relay/Code.gs, a Google Apps Script web app); MAIL_RELAY=<url> or MAIL_RELAY=off overrides
RELAY_DEFAULT = ""
PRESENTATION = WEB / "spinmate-presentation.pdf"   # optional: attached and linked when present
ACCOUNTS = S.WORKSPACE / "accounts.json"
OUTBOX = S.WORKSPACE / "outbox"
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
RESEND_AFTER_S = 3600          # one welcome email per address per hour
_lock = threading.Lock()


def smtp_settings():
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return None
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER", "").strip()
    return {"host": host, "port": port, "user": user, "password": os.environ.get("SMTP_PASSWORD", ""),
            "sender": os.environ.get("SMTP_FROM", "").strip() or user, "tls": port != 465}


def gmail_settings():
    """Gmail API credentials from an OAuth client and a refresh token (see app/gmail_auth.py)."""
    token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    if not token:
        return None
    return {"client_id": os.environ.get("GMAIL_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("GMAIL_CLIENT_SECRET", "").strip(), "refresh_token": token}


def relay_url():
    v = os.environ.get("MAIL_RELAY", "").strip()
    if v.lower() in ("off", "0", "false", "no"):
        return None
    return v or RELAY_DEFAULT or None


def mail_enabled():
    return bool(gmail_settings() or smtp_settings() or relay_url())


def relay_send(name, email, app_url):
    """Asks the SpinMate mail relay to send the welcome email. Only the name and the address leave this computer."""
    req = urllib.request.Request(relay_url(), json.dumps({"name": name, "email": email, "app_url": app_url}).encode(),
                                 {"Content-Type": "application/json"})
    try:                                          # Apps Script answers through a redirect, which urllib follows
        with urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context()) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise OSError(f"mail relay {e.code}") from None
    except ValueError:
        raise OSError("mail relay gave an unexpected answer") from None
    if not out.get("ok"):
        raise OSError(out.get("error") or "mail relay refused the request")
    return "relay"


def sender_address():
    return (os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip()
            or "spinmate@localhost")


def _gmail_send(msg, cfg):
    """Sends the whole MIME message through the Gmail API (HTTPS only)."""
    ctx = ssl.create_default_context()
    body = urllib.parse.urlencode({"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body),
                                    timeout=20, context=ctx) as r:
            token = json.loads(r.read())["access_token"]
        req = urllib.request.Request(
            "https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=media", bytes(msg),
            {"Authorization": f"Bearer {token}", "Content-Type": "message/rfc822"})
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return json.loads(r.read()).get("id", "")
    except urllib.error.HTTPError as e:            # keep Google's reason, e.g. invalid_grant
        raise OSError(f"Gmail API {e.code}: {e.read().decode(errors='replace')[:300]}") from None


def valid_email(email):
    return bool(EMAIL_RE.match(email or ""))


def _load():
    try:
        return json.loads(ACCOUNTS.read_text())
    except (OSError, ValueError):
        return {}


def attached():
    """One sentence naming the attachments."""
    if PRESENTATION.exists():
        return "The user guide and the SpinMate presentation are attached as PDFs"
    return "The user guide is attached as a PDF"


def welcome_html(name, app_url):
    """The welcome email from web/welcome-email.html (the mail relay uses the same file)."""
    text = TEMPLATE.read_text()
    if text.startswith("<!--"):                   # drop the note at the top of the file
        text = text.split("-->", 1)[1].lstrip()
    return (text.replace("{{name}}", html.escape(name)).replace("{{app_url}}", html.escape(app_url))
            .replace("{{attached}}", attached()))


def welcome_message(name, email, app_url="http://localhost:8421"):
    msg = EmailMessage()
    msg["Subject"] = f"Welcome to SpinMate, {name}"
    msg["From"] = formataddr(("SpinMate", sender_address()))
    msg["To"] = formataddr((name, email))
    msg["Message-ID"] = make_msgid(domain="spinmate.local")
    msg.set_content(f"Welcome to SpinMate, {name}!\n\nYour account is ready. {attached()}.\n"
                    f"Open the app: {app_url}\n")
    msg.add_alternative(welcome_html(name, app_url), subtype="html")
    if LOGO.exists():
        # no filename: Gmail then shows the logo only in the body, not as an attachment chip
        msg.get_payload()[1].add_related(LOGO.read_bytes(), "image", "png", cid="<spinmate-logo>")
    if GUIDE.exists():
        msg.add_attachment(GUIDE.read_bytes(), maintype="application", subtype="pdf", filename="SpinMate-User-Guide.pdf")
    if PRESENTATION.exists():
        msg.add_attachment(PRESENTATION.read_bytes(), maintype="application", subtype="pdf", filename="SpinMate-Presentation.pdf")
    return msg


def deliver(msg):
    """Send through the Gmail API or SMTP, or save to the outbox. Returns ("gmail" | "smtp" | "outbox", detail)."""
    gmail = gmail_settings()
    if gmail:
        return "gmail", _gmail_send(msg, gmail)
    cfg = smtp_settings()
    if not cfg:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path = OUTBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9]+', '_', msg['To'])[:40]}.eml"
        path.write_bytes(bytes(msg))
        return "outbox", str(path)
    ctx = ssl.create_default_context()
    if cfg["tls"]:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
            s.starttls(context=ctx)
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20, context=ctx) as s:
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    return "smtp", cfg["host"]


def create_account(name, email, app_url="http://localhost:8421"):
    email = (email or "").strip()
    if not valid_email(email):
        return {"ok": False, "error": "That does not look like an email address."}
    key = email.lower()
    with _lock:
        accounts = _load()
        prev = accounts.get(key) or {}
        recent = time.time() - prev.get("welcome_sent", 0) < RESEND_AFTER_S
        accounts[key] = {"name": name, "email": email, "created": prev.get("created", time.time()),
                         "welcome_sent": prev.get("welcome_sent", 0)}
        ACCOUNTS.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS.write_text(json.dumps(accounts, indent=1))
    if recent:
        return {"ok": True, "sent": False, "mode": "skipped", "message": "A welcome email was sent to this address recently."}
    try:
        if not (gmail_settings() or smtp_settings()) and relay_url():
            mode, detail = "relay", relay_send(name, email, app_url)
        else:
            mode, detail = deliver(welcome_message(name, email, app_url))
    except (smtplib.SMTPException, OSError) as e:
        return {"ok": True, "sent": False, "mode": "error", "message": f"The account is saved, but the email could not be sent: {e}"}
    sent = mode in ("gmail", "smtp", "relay")  # an outbox copy does not count as sent
    if sent:
        with _lock:
            accounts = _load()
            accounts[key]["welcome_sent"] = time.time()
            ACCOUNTS.write_text(json.dumps(accounts, indent=1))
    return {"ok": True, "sent": sent, "mode": mode,
            "message": "Welcome email sent." if sent else "No mail server is set up, so no email was sent."}
