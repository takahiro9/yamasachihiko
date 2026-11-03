// 閲覧画面。ビルド不要の ES モジュール。データは同じオリジンの /api（Worker → D1）から読む。
// 変更の依頼フォームは /api/requests/kinds（contracts/change_requests.json）から組み立てる。

// ---------- 小道具 ----------
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = (s) => new Raw(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/** タグ付きテンプレート。差し込む値はエスケープし、配列はつなぐ。raw() で包んだものはそのまま */
function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    const one = (x) => (x instanceof Raw ? x.s : x === false || x === null || x === undefined ? "" : esc(x));
    out += (Array.isArray(v) ? v.map(one).join("") : one(v)) + strings[i + 1];
  });
  return raw(out);
}
const $ = (sel, root = document) => root.querySelector(sel);
const safeUrl = (u) => (/^https?:\/\//i.test(u ?? "") ? u : "#");
const enc = encodeURIComponent;
const num = (n) => (n ?? 0).toLocaleString("ja-JP");

const JST = { timeZone: "Asia/Tokyo" };
function jst(iso, withTime = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const o = { ...JST, month: "numeric", day: "numeric", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) };
  return d.toLocaleString("ja-JP", o);
}
const period = (a, b) => `${jst(a)} 〜 ${jst(b)}`;

class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
async function api(path, init) {
  const r = await fetch(path, { credentials: "same-origin", ...init });
  if (r.status === 401) throw new ApiError(401, "ログインが切れています。ページを再読み込みして Cloudflare Access でログインし直してください。");
  const body = r.headers.get("Content-Type")?.includes("json") ? await r.json() : null;
  if (!r.ok) throw new ApiError(r.status, body?.error ?? `HTTP ${r.status}`);
  return body;
}
const tryApi = (path) => api(path).catch((e) => (e.status === 404 ? null : Promise.reject(e)));

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

// ---------- Markdown（ブリーフの overview.md・brief.md・themes.md 用の最小限。HTML は通さない） ----------
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, t, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`);
}
function markdown(src) {
  const out = [];
  let list = null, para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (const line of (src ?? "").split("\n")) {
    let m;
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      flushPara(); flushList();
      const lv = Math.min(m[1].length + 1, 4);
      out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
    } else if ((m = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/))) {
      flushPara();
      const tag = m[1] ? "ol" : "ul";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(m[2]);
    } else if ((m = line.match(/^>\s?(.*)$/))) {
      flushPara(); flushList();
      out.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else if (list && /^\s{2,}\S/.test(line)) {
      list.items[list.items.length - 1] += " " + line.trim();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return raw(`<div class="md">${out.join("\n")}</div>`);
}

// ---------- 共有の状態 ----------
const state = { kinds: null, themes: null, status: null };
const getKinds = async () => (state.kinds ??= await api("/api/requests/kinds"));
const getThemes = async () => (state.themes ??= await api("/api/themes"));

async function refreshStatus() {
  try {
    const s = await api("/api/status");
    state.status = s.overview;
    const b = $("#pending-badge");
    b.hidden = !s.overview.pending_requests;
    b.textContent = s.overview.pending_requests;
    const briefs = await api("/api/briefs");
    const latest = briefs[0];
    $("#statusline").textContent = [
      latest ? `最新のブリーフ ${latest.label}（${jst(latest.until)} まで）` : "ブリーフはまだありません",
      `情報源 ${s.overview.source_enabled}/${s.overview.source_total} 本が有効`,
      s.overview.last_published_at ? `最終反映 ${jst(s.overview.last_published_at)}` : null,
    ].filter(Boolean).join(" ・ ");
  } catch { /* 状態行は出なくても困らない */ }
}

// ---------- 部品 ----------
const pill = (text, cls = "") => html`<span class="pill ${cls}">${text}</span>`;
const chips = (items, cls = "") => html`<div class="chips">${(items ?? []).map((c) => html`<span class="chip ${cls}">${c}</span>`)}</div>`;
const empty = (title, body) => html`<div class="card empty"><h2>${title}</h2><p>${body}</p></div>`;
const briefLink = (label, id) => `#/briefs/${enc(label)}/${enc(id)}`;

