-- Turso schema for SPUR Compute Credits

CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  organization TEXT,
  description TEXT,
  track TEXT,
  claim_code TEXT,
  form_version TEXT DEFAULT 'v3',
  status TEXT DEFAULT 'pending',
  ip TEXT,
  user_agent TEXT,
  source TEXT DEFAULT 'v3',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS claim_codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  track TEXT NOT NULL,
  credits TEXT NOT NULL,
  label TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS testimonials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  quote TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT,
  company TEXT,
  approved INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analytics_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  page TEXT,
  section TEXT,
  cta TEXT,
  visitor_id TEXT,
  meta TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- Seed claim codes
INSERT OR IGNORE INTO claim_codes (code, track, credits, label) VALUES ('FOUNDER-SPUR2026', 'founder', '$5,000', 'Founder / Startup');
INSERT OR IGNORE INTO claim_codes (code, track, credits, label) VALUES ('STUDENT-SPUR2026', 'student', '$500', 'Student / Researcher');
INSERT OR IGNORE INTO claim_codes (code, track, credits, label) VALUES ('EVENT-SPUR2026', 'event', '$1,000', 'Event Participant');

-- Seed Nick's testimonial
INSERT OR IGNORE INTO testimonials (quote, name, role, company, approved) VALUES ('SPUR is truly one of a kind. A great partner doing great things. Proud to work with a fellow Canadian company in the space.', 'Nick', 'Augure', 'Augure', 1);

-- Early access signups (from the coming soon page)
CREATE TABLE IF NOT EXISTS early_access (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  source TEXT DEFAULT 'coming_soon',
  created_at TEXT DEFAULT (datetime('now'))
);
