// «Money»: spending by month stacked by hoster, totals per hoster with the split by server,
// and every top-up and charge. Totals come from what the bot saw leave the balances; the
// hoster's own records only split them by server, so nothing is counted twice.
import { count, t } from "./i18n.js";
import {
  api,
  badge,
  card,
  chips,
  h,
  header,
  hosterColor,
  loading,
  message,
  money,
  moment,
  monthName,
  sum,
  sumValue,
} from "./ui.js";

const MONTHS = 12;
const PAGE = 30;
let data = null;
let busy = false;
let selected = null;
let hoster = "all";
let type = "all";
let events = null;

export function resetMoney() {
  data = null;
  events = null;
}

function fetchData(ctx) {
  if (busy) return;
  busy = true;
  api(`/api/money?months=${MONTHS}`)
    .then((body) => {
      data = body;
      ctx.render();
    })
    .catch(ctx.fail)
    .finally(() => {
      busy = false;
    });
}

function eventsQuery(offset) {
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (hoster !== "all") params.set("account", String(hoster));
  if (type === "hoster") params.set("source", "hoster");
  else if (type !== "all") params.set("kind", type);
  return `/api/money/events?${params}`;
}

function fetchEvents(ctx, more = false) {
  const key = `${hoster}|${type}`;
  if (!more) events = { key, items: [], hasMore: false, loading: true };
  else events.loading = true;
  const mine = events;
  api(eventsQuery(more ? mine.items.length : 0))
    .then((body) => {
      if (events !== mine) return;
      mine.items.push(...body.events);
      mine.hasMore = body.has_more;
    })
    .catch(ctx.fail)
    .finally(() => {
      mine.loading = false;
      if (events === mine) ctx.render();
    });
}

// The chart: one column a month, a segment a hoster

function niceMax(value) {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((s) => s * power >= value) ?? 10;
  return step * power;
}

function monthChart(ctx, d) {
  const order = d.hosters.map((x) => x.account_id);
  const values = d.months.map((m) => sumValue(m.total));
  const top = niceMax(Math.max(0, ...values.filter((v) => v != null)));
  const current = d.months.at(-1).month;
  const pick = selected && d.months.some((m) => m.month === selected) ? selected : current;
  const unconverted = d.months.some((m) => m.total.parts.length && !m.total.total);
  const columns = d.months.map((m) => {
    const total = sumValue(m.total);
    const segments = m.hosters
      .map((x) => ({ id: x.account_id, value: sumValue(x.charge) }))
      .filter((x) => x.value);
    const cls = ["col", m.partial && "partial", m.no_data && "nodata", m.month === pick && "sel"].filter(Boolean).join(" ");
    return h(
      "button",
      {
        type: "button",
        class: cls,
        "aria-label": `${monthName(m.month, { year: true })}: ${m.no_data ? t("money.no_data") : sum(m.total)}`,
        onclick: () => {
          selected = m.month;
          ctx.render();
        },
      },
      h(
        "span",
        { class: "stack", style: `height:${m.no_data || !total ? 0 : Math.max(2, (total / top) * 100)}%` },
        segments.map((x) =>
          h("i", { style: `flex:${x.value} 0 0;background:${hosterColor(order.indexOf(x.id))}` }),
        ),
      ),
      h("span", { class: "col-label" }, `${monthName(m.month, { short: true }).slice(0, 3)}${m.partial ? "*" : ""}`),
    );
  });
  const month = d.months.find((m) => m.month === pick);
  const split = month.hosters
    .filter((x) => x.charge.parts.length)
    .map((x) => {
      const ref = d.hosters.find((y) => y.account_id === x.account_id);
      return h(
        "li",
        {},
        h("i", { class: "key", style: `background:${hosterColor(order.indexOf(x.account_id))}` }),
        h("span", { class: "grow" }, ref?.title ?? ""),
        h("b", {}, sum(x.charge)),
      );
    });
  const readout = h(
    "div",
    { class: "month-readout" },
    h(
      "div",
      { class: "month-title" },
      h("span", {}, monthName(pick, { year: true })),
      h("b", {}, month.no_data ? t("money.no_data") : sum(month.total)),
    ),
    (month.partial || month.no_data) && h("div", { class: "muted small" }, month.no_data ? t("collecting.none") : t("money.partial")),
    split.length > 0 && h("ul", { class: "list split" }, split),
  );
  const legend = d.hosters.length > 1
    ? h(
        "div",
        { class: "legend" },
        d.hosters.map((x, i) => h("span", {}, h("i", { class: "key", style: `background:${hosterColor(i)}` }), x.title)),
      )
    : null;
  return h(
    "section",
    { class: "card" },
    h("h2", {}, t("money.chart")),
    readout,
    h(
      "div",
      { class: "bars-wrap" },
      h("span", { class: "bars-top muted" }, money({ amount: String(top), currency: d.currency })),
      h("div", { class: "bars" }, columns),
    ),
    legend,
    h("p", { class: "muted small note" }, t("money.partial_note")),
    unconverted && h("p", { class: "muted small note" }, t("money.unconverted")),
  );
}

