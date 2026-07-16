from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class CounterConfig:
    """Configuration for separated dark clusters on a light background."""

    background_kernel_ratio: float = 0.10
    close_kernel_x_ratio: float = 0.014
    close_kernel_y_ratio: float = 0.010
    min_area_ratio: float = 0.00004
    max_area_ratio: float = 0.00300
    min_side_ratio: float = 0.008
    min_contrast: float = 35.0
    threshold_offset: int = 0
    border_margin_ratio: float = 0.005
    exclude_border: bool = False


@dataclass(frozen=True)
class DetectedItem:
    id: int
    bbox: list[int]
    center: list[float]
    area: int
    contrast: float
    confidence: float
    partial: bool
    strand_count: int = 1
    split_confidence: float = 1.0


@dataclass(frozen=True)
class CountResult:
    threshold: float
    image_width: int
    image_height: int
    items: list[DetectedItem]

    @property
    def count(self) -> int:
        return sum(item.strand_count for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "threshold": round(self.threshold, 2),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "items": [asdict(item) for item in self.items],
        }


def _odd(value: int, minimum: int = 3) -> int:
    value = max(minimum, int(value))
    return value if value % 2 else value + 1


def _kernel_size(length: int, ratio: float, minimum: int = 3) -> int:
    return _odd(round(length * ratio), minimum)


def _oriented_thickness(
    labels: np.ndarray,
    label_id: int,
    bbox: tuple[int, int, int, int],
) -> float:
    x, y, width, height = bbox
    component = labels[y : y + height, x : x + width] == label_id
    ys, xs = np.nonzero(component)
    if xs.size < 2:
        return 1.0
    rect_width, rect_height = cv2.minAreaRect(
        np.column_stack((xs, ys)).astype(np.float32)
    )[1]
    return max(1.0, min(float(rect_width), float(rect_height)))


def _find_profile_peaks(
    profile: np.ndarray,
    min_distance: int,
    min_prominence_ratio: float = 0.15,
    max_peaks: int = 6,
) -> list[tuple[int, float]]:
    if profile.size < 3 or float(profile.max()) <= 0:
        return []

    min_prominence = float(profile.max()) * min_prominence_ratio
    candidates: list[tuple[int, float]] = []
    for index in range(1, len(profile) - 1):
        value = float(profile[index])
        if value <= float(profile[index - 1]) or value < float(profile[index + 1]):
            continue
        left = profile[max(0, index - min_distance) : index]
        right = profile[index + 1 : index + 1 + min_distance]
        left_min = float(left.min()) if left.size else value
        right_min = float(right.min()) if right.size else value
        prominence = value - max(left_min, right_min)
        if prominence >= min_prominence:
            candidates.append((index, prominence))

    selected: list[tuple[int, float]] = []
    for index, prominence in sorted(candidates, key=lambda item: item[1], reverse=True):
        if all(abs(index - existing[0]) >= min_distance for existing in selected):
            selected.append((index, prominence))
        if len(selected) == max_peaks:
            break
    return sorted(selected)


