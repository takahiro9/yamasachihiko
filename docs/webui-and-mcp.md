# 閲覧 UI（Cloudflare）と MCP（Claude Code）の構成

## 1. 全体像

```
                 ┌─────────────── Mac（正本） ───────────────┐
 news-brief ───▶ │ dist/briefs/<ラベル>/  dist/themes/<ラベル>/ │  ← スキルが書くファイルが正本
 news-themes     │        │ news brief import                   │
                 │        ▼                                     │
 CLI / MCP ────▶ │ data/news.db（SQLite）                        │
  （読む: views）│   reports / sources / themes …               │
  （書く: 依頼） │   briefs / brief_* / theme_reviews / docs     │
                 │   change_requests                             │
                 └────────┬──────────────────────▲──────────────┘
                news publish --apply             │ news requests pull
            （差分の upsert SQL → wrangler）      │（画面からの依頼を取り込む）
                          ▼                      │
                 ┌────────── Cloudflare ─────────┴──────┐
                 │ D1（公開用の写し。shared の migration と同じ表）│
                 │   ▲ 読む: contracts/queries.sql            │
                 │   │ 書く: change_requests だけ              │
                 │ Worker world-events-viewer（cloud/worker）  │
                 │   /api/*  +  画面（public/ の静的ファイル）  │
                 │ Cloudflare Access（人のログイン）            │
                 └────────────────────────────────────────────┘
```

決めごと:

- **正本は Mac だけ**。Cloudflare 側は写しを読むだけで、書いてよいのは `change_requests`（変更の依頼）だけ
- **読み取りの定義は 1 か所**（`contracts/queries.sql`）。Worker（D1）と Mac（`world_events.views` → CLI・MCP）が
  同じ SQL を流すので、画面で見えるものと Claude Code から見えるものが一致する
- **依頼の定義も 1 か所**（`contracts/change_requests.json`）。画面・MCP・CLI のどこから来た依頼も同じ規則で形を検証し、
  意味の検証（存在するか等）は Mac で反映するときに正本に対して行う（`publish/ops.py` の `_apply`）
- ブリーフの出来事・つながりは、パイプラインの `events` / `links` とは別の表（`brief_events` / `brief_links`）に置く
  （スキルの「パイプラインの結果と混ぜない」方針のまま、画面と MCP から引けるようにするため）

## 2. ディレクトリ

| 場所 | 中身 |
|---|---|
| `contracts/queries.sql` | 名前付きの読み取りクエリ（`-- name:` / `-- params:` / `-- json:` / `-- one`） |
| `contracts/change_requests.json` | 依頼の種類・表示名・payload の形 |
| `migrations/shared/0004`〜`0005` | 依頼の出どころ（`origin`）、ブリーフ・テーマ整理・文書の表 |
| `src/world_events/contracts.py` / `views.py` | 契約の読み込み・検証、ビュー（`event_detail`・`brief_detail` など） |
| `src/world_events/briefs.py` | `dist/` のブリーフ・テーマ整理を DB に取り込む（何度やってもよい） |
| `src/world_events/publish/d1.py` | D1 への差分 upsert SQL、wrangler での適用、D1 からの依頼の読み出し |
| `cloud/worker/` | 閲覧用の Worker（Hono）。`src/index.ts` が API、`public/` が画面（ビルド不要） |
| `cloud/shared/access.ts` | Access の JWT 検証（summarizer と共通） |

## 3. 画面

| パス | 内容 |
|---|---|
| `#/` `#/briefs/<ラベル>` | ブリーフ: 全体の要点（overview.md）、主な出来事（重要度順・同じ地域は 3 件まで）、地域の要点、カテゴリ・地域の内訳、つながり。旧形式（events.json の無いもの）は brief.md をそのまま表示 |
| `#/briefs/<ラベル>/<E0001>` | 出来事: 要約・いつどこで誰が・報じた媒体と国・報道（先頭 8 本）・つながり（前のブリーフへの参照も） |
| `#/events` | ブリーフを横断した出来事の検索（ブリーフ・カテゴリ・地域・キーワード） |
| `#/themes` | テーマ整理（themes.md・急な動き）と、テーマの定義（争点テーマの追加・改版の依頼） |
| `#/sources` | 情報源の一覧と状態（取得の失敗）、追加・停止・再開の依頼 |
| `#/requests` | 依頼の一覧と結果（未反映 / 反映済み / 却下と理由） |
| `#/pipeline` | run-daily のダイジェストと出来事（パイプラインを動かしたときだけ中身が出る） |

依頼のフォームは `/api/requests/kinds`（＝契約）から組み立てるので、依頼の種類を増やしても画面の改修は要らない
（`_apply` に反映の処理を足すだけ。足し忘れはテストで検出する）。

## 4. 配信の手順（初回）

```sh
cd cloud/worker && npm install
npx wrangler d1 create world-events          # 出た database_id を wrangler.toml に書く
npm run migrate                              # migrations/shared を D1 に適用
# Zero Trust で Access のアプリケーションを作り（この Worker のホスト名、人のメールで許可）、
# Team domain と AUD を wrangler.toml の [vars] に書く
npm run deploy
cd ../.. && uv run news publish --apply      # dist/ を取り込み、D1 に写す（CLOUDFLARE_API_TOKEN が要る）
```

以後は、ブリーフを作るたびに `uv run news publish --apply`。前回の反映以降に変わった分だけを送る（`--full` で全部）。
画面からの依頼は `uv run news requests pull --apply` で取り込み・反映し、次の publish で結果が画面に返る。