function briefEventCard(e, { showLabel = false } = {}) {
  const media = e.media ?? [];
  return html`<a class="card event cardlink" href="${briefLink(e.label, e.id)}">
    <h3>${e.title}</h3>
    <div class="meta">
      ${showLabel ? html`<span>${e.label}</span>` : ""}
      <span>${e.region}</span><span>報道 ${e.n_reports}</span>
      ${media.length ? html`<span>${media.slice(0, 3).join("・")}${media.length > 3 ? ` ほか ${media.length - 3}` : ""}</span>` : ""}
      ${e.link_count ? html`<span>つながり ${e.link_count}</span>` : ""}
    </div>
    ${e.summary ? html`<p>${e.summary}</p>` : ""}
    ${chips(e.categories)}
  </a>`;
}

/** 主な出来事: 重要度順、同じ地域は 3 件まで（溢れた枠は他地域で埋める）。ブリーフの brief.md と同じ規則 */
function pickTop(events, size = 10, cap = 3) {
  const per = {}, out = [];
  for (const e of events) {
    if (out.length >= size) break;
    if ((per[e.region] ?? 0) < cap) { out.push(e); per[e.region] = (per[e.region] ?? 0) + 1; }
  }
  return out;
}

function linkLine(k) {
  const t = (lb, id, title) => html`<a href="${briefLink(lb, id)}">${title ?? `${lb}/${id}`}</a>`;
  return html`<div class="link-row"><div>
      ${t(k.from_label, k.from_id, k.from_title)} <span class="muted">→</span> ${pill(k.type)} <span class="muted">→</span>
      ${t(k.to_label, k.to_id, k.to_title)}
      ${k.reason ? html`<div class="muted small">${k.reason}</div>` : ""}
    </div><span class="muted small">${Math.round((k.confidence ?? 0) * 100)}%</span></div>`;
}

// ---------- 画面: ブリーフ ----------
async function viewBrief(label = "latest") {
  const [b, list] = await Promise.all([tryApi(`/api/briefs/${enc(label)}`), api("/api/briefs")]);
  if (!b) {
    return empty("ブリーフがまだありません",
      "Mac で news-brief スキルを実行したあと、`uv run news publish --apply` で反映すると、ここに表示されます。");
  }
  const picker = list.length > 1 ? html`<select id="brief-picker" aria-label="ブリーフを選ぶ">
      ${list.map((x) => html`<option value="${x.label}" ${x.label === b.label ? "selected" : ""}>${x.label}（出来事 ${x.event_count}）</option>`)}
    </select>` : "";
  const head = html`<div class="page-head"><div>
      <h1>ブリーフ ${b.label}</h1>
      <div class="muted small">${period(b.since, b.until)} JST ・ 報道 ${num(b.total_reports)} 件 ・ 出来事 ${num(b.event_count)} ・ つながり ${num(b.link_count)}</div>
    </div>${picker}</div>`;

  if (b.brief_md) {
    return html`${head}
      <div class="notice">旧形式のブリーフです（出来事の一覧・つながりは無く、要約の文書だけがあります）。news-brief を同じ期間でやり直すと、出来事ごとに見られるようになります。</div>
      <article class="card">${markdown(b.brief_md)}</article>`;
  }

  const top = pickTop(b.top);
  const cats = b.facets.filter((f) => f.facet === "category");
  const regs = b.facets.filter((f) => f.facet === "region");
  const maxCat = Math.max(1, ...cats.map((f) => f.n));
  const points = Object.entries(b.region_points ?? {});
  return html`${head}
    <div class="cols">
      <div>
        ${b.overview ? html`<article class="card">${markdown(b.overview)}</article>` : ""}
        <h2>主な出来事</h2>
        <div class="stack">${top.map((e) => briefEventCard(e))}</div>
        <a class="btn more" href="#/events?label=${enc(b.label)}">このブリーフの出来事をすべて見る（${num(b.event_count)}）</a>
        ${points.length ? html`<h2>地域の要点</h2><div class="stack">${points.map(([r, p]) => html`
          <div class="card"><h3>${r}</h3><p class="small">${p}</p>
          <a class="small" href="#/events?label=${enc(b.label)}&region=${enc(r)}">この地域の出来事</a></div>`)}</div>` : ""}
      </div>
      <aside>
        <h2>カテゴリ</h2>
        <div class="card stack small">${cats.map((f) => html`
          <a class="bar" href="#/events?label=${enc(b.label)}&category=${enc(f.value)}">
            <span>${f.value}</span><span class="muted">${f.n}</span>
            <span class="bar-fill" style="width:${Math.round((f.n / maxCat) * 100)}%"></span></a>`)}</div>
        <h2>地域</h2>
        <div class="chips">${regs.map((f) => html`<a class="chip" href="#/events?label=${enc(b.label)}&region=${enc(f.value)}">${f.value} ${f.n}</a>`)}</div>
        <h2>つながり</h2>
        ${b.links.length ? html`<div class="card stack small">${b.links.slice(0, 20).map((k) => linkLine(k))}</div>`
          : html`<p class="muted small">つながりはありません。</p>`}
      </aside>
    </div>`;
}

