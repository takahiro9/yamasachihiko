-- 閲覧用の読み取りクエリ。D1（cloud/worker）と Mac の SQLite（views.py → CLI・MCP）の両方で同じものを使う。
-- 書けるのは D1 にも載っている表だけ（report_texts・埋め込み・LLM キャッシュ・stage_runs・report_stances は不可。publish/d1.py の _TABLES）。
-- 書式:
--   -- name: <名前>            クエリの区切り
--   -- params: a, b, c         ?1, ?2, ?3 に順に入る。渡されなければ NULL
--   -- json: col1, col2         JSON 文字列の列（読む側でパースする）
--   -- one                      1 行だけ返す（無ければ null）

-- name: status.overview
-- one
SELECT
  (SELECT COUNT(*) FROM events) AS event_count,
  (SELECT COUNT(*) FROM sources WHERE enabled = 1) AS source_enabled,
  (SELECT COUNT(*) FROM sources) AS source_total,
  (SELECT MAX(date) FROM digests) AS latest_digest,
  (SELECT COUNT(*) FROM change_requests WHERE status = 'pending') AS pending_requests,
  (SELECT MAX(started_at) FROM job_runs) AS last_job_at,
  (SELECT status FROM job_runs ORDER BY started_at DESC LIMIT 1) AS last_job_status,
  (SELECT MAX(published_at) FROM job_runs) AS last_published_at;

-- name: jobs.recent
-- params: limit
SELECT id, kind, started_at, finished_at, status, published_at
FROM job_runs ORDER BY started_at DESC LIMIT COALESCE(?1, 20);

-- name: digests.list
-- params: limit
SELECT date, generated_at FROM digests ORDER BY date DESC LIMIT COALESCE(?1, 60);

-- name: digests.get
-- params: date
-- json: body
-- one
SELECT date, generated_at, body FROM digests
WHERE date = COALESCE(?1, (SELECT MAX(date) FROM digests));

-- name: events.list
-- params: theme, region, since, q, limit, offset
SELECT e.id, e.headline, e.summary, e.occurred_at, e.place, e.importance, e.report_count, e.country_count,
       e.region, e.confirmed, e.first_seen_at, e.updated_at,
       (SELECT GROUP_CONCAT(et.theme_id) FROM event_themes et WHERE et.event_id = e.id) AS theme_ids
FROM events e
WHERE (?1 IS NULL OR EXISTS (SELECT 1 FROM event_themes et WHERE et.event_id = e.id AND et.theme_id = ?1))
  AND (?2 IS NULL OR e.region = ?2)
  AND (?3 IS NULL OR e.first_seen_at >= ?3)
  AND (?4 IS NULL OR e.headline LIKE '%' || ?4 || '%' OR e.summary LIKE '%' || ?4 || '%')
ORDER BY e.importance DESC, e.first_seen_at DESC
LIMIT COALESCE(?5, 50) OFFSET COALESCE(?6, 0);

-- name: events.get
-- params: id
-- json: actors
-- one
SELECT id, headline, summary, occurred_at, place, actors, importance, report_count, country_count, region,
       summary_status, confirmed, first_seen_at, updated_at
FROM events WHERE id = ?1;

-- name: events.reports
-- params: id
SELECT r.id, r.url, r.media, r.country, r.language, r.title, r.title_ja, r.published_at
FROM event_reports er JOIN reports r ON r.id = er.report_id
WHERE er.event_id = ?1 ORDER BY r.published_at;

-- name: events.themes
-- params: id
SELECT et.theme_id, et.theme_version, et.confidence, et.confirmed, t.kind, v.name
FROM event_themes et
JOIN themes t ON t.id = et.theme_id
JOIN theme_versions v ON v.theme_id = et.theme_id AND v.version = et.theme_version
WHERE et.event_id = ?1 ORDER BY t.kind, v.name;

-- name: events.links
-- params: id
SELECT k.id, k.kind, k.rationale, k.confidence, k.status, k.confirmed,
       k.from_event_id, k.to_event_id,
       CASE WHEN k.from_event_id = ?1 THEN 'out' ELSE 'in' END AS direction,
       o.id AS other_id, o.headline AS other_headline