def _estimate_strand_count(
    lightness: np.ndarray,
    labels: np.ndarray,
    label_id: int,
    bbox: tuple[int, int, int, int],
) -> tuple[int, float]:
    x, y, width, height = bbox
    image_height, image_width = lightness.shape
    pad = min(32, max(5, round(max(width, height) * 0.25)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1 = min(image_width, x + width + pad)
    y1 = min(image_height, y + height + pad)

    roi = lightness[y0:y1, x0:x1]
    component = (labels[y0:y1, x0:x1] == label_id).astype(np.uint8) * 255
    support = cv2.dilate(
        component,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )
    sigma = max(3.0, min(roi.shape) / 8.0)
    background = cv2.GaussianBlur(roi, (0, 0), sigmaX=sigma)
    darkness = cv2.subtract(background, roi)

    gray = roi.astype(np.float32)
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    weight = (support > 0).astype(np.float32) * darkness.astype(np.float32) / 255.0
    jxx = float(np.sum(weight * gradient_x * gradient_x))
    jyy = float(np.sum(weight * gradient_y * gradient_y))
    jxy = float(np.sum(weight * gradient_x * gradient_y))
    energy = jxx + jyy
    if energy <= 0:
        return 1, 1.0

    coherence = math.sqrt((jxx - jyy) ** 2 + 4.0 * jxy * jxy) / energy
    if coherence < 0.35:
        return 1, 1.0

    gradient_angle = 0.5 * math.degrees(math.atan2(2.0 * jxy, jxx - jyy))
    strand_angle = gradient_angle + 90.0
    center = (roi.shape[1] / 2.0, roi.shape[0] / 2.0)
    rotation = cv2.getRotationMatrix2D(center, strand_angle, 1.0)
    rotated_darkness = cv2.warpAffine(
        darkness,
        rotation,
        (roi.shape[1], roi.shape[0]),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REFLECT,
    )
    rotated_support = cv2.warpAffine(
        support,
        rotation,
        (roi.shape[1], roi.shape[0]),
        flags=cv2.INTER_NEAREST,
    )
    profile = (
        rotated_darkness.astype(np.float32)
        * (rotated_support > 0).astype(np.float32)
        / 255.0
    ).sum(axis=1)
    # Keep close peaks distinct when the source ROI is only a few pixels thick.
    if profile.size < 64:
        profile = cv2.resize(
            profile.reshape(-1, 1),
            (1, 64),
            interpolation=cv2.INTER_LINEAR,
        ).ravel()
    profile = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 7), 0).ravel()
    peaks = _find_profile_peaks(
        profile,
        min_distance=max(4, round(len(profile) * 0.08)),
    )
    if len(peaks) <= 1:
        return 1, 1.0

    average_prominence = float(np.mean([prominence for _, prominence in peaks]))
    prominence_score = min(1.0, average_prominence / max(1.0, float(profile.max()) * 0.30))
    split_confidence = float(np.clip(0.5 * coherence + 0.5 * prominence_score, 0.0, 0.99))
    return len(peaks), round(split_confidence, 3)


def _component_contrast(
    gray: np.ndarray,
    labels: np.ndarray,
    label_id: int,
    bbox: tuple[int, int, int, int],
) -> float:
    x, y, width, height = bbox
    image_height, image_width = gray.shape
    # ponytail: cap local work; raise only if large targets become valid inputs.
    pad = max(3, min(32, round(max(width, height) * 0.7)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(image_width, x + width + pad), min(image_height, y + height + pad)

    local_gray = gray[y0:y1, x0:x1]
    local_labels = labels[y0:y1, x0:x1]
    foreground = local_labels == label_id
    if not np.any(foreground):
        return 0.0

    component_mask = foreground.astype(np.uint8) * 255
    ring_size = _odd(max(3, 2 * pad + 1))
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_size, ring_size))
    dilated = cv2.dilate(component_mask, ring_kernel, iterations=1) > 0
    ring = dilated & (~foreground)

    foreground_mean = float(local_gray[foreground].mean())
    background_mean = float(local_gray[ring].mean()) if np.any(ring) else float(local_gray.mean())
    return max(0.0, background_mean - foreground_mean)


def _confidence(contrast: float, area: int, min_area: int, max_area: int) -> float:
    contrast_score = 1.0 - math.exp(-max(0.0, contrast) / 18.0)
    area_mid = math.sqrt(max(1, min_area) * max(min_area + 1, max_area))
    log_distance = abs(math.log(max(1, area) / max(1.0, area_mid)))
    area_score = math.exp(-0.45 * log_distance)
    return round(float(np.clip(0.75 * contrast_score + 0.25 * area_score, 0.0, 0.99)), 3)


