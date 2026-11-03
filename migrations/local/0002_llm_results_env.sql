-- キャッシュのキーに env と model を入れる（ローカルで作った結果が本番に混ざらないように。パイプライン詳細設計 §4.3）。
-- 中身はキャッシュなので作り直してよい。served_model は Worker が返した実際のモデル ID。
DROP TABLE llm_results;
CREATE TABLE llm_results (
  input_hash TEXT NOT NULL,
  env TEXT NOT NULL CHECK (env IN ('local', 'prod')),
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  served_model TEXT,
  output_json TEXT NOT NULL,
  latency_ms INTEGER,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (input_hash, env, model, prompt_version)
);
