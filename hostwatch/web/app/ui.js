// Shared pieces of the Mini App: elements, numbers, dates in the owner's time zone, the API.
import { count, lang, t } from "./i18n.js";

export const tg = window.Telegram?.WebApp;

// Elements

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SVG = "http://www.w3.org/2000/svg";

export function icon(d) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", d);
  svg.append(path);
  return svg;
}

export function header(title, extra, back) {
  return h(
    "header",
    { class: back ? "head with-back" : "head" },
    back && h("button", { class: "back", type: "button", "aria-label": t("back"), onclick: back }, "‹"),
    h("h1", {}, title),
    extra,
  );
}

export function message(title, text, action) {
  return h(
    "section",
    { class: "message" },
    h("h1", {}, title),
    text && h("p", {}, text),
    action && h("button", { class: "button", type: "button", onclick: action.run }, action.label),
  );
}

export function loading() {
  return h("p", { class: "loading" }, t("loading"));
}

export function card(title, ...body) {
  return h("section", { class: "card" }, title && h("h2", {}, title), body);
}

// A row of chips that scrolls sideways; `items` are [value, label] pairs.
export function chips(items, active, pick, { multi = false } = {}) {
  return h(
    "div",
    { class: "chips", role: "group" },
    items.map(([value, label]) => {
      const on = multi ? active.has(value) : active === value;
      return h(
        "button",
        {
          type: "button",
          class: on ? "chip on" : "chip",
          "aria-pressed": String(on),
          onclick: () => {
            tg?.HapticFeedback?.selectionChanged?.();
            pick(value);
          },
        },
        label,
      );
    }),
  );
}

export function segmented(items, active, pick) {
  return h(
    "div",
    { class: "segmented", role: "tablist" },
    items.map(([value, label]) =>
      h(
        "button",
        {
          type: "button",
          role: "tab",
          class: value === active ? "seg on" : "seg",
          "aria-selected": String(value === active),
          onclick: () => {
            if (value === active) return;
            tg?.HapticFeedback?.selectionChanged?.();
            pick(value);
          },
        },
        label,
      ),
    ),
  );
}

export function badge(text, cls = "") {
  return h("span", { class: `badge ${cls}` }, text);
}

// Numbers

export function money(amount, { exact = false } = {}) {
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
export function sum(s, options) {
  if (!s) return "—";
  if (s.total) return money(s.total, options);
  if (!s.parts.length) return "—";
  return s.parts.map((p) => money(p, options)).join(" + ");
}

// A hoster's own sums: in its own currency when it has one, like its balance.
export function native(s) {
  if (!s || !s.parts.length) return "—";
  return s.parts.length === 1 ? money(s.parts[0]) : sum(s);
}

export function sumValue(s) {
  if (!s) return null;
  if (s.total) return Number(s.total.amount);
  if (s.parts.length === 0) return 0;
  return s.parts.length === 1 ? Number(s.parts[0].amount) : null;
}

export function number(value, digits = 0) {
  if (value == null) return "—";
  return new Intl.NumberFormat(lang(), { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value);
}

export function percent(value) {
  return value == null ? "—" : `${Math.round(value)}%`;
}

// Dates: always in the owner's time zone from the bot's settings

let zone = "UTC";

export function setZone(name) {
  try {
    new Intl.DateTimeFormat("en", { timeZone: name });
    zone = name;
  } catch {
    zone = "UTC";
  }
}

export function getZone() {
  return zone;
}

function fmt(options, value) {
  const date = value instanceof Date ? value : new Date(typeof value === "number" ? value * 1000 : value);
  return new Intl.DateTimeFormat(lang(), { timeZone: zone, ...options }).format(date);
}

export const clock = (v) => fmt({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" }, v);
export const dayMonth = (v) => fmt({ day: "2-digit", month: "2-digit" }, v);
export const dateShort = (v) => fmt({ day: "2-digit", month: "2-digit", year: "numeric" }, v);
export const dayLong = (v) => fmt({ day: "numeric", month: "long" }, v);
export const moment = (v) => `${dayMonth(v)} ${clock(v)}`;
export const weekday = (v) => fmt({ weekday: "short" }, v);
export const monthShort = (v) => fmt({ month: "short" }, v);

// "2026-11-09" of a moment, in the owner's zone.
export function isoDay(v) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(v instanceof Date ? v : new Date(v));
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function monthName(month, { year = false, short = false } = {}) {
  const [y, n] = month.split("-").map(Number);
  const options = { month: short ? "short" : "long", timeZone: "UTC" };
  if (year) options.year = "numeric";
  const name = new Intl.DateTimeFormat(lang(), options).format(new Date(Date.UTC(y, n - 1, 15)));
  return (name.charAt(0).toUpperCase() + name.slice(1)).replace(/\.$/, "");
}

export function days(n) {
  return count(n, "day");
}

// The API

export class ApiError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

export async function api(path) {
  const response = await fetch(path, {
    headers: { "X-Telegram-Init-Data": tg?.initData || "" },
    cache: "no-store",
  });
  if (!response.ok) throw new ApiError(response.status);
  return response.json();
}

// Categorical colours of hosters: fixed by the hoster's place in the list, never by rank.
export function hosterColor(index) {
  return `var(--c${(index % 8) + 1})`;
}
