// «Tips»: what the bot's rules found in the load, the prices and the money, the most money
// first. The words come from the bot in its language; the app lays them out.
import { t } from "./i18n.js";
import { api, h, header, loading, money } from "./ui.js";

let data = null;
let busy = false;

export function resetTips() {
  data = null;
}

function fetchData(ctx) {
  if (busy) return;
  busy = true;
  api("/api/tips")
    .then((body) => {
      data = body;
      ctx.render();
    })
    .catch(ctx.fail)
    .finally(() => {
      busy = false;
    });
}

const ICON = {
  oversized: "↓",
  cramped: "↑",
  cheaper: "⇄",
  renewal: "⏳",
  shortfall: "₽",
  idle: "⏻",
  trend: "↗",
  no_data: "…",
};

function amountBadge(tip) {
  if (!tip.amount) return null;
  const sign = tip.impact === "saving" ? "−" : tip.impact === "cost" ? "+" : "";
  const text = `${sign}${money(tip.amount)}${tip.per_month ? t("tips.per_month") : ""}`;
  const label = t(`tips.impact.${tip.impact}`);
  return h("div", { class: `tip-amount ${tip.impact}` }, h("b", {}, text), h("span", {}, label));
}

function tipCard(ctx, tip) {
  const where = [tip.hoster, tip.server].filter(Boolean).join(" · ");
  const open = tip.server_id != null && tip.account_id != null;
  return h(
    "article",
    { class: `card tip ${tip.kind}` },
    h(
      "div",
      { class: "tip-head" },
      h("span", { class: `tip-icon ${tip.impact || "info"}`, "aria-hidden": "true" }, ICON[tip.kind] || "•"),
      h("div", { class: "grow" }, h("h3", {}, tip.title), where && h("div", { class: "muted small" }, where)),
      amountBadge(tip),
    ),
    h("p", { class: "tip-why" }, tip.why),
    tip.offer && h("p", { class: "tip-offer" }, tip.offer),
    tip.lines.length > 0 && h("ul", { class: "tip-lines" }, tip.lines.map((line) => h("li", {}, line))),
    tip.note && h("p", { class: "muted small tip-note" }, tip.note),
    open &&
      h(
        "button",
        { type: "button", class: "link tip-open", onclick: () => ctx.open(tip) },
        `${t("tips.open")} ›`,
      ),
  );
}

export function tipsView(ctx) {
  if (data === null) {
    fetchData(ctx);
    return [header(t("tab.tips")), loading()];
  }
  const body = [];
  if (!data.tips.length) {
    body.push(h("div", { class: "card empty" }, h("p", {}, t("tips.none")), h("p", { class: "muted small" }, t("tips.none.text"))));
  } else {
    body.push(data.tips.map((tip) => tipCard(ctx, tip)));
  }
  const foot = [t("tips.foot")];
  if (data.no_catalog.length) foot.push(t("tips.no_catalog", { names: data.no_catalog.join(", ") }));
  body.push(h("p", { class: "foot" }, foot.join(" ")));
  return [header(t("tab.tips")), body];
}
