from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# 閾値・重要度の重みはコード内の定数にする（変えると再現性に関わるので git の履歴に残す。§4.8）
CLUSTER_HIGH = 0.82          # これ以上は同じ出来事に追加
CLUSTER_LOW = 0.70           # これ未満は新しい出来事。間は L2 で判定
CLUSTER_WINDOW_DAYS = 3      # 直近何日の出来事を候補にするか
L2_BATCH = 10
EXCERPT_CHARS = 600          # L1 に渡す本文の長さ
L3_MAX_REPORTS = 6
BODY_RETENTION_DAYS = 30
SOURCE_AUTO_DISABLE_FAILURES = 5
DOMAIN_INTERVAL_SEC = 5.0
DAILY_REPORT_CAP = 6000
SPIKE_WINDOW_DAYS = 14
SPIKE_RATIO_ISSUE = 2.5
SPIKE_RATIO_BASIC = 4.0
SPIKE_MIN_COUNT = 5
DIGEST_SIZE = 10
DIGEST_REGION_CAP = 3
DIGEST_PER_THEME = 3
IMPORTANCE_ISSUE_BONUS = 1.0
# 使いすぎの防止: 1 晩のトークン数が前日の BUDGET_RATIO 倍を超えたら止める。
# 履歴が小さいうち（開発の見本データだけ流した翌日など）に誤って止まらないよう下限を置く
BUDGET_RATIO = 2.0
BUDGET_FLOOR_TOKENS = 500_000

# 環境ごとの既定値（パイプライン詳細設計 §4.1）。切り替えは NEWS_ENV だけ
ENV_DEFAULTS = {
    "local": {"base_url": "http://host.docker.internal:11434/v1", "model": "qwen3.5:9b", "concurrency": 2},
    "prod": {"base_url": "", "model": "summarizer", "concurrency": 8},
}


@dataclass(frozen=True)
class Settings:
    env: str = "local"
    db_path: Path = Path("data/news.db")
    dist_dir: Path = Path("dist")
    llm_base_url: str = ENV_DEFAULTS["local"]["base_url"]
    llm_api_key: str = "local"
    llm_model: str = ENV_DEFAULTS["local"]["model"]
    llm_concurrency: int = ENV_DEFAULTS["local"]["concurrency"]
    llm_max_retries: int = 4              # 429 / 5xx は指数的に間隔を空けて再試行（openai SDK）
    llm_extra_body: dict = field(default_factory=dict)   # 例: Qwen3 の thinking を切る指定。サーバーによって書き方が違う
    llm_daily_token_limit: int = 0        # 0 なら前日比の上限だけ使う
    cf_access_client_id: str = ""         # 本番: summarizer 用の Access サービストークン
    cf_access_client_secret: str = ""
    embed_model: str = "BAAI/bge-m3"
    llm_temperature: float = 0.1
    llm_timeout_sec: float = 300.0
    user_agent: str = "world-events/0.1 (+personal use)"
    migrations_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "migrations")

    @classmethod
    def from_env(cls, env: str | None = None, model: str | None = None) -> Settings:
        # .env.example をそのままコピーすると空の値が入るので、空文字は未設定として扱う
        e = {k: v for k, v in os.environ.items() if v != ""}
        env = env or e.get("NEWS_ENV", "local")
        if env not in ENV_DEFAULTS:
            raise ValueError(f"NEWS_ENV は local か prod: {env!r}")
        d = ENV_DEFAULTS[env]
        s = cls(
            env=env,
            db_path=Path(e.get("NEWS_DB_PATH", cls.db_path)),
            dist_dir=Path(e.get("NEWS_DIST_DIR", cls.dist_dir)),
            llm_base_url=e.get("LLM_BASE_URL", d["base_url"]),
            llm_api_key=e.get("LLM_API_KEY", "local"),
            llm_model=model or e.get("LLM_MODEL", d["model"]),
            llm_concurrency=int(e.get("LLM_CONCURRENCY", d["concurrency"])),
            llm_extra_body=json.loads(e.get("LLM_EXTRA_BODY") or "{}"),
            llm_daily_token_limit=int(e.get("LLM_DAILY_TOKEN_LIMIT", 0)),
            cf_access_client_id=e.get("CF_ACCESS_CLIENT_ID", ""),
            cf_access_client_secret=e.get("CF_ACCESS_CLIENT_SECRET", ""),
            embed_model=e.get("EMBED_MODEL", cls.embed_model),
            user_agent=e.get("NEWS_USER_AGENT", cls.user_agent),
        )
        s.validate()
        return s

    def validate(self) -> None:
        """本番の設定漏れ（ローカルの URL のまま本番を流す、トークン無し）を実行前に弾く。"""
        if self.env == "prod":
            if not self.llm_base_url or "host.docker.internal" in self.llm_base_url or "localhost" in self.llm_base_url:
                raise ValueError("NEWS_ENV=prod には summarizer Worker の LLM_BASE_URL が必要")
            if not (self.cf_access_client_id and self.cf_access_client_secret):
                raise ValueError("NEWS_ENV=prod には CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET が必要")

    def with_env(self, env: str, model: str | None = None) -> Settings:
        return replace(Settings.from_env(env, model), db_path=self.db_path, dist_dir=self.dist_dir)

    @property
    def llm_headers(self) -> dict[str, str]:
        if self.cf_access_client_id:
            return {"CF-Access-Client-Id": self.cf_access_client_id, "CF-Access-Client-Secret": self.cf_access_client_secret}
        return {}