async function viewBriefEvent(label, id) {
  const e = await tryApi(`/api/briefs/${enc(label)}/events/${enc(id)}`);
  if (!e) return empty("出来事が見つかりません", `${label}/${id} はまだ反映されていないか、ブリーフが作り直されています。`);
  const regions = Object.entries(e.regions ?? {}).sort((a, b) => b[1] - a[1]);
  return html`
    <p class="small"><a href="#/briefs/${enc(e.label)}">← ブリーフ ${e.label}</a></p>
    <h1>${e.title}</h1>
    <div class="meta"><span>${e.label}/${e.id}</span><span>${e.region}</span><span>報道 ${e.n_reports}</span><span>重要度 ${e.importance}</span></div>
    ${chips(e.categories)}
    <div class="cols" style="margin-top:16px">
      <div>
        ${e.summary ? html`<article class="card"><p style="margin:0">${e.summary}</p></article>` : ""}
        <h2>つながり</h2>
        ${e.links.length ? html`<div class="card stack small">${e.links.map((k) => html`<div class="link-row"><div>
            <span class="muted">${k.direction === "out" ? "この出来事 →" : "←"}</span> ${pill(k.type)}
            <a href="${briefLink(k.other_label, k.other_id)}">${k.other_title ?? `${k.other_label}/${k.other_id}`}</a>
            ${k.other_label !== e.label ? html`<span class="muted">（${k.other_label}）</span>` : ""}
            ${k.reason ? html`<div class="muted">${k.reason}</div>` : ""}
          </div><span class="muted">${Math.round(k.confidence * 100)}%</span></div>`)}</div>` : html`<p class="muted small">つながりはありません。</p>`}
        <h2>報道${e.n_reports > e.reports.length ? html` <span class="muted small">（${e.n_reports} 本のうち先頭 ${e.reports.length} 本）</span>` : ""}</h2>
        <div class="stack">${e.reports.map((r) => html`<div class="card small">
            <a href="${safeUrl(r.url)}" target="_blank" rel="noopener noreferrer">${r.title_ja || r.title}</a>
            ${r.title_ja && r.title_ja !== r.title ? html`<div class="muted">${r.title}</div>` : ""}
            <div class="meta"><span>${r.media}</span><span>${r.country} / ${r.language}</span><span>${jst(r.published_at)}</span></div>
          </div>`)}</div>
      </div>
      <aside>
        <h2>概要</h2>
        <dl class="card kv">
          ${e.when_text ? html`<dt>いつ</dt><dd>${e.when_text}</dd>` : ""}
          ${e.where_text ? html`<dt>どこで</dt><dd>${e.where_text}</dd>` : ""}
          ${e.who?.length ? html`<dt>誰が</dt><dd>${e.who.join("、")}</dd>` : ""}
          ${e.about ? html`<dt>対象</dt><dd>${e.about}</dd>` : ""}
          <dt>報じた期間</dt><dd>${jst(e.first_seen)} 〜 ${jst(e.last_seen)}</dd>
          <dt>媒体</dt><dd>${(e.media ?? []).join("、")}</dd>
          <dt>国</dt><dd>${(e.countries ?? []).join("、")}</dd>
          <dt>言語</dt><dd>${(e.languages ?? []).join("、")}</dd>
        </dl>
        ${regions.length ? html`<h2>報じた地域</h2><div class="chips">${regions.map(([r, n]) => html`<span class="chip">${r} ${n}</span>`)}</div>` : ""}
      </aside>
    </div>`;
}