function hostersCard(d) {
  const rows = d.hosters.map((x, i) =>
    h(
      "li",
      { class: "hoster-money-row" },
      h(
        "div",
        { class: "row-top" },
        h("i", { class: "key", style: `background:${hosterColor(i)}` }),
        h("span", { class: "name" }, x.title),
        h("b", {}, sum(x.charge)),
      ),
      x.topup.parts.length > 0 && h("div", { class: "muted small sub" }, t("money.topped", { amount: sum(x.topup) })),
      (x.servers.length > 0 || x.unassigned.parts.length > 0) &&
        h(
          "ul",
          { class: "list servers-split" },
          x.servers.map((s) =>
            h("li", {}, h("span", { class: "grow" }, s.name, s.estimate && badge(t("estimate"), "estimate")), h("span", {}, sum(s.charge))),
          ),
          x.unassigned.parts.length > 0 &&
            h("li", { class: "muted" }, h("span", { class: "grow" }, t("money.unassigned")), h("span", {}, sum(x.unassigned))),
        ),
    ),
  );
  return card(t("money.hosters"), h("ul", { class: "list" }, rows));
}

function eventRow(e) {
  const sign = e.kind === "topup" ? "+" : "−";
  const where = [e.hoster, e.server].filter(Boolean).join(" · ");
  const other = e.converted && e.converted.currency !== e.amount.currency ? money(e.converted) : null;
  return h(
    "li",
    { class: "money-event" },
    h(
      "div",
      { class: "grow" },
      h("div", {}, where || "—"),
      h("div", { class: "muted small" }, moment(e.at), e.description && e.source === "hoster" ? ` · ${e.description}` : ""),
    ),
    h(
      "div",
      { class: "amount" },
      h("b", { class: e.kind }, `${sign}${money(e.amount, { exact: true })}`),
      other && h("div", { class: "muted small" }, `≈ ${other}`),
    ),
  );
}

function eventsCard(ctx, d) {
  const key = `${hoster}|${type}`;
  if (!events || events.key !== key) fetchEvents(ctx);
  const filters = [];
  if (d.hosters.length > 1) {
    filters.push(
      chips([["all", t("journal.hoster.all")], ...d.hosters.map((x) => [x.account_id, x.title])], hoster, (value) => {
        hoster = value;
        ctx.render();
      }),
    );
  }
  filters.push(
    chips(["all", "topup", "charge", "hoster"].map((k) => [k, t(`money.type.${k}`)]), type, (value) => {
      type = value;
      ctx.render();
    }),
  );
  const body = [];
  if (events.items.length) body.push(h("ul", { class: "list" }, events.items.map(eventRow)));
  else if (!events.loading) body.push(h("p", { class: "muted" }, hoster === "all" && type === "all" ? t("money.events.none") : t("nothing.filters")));
  if (events.loading) body.push(h("p", { class: "muted center" }, t("loading")));
  else if (events.hasMore) body.push(h("button", { type: "button", class: "more", onclick: () => { fetchEvents(ctx, true); ctx.render(); } }, t("more")));
  return h("section", { class: "card" }, h("h2", {}, t("money.events")), h("div", { class: "filters in-card" }, filters), body);
}

export function moneyView(ctx) {
  const refresh = h("button", { class: "icon-button", type: "button", "aria-label": t("refresh"), onclick: () => { resetMoney(); ctx.render(); } }, "↻");
  if (!data) {
    fetchData(ctx);
    return [header(t("tab.money")), loading()];
  }
  if (!data.hosters.length) return [header(t("tab.money"), refresh), message(t("empty.title"), t("empty.text"))];
  return [
    header(t("tab.money"), refresh),
    h(
      "div",
      { class: "grid" },
      h("div", { class: "card stat" }, h("div", { class: "label" }, t("money.spent", { n: count(data.months.length, "month") })), h("div", { class: "value" }, sum(data.charge))),
      h("div", { class: "card stat" }, h("div", { class: "label" }, t("money.topups")), h("div", { class: "value" }, sum(data.topup))),
    ),
    monthChart(ctx, data),
    hostersCard(data),
    eventsCard(ctx, data),
  ];
}