FROM links k
JOIN events o ON o.id = CASE WHEN k.from_event_id = ?1 THEN k.to_event_id ELSE k.from_event_id END
WHERE k.from_event_id = ?1 OR k.to_event_id = ?1
ORDER BY k.confidence DESC;

-- name: regions.list
SELECT COALESCE(region, 'その他') AS region, COUNT(*) AS event_count
FROM events GROUP BY COALESCE(region, 'その他') ORDER BY event_count DESC;

-- name: themes.list
-- json: stance_axis, phases
SELECT t.id, t.kind, t.parent_id, t.is_fallback, t.current_version, t.updated_at,
       v.name, v.scope, v.stance_axis, v.phases,
       (SELECT COUNT(*) FROM event_themes et WHERE et.theme_id = t.id) AS event_count
FROM themes t JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version
ORDER BY t.kind, t.is_fallback, t.parent_id, t.id;

-- name: themes.get
-- params: id
-- json: stance_axis, phases
-- one
SELECT t.id, t.kind, t.parent_id, t.is_fallback, t.current_version, t.created_at, t.updated_at,
       v.name, v.scope, v.stance_axis, v.phases
FROM themes t JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version
WHERE t.id = ?1;

-- name: themes.versions
-- params: id
-- json: stance_axis, phases
SELECT version, name, scope, stance_axis, phases, reason, created_at
FROM theme_versions WHERE theme_id = ?1 ORDER BY version DESC;

-- name: themes.snapshots
-- params: id
-- json: distribution, top_events, stance_distribution
SELECT month, theme_version, report_count, event_count, distribution, top_events,
       change_summary, phase, phase_rationale, stance_distribution
FROM snapshots WHERE theme_id = ?1 ORDER BY month DESC;

-- name: spikes.recent
-- params: since, theme
SELECT s.date, s.theme_id, s.region, s.count, s.baseline, v.name
FROM spikes s
JOIN themes t ON t.id = s.theme_id
JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version
WHERE (?1 IS NULL OR s.date >= ?1) AND (?2 IS NULL OR s.theme_id = ?2)
ORDER BY s.date DESC, s.count DESC
LIMIT 200;

-- name: sources.list
-- params: enabled
SELECT s.id, s.type, s.url, s.media, s.country, s.language, s.interval_days, s.enabled, s.disabled_reason,
       s.note, s.updated_at, st.last_fetched_at, st.consecutive_failures, st.last_item_count, st.last_error
FROM sources s LEFT JOIN source_state st ON st.source_id = s.id
WHERE (?1 IS NULL OR s.enabled = ?1)
ORDER BY s.enabled DESC, s.country, s.media, s.id;

-- name: sources.history
-- params: id
-- json: before_json, after_json
SELECT id, at, actor, action, before_json, after_json, reason
FROM source_history WHERE source_id = ?1 ORDER BY at DESC, id DESC;

-- name: requests.list
-- params: status, limit
-- json: payload
SELECT id, kind, payload, status, reason, created_at, applied_at
FROM change_requests
WHERE (?1 IS NULL OR status = ?1)
ORDER BY created_at DESC LIMIT COALESCE(?2, 100);

-- ===== 簡易ブリーフ（news-brief）とテーマ整理（news-themes） =====

-- name: briefs.list
-- params: limit
SELECT label, since, until, total_reports, event_count, link_count, imported_at
FROM briefs ORDER BY until DESC LIMIT COALESCE(?1, 100);

-- name: briefs.get
-- params: label
-- json: region_points
-- one
SELECT label, since, until, total_reports, event_count, link_count, region_points, imported_at
FROM briefs WHERE label = COALESCE(?1, (SELECT label FROM briefs ORDER BY until DESC LIMIT 1));

-- name: docs.get
-- params: scope, label, name
-- one
SELECT ?1 AS scope, ?2 AS label, ?3 AS name, GROUP_CONCAT(body, '') AS body
FROM (SELECT body FROM docs WHERE scope = ?1 AND label = ?2 AND name = ?3 ORDER BY seq)
HAVING COUNT(*) > 0;

