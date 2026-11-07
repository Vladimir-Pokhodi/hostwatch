MESSAGES: dict[str, str] = {
    "greeting": "Привет, {name}! Я слежу за вашими серверами.",
    "balance.low": "На балансе {hoster} осталось {amount}. Хватит до {date}.",
    "forecast.method.hoster_hours": "Метод: срок, который считает сам хостер.",
    "forecast.method.hourly_rate": "Метод: баланс, делённый на сумму почасовых ставок.",
    "forecast.method.charges": "Метод: будущие списания по серверам с продлением ({count}).",
    "forecast.method.mixed": (
        "Метод: почасовые ставки и будущие списания по серверам с продлением ({count})."
    ),
    "forecast.method.no_spending": "Метод: списаний не ожидается.",
    "forecast.no_spending": "Баланс не тратится.",
    "forecast.beyond_horizon": "Денег хватит больше чем на год.",
    "forecast.until": "Хватит до {date}.",
    "forecast.charge": "{date} спишется {amount} ({server}).",
    "forecast.more_charges": "И ещё списаний: {count}.",
    "forecast.shortfall": "{date} не хватит {amount} на {server}.",
    "forecast.not_renewing": "{server} не продлевается.",
    "forecast.not_renewing_until": "{server} не продлевается, закончится {date}.",
    "forecast.mismatch": (
        "Реальный расход за 14 дней {actual}, по прогнозу {predicted}. Прогноз может быть неточным."
    ),
}
