// «Servers»: every server of every hoster, sorted and filtered; a server's page with its
// load charts, the history of its plan and price, and what it cost.
import { lineChart } from "./charts.js";
import { count, t } from "./i18n.js";
import {
  api,
  badge,
  card,
  chips,
  dateShort,
  dayMonth,
  days,
  h,
  header,
  loading,
  message,
  money,
  moment,
  monthName,
  percent,
  segmented,
  sum,
  sumValue,
  tg,
} from "./ui.js";

let list = null;
let listLoading = false;
const pages = new Map();
const usage = new Map();
let sortKey = "name";
let sortDir = 1;
const hosterFilter = new Set();
let statusFilter = "all";
let span = "24h";

const SORTS = ["name", "hoster", "price", "cpu", "ram", "charge"];
const FIRST_DIR = { name: 1, hoster: 1, price: -1, cpu: -1, ram: -1, charge: 1 };

export function resetServers() {
  list = null;
  pages.clear();
  usage.clear();
}

function fetchList(ctx) {
  if (listLoading) return;
  listLoading = true;
  api("/api/servers")
    .then((data) => {
      list = data;
      ctx.render();
    })
    .catch(ctx.fail)
    .finally(() => {
      listLoading = false;
    });
}

// The list

function monthValue(s) {
  if (s.monthly_converted) return Number(s.monthly_converted.amount);
  return s.monthly ? Number(s.monthly.amount) : null;
}

function sorted(servers) {
  const key = {
    name: (s) => s.name.toLowerCase(),
    hoster: (s) => `${s.hoster.toLowerCase()}\u0000${s.name.toLowerCase()}`,
    price: monthValue,
    cpu: (s) => s.cpu,
    ram: (s) => s.ram,
    charge: (s) => s.days_to_charge,
  }[sortKey];
  return [...servers].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    if (x == null && y == null) return a.name.localeCompare(b.name);
    if (x == null) return 1;
    if (y == null) return -1;
    const order = typeof x === "string" ? x.localeCompare(y) : x - y;
    return order * sortDir || a.name.localeCompare(b.name);
  });
}

function matches(s) {
  if (hosterFilter.size && !hosterFilter.has(s.account_id)) return false;
  if (statusFilter === "problems") return s.level === "warn" || s.level === "bad";
  if (statusFilter === "running") return s.status === "running";
  if (statusFilter === "stopped") return s.status !== "running";
  if (statusFilter === "load") return s.high_load;
  return true;
}

function chargeText(s) {
  const n = s.days_to_charge;
  if (n == null) return s.billing === "hourly" ? t("server.hourly", { amount: money(s.price) }) : "";
  if (n < 0) return t("server.overdue");
  if (n === 0) return t("server.today");
  return t("server.in_days", { n: days(n) });
}

function chargeLevel(s, limit) {
  const n = s.days_to_charge;
  if (n == null) return "";
  const renews = s.autorenew === "on" || s.autopay;
  if (renews) return "";
  if (n <= 1) return "bad";
  return n <= limit ? "warn" : "";
}

function meter(label, value, limit) {
  if (value == null) return null;
  const level = value >= limit ? "warn" : "";
  return h(
    "span",
    { class: `meter ${level}` },
    h("span", { class: "meter-label" }, label),
    h("span", { class: "meter-bar" }, h("i", { style: `width:${Math.min(100, Math.max(2, value))}%` })),
    h("b", {}, percent(value)),
  );
}

function meters(s, limits) {
  const parts = [
    meter("CPU", s.cpu, limits.load),
    meter("RAM", s.ram, limits.load),
    meter(t("chart.disk").split(",")[0], s.disk, limits.disk),
  ].filter(Boolean);
  if (!parts.length) return null;
  return h("div", { class: "meters" }, parts);
}

function serverRow(s, limits, open) {
  const others = s.problems;
  const level = s.level;
  return h(
    "button",
    { type: "button", class: "row server", onclick: () => open(s) },
    h(
      "div",
      { class: "row-top" },
      h("i", { class: `dot ${level}` }),
      h("span", { class: "name" }, s.name),
      h("span", { class: "price" }, s.monthly ? t("server.month", { amount: money(s.monthly) }) : "—"),
    ),
    h(
      "div",
      { class: "row-sub" },
      h("span", { class: "where" }, [s.hoster, s.ip].filter(Boolean).join(" · ")),
      h("span", { class: `when ${chargeLevel(s, limits.days)}` }, chargeText(s)),
    ),
    meters(s, limits),
    level === "off" && h("div", { class: "row-note muted" }, t("level.off")),
    others.length > 0 && h("div", { class: `row-note ${level}` }, others.join(" · ")),
    h("span", { class: "chevron", "aria-hidden": "true" }, "›"),
  );
}

