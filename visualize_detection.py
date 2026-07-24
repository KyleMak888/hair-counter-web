#!/usr/bin/env python3
"""Visualize detection results to understand what's being missed."""

import sys
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.counter import count_dark_clusters, CounterConfig


def visualize_detection(image_path: Path, output_path: Path, config: CounterConfig):
    """Create annotated image showing what was detected."""
    image = cv2.imread(str(image_path))
    if image is None:
        print(f"Failed to load {image_path}")
        return

    result = count_dark_clusters(image, config)

    # Create annotated image
    annotated = image.copy()

    for item in result.items:
        x, y, w, h = item.bbox
        color = (0, 255, 0) if item.strand_count == 1 else (0, 165, 255)  # Green for single, Orange for cluster
        thickness = 2 if item.strand_count == 1 else 3

        # Draw bounding box
        cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)

        # Draw ID and strand count
        label = f"{item.id}" if item.strand_count == 1 else f"{item.id}({item.strand_count})"
        cv2.putText(annotated, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1, cv2.LINE_AA)

    # Add statistics overlay
    info_text = [
        f"Total: {result.count}",
        f"Detections: {len(result.items)}",
        f"Threshold: {result.threshold:.1f}",
        f"Config: offset={config.threshold_offset}, contrast={config.min_contrast}",
    ]

    y_offset = 30
    for text in info_text:
        cv2.putText(annotated, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 255), 2, cv2.LINE_AA)
        y_offset += 30

    cv2.imwrite(str(output_path), annotated)
    print(f"Saved annotated image to {output_path}")


def main():
    sample_dir = Path(__file__).parent / "sample-images"
    output_dir = Path(__file__).parent / "output" / "detection_analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Test one image with different configs
    test_image = sample_dir / "3661-1-02-0.bmp"

    configs = {
        "default": CounterConfig(),
        "sensitive": CounterConfig(threshold_offset=-10, min_contrast=25),
        "very_sensitive": CounterConfig(threshold_offset=-20, min_contrast=20),
        "strict": CounterConfig(threshold_offset=10, min_contrast=45),
    }

    for config_name, config in configs.items():
        output_path = output_dir / f"{test_image.stem}_{config_name}.png"
        visualize_detection(test_image, output_path, config)


if __name__ == "__main__":
    main()
