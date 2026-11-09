// UI strings of the Mini App. The language is the bot's (`lang` in /api/overview);
// until the data comes, Telegram's language of the user.

const STRINGS = {
  ru: {
    "tab.overview": "Обзор",
    "tab.servers": "Серверы",
    "tab.money": "Деньги",
    "tab.journal": "Журнал",
    "tab.tips": "Советы",
    "soon.title": "Скоро",
    "soon.text": "Этот раздел появится в следующем обновлении.",
    "outside.title": "Откройте из Telegram",
    "outside.text": "Аналитика открывается кнопкой «📊 Аналитика» в чате с ботом.",
    "denied.title": "Нет доступа",
    "denied.text": "Откройте аналитику заново из чата с ботом.",
    "error.title": "Не удалось загрузить",
    "error.text": "Сервер не ответил. Попробуйте ещё раз.",
    "retry": "Повторить",
    "loading": "Загрузка…",
    "state.ok": "Всё в порядке",
    "state.problems": "{n}",
    "problems.title": "Проблемы",
    "balances": "На балансах",
    "monthly": "В месяц",
    "spend.title": "Расход",
    "spend.this": "Этот месяц",
    "spend.last": "Прошлый месяц",
    "spend.none": "Списаний пока не видно: история копится с момента обновления.",
    "spend.more": "больше, чем в прошлом",
    "spend.less": "меньше, чем в прошлом",
    "spend.same": "столько же, сколько в прошлом",
    "hosters.title": "Хостеры",
    "hoster.monthly": "в месяц",
    "hoster.spend": "в этом месяце",
    "hoster.no_balance": "нет данных",
    "status.relogin": "нужен вход",
    "status.error": "ошибка",
    "updated": "Обновлено {time}",
    "rates": "курс ЦБ на {date}",
    "refresh": "Обновить",
    "empty.title": "Хостеры не подключены",
    "empty.text": "Подключите первого в чате с ботом.",
    "plural.hoster": "хостер|хостера|хостеров",
    "plural.server": "сервер|сервера|серверов",
    "plural.problem": "проблема|проблемы|проблем",
  },
  en: {
    "tab.overview": "Overview",
    "tab.servers": "Servers",
    "tab.money": "Money",
    "tab.journal": "Journal",
    "tab.tips": "Tips",
    "soon.title": "Coming soon",
    "soon.text": "This section arrives in the next update.",
    "outside.title": "Open it from Telegram",
    "outside.text": "Analytics opens with the «📊 Analytics» button in the chat with the bot.",
    "denied.title": "No access",
    "denied.text": "Open analytics again from the chat with the bot.",
    "error.title": "Could not load",
    "error.text": "The server did not answer. Try again.",
    "retry": "Try again",
    "loading": "Loading…",
    "state.ok": "All good",
    "state.problems": "{n}",
    "problems.title": "Problems",
    "balances": "On balances",
    "monthly": "A month",
    "spend.title": "Spending",
    "spend.this": "This month",
    "spend.last": "Last month",
    "spend.none": "No charges seen yet: history builds up from the update on.",
    "spend.more": "more than last month",
    "spend.less": "less than last month",
    "spend.same": "the same as last month",
    "hosters.title": "Hosters",
    "hoster.monthly": "a month",
    "hoster.spend": "this month",
    "hoster.no_balance": "no data",
    "status.relogin": "login needed",
    "status.error": "error",
    "updated": "Updated {time}",
    "rates": "CBR rate of {date}",
    "refresh": "Refresh",
    "empty.title": "No hosters connected",
    "empty.text": "Connect the first one in the chat with the bot.",
    "plural.hoster": "hoster|hosters",
    "plural.server": "server|servers",
    "plural.problem": "problem|problems",
  },
};

let current = "ru";

export function setLang(lang) {
  current = STRINGS[lang] ? lang : "en";
  document.documentElement.lang = current;
}

export function lang() {
  return current;
}

export function t(key, params = {}) {
  const text = STRINGS[current][key] ?? STRINGS.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ""));
}

export function count(n, noun) {
  const forms = t(`plural.${noun}`).split("|");
  let form;
  if (current === "ru") {
    const d = n % 10;
    const h = n % 100;
    form = d === 1 && h !== 11 ? forms[0] : d >= 2 && d <= 4 && (h < 12 || h > 14) ? forms[1] : forms[2];
  } else {
    form = n === 1 ? forms[0] : forms[1];
  }
  return `${n} ${form}`;
}
