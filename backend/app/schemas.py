from __future__ import annotations

from pydantic import BaseModel, Field


USERNAME_PATTERN = r"^[A-Za-z0-9_.-]+$"


class DetectedItemResponse(BaseModel):
    id: int = Field(ge=1)
    bbox: list[int] = Field(description="[x, y, width, height] in source-image pixels")
    center: list[float] = Field(description="[x, y] in source-image pixels")
    area: int = Field(ge=1)
    contrast: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1, description="Heuristic score, not an ML probability")
    partial: bool
    strand_count: int = Field(default=1, ge=1)
    split_confidence: float = Field(default=1.0, ge=0, le=1)


class BillingResponse(BaseModel):
    request_id: str
    billable_count: int = Field(ge=0)
    unit_price_fen: int = Field(ge=0)
    charged_amount_fen: int = Field(ge=0)
    balance_fen: int = Field(ge=0)


class CountResponse(BaseModel):
    count: int = Field(ge=0)
    threshold: float = Field(ge=0, le=255)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    processing_ms: float = Field(ge=0)
    items: list[DetectedItemResponse]
    billing: BillingResponse | None = None


class BatchItemError(BaseModel):
    index: int = Field(ge=0)
    filename: str
    error: str


class BatchItemResult(BaseModel):
    index: int = Field(ge=0)
    filename: str
    result: CountResponse


class BatchCountResponse(BaseModel):
    batch_id: str
    total_count: int = Field(ge=0)
    total_charged_fen: int = Field(ge=0)
    balance_fen: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    results: list[BatchItemResult]
    errors: list[BatchItemError]


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)


class AccountResponse(BaseModel):
    id: int = Field(ge=1)
    username: str
    display_name: str
    role: str
    active: bool
    unit_price_fen: int = Field(ge=0)
    balance_fen: int = Field(ge=0)
    created_at: str
    updated_at: str
    total_billable_count: int = Field(default=0, ge=0)
    total_spent_fen: int = Field(default=0, ge=0)
    last_recognition_at: str | None = None


class AccountCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    unit_price_fen: int = Field(default=0, ge=0)


class AccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    unit_price_fen: int | None = Field(default=None, ge=0)
    active: bool | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class BalanceAdjustmentRequest(BaseModel):
    amount_fen: int
    note: str = Field(min_length=1, max_length=200)


class LedgerEntryResponse(BaseModel):
    id: int
    entry_type: str
    amount_fen: int
    balance_after_fen: int = Field(ge=0)
    billable_count: int | None = None
    unit_price_fen: int | None = None
    note: str
    created_at: str
    account_id: int
    username: str
    display_name: str
    admin_username: str | None = None


class AuditLogResponse(BaseModel):
    id: int
    action: str
    details_json: str
    details: dict
    created_at: str
    admin_username: str | None = None
    target_username: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    max_upload_bytes: int = Field(gt=0)
    max_image_pixels: int = Field(gt=0)
    max_image_side: int = Field(gt=0)
    max_batch_size: int = Field(gt=0)