// ---------- 画面: 出来事（ブリーフを横断して探す） ----------
async function viewEvents(params) {
  const f = { label: params.get("label") || "", category: params.get("category") || "", region: params.get("region") || "", q: params.get("q") || "" };
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const [list, facets, briefs] = await Promise.all([
    api(`/api/brief-events?${qs}&limit=50`), api(`/api/brief-facets${f.label ? `?label=${enc(f.label)}` : ""}`), api("/api/briefs"),
  ]);
  const opt = (vals, cur, all) => html`<option value="">${all}</option>${vals.map((v) => html`<option value="${v}" ${v === cur ? "selected" : ""}>${v}</option>`)}`;
  const cats = facets.filter((x) => x.facet === "category").map((x) => x.value);
  const regs = facets.filter((x) => x.facet === "region").map((x) => x.value);
  setTimeout(() => wireFilters(f), 0);
  return html`
    <div class="page-head"><div><h1>出来事</h1><p class="muted small" style="margin:0">ブリーフでまとめた出来事を、期間・カテゴリ・地域で探す（重要度順）</p></div></div>
    <form class="filters" id="filters">
      <input type="search" name="q" value="${f.q}" placeholder="見出し・要約・場所で探す">
      <select name="label">${opt(briefs.map((b) => b.label), f.label, "すべてのブリーフ")}</select>
      <select name="category">${opt(cats, f.category, "すべてのカテゴリ")}</select>
      <select name="region">${opt(regs, f.region, "すべての地域")}</select>
    </form>
    <div class="stack" id="event-list">${list.length ? list.map((e) => briefEventCard(e, { showLabel: !f.label })) : html`<p class="muted">当てはまる出来事はありません。</p>`}</div>
    ${list.length === 50 ? html`<button class="btn more" id="more" data-offset="50">さらに表示</button>` : ""}`;
}

function wireFilters(f) {
  const form = $("#filters");
  if (!form) return;
  const go = () => {
    const qs = new URLSearchParams([...new FormData(form)].filter(([, v]) => v));
    location.hash = `#/events${qs.size ? `?${qs}` : ""}`;
  };
  form.addEventListener("change", go);
  form.addEventListener("submit", (ev) => { ev.preventDefault(); go(); });
  $("#more")?.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const offset = Number(btn.dataset.offset);
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    const more = await api(`/api/brief-events?${qs}&limit=50&offset=${offset}`);
    $("#event-list").insertAdjacentHTML("beforeend", more.map((e) => briefEventCard(e, { showLabel: !f.label })).join(""));
    if (more.length < 50) btn.remove(); else btn.dataset.offset = offset + 50;
  });
}

// ---------- 画面: テーマ ----------
async function viewThemes(label) {
  const [reviews, themes] = await Promise.all([api("/api/theme-reviews"), getThemes()]);
  const r = reviews.length ? await tryApi(`/api/theme-reviews/${enc(label ?? "latest")}`) : null;
  const basics = themes.filter((t) => t.kind === "basic");
  const issues = themes.filter((t) => t.kind === "issue");
  const spikes = r?.spikes ?? [];
  return html`
    <div class="page-head"><div><h1>テーマ</h1>
      ${r ? html`<div class="muted small">テーマ整理 ${r.label}（${r.since} 〜 ${r.until}、出来事 ${num(r.event_count)}）</div>` : ""}</div>
      ${reviews.length > 1 ? html`<select id="review-picker">${reviews.map((x) => html`<option value="${x.label}" ${x.label === r?.label ? "selected" : ""}>${x.label}</option>`)}</select>` : ""}
    </div>
    ${!r ? html`<div class="notice">テーマ整理はまだありません。ブリーフがたまったら Mac で news-themes スキルを実行し、publish すると、ここにテーマごとの動きと急な動きが出ます。</div>` : ""}
    ${r?.review ? html`<article class="card">${markdown(r.review)}</article>` : ""}
    ${r && !r.review && r.themes?.length ? html`<h2>テーマごとの件数</h2><div class="table-wrap"><table>
        <thead><tr><th>カテゴリ</th><th class="num">出来事</th><th class="num">報道</th><th>主な地域</th></tr></thead>
        <tbody>${r.themes.map((t) => html`<tr><td><a href="#/events?category=${enc(t.category)}">${t.category}</a></td>
          <td class="num">${t.events}</td><td class="num">${num(t.reports)}</td>
          <td class="small">${Object.entries(t.regions ?? {}).slice(0, 3).map(([k, v]) => `${k} ${v}`).join("、")}</td></tr>`)}</tbody></table></div>` : ""}
    ${spikes.length ? html`<h2>急な動き</h2><div class="stack">${spikes.map((s) => html`<div class="notice">
        ${s.date ?? ""} <b>${s.category ?? s.name ?? ""}</b>${s.region ? ` × ${s.region}` : ""} — ${s.count} 件${s.baseline !== undefined ? `（平常 ${s.baseline}）` : ""}${s.reference ? "（参考）" : ""}</div>`)}</div>` : ""}

    <h2>テーマの定義</h2>
    <p class="muted small">基本テーマと、その下で追いかける争点テーマ。追加・改版は依頼として送り、Mac 側で取り込んだときに反映されます。</p>
    <div class="grid">${basics.map((t) => html`<div class="card">
        <h3>${t.name}</h3><p class="small muted" style="margin:.2rem 0">${t.scope}</p>
        ${issues.filter((i) => i.parent_id === t.id).map((i) => html`<div class="small" style="margin-top:6px">
          <span class="chip issue">争点</span> <b>${i.name}</b> <span class="muted">v${i.current_version}</span>
          <div class="muted">${i.scope}</div>
          ${i.stance_axis?.length ? html`<div class="muted">論調: ${i.stance_axis.join(" / ")}</div>` : ""}
          ${i.phases?.length ? html`<div class="muted">フェーズ: ${i.phases.join(" → ")}</div>` : ""}
          <button class="btn small" data-req="theme.update" data-preset='${JSON.stringify({ id: i.id, name: i.name, scope: i.scope, stance_axis: i.stance_axis, phases: i.phases })}'>改版を依頼</button>
        </div>`)}
        <div class="row-actions" style="margin-top:8px">
          <button class="btn small" data-req="theme.add" data-preset='${JSON.stringify({ kind: "issue", parent_id: t.id })}' data-lock="kind,parent_id">争点テーマを追加</button>
          <button class="btn small ghost" data-req="theme.update" data-preset='${JSON.stringify({ id: t.id, name: t.name, scope: t.scope })}' data-lock="id">範囲を改版</button>
        </div>
      </div>`)}</div>`;
}

