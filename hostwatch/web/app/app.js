// hostwatch Mini App: read-only views of the bot's data. No build step, no framework.
import { destroyCharts } from "./charts.js";
import { count, setLang, t } from "./i18n.js";
import { journalView, resetJournal } from "./journal.js";
import { moneyView, resetMoney } from "./money.js";
import { resetServers, serverView, serversView } from "./servers.js";
import { resetTips, tipsView } from "./tips.js";
import { clock, h, header, icon, message, money, monthName, native, setZone, sum, sumValue, tg } from "./ui.js";

const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");

const ICONS = {
  overview: "M4 13h6V4H4v9zm0 7h6v-5H4v5zm10 0h6v-9h-6v9zm0-16v5h6V4h-6z",
  servers: "M4 5h16v5H4V5zm0 9h16v5H4v-5zm3-6.5h.01M7 16.5h.01",
  money:
    "M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3 2 2.7 4.5 3 4.5 1.3 4.5 3-2 3-4.5 3-4.5-1.3-4.5-3",
  journal: "M6 4h12v16H6V4zm3 4h6m-6 4h6m-6 4h4",
  tips: "M9 18h6m-5 3h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z",
};
const TABS = ["overview", "servers", "money", "journal", "tips"];

let tab = "overview";
// The open server page: [account id, server id], or null.
let server = null;
let data = null;
let state = "loading";
let pending = [];

// What the tabs get: re-render, open and leave a server page, report a failed request,
// and run code once the new nodes are in the page (charts need their width).
const ctx = {
  render: () => render(),
  open: (s) => openServer(s.account_id, s.server_id),
  back: () => closeServer(),
  fail: (error) => {
    state = error?.status === 401 || error?.status === 429 ? "denied" : "error";
    render();
  },
  after: (fn) => pending.push(fn),
};

// Overview

function statePill(d) {
  if (!d.problems.length) return h("span", { class: "pill ok" }, h("i", { class: "dot ok" }), t("state.ok"));
  const level = d.problems.some((p) => p.level === "bad") ? "bad" : "warn";
  return h("span", { class: `pill ${level}` }, h("i", { class: `dot ${level}` }), count(d.problems.length, "problem"));
}

function problemsCard(problems) {
  if (!problems.length) return null;
  return h(
    "section",
    { class: "card problems" },
    h("h2", {}, t("problems.title")),
    h(
      "ul",
      { class: "list" },
      problems.map((p) =>
        h(
          "li",
          { class: "problem" },
          h("i", { class: `dot ${p.level}` }),
          h(
            "div",
            {},
            h("div", { class: "where" }, p.server ? `${p.hoster} · ${p.server}` : p.hoster),
            h("div", { class: "what" }, p.what),
          ),
        ),
      ),
    ),
  );
}

function stat(label, value, note) {
  return h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), note && h("div", { class: "note" }, note));
}

function spendCard(spend) {
  const now = sumValue(spend.this_month);
  const before = sumValue(spend.last_month);
  const empty = !spend.this_month.parts.length && !spend.last_month.parts.length;
  const body = [];
  if (empty) {
    body.push(h("p", { class: "muted" }, t("spend.none")));
  } else {
    const max = Math.max(now ?? 0, before ?? 0) || 1;
    const bar = (label, s, value, cls) =>
      h(
        "div",
        { class: "bar-row" },
        h("div", { class: "bar-label" }, h("span", {}, label), h("b", {}, sum(s))),
        h("div", { class: "bar" }, h("span", { class: cls, style: `width:${value == null ? 0 : Math.max(2, (value / max) * 100)}%` })),
      );
    body.push(bar(`${t("spend.this")} · ${monthName(spend.month)}`, spend.this_month, now, "now"));
    body.push(bar(`${t("spend.last")} · ${monthName(spend.previous_month)}`, spend.last_month, before, "before"));
    if (now != null && before) {
      const diff = Math.round(((now - before) / before) * 100);
      const words = diff > 0 ? t("spend.more") : diff < 0 ? t("spend.less") : t("spend.same");
      body.push(h("p", { class: "muted small" }, diff === 0 ? words : `${diff > 0 ? "+" : "−"}${Math.abs(diff)}% · ${words}`));
    }
  }
  return h("section", { class: "card spend" }, h("h2", {}, t("spend.title")), body);
}

function hosterCard(x) {
  const status = x.status === "relogin" || x.status === "error" ? t(`status.${x.status}`) : null;
  return h(
    "article",
    { class: `card hoster ${x.level}` },
    h(
      "div",
      { class: "hoster-head" },
      h("i", { class: `dot ${x.level}` }),
      h("span", { class: "name" }, x.title),
      h("span", { class: "servers" }, count(x.servers, "server")),
    ),
    h(
      "div",
      { class: "hoster-money" },
      h("span", { class: "balance" }, x.balance ? money(x.balance, { exact: true }) : t("hoster.no_balance")),
      (status || x.lasts) && h("span", { class: `lasts ${x.money_level}` }, status || x.lasts),
    ),
    h(
      "dl",
      { class: "facts" },
      h("div", {}, h("dt", {}, t("hoster.monthly")), h("dd", {}, native(x.monthly))),
      h("div", {}, h("dt", {}, t("hoster.spend")), h("dd", {}, native(x.spend_month))),
    ),
  );
}

