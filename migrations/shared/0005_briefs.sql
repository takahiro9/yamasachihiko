-- 簡易ブリーフ（news-brief スキル）とテーマ整理（news-themes スキル）の結果。正本は dist/ のファイルで、
-- `news brief import` が取り込み、publish で D1 に写す。パイプラインの events / links / event_themes とは混ぜない。

CREATE TABLE briefs (
  label TEXT PRIMARY KEY,            -- 1 日なら JST の日付、複数日なら <開始日>_<終了日>
  since TEXT NOT NULL,
  until TEXT NOT NULL,
  total_reports INTEGER NOT NULL DEFAULT 0,
  event_count INTEGER NOT NULL DEFAULT 0,
  link_count INTEGER NOT NULL DEFAULT 0,
  region_points TEXT,                -- JSON: 地域 → 地域の要点
  imported_at TEXT NOT NULL
);

CREATE TABLE brief_events (
  label TEXT NOT NULL REFERENCES briefs(label),
  id TEXT NOT NULL,                  -- E0001…（ブリーフの中で重要度順）
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  when_text TEXT, where_text TEXT, about TEXT,
  who TEXT,                          -- JSON 配列
  category TEXT NOT NULL,            -- 主分類（categories の先頭）
  categories TEXT NOT NULL,          -- JSON 配列
  region TEXT NOT NULL,
  regions TEXT,                      -- JSON: 地域 → 報道数
  n_reports INTEGER NOT NULL,
  media TEXT, countries TEXT, languages TEXT,   -- JSON 配列
  importance REAL NOT NULL DEFAULT 0,
  first_seen TEXT, last_seen TEXT,
  PRIMARY KEY (label, id)
);
CREATE INDEX idx_brief_events_cat ON brief_events(category, importance);

-- 出来事を報じた報道（rank は報じた時刻順）。D1 には先頭の数本だけ載せる
CREATE TABLE brief_event_reports (
  label TEXT NOT NULL,
  event_id TEXT NOT NULL,
  report_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  PRIMARY KEY (label, event_id, report_id)
);

-- つながり。端は <ラベル>/<出来事 id>（前のブリーフの出来事を指すこともある）
CREATE TABLE brief_links (
  label TEXT NOT NULL REFERENCES briefs(label),
  seq INTEGER NOT NULL,
  from_label TEXT NOT NULL, from_id TEXT NOT NULL,
  to_label TEXT NOT NULL, to_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('原因', '反応', '続報', '激化', '収束')),
  reason TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5,
  PRIMARY KEY (label, seq)
);
CREATE INDEX idx_brief_links_from ON brief_links(from_label, from_id);
CREATE INDEX idx_brief_links_to ON brief_links(to_label, to_id);

-- テーマ整理（news-themes）
CREATE TABLE theme_reviews (
  label TEXT PRIMARY KEY,
  since TEXT NOT NULL,
  until TEXT NOT NULL,
  briefs TEXT,                       -- JSON: 材料にしたブリーフのラベル
  event_count INTEGER NOT NULL DEFAULT 0,
  themes TEXT,                       -- JSON: テーマごとの件数・地域の内訳（出来事の一覧は brief_events を参照）
  spikes TEXT,                       -- JSON
  imported_at TEXT NOT NULL
);

-- Markdown の文書（overview.md・brief.md・themes.md）。D1 の 1 文の上限（100KB）に収まるよう分割して持つ
CREATE TABLE docs (
  scope TEXT NOT NULL CHECK (scope IN ('brief', 'themes')),
  label TEXT NOT NULL,
  name TEXT NOT NULL,                -- overview / brief / themes
  seq INTEGER NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (scope, label, name, seq)
);