// ---------- 画面: 情報源 ----------
async function viewSources(params) {
  const filter = params.get("show") ?? "all";
  const list = await api(`/api/sources${filter === "on" ? "?enabled=1" : filter === "off" ? "?enabled=0" : ""}`);
  const failing = list.filter((s) => s.enabled && s.consecutive_failures > 0).length;
  setTimeout(() => {
    $("#src-q")?.addEventListener("input", (ev) => {
      const v = ev.target.value.toLowerCase();
      for (const tr of document.querySelectorAll("#src-table tbody tr")) tr.hidden = v && !tr.dataset.k.includes(v);
    });
    $("#src-show")?.addEventListener("change", (ev) => (location.hash = `#/sources?show=${ev.target.value}`));
  }, 0);
  return html`
    <div class="page-head"><div><h1>情報源</h1>
      <div class="muted small">${list.length} 本${failing ? html` ・ <span class="error">取得に失敗中 ${failing} 本</span>` : ""}</div></div>
      <button class="btn primary" data-req="source.add">情報源の追加を依頼</button></div>
    <div class="filters">
      <input type="search" id="src-q" placeholder="媒体名・国・URL で絞る">
      <select id="src-show">
        <option value="all" ${filter === "all" ? "selected" : ""}>すべて</option>
        <option value="on" ${filter === "on" ? "selected" : ""}>有効だけ</option>
        <option value="off" ${filter === "off" ? "selected" : ""}>停止中だけ</option>
      </select>
    </div>
    <div class="table-wrap"><table id="src-table">
      <thead><tr><th>媒体</th><th>国 / 言語</th><th>最後の取得</th><th class="num">件数</th><th>状態</th><th></th></tr></thead>
      <tbody>${list.map((s) => html`<tr class="${s.enabled ? "" : "disabled"}" data-k="${`${s.media} ${s.country} ${s.language} ${s.url} ${s.id}`.toLowerCase()}">
        <td><b>${s.media}</b><div class="muted small"><a href="${safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${s.type}</a> ・ ${s.id}</div></td>
        <td>${s.country} / ${s.language}</td>
        <td class="small">${jst(s.last_fetched_at) || "—"}</td>
        <td class="num">${s.last_item_count ?? "—"}</td>
        <td>${s.enabled ? (s.consecutive_failures ? pill(`失敗 ${s.consecutive_failures} 回`, "warn") : pill("有効", "ok")) : pill("停止", "bad")}
          ${!s.enabled && s.disabled_reason ? html`<div class="muted small">${s.disabled_reason}</div>` : ""}
          ${s.last_error && s.consecutive_failures ? html`<div class="muted small">${String(s.last_error).slice(0, 80)}</div>` : ""}</td>
        <td>${s.enabled
          ? html`<button class="btn small danger" data-req="source.disable" data-preset='${JSON.stringify({ id: s.id })}' data-lock="id">停止</button>`
          : html`<button class="btn small" data-req="source.enable" data-preset='${JSON.stringify({ id: s.id })}' data-lock="id">再開</button>`}</td>
      </tr>`)}</tbody></table></div>`;
}

