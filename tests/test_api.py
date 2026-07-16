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


def test_svip_account_api_usage_and_plan_transitions() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        with patch.object(main_module, "database", database):
            admin_client = TestClient(main_module.app)
            admin_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin-pass-123"},
            )
            rejected_create = admin_client.post(
                "/api/admin/accounts",
                json={
                    "username": "priced-svip",
                    "display_name": "错误买断机构",
                    "password": "priced-svip-pass",
                    "unit_price_fen": 10,
                    "plan": "svip",
                },
            )
            assert rejected_create.status_code == 422

            created = admin_client.post(
                "/api/admin/accounts",
                json={
                    "username": "svip-client",
                    "display_name": "买断客户",
                    "password": "svip-client-pass",
                    "unit_price_fen": 0,
                    "plan": "svip",
                },
            )
            assert created.status_code == 201
            account_id = created.json()["id"]
            assert created.json()["plan"] == "svip"

            client = TestClient(main_module.app)
            login = client.post(
                "/api/auth/login",
                json={"username": "svip-client", "password": "svip-client-pass"},
            )
            assert login.status_code == 200
            assert login.json()["plan"] == "svip"
            assert client.get("/api/me").json()["plan"] == "svip"

            invalid = client.post(
                "/api/count",
                files={"file": ("invalid.jpg", b"not-an-image", "image/jpeg")},
                headers={"Idempotency-Key": "request-api-svip-invalid"},
            )
            assert invalid.status_code == 415

            first = client.post(
                "/api/count?threshold_offset=17&min_contrast=23",
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-svip-single-0001"},
            )
            assert first.status_code == 200
            payload = first.json()
            assert payload["billing"] == {
                "request_id": "request-api-svip-single-0001",
                "billable_count": payload["count"],
                "unit_price_fen": 0,
                "charged_amount_fen": 0,
                "balance_fen": 0,
                "plan": "svip",
            }
            replay = client.post(
                "/api/count?threshold_offset=17&min_contrast=23",
                files={"file": _image_file()},
                headers={"Idempotency-Key": "request-api-svip-single-0001"},
            )
            assert replay.json() == payload

            image_file = _image_file()
            batch = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=[("files", image_file), ("files", image_file)],
                headers={"Idempotency-Key": "request-api-svip-batch-0001"},
            )
            assert batch.status_code == 200
            batch_payload = batch.json()
            assert batch_payload["succeeded"] == 2
            assert batch_payload["total_charged_fen"] == 0
            assert batch_payload["balance_fen"] == 0
            assert all(
                item["result"]["billing"]["plan"] == "svip"
                for item in batch_payload["results"]
            )
            assert not [
                entry
                for entry in database.list_ledger()
                if entry["account_id"] == account_id and entry["entry_type"] == "charge"
            ]

            price_change = admin_client.patch(
                f"/api/admin/accounts/{account_id}",
                json={"unit_price_fen": 25},
            )
            assert price_change.status_code == 422
            balance_change = admin_client.post(
                f"/api/admin/accounts/{account_id}/balance-adjustments",
                json={"amount_fen": 500, "note": "错误充值"},
            )
            assert balance_change.status_code == 422

            downgraded = admin_client.patch(
                f"/api/admin/accounts/{account_id}",
                json={"plan": "standard", "unit_price_fen": 17},
            )
            assert downgraded.status_code == 200
            assert downgraded.json()["unit_price_fen"] == 17
            assert client.get("/api/me").json()["plan"] == "standard"
            adjusted = admin_client.post(
                f"/api/admin/accounts/{account_id}/balance-adjustments",
                json={"amount_fen": 500, "note": "恢复按量后充值"},
            )
            assert adjusted.status_code == 200

            upgraded = admin_client.patch(
                f"/api/admin/accounts/{account_id}",
                json={"plan": "svip"},
            )
            assert upgraded.status_code == 200
            assert upgraded.json()["unit_price_fen"] == 17
            assert upgraded.json()["balance_fen"] == 500
            current = client.get("/api/me")
            assert current.status_code == 200
            assert current.json()["plan"] == "svip"
            assert current.json()["balance_fen"] == 500

            audit = admin_client.get("/api/admin/audit").json()
            plan_changes = [
                entry["details"]["plan"]
                for entry in audit
                if entry["action"] == "update_account" and "plan" in entry["details"]
            ]
            assert "standard" in plan_changes
            assert "svip" in plan_changes


def test_svip_processing_timeout_does_not_record_usage_or_charge() -> None:
    with TemporaryDirectory() as directory:
        database, admin, user = _configured_database(directory)
        database.update_account(admin["id"], user["id"], plan="svip")
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
            listed = next(item for item in database.list_accounts() if item["id"] == user["id"])
            assert listed["total_billable_count"] == 0


