from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import (  # noqa: E402
    AccountDisabled,
    Database,
    DatabaseError,
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


def test_legacy_database_migration_adds_standard_plan_idempotently() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "legacy.db"
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    unit_price_fen INTEGER NOT NULL DEFAULT 0 CHECK (unit_price_fen >= 0),
                    balance_fen INTEGER NOT NULL DEFAULT 0 CHECK (balance_fen >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO accounts (
                    username, display_name, password_hash, role, active,
                    unit_price_fen, balance_fen, created_at, updated_at
                ) VALUES ('legacy', '历史客户', 'unused', 'user', 1, 12, 345, 'now', 'now')
                """
            )

        database = Database(str(path), session_ttl_seconds=3600)
        database.initialize()
        database.initialize()

        assert database.get_account(1)["plan"] == "standard"
        with sqlite3.connect(path) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(accounts)")]
        assert columns.count("plan") == 1


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
            "plan": "standard",
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


def test_historical_cached_recognition_defaults_to_standard_plan() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "historical", "历史缓存机构", "historical-pass-123", 10
        )
        database.adjust_balance(admin["id"], account["id"], 1_000, "充值")
        first, _ = database.charge_recognition(
            account["id"], "request-historical-0001", 3, {"count": 3}
        )

        with sqlite3.connect(database.path) as connection:
            payload = json.loads(
                connection.execute(
                    "SELECT response_json FROM recognitions WHERE account_id = ?",
                    (account["id"],),
                ).fetchone()[0]
            )
            payload["billing"].pop("plan")
            connection.execute(
                "UPDATE recognitions SET response_json = ? WHERE account_id = ?",
                (json.dumps(payload), account["id"]),
            )

        cached = database.cached_recognition(account["id"], "request-historical-0001")
        assert cached is not None
        assert cached["billing"]["plan"] == "standard"
        replay, replayed = database.charge_recognition(
            account["id"], "request-historical-0001", 99, {"count": 99}
        )
        assert replayed
        assert replay == cached
        assert replay["billing"]["charged_amount_fen"] == first["billing"]["charged_amount_fen"]


def test_svip_usage_freezes_financials_preserves_sessions_and_tracks_usage() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        account = database.create_account(
            admin["id"], "svip", "买断机构", "svip-pass-123", 25
        )
        database.adjust_balance(admin["id"], account["id"], 500, "充值")
        session = database.create_session(account["id"])

        upgraded = database.update_account(
            admin["id"], account["id"], plan="svip"
        )
        assert upgraded["unit_price_fen"] == 25
        assert upgraded["balance_fen"] == 500
        assert database.account_for_session(session)["plan"] == "svip"

        free, replayed = database.charge_recognition(
            account["id"], "request-svip-0001", 40, {"count": 40}
        )
        assert not replayed
        assert free["billing"] == {
            "request_id": "request-svip-0001",
            "billable_count": 40,
            "unit_price_fen": 0,
            "charged_amount_fen": 0,
            "balance_fen": 500,
            "plan": "svip",
        }
        replay, replayed = database.charge_recognition(
            account["id"], "request-svip-0001", 99, {"count": 99}
        )
        assert replayed
        assert replay == free
        assert database.get_account(account["id"])["balance_fen"] == 500
        assert not [
            entry for entry in database.list_ledger() if entry["entry_type"] == "charge"
        ]
        listed = next(item for item in database.list_accounts() if item["id"] == account["id"])
        assert listed["total_billable_count"] == 40
        assert listed["total_spent_fen"] == 0

        database.update_account(admin["id"], account["id"], active=False)
        with pytest.raises(AccountDisabled):
            database.charge_recognition(
                account["id"], "request-svip-disabled", 1, {"count": 1}
            )
        database.update_account(admin["id"], account["id"], active=True)

        with pytest.raises(DatabaseError, match="不能修改单价"):
            database.update_account(admin["id"], account["id"], unit_price_fen=30)
        with pytest.raises(InvalidBalanceAdjustment, match="不能调整余额"):
            database.adjust_balance(admin["id"], account["id"], 100, "错误充值")

        downgraded = database.update_account(
            admin["id"], account["id"], plan="standard", unit_price_fen=30
        )
        assert downgraded["plan"] == "standard"
        assert downgraded["unit_price_fen"] == 30
        assert downgraded["balance_fen"] == 500
        charged, _ = database.charge_recognition(
            account["id"], "request-standard-after-svip", 2, {"count": 2}
        )
        assert charged["billing"]["charged_amount_fen"] == 60
        assert charged["billing"]["balance_fen"] == 440

        plan_changes = [
            entry["details"]
            for entry in database.list_audit_logs()
            if entry["action"] == "update_account" and "plan" in entry["details"]
        ]
        assert {change["plan"] for change in plan_changes} == {"standard", "svip"}


def test_new_svip_account_rejects_nonzero_price() -> None:
    with TemporaryDirectory() as directory:
        database, admin = _database(directory)
        with pytest.raises(DatabaseError, match="不能设置单价"):
            database.create_account(
                admin["id"], "bad-svip", "错误买断机构", "bad-svip-pass", 10, "svip"
            )


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