// ---------- 画面: 依頼 ----------
async function viewRequests() {
  const [list, kinds] = await Promise.all([api("/api/requests"), getKinds()]);
  const cls = { pending: "warn", applied: "ok", rejected: "bad" };
  const label = { pending: "未反映", applied: "反映済み", rejected: "却下" };
  return html`
    <div class="page-head"><div><h1>変更の依頼</h1>
      <p class="muted small" style="margin:0">画面から送った依頼は、Mac 側で取り込んだとき（<code>news requests pull --apply</code>、または Claude Code から）に検証・反映され、次の publish で結果がここに返ります。</p></div></div>
    ${list.length ? html`<div class="table-wrap"><table>
      <thead><tr><th>日時</th><th>依頼</th><th>内容</th><th>状態</th></tr></thead>
      <tbody>${list.map((r) => html`<tr>
        <td class="small">${jst(r.created_at)}<div class="muted">${r.origin === "local" ? "Mac" : "画面"}</div></td>
        <td>${kinds[r.kind]?.label ?? r.kind}</td>
        <td><pre class="payload">${JSON.stringify(r.payload, null, 1)}</pre></td>
        <td>${pill(label[r.status] ?? r.status, cls[r.status])}${r.reason ? html`<div class="muted small">${r.reason}</div>` : ""}
          ${r.applied_at ? html`<div class="muted small">${jst(r.applied_at)}</div>` : ""}</td>
      </tr>`)}</tbody></table></div>` : empty("依頼はまだありません", "情報源やテーマの画面から、追加・停止・改版を依頼できます。")}`;
}

// ---------- 画面: パイプライン（run-daily の結果。データがあるときだけ使う） ----------
async function viewPipeline() {
  const d = await tryApi("/api/digests/latest");
  if (!d) return empty("パイプラインの結果はありません", "毎晩の処理（run-daily）は止めています。いまはブリーフを見てください。");
  const body = d.body;
  const card = (e) => html`<a class="card event cardlink" href="#/pipeline/events/${enc(e.id)}"><h3>${e.headline}</h3>
    <div class="meta"><span>${e.region}</span><span>報道 ${e.report_count}</span><span>${e.country_count} か国</span></div>
    ${e.summary ? html`<p>${e.summary}</p>` : ""}${e.continuation ? html`<div class="cont">↳ ${e.continuation}</div>` : ""}</a>`;
  return html`<h1>ダイジェスト ${body.date}</h1>
    ${body.spikes.map((s) => html`<div class="notice">急な動き: <b>${s.name}</b>${s.region ? `（${s.region}）` : ""} — ${s.count} 件（平常 ${s.baseline}）</div>`)}
    ${body.notices.map((n) => html`<div class="notice">${n}</div>`)}
    <h2>主な出来事</h2><div class="stack">${body.top.map(card)}</div>
    ${body.sections.map((s) => html`<h2>${s.name}</h2><div class="stack">${s.events.map(card)}</div>`)}`;
}

