from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .config import settings
from .counter import CounterConfig, count_dark_clusters
from .database import (
    AccountDisabled,
    AccountNotFound,
    Database,
    DatabaseError,
    DuplicateUsername,
    InsufficientBalance,
    InvalidBalanceAdjustment,
    InvalidCredentials,
)
from .image_io import decode_and_validate_image, read_upload_limited
from .schemas import (
    AccountCreateRequest,
    AccountResponse,
    AccountUpdateRequest,
    AuditLogResponse,
    BalanceAdjustmentRequest,
    BatchCountResponse,
    BatchItemError,
    BatchItemResult,
    CountResponse,
    HealthResponse,
    LedgerEntryResponse,
    LoginRequest,
    PasswordResetRequest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("hair-counter")
processing_slots = asyncio.Semaphore(settings.max_processing_concurrency)
database = Database(settings.database_path, settings.session_ttl_seconds)
SESSION_COOKIE = "hair_session"


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    if bool(settings.admin_username) != bool(settings.admin_password):
        raise RuntimeError("ADMIN_USERNAME and ADMIN_PASSWORD must be set together")
    if settings.admin_password and len(settings.admin_password) < 8:
        raise RuntimeError("ADMIN_PASSWORD must contain at least 8 characters")
    if not settings.admin_username and not database.has_admin():
        raise RuntimeError(
            "ADMIN_USERNAME and ADMIN_PASSWORD are required for the first startup"
        )
    created_admin = database.bootstrap_admin(
        settings.admin_username,
        settings.admin_password,
    )
    logger.info(
        "starting app version=%s max_upload_bytes=%s max_pixels=%s concurrency=%s database=%s",
        settings.app_version,
        settings.max_upload_bytes,
        settings.max_image_pixels,
        settings.max_processing_concurrency,
        settings.database_path,
    )
    if created_admin:
        logger.info("initial administrator created username=%s", settings.admin_username)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)

# Same-origin Nginx deployment does not need CORS. These localhost origins only
# make local frontend development convenient and can safely be removed later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


def _session_account(request: Request) -> dict[str, Any]:
    account = database.account_for_session(request.cookies.get(SESSION_COOKIE))
    if account is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return account


def _admin_account(account: dict[str, Any] = Depends(_session_account)) -> dict[str, Any]:
    if account["role"] != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    return account


def _account_response(account: dict[str, Any]) -> AccountResponse:
    return AccountResponse(**account)


def _database_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AccountNotFound):
        return HTTPException(status_code=404, detail="账号不存在")
    if isinstance(exc, DuplicateUsername):
        return HTTPException(status_code=409, detail="用户名已存在")
    if isinstance(exc, (InvalidBalanceAdjustment, DatabaseError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="账户操作失败")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        max_upload_bytes=settings.max_upload_bytes,
        max_image_pixels=settings.max_image_pixels,
        max_image_side=settings.max_image_side,
        max_batch_size=settings.max_batch_size,
    )


@app.post("/api/auth/login", response_model=AccountResponse)
def login(credentials: LoginRequest, response: Response) -> AccountResponse:
    try:
        account = database.authenticate(credentials.username, credentials.password)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from exc
    except AccountDisabled as exc:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员") from exc

    token = database.create_session(account["id"])
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return _account_response(account)


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    database.delete_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@app.get("/api/me", response_model=AccountResponse)
def me(account: dict[str, Any] = Depends(_session_account)) -> AccountResponse:
    return _account_response(account)


@app.get("/api/admin/accounts", response_model=list[AccountResponse])
def admin_accounts(_: dict[str, Any] = Depends(_admin_account)) -> list[AccountResponse]:
    return [_account_response(account) for account in database.list_accounts()]


@app.post("/api/admin/accounts", response_model=AccountResponse, status_code=201)
def create_account(
    payload: AccountCreateRequest,
    admin: dict[str, Any] = Depends(_admin_account),
) -> AccountResponse:
    try:
        account = database.create_account(
            admin["id"],
            payload.username,
            payload.display_name,
            payload.password,
            payload.unit_price_fen,
            payload.plan,
        )
    except DatabaseError as exc:
        raise _database_http_error(exc) from exc
    return _account_response(account)


@app.patch("/api/admin/accounts/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    payload: AccountUpdateRequest,
    admin: dict[str, Any] = Depends(_admin_account),
) -> AccountResponse:
    try:
        account = database.update_account(
            admin["id"],
            account_id,
            display_name=payload.display_name,
            unit_price_fen=payload.unit_price_fen,
            active=payload.active,
            plan=payload.plan,
        )
    except DatabaseError as exc:
        raise _database_http_error(exc) from exc
    return _account_response(account)


