"""Enhanced hair counter with improved detection algorithms."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .counter import (
    CounterConfig,
    DetectedItem,
    CountResult,
    _odd,
    _kernel_size,
    _find_profile_peaks,
    _component_contrast,
    _confidence,
)


@dataclass(frozen=True)
class EnhancedCounterConfig(CounterConfig):
    """Configuration for the opt-in enhanced v2 detector."""

    min_contrast: float = 35.0
    threshold_offset: int = 0

    # Multi-scale detection
    enable_multiscale: bool = True
    scale_factors: tuple[float, ...] = (0.9, 1.0, 1.1)

    # Adaptive thresholding
    use_adaptive_threshold: bool = False
    adaptive_block_size_ratio: float = 0.05  # Relative to image size

    # Morphological improvements
    enable_opening: bool = False
    opening_kernel_ratio: float = 0.005

    # Detection merging
    nms_iou_threshold: float = 0.4
    consensus_iou_threshold: float = 0.20
    min_consensus_votes: int = 2

    # Enhanced filtering
    use_confidence_filter: bool = True
    min_confidence_threshold: float = 0.45
    min_aspect_ratio: float = 2.2
    strong_aspect_ratio: float = 3.2
    max_thickness_ratio: float = 0.55
    min_fill_ratio: float = 0.08
    max_fill_ratio: float = 0.92
    strong_contrast: float = 55.0


def _apply_clahe(image: np.ndarray) -> np.ndarray:
    """Apply CLAHE to improve local contrast."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel = lab[:, :, 0]

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)

    lab[:, :, 0] = l_channel
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _compute_iou(bbox1: list[int], bbox2: list[int]) -> float:
    """Compute Intersection over Union between two bounding boxes."""
    x1, y1, w1, h1 = bbox1
    x2, y2, w2, h2 = bbox2

    # Compute intersection
    x_left = max(x1, x2)
    y_top = max(y1, y2)
    x_right = min(x1 + w1, x2 + w2)
    y_bottom = min(y1 + h1, y2 + h2)

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area1 = w1 * h1
    area2 = w2 * h2
    union = area1 + area2 - intersection

    return intersection / union if union > 0 else 0.0


def _non_max_suppression_candidates(
    candidates: list[dict[str, Any]],
    iou_threshold: float = 0.4
) -> list[dict[str, Any]]:
    """Apply Non-Maximum Suppression to remove duplicate detections."""
    if not candidates:
        return []

    sorted_candidates = sorted(candidates, key=lambda item: item["confidence"], reverse=True)

    keep = []
    while sorted_candidates:
        best = sorted_candidates.pop(0)
        keep.append(best)

        sorted_candidates = [
            item for item in sorted_candidates
            if _compute_iou(best["bbox"], item["bbox"]) < iou_threshold
        ]

    return keep


def _component_mask_thickness(component_mask: np.ndarray) -> float:
    ys, xs = np.nonzero(component_mask)
    if xs.size < 2:
        return 1.0
    rect_width, rect_height = cv2.minAreaRect(
        np.column_stack((xs, ys)).astype(np.float32)
    )[1]
    return max(1.0, min(float(rect_width), float(rect_height)))


def _scale_component_candidate(
    component_mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    scale: float,
    source_width: int,
    source_height: int,
) -> tuple[list[int], np.ndarray]:
    x, y, width, height = bbox
    if scale == 1.0:
        return [x, y, width, height], component_mask

    x0 = max(0, min(source_width - 1, round(x / scale)))
    y0 = max(0, min(source_height - 1, round(y / scale)))
    x1 = min(source_width, max(x0 + 1, round((x + width) / scale)))
    y1 = min(source_height, max(y0 + 1, round((y + height) / scale)))
    scaled_width = x1 - x0
    scaled_height = y1 - y0
    scaled_mask = cv2.resize(
        component_mask,
        (scaled_width, scaled_height),
        interpolation=cv2.INTER_NEAREST,
    )
    return [x0, y0, scaled_width, scaled_height], (scaled_mask > 0).astype(np.uint8)


