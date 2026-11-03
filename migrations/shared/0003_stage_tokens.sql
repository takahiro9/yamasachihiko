-- 要約 LLM の使用量を段階ごとに記録する（本番は従量課金。前日の 2 倍を超えたら止めるため。パイプライン詳細設計 §7）
ALTER TABLE stage_runs ADD COLUMN tokens_in INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stage_runs ADD COLUMN tokens_out INTEGER NOT NULL DEFAULT 0;
