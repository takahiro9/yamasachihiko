-- 公開用テーブル。D1 と共通（拡張を使わない）。
-- 書き込む側は表ごとに 1 つ: Mac のバッチだけが書く。Cloudflare 側が書くのは change_requests だけ。

CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('rss', 'sitemap', 'html_list', 'gdelt_query')),
  url TEXT NOT NULL DEFAULT '',
  media TEXT NOT NULL CHECK (media <> ''),
  country TEXT NOT NULL CHECK (country <> ''),
  language TEXT NOT NULL CHECK (language <> ''),
  interval_days INTEGER NOT NULL DEFAULT 1 CHECK (interval_days >= 1),
  extractor TEXT,                -- JSON。html_list のみ
  query TEXT,                    -- gdelt_query のみ
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  disabled_reason TEXT,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE source_state (
  source_id TEXT PRIMARY KEY REFERENCES sources(id),
  last_fetched_at TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_item_count INTEGER,
  feed_cap_hits INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE source_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  at TEXT NOT NULL,
  actor TEXT NOT NULL CHECK (actor IN ('cli', 'web', 'auto')),
  action TEXT NOT NULL CHECK (action IN ('add', 'update', 'disable', 'enable')),
  before_json TEXT,
  after_json TEXT,
  reason TEXT
);
CREATE INDEX idx_source_history_source ON source_history(source_id, at);

CREATE TABLE themes (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('basic', 'issue')),
  parent_id TEXT REFERENCES themes(id),
  is_fallback INTEGER NOT NULL DEFAULT 0 CHECK (is_fallback IN (0, 1)),
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((kind = 'issue') = (parent_id IS NOT NULL))
);

CREATE TABLE theme_versions (
  theme_id TEXT NOT NULL REFERENCES themes(id),
  version INTEGER NOT NULL,
  name TEXT NOT NULL,
  scope TEXT NOT NULL,
  stance_axis TEXT,              -- JSON 配列。争点テーマのみ
  phases TEXT,                   -- JSON 配列。争点テーマのみ
  reason TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (theme_id, version)
);

CREATE TABLE reports (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL,
  source_id TEXT,
  media TEXT NOT NULL,
  country TEXT NOT NULL,
  language TEXT NOT NULL,
  title TEXT NOT NULL,
  excerpt TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  title_ja TEXT,
  points_ja TEXT,                -- JSON 配列
  entities TEXT,                 -- JSON
  numbers TEXT,                  -- JSON
  event_time TEXT,
  l1_status TEXT NOT NULL DEFAULT 'pending' CHECK (l1_status IN ('pending', 'done', 'failed', 'skipped'))
);
CREATE INDEX idx_reports_fetched ON reports(fetched_at);
CREATE INDEX idx_reports_hash ON reports(content_hash);

CREATE TABLE events (
  id TEXT PRIMARY KEY,
  headline TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  occurred_at TEXT,
  place TEXT,
  actors TEXT,                   -- JSON 配列（当面は文字列）
  importance REAL NOT NULL DEFAULT 0,
  report_count INTEGER NOT NULL DEFAULT 0,
  country_count INTEGER NOT NULL DEFAULT 0,
  region TEXT,
  summary_status TEXT NOT NULL DEFAULT 'pending' CHECK (summary_status IN ('pending', 'single', 'done', 'fallback')),
  summary_report_count INTEGER NOT NULL DEFAULT 0,
  summary_country_count INTEGER NOT NULL DEFAULT 0,
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),   -- 人が直したら 1。以後は自動で上書きしない
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_events_updated ON events(updated_at);

CREATE TABLE event_reports (
  event_id TEXT NOT NULL REFERENCES events(id),
  report_id TEXT NOT NULL REFERENCES reports(id),
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
  joined_at TEXT NOT NULL,
  PRIMARY KEY (event_id, report_id),
  UNIQUE (report_id)             -- 報道は 1 つの出来事にだけ属する
);

CREATE TABLE links (
  id TEXT PRIMARY KEY,
  from_event_id TEXT NOT NULL REFERENCES events(id),
  to_event_id TEXT NOT NULL REFERENCES events(id),
  kind TEXT NOT NULL CHECK (kind IN ('原因', '反応', '続報', '激化', '収束')),
  rationale TEXT NOT NULL,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  status TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('confirmed_by_model', 'pending_review', 'rejected')),
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
  updated_at TEXT NOT NULL,
  CHECK (from_event_id <> to_event_id)
);

CREATE TABLE event_themes (
  event_id TEXT NOT NULL REFERENCES events(id),
  theme_id TEXT NOT NULL REFERENCES themes(id),
  theme_version INTEGER NOT NULL,     -- 判定に使った版。自動では判定し直さない
  confidence REAL,
  confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
  PRIMARY KEY (event_id, theme_id)
);
CREATE INDEX idx_event_themes_theme ON event_themes(theme_id);

CREATE TABLE report_stances (
  report_id TEXT NOT NULL REFERENCES reports(id),
  theme_id TEXT NOT NULL REFERENCES themes(id),
  theme_version INTEGER NOT NULL,
  stance TEXT NOT NULL,
  PRIMARY KEY (report_id, theme_id)
);

CREATE TABLE snapshots (
  theme_id TEXT NOT NULL REFERENCES themes(id),
  month TEXT NOT NULL,                -- YYYY-MM（JST）
  theme_version INTEGER NOT NULL,
  report_count INTEGER NOT NULL,
  event_count INTEGER NOT NULL,
  distribution TEXT NOT NULL,         -- JSON: 国・地域・言語ごとの内訳
  top_events TEXT NOT NULL,           -- JSON: 代表の出来事
  change_summary TEXT,
  phase TEXT,
  phase_rationale TEXT,
  stance_distribution TEXT,
  PRIMARY KEY (theme_id, month)
);

CREATE TABLE spikes (
  date TEXT NOT NULL,                 -- JST の日付
  theme_id TEXT NOT NULL REFERENCES themes(id),
  region TEXT NOT NULL DEFAULT '',    -- '' はテーマ全体
  count INTEGER NOT NULL,
  baseline REAL NOT NULL,
  PRIMARY KEY (date, theme_id, region)
);

CREATE TABLE digests (
  date TEXT PRIMARY KEY,              -- JST の日付
  generated_at TEXT NOT NULL,
  body TEXT NOT NULL                  -- JSON
);

CREATE TABLE job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('daily', 'monthly')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'partial', 'failed')),
  published_at TEXT
);

CREATE TABLE stage_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_run_id INTEGER REFERENCES job_runs(id),
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed', 'skipped')),
  processed INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE INDEX idx_stage_runs_stage ON stage_runs(stage, started_at);

-- Cloudflare 側（Workers）が書く唯一の表。Mac が取り込んで正本に反映する。
CREATE TABLE change_requests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- source.add / source.disable / theme.update / link.confirm / event.split ...
  payload TEXT NOT NULL,              -- JSON
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  reason TEXT,
  created_at TEXT NOT NULL,
  applied_at TEXT
);