## 4.1 ローカルで全体を動かす（docker compose）

```sh
docker compose up -d --build      # viewer（画面 + API + ローカル D1）と mcp を起動
open http://localhost:8788        # 画面（ローカルでは Access を外している）
docker compose logs -f mcp        # MCP のログ
docker compose down               # 停止（ローカル D1 は volume wrangler-state に残る）
```

| サービス | 中身 | ポート |
|---|---|---|
| `viewer` | `wrangler dev`。起動のたびに migration を当て、`news publish --local --full` で dist/ を取り込んで写してから画面を出す | 127.0.0.1:8788 |
| `mcp` | `news mcp --transport http`。Claude Code は `.mcp.json`（`http://localhost:8765/mcp`）で接続する | 127.0.0.1:8765 |
| `worker` | パイプラインの CLI（埋め込みの torch を含む重いイメージ）。`--profile pipeline` を付けたときだけ | — |

- `data/`（正本の DB）と `dist/` はホストと共有。`src/`・`contracts/`・`migrations/`・スキル・画面のファイルは読み取り専用で重ねているので、
  手元の変更は再起動（`docker compose restart mcp`）か、画面ならそのまま反映される。依存を変えたら `--build`
- ローカルの D1 は volume `wrangler-state` にあり、viewer と mcp が共有する（mcp の `publish(target="local")`・`pull_requests` がそのまま画面に効く）
- コンテナが動いている間は、ホストから `uv run news ...` で同じ DB に書かない（Docker Desktop のファイル共有越しの SQLite の同時書き込みは壊れやすい）。
  CLI は `docker compose exec mcp uv run --no-sync news ...` で使う
- 要約 LLM（パイプライン）はホストの Ollama（`host.docker.internal`）。パイプラインは `docker compose --profile pipeline run --rm worker run-stage l1` など

compose を使わずに画面だけ見る:

```sh
cd cloud/worker && cp .dev.vars.example .dev.vars   # ACCESS_DISABLED=1（本番では設定しない）
npm run migrate:local && (cd ../.. && uv run news publish --local) && npm run dev
```

## 5. MCP（Claude Code からニュースの収集と出来事の整理を回す）

実装は `src/world_events/mcp_server.py`（`news mcp`。要: `uv sync --extra mcp`）。ローカルでは docker compose の `mcp` サービスとして
HTTP で動かし、`.mcp.json` で Claude Code に登録してある（初回はプロジェクトの MCP サーバーとして承認を求められる）。
compose を使わない場合は `.mcp.json` を `{"command": "uv", "args": ["run", "news", "mcp"]}`（stdio）にしてもよい。

**MCP サーバーは Mac 上で動かす**。正本の DB とファイルが Mac にあり、
収集（robots.txt・間隔の制御）もスキルのスクリプトも Mac で動くため。Cloudflare 側に MCP は置かない
（置く場合も D1 の読み取りと依頼の書き込みだけになり、画面と同じことしかできない）。

ツールは「決まった手順の処理」と「読み取り」だけにし、**判断（出来事の統合・つながり）は Claude Code 側に残す**。
いまの news-brief スキルの手順 2・4（サブエージェントでの地域ごとの出来事化、メインセッションでの統合）は
そのまま Claude Code が行い、MCP はその前後の決まった処理を受け持つ。

| ツール | 中身（既存の実装） | 種類 |
|---|---|---|
| `status` | `views.run("status.overview")` + 収集済みの期間・最新のブリーフ・収集の状態 | 読む |
| `list_queries` / `view(name, params)` | 契約のクエリを名前で実行（情報源・履歴・依頼など、画面で見えるものは全部） | 読む |
| `collect_start(fetch_bodies)` / `collect_status` | `news run-stage collect` を子プロセスで開始し、進み具合とログの末尾を返す（数十分かかるため） | 書く（外部に取りに行く。確認を取ってから） |
| `brief_export(since, until)` | news-brief の `export_headlines.py` | 書く（dist/） |
| `brief_index(label, prev?)` | `build_events.py index` | 書く（dist/） |
| `brief_finalize(label)` | `build_events.py finalize` → `briefs.import_brief` | 書く（dist/・DB） |
| `themes_aggregate(since, until)` | news-themes の `aggregate_themes.py` | 書く（dist/） |
| `import_dist(path)` | `briefs.import_*`（themes.md を書いたあとなど） | 書く（DB） |
| `get_brief` / `search_events` / `get_event` / `get_theme_review` | `views.brief_detail` / `views.run("brief_events.list")` / `views.brief_event_detail` / `views.theme_review_detail` | 読む（画面と同じクエリ） |
| `request_kinds` / `submit_request(kind, payload, apply)` | 契約 / `ops.submit_change_request`（`origin='local'`） | 書く |
| `pull_requests(apply, target)` | `d1.fetch_pending_requests` → `ops.import_change_requests` | 書く |
| `publish(target)` | dist/ の取り込み → D1 へ。`local`（compose の viewer）/ `remote`（Cloudflare。確認を取ってから）/ `none` | 書く |

ラベルは `^[\d_-]{4,40}$` だけを受け付け、`import_dist` は dist/ の外を読まない。HTTP では Host が localhost のものだけを受け付ける
（DNS リバインディング対策）。

残っていること: スキルの `scripts/*.py` はいまもスキルの中にあり、MCP はそれを子プロセスで呼んでいる。
いずれ `src/world_events/brief/` に移して、スキルと MCP が同じ関数を呼ぶ形にするとよい。