async function viewPipelineEvent(id) {
  const [e, themes] = await Promise.all([tryApi(`/api/events/${enc(id)}`), getThemes()]);
  if (!e) return empty("出来事が見つかりません", id);
  const attached = new Set(e.themes.map((t) => t.theme_id));
  return html`<p class="small"><a href="#/pipeline">← ダイジェスト</a></p>
    <h1>${e.headline}</h1>
    <div class="meta"><span>${e.region}</span><span>報道 ${e.report_count}</span><span>${e.country_count} か国</span>${e.confirmed ? pill("確認済み", "ok") : ""}</div>
    ${e.summary ? html`<article class="card" style="margin-top:12px"><p style="margin:0">${e.summary}</p></article>` : ""}
    <h2>テーマ</h2>
    <div class="chips">${e.themes.map((t) => html`<span class="chip ${t.kind}">${t.name}
      <button title="外す依頼" data-req="event.remove_theme" data-preset='${JSON.stringify({ event_id: e.id, theme_id: t.theme_id })}' data-lock="event_id,theme_id">×</button></span>`)}</div>
    <div class="filters" style="margin-top:8px"><select id="add-theme"><option value="">テーマを付ける依頼…</option>
      ${themes.filter((t) => !attached.has(t.id)).map((t) => html`<option value="${t.id}" data-v="${t.current_version}">${t.name}</option>`)}</select></div>
    <h2>つながり</h2>
    ${e.links.length ? html`<div class="card stack small">${e.links.map((k) => html`<div class="link-row"><div>
      ${k.direction === "out" ? "→" : "←"} ${pill(k.kind)} <a href="#/pipeline/events/${enc(k.other_id)}">${k.other_headline}</a>
      <div class="muted">${k.rationale}</div></div>
      <div class="row-actions">${pill(k.status)}
        <button class="btn small" data-req="link.confirm" data-preset='${JSON.stringify({ id: k.id })}' data-lock="id" data-direct>確認</button>
        <button class="btn small danger" data-req="link.reject" data-preset='${JSON.stringify({ id: k.id })}' data-lock="id">却下</button></div></div>`)}</div>`
      : html`<p class="muted small">つながりはありません。</p>`}
    <h2>報道</h2>
    <div class="stack">${e.reports.map((r) => html`<div class="card small"><a href="${safeUrl(r.url)}" target="_blank" rel="noopener noreferrer">${r.title_ja || r.title}</a>
      <div class="meta"><span>${r.media}</span><span>${r.country}</span><span>${jst(r.published_at)}</span></div></div>`)}</div>`;
}

// ---------- 依頼のダイアログ（契約から組み立てる） ----------
const FIELD_LABELS = {
  id: "ID", type: "種類（rss / sitemap / html_list / gdelt_query）", url: "URL", media: "媒体名", country: "国コード（JP, US…）",
  language: "言語コード（ja, en…）", interval_days: "取得の間隔（日）", query: "検索条件（gdelt_query のみ）", extractor: "抽出の設定（JSON。html_list のみ）",
  note: "メモ", reason: "理由", kind: "種類（basic / issue）", parent_id: "親の基本テーマ", name: "名前", scope: "範囲",
  stance_axis: "論調の軸（カンマ区切り。例: 推進,慎重,反対）", phases: "フェーズ（カンマ区切り。例: 黎明,拡大,摩擦,ルール整備,定着）",
  event_id: "出来事", theme_id: "テーマ", theme_version: "テーマの版",
};

async function openRequest(kind, preset = {}, lock = []) {
  const kinds = await getKinds();
  const k = kinds[kind];
  if (!k) return toast(`未対応の依頼: ${kind}`);
  const dlg = $("#req-dialog");
  $("#req-title").textContent = k.label;
  $("#req-error").hidden = true;
  $("#req-fields").innerHTML = Object.entries(k.fields).map(([name, t]) => {
    const base = t.replace(/\?$/, "");
    const opt = t.endsWith("?");
    let v = preset[name];
    if (Array.isArray(v)) v = v.join(",");
    else if (v && typeof v === "object") v = JSON.stringify(v);
    const locked = lock.includes(name);
    const attrs = `name="${esc(name)}" data-type="${esc(base)}" ${opt ? "" : "required"} ${locked ? "readonly" : ""}`;
    const input = name === "reason" || name === "scope" || base === "object"
      ? `<textarea ${attrs} rows="${base === "object" ? 3 : 2}">${esc(v)}</textarea>`
      : `<input ${attrs} type="${base === "integer" ? "number" : "text"}" value="${esc(v)}">`;
    if (locked && v !== undefined && v !== "") return `<input type="hidden" ${attrs} value="${esc(v)}"><div class="field"><span>${esc(FIELD_LABELS[name] ?? name)}</span><b>${esc(v)}</b></div>`;
    return `<label class="field"><span>${esc(FIELD_LABELS[name] ?? name)}${opt ? "（任意）" : ""}</span>${input}</label>`;
  }).join("");
  dlg.dataset.kind = kind;
  dlg.showModal();
  dlg.querySelector("input:not([type=hidden]):not([readonly]), textarea")?.focus();
}

