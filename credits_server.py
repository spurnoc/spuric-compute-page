"""
Compute Credits backend — serves the landing pages and sends confirmation emails.

Stdlib only (http.server + smtplib + email.mime). No pip install needed.

USAGE:
  Option A — Resend API (free, 100/day, no SMTP needed):
    1. Sign up at https://resend.com (Google/GitHub login, 2 minutes)
    2. Get your API key from https://resend.com/api-keys
    3. Set env vars and run:
       RESEND_API_KEY=re_xxxxx  RESEND_FROM=credits@spuric.com  python credits_server.py

  Option B — Gmail App Password (free, 500/day):
    1. Go to https://myaccount.google.com/apppasswords
    2. Create an app password
    3. Set env vars and run:
       SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USER=you@gmail.com  SMTP_PASS=****  SMTP_FROM=you@gmail.com  python credits_server.py

  Option C — Brevo/Sendinblue (free, 300/day):
    1. Sign up at https://brevo.com
    2. Get SMTP credentials from Settings > SMTP & API
    3. Set env vars and run:
       SMTP_HOST=smtp-relay.brevo.com  SMTP_PORT=587  SMTP_USER=you@spuric.com  SMTP_PASS=****  SMTP_FROM=credits@spuric.com  python credits_server.py

  Then open: http://localhost:8090/v1  or  http://localhost:8090/v2

ENVIRONMENT VARIABLES:
  RESEND_API_KEY  — Resend API key (starts with re_). Uses HTTP API, no SMTP needed.
  RESEND_FROM     — sender address for Resend, e.g. credits@spuric.com

  SMTP_HOST   — e.g. smtp.gmail.com, smtp-relay.brevo.com
  SMTP_PORT   — 587 (STARTTLS) or 465 (SSL). Default: 587
  SMTP_USER   — login username (usually the sender email)
  SMTP_PASS   — password or API key
  SMTP_FROM   — sender address, e.g. credits@spuric.com
  SMTP_SSL    — "1" for SSL (port 465), "0" for STARTTLS (port 587). Default: 0
  BASE_URL    — public URL where the pages are served. Default: http://localhost:8090
"""

import os
import json
import smtplib
import ssl
import re
import html
import uuid
from datetime import datetime, timezone
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse, urlencode

# ── Config ──────────────────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8090"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_USE_SSL = os.getenv("SMTP_SSL", "0") == "1"
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")

# Resend API config (Option A — no SMTP needed)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "SPUR Compute <onboarding@resend.dev>")
RESEND_API_URL = "https://api.resend.com/emails"

FOMO_PASSWORD = os.getenv("FOMO_PASSWORD", "spur2026")

PAGES_DIR = os.path.dirname(os.path.abspath(__file__))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "spurnoc")

# ── Cloudflare Turnstile (free bot protection) ───────────────────────
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")  # set to enable
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")  # set to enable
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# ── Turso (libSQL) database — primary ────────────────────────────────
TURSO_URL = os.getenv("TURSO_URL", "")
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "")
TURSO_API_URL = f"{TURSO_URL}/v2/pipeline" if TURSO_URL else ""

# ── Cloudflare D1 — backup ────────────────────────────────────────
CF_ACCOUNT = os.getenv("CF_ACCOUNT", "")
CF_DB = os.getenv("CF_DB", "")
CF_TOKEN = os.getenv("CF_TOKEN", "")
CF_API_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/d1/database/{CF_DB}/query" if CF_ACCOUNT and CF_DB else ""

# Track which DB last served a read (for admin display)
DB_ACTIVE = "turso"

import threading
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=4)