export function serversView(ctx) {
  if (!list) {
    fetchList(ctx);
    return [header(t("tab.servers")), loading()];
  }
  const all = list.servers;
  const refresh = h("button", { class: "icon-button", type: "button", "aria-label": t("refresh"), onclick: () => { list = null; ctx.render(); } }, "↻");
  if (!all.length) return [header(t("tab.servers"), refresh), message(t("nothing"), t("empty.text"))];
  const shown = sorted(all.filter(matches));
  const pickSort = h(
    "select",
    {
      class: "select",
      "aria-label": t("sort.label"),
      onchange: (e) => {
        sortKey = e.target.value;
        sortDir = FIRST_DIR[sortKey];
        ctx.render();
      },
    },
    SORTS.map((key) => h("option", { value: key, selected: key === sortKey }, t(`sort.${key}`))),
  );
  const direction = h(
    "button",
    {
      type: "button",
      class: "icon-button small",
      "aria-label": sortDir > 0 ? "↑" : "↓",
      onclick: () => {
        sortDir = -sortDir;
        ctx.render();
      },
    },
    sortDir > 0 ? "↑" : "↓",
  );
  const filters = [];
  if (list.hosters.length > 1) {
    filters.push(
      chips(
        list.hosters.map((x) => [x.account_id, x.title]),
        hosterFilter,
        (id) => {
          if (hosterFilter.has(id)) hosterFilter.delete(id);
          else hosterFilter.add(id);
          ctx.render();
        },
        { multi: true },
      ),
    );
  }
  filters.push(
    chips(
      ["all", "problems", "running", "stopped", "load"].map((k) => [k, t(`filter.${k}`)]),
      statusFilter,
      (value) => {
        statusFilter = value;
        ctx.render();
      },
    ),
  );
  const limits = list.limits;
  return [
    header(t("tab.servers"), refresh),
    h("div", { class: "filters" }, filters),
    h(
      "div",
      { class: "toolbar" },
      h("span", { class: "muted small" }, t("servers.count", { shown: shown.length, total: count(all.length, "server") })),
      h("span", { class: "sort" }, pickSort, direction),
    ),
    shown.length
      ? h("div", { class: "card list-card" }, shown.map((s) => serverRow(s, limits, ctx.open)))
      : h(
          "div",
          { class: "card empty" },
          h("p", { class: "muted" }, t("nothing.filters")),
          h("button", { type: "button", class: "link", onclick: () => { hosterFilter.clear(); statusFilter = "all"; ctx.render(); } }, t("reset")),
        ),
  ];
}

// A server's page

function keyOf(acc, sid) {
  return `${acc}/${sid}`;
}

function path(acc, sid) {
  return `/api/servers/${acc}/${encodeURIComponent(sid)}`;
}

function fetchPage(ctx, acc, sid) {
  const key = keyOf(acc, sid);
  pages.set(key, { loading: true });
  api(path(acc, sid))
    .then((data) => pages.set(key, { data }))
    .catch((error) => {
      if (error.status === 404) pages.set(key, { gone: true });
      else {
        pages.delete(key);
        ctx.fail(error);
      }
    })
    .finally(() => ctx.render());
}

function fetchUsage(ctx, acc, sid, range) {
  const key = `${keyOf(acc, sid)}@${range}`;
  usage.set(key, { loading: true });
  api(`${path(acc, sid)}/usage?range=${range}`)
    .then((data) => usage.set(key, { data }))
    .catch((error) => {
      usage.delete(key);
      if (error.status !== 404) ctx.fail(error);
    })
    .finally(() => ctx.render());
}

function levelText(s) {
  if (s.problems.length) return s.problems.join(" · ");
  if (s.high_load) return t("filter.load");
  if (s.level === "off") return t("level.off");
  if (s.level === "unknown") return t("level.unknown");
  return t(`status.${s.status}`);
}

function fact(label, value, cls) {
  return h("div", {}, h("dt", {}, label), h("dd", { class: cls }, value ?? "—"));
}