def test_batch_count_processes_multiple_images_and_charges_per_image() -> None:
    with TemporaryDirectory() as directory:
        database, _, user = _configured_database(directory)
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )

            image_file = _image_file()
            files = [("files", image_file), ("files", image_file)]
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=files,
                headers={"Idempotency-Key": "request-api-batch-0001"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["batch_id"] == "request-api-batch-0001"
            assert payload["succeeded"] == 2
            assert payload["failed"] == 0
            assert len(payload["results"]) == 2
            assert len(payload["errors"]) == 0

            first_count = payload["results"][0]["result"]["count"]
            second_count = payload["results"][1]["result"]["count"]
            assert first_count > 0
            assert second_count > 0
            assert payload["total_count"] == first_count + second_count
            assert payload["total_charged_fen"] == (first_count + second_count) * 10
            assert payload["balance_fen"] == 2_000 - payload["total_charged_fen"]

            replay = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=files,
                headers={"Idempotency-Key": "request-api-batch-0001"},
            )
            assert replay.status_code == 200
            replay_payload = replay.json()
            assert replay_payload["total_count"] == payload["total_count"]
            assert replay_payload["total_charged_fen"] == payload["total_charged_fen"]
            assert database.get_account(user["id"])["balance_fen"] == 2_000 - payload["total_charged_fen"]


def test_batch_uses_each_items_current_plan_when_account_changes_mid_batch() -> None:
    with TemporaryDirectory() as directory:
        database, admin, user = _configured_database(directory)
        original_charge = database.charge_recognition
        charge_calls = 0

        def charge_and_upgrade(*args, **kwargs):
            nonlocal charge_calls
            result = original_charge(*args, **kwargs)
            charge_calls += 1
            if charge_calls == 1:
                database.update_account(admin["id"], user["id"], plan="svip")
            return result

        with (
            patch.object(main_module, "database", database),
            patch.object(database, "charge_recognition", side_effect=charge_and_upgrade),
        ):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )
            image_file = _image_file()
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=[("files", image_file), ("files", image_file)],
                headers={"Idempotency-Key": "request-api-batch-plan-switch"},
            )

        assert response.status_code == 200
        payload = response.json()
        billings = [item["result"]["billing"] for item in payload["results"]]
        assert [billing["plan"] for billing in billings] == ["standard", "svip"]
        assert billings[0]["charged_amount_fen"] > 0
        assert billings[1]["charged_amount_fen"] == 0
        assert payload["total_charged_fen"] == billings[0]["charged_amount_fen"]
        assert payload["balance_fen"] == 2_000 - billings[0]["charged_amount_fen"]


def test_batch_with_invalid_image_continues_and_reports_error() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )

            image_file = _image_file()
            files = [
                ("files", image_file),
                ("files", ("invalid.jpg", b"not-an-image", "image/jpeg")),
            ]
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=files,
                headers={"Idempotency-Key": "request-api-batch-mixed-0001"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["succeeded"] == 1
            assert payload["failed"] == 1
            assert len(payload["results"]) == 1
            assert len(payload["errors"]) == 1
            assert payload["errors"][0]["index"] == 1
            assert payload["errors"][0]["filename"] == "invalid.jpg"


def test_batch_insufficient_balance_stops_and_reports_remaining() -> None:
    with TemporaryDirectory() as directory:
        database, admin, _ = _configured_database(directory)
        limited = database.create_account(
            admin["id"], "batchlimited", "批量余额不足", "limited-pass-123", 100
        )
        database.adjust_balance(admin["id"], limited["id"], 100, "测试充值")
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "batchlimited", "password": "limited-pass-123"},
            )

            image_file = _image_file()
            files = [("files", image_file), ("files", image_file), ("files", image_file)]
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=files,
                headers={"Idempotency-Key": "request-api-batch-insufficient-01"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["succeeded"] >= 0
            assert payload["failed"] >= 1
            assert any("余额不足" in err["error"] for err in payload["errors"])
            assert database.get_account(limited["id"])["balance_fen"] == 100 - payload["total_charged_fen"]


def test_batch_exceeds_max_size_rejected() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        oversized_settings = replace(main_module.settings, max_batch_size=2)
        with (
            patch.object(main_module, "database", database),
            patch.object(main_module, "settings", oversized_settings),
        ):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )

            image_file = _image_file()
            files = [("files", image_file), ("files", image_file), ("files", image_file)]
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=files,
                headers={"Idempotency-Key": "request-api-batch-oversize-001"},
            )
            assert response.status_code == 413


def test_batch_requires_authentication() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            image_file = _image_file()
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=[("files", image_file)],
                headers={"Idempotency-Key": "request-api-batch-unauthorized"},
            )
            assert response.status_code == 401


def test_batch_empty_files_rejected() -> None:
    with TemporaryDirectory() as directory:
        database, _, _ = _configured_database(directory)
        with patch.object(main_module, "database", database):
            client = TestClient(main_module.app)
            client.post(
                "/api/auth/login",
                json={"username": "clinic", "password": "clinic-pass-123"},
            )
            response = client.post(
                "/api/count/batch?threshold_offset=17&min_contrast=23",
                files=[],
                headers={"Idempotency-Key": "request-api-batch-empty-001"},
            )
            assert response.status_code in (400, 422)