def _turso_execute(sql, args=None):
    """Execute SQL on Turso. Returns raw response dict or None on failure."""
    if not TURSO_API_URL:
        return None
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    payload = json.dumps({"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}).encode("utf-8")
    req = urllib.request.Request(
        TURSO_API_URL, data=payload,
        headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cf_execute(sql, args=None):
    """Execute SQL on Cloudflare D1. Returns raw response dict or None on failure."""
    if not CF_API_URL:
        return None
    body = {"sql": sql}
    if args:
        body["params"] = [str(a) for a in args]
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        CF_API_URL, data=payload,
        headers={"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_turso(data):
    """Parse Turso response into (ok, cols, rows)."""
    results = data.get("results", [])
    if not results or results[0].get("type") != "ok":
        return False, [], []
    result = results[0].get("response", {}).get("result", {})
    cols = [c["name"] for c in result.get("cols", [])]
    rows = []
    for row in result.get("rows", []):
        vals = []
        for cell in row:
            if cell is None:
                vals.append(None)
            elif isinstance(cell, dict):
                vals.append(cell.get("value"))
            else:
                vals.append(cell)
        rows.append(dict(zip(cols, vals)))
    return True, cols, rows


def _normalize_d1(data):
    """Parse D1 response into (ok, cols, rows)."""
    if not data or not data.get("success"):
        return False, [], []
    result = data.get("result", [{}])
    if not result:
        return True, [], []
    results = result[0].get("results", [])
    if not results:
        return True, [], []
    cols = list(results[0].keys())
    rows = results
    return True, cols, rows


def db_write(sql, args=None):
    """Write to BOTH databases in parallel. Returns True if at least one succeeded."""
    def try_turso():
        try:
            _turso_execute(sql, args)
            return True
        except Exception as e:
            print(f"[TURSO] Write failed: {e}")
            return False

    def try_d1():
        try:
            _cf_execute(sql, args)
            return True
        except Exception as e:
            print(f"[D1] Write failed: {e}")
            return False

    f_turso = _pool.submit(try_turso)
    f_d1 = _pool.submit(try_d1)

    ok_turso = f_turso.result()
    ok_d1 = f_d1.result()

    if ok_turso and ok_d1:
        print(f"[DB] Write succeeded on both")
    elif ok_turso:
        print(f"[DB] Write succeeded on Turso only")
    elif ok_d1:
        print(f"[DB] Write succeeded on D1 only")
    else:
        print(f"[DB] Write FAILED on both")

    return ok_turso or ok_d1


def db_query(sql, args=None):
    """Read from whichever database responds first. Falls back to the other if one fails."""
    global DB_ACTIVE

    def try_turso():
        data = _turso_execute(sql, args)
        if data is None:
            return None
        ok, cols, rows = _normalize_turso(data)
        return ("turso", ok, cols, rows)

    def try_d1():
        data = _cf_execute(sql, args)
        if data is None:
            return None
        ok, cols, rows = _normalize_d1(data)
        return ("d1", ok, cols, rows)

    f_turso = _pool.submit(try_turso)
    f_d1 = _pool.submit(try_d1)

    # Wait for whichever finishes first
    import time
    done = []
    pending = [f_turso, f_d1]
    while pending:
        # Check if any completed
        for f in list(pending):
            if f.done():
                result = f.result()
                if result and result[1]:  # ok=True
                    DB_ACTIVE = result[0]
                    return result[2], result[3]  # cols, rows
                done.append(result)
                pending.remove(f)

        if not pending:
            break
        time.sleep(0.001)  # avoid CPU spin

    # If we get here, neither returned ok=True. Return whatever we got.
    for d in done:
        if d:
            return d[2], d[3]
    return [], []


# Keep turso_execute/turso_query as wrappers for backwards compat
def turso_execute(sql, args=None):
    """Wrapper: writes go to both, reads use db_query."""
    return db_write(sql, args)

def turso_query(sql, args=None):
    """Wrapper: reads race both databases, returns rows as list of dicts."""
    cols, rows = db_query(sql, args)
    return rows


def verify_turnstile(token):
    """Verify a Cloudflare Turnstile token. Returns True if valid or not configured."""
    if not TURNSTILE_SECRET_KEY:
        return True  # Turnstile not configured — allow through
    if not token:
        return False
    try:
        data = urlencode({"secret": TURNSTILE_SECRET_KEY, "response": token}).encode()
        req = urllib.request.Request(TURNSTILE_VERIFY_URL, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return result.get("success", False)
    except Exception as e:
        print(f"[TURNSTILE] Verification error: {e}")
        return False


# ── Database-backed data functions ───────────────────────────────────
def load_submissions():
    return turso_query("SELECT * FROM submissions ORDER BY created_at DESC")

def add_submission(sub):
    db_write(
        "INSERT INTO submissions (name, email, organization, description, track, claim_code, form_version, status, ip, user_agent, created_at, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [sub.get("name",""), sub.get("email",""), sub.get("organization",""), sub.get("description",""),
         sub.get("track",""), sub.get("claim_code",""), sub.get("form_version","v3"), sub.get("status","pending"),
         sub.get("ip",""), sub.get("user_agent",""), sub.get("submitted_at",""), sub.get("source","v3")]
    )

def update_submission_status(sub_id, status):
    turso_execute("UPDATE submissions SET status=? WHERE id=?", [status, sub_id])
    rows = turso_query("SELECT * FROM submissions WHERE id=?", [sub_id])
    return rows[0] if rows else None

def load_claim_codes():
    return turso_query("SELECT code, track, credits, label FROM claim_codes ORDER BY id")

def save_claim_codes(codes):
    # Replace all codes (used by admin mutate)
    turso_execute("DELETE FROM claim_codes")
    for c in codes:
        turso_execute(
            "INSERT INTO claim_codes (code, track, credits, label) VALUES (?,?,?,?)",
            [c["code"], c["track"], c["credits"], c.get("label","")]
        )

def load_testimonials():
    return turso_query("SELECT id, quote, name, role, company, approved FROM testimonials WHERE approved=1 ORDER BY id")

def load_events():
    return turso_query("SELECT event_type, page, section, cta, visitor_id, meta, created_at FROM analytics_events ORDER BY created_at DESC LIMIT 5000")

def add_early_access(email, source="coming_soon"):
    turso_execute(
        "INSERT OR IGNORE INTO early_access (email, source, created_at) VALUES (?, ?, ?)",
        [email, source, datetime.now(timezone.utc).isoformat()]
    )

def add_event(event):
    import json as _json
    meta = _json.dumps(event.get("data", {}))
    turso_execute(
        "INSERT INTO analytics_events (event_type, page, section, cta, visitor_id, meta, created_at) VALUES (?,?,?,?,?,?,?)",
        [event.get("type",""), event.get("page",""), event.get("section",""), event.get("cta",""),
         event.get("visitor_id",""), meta, event.get("timestamp","")]
    )

TRACK_LABELS = {
    "founder": "Founder / Startup",
    "student": "Student / Researcher",
    "event": "Event Participant",
}

TRACK_CREDITS = {
    "founder": "$5,000",
    "student": "$500",
    "event": "$1,000",
}

EMAIL_SUBJECT = "Your SPUR Compute Credits request has been received"

EMAIL_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" style="width:100%;max-width:560px;margin:0 auto;padding:32px 16px;">
    <tr>
      <td style="background:#ffffff;border-radius:8px;border:1px solid #e5e5e5;padding:32px;">

        <div style="display:flex;align-items:center;gap:8px;margin-bottom:24px;">
          <div style="width:28px;height:28px;border-radius:6px;background:#F87820;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;">S</div>
          <span style="font-weight:700;font-size:15px;color:#1a1a1a;">SPUR Innovation</span>
        </div>

        <h1 style="font-size:22px;font-weight:600;color:#1a1a1a;margin:0 0 12px;">Your compute credits request has been received</h1>

        <p style="font-size:16px;line-height:1.6;color:#444;margin:0 0 16px;">
          Hi {name},
        </p>

        <p style="font-size:16px;line-height:1.6;color:#444;margin:0 0 16px;">
          We received your request for <strong>{track}</strong> compute credits on SPUR's sovereign Canadian infrastructure.
          Here is a summary of your request:
        </p>

        <table role="presentation" style="width:100%;border-collapse:collapse;margin:0 0 20px;font-size:14px;">
          <tr><td style="padding:6px 0;color:#888;border-bottom:1px solid #eee;">Name</td><td style="padding:6px 0;color:#1a1a1a;border-bottom:1px solid #eee;text-align:right;font-weight:500;">{name}</td></tr>
          <tr><td style="padding:6px 0;color:#888;border-bottom:1px solid #eee;">Track</td><td style="padding:6px 0;color:#1a1a1a;border-bottom:1px solid #eee;text-align:right;font-weight:500;">{track}</td></tr>
          <tr><td style="padding:6px 0;color:#888;border-bottom:1px solid #eee;">Credit allocation</td><td style="padding:6px 0;color:#F87820;border-bottom:1px solid #eee;text-align:right;font-weight:700;">{credits}</td></tr>
          <tr><td style="padding:6px 0;color:#888;border-bottom:1px solid #eee;">Use case</td><td style="padding:6px 0;color:#1a1a1a;border-bottom:1px solid #eee;text-align:right;font-weight:500;">{use_case}</td></tr>
          <tr><td style="padding:6px 0;color:#888;border-bottom:1px solid #eee;">GPU preference</td><td style="padding:6px 0;color:#1a1a1a;border-bottom:1px solid #eee;text-align:right;font-weight:500;">{gpu}</td></tr>
          <tr><td style="padding:6px 0;color:#888;">Duration</td><td style="padding:6px 0;color:#1a1a1a;text-align:right;font-weight:500;">{duration}</td></tr>
        </table>

        <div style="background:#f0f7ff;border:1px solid #cfe3ff;border-radius:6px;padding:14px 16px;margin:0 0 20px;">
          <p style="margin:0;font-size:14px;line-height:1.5;color:#1a4d8f;">
            <strong>What happens next?</strong><br>
            Our team will review your request and activate your credits within one business day.
            You will receive a follow-up email with your SPUR console access and onboarding instructions.
          </p>
        </div>

        <div style="background:#f5f5f5;border-radius:6px;padding:14px 16px;margin:0 0 20px;">
          <p style="margin:0;font-size:13px;line-height:1.5;color:#666;">
            <strong>Your project:</strong><br>
            {description}
          </p>
        </div>

        <p style="font-size:14px;line-height:1.6;color:#888;margin:0 0 8px;">
          Questions? Reply to this email or contact us at
          <a href="mailto:credits@spuric.com" style="color:#F87820;">credits@spuric.com</a>
        </p>

        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="font-size:12px;line-height:1.5;color:#aaa;margin:0;">
          SPUR Innovation Centre &middot; Waterloo, Ontario, Canada<br>
          Hosted in Canada &middot; No US Cloud Act exposure &middot; PIPEDA-aligned<br>
          &copy; 2026 SPUR Innovation Centre
        </p>

      </td>
    </tr>
  </table>
</body>
</html>
"""

EMAIL_TEXT_TEMPLATE = """\
SPUR Innovation — Compute Credits Request Received

Hi {name},

We received your request for {track} compute credits on SPUR's sovereign
Canadian infrastructure.

Summary:
  Name:        {name}
  Track:       {track}
  Credits:     {credits}
  Use case:    {use_case}
  GPU:         {gpu}
  Duration:    {duration}

Your project:
  {description}

What happens next?
  Our team will review your request and activate your credits within one
  business day. You will receive a follow-up email with your SPUR console
  access and onboarding instructions.

Questions? Reply to this email or contact credits@spuric.com

SPUR Innovation Centre · Waterloo, Ontario, Canada
Hosted in Canada · No US Cloud Act exposure · PIPEDA-aligned
© 2026 SPUR Innovation Centre
"""


def _build_email_content(recipient_name, track, use_case, gpu_pref, duration, description):
    """Build the email subject, HTML body, and text body. Returns (subject, html, text)."""
    track_label = TRACK_LABELS.get(track, track)
    credits = TRACK_CREDITS.get(track, "TBD")

    duration_display = {
        "week": "1 week", "month": "1 month",
        "3_months": "3 months", "ongoing": "Ongoing"
    }.get(duration, duration)

    use_case_display = use_case.replace("_", " ").title() if use_case else "Not specified"
    gpu_display = {
        "b300": "NVIDIA B300", "h100": "NVIDIA H100",
        "no_preference": "No preference"
    }.get(gpu_pref, gpu_pref)

    desc_display = description[:500] + "..." if len(description) > 500 else description

    fmt_args = {
        "name": html.escape(recipient_name),
        "track": track_label,
        "credits": credits,
        "use_case": use_case_display,
        "gpu": gpu_display,
        "duration": duration_display,
        "description": html.escape(desc_display),
    }

    html_body = EMAIL_HTML_TEMPLATE.format(**fmt_args)
    text_body = EMAIL_TEXT_TEMPLATE.format(
        name=recipient_name,
        track=track_label,
        credits=credits,
        use_case=use_case_display,
        gpu=gpu_display,
        duration=duration_display,
        description=desc_display,
    )
    return EMAIL_SUBJECT, html_body, text_body


def _send_via_resend(recipient_email, subject, html_body, text_body):
    """Send email via Resend HTTP API. Returns True on success."""
    payload = json.dumps({
        "from": RESEND_FROM,
        "to": [recipient_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
        "reply_to": RESEND_FROM,
    }).encode("utf-8")

    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "SPUR-Compute-Credits/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            if resp.status == 200:
                print(f"[RESEND] Email sent to {recipient_email} (id={json.loads(body).get('id', '?')})")
                return True
            else:
                print(f"[RESEND] Error {resp.status}: {body}")
                return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"[RESEND] HTTP {e.code}: {err_body}")
        return False
    except Exception as e:
        print(f"[RESEND] Error: {e}")
        return False


def _send_via_smtp(recipient_email, subject, html_body, text_body):
    """Send email via SMTP. Returns True on success."""
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"SPUR Innovation <{SMTP_FROM}>"
    msg["To"] = recipient_email
    msg["Reply-To"] = SMTP_FROM

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        if SMTP_USE_SSL:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, recipient_email, msg.as_string())
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls(context=context)
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, recipient_email, msg.as_string())

        print(f"[SMTP] Confirmation email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"[SMTP] Error sending email to {recipient_email}: {e}")
        return False


def send_confirmation_email(recipient_email: str, recipient_name: str,
                             track: str, use_case: str, gpu_pref: str,
                             duration: str, description: str) -> bool:
    """Send a confirmation email. Tries Resend API first, then SMTP. Returns True on success."""

    subject, html_body, text_body = _build_email_content(
        recipient_name, track, use_case, gpu_pref, duration, description
    )

    # Option A: Resend API
    if RESEND_API_KEY:
        return _send_via_resend(recipient_email, subject, html_body, text_body)

    # Option B/C: SMTP (Gmail, Brevo, etc.)
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        return _send_via_smtp(recipient_email, subject, html_body, text_body)

    print(f"[EMAIL] No email provider configured — skipping email to {recipient_email}")
    print(f"[EMAIL] Set RESEND_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASS to enable")
    return False


class CreditsHandler(BaseHTTPRequestHandler):
    """HTTP handler: serves HTML pages and handles form submissions."""

    def log_message(self, format, *args):
        # Simplified logging
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/v1", "/v1/") or path == "/compute-credits-v1.html":
            self._serve_html("v1.html")
        elif path in ("/v2", "/v2/") or path == "/compute-credits-v2.html":
            self._serve_html("v2.html")
        elif path in ("/v3", "/v3/") or path in ("/compute/v3", "/compute/v3/"):
            self._serve_html("v3.html")
        elif path in ("/fomo", "/fomo/") or path in ("/compute/fomo", "/compute/fomo/"):
            self._serve_html("fomo.html")
        elif path in ("/admin", "/admin/") or path in ("/compute/admin", "/compute/admin/"):
            self._serve_html("admin.html")
        elif path in ("/careers/v1", "/careers/v1/"):
            self._serve_html("careers-v1.html")
        elif path in ("/careers/v2", "/careers/v2/"):
            self._serve_html("careers-v2.html")
        elif path in ("/careers/admin", "/careers/admin/"):
            self._serve_html("careers-admin.html")
        elif path == "/" or path == "":
            # Default: serve V3
            self._serve_html("v3.html")
        elif path == "/health":
            email_ready = bool(RESEND_API_KEY or (SMTP_HOST and SMTP_USER and SMTP_PASS))
            provider = "resend" if RESEND_API_KEY else ("smtp" if SMTP_HOST else "none")
            self._json_response({"status": "ok", "email_configured": email_ready, "provider": provider, "db": DB_ACTIVE})
        elif path == "/api/admin/submissions":
            self._handle_admin_submissions()
        elif path == "/api/admin/claim-codes":
            self._handle_admin_claim_codes_get()
        elif path == "/api/admin/analytics":
            self._handle_admin_analytics()
        elif path == "/api/fomo/feed":
            self._handle_fomo_feed()
        elif path == "/api/testimonials":
            self._handle_testimonials()
        elif path == "/api/admin/early-access":
            self._handle_admin_early_access()
        elif path == "/api/track":
            self._handle_track()
        elif path == "/api/turnstile-site-key":
            self._json_response({"enabled": bool(TURNSTILE_SITE_KEY), "site_key": TURNSTILE_SITE_KEY})
        elif path.endswith((".webp", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".ico")):
            self._serve_static(path)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/submit":
            self._handle_submission()
        elif path == "/api/claim":
            self._handle_claim()
        elif path == "/api/track":
            self._handle_track()
        elif path == "/api/fomo/auth":
            self._handle_fomo_auth()
        elif path.startswith("/api/admin/submissions/") and path.endswith("/status"):
            self._handle_admin_update_status(path)
        elif path == "/api/admin/claim-codes":
            self._handle_admin_claim_codes_mutate("create")
        elif path == "/api/admin/claim-codes/update":
            self._handle_admin_claim_codes_mutate("update")
        elif path == "/api/admin/claim-codes/delete":
            self._handle_admin_claim_codes_mutate("delete")
        else:
            self._json_response({"error": "Not found"}, 404)

    def _serve_html(self, filename):
        filepath = os.path.join(PAGES_DIR, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except FileNotFoundError:
            self._json_response({"error": f"{filename} not found"}, 404)

    def _serve_static(self, path):
        """Serve static files (images, etc.) from PAGES_DIR."""
        # Strip query string and path traversal
        filename = os.path.basename(path.split("?")[0])
        filepath = os.path.join(PAGES_DIR, filename)
        if not os.path.isfile(filepath):
            self._json_response({"error": "Not found"}, 404)
            return

        ext = os.path.splitext(filename)[1].lower()
        content_types = {
            ".webp": "image/webp", ".png": "image/png",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml", ".gif": "image/gif",
            ".ico": "image/x-icon",
        }
        ct = content_types.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _handle_submission(self):
        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        # Extract and validate fields — supports v2 (9-field) and v3 (4-field) forms
        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        track = (data.get("track") or "").strip()
        # v3 sends "org" instead of "organization", "build" instead of "description"
        organization = (data.get("organization") or data.get("org") or "").strip()
        description = (data.get("description") or data.get("build") or "").strip()
        role = (data.get("role") or "").strip()
        event_code = (data.get("event_code") or "").strip()
        use_case = (data.get("use_case") or "").strip()
        workload = (data.get("workload") or "").strip()
        gpu_pref = (data.get("gpu_pref") or data.get("gpu_preference") or "").strip()
        duration = (data.get("duration") or "").strip()
        credit_type = (data.get("credit_type") or "api_and_compute").strip()

        # Honeypot check
        if data.get("website"):
            self._json_response({"error": "Spam detected"}, 400)
            return

        # Turnstile bot protection (if configured)
        turnstile_token = data.get("cf_turnstile_response") or ""
        if not verify_turnstile(turnstile_token):
            self._json_response({"error": "Bot verification failed", "fields": {"turnstile": "Please complete the verification."}}, 403)
            return

        # Validate required fields (v3: only name, email, track, build)
        errors = {}
        if not name:
            errors["name"] = "Name is required"
        if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
            errors["email"] = "Valid email is required"
        if not track:
            errors["track"] = "Track is required"
        if not description:
            errors["build"] = "Project description is required"

        if errors:
            self._json_response({"error": "Validation failed", "fields": errors}, 422)
            return

        # Send the confirmation email
        email_sent = send_confirmation_email(
            recipient_email=email,
            recipient_name=name,
            track=track,
            use_case=use_case,
            gpu_pref=gpu_pref,
            duration=duration,
            description=description,
        )

        print(f"[SUBMIT] {name} <{email}> — track={track}, use_case={use_case}, gpu={gpu_pref}, email_sent={email_sent}")

        # If this is a "notify me" signup from the coming soon page, store separately
        if track == "notify":
            add_early_access(email, "coming_soon")
            self._json_response({
                "success": True,
                "message": f"Thanks! We'll notify you at {email} when credits go live.",
                "email_sent": False,
                "track": "notify",
            })
            return

        # Persist submission to Turso
        submission = {
            "name": name,
            "email": email,
            "track": track,
            "organization": organization,
            "description": description,
            "claim_code": "",
            "form_version": "v3" if "build" in data else "v2",
            "status": "pending",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "source": data.get("source", "v3"),
        }
        add_submission(submission)

        if track == "notify":
            msg = f"Thanks! We'll notify you at {email} when credits go live."
        else:
            msg = f"Thanks, {name}! We received your {TRACK_LABELS.get(track, track)} credit request. Check your email at {email} for confirmation."

        self._json_response({
            "success": True,
            "message": msg,
            "email_sent": email_sent,
            "track": track,
            "credits": TRACK_CREDITS.get(track, ""),
        })

    def _handle_claim(self):
        """Validate a claim code and return track/credit info."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        claim_code = (data.get("claim_code") or "").strip().upper()

        if not claim_code:
            self._json_response({"valid": False, "error": "Enter a claim code."}, 422)
            return

        # Valid claim codes loaded from claim_codes.json
        claim_codes = load_claim_codes()
        VALID_CODES = {c["code"].upper(): c for c in claim_codes}

        entry = VALID_CODES.get(claim_code)
        if not entry:
            self._json_response({"valid": False, "error": "Invalid or expired claim code."}, 200)
            return

        print(f"[CLAIM] Code validated: {claim_code} → {entry['track']}")
        self._json_response({
            "valid": True,
            "track": entry["track"],
            "credits": entry["credits"],
            "label": entry["label"],
            "claim_code": claim_code,
        })

    def _handle_track(self):
        """POST /api/track — receive analytics events from the frontend."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        event = {
            "type": (data.get("type") or "").strip(),
            "section": (data.get("section") or "").strip(),
            "data": data.get("data") or {},
            "page": (data.get("page") or "").strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if not event["type"]:
            self._json_response({"error": "Event type required"}, 422)
            return

        add_event(event)
        self._json_response({"success": True})

    def _handle_admin_analytics(self):
        """GET /api/admin/analytics — aggregated analytics for the admin dashboard."""
        token = self.headers.get("Authorization", "").replace("Bearer ", "").replace("Token ", "").strip()
        if token != ADMIN_TOKEN:
            self._json_response({"error": "Unauthorized"}, 401)
            return

        events = load_events()
        subs = load_submissions()

        # CTA clicks grouped by section
        cta_clicks = {}
        # Scroll depth reached
        scroll_depth = {25: 0, 50: 0, 75: 0, 100: 0}
        # Form submissions (conversion events)
        form_submits = 0
        # Page views
        page_views = {}
        # Daily breakdown of submissions
        daily_subs = {}
        # Track breakdown
        track_counts = {"founder": 0, "student": 0, "event": 0}
        # Status breakdown
        status_counts = {"pending": 0, "approved": 0, "rejected": 0}

        for e in events:
            etype = e.get("event_type", "")
            if etype == "cta_click":
                sec = e.get("section", "unknown")
                cta_clicks[sec] = cta_clicks.get(sec, 0) + 1
            elif etype == "scroll_depth":
                meta = e.get("meta", "")
                try:
                    meta_d = json.loads(meta) if isinstance(meta, str) else meta
                    depth = int(meta_d.get("depth", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    depth = 0
                for threshold in scroll_depth:
                    if depth >= threshold:
                        scroll_depth[threshold] += 1
            elif etype == "form_submit":
                form_submits += 1
            elif etype == "page_view":
                pg = e.get("page", "unknown")
                page_views[pg] = page_views.get(pg, 0) + 1

        for s in subs:
            date_str = (s.get("created_at") or s.get("submitted_at") or "")[:10]
            if date_str:
                daily_subs[date_str] = daily_subs.get(date_str, 0) + 1
            track = s.get("track", "")
            if track in track_counts:
                track_counts[track] += 1
            status = s.get("status", "pending")
            if status in status_counts:
                status_counts[status] += 1

        # Sort daily subs by date
        daily_sorted = sorted(daily_subs.items())

        self._json_response({
            "totals": {
                "events": len(events),
                "submissions": len(subs),
                "cta_clicks": sum(cta_clicks.values()),
                "form_submits": form_submits,
                "page_views": sum(page_views.values()),
            },
            "cta_clicks_by_section": cta_clicks,
            "scroll_depth": scroll_depth,
            "page_views": page_views,
            "daily_submissions": daily_sorted,
            "track_breakdown": track_counts,
            "status_breakdown": status_counts,
            "turnstile_enabled": bool(TURNSTILE_SECRET_KEY),
        })

    def _handle_fomo_auth(self):
        """POST /api/fomo/auth - validate FOMO page access password."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"ok": False}, 400)
            return
        password = (data.get("password") or "").strip()
        if password and password == FOMO_PASSWORD:
            self._json_response({"ok": True})
        else:
            self._json_response({"ok": False}, 403)

    def _handle_fomo_feed(self):
        """GET /api/fomo/feed — public, returns anonymized FOMO-page claims only."""
        subs = turso_query("SELECT name, track, organization, created_at FROM submissions WHERE source='fomo' ORDER BY created_at DESC LIMIT 20")
        # Anonymize: only initials, track, org, timestamp. No email, no full name.
        anon = []
        for s in subs:
            name = s.get("name", "")
            parts = name.split()
            initials = "".join([p[0] for p in parts if p])[:2].upper() or "?"
            anon.append({
                "name": initials + ".",
                "track": s.get("track", ""),
                "organization": s.get("organization", ""),
                "submitted_at": s.get("created_at", ""),
            })
        self._json_response({"submissions": anon})

    def _handle_testimonials(self):
        """GET /api/testimonials - returns approved testimonials."""
        testimonials = load_testimonials()
        # Convert approved integer to bool for JSON
        result = []
        for t in testimonials:
            result.append({
                "id": t.get("id"),
                "quote": t.get("quote"),
                "name": t.get("name"),
                "role": t.get("role"),
                "company": t.get("company"),
                "approved": bool(t.get("approved")),
            })
        self._json_response({"testimonials": result})

    def _handle_admin_early_access(self):
        """GET /api/admin/early-access — return all early access signups."""
        if not self._check_admin_token():
            self._json_response({"error": "Unauthorized"}, 401)
            return
        signups = turso_query("SELECT id, email, source, created_at FROM early_access ORDER BY created_at DESC")
        self._json_response({"early_access": signups})

    def _handle_admin_submissions(self):
        """GET /api/admin/submissions — return all submissions + claim codes."""
        token = self.headers.get("Authorization", "").replace("Bearer ", "").replace("Token ", "").strip()
        if token != ADMIN_TOKEN:
            self._json_response({"error": "Unauthorized"}, 401)
            return
        subs = load_submissions()
        self._json_response({"submissions": subs, "claim_codes": load_claim_codes()})

    def _handle_admin_update_status(self, path):
        """PUT/POST /api/admin/submissions/{id}/status — update submission status."""
        token = self.headers.get("Authorization", "").replace("Bearer ", "").replace("Token ", "").strip()
        if token != ADMIN_TOKEN:
            self._json_response({"error": "Unauthorized"}, 401)
            return

        # Extract submission ID from path: /api/admin/submissions/{id}/status
        parts = path.strip("/").split("/")
        if len(parts) < 4:
            self._json_response({"error": "Invalid path"}, 400)
            return
        try:
            sub_id = parts[3]
        except (IndexError, ValueError):
            self._json_response({"error": "Invalid submission ID"}, 400)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        status = (data.get("status") or "").strip().lower()
        if status not in ("pending", "approved", "rejected"):
            self._json_response({"error": "Status must be pending, approved, or rejected"}, 422)
            return

        updated = update_submission_status(sub_id, status)
        if updated:
            print(f"[ADMIN] Submission {sub_id} → {status}")
            self._json_response({"success": True, "submission": updated})
        else:
            self._json_response({"error": "Submission not found"}, 404)

    def _check_admin_token(self):
        token = self.headers.get("Authorization", "").replace("Bearer ", "").replace("Token ", "").strip()
        return token == ADMIN_TOKEN

    def _handle_admin_claim_codes_get(self):
        """GET /api/admin/claim-codes - list all claim codes."""
        if not self._check_admin_token():
            self._json_response({"error": "Unauthorized"}, 401)
            return
        codes = load_claim_codes()
        self._json_response({"claim_codes": codes})

    def _handle_admin_claim_codes_mutate(self, action):
        """POST /api/admin/claim-codes (create), /update, /delete."""
        if not self._check_admin_token():
            self._json_response({"error": "Unauthorized"}, 401)
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        if action == "create":
            code = (data.get("code") or "").strip().upper()
            track = (data.get("track") or "").strip().lower()
            credits = (data.get("credits") or "").strip()
            label = (data.get("label") or "").strip()
            if not code or not track:
                self._json_response({"error": "Code and track are required"}, 422)
                return
            existing = turso_query("SELECT code FROM claim_codes WHERE code=?", [code])
            if existing:
                self._json_response({"error": "Code already exists"}, 409)
                return
            if track not in TRACK_LABELS:
                self._json_response({"error": "Track must be founder, student, or event"}, 422)
                return
            entry = {"code": code, "track": track, "credits": credits or TRACK_CREDITS.get(track, ""), "label": label or TRACK_LABELS.get(track, "")}
            turso_execute("INSERT INTO claim_codes (code, track, credits, label) VALUES (?,?,?,?)", [code, track, entry["credits"], entry["label"]])
            print(f"[ADMIN] Claim code created: {code}")
            self._json_response({"success": True, "claim_code": entry})

        elif action == "update":
            code = (data.get("code") or "").strip().upper()
            if not code:
                self._json_response({"error": "Code is required"}, 422)
                return
            existing = turso_query("SELECT code FROM claim_codes WHERE code=?", [code])
            if not existing:
                self._json_response({"error": "Code not found"}, 404)
                return
            if data.get("track"):
                turso_execute("UPDATE claim_codes SET track=? WHERE code=?", [data["track"].strip().lower(), code])
            if data.get("credits"):
                turso_execute("UPDATE claim_codes SET credits=? WHERE code=?", [data["credits"].strip(), code])
            if data.get("label"):
                turso_execute("UPDATE claim_codes SET label=? WHERE code=?", [data["label"].strip(), code])
            codes = load_claim_codes()
            print(f"[ADMIN] Claim code updated: {code}")
            self._json_response({"success": True, "claim_codes": codes})

        elif action == "delete":
            code = (data.get("code") or "").strip().upper()
            if not code:
                self._json_response({"error": "Code is required"}, 422)
                return
            existing = turso_query("SELECT code FROM claim_codes WHERE code=?", [code])
            if not existing:
                self._json_response({"error": "Code not found"}, 404)
                return
            turso_execute("DELETE FROM claim_codes WHERE code=?", [code])
            codes = load_claim_codes()
            print(f"[ADMIN] Claim code deleted: {code}")
            self._json_response({"success": True, "claim_codes": codes})

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    # Print startup info
    email_configured = bool(RESEND_API_KEY or (SMTP_HOST and SMTP_USER and SMTP_PASS))
    if RESEND_API_KEY:
        provider = f"Resend API (from: {RESEND_FROM})"
    elif SMTP_HOST:
        provider = f"SMTP {SMTP_HOST}:{SMTP_PORT} ({'SSL' if SMTP_USE_SSL else 'STARTTLS'}) from: {SMTP_FROM}"
    else:
        provider = "NOT CONFIGURED"

    print("=" * 60)
    print("  SPUR Compute Credits Server")
    print("=" * 60)
    print(f"  Pages:")
    print(f"    http://localhost:{PORT}/v1  (spuric.com match)")
    print(f"    http://localhost:{PORT}/v2  (own design system)")
    print(f"    http://localhost:{PORT}/v3  (v3 — claim flow, final copy)")
    print(f"    http://localhost:{PORT}/fomo (FOMO — scarcity + live feed)")
    print(f"    http://localhost:{PORT}/admin (admin dashboard)")
    print(f"    http://localhost:{PORT}/health")
    print(f"  API:  POST http://localhost:{PORT}/api/submit")
    print()
    print(f"  Email: {provider}")
    if not email_configured:
        print(f"  Free options:")
        print(f"    Resend:  RESEND_API_KEY=re_xxx  RESEND_FROM=credits@spuric.com  python credits_server.py")
        print(f"    Gmail:   SMTP_HOST=smtp.gmail.com  SMTP_USER=you@gmail.com  SMTP_PASS=****  python credits_server.py")
        print(f"    Brevo:   SMTP_HOST=smtp-relay.brevo.com  SMTP_USER=you@spuric.com  SMTP_PASS=****  python credits_server.py")
        print(f"  Emails will be skipped until configured.")
    print("=" * 60)
    print()

    server = HTTPServer(("0.0.0.0", PORT), CreditsHandler)
    print(f"  Listening on http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down.")
        server.server_close()