@app.post("/api/admin/accounts/{account_id}/password", status_code=204)
def reset_password(
    account_id: int,
    payload: PasswordResetRequest,
    admin: dict[str, Any] = Depends(_admin_account),
) -> Response:
    try:
        database.reset_password(admin["id"], account_id, payload.password)
    except DatabaseError as exc:
        raise _database_http_error(exc) from exc
    return Response(status_code=204)


@app.post(
    "/api/admin/accounts/{account_id}/balance-adjustments",
    response_model=AccountResponse,
)
def adjust_balance(
    account_id: int,
    payload: BalanceAdjustmentRequest,
    admin: dict[str, Any] = Depends(_admin_account),
) -> AccountResponse:
    try:
        account = database.adjust_balance(
            admin["id"],
            account_id,
            payload.amount_fen,
            payload.note,
        )
    except DatabaseError as exc:
        raise _database_http_error(exc) from exc
    return _account_response(account)


@app.get("/api/admin/ledger", response_model=list[LedgerEntryResponse])
def admin_ledger(
    limit: int = Query(100, ge=1, le=500),
    _: dict[str, Any] = Depends(_admin_account),
) -> list[LedgerEntryResponse]:
    return [LedgerEntryResponse(**entry) for entry in database.list_ledger(limit)]


@app.get("/api/admin/audit", response_model=list[AuditLogResponse])
def admin_audit(
    limit: int = Query(100, ge=1, le=500),
    _: dict[str, Any] = Depends(_admin_account),
) -> list[AuditLogResponse]:
    return [AuditLogResponse(**entry) for entry in database.list_audit_logs(limit)]


@app.post("/api/count", response_model=CountResponse)
async def count(
    file: UploadFile = File(description="JPG/PNG/WebP/BMP image"),
    exclude_border: bool = Query(False, description="Ignore edge-touching objects"),
    threshold_offset: int = Query(0, ge=-100, le=100),
    min_contrast: float = Query(35.0, ge=0.0, le=255.0),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
    account: dict[str, Any] = Depends(_session_account),
) -> CountResponse:
    cached = database.cached_recognition(account["id"], idempotency_key)
    if cached is not None:
        logger.info(
            "count replay account_id=%s request_id=%s count=%s",
            account["id"],
            idempotency_key,
            cached["count"],
        )
        return CountResponse(**cached)

    started = time.perf_counter()
    data = await read_upload_limited(file, settings.max_upload_bytes)
    decoded = await run_in_threadpool(decode_and_validate_image, data, settings)

    config = CounterConfig(
        exclude_border=exclude_border,
        threshold_offset=threshold_offset,
        min_contrast=min_contrast,
    )

    try:
        async with processing_slots:
            result = await asyncio.wait_for(
                run_in_threadpool(count_dark_clusters, decoded.bgr, config),
                timeout=settings.request_timeout_seconds,
            )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="图片处理超时，请缩小图片后重试") from exc

    processing_ms = round((time.perf_counter() - started) * 1000.0, 2)
    payload = result.to_dict()
    payload["processing_ms"] = processing_ms
    try:
        billed_payload, replayed = database.charge_recognition(
            account["id"],
            idempotency_key,
            result.count,
            payload,
        )
    except InsufficientBalance as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "insufficient_balance",
                "message": "余额不足，请联系管理员充值",
                "balance_fen": exc.balance_fen,
                "required_fen": exc.required_fen,
            },
        ) from exc
    except AccountDisabled as exc:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员") from exc

    logger.info(
        "count complete account_id=%s width=%s height=%s count=%s charge_fen=%s replayed=%s processing_ms=%s",
        account["id"],
        decoded.width,
        decoded.height,
        result.count,
        billed_payload["billing"]["charged_amount_fen"],
        replayed,
        processing_ms,
    )
    return CountResponse(**billed_payload)


