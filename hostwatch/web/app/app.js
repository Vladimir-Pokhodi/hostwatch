// hostwatch Mini App: read-only views of the bot's data. No build step, no framework.
import { count, lang, setLang, t } from "./i18n.js";

const tg = window.Telegram?.WebApp;
const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");

const ICONS = {
  overview: "M4 13h6V4H4v9zm0 7h6v-5H4v5zm10 0h6v-9h-6v9zm0-16v5h6V4h-6z",
  servers:
    "M4 5h16v5H4V5zm0 9h16v5H4v-5zm3-6.5h.01M7 16.5h.01",
  money:
    "M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3 2 2.7 4.5 3 4.5 1.3 4.5 3-2 3-4.5 3-4.5-1.3-4.5-3",
  journal: "M6 4h12v16H6V4zm3 4h6m-6 4h6m-6 4h4",
  tips: "M9 18h6m-5 3h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z",
};
const TABS = ["overview", "servers", "money", "journal", "tips"];

let tab = "overview";
let data = null;
let state = "loading";

// Elements

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

// Numbers and dates

function money(amount, { exact = false } = {}) {
  if (!amount) return "—";
  const value = Number(amount.amount);
  const digits = exact ? 2 : Math.abs(value) >= 100 ? 0 : 2;
  try {
    return new Intl.NumberFormat(lang(), {
      style: "currency",
      currencyDisplay: "narrowSymbol",
      currency: amount.currency,
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  } catch {
    return `${value.toFixed(digits)} ${amount.currency}`;
  }
}

// A sum of several currencies: one number when the rates allow it.
function sum(s, options) {
  if (!s) return "—";
  if (s.total) return money(s.total, options);
  if (!s.parts.length) return "—";
  return s.parts.map((p) => money(p, options)).join(" + ");
}

// A hoster's own sums: in its own currency when it has one, like its balance.
function native(s) {
  if (!s || !s.parts.length) return "—";
  return s.parts.length === 1 ? money(s.parts[0]) : sum(s);
}

function sumValue(s) {
  if (!s) return null;
  if (s.total) return Number(s.total.amount);
  if (s.parts.length === 0) return 0;
  return s.parts.length === 1 ? Number(s.parts[0].amount) : null;
}

function monthName(month) {
  const [year, number] = month.split("-").map(Number);
  const name = new Intl.DateTimeFormat(lang(), { month: "long" }).format(
    new Date(Date.UTC(year, number - 1, 15)),
  );
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function clock(iso) {
  return new Intl.DateTimeFormat(lang(), { hour: "2-digit", minute: "2-digit" }).format(
    new Date(iso),
  );
}

// Theme: Telegram's colours, light or dark

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
}

// Screens

function message(title, text, action) {
  return h(
    "section",
    { class: "message" },
    h("h1", {}, title),
    h("p", {}, text),
    action && h("button", { class: "button", type: "button", onclick: action.run }, action.label),
  );
}

function header(title, extra) {
  return h("header", { class: "head" }, h("h1", {}, title), extra);
}

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
  const refresh = h("button", { class: "icon-button", type: "button", "aria-label": t("refresh"), onclick: load }, "↻");
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

function soon() {
  return [header(t(`tab.${tab}`)), message(t("soon.title"), t("soon.text"))];
}

function render() {
  view.replaceChildren();
  let content;
  if (state === "outside") content = message(t("outside.title"), t("outside.text"));
  else if (state === "denied") content = message(t("denied.title"), t("denied.text"));
  else if (state === "error") content = message(t("error.title"), t("error.text"), { label: t("retry"), run: load });
  else if (tab !== "overview") content = soon();
  else if (state === "loading" || !data) content = h("p", { class: "loading" }, t("loading"));
  else content = overview(data);
  view.append(...[content].flat(Infinity).filter(Boolean));
  renderTabs();
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
            if (tab === name) return;
            tab = name;
            tg?.HapticFeedback?.selectionChanged?.();
            window.scrollTo(0, 0);
            render();
          },
        },
        icon(name),
        h("span", {}, t(`tab.${name}`)),
      ),
    ),
  );
}

// Data

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
      state = "ready";
    }
  } catch {
    state = "error";
  }
  render();
}

setLang(tg?.initDataUnsafe?.user?.language_code === "ru" ? "ru" : (navigator.language || "en").slice(0, 2));
applyTheme();
tg?.onEvent?.("themeChanged", applyTheme);
tg?.ready?.();
tg?.expand?.();
load();
