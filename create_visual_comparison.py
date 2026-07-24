#!/usr/bin/env python3
"""Create side-by-side comparison visualizations of detection algorithms."""

import sys
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.counter import count_dark_clusters, CounterConfig
from app.counter_enhanced import count_dark_clusters_enhanced, EnhancedCounterConfig


def create_comparison_visualization(image_path: Path, output_dir: Path):
    """Create a side-by-side comparison of original vs enhanced detection."""
    image = cv2.imread(str(image_path))
    if image is None:
        print(f"Failed to load {image_path}")
        return

    # Run detections
    original = count_dark_clusters(image, CounterConfig())
    enhanced = count_dark_clusters_enhanced(
        image,
        EnhancedCounterConfig(
            threshold_offset=-15,
            min_contrast=20,
            min_confidence_threshold=0.3,
            scale_factors=(0.7, 0.85, 1.0, 1.15),
        )
    )

    # Create annotated images
    img_original = image.copy()
    img_enhanced = image.copy()

    # Annotate original
    for item in original.items:
        x, y, w, h = item.bbox
        color = (0, 255, 0)
        cv2.rectangle(img_original, (x, y), (x + w, y + h), color, 2)
        label = f"{item.id}"
        cv2.putText(img_original, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, color, 1, cv2.LINE_AA)

    # Annotate enhanced
    for item in enhanced.items:
        x, y, w, h = item.bbox
        color = (255, 0, 0)
        cv2.rectangle(img_enhanced, (x, y), (x + w, y + h), color, 2)
        label = f"{item.id}"
        cv2.putText(img_enhanced, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, color, 1, cv2.LINE_AA)

    # Add text overlays
    cv2.putText(img_original, f"Original: {original.count} hairs",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
    cv2.putText(img_enhanced, f"Enhanced: {enhanced.count} hairs",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)

    # Create side-by-side comparison
    h, w = image.shape[:2]
    comparison = np.zeros((h, w * 2 + 20, 3), dtype=np.uint8)
    comparison.fill(255)

    comparison[:h, :w] = img_original
    comparison[:h, w+20:] = img_enhanced

    # Add divider
    comparison[:, w:w+20] = 200

    # Add labels at bottom
    label_height = 60
    label_area = np.ones((label_height, w * 2 + 20, 3), dtype=np.uint8) * 240

    cv2.putText(label_area, "ORIGINAL ALGORITHM",
                (w//2 - 150, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(label_area, "ENHANCED ALGORITHM",
                (w + w//2 - 150, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)

    final = np.vstack([comparison, label_area])

    # Save
    output_path = output_dir / f"comparison_{image_path.stem}.jpg"
    cv2.imwrite(str(output_path), final, [cv2.IMWRITE_JPEG_QUALITY, 95])

    print(f"✓ Created {output_path.name}")
    print(f"  Original: {original.count} | Enhanced: {enhanced.count} | Improvement: +{enhanced.count - original.count}")


def main():
    sample_dir = Path(__file__).parent / "sample-images"
    output_dir = Path(__file__).parent / "output" / "comparisons"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not sample_dir.exists():
        print(f"Sample directory not found: {sample_dir}")
        return

    print("=" * 80)
    print("Creating side-by-side comparison visualizations")
    print("=" * 80)
    print()

    # Process first 5 images as examples
    image_files = sorted(sample_dir.glob("*.bmp"))[:5]

    for image_path in image_files:
        create_comparison_visualization(image_path, output_dir)

    print()
    print("=" * 80)
    print(f"All visualizations saved to: {output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    main()