@app.post("/api/count/batch", response_model=BatchCountResponse)
async def count_batch(
    files: list[UploadFile] = File(description="JPG/PNG/WebP/BMP images"),
    exclude_border: bool = Query(False, description="Ignore edge-touching objects"),
    threshold_offset: int = Query(0, ge=-100, le=100),
    min_contrast: float = Query(35.0, ge=0.0, le=255.0),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
    account: dict[str, Any] = Depends(_session_account),
) -> BatchCountResponse:
    if not files:
        raise HTTPException(status_code=400, detail="未选择任何图片")
    if len(files) > settings.max_batch_size:
        raise HTTPException(
            status_code=413,
            detail=f"批量处理最多 {settings.max_batch_size} 张图片",
        )

    config = CounterConfig(
        exclude_border=exclude_border,
        threshold_offset=threshold_offset,
        min_contrast=min_contrast,
    )

    results: list[BatchItemResult] = []
    errors: list[BatchItemError] = []
    total_count = 0
    total_charged_fen = 0

    for index, upload_file in enumerate(files):
        filename = upload_file.filename or f"image-{index}"
        item_key = f"{idempotency_key}-{index}"

        cached = database.cached_recognition(account["id"], item_key)
        if cached is not None:
            charged = cached.get("billing", {}).get("charged_amount_fen", 0)
            total_count += cached.get("count", 0)
            total_charged_fen += charged
            results.append(BatchItemResult(
                index=index,
                filename=filename,
                result=CountResponse(**cached),
            ))
            logger.info(
                "batch replay account_id=%s batch_key=%s index=%s count=%s",
                account["id"], idempotency_key, index, cached.get("count"),
            )
            continue

        try:
            started = time.perf_counter()
            data = await read_upload_limited(upload_file, settings.max_upload_bytes)
            decoded = await run_in_threadpool(decode_and_validate_image, data, settings)

            async with processing_slots:
                result = await asyncio.wait_for(
                    run_in_threadpool(count_dark_clusters, decoded.bgr, config),
                    timeout=settings.request_timeout_seconds,
                )

            processing_ms = round((time.perf_counter() - started) * 1000.0, 2)
            payload = result.to_dict()
            payload["processing_ms"] = processing_ms

            billed_payload, replayed = database.charge_recognition(
                account["id"],
                item_key,
                result.count,
                payload,
            )
            total_count += result.count
            total_charged_fen += billed_payload["billing"]["charged_amount_fen"]
            results.append(BatchItemResult(
                index=index,
                filename=filename,
                result=CountResponse(**billed_payload),
            ))
            logger.info(
                "batch count complete account_id=%s batch_key=%s index=%s filename=%s count=%s charge_fen=%s",
                account["id"], idempotency_key, index, filename,
                result.count, billed_payload["billing"]["charged_amount_fen"],
            )

        except asyncio.TimeoutError:
            errors.append(BatchItemError(index=index, filename=filename, error="图片处理超时，请缩小图片后重试"))
            logger.warning(
                "batch item timeout account_id=%s batch_key=%s index=%s filename=%s",
                account["id"], idempotency_key, index, filename,
            )

        except InsufficientBalance as exc:
            for remaining_index in range(index, len(files)):
                remaining_name = files[remaining_index].filename or f"image-{remaining_index}"
                errors.append(BatchItemError(
                    index=remaining_index,
                    filename=remaining_name,
                    error="余额不足，已跳过",
                ))
            logger.warning(
                "batch insufficient balance account_id=%s batch_key=%s index=%s balance_fen=%s required_fen=%s",
                account["id"], idempotency_key, index, exc.balance_fen, exc.required_fen,
            )
            break

        except AccountDisabled:
            for remaining_index in range(index, len(files)):
                remaining_name = files[remaining_index].filename or f"image-{remaining_index}"
                errors.append(BatchItemError(
                    index=remaining_index,
                    filename=remaining_name,
                    error="账号已停用，请联系管理员",
                ))
            break

        except HTTPException as exc:
            error_message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            errors.append(BatchItemError(index=index, filename=filename, error=error_message))
            logger.warning(
                "batch item failed account_id=%s batch_key=%s index=%s filename=%s status=%s",
                account["id"], idempotency_key, index, filename, exc.status_code,
            )

        except Exception as exc:
            errors.append(BatchItemError(index=index, filename=filename, error=str(exc)))
            logger.warning(
                "batch item failed account_id=%s batch_key=%s index=%s filename=%s error=%s",
                account["id"], idempotency_key, index, filename, exc,
            )

    current_account = database.get_account(account["id"])
    response = BatchCountResponse(
        batch_id=idempotency_key,
        total_count=total_count,
        total_charged_fen=total_charged_fen,
        balance_fen=current_account["balance_fen"],
        succeeded=len(results),
        failed=len(errors),
        results=results,
        errors=errors,
    )
    logger.info(
        "batch complete account_id=%s batch_key=%s succeeded=%s failed=%s total_count=%s total_charged_fen=%s",
        account["id"], idempotency_key, response.succeeded, response.failed,
        total_count, total_charged_fen,
    )
    return response
