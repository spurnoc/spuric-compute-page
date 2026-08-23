# SPUR Compute Credits Landing Pages

This repo contains the landing pages for SPUR's Compute Credits program. The pages are served by a lightweight Python HTTP server and deployed to nocbox-1 via Docker Compose.

## What This Is

SPUR offers compute credits (API credits and optional GPU access) to founders, students, and event participants. These pages let people learn about the program, see what others have built, and claim credits. There is also an admin dashboard for reviewing submissions and viewing analytics.

## Pages

| Route | File | Description |
|---|---|---|
| `/compute/v3` | `v3.html` | Main landing page. Tracks (Student/Founder/Event), project showcase, data retention policy, FAQ, claim form. |
| `/compute/fomo` | `fomo.html` | Scarcity page. Shows a $1,000,000 credit pool that ticks down as people claim. Has a claim code entry that opens a secret direct-claim modal (skips waitlist). |
| `/compute/admin` | `admin.html` | Admin dashboard. Two tabs: Submissions (table, detail modal, approve/reject, CSV export) and Analytics (daily trends, track breakdown, conversion funnel, CTA performance, scroll depth). Token-protected. |
| `/compute/v1` | `v1.html` | Legacy v1 landing page. |
| `/compute/v2` | `v2.html` | Legacy v2 landing page. |

## Server

`credits_server.py` is a Python stdlib HTTP server (no framework, no dependencies beyond Python 3.11). It handles:

- Serving HTML pages at the routes above (with and without `/compute/` prefix)
- Serving static image files (.webp, .png, .jpg, .svg) with proper content types and cache headers
- `POST /api/submit` - Form submissions from v3 and fomo pages
- `POST /api/claim` - Claim code validation
- `GET /api/fomo/feed` - Public anonymized feed of recent claimers (initials only, no emails)
- `GET /api/admin/submissions` - Token-protected submissions list
- `POST /api/admin/submissions/{id}/status` - Token-protected status update (approve/reject/reset)
- `GET /api/admin/analytics` - Token-protected aggregated analytics
- `POST /api/track` - Public analytics event tracking
- `GET /api/turnstile-site-key` - Tells frontend whether to render Cloudflare Turnstile widget

Data is persisted to `submissions.json` and `analytics_events.json` (both gitignored, created at runtime).

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ADMIN_TOKEN` | No | `spurnoc` | Protects admin API endpoints. Set to a strong value in production. |
| `PORT` | No | `8090` | HTTP port to listen on. |
| `BASE_URL` | No | - | Base URL for links in emails and responses. |
| `SMTP_HOST` | No | - | SMTP server for sending notification emails on new submissions. |
| `SMTP_PORT` | No | `587` | SMTP port. |
| `SMTP_USER` | No | - | SMTP username. |
| `SMTP_PASS` | No | - | SMTP password. |
| `SMTP_FROM` | No | - | From address for notification emails. |
| `TURNSTILE_SITE_KEY` | No | - | Cloudflare Turnstile site key. If unset, bot protection is skipped. |
| `TURNSTILE_SECRET_KEY` | No | - | Cloudflare Turnstile secret key for server-side verification. |

## Claim Codes

Valid claim codes are defined in `credits_server.py` in the `VALID_CODES` dict. Each code maps to a track (founder/student/event) and credit amount. Codes can be entered on the FOMO page to skip the waitlist and claim directly via a modal popup.

## Deployment

Deployed to nocbox-1 (10.220.3.168) via Docker Compose:

```bash
# Copy updated files to the server
scp *.html credits_server.py Dockerfile *.webp arahman@10.220.3.168:~/deploy/landings/

# Rebuild and restart the container
ssh arahman@10.220.3.168 'cd ~/deploy && sudo docker compose up -d --build landings'
```

The Docker container (`deploy-landings-1`) runs on port 8090. All routes are accessible at `http://10.220.3.168:8090/compute/{page}`.

## Tech Stack

- Python 3.11 stdlib HTTP server (no Flask, no FastAPI, no dependencies)
- Vanilla HTML/CSS/JS (no build step, no framework)
- Docker for deployment
- OKLCH color system, Sora/DM Sans/JetBrains Mono fonts (Google Fonts CDN)
- Custom analytics (no GTM, no external analytics service)
- SVG charts in admin dashboard (no chart library)

## Key Design Decisions

- **API credits = Model as a Service**: Users call hosted models via API. We never imply users get inference on their own models via API credits.
- **Compute credits = GPU access**: Optional, not guaranteed. The form has a toggle between "API credits only" (default) and "API credits + compute". Selecting compute shows a warning that GPU access is limited and not guaranteed, but API credits are always available.
- **No server implications**: Copy never says users will be "on our servers", "console login", "our fleet", or "owned and operated". Uses "access" language instead.
- **No fabricated testimonials**: The testimonials section is currently replaced with a "Your quote could be here next" CTA. Real quotes will be added when available.
- **No em dashes**: Per brand guideline, em dashes are not used anywhere in the copy.
- **Data retention**: Full data retention policy is included on the page, adapted from the official SPUR Compute Data Retention and Privacy Statement. No client-identifying info is exposed.
