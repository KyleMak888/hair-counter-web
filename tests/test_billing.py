from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import (  # noqa: E402
    AccountDisabled,
    Database,
    InsufficientBalance,
    InvalidBalanceAdjustment,
    InvalidCredentials,
)


def _database(directory: str) -> tuple[Database, dict]:
    database = Database(str(Path(directory) / "billing.db"), session_ttl_seconds=3600)
    database.initialize()
    assert not database.has_admin()
    assert database.bootstrap_admin("admin", "admin-pass-123")
    assert database.has_admin()
    admin = database.authenticate("admin", "admin-pass-123")
    return database, admin


def test_account_pricing_idempotency_and_price_snapshot() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "clinic-a", "机构 A", "clinic-pass-123", 10
        )
        database.adjust_balance(admin["id"], account["id"], 10_000, "首次充值")

        first, replayed = database.charge_recognition(
            account["id"], "request-account-a-0001", 63, {"count": 63}
        )
        assert not replayed
        assert first["billing"] == {
            "request_id": "request-account-a-0001",
            "billable_count": 63,
            "unit_price_fen": 10,
            "charged_amount_fen": 630,
            "balance_fen": 9_370,
        }

        replay, replayed = database.charge_recognition(
            account["id"], "request-account-a-0001", 99, {"count": 99}
        )
        assert replayed
        assert replay == first
        assert database.get_account(account["id"])["balance_fen"] == 9_370

        database.update_account(admin["id"], account["id"], unit_price_fen=25)
        second, _ = database.charge_recognition(
            account["id"], "request-account-a-0002", 2, {"count": 2}
        )
        assert second["billing"]["charged_amount_fen"] == 50

        charges = [
            entry for entry in database.list_ledger() if entry["entry_type"] == "charge"
        ]
        assert [entry["unit_price_fen"] for entry in charges] == [25, 10]
        summary = database.get_account(account["id"])
        listed = next(item for item in database.list_accounts() if item["id"] == account["id"])
        assert summary["balance_fen"] == 9_320
        assert listed["total_billable_count"] == 65
        assert listed["total_spent_fen"] == 680


def test_different_accounts_use_independent_prices_and_balances() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        first = database.create_account(
            admin["id"], "first", "第一机构", "first-pass-123", 5
        )
        second = database.create_account(
            admin["id"], "second", "第二机构", "second-pass-123", 30
        )
        database.adjust_balance(admin["id"], first["id"], 1_000, "充值")
        database.adjust_balance(admin["id"], second["id"], 1_000, "充值")

        first_result, _ = database.charge_recognition(
            first["id"], "request-first-0001", 10, {"count": 10}
        )
        second_result, _ = database.charge_recognition(
            second["id"], "request-second-0001", 10, {"count": 10}
        )
        assert first_result["billing"]["charged_amount_fen"] == 50
        assert second_result["billing"]["charged_amount_fen"] == 300
        assert database.get_account(first["id"])["balance_fen"] == 950
        assert database.get_account(second["id"])["balance_fen"] == 700


def test_insufficient_balance_and_disabled_account_do_not_charge() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "limited", "余额不足机构", "limited-pass-123", 10
        )
        database.adjust_balance(admin["id"], account["id"], 100, "充值")

        try:
            database.charge_recognition(
                account["id"], "request-limited-001", 11, {"count": 11}
            )
        except InsufficientBalance as exc:
            assert exc.balance_fen == 100
            assert exc.required_fen == 110
        else:
            raise AssertionError("insufficient balance was charged")
        assert database.get_account(account["id"])["balance_fen"] == 100
        assert not [
            entry for entry in database.list_ledger() if entry["entry_type"] == "charge"
        ]

        session = database.create_session(account["id"])
        database.update_account(admin["id"], account["id"], active=False)
        assert database.account_for_session(session) is None
        try:
            database.authenticate("limited", "limited-pass-123")
        except AccountDisabled:
            pass
        else:
            raise AssertionError("disabled account authenticated")
        try:
            database.charge_recognition(
                account["id"], "request-limited-002", 1, {"count": 1}
            )
        except AccountDisabled:
            pass
        else:
            raise AssertionError("disabled account was charged")


def test_concurrent_charges_cannot_overdraw_balance() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "concurrent", "并发机构", "concurrent-pass-123", 10
        )
        database.adjust_balance(admin["id"], account["id"], 100, "充值")

        def charge(request_id: str) -> str:
            try:
                database.charge_recognition(
                    account["id"], request_id, 6, {"count": 6}
                )
                return "charged"
            except InsufficientBalance:
                return "insufficient"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(
                executor.map(
                    charge,
                    ["request-concurrent-01", "request-concurrent-02"],
                )
            )
        assert sorted(outcomes) == ["charged", "insufficient"]
        assert database.get_account(account["id"])["balance_fen"] == 40


def test_adjustments_password_reset_sessions_and_persistence() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "persist", "持久化机构", "persist-pass-123", 12
        )
        adjusted = database.adjust_balance(
            admin["id"], account["id"], 500, "线下收款"
        )
        assert adjusted["balance_fen"] == 500
        try:
            database.adjust_balance(admin["id"], account["id"], -501, "错误冲减")
        except InvalidBalanceAdjustment:
            pass
        else:
            raise AssertionError("negative balance adjustment succeeded")

        session = database.create_session(account["id"])
        assert database.account_for_session(session)["username"] == "persist"
        database.reset_password(admin["id"], account["id"], "new-persist-pass")
        assert database.account_for_session(session) is None
        try:
            database.authenticate("persist", "persist-pass-123")
        except InvalidCredentials:
            pass
        else:
            raise AssertionError("old password still works")
        assert database.authenticate("persist", "new-persist-pass")["id"] == account["id"]

        reopened = Database(str(Path(directory) / "billing.db"), session_ttl_seconds=3600)
        reopened.initialize()
        assert reopened.get_account(account["id"])["balance_fen"] == 500
        actions = {entry["action"] for entry in reopened.list_audit_logs()}
        assert {
            "bootstrap_admin",
            "create_account",
            "adjust_balance",
            "reset_password",
        }.issubset(actions)
