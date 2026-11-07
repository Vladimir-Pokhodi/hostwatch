from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hostwatch.core.forecast import Method, add_months, forecast
from hostwatch.core.models import (
    Autorenew,
    Balance,
    BillingModel,
    Money,
    Server,
    ServerKind,
    ServerStatus,
)

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def eur(amount: str) -> Money:
    return Money.of(amount, "EUR")


def balance(amount: str, hours_left: float | None = None) -> Balance:
    return Balance(money=eur(amount), hours_left=hours_left)


def periodic(
    name: str, price: str, due_in_days: int, autorenew: Autorenew = Autorenew.ON
) -> Server:
    return Server(
        external_id=name,
        name=name,
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="active",
        price=eur(price),
        period_months=1,
        next_charge_at=NOW + timedelta(days=due_in_days),
        autorenew=autorenew,
        billing=BillingModel.PERIODIC,
    )


def hourly(name: str, rate: str) -> Server:
    return Server(
        external_id=name,
        name=name,
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="on",
        price=eur(rate),
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.HOURLY,
    )


def test_hourly_uses_hoster_hours_left() -> None:
    result = forecast(balance("100", hours_left=72), [hourly("a", "1")], NOW)

    assert result.method is Method.HOSTER_HOURS
    assert result.runs_out_at == NOW + timedelta(hours=72)
    assert "hoster's own estimate" in result.explain("en")


def test_hourly_without_hours_left_divides_balance_by_rates() -> None:
    result = forecast(balance("100"), [hourly("a", "1.5"), hourly("b", "0.5")], NOW)

    assert result.method is Method.HOURLY_RATE
    assert result.runs_out_at == NOW + timedelta(hours=50)


def test_periodic_walks_charges() -> None:
    servers = [periodic("beaver", "4.50", 8), periodic("bear", "12.00", 16)]

    result = forecast(balance("20.00"), servers, NOW)

    assert result.method is Method.CHARGES
    assert [(c.server, c.at) for c in result.paid] == [
        ("beaver", NOW + timedelta(days=8)),
        ("bear", NOW + timedelta(days=16)),
    ]
    assert result.runs_out_at == datetime(2026, 12, 15, 12, 0, tzinfo=UTC)
    assert result.shortfall is not None
    assert result.shortfall.server == "beaver"
    assert result.shortfall.missing == eur("1.00")
    text = result.explain("ru")
    assert "Хватит до 15.12." in text
    assert "15.11 спишется €4.50 (beaver)." in text
    assert "15.12 не хватит €1.00 на beaver." in text


def test_shortfall_on_second_charge() -> None:
    result = forecast(balance("10"), [periodic("bear", "6", 3)], NOW)

    assert len(result.paid) == 1
    assert result.shortfall is not None
    assert result.shortfall.at == add_months(NOW + timedelta(days=3), 1)
    assert result.shortfall.missing == eur("2")


def test_mixed_hourly_and_periodic() -> None:
    servers = [hourly("cloud", "0.1"), periodic("dedic", "50", 2)]

    result = forecast(balance("100"), servers, NOW)

    # 48 h drain 4.80, charge 50, then 45.20 lasts 452 h before the next charge.
    assert result.method is Method.MIXED
    assert len(result.paid) == 1
    assert result.shortfall is None
    assert result.runs_out_at == NOW + timedelta(days=2, hours=452)


def test_mixed_ignores_hoster_hours_left() -> None:
    servers = [hourly("cloud", "0.1"), periodic("dedic", "50", 2)]

    result = forecast(balance("100", hours_left=1000), servers, NOW)

    assert result.method is Method.MIXED


def test_server_without_renewal_is_excluded_and_listed() -> None:
    servers = [periodic("beaver", "4.50", 8), periodic("owl", "30", 5, Autorenew.OFF)]

    result = forecast(balance("100"), servers, NOW)

    assert {c.server for c in result.paid} == {"beaver"}
    assert [(n.server, n.ends_at) for n in result.not_renewing] == [
        ("owl", NOW + timedelta(days=5))
    ]
    assert "owl does not renew and ends on 12.11." in result.explain("en")


def test_only_non_renewing_means_no_spending() -> None:
    result = forecast(balance("5"), [periodic("owl", "30", 5, Autorenew.OFF)], NOW)

    assert result.method is Method.NO_SPENDING
    assert result.runs_out_at is None


def test_autopay_rule_counts_as_renewal() -> None:
    server = periodic("regxa", "5", 4, Autorenew.NOT_AVAILABLE)

    without = forecast(balance("100"), [server], NOW)
    with_rule = forecast(balance("100"), [server], NOW, autopay=frozenset({"regxa"}))

    assert without.paid == ()
    assert with_rule.paid[0].server == "regxa"


def test_beyond_horizon() -> None:
    result = forecast(balance("100000"), [periodic("beaver", "4.50", 8)], NOW)

    assert result.runs_out_at is None
    assert result.shortfall is None
    assert "more than a year" in result.explain("en")


def test_mismatch_with_real_spending_over_20_percent() -> None:
    # Model: 0.05/h over 14 days = 16.80. Real: 10 + 16.80 = 26.80; the top-up is not spending.
    history = [
        (NOW - timedelta(days=14), eur("100")),
        (NOW - timedelta(days=10), eur("90")),
        (NOW - timedelta(days=7), eur("150")),
        (NOW, eur("133.20")),
    ]

    result = forecast(balance("133.20"), [hourly("cloud", "0.05")], NOW, history)

    assert result.reconciliation is not None
    assert result.reconciliation.predicted == eur("16.80")
    assert result.reconciliation.actual == eur("26.80")
    assert result.reconciliation.mismatch
    assert "Real spending over 14 days is €26.80, the forecast says €16.80." in result.explain("en")


def test_no_mismatch_when_spending_matches() -> None:
    history = [(NOW - timedelta(days=14), eur("100")), (NOW, eur("85"))]

    result = forecast(balance("85"), [hourly("cloud", "0.05")], NOW, history)

    assert result.reconciliation is not None
    assert not result.reconciliation.mismatch
    assert "Real spending" not in result.explain("en")


def test_past_periodic_charges_count_in_reconciliation() -> None:
    history = [(NOW - timedelta(days=14), eur("100")), (NOW, eur("95.50"))]
    # Charged 10 days ago, next charge in about 20 days.
    server = periodic("beaver", "4.50", 0)
    server = server.model_copy(update={"next_charge_at": add_months(NOW - timedelta(days=10), 1)})

    result = forecast(balance("95.50"), [server], NOW, history)

    assert result.reconciliation is not None
    assert result.reconciliation.predicted == eur("4.50")
    assert not result.reconciliation.mismatch


def test_short_history_is_not_reconciled() -> None:
    history = [(NOW - timedelta(days=3), eur("100")), (NOW, eur("50"))]

    result = forecast(balance("50"), [hourly("cloud", "0.05")], NOW, history)

    assert result.reconciliation is None


def test_add_months_clamps_day() -> None:
    assert add_months(datetime(2026, 1, 31, tzinfo=UTC), 1) == datetime(2026, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 11, 30, tzinfo=UTC), 3) == datetime(2027, 2, 28, tzinfo=UTC)
    assert add_months(datetime(2026, 3, 31, tzinfo=UTC), -1) == datetime(2026, 2, 28, tzinfo=UTC)


def test_money_never_float() -> None:
    result = forecast(balance("20.00"), [periodic("beaver", "4.50", 8)], NOW)

    assert all(isinstance(c.amount.amount, Decimal) for c in result.paid)
