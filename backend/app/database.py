from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PASSWORD_ITERATIONS = 600_000
ACCOUNT_PLANS = {"standard", "svip"}


class DatabaseError(Exception):
    pass


class DuplicateUsername(DatabaseError):
    pass


class InvalidCredentials(DatabaseError):
    pass


class AccountDisabled(DatabaseError):
    pass


class AccountNotFound(DatabaseError):
    pass


class InvalidBalanceAdjustment(DatabaseError):
    pass


class InsufficientBalance(DatabaseError):
    def __init__(self, balance_fen: int, required_fen: int) -> None:
        super().__init__("Insufficient balance")
        self.balance_fen = balance_fen
        self.required_fen = required_fen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_password(password: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _recognition_payload(response_json: str) -> dict[str, Any]:
    payload = json.loads(response_json)
    billing = payload.get("billing")
    if isinstance(billing, dict):
        billing.setdefault("plan", "standard")
    return payload


class Database:
    def __init__(self, path: str, session_ttl_seconds: int = 7 * 24 * 60 * 60) -> None:
        self.path = path
        self.session_ttl_seconds = session_ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    plan TEXT NOT NULL DEFAULT 'standard' CHECK (plan IN ('standard', 'svip')),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    unit_price_fen INTEGER NOT NULL DEFAULT 0 CHECK (unit_price_fen >= 0),
                    balance_fen INTEGER NOT NULL DEFAULT 0 CHECK (balance_fen >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recognitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id),
                    request_id TEXT NOT NULL,
                    billable_count INTEGER NOT NULL CHECK (billable_count >= 0),
                    unit_price_fen INTEGER NOT NULL CHECK (unit_price_fen >= 0),
                    charged_amount_fen INTEGER NOT NULL CHECK (charged_amount_fen >= 0),
                    balance_after_fen INTEGER NOT NULL CHECK (balance_after_fen >= 0),
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (account_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id),
                    entry_type TEXT NOT NULL CHECK (entry_type IN ('charge', 'adjustment')),
                    amount_fen INTEGER NOT NULL,
                    balance_after_fen INTEGER NOT NULL CHECK (balance_after_fen >= 0),
                    billable_count INTEGER,
                    unit_price_fen INTEGER,
                    recognition_id INTEGER REFERENCES recognitions(id),
                    admin_account_id INTEGER REFERENCES accounts(id),
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_account_id INTEGER REFERENCES accounts(id),
                    target_account_id INTEGER REFERENCES accounts(id),
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
                CREATE INDEX IF NOT EXISTS idx_recognitions_account ON recognitions(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_entries(account_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
                """
            )
            account_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
            }
            if "plan" not in account_columns:
                connection.execute(
                    """
                    ALTER TABLE accounts
                    ADD COLUMN plan TEXT NOT NULL DEFAULT 'standard'
                    CHECK (plan IN ('standard', 'svip'))
                    """
                )

    @staticmethod
    def _account(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "display_name": str(row["display_name"]),
            "role": str(row["role"]),
            "plan": str(row["plan"]) if "plan" in keys else "standard",
            "active": bool(row["active"]),
            "unit_price_fen": int(row["unit_price_fen"]),
            "balance_fen": int(row["balance_fen"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "total_billable_count": int(row["total_billable_count"] or 0)
            if "total_billable_count" in keys
            else 0,
            "total_spent_fen": int(row["total_spent_fen"] or 0)
            if "total_spent_fen" in keys
            else 0,
            "last_recognition_at": row["last_recognition_at"]
            if "last_recognition_at" in keys
            else None,
        }

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        admin_account_id: int | None,
        target_account_id: int | None,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_logs (
                admin_account_id, target_account_id, action, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                admin_account_id,
                target_account_id,
                action,
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    def bootstrap_admin(self, username: str, password: str) -> bool:
        if not username or not password:
            return False
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM accounts WHERE role = 'admin' LIMIT 1"
                ).fetchone():
                    connection.commit()
                    return False
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO accounts (
                            username, display_name, password_hash, role, active,
                            unit_price_fen, balance_fen, created_at, updated_at
                        ) VALUES (?, ?, ?, 'admin', 1, 0, 0, ?, ?)
                        """,
                        (username, "系统管理员", hash_password(password), now, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DuplicateUsername(username) from exc
                self._audit(connection, None, int(cursor.lastrowid), "bootstrap_admin")
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def has_admin(self) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM accounts WHERE role = 'admin' LIMIT 1"
            ).fetchone() is not None

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            raise InvalidCredentials()
        if not bool(row["active"]):
            raise AccountDisabled()
        return self._account(row)

    def create_session(self, account_id: int) -> str:
        token = secrets.token_urlsafe(32)
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=self.session_ttl_seconds)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (_now(),),
            )
            connection.execute(
                """
                INSERT INTO sessions (token_hash, account_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    account_id,
                    created_at.isoformat(timespec="seconds"),
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
        return token

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (_token_hash(token),),
            )

    def account_for_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT a.*
                FROM sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND a.active = 1
                """,
                (_token_hash(token), _now()),
            ).fetchone()
        return self._account(row) if row is not None else None

    def create_account(
        self,
        admin_account_id: int,
        username: str,
        display_name: str,
        password: str,
        unit_price_fen: int,
        plan: str = "standard",
    ) -> dict[str, Any]:
        if plan not in ACCOUNT_PLANS:
            raise DatabaseError("客户类型无效")
        if plan == "svip" and unit_price_fen != 0:
            raise DatabaseError("SVIP 买断账户不能设置单价")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO accounts (
                            username, display_name, password_hash, role, plan, active,
                            unit_price_fen, balance_fen, created_at, updated_at
                        ) VALUES (?, ?, ?, 'user', ?, 1, ?, 0, ?, ?)
                        """,
                        (
                            username,
                            display_name,
                            hash_password(password),
                            plan,
                            unit_price_fen,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DuplicateUsername(username) from exc
                account_id = int(cursor.lastrowid)
                self._audit(
                    connection,
                    admin_account_id,
                    account_id,
                    "create_account",
                    {
                        "username": username,
                        "plan": plan,
                        "unit_price_fen": unit_price_fen,
                    },
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_account(account_id)

    def get_account(self, account_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            raise AccountNotFound(account_id)
        return self._account(row)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.*,
                    COALESCE(SUM(r.billable_count), 0) AS total_billable_count,
                    COALESCE(SUM(r.charged_amount_fen), 0) AS total_spent_fen,
                    MAX(r.created_at) AS last_recognition_at
                FROM accounts a
                LEFT JOIN recognitions r ON r.account_id = a.id
                GROUP BY a.id
                ORDER BY CASE a.role WHEN 'admin' THEN 0 ELSE 1 END, a.created_at DESC
                """
            ).fetchall()
        return [self._account(row) for row in rows]

    def update_account(
        self,
        admin_account_id: int,
        account_id: int,
        *,
        display_name: str | None = None,
        unit_price_fen: int | None = None,
        active: bool | None = None,
        plan: str | None = None,
    ) -> dict[str, Any]:
        if plan is not None and plan not in ACCOUNT_PLANS:
            raise DatabaseError("客户类型无效")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT * FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                if current is None:
                    raise AccountNotFound(account_id)
                if current["role"] == "admin" and active is False:
                    raise DatabaseError("管理员账号不能停用")
                current_plan = str(current["plan"])
                target_plan = plan or current_plan
                if current["role"] == "admin" and target_plan != "standard":
                    raise DatabaseError("管理员账号不能设为 SVIP")
                if target_plan == "svip" and unit_price_fen is not None:
                    raise DatabaseError("SVIP 期间不能修改单价")

                changes: dict[str, Any] = {}
                if display_name is not None and display_name != current["display_name"]:
                    changes["display_name"] = display_name
                if unit_price_fen is not None and unit_price_fen != current["unit_price_fen"]:
                    changes["unit_price_fen"] = unit_price_fen
                if active is not None and int(active) != current["active"]:
                    changes["active"] = int(active)
                if plan is not None and plan != current_plan:
                    changes["plan"] = plan

                if changes:
                    columns = ", ".join(f"{name} = ?" for name in changes)
                    connection.execute(
                        f"UPDATE accounts SET {columns}, updated_at = ? WHERE id = ?",
                        (*changes.values(), _now(), account_id),
                    )
                    self._audit(
                        connection,
                        admin_account_id,
                        account_id,
                        "update_account",
                        changes,
                    )
                    if changes.get("active") == 0:
                        connection.execute(
                            "DELETE FROM sessions WHERE account_id = ?",
                            (account_id,),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_account(account_id)

    def reset_password(
        self,
        admin_account_id: int,
        account_id: int,
        new_password: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "UPDATE accounts SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (hash_password(new_password), _now(), account_id),
                )
                if cursor.rowcount != 1:
                    raise AccountNotFound(account_id)
                connection.execute("DELETE FROM sessions WHERE account_id = ?", (account_id,))
                self._audit(
                    connection,
                    admin_account_id,
                    account_id,
                    "reset_password",
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def adjust_balance(
        self,
        admin_account_id: int,
        account_id: int,
        amount_fen: int,
        note: str,
    ) -> dict[str, Any]:
        if amount_fen == 0:
            raise InvalidBalanceAdjustment("调整金额不能为零")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT plan, balance_fen FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                if row is None:
                    raise AccountNotFound(account_id)
                if row["plan"] == "svip":
                    raise InvalidBalanceAdjustment("SVIP 买断账户不能调整余额")
                balance_after = int(row["balance_fen"]) + amount_fen
                if balance_after < 0:
                    raise InvalidBalanceAdjustment("调整后余额不能小于零")
                now = _now()
                connection.execute(
                    "UPDATE accounts SET balance_fen = ?, updated_at = ? WHERE id = ?",
                    (balance_after, now, account_id),
                )
                connection.execute(
                    """
                    INSERT INTO ledger_entries (
                        account_id, entry_type, amount_fen, balance_after_fen,
                        admin_account_id, note, created_at
                    ) VALUES (?, 'adjustment', ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        amount_fen,
                        balance_after,
                        admin_account_id,
                        note,
                        now,
                    ),
                )
                self._audit(
                    connection,
                    admin_account_id,
                    account_id,
                    "adjust_balance",
                    {"amount_fen": amount_fen, "note": note},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_account(account_id)

    def cached_recognition(
        self,
        account_id: int,
        request_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT response_json FROM recognitions
                WHERE account_id = ? AND request_id = ?
                """,
                (account_id, request_id),
            ).fetchone()
        return _recognition_payload(row["response_json"]) if row is not None else None

    def charge_recognition(
        self,
        account_id: int,
        request_id: str,
        billable_count: int,
        response_payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT response_json FROM recognitions
                    WHERE account_id = ? AND request_id = ?
                    """,
                    (account_id, request_id),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return _recognition_payload(existing["response_json"]), True

                account = connection.execute(
                    "SELECT active, plan, unit_price_fen, balance_fen FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
                if account is None:
                    raise AccountNotFound(account_id)
                if not bool(account["active"]):
                    raise AccountDisabled()

                plan = str(account["plan"])
                balance_fen = int(account["balance_fen"])
                unit_price_fen = 0 if plan == "svip" else int(account["unit_price_fen"])
                charged_amount_fen = billable_count * unit_price_fen
                if plan == "standard" and balance_fen < charged_amount_fen:
                    raise InsufficientBalance(balance_fen, charged_amount_fen)
                balance_after_fen = balance_fen - charged_amount_fen

                payload = dict(response_payload)
                payload["billing"] = {
                    "request_id": request_id,
                    "billable_count": billable_count,
                    "unit_price_fen": unit_price_fen,
                    "charged_amount_fen": charged_amount_fen,
                    "balance_fen": balance_after_fen,
                    "plan": plan,
                }
                response_json = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                now = _now()
                if plan == "standard":
                    connection.execute(
                        "UPDATE accounts SET balance_fen = ?, updated_at = ? WHERE id = ?",
                        (balance_after_fen, now, account_id),
                    )
                cursor = connection.execute(
                    """
                    INSERT INTO recognitions (
                        account_id, request_id, billable_count, unit_price_fen,
                        charged_amount_fen, balance_after_fen, response_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        request_id,
                        billable_count,
                        unit_price_fen,
                        charged_amount_fen,
                        balance_after_fen,
                        response_json,
                        now,
                    ),
                )
                if plan == "standard":
                    connection.execute(
                        """
                        INSERT INTO ledger_entries (
                            account_id, entry_type, amount_fen, balance_after_fen,
                            billable_count, unit_price_fen, recognition_id, note, created_at
                        ) VALUES (?, 'charge', ?, ?, ?, ?, ?, '自动识别扣费', ?)
                        """,
                        (
                            account_id,
                            -charged_amount_fen,
                            balance_after_fen,
                            billable_count,
                            unit_price_fen,
                            int(cursor.lastrowid),
                            now,
                        ),
                    )
                connection.commit()
                return payload, False
            except Exception:
                connection.rollback()
                raise

    def list_ledger(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    l.id, l.entry_type, l.amount_fen, l.balance_after_fen,
                    l.billable_count, l.unit_price_fen, l.note, l.created_at,
                    a.id AS account_id, a.username, a.display_name,
                    admin.username AS admin_username
                FROM ledger_entries l
                JOIN accounts a ON a.id = l.account_id
                LEFT JOIN accounts admin ON admin.id = l.admin_account_id
                ORDER BY l.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    log.id, log.action, log.details_json, log.created_at,
                    admin.username AS admin_username,
                    target.username AS target_username
                FROM audit_logs log
                LEFT JOIN accounts admin ON admin.id = log.admin_account_id
                LEFT JOIN accounts target ON target.id = log.target_account_id
                ORDER BY log.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]
