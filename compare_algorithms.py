#!/usr/bin/env python3
"""Compare original and enhanced detection algorithms."""

import sys
from pathlib import Path
import cv2
import numpy as np
import time

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.counter import count_dark_clusters, CounterConfig
from app.counter_enhanced import count_dark_clusters_enhanced, EnhancedCounterConfig


def compare_algorithms(image_path: Path):
    """Compare original vs enhanced detection on a single image."""
    image = cv2.imread(str(image_path))
    if image is None:
        return None

    results = {}

    # Original algorithm - default config
    start = time.time()
    original_default = count_dark_clusters(image, CounterConfig())
    results["original_default"] = {
        "count": original_default.count,
        "detections": len(original_default.items),
        "time_ms": (time.time() - start) * 1000,
        "threshold": original_default.threshold,
    }

    # Original algorithm - sensitive config
    start = time.time()
    original_sensitive = count_dark_clusters(
        image,
        CounterConfig(threshold_offset=-10, min_contrast=25)
    )
    results["original_sensitive"] = {
        "count": original_sensitive.count,
        "detections": len(original_sensitive.items),
        "time_ms": (time.time() - start) * 1000,
        "threshold": original_sensitive.threshold,
    }

    # Enhanced algorithm - default config
    start = time.time()
    enhanced_default = count_dark_clusters_enhanced(
        image,
        EnhancedCounterConfig()
    )
    results["enhanced_default"] = {
        "count": enhanced_default.count,
        "detections": len(enhanced_default.items),
        "time_ms": (time.time() - start) * 1000,
        "threshold": enhanced_default.threshold,
    }

    # Enhanced algorithm - sensitive config
    start = time.time()
    enhanced_sensitive = count_dark_clusters_enhanced(
        image,
        EnhancedCounterConfig(
            threshold_offset=-10,
            min_contrast=25,
            min_confidence_threshold=0.4
        )
    )
    results["enhanced_sensitive"] = {
        "count": enhanced_sensitive.count,
        "detections": len(enhanced_sensitive.items),
        "time_ms": (time.time() - start) * 1000,
        "threshold": enhanced_sensitive.threshold,
    }

    # Enhanced algorithm - aggressive config
    start = time.time()
    enhanced_aggressive = count_dark_clusters_enhanced(
        image,
        EnhancedCounterConfig(
            threshold_offset=-15,
            min_contrast=20,
            min_confidence_threshold=0.3,
            scale_factors=(0.7, 0.85, 1.0, 1.15),
        )
    )
    results["enhanced_aggressive"] = {
        "count": enhanced_aggressive.count,
        "detections": len(enhanced_aggressive.items),
        "time_ms": (time.time() - start) * 1000,
        "threshold": enhanced_aggressive.threshold,
    }

    return results


def main():
    sample_dir = Path(__file__).parent / "sample-images"

    if not sample_dir.exists():
        print(f"Sample directory not found: {sample_dir}")
        return

    image_files = sorted(sample_dir.glob("*.bmp"))[:10]  # Test first 10 images

    print("=" * 100)
    print("ALGORITHM COMPARISON: Original vs Enhanced")
    print("=" * 100)
    print()

    all_results = {}

    for image_path in image_files:
        print(f"📷 {image_path.name}")
        print("-" * 100)

        results = compare_algorithms(image_path)
        if results is None:
            print("   ❌ Failed to load image\n")
            continue

        all_results[image_path.name] = results

        # Print results in a table
        configs = [
            "original_default",
            "original_sensitive",
            "enhanced_default",
            "enhanced_sensitive",
            "enhanced_aggressive",
        ]

        print(f"{'Configuration':<25} {'Count':>8} {'Detections':>12} {'Time (ms)':>12} {'Threshold':>12}")
        print("-" * 100)

        for config_name in configs:
            r = results[config_name]
            print(f"{config_name:<25} {r['count']:>8} {r['detections']:>12} {r['time_ms']:>12.1f} {r['threshold']:>12.1f}")

        print()

    # Summary statistics
    print("\n" + "=" * 100)
    print("SUMMARY ACROSS ALL IMAGES")
    print("=" * 100)
    print()

    configs = [
        "original_default",
        "original_sensitive",
        "enhanced_default",
        "enhanced_sensitive",
        "enhanced_aggressive",
    ]

    print(f"{'Configuration':<25} {'Avg Count':>12} {'Min Count':>12} {'Max Count':>12} {'Avg Time (ms)':>15}")
    print("-" * 100)

    for config_name in configs:
        counts = [r[config_name]["count"] for r in all_results.values()]
        times = [r[config_name]["time_ms"] for r in all_results.values()]

        avg_count = np.mean(counts)
        min_count = min(counts)
        max_count = max(counts)
        avg_time = np.mean(times)

        print(f"{config_name:<25} {avg_count:>12.1f} {min_count:>12} {max_count:>12} {avg_time:>15.1f}")

    print()
    print("=" * 100)
    print("INTERPRETATION:")
    print("- If enhanced algorithms detect MORE hairs consistently, they're capturing missed detections")
    print("- If time increases by 2-3x, that's expected due to multi-scale detection")
    print("- Look for images where enhanced_aggressive finds significantly more than original")
    print("=" * 100)


if __name__ == "__main__":
    main()