function priceText(s) {
  if (!s.price) return "—";
  if (s.billing === "hourly") return `${t("server.hourly", { amount: money(s.price, { exact: true }) })} · ${t("server.month", { amount: money(s.monthly) })}`;
  if (s.period_months && s.period_months > 1) return `${money(s.price)} / ${count(s.period_months, "month")} · ${t("server.month", { amount: money(s.monthly) })}`;
  return t("server.month", { amount: money(s.price, { exact: true }) });
}

function factsCard(s, limits) {
  const renews = s.autorenew === "on" || s.autopay;
  const charge = s.next_charge_at ? `${dayMonth(s.next_charge_at)} · ${chargeText(s)}` : s.billing === "hourly" ? t("server.hourly", { amount: money(s.price) }) : "—";
  return h(
    "section",
    { class: "card info" },
    h(
      "dl",
      { class: "facts" },
      fact(t("server.ip"), s.ip),
      fact(t("server.plan"), s.plan),
      fact(t("server.price"), priceText(s)),
      fact(renews || !s.next_charge_at ? t("server.next_charge") : t("server.ends"), charge, chargeLevel(s, limits.days)),
      fact(t("server.autorenew"), t(s.autorenew)),
      fact(t("server.autopay"), t(s.autopay ? "on" : "off")),
    ),
    meters(s, limits),
  );
}

function collecting(since) {
  return since ? t("collecting", { date: dayMonth(since) }) : t("collecting.none");
}

function lastValue(values) {
  for (let i = values.length - 1; i >= 0; i--) if (values[i] != null) return values[i];
  return null;
}

function chartBlock(after, u, { title, lines, percent: isPercent, unit }) {
  const usable = lines.filter((line) => line.values.some((v) => v != null));
  if (!usable.length) return null;
  const readout = h("span", { class: "readout" });
  const format = (v) => (v == null ? "—" : isPercent ? percent(v) : `${v.toFixed(v < 10 ? 2 : 1)}`);
  const idle = () => {
    readout.replaceChildren(
      ...usable.map((line, i) =>
        h("span", {}, usable.length > 1 && h("i", { class: "key", style: `background:${line.color}` }), `${i === 0 ? `${t("chart.latest")} ` : ""}${usable.length > 1 ? `${line.label} ` : ""}${format(lastValue(line.values))}${unit && !isPercent ? ` ${unit}` : ""}`),
      ),
    );
  };
  const at = (idx) => {
    if (idx == null) return idle();
    readout.replaceChildren(
      h("span", { class: "muted" }, moment(u.t[idx])),
      ...usable.map((line) => {
        const avg = line.values[idx];
        const peak = line.peak?.[idx];
        const text = peak != null && peak !== avg ? `${format(avg)} · ${t("chart.peak")} ${format(peak)}` : format(avg);
        return h("span", {}, usable.length > 1 && h("i", { class: "key", style: `background:${line.color}` }), usable.length > 1 ? `${line.label} ${text}` : text);
      }),
    );
  };
  idle();
  const box = h("div", { class: "chart" });
  after(() =>
    lineChart(box, {
      t: u.t,
      lines: usable,
      percent: isPercent,
      range: [Math.floor(Date.parse(u.start) / 1000), Math.floor(Date.parse(u.end) / 1000)],
      onCursor: at,
    }),
  );
  return h("div", { class: "chart-block" }, h("div", { class: "chart-head" }, h("h3", {}, title), readout), box);
}

