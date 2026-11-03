-- Mac 専用。D1 には載せない（本文・埋め込み・LLM キャッシュ・評価セット）。

CREATE TABLE report_texts (
  report_id TEXT PRIMARY KEY REFERENCES reports(id),
  body TEXT NOT NULL,
  stored_at TEXT NOT NULL            -- 本文は 30 日で捨てる（prune）
);

CREATE TABLE report_embeddings (
  report_id TEXT PRIMARY KEY REFERENCES reports(id),
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL               -- float32 little-endian
);

CREATE TABLE event_embeddings (
  event_id TEXT PRIMARY KEY REFERENCES events(id),
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,              -- 報道の埋め込みの平均（正規化済み）
  n INTEGER NOT NULL
);

CREATE TABLE theme_embeddings (
  theme_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (theme_id, version, model)
);

CREATE TABLE llm_results (
  input_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  output_json TEXT NOT NULL,
  latency_ms INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY (input_hash, model, prompt_version)
);

-- 評価セット（記事の抜粋 + 人が付けた正解）。git に入れない
CREATE TABLE eval_samples (
  id TEXT PRIMARY KEY,
  task TEXT NOT NULL CHECK (task IN ('l1', 'l2', 'l3')),
  input_json TEXT NOT NULL,
  expected_json TEXT,
  created_at TEXT NOT NULL
);