def _estimate_strand_count_from_mask(
    lightness: np.ndarray,
    component_mask: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> tuple[int, float]:
    x, y, width, height = bbox
    if component_mask.size == 0 or not np.any(component_mask):
        return 1, 1.0

    image_height, image_width = lightness.shape
    pad = min(32, max(5, round(max(width, height) * 0.25)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1 = min(image_width, x + width + pad)
    y1 = min(image_height, y + height + pad)

    roi = lightness[y0:y1, x0:x1]
    component = np.zeros_like(roi, dtype=np.uint8)
    mask_x0 = max(0, x - x0)
    mask_y0 = max(0, y - y0)
    mask_width = min(component_mask.shape[1], component.shape[1] - mask_x0)
    mask_height = min(component_mask.shape[0], component.shape[0] - mask_y0)
    if mask_width <= 0 or mask_height <= 0:
        return 1, 1.0
    component[
        mask_y0 : mask_y0 + mask_height,
        mask_x0 : mask_x0 + mask_width,
    ] = component_mask[:mask_height, :mask_width] * 255

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


def _detect_at_scale_v1(
    image: np.ndarray,
    scale: float,
    config: EnhancedCounterConfig
) -> list[dict[str, Any]]:
    """Run detection at a specific scale."""
    source_height, source_width = image.shape[:2]
    if scale != 1.0:
        h, w = image.shape[:2]
        new_h, new_w = int(h * scale), int(w * scale)
        scaled_image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    else:
        scaled_image = image

    image_height, image_width = scaled_image.shape[:2]
    image_area = image_height * image_width

    # Convert to LAB and get lightness
    lab = cv2.cvtColor(scaled_image, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]

    # Background subtraction
    background_kernel_size = _kernel_size(
        min(image_height, image_width), config.background_kernel_ratio, 9
    )
    background_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (background_kernel_size, background_kernel_size)
    )
    blackhat = cv2.morphologyEx(lightness, cv2.MORPH_BLACKHAT, background_kernel)

    # Thresholding - combine global and adaptive
    if config.use_adaptive_threshold:
        # Adaptive threshold
        block_size = _odd(
            int(min(image_height, image_width) * config.adaptive_block_size_ratio),
            11
        )
        adaptive_mask = cv2.adaptiveThreshold(
            blackhat,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            -2
        )

        # Also compute global threshold
        otsu_threshold, _ = cv2.threshold(
            blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        final_threshold = float(np.clip(otsu_threshold + config.threshold_offset, 0, 255))
        _, global_mask = cv2.threshold(blackhat, final_threshold, 255, cv2.THRESH_BINARY)

        # Combine: OR operation to get union
        mask = cv2.bitwise_or(adaptive_mask, global_mask)
        threshold_value = final_threshold
    else:
        # Original Otsu only
        otsu_threshold, _ = cv2.threshold(
            blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        final_threshold = float(np.clip(otsu_threshold + config.threshold_offset, 0, 255))
        _, mask = cv2.threshold(blackhat, final_threshold, 255, cv2.THRESH_BINARY)
        threshold_value = final_threshold

    # Morphological operations
    if config.enable_opening:
        # Opening to remove noise
        opening_size = _kernel_size(
            min(image_height, image_width), config.opening_kernel_ratio, 3
        )
        opening_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (opening_size, opening_size)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, opening_kernel, iterations=1)

    # Closing to connect nearby components
    close_width = _kernel_size(image_width, config.close_kernel_x_ratio, 3)
    close_height = _kernel_size(image_height, config.close_kernel_y_ratio, 3)
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (close_width, close_height)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=1)

    # Connected components analysis
    component_count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )

    # Size filters
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
        component_mask = (labels[y : y + height, x : x + width] == label_id).astype(
            np.uint8
        )

        # Scale coordinates back to original image size
        if scale != 1.0:
            original_bbox, component_mask = _scale_component_candidate(
                component_mask,
                (x, y, width, height),
                scale,
                source_width,
                source_height,
            )
            x, y, width, height = original_bbox
            center_x = center_x / scale
            center_y = center_y / scale
            area = int(area / (scale * scale))

        candidates.append(
            {
                "bbox": [x, y, width, height],
                "center": [float(center_x), float(center_y)],
                "area": area,
                "contrast": contrast,
                "partial": bool(partial),
                "scale": scale,
                "threshold": threshold_value,
                "component_mask": component_mask,
                "thickness": _component_mask_thickness(component_mask),
            }
        )

    return candidates


def count_dark_clusters_enhanced_v1(
    image: np.ndarray,
    config: EnhancedCounterConfig | None = None
) -> CountResult:
    """Previous enhanced detector kept for offline diagnostics."""
    config = config or EnhancedCounterConfig(
        threshold_offset=-10,
        min_contrast=25,
        scale_factors=(0.8, 1.0, 1.2),
        use_adaptive_threshold=True,
        enable_opening=True,
        min_confidence_threshold=0.4,
    )

    if image is None or image.size == 0:
        raise ValueError("Input image is empty")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Expected BGR image with 3 channels, got {image.shape}")

    original_height, original_width = image.shape[:2]

    # Optional: Apply CLAHE for better local contrast
    # processed_image = _apply_clahe(image)
    processed_image = image

    # Multi-scale detection
    all_candidates = []

    if config.enable_multiscale:
        scales = config.scale_factors
    else:
        scales = (1.0,)

    for scale in scales:
        candidates = _detect_at_scale_v1(processed_image, scale, config)
        all_candidates.extend(candidates)

    if not all_candidates:
        return CountResult(
            threshold=0.0,
            image_width=original_width,
            image_height=original_height,
            items=[],
        )

    lab = cv2.cvtColor(processed_image, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]

    image_area = original_height * original_width
    min_area = max(3, round(image_area * config.min_area_ratio))
    max_area = max(min_area + 1, round(image_area * config.max_area_ratio))

    for candidate in all_candidates:
        candidate["confidence"] = _confidence(
            candidate["contrast"], candidate["area"], min_area, max_area
        )

    # Apply NMS to remove duplicates from multi-scale detection
    candidates = _non_max_suppression_candidates(all_candidates, config.nms_iou_threshold)

    # Apply confidence filter
    if config.use_confidence_filter:
        candidates = [
            candidate
            for candidate in candidates
            if candidate["confidence"] >= config.min_confidence_threshold
        ]

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
                strand_count, split_confidence = _estimate_strand_count_from_mask(
                    lightness,
                    candidate["component_mask"],
                    tuple(candidate["bbox"]),
                )
                candidate["strand_count"] = strand_count
                candidate["split_confidence"] = split_confidence
            else:
                candidate["strand_count"] = 1
                candidate["split_confidence"] = 1.0

    # Sort by position (top to bottom, left to right)
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
            confidence=candidate["confidence"],
            partial=candidate["partial"],
            strand_count=candidate.get("strand_count", 1),
            split_confidence=candidate.get("split_confidence", 1.0),
        )
        for index, candidate in enumerate(candidates, start=1)
    ]

    avg_threshold = float(np.mean([c.get("threshold", 0) for c in all_candidates]))

    return CountResult(
        threshold=avg_threshold,
        image_width=original_width,
        image_height=original_height,
        items=items,
    )


