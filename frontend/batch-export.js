(function (root) {
  "use strict";

  function strandCount(item) {
    return Math.max(1, Number(item?.strand_count) || 1);
  }

  function automaticItems(items) {
    return (items || []).filter((item) => !item.manual);
  }

  function clusterConfidence(item) {
    const score = Number(item.confidence || 0) * Number(item.split_confidence ?? 1);
    return Number.isFinite(score) ? Math.round(score * 1_000_000) / 1_000_000 : null;
  }

  function totalStrands(items) {
    return (items || []).reduce((total, item) => total + strandCount(item), 0);
  }

  function hasNetManualChanges(initialItems, finalItems) {
    const initial = automaticItems(initialItems);
    const final = finalItems || [];
    if (final.some((item) => item.manual)) return true;

    const finalById = new Map(automaticItems(final).map((item) => [item.id, item]));
    if (finalById.size !== initial.length) return true;
    return initial.some((item) => {
      const finalItem = finalById.get(item.id);
      return !finalItem || strandCount(finalItem) !== strandCount(item);
    });
  }

  function markerRow(batchItem, annotatedPath, sourceItem, values) {
    const [centerX, centerY] = sourceItem.center;
    const [bboxX, bboxY, bboxWidth, bboxHeight] = sourceItem.bbox;
    return {
      imageIndex: batchItem.queueIndex + 1,
      originalFilename: batchItem.sourceFilename,
      annotatedPath,
      originalClusterId: values.originalClusterId,
      finalAnnotationId: values.finalAnnotationId,
      strandIndex: values.strandIndex,
      centerX,
      centerY,
      bboxX,
      bboxY,
      bboxWidth,
      bboxHeight,
      correction: values.correction,
      finalCounted: values.finalCounted ? "是" : "否",
      confidence: values.confidence,
    };
  }

  function markerRowsForItem(batchItem, annotatedPath) {
    const initialItems = automaticItems(batchItem.initialItems);
    const finalItems = batchItem.finalItems || [];
    const initialIds = new Set(initialItems.map((item) => item.id));
    const finalAutomaticById = new Map();
    finalItems.forEach((item, index) => {
      if (!item.manual) finalAutomaticById.set(item.id, { item, annotationId: index + 1 });
    });

    const rows = [];
    for (const initialItem of initialItems) {
      const finalEntry = finalAutomaticById.get(initialItem.id);
      const initialCount = strandCount(initialItem);
      const finalCount = finalEntry ? strandCount(finalEntry.item) : 0;
      const sourceItem = finalEntry?.item || initialItem;
      const confidence = clusterConfidence(initialItem);

      for (let strandIndex = 1; strandIndex <= Math.max(initialCount, finalCount); strandIndex += 1) {
        const retained = strandIndex <= Math.min(initialCount, finalCount);
        const added = strandIndex > initialCount;
        rows.push(markerRow(batchItem, annotatedPath, sourceItem, {
          originalClusterId: initialItem.id,
          finalAnnotationId: finalEntry?.annotationId ?? null,
          strandIndex,
          correction: retained ? "自动保留" : added ? "人工增加数量" : "人工删除",
          finalCounted: strandIndex <= finalCount,
          confidence,
        }));
      }
    }

    finalItems.forEach((finalItem, index) => {
      if (!finalItem.manual && initialIds.has(finalItem.id)) return;
      const count = strandCount(finalItem);
      const confidence = finalItem.manual ? null : clusterConfidence(finalItem);
      for (let strandIndex = 1; strandIndex <= count; strandIndex += 1) {
        rows.push(markerRow(batchItem, annotatedPath, finalItem, {
          originalClusterId: finalItem.manual ? null : finalItem.id,
          finalAnnotationId: index + 1,
          strandIndex,
          correction: finalItem.manual && strandIndex === 1 ? "人工补点" : finalItem.manual ? "人工增加数量" : "自动保留",
          finalCounted: true,
          confidence,
        }));
      }
    });

    return rows;
  }

  function createMarkerRows(snapshot, pathsByQueueIndex) {
    const rows = [];
    for (const item of snapshot.items) {
      if (item.status !== "done" || !item.response) continue;
      const itemRows = markerRowsForItem(item, pathsByQueueIndex.get(item.queueIndex) || "");
      const finalCount = itemRows.filter((row) => row.finalCounted === "是").length;
      if (finalCount !== item.count) throw new Error(`第 ${item.queueIndex + 1} 张图片的标记明细与最终数量不一致`);
      rows.push(...itemRows);
    }
    return rows;
  }

  function createSummaryRows(snapshot, pathsByQueueIndex) {
    return snapshot.items.map((item) => {
      const succeeded = item.status === "done" && Boolean(item.response);
      const failed = item.status === "error";
      const automaticCount = succeeded ? totalStrands(item.initialItems) : null;
      return {
        index: item.queueIndex + 1,
        originalFilename: item.sourceFilename,
        annotatedPath: succeeded ? (pathsByQueueIndex.get(item.queueIndex) || "") : "",
        status: succeeded ? "成功" : failed ? "失败" : "未处理",
        automaticCount,
        manualAdjustment: succeeded ? item.count - automaticCount : null,
        finalCount: succeeded ? item.count : null,
        manuallyEdited: succeeded ? (item.dirty ? "是" : "否") : "",
        error: failed ? (item.error || "识别失败") : "",
      };
    });
  }

  const api = Object.freeze({ createMarkerRows, createSummaryRows, hasNetManualChanges, markerRowsForItem });
  root.BatchExportData = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : this);
