from pathlib import Path
import math
import sys
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.counter import CounterConfig, _component_contrast, count_dark_clusters  # noqa: E402
from app.counter_enhanced import (  # noqa: E402
    EnhancedCounterConfig,
    count_dark_clusters_enhanced,
)
from app.image_io import decode_and_validate_image  # noqa: E402
from app.main import health  # noqa: E402
from app.schemas import CountResponse  # noqa: E402


def _synthetic_scene(kind: str) -> np.ndarray:
    image = np.full((500, 800, 3), 235, dtype=np.uint8)
    for row in range(2):
        for column in range(4):
            cv2.ellipse(
                image,
                (90 + column * 180, 70 + row * 100),
                (22, 4),
                -20,
                0,
                360,
                (20, 20, 20),
                -1,
            )

    if kind in {"double", "triple"}:
        offsets = (-6, 6) if kind == "double" else (-11, 0, 11)
        angle = math.radians(-20)
        for offset in offsets:
            center = (
                round(400 - math.sin(angle) * offset),
                round(370 + math.cos(angle) * offset),
            )
            cv2.ellipse(image, center, (22, 4), -20, 0, 360, (20, 20, 20), -1)
    elif kind == "thick":
        cv2.ellipse(image, (400, 370), (28, 11), -20, 0, 360, (20, 20, 20), -1)
    elif kind == "shadow":
        cv2.ellipse(image, (404, 376), (26, 7), -20, 0, 360, (115, 115, 115), -1)
        cv2.ellipse(image, (400, 370), (22, 4), -20, 0, 360, (20, 20, 20), -1)
    elif kind == "broken":
        cv2.line(image, (360, 384), (394, 372), (20, 20, 20), 7)
        cv2.line(image, (401, 369), (440, 355), (20, 20, 20), 7)
    elif kind == "cross":
        cv2.ellipse(image, (396, 365), (28, 4), -28, 0, 360, (20, 20, 20), -1)
        cv2.ellipse(image, (405, 378), (28, 4), -48, 0, 360, (20, 20, 20), -1)
    return image


def test_synthetic_count_is_stable() -> None:
    image = np.full((288, 360, 3), 235, dtype=np.uint8)
    for row in range(4):
        for column in range(5):
            center = (40 + column * 65, 40 + row * 60)
            cv2.ellipse(image, center, (7, 3), 0, 0, 360, (20, 20, 20), -1)

    result = count_dark_clusters(image, CounterConfig())
    assert result.count == 20
    assert result.count == sum(item.strand_count for item in result.items)
    assert result.image_width == 360
    assert result.image_height == 288


def test_cluster_split_synthetic_cases() -> None:
    expected_counts = {
        "double": 10,
        "triple": 11,
        "cross": 10,
        "thick": 9,
        "shadow": 9,
        "broken": 9,
    }
    for kind, expected in expected_counts.items():
        result = count_dark_clusters(_synthetic_scene(kind), CounterConfig())
        assert result.count == expected, kind
        assert result.count == sum(item.strand_count for item in result.items)


def test_touching_hair_fixture_counts_marked_clusters() -> None:
    image = cv2.imread(str(ROOT / "tests" / "fixtures" / "touching-hairs.png"))
    result = count_dark_clusters(image, CounterConfig())
    marked_boxes = [
        ((830, 46, 155, 118), 2),
        ((882, 282, 122, 99), 3),
        ((864, 786, 159, 158), 2),
    ]
    for (x, y, width, height), expected in marked_boxes:
        actual = sum(
            item.strand_count
            for item in result.items
            if x <= item.center[0] <= x + width and y <= item.center[1] <= y + height
        )
        assert actual == expected

    response = CountResponse(**result.to_dict(), processing_ms=0)
    assert response.count == sum(item.strand_count for item in response.items)
    assert all(1 <= item.strand_count <= 6 for item in response.items)
    assert all(0 <= item.split_confidence <= 1 for item in response.items)


def test_low_resolution_source_counts_marked_clusters() -> None:
    image = cv2.imread(str(ROOT / "tests" / "fixtures" / "touching-hairs-source.jpg"))
    result = count_dark_clusters(
        image,
        CounterConfig(threshold_offset=17, min_contrast=23),
    )
    marked_boxes = [
        ((194, 11, 36, 28), 2),
        ((206, 66, 29, 23), 3),
        ((202, 184, 38, 37), 2),
    ]
    for (x, y, width, height), expected in marked_boxes:
        actual = sum(
            item.strand_count
            for item in result.items
            if x <= item.center[0] <= x + width and y <= item.center[1] <= y + height
        )
        assert actual == expected


def test_enhanced_defaults_use_balanced_preset() -> None:
    config = EnhancedCounterConfig()
    assert config.threshold_offset == -10
    assert config.min_contrast == 25.0
    assert config.min_confidence_threshold == 0.4
    assert config.scale_factors == (0.8, 1.0, 1.2)


def test_enhanced_counter_preserves_cluster_split_estimation() -> None:
    config = EnhancedCounterConfig(
        threshold_offset=0,
        min_contrast=35,
        enable_multiscale=False,
        use_adaptive_threshold=False,
        enable_opening=False,
        use_confidence_filter=False,
    )
    expected_counts = {
        "double": 10,
        "triple": 11,
    }
    for kind, expected in expected_counts.items():
        result = count_dark_clusters_enhanced(_synthetic_scene(kind), config)
        assert result.count == expected, kind
        assert any(item.strand_count > 1 for item in result.items), kind
        assert result.count == sum(item.strand_count for item in result.items)


def test_oversized_image_is_rejected_before_decode() -> None:
    raw = MagicMock()
    raw.__enter__.return_value = raw
    raw.format = "PNG"
    raw.size = (5000, 5000)

    with patch("app.image_io.Image.open", return_value=raw):
        try:
            decode_and_validate_image(b"image", Settings())
        except HTTPException as exc:
            assert exc.status_code == 413
        else:
            raise AssertionError("oversized image was accepted")

    raw.load.assert_not_called()


def test_contrast_ring_size_is_capped() -> None:
    gray = np.full((30, 2000), 200, dtype=np.uint8)
    labels = np.zeros_like(gray, dtype=np.int32)
    labels[10:20, :] = 1
    gray[10:20, :] = 20

    with patch(
        "app.counter.cv2.getStructuringElement",
        return_value=np.ones((3, 3), dtype=np.uint8),
    ) as kernel:
        assert _component_contrast(gray, labels, 1, (0, 10, 2000, 10)) > 0

    assert kernel.call_args.args[1] == (65, 65)


def test_health_reports_frontend_limits() -> None:
    response = health()
    assert response.max_upload_bytes > 0
    assert response.max_image_pixels > 0
    assert response.max_image_side > 0
    assert response.max_batch_size > 0
