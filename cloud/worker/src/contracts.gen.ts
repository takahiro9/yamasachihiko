// 生成物。直接編集しない（contracts/ を直して npm run gen）
import type { ContractKinds, ContractQueries } from "./contracts.ts";
export const QUERIES: ContractQueries = {
  "status.overview": {
    "sql": "SELECT\n  (SELECT COUNT(*) FROM events) AS event_count,\n  (SELECT COUNT(*) FROM sources WHERE enabled = 1) AS source_enabled,\n  (SELECT COUNT(*) FROM sources) AS source_total,\n  (SELECT MAX(date) FROM digests) AS latest_digest,\n  (SELECT COUNT(*) FROM change_requests WHERE status = 'pending') AS pending_requests,\n  (SELECT MAX(started_at) FROM job_runs) AS last_job_at,\n  (SELECT status FROM job_runs ORDER BY started_at DESC LIMIT 1) AS last_job_status,\n  (SELECT MAX(published_at) FROM job_runs) AS last_published_at",
    "params": [],
    "json": [],
    "one": true
  },
  "jobs.recent": {
    "sql": "SELECT id, kind, started_at, finished_at, status, published_at\nFROM job_runs ORDER BY started_at DESC LIMIT COALESCE(?1, 20)",
    "params": [
      "limit"
    ],
    "json": [],
    "one": false
  },
  "digests.list": {
    "sql": "SELECT date, generated_at FROM digests ORDER BY date DESC LIMIT COALESCE(?1, 60)",
    "params": [
      "limit"
    ],
    "json": [],
    "one": false
  },
  "digests.get": {
    "sql": "SELECT date, generated_at, body FROM digests\nWHERE date = COALESCE(?1, (SELECT MAX(date) FROM digests))",
    "params": [
      "date"
    ],
    "json": [
      "body"
    ],
    "one": true
  },
  "events.list": {
    "sql": "SELECT e.id, e.headline, e.summary, e.occurred_at, e.place, e.importance, e.report_count, e.country_count,\n       e.region, e.confirmed, e.first_seen_at, e.updated_at,\n       (SELECT GROUP_CONCAT(et.theme_id) FROM event_themes et WHERE et.event_id = e.id) AS theme_ids\nFROM events e\nWHERE (?1 IS NULL OR EXISTS (SELECT 1 FROM event_themes et WHERE et.event_id = e.id AND et.theme_id = ?1))\n  AND (?2 IS NULL OR e.region = ?2)\n  AND (?3 IS NULL OR e.first_seen_at >= ?3)\n  AND (?4 IS NULL OR e.headline LIKE '%' || ?4 || '%' OR e.summary LIKE '%' || ?4 || '%')\nORDER BY e.importance DESC, e.first_seen_at DESC\nLIMIT COALESCE(?5, 50) OFFSET COALESCE(?6, 0)",
    "params": [
      "theme",
      "region",
      "since",
      "q",
      "limit",
      "offset"
    ],
    "json": [],
    "one": false
  },
  "events.get": {
    "sql": "SELECT id, headline, summary, occurred_at, place, actors, importance, report_count, country_count, region,\n       summary_status, confirmed, first_seen_at, updated_at\nFROM events WHERE id = ?1",
    "params": [
      "id"
    ],
    "json": [
      "actors"
    ],
    "one": true
  },
  "events.reports": {
    "sql": "SELECT r.id, r.url, r.media, r.country, r.language, r.title, r.title_ja, r.published_at\nFROM event_reports er JOIN reports r ON r.id = er.report_id\nWHERE er.event_id = ?1 ORDER BY r.published_at",
    "params": [
      "id"
    ],
    "json": [],
    "one": false
  },
  "events.themes": {
    "sql": "SELECT et.theme_id, et.theme_version, et.confidence, et.confirmed, t.kind, v.name\nFROM event_themes et\nJOIN themes t ON t.id = et.theme_id\nJOIN theme_versions v ON v.theme_id = et.theme_id AND v.version = et.theme_version\nWHERE et.event_id = ?1 ORDER BY t.kind, v.name",
    "params": [
      "id"
    ],
    "json": [],
    "one": false
  },
  "events.links": {
    "sql": "SELECT k.id, k.kind, k.rationale, k.confidence, k.status, k.confirmed,\n       k.from_event_id, k.to_event_id,\n       CASE WHEN k.from_event_id = ?1 THEN 'out' ELSE 'in' END AS direction,\n       o.id AS other_id, o.headline AS other_headline\nFROM links k\nJOIN events o ON o.id = CASE WHEN k.from_event_id = ?1 THEN k.to_event_id ELSE k.from_event_id END\nWHERE k.from_event_id = ?1 OR k.to_event_id = ?1\nORDER BY k.confidence DESC",
    "params": [
      "id"
    ],
    "json": [],
    "one": false
  },
  "regions.list": {
    "sql": "SELECT COALESCE(region, 'その他') AS region, COUNT(*) AS event_count\nFROM events GROUP BY COALESCE(region, 'その他') ORDER BY event_count DESC",
    "params": [],
    "json": [],
    "one": false
  },
  "themes.list": {
    "sql": "SELECT t.id, t.kind, t.parent_id, t.is_fallback, t.current_version, t.updated_at,\n       v.name, v.scope, v.stance_axis, v.phases,\n       (SELECT COUNT(*) FROM event_themes et WHERE et.theme_id = t.id) AS event_count\nFROM themes t JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version\nORDER BY t.kind, t.is_fallback, t.parent_id, t.id",
    "params": [],
    "json": [
      "stance_axis",
      "phases"
    ],
    "one": false
  },
  "themes.get": {
    "sql": "SELECT t.id, t.kind, t.parent_id, t.is_fallback, t.current_version, t.created_at, t.updated_at,\n       v.name, v.scope, v.stance_axis, v.phases\nFROM themes t JOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version\nWHERE t.id = ?1",
    "params": [
      "id"
    ],
    "json": [
      "stance_axis",
      "phases"
    ],
    "one": true
  },
  "themes.versions": {
    "sql": "SELECT version, name, scope, stance_axis, phases, reason, created_at\nFROM theme_versions WHERE theme_id = ?1 ORDER BY version DESC",
    "params": [
      "id"
    ],
    "json": [
      "stance_axis",
      "phases"
    ],
    "one": false
  },
  "themes.snapshots": {
    "sql": "SELECT month, theme_version, report_count, event_count, distribution, top_events,\n       change_summary, phase, phase_rationale, stance_distribution\nFROM snapshots WHERE theme_id = ?1 ORDER BY month DESC",
    "params": [
      "id"
    ],
    "json": [
      "distribution",
      "top_events",
      "stance_distribution"
    ],
    "one": false
  },
  "spikes.recent": {
    "sql": "SELECT s.date, s.theme_id, s.region, s.count, s.baseline, v.name\nFROM spikes s\nJOIN themes t ON t.id = s.theme_id\nJOIN theme_versions v ON v.theme_id = t.id AND v.version = t.current_version\nWHERE (?1 IS NULL OR s.date >= ?1) AND (?2 IS NULL OR s.theme_id = ?2)\nORDER BY s.date DESC, s.count DESC\nLIMIT 200",
    "params": [
      "since",
      "theme"
    ],
    "json": [],
    "one": false
  },
  "sources.list": {
    "sql": "SELECT s.id, s.type, s.url, s.media, s.country, s.language, s.interval_days, s.enabled, s.disabled_reason,\n       s.note, s.updated_at, st.last_fetched_at, st.consecutive_failures, st.last_item_count, st.last_error\nFROM sources s LEFT JOIN source_state st ON st.source_id = s.id\nWHERE (?1 IS NULL OR s.enabled = ?1)\nORDER BY s.enabled DESC, s.country, s.media, s.id",
    "params": [
      "enabled"
    ],
    "json": [],
    "one": false
  },
  "sources.history": {
    "sql": "SELECT id, at, actor, action, before_json, after_json, reason\nFROM source_history WHERE source_id = ?1 ORDER BY at DESC, id DESC",
    "params": [
      "id"
    ],
    "json": [
      "before_json",
      "after_json"
    ],
    "one": false
  },
  "requests.list": {
    "sql": "SELECT id, kind, payload, status, reason, created_at, applied_at\nFROM change_requests\nWHERE (?1 IS NULL OR status = ?1)\nORDER BY created_at DESC LIMIT COALESCE(?2, 100)",
    "params": [
      "status",
      "limit"
    ],
    "json": [
      "payload"
    ],
    "one": false
  },
  "briefs.list": {
    "sql": "SELECT label, since, until, total_reports, event_count, link_count, imported_at\nFROM briefs ORDER BY until DESC LIMIT COALESCE(?1, 100)",
    "params": [
      "limit"
    ],
    "json": [],
    "one": false
  },
  "briefs.get": {
    "sql": "SELECT label, since, until, total_reports, event_count, link_count, region_points, imported_at\nFROM briefs WHERE label = COALESCE(?1, (SELECT label FROM briefs ORDER BY until DESC LIMIT 1))",
    "params": [
      "label"
    ],
    "json": [
      "region_points"
    ],
    "one": true
  },
  "docs.get": {
    "sql": "SELECT ?1 AS scope, ?2 AS label, ?3 AS name, GROUP_CONCAT(body, '') AS body\nFROM (SELECT body FROM docs WHERE scope = ?1 AND label = ?2 AND name = ?3 ORDER BY seq)\nHAVING COUNT(*) > 0",
    "params": [
      "scope",
      "label",
      "name"
    ],
    "json": [],
    "one": true
  },
  "brief_events.list": {
    "sql": "SELECT e.label, e.id, e.title, e.summary, e.when_text, e.where_text, e.category, e.categories, e.region, e.regions,\n       e.n_reports, e.media, e.countries, e.importance, e.first_seen,\n       (SELECT COUNT(*) FROM brief_links k\n         WHERE (k.from_label = e.label AND k.from_id = e.id) OR (k.to_label = e.label AND k.to_id = e.id)) AS link_count\nFROM brief_events e\nWHERE (?1 IS NULL OR e.label = ?1)\n  AND (?2 IS NULL OR EXISTS (SELECT 1 FROM json_each(e.categories) c WHERE c.value = ?2))\n  AND (?3 IS NULL OR e.region = ?3)\n  AND (?4 IS NULL OR e.title LIKE '%' || ?4 || '%' OR e.summary LIKE '%' || ?4 || '%' OR e.where_text LIKE '%' || ?4 || '%')\nORDER BY e.importance DESC, e.label DESC, e.id\nLIMIT COALESCE(?5, 50) OFFSET COALESCE(?6, 0)",
    "params": [
      "label",
      "category",
      "region",
      "q",
      "limit",
      "offset"
    ],
    "json": [
      "categories",
      "regions",
      "media",
      "countries"
    ],
    "one": false
  },
  "brief_events.get": {
    "sql": "SELECT e.*, b.since AS brief_since, b.until AS brief_until\nFROM brief_events e JOIN briefs b ON b.label = e.label\nWHERE e.label = ?1 AND e.id = ?2",
    "params": [
      "label",
      "id"
    ],
    "json": [
      "who",
      "categories",
      "regions",
      "media",
      "countries",
      "languages"
    ],
    "one": true
  },
  "brief_events.reports": {
    "sql": "SELECT r.id, r.url, r.media, r.country, r.language, r.title, r.title_ja, r.published_at\nFROM brief_event_reports x JOIN reports r ON r.id = x.report_id\nWHERE x.label = ?1 AND x.event_id = ?2 ORDER BY x.rank",
    "params": [
      "label",
      "id"
    ],
    "json": [],
    "one": false
  },
  "brief_events.links": {
    "sql": "SELECT k.type, k.reason, k.confidence,\n       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN 'out' ELSE 'in' END AS direction,\n       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_label ELSE k.from_label END AS other_label,\n       CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_id ELSE k.from_id END AS other_id,\n       o.title AS other_title\nFROM brief_links k\nLEFT JOIN brief_events o ON\n  (o.label, o.id) = (CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_label ELSE k.from_label END,\n                     CASE WHEN k.from_label = ?1 AND k.from_id = ?2 THEN k.to_id ELSE k.from_id END)\nWHERE (k.from_label = ?1 AND k.from_id = ?2) OR (k.to_label = ?1 AND k.to_id = ?2)\nORDER BY k.confidence DESC",
    "params": [
      "label",
      "id"
    ],
    "json": [],
    "one": false
  },
  "brief_links.list": {
    "sql": "SELECT k.seq, k.type, k.reason, k.confidence, k.from_label, k.from_id, f.title AS from_title,\n       k.to_label, k.to_id, t.title AS to_title\nFROM brief_links k\nLEFT JOIN brief_events f ON f.label = k.from_label AND f.id = k.from_id\nLEFT JOIN brief_events t ON t.label = k.to_label AND t.id = k.to_id\nWHERE k.label = ?1 ORDER BY k.confidence DESC, k.seq",
    "params": [
      "label"
    ],
    "json": [],
    "one": false
  },
  "brief_events.facets": {
    "sql": "SELECT 'category' AS facet, c.value AS value, COUNT(*) AS n, SUM(e.n_reports) AS reports\nFROM brief_events e, json_each(e.categories) c WHERE (?1 IS NULL OR e.label = ?1) GROUP BY c.value\nUNION ALL\nSELECT 'region', e.region, COUNT(*), SUM(e.n_reports)\nFROM brief_events e WHERE (?1 IS NULL OR e.label = ?1) GROUP BY e.region\nORDER BY 1, 3 DESC",
    "params": [
      "label"
    ],
    "json": [],
    "one": false
  },
  "theme_reviews.list": {
    "sql": "SELECT label, since, until, event_count, imported_at FROM theme_reviews ORDER BY until DESC",
    "params": [],
    "json": [],
    "one": false
  },
  "theme_reviews.get": {
    "sql": "SELECT label, since, until, briefs, event_count, themes, spikes, imported_at\nFROM theme_reviews WHERE label = COALESCE(?1, (SELECT label FROM theme_reviews ORDER BY until DESC LIMIT 1))",
    "params": [
      "label"
    ],
    "json": [
      "briefs",
      "themes",
      "spikes"
    ],
    "one": true
  }
};
export const KINDS: ContractKinds = {
  "source.add": {
    "label": "情報源を追加",
    "fields": {
      "id": "string",
      "type": "string",
      "url": "string?",
      "media": "string",
      "country": "string",
      "language": "string",
      "interval_days": "integer?",
      "query": "string?",
      "extractor": "object?",
      "note": "string?",
      "reason": "string?"
    }
  },
  "source.disable": {
    "label": "情報源を停止",
    "fields": {
      "id": "string",
      "reason": "string"
    }
  },
  "source.enable": {
    "label": "情報源を再開",
    "fields": {
      "id": "string",
      "reason": "string?"
    }
  },
  "theme.add": {
    "label": "テーマを追加",
    "fields": {
      "id": "string",
      "kind": "string",
      "parent_id": "string?",
      "name": "string",
      "scope": "string",
      "stance_axis": "string[]?",
      "phases": "string[]?",
      "reason": "string?"
    }
  },
  "theme.update": {
    "label": "テーマを改版",
    "fields": {
      "id": "string",
      "name": "string?",
      "scope": "string?",
      "stance_axis": "string[]?",
      "phases": "string[]?",
      "reason": "string"
    }
  },
  "link.confirm": {
    "label": "つながりを確認",
    "fields": {
      "id": "string"
    }
  },
  "link.reject": {
    "label": "つながりを却下",
    "fields": {
      "id": "string",
      "reason": "string?"
    }
  },
  "event.confirm_theme": {
    "label": "出来事にテーマを付ける",
    "fields": {
      "event_id": "string",
      "theme_id": "string",
      "theme_version": "integer"
    }
  },
  "event.remove_theme": {
    "label": "出来事からテーマを外す",
    "fields": {
      "event_id": "string",
      "theme_id": "string"
    }
  }
};