function collectPayload(form) {
  const p = {};
  for (const el of form.querySelectorAll("[name][data-type]")) {
    const v = el.value.trim();
    if (!v) continue;
    const t = el.dataset.type;
    if (t === "integer") p[el.name] = Number(v);
    else if (t === "string[]") p[el.name] = v.split(/[,、\n]/).map((s) => s.trim()).filter(Boolean);
    else if (t === "object") p[el.name] = JSON.parse(v);
    else p[el.name] = v;
  }
  return p;
}

async function sendRequest(kind, payload) {
  const r = await api("/api/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, payload }) });
  toast("依頼を受け付けました。Mac 側で取り込んだときに反映されます。");
  refreshStatus();
  return r;
}

function wireDialog() {
  const dlg = $("#req-dialog");
  $("#req-cancel").addEventListener("click", () => dlg.close());
  $("#req-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("#req-error");
    try {
      await sendRequest(dlg.dataset.kind, collectPayload(ev.target));
      dlg.close();
    } catch (e) {
      err.textContent = e instanceof SyntaxError ? "JSON の書き方が正しくありません" : e.message;
      err.hidden = false;
    }
  });
  // data-req の付いたボタンで依頼を開く（data-direct は確認なしで送る）
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-req]");
    if (!btn) return;
    ev.preventDefault();
    const preset = btn.dataset.preset ? JSON.parse(btn.dataset.preset) : {};
    const lock = (btn.dataset.lock ?? "").split(",").filter(Boolean);
    if (btn.hasAttribute("data-direct")) {
      try { await sendRequest(btn.dataset.req, preset); btn.disabled = true; } catch (e) { toast(e.message); }
    } else {
      openRequest(btn.dataset.req, preset, lock);
    }
  });
  document.addEventListener("change", async (ev) => {
    if (ev.target.id === "brief-picker") location.hash = `#/briefs/${enc(ev.target.value)}`;
    if (ev.target.id === "review-picker") location.hash = `#/themes/${enc(ev.target.value)}`;
    if (ev.target.id === "add-theme" && ev.target.value) {
      const o = ev.target.selectedOptions[0];
      const id = location.hash.split("/").pop();
      openRequest("event.confirm_theme", { event_id: decodeURIComponent(id), theme_id: o.value, theme_version: Number(o.dataset.v) },
        ["event_id", "theme_id", "theme_version"]);
      ev.target.value = "";
    }
  });
}

// ---------- ルーティング ----------
const routes = [
  [/^\/$/, "briefs", () => viewBrief()],
  [/^\/briefs\/([^/]+)$/, "briefs", (m) => viewBrief(decodeURIComponent(m[1]))],
  [/^\/briefs\/([^/]+)\/([^/]+)$/, "briefs", (m) => viewBriefEvent(decodeURIComponent(m[1]), decodeURIComponent(m[2]))],
  [/^\/events$/, "events", (_, p) => viewEvents(p)],
  [/^\/themes$/, "themes", () => viewThemes()],
  [/^\/themes\/([^/]+)$/, "themes", (m) => viewThemes(decodeURIComponent(m[1]))],
  [/^\/sources$/, "sources", (_, p) => viewSources(p)],
  [/^\/requests$/, "requests", () => viewRequests()],
  [/^\/pipeline$/, "", () => viewPipeline()],
  [/^\/pipeline\/events\/([^/]+)$/, "", (m) => viewPipelineEvent(decodeURIComponent(m[1]))],
];

let seq = 0;
async function render() {
  const [path, query] = (location.hash.slice(1) || "/").split("?");
  const params = new URLSearchParams(query ?? "");
  const view = $("#view");
  const my = ++seq;
  for (const [re, nav, fn] of routes) {
    const m = path.match(re);
    if (!m) continue;
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
    view.innerHTML = `<div class="skeleton">読み込み中…</div>`;
    try {
      const out = await fn(m, params);
      if (my !== seq) return; // 途中で別の画面に移った
      view.innerHTML = String(out);
      const h1 = view.querySelector("h1");
      document.title = h1 ? `${h1.textContent} — 世界の出来事` : "世界の出来事";
    } catch (e) {
      if (my !== seq) return;
      view.innerHTML = String(empty("読み込めませんでした", e.message));
    }
    window.scrollTo(0, 0);
    return;
  }
  view.innerHTML = String(empty("ページが見つかりません", path));
}

wireDialog();
window.addEventListener("hashchange", render);
render();
refreshStatus();
