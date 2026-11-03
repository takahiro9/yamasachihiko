-- 手動の publish（news publish）の記録。前回以降に変わった分だけを D1 に送るため。
CREATE TABLE publish_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1))   -- wrangler で D1 に適用までしたか
);