function overview(d) {
  const sub = `${count(d.hosters_count, "hoster")} · ${count(d.servers_count, "server")}`;
  const refresh = h("button", { class: "icon-button", type: "button", "aria-label": t("refresh"), onclick: reload }, "↻");
  if (!d.hosters.length) {
    return [header(t("tab.overview"), refresh), message(t("empty.title"), t("empty.text"))];
  }
  const foot = [t("updated", { time: clock(d.generated_at) })];
  if (d.rates_date) {
    const [y, m, day] = d.rates_date.split("-");
    foot.push(t("rates", { date: `${day}.${m}.${y}` }));
  }
  return [
    header(t("tab.overview"), refresh),
    h("div", { class: "summary" }, statePill(d), h("span", { class: "muted" }, sub)),
    problemsCard(d.problems),
    h("div", { class: "grid" }, stat(t("balances"), sum(d.totals.balances)), stat(t("monthly"), sum(d.totals.monthly))),
    spendCard(d.spend),
    h("h2", { class: "section" }, t("hosters.title")),
    d.hosters.map(hosterCard),
    h("p", { class: "foot" }, foot.join(" · ")),
  ];
}

// Pages

function content() {
  if (state === "outside") return message(t("outside.title"), t("outside.text"));
  if (state === "denied") return message(t("denied.title"), t("denied.text"));
  if (state === "error") return message(t("error.title"), t("error.text"), { label: t("retry"), run: reload });
  if (state === "loading" || !data) return h("p", { class: "loading" }, t("loading"));
  if (tab === "overview") return overview(data);
  if (tab === "servers") return server ? serverView(ctx, ...server) : serversView(ctx);
  if (tab === "money") return moneyView(ctx);
  if (tab === "journal") return journalView(ctx);
  return tipsView(ctx);
}

function render() {
  destroyCharts();
  pending = [];
  const nodes = [content()].flat(Infinity).filter(Boolean);
  view.replaceChildren(...nodes);
  renderTabs();
  syncBackButton();
  const run = pending;
  pending = [];
  for (const fn of run) fn();
}

function renderTabs() {
  tabsEl.hidden = state === "outside" || state === "denied";
  tabsEl.replaceChildren(
    ...TABS.map((name) =>
      h(
        "button",
        {
          type: "button",
          role: "tab",
          class: name === tab ? "tab active" : "tab",
          "aria-selected": String(name === tab),
          onclick: () => {
            if (tab === name && !server) return;
            tab = name;
            server = null;
            tg?.HapticFeedback?.selectionChanged?.();
            window.scrollTo(0, 0);
            render();
          },
        },
        icon(ICONS[name]),
        h("span", {}, t(`tab.${name}`)),
      ),
    ),
  );
}

// Telegram's own «Back» in the header leaves a server page.
let backBound = false;
function syncBackButton() {
  const button = tg?.BackButton;
  if (!button) return;
  if (!backBound) {
    button.onClick(() => closeServer());
    backBound = true;
  }
  if (server && state === "ready") button.show();
  else button.hide();
}

let listScroll = 0;
// The tab a server page was opened from («Tips» links to servers); «Back» returns there.
let fromTab = "servers";
function openServer(acc, sid) {
  listScroll = window.scrollY;
  fromTab = tab;
  tab = "servers";
  server = [acc, sid];
  tg?.HapticFeedback?.impactOccurred?.("light");
  window.scrollTo(0, 0);
  render();
}

function closeServer() {
  if (!server) return;
  server = null;
  tab = fromTab;
  fromTab = "servers";
  render();
  window.scrollTo(0, listScroll);
}

// Data

function reload() {
  resetServers();
  resetMoney();
  resetJournal();
  resetTips();
  load();
}

async function load() {
  if (!tg?.initData) {
    state = "outside";
    render();
    return;
  }
  if (!data) {
    state = "loading";
    render();
  }
  try {
    const response = await fetch("/api/overview", {
      headers: { "X-Telegram-Init-Data": tg.initData },
      cache: "no-store",
    });
    if (response.status === 401 || response.status === 429) {
      state = "denied";
    } else if (!response.ok) {
      state = "error";
    } else {
      data = await response.json();
      setLang(data.lang);
      setZone(data.tz || "UTC");
      state = "ready";
    }
  } catch {
    state = "error";
  }
  render();
}

function applyTheme() {
  const scheme = tg?.colorScheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.scheme = scheme;
  try {
    tg?.setHeaderColor?.("secondary_bg_color");
    tg?.setBackgroundColor?.("secondary_bg_color");
    tg?.setBottomBarColor?.("secondary_bg_color");
  } catch {
    // Older clients have no such methods.
  }
  if (state === "ready") render();
}

// «📊 Charts» on a server card in the chat opens the app on that server: ?server=1:abc;
// ?tab=tips and the like open a tab.
const params = new URLSearchParams(location.search);
const wantedTab = params.get("tab");
if (TABS.includes(wantedTab)) tab = wantedTab;
const wanted = params.get("server");
if (wanted && /^\d+:.+/.test(wanted)) {
  const at = wanted.indexOf(":");
  tab = "servers";
  server = [Number(wanted.slice(0, at)), wanted.slice(at + 1)];
}

setLang(tg?.initDataUnsafe?.user?.language_code === "ru" ? "ru" : (navigator.language || "en").slice(0, 2));
applyTheme();
tg?.onEvent?.("themeChanged", applyTheme);
tg?.ready?.();
tg?.expand?.();
load();