function chartsCard(ctx, s, acc, sid, since) {
  const pick = segmented(
    ["24h", "7d", "30d", "1y"].map((r) => [r, t(`range.${r}`)]),
    span,
    (value) => {
      span = value;
      ctx.render();
    },
  );
  const body = [];
  if (!s.usage_supported) {
    body.push(h("p", { class: "muted" }, t("server.usage_unsupported")));
  } else {
    const key = `${keyOf(acc, sid)}@${span}`;
    const entry = usage.get(key);
    if (!entry) fetchUsage(ctx, acc, sid, span);
    if (!entry?.data) {
      body.push(h("p", { class: "muted chart-wait" }, t("loading")));
    } else {
      const u = entry.data;
      const peaks = u.resolution !== "samples";
      const line = (name, color, label) => ({
        label,
        color,
        values: u.series[name].avg,
        peak: peaks ? u.series[name].max : null,
      });
      const blocks = [
        chartBlock(ctx.after, u, { title: t("chart.cpu"), lines: [line("cpu", "var(--accent)", "CPU")], percent: true }),
        chartBlock(ctx.after, u, { title: t("chart.ram"), lines: [line("ram", "var(--accent)", "RAM")], percent: true }),
        chartBlock(ctx.after, u, { title: t("chart.disk"), lines: [line("disk", "var(--accent)", t("chart.disk"))], percent: true }),
        chartBlock(ctx.after, u, {
          title: t("chart.net"),
          lines: [
            { ...line("net_in", "var(--c1)", t("chart.in")), peak: null },
            { ...line("net_out", "var(--c2)", t("chart.out")), peak: null },
          ],
          percent: false,
        }),
      ].filter(Boolean);
      if (blocks.length) {
        body.push(...blocks);
        if (peaks) body.push(h("p", { class: "muted small legend-note" }, h("i", { class: "band-key" }), `${t("chart.avg")} · ${t("chart.peak")}`));
      } else {
        body.push(h("p", { class: "muted empty-chart" }, collecting(since)));
      }
    }
  }
  return h("section", { class: "card charts" }, h("div", { class: "card-head" }, h("h2", {}, t("server.charts"))), pick, body);
}

function eventValue(kind, value) {
  if (value == null || value === "") return "—";
  if (kind === "price") {
    const [amount, currency] = value.split(" ");
    return currency ? money({ amount, currency }, { exact: true }) : value;
  }
  if (kind === "status") return t(`status.${value}`);
  if (kind === "autorenew") return t(value);
  return value;
}

function historyCard(events) {
  const rows = events.map((e) => {
    let what;
    if (e.kind === "appeared") what = `${t("event.appeared")}: ${e.new ?? ""}`;
    else if (e.kind === "removed") what = t("event.removed");
    else what = `${t(`event.${e.kind}`)}: ${eventValue(e.kind, e.old)} → ${eventValue(e.kind, e.new)}`;
    return h("li", { class: "event" }, h("span", { class: "when muted" }, dateShort(e.at)), h("span", {}, what));
  });
  return card(
    t("server.history"),
    rows.length ? h("ul", { class: "list events" }, rows) : h("p", { class: "muted" }, t("server.history.none")),
  );
}

function costCard(cost) {
  if (cost.basis === "none") return card(t("server.cost"), h("p", { class: "muted" }, t("server.cost.none")));
  const values = cost.months.map((m) => sumValue(m.amount) ?? 0);
  const max = Math.max(...values, 1);
  const note = cost.basis === "hoster" ? t("server.cost.hoster") : t("server.cost.estimate", { date: dateShort(cost.since) });
  return h(
    "section",
    { class: "card cost" },
    h("h2", {}, t("server.cost")),
    h("div", { class: "cost-total" }, h("b", {}, sum(cost.total)), cost.basis === "estimate" && badge(t("estimate"), "estimate")),
    h("p", { class: "muted small" }, note),
    h(
      "div",
      { class: "cost-months" },
      [...cost.months].reverse().map((m, i, all) =>
        h(
          "div",
          { class: "bar-row" },
          h("div", { class: "bar-label" }, h("span", {}, monthName(m.month, { year: true })), h("b", {}, sum(m.amount))),
          h("div", { class: "bar" }, h("span", { class: "now", style: `width:${Math.max(2, ((values[all.length - 1 - i] ?? 0) / max) * 100)}%` })),
        ),
      ),
    ),
  );
}

export function serverView(ctx, acc, sid) {
  const key = keyOf(acc, sid);
  const entry = pages.get(key);
  if (!entry) fetchPage(ctx, acc, sid);
  if (!entry || entry.loading) return [header("", null, ctx.back), loading()];
  if (entry.gone) return [header(t("tab.servers"), null, ctx.back), message(t("nothing"), t("server.gone"))];
  const page = entry.data;
  const s = page.server;
  const limits = page.limits;
  return [
    header(s.name, null, ctx.back),
    h(
      "div",
      { class: "summary" },
      h("span", { class: `pill ${s.level}` }, h("i", { class: `dot ${s.level}` }), levelText(s)),
      h("span", { class: "muted" }, s.hoster),
    ),
    factsCard(s, limits),
    chartsCard(ctx, s, acc, sid, page.collecting_since),
    costCard(page.cost),
    historyCard(page.events),
  ];
}

export function haptic() {
  tg?.HapticFeedback?.impactOccurred?.("light");
}
