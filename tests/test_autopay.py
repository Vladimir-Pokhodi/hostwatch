"""Autopay rule (stage 11): every guard, the hourly job, the dry run and the bot screens."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.types import InlineKeyboardMarkup

from hostwatch.autopay_cli import Assume, describe_plan, memory_copy
from hostwatch.bot.autopay import AutopayCb, Reporter, parse_limit
from hostwatch.bot.callbacks import ActionCb, ServerCb, SetCb, note
from hostwatch.bot.views import server_actions
from hostwatch.core.accounts import AccountRunner
from hostwatch.core.alerts import AccountView
from hostwatch.core.autopay import (
    DEFAULT_DAYS,
    Ledger,
    Rule,
    Verdict,
    decide,
    eligible,
    month_start,
)
from hostwatch.core.errors import HosterError
from hostwatch.core.forecast import forecast
from hostwatch.core.models import (
    ActionResult,
    Autorenew,
    Balance,
    BillingModel,
    Invoice,
    InvoiceStatus,
    Money,
    Server,
    ServerAction,
    ServerKind,
    ServerStatus,
)
from hostwatch.core.plugin import Capability, Plugin, PluginMeta, capabilities
from hostwatch.core.registry import Registry
from hostwatch.scheduler.autopay import AutopayJob, Event, Outcome
from hostwatch.storage.autopay import DECLINED, AutopayStore
from hostwatch.storage.db import schema_version
from hostwatch.storage.repositories import (
    AccountRepository,
    Actor,
    JournalEntry,
    JournalRepository,
    ServerRepository,
)
from tests.test_bot import Harness, _data

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Moscow")
CAPS = frozenset({Capability.INVOICES, Capability.PAY_INVOICE})


def usd(amount: str) -> Money:
    return Money.of(amount, "USD")


def server(sid: str = "10", price: str | None = "5.00", **changes: object) -> Server:
    base = Server(
        external_id=sid,
        name=f"vps-{sid}",
        kind=ServerKind.VPS,
        status=ServerStatus.RUNNING,
        raw_status="Active",
        price=usd(price) if price is not None else None,
        period_months=1,
        next_charge_at=NOW + timedelta(days=2),
        autorenew=Autorenew.NOT_AVAILABLE,
        billing=BillingModel.PERIODIC,
        actions=frozenset({ServerAction.RESTART}),
    )
    return base.model_copy(update=changes)


def invoice(
    iid: str = "500", amount: str = "5.00", sid: str | None = "10", days: float = 2, **kw: object
) -> Invoice:
    base = Invoice(
        id=iid,
        amount=usd(amount),
        status=InvoiceStatus.UNPAID,
        due_at=NOW + timedelta(days=days),
        server_id=sid,
    )
    return base.model_copy(update=kw)


def rule(limit: str = "100", days: int = 3) -> Rule:
    return Rule(days=days, limit=usd(limit))


def verdicts(
    invoices: list[Invoice],
    servers: list[Server] | None = None,
    marked: frozenset[str] = frozenset({"10"}),
    the_rule: Rule | None = None,
    ledger: Ledger | None = None,
    balance: str = "50",
) -> list[Verdict]:
    decisions = decide(
        invoices,
        servers if servers is not None else [server()],
        marked,
        CAPS,
        the_rule or rule(),
        ledger or Ledger(),
        usd(balance),
        NOW,
    )
    return [d.verdict for d in decisions]


# The rule, guard by guard


def test_a_due_renewal_of_a_marked_server_is_paid() -> None:
    assert verdicts([invoice()]) == [Verdict.PAY]


def test_nothing_is_marked_by_default_so_nothing_is_paid() -> None:
    assert verdicts([invoice()], marked=frozenset()) == [Verdict.NOT_MARKED]


def test_zero_limit_pays_nothing() -> None:
    assert verdicts([invoice()], the_rule=rule(limit="0")) == [Verdict.OVER_LIMIT]


def test_an_invoice_paid_before_is_not_paid_again() -> None:
    assert verdicts([invoice()], ledger=Ledger(done=frozenset({"500"}))) == [Verdict.ALREADY]


def test_an_invoice_the_owner_declined_is_not_paid() -> None:
    assert verdicts([invoice()], ledger=Ledger(declined=frozenset({"500"}))) == [Verdict.DECLINED]


def test_an_invoice_of_another_service_is_not_paid() -> None:
    other = server("11")
    assert verdicts([invoice(sid="11")], servers=[server(), other]) == [Verdict.NOT_MARKED]
    # A service the bot does not know (a domain, a deleted server).
    assert verdicts([invoice(sid="99")]) == [Verdict.NOT_SERVICE]


def test_an_upgrade_or_top_up_invoice_is_not_paid() -> None:
    # Plugins give no server for invoices that are not a renewal of exactly one service.
    assert verdicts([invoice(sid=None)]) == [Verdict.NOT_SERVICE]


def test_only_unpaid_invoices_are_paid() -> None:
    paid = invoice(status=InvoiceStatus.PAID)
    cancelled = invoice(iid="501", status=InvoiceStatus.CANCELLED)
    assert verdicts([paid, cancelled]) == [Verdict.NOT_UNPAID, Verdict.NOT_UNPAID]


def test_a_server_with_the_hosters_own_autorenewal_is_not_paid() -> None:
    native = server(autorenew=Autorenew.OFF)
    assert verdicts([invoice()], servers=[native]) == [Verdict.NOT_ELIGIBLE]
    assert not eligible(CAPS, native)
    assert not eligible(CAPS, server(billing=BillingModel.HOURLY))
    assert not eligible({Capability.INVOICES}, server())
    assert eligible(CAPS, server())


def test_an_invoice_far_from_its_due_date_waits() -> None:
    assert verdicts([invoice(days=3.5)]) == [Verdict.NOT_DUE]
    assert verdicts([invoice(days=3)]) == [Verdict.PAY]
    # Overdue is due.
    assert verdicts([invoice(days=-1)]) == [Verdict.PAY]
    assert verdicts([invoice(days=6)], the_rule=rule(days=7)) == [Verdict.PAY]


def test_without_a_due_date_the_servers_end_is_used() -> None:
    assert verdicts([invoice(due_at=None)]) == [Verdict.PAY]
    far = server(next_charge_at=NOW + timedelta(days=20))
    assert verdicts([invoice(due_at=None)], servers=[far]) == [Verdict.NOT_DUE]
    assert verdicts([invoice(due_at=None)], servers=[server(next_charge_at=None)]) == [
        Verdict.NO_DUE_DATE
    ]


def test_a_sum_grown_by_more_than_ten_percent_is_asked_about() -> None:
    assert verdicts([invoice(amount="5.50")]) == [Verdict.PAY]  # exactly +10 %
    assert verdicts([invoice(amount="5.51")]) == [Verdict.ASK_GROWN]
    # The previous period is the last paid sum, not the price on the card.
    last = Ledger(previous={"10": usd("6.00")})
    assert verdicts([invoice(amount="6.50")], ledger=last) == [Verdict.PAY]
    assert verdicts([invoice(amount="4.00")], ledger=last) == [Verdict.PAY]


def test_nothing_to_compare_with_is_asked_about() -> None:
    assert verdicts([invoice()], servers=[server(price=None)]) == [Verdict.ASK_NEW]
    # The price in another currency says nothing about this sum.
    eur = server().model_copy(update={"price": Money.of("5", "EUR")})
    assert verdicts([invoice()], servers=[eur]) == [Verdict.ASK_NEW]


def test_the_monthly_limit_counts_what_was_spent_and_what_is_paid_now() -> None:
    servers = [server("10"), server("11")]
    both = [invoice("500", sid="10"), invoice("501", sid="11", days=2.5)]
    marked = frozenset({"10", "11"})
    assert verdicts(both, servers, marked, rule(limit="10")) == [Verdict.PAY, Verdict.PAY]
    assert verdicts(both, servers, marked, rule(limit="9.99")) == [
        Verdict.PAY,
        Verdict.OVER_LIMIT,
    ]
    spent = Ledger(spent=usd("6"))
    assert verdicts(both, servers, marked, rule(limit="10"), spent) == [
        Verdict.OVER_LIMIT,
        Verdict.OVER_LIMIT,
    ]


def test_not_enough_balance_is_not_paid() -> None:
    servers = [server("10"), server("11")]
    both = [invoice("500", sid="10"), invoice("501", sid="11", days=2.5)]
    marked = frozenset({"10", "11"})
    assert verdicts(both, servers, marked, balance="7") == [Verdict.PAY, Verdict.LOW_BALANCE]
    assert verdicts([invoice()], balance="4.99") == [Verdict.LOW_BALANCE]


def test_another_currency_is_not_paid() -> None:
    euro = invoice().model_copy(update={"amount": Money.of("5", "EUR")})
    assert verdicts([euro]) == [Verdict.OTHER_CURRENCY]


def test_month_starts_in_the_owners_time_zone() -> None:
    late = datetime(2026, 11, 30, 22, 0, tzinfo=UTC)  # 1 December in Moscow
    assert month_start(late, TZ) == datetime(2026, 12, 1, tzinfo=TZ)


def test_forecast_counts_autopay_servers_as_renewing() -> None:
    marked = server()
    plain = forecast(Balance(money=usd("50")), [marked], NOW)
    with_rule = forecast(Balance(money=usd("50")), [marked], NOW, autopay=frozenset({"10"}))
    assert plain.renewing_count == 0 and len(plain.not_renewing) == 1
    assert with_rule.renewing_count == 1 and with_rule.not_renewing == ()


# Storage: safe defaults, journal, notices


@pytest.fixture
def store(conn: sqlite3.Connection) -> AutopayStore:
    return AutopayStore(conn)


def test_fresh_database_marks_nothing_and_limits_are_zero(
    conn: sqlite3.Connection, store: AutopayStore, accounts: AccountRepository
) -> None:
    acc = accounts.add("pay", "main", {})
    assert schema_version(conn) >= 5
    assert store.accounts() == []
    assert store.marked(acc) == frozenset()
    assert store.limit(acc, "USD") == usd("0")
    assert store.days() == DEFAULT_DAYS == 3


def test_limit_and_days_are_kept(store: AutopayStore, accounts: AccountRepository) -> None:
    acc = accounts.add("pay", "main", {})
    store.set_limit(acc, Decimal("12.50"))
    store.set_days(5)
    assert store.rule(acc, "USD") == Rule(days=5, limit=usd("12.50"))
    with pytest.raises(ValueError):
        store.set_limit(acc, Decimal(-1))
    with pytest.raises(ValueError):
        store.set_days(0)


def _journal(
    conn: sqlite3.Connection, acc: int, actor: Actor, inv: str, ok: bool, at: datetime = NOW
) -> None:
    JournalRepository(conn).add(
        JournalEntry(
            at=at,
            actor=actor,
            account_id=acc,
            action="pay_invoice",
            params={
                "action": "pay_invoice",
                "server": "10",
                "invoice": inv,
                "amount": "5.00",
                "currency": "USD",
            },
            response="",
            ok=ok,
        )
    )


def test_ledger_reads_the_journal(
    conn: sqlite3.Connection, store: AutopayStore, accounts: AccountRepository
) -> None:
    acc = accounts.add("pay", "main", {})
    since = month_start(NOW, TZ)
    _journal(conn, acc, "owner", "1", ok=True)
    _journal(conn, acc, "owner", "2", ok=False)  # the owner may try again, and so may autopay
    _journal(conn, acc, "autopay", "3", ok=False)  # money may have left: never again
    _journal(conn, acc, "autopay", "4", ok=True)
    _journal(conn, acc, "autopay", "5", ok=True, at=since - timedelta(seconds=1))
    ledger = store.ledger(acc, "USD", since)
    assert ledger.done == {"1", "3", "4", "5"}
    assert ledger.spent == usd("10.00")  # 3 and 4: an unknown result counts as spent
    assert ledger.previous == {"10": usd("5.00")}


def test_a_notice_answer_is_taken_once(store: AutopayStore, accounts: AccountRepository) -> None:
    acc = accounts.add("pay", "main", {})
    store.put_notice(acc, "500", Verdict.ASK_GROWN.value, usd("9"), NOW)
    asks = {Verdict.ASK_GROWN.value}
    assert store.take_notice(acc, "500", asks, DECLINED)
    assert not store.take_notice(acc, "500", asks, DECLINED)
    assert store.ledger(acc, "USD", NOW).declined == {"500"}


# The hourly job against a plugin that can pay invoices


class PayPlugin(Plugin):
    """A Regxa-like hoster: no autorenewal, invoices paid from the balance."""

    meta = PluginMeta(id="pay", name="Payhost", site="https://pay.invalid", auth=(), currency="USD")
    invoice_list: ClassVar[list[Invoice]] = []
    credit: ClassVar[str] = "50"
    calls: ClassVar[list[str]] = []
    fail_on: ClassVar[set[str]] = set()

    async def servers(self) -> list[Server]:
        return [server("10"), server("11")]

    async def balance(self) -> Balance:
        return Balance(money=usd(self.credit))

    async def invoices(self) -> list[Invoice]:
        return list(self.invoice_list)

    async def pay_invoice(self, invoice_id: str, amount: Money) -> ActionResult:
        self.calls.append(f"pay {invoice_id} {amount.amount}")
        if invoice_id in self.fail_on:
            raise HosterError("the hoster broke in the middle")
        return ActionResult(ok=True, detail=f"invoice {invoice_id} paid")


@pytest.fixture(autouse=True)
def _reset_plugin() -> Iterator[None]:
    PayPlugin.invoice_list = [invoice()]
    PayPlugin.credit = "50"
    PayPlugin.calls = []
    PayPlugin.fail_on = set()
    yield


class Setup:
    def __init__(self, conn: sqlite3.Connection, accounts: AccountRepository) -> None:
        self.conn = conn
        self.accounts = accounts
        self.registry = Registry({"pay": PayPlugin})
        self.store = AutopayStore(conn)
        self.events: list[Event] = []
        self.acc = accounts.add("pay", "main", {})
        ServerRepository(conn).replace(self.acc, [server("10"), server("11")], NOW)
        self.runner = AccountRunner(accounts, self.registry, lambda _: httpx.AsyncClient())
        self.job = AutopayJob(
            self.runner,
            accounts,
            ServerRepository(conn),
            self.store,
            self.registry,
            self._report,
            lambda: TZ,
            now=lambda: NOW,
        )

    async def _report(self, event: Event) -> None:
        self.events.append(event)

    def journal(self) -> list[JournalEntry]:
        return JournalRepository(self.conn).recent()


@pytest.fixture
def setup(conn: sqlite3.Connection, accounts: AccountRepository) -> Setup:
    return Setup(conn, accounts)


async def test_after_deploy_the_job_pays_nothing(setup: Setup) -> None:
    """Live accounts after the update: invoices due, but nothing marked, limit zero."""
    PayPlugin.invoice_list = [invoice("500", sid="10"), invoice("501", sid="11")]
    await setup.job.tick()
    assert PayPlugin.calls == []
    assert setup.events == []
    assert setup.journal() == []


async def test_marking_alone_pays_nothing_until_a_limit_is_set(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    await setup.job.tick()
    await setup.job.tick()
    assert PayPlugin.calls == []
    assert [e.decision.verdict for e in setup.events] == [Verdict.OVER_LIMIT]  # told once


async def test_the_job_pays_once_and_reports(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(20))

    await setup.job.tick()
    # The hoster still shows the invoice unpaid (a slow cabinet): no second payment.
    await setup.job.tick()

    assert PayPlugin.calls == ["pay 500 5.00"]
    [event] = setup.events
    assert event.outcome is Outcome.PAID
    [entry] = setup.journal()
    assert (entry.actor, entry.action, entry.ok) == ("autopay", "pay_invoice", True)
    assert entry.params["invoice"] == "500" and entry.params["amount"] == "5.00"
    assert entry.response == "invoice 500 paid"


async def test_a_hoster_error_in_the_middle_stops_and_is_not_retried(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.mark(setup.acc, "11", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(20))
    PayPlugin.invoice_list = [invoice("500", sid="10"), invoice("501", sid="11", days=2.5)]
    PayPlugin.fail_on = {"500"}

    await setup.job.tick()
    assert PayPlugin.calls == ["pay 500 5.00"]  # 501 waits for the next run
    assert [e.outcome for e in setup.events] == [Outcome.FAILED]
    [entry] = setup.journal()
    assert not entry.ok and "broke in the middle" in entry.response

    await setup.job.tick()
    assert PayPlugin.calls == ["pay 500 5.00", "pay 501 5.00"]  # 500 is never tried again


async def test_an_attempt_cut_short_is_not_repeated(setup: Setup) -> None:
    """The bot died after writing the attempt and before the hoster answered."""
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(20))
    params = {"invoice": "500", "server": "10", "amount": "5.00", "currency": "USD"}
    setup.store.begin(setup.acc, params, NOW)
    await setup.job.tick()
    assert PayPlugin.calls == []


async def test_a_grown_sum_is_asked_once_and_not_paid(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(20))
    PayPlugin.invoice_list = [invoice(amount="7.00")]
    await setup.job.tick()
    await setup.job.tick()
    assert PayPlugin.calls == []
    [event] = setup.events
    assert event.decision.verdict is Verdict.ASK_GROWN
    assert event.decision.reference == usd("5.00")
    # The sum changed again: the owner is asked about the new one.
    PayPlugin.invoice_list = [invoice(amount="8.00")]
    await setup.job.tick()
    assert len(setup.events) == 2


async def test_low_balance_is_reported_once(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(20))
    PayPlugin.credit = "1"
    await setup.job.tick()
    await setup.job.tick()
    assert PayPlugin.calls == []
    assert [e.decision.verdict for e in setup.events] == [Verdict.LOW_BALANCE]


async def test_monthly_limit_holds_across_runs(setup: Setup) -> None:
    setup.store.mark(setup.acc, "10", True, NOW)
    setup.store.mark(setup.acc, "11", True, NOW)
    setup.store.set_limit(setup.acc, Decimal(8))
    PayPlugin.invoice_list = [invoice("500", sid="10")]
    await setup.job.tick()
    PayPlugin.invoice_list = [invoice("501", sid="11")]
    await setup.job.tick()
    assert PayPlugin.calls == ["pay 500 5.00"]
    assert setup.events[-1].decision.verdict is Verdict.OVER_LIMIT


async def test_dry_run_reads_and_pays_nothing(setup: Setup, tmp_path: object) -> None:
    PayPlugin.invoice_list = [
        invoice("500", sid="10"),
        invoice("501", sid=None),
        invoice("502", sid="11", amount="9.00"),
    ]
    text = await describe_plan(
        setup.conn,
        setup.accounts,
        setup.runner,
        setup.registry,
        setup.acc,
        Assume(all_eligible=True, limit="100"),
        redact=True,
        now=lambda: NOW,
    )
    assert PayPlugin.calls == []
    assert setup.journal() == []
    assert "Nothing is paid" in text
    assert "would pay now" in text and "top-up, upgrade" in text and "grew" in text
    assert "500" not in text and "5.00" not in text and "vps-10" not in text
    assert "Total: would pay 1, held 1, skipped 1" in text
    # With the stored settings (nothing marked) nothing would be paid.
    stored = await describe_plan(
        setup.conn,
        setup.accounts,
        setup.runner,
        setup.registry,
        setup.acc,
        Assume(),
        False,
        lambda: NOW,
    )
    assert "Total: would pay 0" in stored


def test_dry_run_copy_leaves_the_file_alone(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    from hostwatch.storage.db import MIGRATIONS

    for sql in MIGRATIONS[:4]:
        old.executescript(sql)
    old.execute("PRAGMA user_version = 4")
    old.commit()
    old.close()
    copy = memory_copy(path)
    assert schema_version(copy) == len(MIGRATIONS)
    copy.close()
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 4
    check.close()


# The bot


@pytest.fixture
async def harness(conn: sqlite3.Connection, accounts: AccountRepository) -> AsyncIterator[Harness]:
    h = Harness(conn, accounts)
    h.hub.registry._plugins["pay"] = PayPlugin
    h.hub.autopay = AutopayStore(conn)
    yield h
    await h.poller.stop()
    await h.bot.session.close()


async def _pay_account(h: Harness) -> int:
    h.onboard("ru")
    acc = h.hub.accounts.add("pay", "main", {})
    await h.poller.poll_account(acc)
    return acc


def test_autopay_button_only_without_native_autorenewal() -> None:
    assert Capability.PAY_INVOICE in capabilities(PayPlugin)
    assert server_actions(_data(PayPlugin, [server()]), server()) == ["autopay"]
    native = server(autorenew=Autorenew.ON)
    assert server_actions(_data(PayPlugin, [native]), native) == []


async def test_card_marks_and_unmarks_a_server(harness: Harness) -> None:
    acc = await _pay_account(harness)
    await harness.press(ServerCb(acc=acc, sid="10"))
    assert "Автооплата бота: выкл." in harness.session.last_text()
    assert "Включить автооплату" in harness.session.keyboard().values()

    await harness.press(ActionCb(acc=acc, sid="10", act="autopay"))
    store = harness.hub.autopay
    assert store is not None and store.marked(acc) == {"10"}
    assert "лимит 0" in harness.alerts()[-1]
    assert "Автооплата бота: вкл." in harness.session.last_text()
    # The forecast now counts the server as renewing.
    data = harness.hub.load(acc)
    assert data.autopay == {"10"} and data.forecast is not None
    assert data.forecast.renewing_count == 1

    await harness.press(ActionCb(acc=acc, sid="10", act="autopay"))
    assert store.marked(acc) == frozenset()
    assert PayPlugin.calls == []


async def test_settings_set_days_and_limit(harness: Harness) -> None:
    acc = await _pay_account(harness)
    await harness.press(SetCb(key="ap"))
    text = harness.session.last_text()
    assert "Срок: за 3 дн." in text and "Лимит 0" in text

    await harness.press(SetCb(key="ap", value=f"lim{acc}"))
    await harness.send("abc")
    assert "Нужна сумма" in harness.session.last_text()
    await harness.send("12,50")
    await harness.press(SetCb(key="ap", value="days"))
    await harness.send("5")

    store = harness.hub.autopay
    assert store is not None
    assert store.rule(acc, "USD") == Rule(days=5, limit=usd("12.50"))
    assert "лимит $12.50 в месяц" in harness.session.last_text()


def test_parse_limit() -> None:
    assert parse_limit("15") == Decimal(15)
    assert parse_limit(" 1 000,5 ") == Decimal("1000.5")
    assert parse_limit("0") == Decimal(0)
    for bad in ("-1", "1.005", "abc", "NaN", "1e9"):
        assert parse_limit(bad) is None


async def test_owner_pays_a_grown_invoice_once(harness: Harness) -> None:
    acc = await _pay_account(harness)
    store = harness.hub.autopay
    assert store is not None
    PayPlugin.invoice_list = [invoice(amount="7.00")]
    store.put_notice(acc, "500", Verdict.ASK_GROWN.value, usd("7.00"), NOW)

    await harness.press(AutopayCb(acc=acc, inv="500", op="pay"))
    await harness.press(AutopayCb(acc=acc, inv="500", op="pay"))

    assert PayPlugin.calls == ["pay 500 7.00"]
    assert harness.alerts()[-1] == "Кнопка устарела или уже нажата. Ничего не сделал."
    entry = harness.hub.journal.recent()[0]
    assert (entry.actor, entry.ok, entry.params["invoice"]) == ("owner", True, "500")
    # The money notice stays in the chat; the menu with the result goes below it.
    notice, result = harness.session.texts()[-2:]
    assert "Действие с деньгами" in notice
    assert "оплатить счёт #500" in result and "Действие с деньгами" not in result


async def test_owner_declines_a_grown_invoice(harness: Harness) -> None:
    acc = await _pay_account(harness)
    store = harness.hub.autopay
    assert store is not None
    store.put_notice(acc, "500", Verdict.ASK_GROWN.value, usd("7.00"), NOW)
    await harness.press(AutopayCb(acc=acc, inv="500", op="no"))
    assert PayPlugin.calls == []
    assert store.ledger(acc, "USD", NOW).declined == {"500"}


async def test_owner_pays_nothing_if_the_sum_changed_again(harness: Harness) -> None:
    acc = await _pay_account(harness)
    store = harness.hub.autopay
    assert store is not None
    PayPlugin.invoice_list = [invoice(amount="9.00")]
    store.put_notice(acc, "500", Verdict.ASK_GROWN.value, usd("7.00"), NOW)
    await harness.press(AutopayCb(acc=acc, inv="500", op="pay"))
    assert PayPlugin.calls == []
    assert "снова изменилась" in harness.session.last_text()


async def test_reports_reach_the_owner_with_buttons(harness: Harness) -> None:
    acc = await _pay_account(harness)
    sent: list[tuple[str, InlineKeyboardMarkup | None]] = []

    async def send(text: str, markup: InlineKeyboardMarkup | None) -> bool:
        sent.append((text, markup))
        return True

    store = harness.hub.autopay
    assert store is not None
    store.mark(acc, "10", True, NOW)
    store.set_limit(acc, Decimal(20))
    job = AutopayJob(
        harness.poller._runner,
        harness.hub.accounts,
        harness.hub.servers,
        store,
        harness.hub.registry,
        Reporter(harness.hub, send),
        lambda: TZ,
        now=lambda: NOW,
    )
    PayPlugin.invoice_list = [invoice("500", amount="5.00"), invoice("501", sid="10", amount="9")]
    await job.tick()

    paid, grown = sent
    assert "Действие с деньгами" in paid[0] and "оплачен счёт #500" in paid[0]
    assert "потрачено $5.00 из $20.00" in paid[0]
    assert "вырос: $9.00 вместо $5.00" in grown[0]
    assert grown[1] is not None
    buttons = [b.callback_data for row in grown[1].inline_keyboard for b in row]
    # A notification's buttons work outside the menu: they carry the note mark.
    assert note(AutopayCb(acc=acc, inv="501", op="pay")) in buttons


def test_an_autopay_server_is_not_reported_as_lapsing() -> None:
    from tests.test_alerts import NOON, Book, account
    from tests.test_alerts import server as alert_server

    ends = NOON + timedelta(days=2)
    lapsing = alert_server(
        billing=BillingModel.PERIODIC,
        price=Money.of("5", "EUR"),
        period_months=1,
        next_charge_at=ends,
    )
    rich = Balance(money=Money.of("100", "EUR"))
    marked = frozenset({lapsing.external_id})
    with_rule = AccountView(
        account(),
        "Fake",
        [lapsing],
        forecast(rich, [lapsing], NOON, autopay=marked),
        autopay=marked,
    )
    assert Book().tick(NOON, with_rule) == []
    without = AccountView(account(), "Fake", [lapsing], forecast(rich, [lapsing], NOON))
    assert any("не продлевается" in text for _, text in Book().tick(NOON, without))
