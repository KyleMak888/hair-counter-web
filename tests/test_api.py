from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from dataclasses import replace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.main as main_module  # noqa: E402
from app.database import Database  # noqa: E402


def _configured_database(directory: str) -> tuple[Database, dict, dict]:
    database = Database(str(Path(directory) / "api.db"), session_ttl_seconds=3600)
    database.initialize()
    database.bootstrap_admin("admin", "admin-pass-123")
    admin = database.authenticate("admin", "admin-pass-123")
    user = database.create_account(
        admin["id"], "clinic", "测试机构", "clinic-pass-123", 10
    )
    database.adjust_balance(admin["id"], user["id"], 2_000, "测试充值")
    return database, admin, user


def _image_file() -> tuple[str, bytes, str]:
    path = ROOT / "tests" / "fixtures" / "touching-hairs-source.jpg"
    return path.name, path.read_bytes(), "image/jpeg"


def test_authenticated_count_billing_and_idempotent_api() -> None:
    with TemporaryDirectory() as directory:
        database, _, user = _configured_database(directory)
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            unauthorized = client.post(
                "/api/count",
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-unauthorized"},
            )
            assert unauthorized.status_code == 401

            login = client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )
            assert login.status_code == 200
            assert login.json()["balance_fen"] == 2_000

            invalid = client.post(
                "/api/count",
                files={"file": ("invalid.jpg", b"not-an-image", "image/jpeg")},
                headers={"Idempotency-Key": "request-api-invalid-0001"},
            )
            assert invalid.status_code == 415
            assert database.get_account(user["id"])["balance_fen"] == 2_000

            url = "/api/count?threshold_offset=17&min_contrast=23&exclude_border=false"
            first = client.post(
                url,
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-count-0001"},
            )
            assert first.status_code == 200
            payload = first.json()
            assert payload["count"] == sum(
                item["strand_count"] for item in payload["items"]
            )
            assert payload["billing"]["billable_count"] == payload["count"]
            assert payload["billing"]["charged_amount_fen"] == payload["count"] * 10

            replay = client.post(
                url,
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-count-0001"},
            )
            assert replay.status_code == 200
            assert replay.json() == payload
            assert database.get_account(user["id"])["balance_fen"] == 2_000 - payload[
                "billing"
            ]["charged_amount_fen"]

            second = client.post(
                url,
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-count-0002"},
            )
            assert second.status_code == 200
            assert second.json()["billing"]["balance_fen"] == 2_000 - 2 * payload[
                "billing"
            ]["charged_amount_fen"]
            assert not list(Path(directory).glob("*.jpg"))
            assert not list(Path(directory).glob("*.png"))


def test_admin_account_management_and_insufficient_balance_api() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        with patch.object(main_module, "database", database):
            user_client = TestClient(main_module.app)
            user_client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )
            assert user_client.get("/api/admin/accounts").status_code == 403

            admin_client = TestClient(main_module.app)
            admin_login = admin_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin-pass-123"},
            )
            assert admin_login.status_code == 200
            created = admin_client.post(
                "/api/admin/accounts",
                json={
                    "username": "empty",
                    "display_name": "余额不足机构",
                    "password": "empty-pass-123",
                    "unit_price_fen": 100,
                },
            )
            assert created.status_code == 201
            account_id = created.json()["id"]
            adjusted = admin_client.post(
                f"/api/admin/accounts/{account_id}/balance-adjustments",
                json={"amount_fen": 100, "note": "测试充值"},
            )
            assert adjusted.status_code == 200
            updated = admin_client.patch(
                f"/api/admin/accounts/{account_id}",
                json={"unit_price_fen": 200},
            )
            assert updated.json()["unit_price_fen"] == 200
            assert admin_client.get("/api/admin/ledger").status_code == 200
            assert admin_client.get("/api/admin/audit").status_code == 200

            empty_client = TestClient(main_module.app)
            empty_client.post(
                "/api/auth/login",
                json={"username": "empty", "password": "empty-pass-123"},
            )
            response = empty_client.post(
                "/api/count?threshold_offset=17&min_contrast=23",
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-empty-0001"},
            )
            assert response.status_code == 402
            assert response.json()["detail"]["code"] == "insufficient_balance"
            assert database.get_account(account_id)["balance_fen"] == 100


def test_processing_timeout_does_not_charge() -> None:
    with TemporaryDirectory() as directory:
        database, _, user = _configured_database(directory)
        original_counter = main_module.count_dark_clusters

        def slow_counter(*args, **kwargs):
            time.sleep(0.05)
            return original_counter(*args, **kwargs)

        timeout_settings = replace(
            main_module.settings,
            request_timeout_seconds=0.001,
        )
        with (
            patch.object(main_module, "database", database),
            patch.object(main_module, "settings", timeout_settings),
            patch.object(main_module, "count_dark_clusters", slow_counter),
        ):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )
            response = client.post(
                "/api/count?threshold_offset=17&min_contrast=23",
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-timeout-001"},
            )
            assert response.status_code == 504
            assert database.get_account(user["id"])["balance_fen"] == 2_000
            assert not [
                entry
                for entry in database.list_ledger()
                if entry["entry_type"] == "charge"
            ]
