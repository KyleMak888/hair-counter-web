#!/usr/bin/env python3
"""Quick test script to process sample images with enhanced algorithm."""

import sys
from pathlib import Path
import cv2
import time
import json

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.counter_enhanced import count_dark_clusters_enhanced, EnhancedCounterConfig


def process_image(image_path: Path, config: EnhancedCounterConfig):
    """Process a single image and return results."""
    print(f"\n{'='*80}")
    print(f"Processing: {image_path.name}")
    print('='*80)

    image = cv2.imread(str(image_path))
    if image is None:
        print("❌ Failed to load image")
        return None

    start = time.time()
    result = count_dark_clusters_enhanced(image, config)
    elapsed = (time.time() - start) * 1000

    print(f"\n📊 Results:")
    print(f"   Total count: {result.count} hairs")
    print(f"   Detections: {len(result.items)}")
    print(f"   Processing time: {elapsed:.1f} ms")
    print(f"   Image size: {result.image_width}×{result.image_height}")
    print(f"   Threshold: {result.threshold:.1f}")

    # Statistics
    confidences = [item.confidence for item in result.items]
    contrasts = [item.contrast for item in result.items]
    areas = [item.area for item in result.items]

    if confidences:
        print(f"\n📈 Statistics:")
        print(f"   Avg confidence: {sum(confidences)/len(confidences):.3f}")
        print(f"   Min confidence: {min(confidences):.3f}")
        print(f"   Avg contrast: {sum(contrasts)/len(contrasts):.1f}")
        print(f"   Avg area: {sum(areas)/len(areas):.1f} px²")

    # Low confidence warnings
    low_conf = [item for item in result.items if item.confidence < 0.5]
    if low_conf:
        print(f"\n⚠️  {len(low_conf)} detections with confidence < 0.5 (may need review)")

    return {
        "filename": image_path.name,
        "count": result.count,
        "detections": len(result.items),
        "time_ms": elapsed,
        "items": [
            {
                "id": item.id,
                "bbox": item.bbox,
                "center": item.center,
                "confidence": item.confidence,
                "contrast": item.contrast,
                "area": item.area,
            }
            for item in result.items
        ]
    }


def main():
    sample_dir = Path(__file__).parent / "sample-images"
    output_dir = Path(__file__).parent / "output" / "enhanced_results"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not sample_dir.exists():
        print(f"❌ Sample directory not found: {sample_dir}")
        return

    print("\n" + "="*80)
    print("ENHANCED HAIR DETECTION - Quick Test")
    print("="*80)

    # Configuration presets
    presets = {
        "1": ("Balanced", EnhancedCounterConfig(
            threshold_offset=-10,
            min_contrast=25,
            min_confidence_threshold=0.4,
            scale_factors=(0.8, 1.0, 1.2),
        )),
        "2": ("Aggressive (High Recall)", EnhancedCounterConfig(
            threshold_offset=-15,
            min_contrast=20,
            min_confidence_threshold=0.3,
            scale_factors=(0.7, 0.85, 1.0, 1.15),
        )),
        "3": ("Conservative (High Precision)", EnhancedCounterConfig(
            threshold_offset=0,
            min_contrast=35,
            min_confidence_threshold=0.6,
            scale_factors=(0.9, 1.0, 1.1),
        )),
    }

    print("\nSelect configuration preset:")
    for key, (name, _) in presets.items():
        print(f"  {key}. {name}")
    print(f"  4. Custom (modify parameters manually)")

    choice = input("\nEnter choice (1-4) [default: 2]: ").strip() or "2"

    if choice in presets:
        preset_name, config = presets[choice]
        print(f"\n✓ Using preset: {preset_name}")
    else:
        print("\n⚠️  Custom configuration - using aggressive preset as base")
        config = presets["2"][1]

    # Select images
    image_files = sorted(sample_dir.glob("*.bmp"))
    print(f"\nFound {len(image_files)} sample images")

    print("\nOptions:")
    print("  1. Process all images")
    print("  2. Process first 5 images")
    print("  3. Process single image")

    batch_choice = input("\nEnter choice (1-3) [default: 2]: ").strip() or "2"

    if batch_choice == "1":
        selected = image_files
    elif batch_choice == "3":
        print("\nAvailable images:")
        for idx, img in enumerate(image_files[:10], 1):
            print(f"  {idx}. {img.name}")
        img_idx = int(input("\nEnter image number: ").strip()) - 1
        selected = [image_files[img_idx]]
    else:
        selected = image_files[:5]

    print(f"\n✓ Will process {len(selected)} image(s)")

    # Process images
    results = []
    for image_path in selected:
        result = process_image(image_path, config)
        if result:
            results.append(result)

            # Create annotated image
            image = cv2.imread(str(image_path))
            annotated = image.copy()

            # Get result from enhanced algorithm
            detection_result = count_dark_clusters_enhanced(image, config)

            for item in detection_result.items:
                x, y, w, h = item.bbox

                # Color based on confidence
                if item.confidence >= 0.7:
                    color = (0, 255, 0)  # Green - high confidence
                elif item.confidence >= 0.5:
                    color = (0, 165, 255)  # Orange - medium
                else:
                    color = (0, 0, 255)  # Red - low confidence

                thickness = 2
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, thickness)

                label = f"{item.id}"
                cv2.putText(annotated, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, color, 1, cv2.LINE_AA)

            # Add info overlay
            cv2.putText(annotated, f"Total: {detection_result.count} hairs",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.putText(annotated, f"Enhanced Algorithm",
                        (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2, cv2.LINE_AA)

            # Save annotated image
            output_path = output_dir / f"enhanced_{image_path.stem}.jpg"
            cv2.imwrite(str(output_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 95])

    # Save JSON results
    json_path = output_dir / "results.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"\nProcessed: {len(results)} images")
    if results:
        total_count = sum(r["count"] for r in results)
        avg_count = total_count / len(results)
        avg_time = sum(r["time_ms"] for r in results) / len(results)
        print(f"Total hairs detected: {total_count}")
        print(f"Average per image: {avg_count:.1f}")
        print(f"Average processing time: {avg_time:.1f} ms")

    print(f"\n✓ Results saved to:")
    print(f"  - JSON: {json_path}")
    print(f"  - Annotated images: {output_dir}/enhanced_*.jpg")
    print("\n" + "="*80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