def count_dark_clusters(image: np.ndarray, config: CounterConfig | None = None) -> CountResult:
    config = config or CounterConfig()
    if image is None or image.size == 0:
        raise ValueError("Input image is empty")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Expected BGR image with 3 channels, got {image.shape}")

    image_height, image_width = image.shape[:2]
    image_area = image_height * image_width

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]

    background_kernel_size = _kernel_size(
        min(image_height, image_width), config.background_kernel_ratio, 9
    )
    background_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (background_kernel_size, background_kernel_size)
    )
    blackhat = cv2.morphologyEx(lightness, cv2.MORPH_BLACKHAT, background_kernel)

    otsu_threshold, _ = cv2.threshold(
        blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    final_threshold = float(np.clip(otsu_threshold + config.threshold_offset, 0, 255))
    _, mask = cv2.threshold(blackhat, final_threshold, 255, cv2.THRESH_BINARY)

    close_width = _kernel_size(image_width, config.close_kernel_x_ratio, 3)
    close_height = _kernel_size(image_height, config.close_kernel_y_ratio, 3)
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (close_width, close_height)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )

    min_area = max(3, round(image_area * config.min_area_ratio))
    max_area = max(min_area + 1, round(image_area * config.max_area_ratio))
    min_side = max(2, round(min(image_height, image_width) * config.min_side_ratio))
    border_margin = max(1, round(min(image_height, image_width) * config.border_margin_ratio))

    candidates: list[dict[str, Any]] = []
    for label_id in range(1, component_count):
        x = int(stats[label_id, cv2.CC_STAT_LEFT])
        y = int(stats[label_id, cv2.CC_STAT_TOP])
        width = int(stats[label_id, cv2.CC_STAT_WIDTH])
        height = int(stats[label_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[label_id, cv2.CC_STAT_AREA])

        if not min_area <= area <= max_area:
            continue
        if max(width, height) < min_side:
            continue

        partial = (
            x <= border_margin
            or y <= border_margin
            or x + width >= image_width - border_margin
            or y + height >= image_height - border_margin
        )
        if config.exclude_border and partial:
            continue

        contrast = _component_contrast(
            lightness, labels, label_id, (x, y, width, height)
        )
        if contrast < config.min_contrast:
            continue

        center_x, center_y = centroids[label_id]
        candidates.append(
            {
                "label_id": label_id,
                "bbox": [x, y, width, height],
                "center": [float(center_x), float(center_y)],
                "area": area,
                "contrast": contrast,
                "partial": bool(partial),
                "thickness": _oriented_thickness(
                    labels, label_id, (x, y, width, height)
                ),
            }
        )

    if candidates:
        median_area = float(np.median([candidate["area"] for candidate in candidates]))
        median_thickness = float(
            np.median([candidate["thickness"] for candidate in candidates])
        )
        for candidate in candidates:
            suspected_cluster = (
                candidate["area"] >= median_area * 1.5
                or candidate["thickness"] >= median_thickness * 1.45
            )
            if suspected_cluster:
                strand_count, split_confidence = _estimate_strand_count(
                    lightness,
                    labels,
                    candidate["label_id"],
                    tuple(candidate["bbox"]),
                )
                candidate["strand_count"] = strand_count
                candidate["split_confidence"] = split_confidence
            else:
                candidate["strand_count"] = 1
                candidate["split_confidence"] = 1.0

    median_height = (
        float(np.median([candidate["bbox"][3] for candidate in candidates]))
        if candidates
        else 8.0
    )
    row_height = max(12.0, median_height * 2.5)
    candidates.sort(
        key=lambda candidate: (
            round(candidate["center"][1] / row_height),
            candidate["center"][0],
        )
    )

    items = [
        DetectedItem(
            id=index,
            bbox=candidate["bbox"],
            center=[round(candidate["center"][0], 2), round(candidate["center"][1], 2)],
            area=candidate["area"],
            contrast=round(candidate["contrast"], 2),
            confidence=_confidence(candidate["contrast"], candidate["area"], min_area, max_area),
            partial=candidate["partial"],
            strand_count=candidate["strand_count"],
            split_confidence=candidate["split_confidence"],
        )
        for index, candidate in enumerate(candidates, start=1)
    ]

    return CountResult(
        threshold=final_threshold,
        image_width=image_width,
        image_height=image_height,
        items=items,
    )