def _component_shape_features(
    component_mask: np.ndarray,
    bbox: tuple[int, int, int, int],
    area: int,
) -> dict[str, float]:
    x, y, width, height = bbox
    del x, y
    ys, xs = np.nonzero(component_mask)
    if xs.size < 2:
        major_axis = float(max(width, height, 1))
        minor_axis = 1.0
    else:
        rect_width, rect_height = cv2.minAreaRect(
            np.column_stack((xs, ys)).astype(np.float32)
        )[1]
        major_axis = max(float(rect_width), float(rect_height), 1.0)
        minor_axis = max(1.0, min(float(rect_width), float(rect_height)))

    bbox_area = max(1, width * height)
    return {
        "aspect_ratio": round(major_axis / minor_axis, 3),
        "minor_axis": round(minor_axis, 3),
        "thickness_ratio": round(minor_axis / major_axis, 3),
        "fill_ratio": round(float(area) / bbox_area, 3),
    }


def _passes_v2_shape_filter(
    candidate: dict[str, Any],
    config: EnhancedCounterConfig,
) -> tuple[bool, bool]:
    aspect_ratio = candidate["aspect_ratio"]
    thickness_ratio = candidate["thickness_ratio"]
    fill_ratio = candidate["fill_ratio"]
    contrast = candidate["contrast"]

    elongated = (
        aspect_ratio >= config.min_aspect_ratio
        and thickness_ratio <= config.max_thickness_ratio
    )
    plausible_fill = config.min_fill_ratio <= fill_ratio <= config.max_fill_ratio
    normal = contrast >= config.min_contrast and elongated and plausible_fill
    strong = (
        contrast >= config.strong_contrast
        and aspect_ratio >= config.strong_aspect_ratio
        and thickness_ratio <= config.max_thickness_ratio
        and fill_ratio <= config.max_fill_ratio
    )
    return normal or strong, strong


