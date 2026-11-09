// «Journal»: every action of the owner, the autopay and the bot, newest first, by day,
// with filters by who, what, hoster and dates. More come as the list scrolls.
import { t } from "./i18n.js";
import { api, chips, clock, dayLong, h, header, isoDay, loading } from "./ui.js";

const PAGE = 30;
let actor = "all";
let group = "all";
let account = "all";
let since = "";
let until = "";
let state = null;
let hosters = null;
let observer = null;

export function resetJournal() {
  state = null;
  hosters = null;
}

function query(offset) {
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (actor !== "all") params.set("actor", actor);
  if (group !== "all") params.set("group", group);
  if (account !== "all") params.set("account", account);
  if (since) params.set("since", since);
  if (until) params.set("until", until);
  return `/api/journal?${params}`;
}

function key() {
  return [actor, group, account, since, until].join("|");
}

function fetchPage(ctx, more = false) {
  if (!more) state = { key: key(), items: [], hasMore: false, loading: true };
  else if (state.loading) return;
  else state.loading = true;
  const mine = state;
  api(query(more ? mine.items.length : 0))
    .then((body) => {
      if (state !== mine) return;
      mine.items.push(...body.entries);
      mine.hasMore = body.has_more;
    })
    .catch(ctx.fail)
    .finally(() => {
      mine.loading = false;
      if (state === mine) ctx.render();
    });
}

function fetchHosters(ctx) {
  hosters = [];
  api("/api/servers")
    .then((body) => {
      hosters = body.hosters;
      ctx.render();
    })
    .catch(() => {});
}

function dayTitle(day, today, yesterday) {
  if (day === today) return t("journal.today");
  if (day === yesterday) return t("journal.yesterday");
  return dayLong(`${day}T12:00:00Z`);
}

function entryRow(e) {
  const who = t(`journal.who.${e.actor}`);
  // A held autopay is kept as not done; it waits for the owner, it did not fail.
  const held = e.action === "autopay_hold";
  const failed = !e.ok && !held;
  return h(
    "li",
    { class: "entry" },
    h("span", { class: "time muted" }, clock(e.at)),
    h("i", { class: `dot ${held ? "warn" : failed ? "bad" : "ok"}`, title: failed ? t("journal.failed") : "" }),
    h(
      "div",
      { class: "grow" },
      h("div", { class: "what" }, e.text),
      h("div", { class: "muted small" }, [e.hoster, who, failed && t("journal.failed")].filter(Boolean).join(" · ")),
    ),
  );
}

function dateInput(label, value, set) {
  return h(
    "label",
    { class: "date" },
    h("span", { class: "muted small" }, label),
    h("input", {
      type: "date",
      value,
      max: isoDay(new Date()),
      onchange: (e) => set(e.target.value),
    }),
  );
}

export function journalView(ctx) {
  if (key() !== state?.key) fetchPage(ctx);
  if (hosters === null) fetchHosters(ctx);
  const filters = [
    chips(["all", "owner", "autopay", "system"].map((k) => [k, t(k === "all" ? "journal.actor.all" : `journal.who.${k}`)]), actor, (v) => {
      actor = v;
      ctx.render();
    }),
    chips(["all", "servers", "money", "accounts", "settings", "other"].map((k) => [k, t(`journal.group.${k}`)]), group, (v) => {
      group = v;
      ctx.render();
    }),
  ];
  const row = [];
  if (hosters && hosters.length > 1) {
    row.push(
      h(
        "select",
        {
          class: "select",
          onchange: (e) => {
            account = e.target.value;
            ctx.render();
          },
        },
        h("option", { value: "all", selected: account === "all" }, t("journal.hoster.all")),
        hosters.map((x) => h("option", { value: String(x.account_id), selected: account === String(x.account_id) }, x.title)),
      ),
    );
  }
  row.push(
    dateInput(t("journal.from"), since, (v) => {
      since = v;
      if (until && since && since > until) until = since;
      ctx.render();
    }),
    dateInput(t("journal.to"), until, (v) => {
      until = v;
      if (since && until && since > until) since = until;
      ctx.render();
    }),
  );
  const filtered = actor !== "all" || group !== "all" || account !== "all" || since || until;
  if (filtered) {
    row.push(
      h("button", { type: "button", class: "link", onclick: () => { actor = group = account = "all"; since = until = ""; ctx.render(); } }, t("reset")),
    );
  }
  filters.push(h("div", { class: "filter-row" }, row));

  const body = [];
  if (!state.items.length && state.loading) body.push(loading());
  else if (!state.items.length) {
    body.push(h("div", { class: "card empty" }, h("p", { class: "muted" }, filtered ? t("nothing.filters") : t("journal.none"))));
  } else {
    const today = isoDay(new Date());
    const yesterday = isoDay(new Date(Date.now() - 86400000));
    const days = new Map();
    for (const e of state.items) {
      const day = isoDay(e.at);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(e);
    }
    for (const [day, items] of days) {
      body.push(h("h2", { class: "section" }, dayTitle(day, today, yesterday)));
      body.push(h("ul", { class: "card list journal" }, items.map(entryRow)));
    }
    if (state.hasMore) {
      const more = h("button", { type: "button", class: "more", onclick: () => fetchPage(ctx, true) }, state.loading ? t("loading") : t("more"));
      body.push(more);
      // Infinite scroll: the next page comes as the button scrolls into view.
      ctx.after(() => {
        observer?.disconnect();
        if (!("IntersectionObserver" in window)) return;
        observer = new IntersectionObserver((seen) => {
          if (seen.some((x) => x.isIntersecting) && !state.loading) fetchPage(ctx, true);
        }, { rootMargin: "200px" });
        observer.observe(more);
      });
    }
  }
  return [header(t("tab.journal")), h("div", { class: "filters" }, filters), body];
}
