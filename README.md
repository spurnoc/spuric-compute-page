# SPUR Compute Credits Landing Pages

Landing pages for SPUR Compute Credits, served by `credits_server.py` on port 8090.

## Pages

- `/compute/v3` - Main compute credits landing page
- `/compute/fomo` - FOMO/scarcity page with credit pool countdown
- `/compute/admin` - Admin dashboard with submissions and analytics
- `/compute/v1` - Legacy v1 page
- `/compute/v2` - Legacy v2 page

## Tech

- `credits_server.py` - Python stdlib HTTP server (no framework)
- Static file serving for images (.webp, .png, .jpg, .svg)
- Submission persistence via `submissions.json`
- Analytics via `analytics_events.json`
- Admin API protected by `ADMIN_TOKEN` env var
- Optional Cloudflare Turnstile bot protection

## Deploy

Deployed to nocbox-1 (10.220.3.168) via Docker Compose:

```bash
scp *.html credits_server.py arahman@10.220.3.168:~/deploy/landings/
ssh arahman@10.220.3.168 'cd ~/deploy && sudo docker compose up -d --build landings'
```