-- name: brief_events.list
-- params: label, category, region, q, limit, offset
-- json: categories, regions, media, countries
SELECT e.label, e.id, e.title, e.summary, e.when_text, e.where_text, e.category, e.categories, e.region, e.regions,
       e.n_reports, e.media, e.countries, e.importance, e.first_seen,
       (SELECT COUNT(*) FROM brief_links k
         WHERE (k.from_label = e.label AND k.from_id = e.id) OR (k.to_label = e.label AND k.to_id = e.id)) AS link_count
FROM brief_events e
WHERE (?1 IS NULL OR e.label = ?1)
  AND (?2 IS NULL OR EXISTS (SELECT 1 FROM json_each(e.categories) c WHERE c.value = ?2))
  AND (?3 IS NULL OR e.region = ?3)
  AND (?4 IS NULL OR e.title LIKE '%' || ?4 || '%' OR e.summary LIKE '%' || ?4 || '%' OR e.where_text LIKE '%' || ?4 || '%')
ORDER BY e.importance DESC, e.label DESC, e.id
LIMIT COALESCE(?5, 50) OFFSET COALESCE(?6, 0);

-- name: brief_events.get
-- params: label, id
-- json: who, categories, regions, media, countries, languages
-- one
SELECT e.*, b.since AS brief_since, b.until AS brief_until
FROM brief_events e JOIN briefs b ON b.label = e.label
WHERE e.label = ?1 AND e.id = ?2;

-- name: brief_events.reports
-- params: label, id
SELECT r.id, r.url, r.media, r.country, r.language, r.title, r.title_ja, r.published_at
FROM brief_event_reports x JOIN reports r ON r.id = x.report_id
WHERE x.label = ?1 AND x.event_id = ?2 ORDER BY x.rank;

-- name: brief_events.links
-- params: label, id
SELECT k.type, k.reason, k.confidence,
       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN 'out' ELSE 'in' END AS direction,
       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_label ELSE k.from_label END AS other_label,
       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_id ELSE k.from_id END AS other_id,
       o.title AS other_title
FROM brief_links k
LEFT JOIN brief_events o ON
  (o.label, o.id) = (CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_label ELSE k.from_label END,
                     CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_id ELSE k.from_id END)
WHERE (k.from_label = ?1 AND k.from_id = ?2) OR (k.to_label = ?1 AND k.to_id = ?2)
ORDER BY k.confidence DESC;

-- name: brief_links.list
-- params: label
SELECT k.seq, k.type, k.reason, k.confidence, k.from_label, k.from_id, f.title AS from_title,
       k.to_label, k.to_id, t.title AS to_title
FROM brief_links k
LEFT JOIN brief_events f ON f.label = k.from_label AND f.id = k.from_id
LEFT JOIN brief_events t ON t.label = k.to_label AND t.id = k.to_id
WHERE k.label = ?1 ORDER BY k.confidence DESC, k.seq;

-- name: brief_events.facets
-- params: label
SELECT 'category' AS facet, c.value AS value, COUNT(*) AS n, SUM(e.n_reports) AS reports
FROM brief_events e, json_each(e.categories) c WHERE (?1 IS NULL OR e.label = ?1) GROUP BY c.value
UNION ALL
SELECT 'region', e.region, COUNT(*), SUM(e.n_reports)
FROM brief_events e WHERE (?1 IS NULL OR e.label = ?1) GROUP BY e.region
ORDER BY 1, 3 DESC;

-- name: theme_reviews.list
SELECT label, since, until, event_count, imported_at FROM theme_reviews ORDER BY until DESC;

-- name: theme_reviews.get
-- params: label
-- json: briefs, themes, spikes
-- one
SELECT label, since, until, briefs, event_count, themes, spikes, imported_at
FROM theme_reviews WHERE label = COALESCE(?1, (SELECT label FROM theme_reviews ORDER BY until DESC LIMIT 1));
