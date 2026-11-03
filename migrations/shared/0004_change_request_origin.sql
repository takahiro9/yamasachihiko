-- 変更依頼の出どころ。web = 閲覧 UI（D1 に書かれ、Mac が取り込む）、local = Mac 上の CLI・MCP（ローカルの正本に直接書く）。
-- 情報源の変更履歴の actor（web / cli）はここから決める。
ALTER TABLE change_requests ADD COLUMN origin TEXT NOT NULL DEFAULT 'web' CHECK (origin IN ('web', 'local'));
