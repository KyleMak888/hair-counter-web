from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

from .config import Settings

ImageFile.LOAD_TRUNCATED_IMAGES = False

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}


@dataclass(frozen=True)
class DecodedImage:
    bgr: np.ndarray
    width: int
    height: int
    format: str


async def read_upload_limited(upload: UploadFile, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"图片不能超过 {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    return b"".join(chunks)


def decode_and_validate_image(data: bytes, settings: Settings) -> DecodedImage:
    try:
        with Image.open(io.BytesIO(data)) as raw:
            image_format = (raw.format or "").upper()
            if image_format not in ALLOWED_FORMATS:
                raise HTTPException(
                    status_code=415,
                    detail="仅支持 JPG、PNG、WebP 和 BMP 图片",
                )

            width, height = raw.size
            if width <= 0 or height <= 0:
                raise HTTPException(status_code=422, detail="图片尺寸无效")
            if width > settings.max_image_side or height > settings.max_image_side:
                raise HTTPException(
                    status_code=413,
                    detail=f"图片单边不能超过 {settings.max_image_side} 像素",
                )
            if width * height > settings.max_image_pixels:
                raise HTTPException(
                    status_code=413,
                    detail=f"图片总像素不能超过 {settings.max_image_pixels:,}",
                )

            raw.load()
            oriented = ImageOps.exif_transpose(raw)
            width, height = oriented.size
            rgb = oriented.convert("RGB")
            rgb_array = np.asarray(rgb, dtype=np.uint8)
            bgr = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
            return DecodedImage(
                bgr=bgr,
                width=width,
                height=height,
                format=image_format,
            )
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=413, detail="图片像素数量过大") from exc
    except Image.DecompressionBombWarning as exc:
        raise HTTPException(status_code=413, detail="图片像素数量过大") from exc
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=415, detail="无法识别该图片格式") from exc
    except OSError as exc:
        raise HTTPException(status_code=422, detail="图片损坏或无法完整解码") from exc