def _detect_at_scale_v2(
    image: np.ndarray,
    scale: float,
    config: EnhancedCounterConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_height, source_width = image.shape[:2]
    if scale != 1.0:
        scaled_image = cv2.resize(
            image,
            (int(source_width * scale), int(source_height * scale)),
            interpolation=cv2.INTER_LINEAR,
        )
    else:
        scaled_image = image

    image_height, image_width = scaled_image.shape[:2]
    image_area = image_height * image_width

    lab = cv2.cvtColor(scaled_image, cv2.COLOR_BGR2LAB)
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

    if config.use_adaptive_threshold:
        block_size = _odd(
            int(min(image_height, image_width) * config.adaptive_block_size_ratio),
            11,
        )
        adaptive_mask = cv2.adaptiveThreshold(
            blackhat,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            -2,
        )
        mask = cv2.bitwise_and(
            cv2.dilate(
                mask,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
                iterations=1,
            ),
            adaptive_mask,
        )

    if config.enable_opening:
        opening_size = min(
            3,
            _kernel_size(min(image_height, image_width), config.opening_kernel_ratio, 3),
        )
        opening_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (opening_size, opening_size)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, opening_kernel, iterations=1)

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

    scale_stats = {
        "scale": scale,
        "threshold": round(final_threshold, 2),
        "raw_components": max(0, component_count - 1),
        "size_rejected": 0,
        "contrast_rejected": 0,
        "shape_rejected": 0,
        "candidates": 0,
        "mask_pixels": int(np.count_nonzero(mask)),
    }
    candidates: list[dict[str, Any]] = []
    for label_id in range(1, component_count):
        x = int(stats[label_id, cv2.CC_STAT_LEFT])
        y = int(stats[label_id, cv2.CC_STAT_TOP])
        width = int(stats[label_id, cv2.CC_STAT_WIDTH])
        height = int(stats[label_id, cv2.CC_STAT_HEIGHT])
        area = int(stats[label_id, cv2.CC_STAT_AREA])

        if not min_area <= area <= max_area or max(width, height) < min_side:
            scale_stats["size_rejected"] += 1
            continue

        partial = (
            x <= border_margin
            or y <= border_margin
            or x + width >= image_width - border_margin
            or y + height >= image_height - border_margin
        )
        if config.exclude_border and partial:
            scale_stats["size_rejected"] += 1
            continue

        contrast = _component_contrast(
            lightness, labels, label_id, (x, y, width, height)
        )
        if contrast < config.min_contrast:
            scale_stats["contrast_rejected"] += 1
            continue

        center_x, center_y = centroids[label_id]
        component_mask = (labels[y : y + height, x : x + width] == label_id).astype(
            np.uint8
        )

        if scale != 1.0:
            original_bbox, component_mask = _scale_component_candidate(
                component_mask,
                (x, y, width, height),
                scale,
                source_width,
                source_height,
            )
            x, y, width, height = original_bbox
            center_x = center_x / scale
            center_y = center_y / scale
            area = max(1, int(area / (scale * scale)))

        shape = _component_shape_features(component_mask, (x, y, width, height), area)
        candidate = {
            "bbox": [x, y, width, height],
            "center": [float(center_x), float(center_y)],
            "area": area,
            "contrast": contrast,
            "partial": bool(partial),
            "scale": scale,
            "threshold": final_threshold,
            "component_mask": component_mask,
            "thickness": shape["minor_axis"],
            **shape,
        }
        shape_passed, strong_shape = _passes_v2_shape_filter(candidate, config)
        if not shape_passed:
            scale_stats["shape_rejected"] += 1
            continue
        candidate["strong_shape"] = strong_shape
        candidates.append(candidate)

    scale_stats["candidates"] = len(candidates)
    return candidates, scale_stats


