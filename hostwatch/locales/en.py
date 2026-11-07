MESSAGES: dict[str, str] = {
    "greeting": "Hi, {name}! I keep an eye on your servers.",
    "balance.low": "{hoster} balance is down to {amount}. It lasts until {date}.",
    "forecast.method.hoster_hours": "Method: the hoster's own estimate.",
    "forecast.method.hourly_rate": "Method: balance divided by the sum of hourly rates.",
    "forecast.method.charges": "Method: upcoming charges of servers that renew ({count}).",
    "forecast.method.mixed": (
        "Method: hourly rates plus upcoming charges of servers that renew ({count})."
    ),
    "forecast.method.no_spending": "Method: no charges expected.",
    "forecast.no_spending": "Nothing is spending the balance.",
    "forecast.beyond_horizon": "The money lasts more than a year.",
    "forecast.until": "Lasts until {date}.",
    "forecast.charge": "{date}: {amount} will be charged ({server}).",
    "forecast.more_charges": "{count} more charges.",
    "forecast.shortfall": "{date}: {amount} short for {server}.",
    "forecast.not_renewing": "{server} does not renew.",
    "forecast.not_renewing_until": "{server} does not renew and ends on {date}.",
    "forecast.mismatch": (
        "Real spending over 14 days is {actual}, the forecast says {predicted}."
        " The forecast may be off."
    ),
}
