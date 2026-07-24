#!/usr/bin/env python3
"""Analyze sample images to understand current detection performance."""

import sys
from pathlib import Path
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.counter import count_dark_clusters, CounterConfig


def analyze_image(image_path: Path, config: CounterConfig) -> dict:
    """Analyze a single image and return detection statistics."""
    image = cv2.imread(str(image_path))
    if image is None:
        return {"error": "Failed to load image"}

    result = count_dark_clusters(image, config)

    # Calculate statistics
    strand_counts = [item.strand_count for item in result.items]
    confidences = [item.confidence for item in result.items]
    contrasts = [item.contrast for item in result.items]
    areas = [item.area for item in result.items]

    return {
        "filename": image_path.name,
        "image_size": f"{result.image_width}x{result.image_height}",
        "total_count": result.count,
        "num_detections": len(result.items),
        "threshold": result.threshold,
        "avg_confidence": np.mean(confidences) if confidences else 0,
        "min_confidence": min(confidences) if confidences else 0,
        "avg_contrast": np.mean(contrasts) if contrasts else 0,
        "min_contrast": min(contrasts) if contrasts else 0,
        "avg_area": np.mean(areas) if areas else 0,
        "clusters_detected": sum(1 for sc in strand_counts if sc > 1),
        "max_strand_in_cluster": max(strand_counts) if strand_counts else 0,
    }


def main():
    sample_dir = Path(__file__).parent / "sample-images"

    if not sample_dir.exists():
        print(f"Sample directory not found: {sample_dir}")
        return

    # Test with different configurations
    configs = {
        "default": CounterConfig(),
        "sensitive": CounterConfig(threshold_offset=-10, min_contrast=25),
        "strict": CounterConfig(threshold_offset=10, min_contrast=45),
    }

    image_files = sorted(sample_dir.glob("*.bmp"))[:5]  # Test first 5 images

    for config_name, config in configs.items():
        print(f"\n{'='*80}")
        print(f"Configuration: {config_name}")
        print(f"  threshold_offset: {config.threshold_offset}")
        print(f"  min_contrast: {config.min_contrast}")
        print(f"{'='*80}\n")

        for image_path in image_files:
            stats = analyze_image(image_path, config)

            if "error" in stats:
                print(f"❌ {stats['filename']}: {stats['error']}")
                continue

            print(f"📷 {stats['filename']}")
            print(f"   Size: {stats['image_size']}")
            print(f"   Total count: {stats['total_count']}")
            print(f"   Detections: {stats['num_detections']}")
            print(f"   Threshold: {stats['threshold']:.1f}")
            print(f"   Avg confidence: {stats['avg_confidence']:.3f}")
            print(f"   Avg contrast: {stats['avg_contrast']:.1f} (min: {stats['min_contrast']:.1f})")
            print(f"   Avg area: {stats['avg_area']:.1f}")
            print(f"   Clusters (multi-strand): {stats['clusters_detected']}")
            print(f"   Max strands in cluster: {stats['max_strand_in_cluster']}")
            print()


if __name__ == "__main__":
    main()