def _with_consensus_votes(
    candidates: list[dict[str, Any]],
    config: EnhancedCounterConfig,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    scales = {candidate["scale"] for candidate in candidates}
    if len(scales) == 1:
        for candidate in candidates:
            candidate["consensus_votes"] = 1
            candidate["consensus_passed"] = True
        return candidates

    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        votes = {candidate["scale"]}
        for other in candidates:
            if other is candidate or other["scale"] in votes:
                continue
            if _compute_iou(candidate["bbox"], other["bbox"]) >= config.consensus_iou_threshold:
                votes.add(other["scale"])
        candidate["consensus_votes"] = len(votes)
        candidate["consensus_passed"] = len(votes) >= config.min_consensus_votes
        if candidate["strong_shape"] or candidate["consensus_passed"]:
            kept.append(candidate)
    return kept


def diagnose_dark_clusters_enhanced_v2(
    image: np.ndarray,
    config: EnhancedCounterConfig | None = None,
) -> tuple[CountResult, dict[str, Any]]:
    """Run enhanced v2 and return JSON-safe candidate statistics for offline QA."""
    config = config or EnhancedCounterConfig()
    if image is None or image.size == 0:
        raise ValueError("Input image is empty")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Expected BGR image with 3 channels, got {image.shape}")

    original_height, original_width = image.shape[:2]
    scales = config.scale_factors if config.enable_multiscale else (1.0,)

    all_candidates: list[dict[str, Any]] = []
    scale_stats: list[dict[str, Any]] = []
    for scale in scales:
        candidates, stats = _detect_at_scale_v2(image, scale, config)
        all_candidates.extend(candidates)
        scale_stats.append(stats)

    diagnostics: dict[str, Any] = {
        "algorithm": "enhanced_v2",
        "image_width": original_width,
        "image_height": original_height,
        "scales": scale_stats,
        "raw_candidates": len(all_candidates),
        "consensus_candidates": 0,
        "final_candidates": 0,
        "final_boxes": [],
    }
    if not all_candidates:
        return (
            CountResult(
                threshold=0.0,
                image_width=original_width,
                image_height=original_height,
                items=[],
            ),
            diagnostics,
        )

    image_area = original_height * original_width
    min_area = max(3, round(image_area * config.min_area_ratio))
    max_area = max(min_area + 1, round(image_area * config.max_area_ratio))
    for candidate in all_candidates:
        candidate["confidence"] = _confidence(
            candidate["contrast"], candidate["area"], min_area, max_area
        )

    candidates = _with_consensus_votes(all_candidates, config)
    diagnostics["consensus_candidates"] = len(candidates)

    if config.use_confidence_filter:
        candidates = [
            candidate
            for candidate in candidates
            if candidate["confidence"] >= config.min_confidence_threshold
        ]

    candidates = _non_max_suppression_candidates(candidates, config.nms_iou_threshold)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]
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
                strand_count, split_confidence = _estimate_strand_count_from_mask(
                    lightness,
                    candidate["component_mask"],
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
            confidence=candidate["confidence"],
            partial=candidate["partial"],
            strand_count=candidate.get("strand_count", 1),
            split_confidence=candidate.get("split_confidence", 1.0),
        )
        for index, candidate in enumerate(candidates, start=1)
    ]
    diagnostics["final_candidates"] = len(items)
    diagnostics["final_boxes"] = [
        {
            "bbox": item.bbox,
            "center": item.center,
            "confidence": item.confidence,
            "strand_count": item.strand_count,
        }
        for item in items
    ]

    avg_threshold = float(np.mean([candidate["threshold"] for candidate in all_candidates]))
    return (
        CountResult(
            threshold=avg_threshold,
            image_width=original_width,
            image_height=original_height,
            items=items,
        ),
        diagnostics,
    )


def count_dark_clusters_enhanced_v2(
    image: np.ndarray,
    config: EnhancedCounterConfig | None = None,
) -> CountResult:
    result, _ = diagnose_dark_clusters_enhanced_v2(image, config)
    return result


count_dark_clusters_enhanced = count_dark_clusters_enhanced_v2
