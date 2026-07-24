#!/usr/bin/env python3
"""Offline comparison for legacy, enhanced v1, and enhanced v2 detectors."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app.counter import CounterConfig, CountResult, count_dark_clusters  # noqa: E402
from app.counter_enhanced import (  # noqa: E402
    EnhancedCounterConfig,
    count_dark_clusters_enhanced_v1,
    diagnose_dark_clusters_enhanced_v2,
)


def _draw_result(image, result: CountResult, color: tuple[int, int, int]):
    annotated = image.copy()
    for item in result.items:
        x, y, width, height = item.bbox
        cv2.rectangle(annotated, (x, y), (x + width, y + height), color, 2)
        label = f"{item.id}"
        if item.strand_count > 1:
            label = f"{label}x{item.strand_count}"
        cv2.putText(
            annotated,
            label,
            (x, max(12, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        annotated,
        f"count={result.count} detections={len(result.items)}",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        color,
        2,
        cv2.LINE_AA,
    )
    return annotated


def _summary(result: CountResult) -> dict:
    return {
        "count": result.count,
        "detections": len(result.items),
        "threshold": round(result.threshold, 2),
        "items": [
            {
                "id": item.id,
                "bbox": item.bbox,
                "center": item.center,
                "area": item.area,
                "contrast": item.contrast,
                "confidence": item.confidence,
                "partial": item.partial,
                "strand_count": item.strand_count,
                "split_confidence": item.split_confidence,
            }
            for item in result.items
        ],
    }


def _process_image(image_path: Path, output_dir: Path) -> dict:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Failed to read image: {image_path}")

    legacy = count_dark_clusters(image, CounterConfig())
    enhanced_v1 = count_dark_clusters_enhanced_v1(
        image,
        EnhancedCounterConfig(
            threshold_offset=-10,
            min_contrast=25,
            scale_factors=(0.8, 1.0, 1.2),
            use_adaptive_threshold=True,
            enable_opening=True,
            min_confidence_threshold=0.4,
        ),
    )
    enhanced_v2, diagnostics = diagnose_dark_clusters_enhanced_v2(
        image,
        EnhancedCounterConfig(),
    )

    stem = image_path.stem
    cv2.imwrite(str(output_dir / f"{stem}_legacy.jpg"), _draw_result(image, legacy, (0, 180, 0)))
    cv2.imwrite(
        str(output_dir / f"{stem}_enhanced_v1.jpg"),
        _draw_result(image, enhanced_v1, (0, 0, 255)),
    )
    cv2.imwrite(
        str(output_dir / f"{stem}_enhanced_v2.jpg"),
        _draw_result(image, enhanced_v2, (255, 0, 0)),
    )

    payload = {
        "filename": image_path.name,
        "legacy": _summary(legacy),
        "enhanced_v1": _summary(enhanced_v1),
        "enhanced_v2": _summary(enhanced_v2),
        "enhanced_v2_diagnostics": diagnostics,
    }
    (output_dir / f"{stem}_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "algorithm_diagnostics",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for image_path in args.images:
        summaries.append(_process_image(image_path, args.output_dir))

    index = [
        {
            "filename": item["filename"],
            "legacy_count": item["legacy"]["count"],
            "enhanced_v1_count": item["enhanced_v1"]["count"],
            "enhanced_v2_count": item["enhanced_v2"]["count"],
            "enhanced_v2_raw_candidates": item["enhanced_v2_diagnostics"]["raw_candidates"],
            "enhanced_v2_final_candidates": item["enhanced_v2_diagnostics"]["final_candidates"],
        }
        for item in summaries
    ]
    (args.output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote diagnostics to {args.output_dir}")


if __name__ == "__main__":
    main()
