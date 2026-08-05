const assert = require("node:assert/strict");
const test = require("node:test");

const BatchExportData = require("../frontend/batch-export.js");

function item(id, strandCount, options = {}) {
  return {
    id,
    bbox: options.bbox || [10 * id, 20, 8, 9],
    center: options.center || [10 * id + 4, 24.5],
    confidence: options.confidence ?? 0.8,
    split_confidence: options.splitConfidence ?? 0.5,
    strand_count: strandCount,
    manual: Boolean(options.manual),
  };
}

test("detects net manual changes instead of transient edit history", () => {
  const initial = [item(1, 2), item(2, 1)];
  assert.equal(BatchExportData.hasNetManualChanges(initial, initial.map((entry) => ({ ...entry }))), false);
  assert.equal(BatchExportData.hasNetManualChanges(initial, [item(1, 3), item(2, 1)]), true);
  assert.equal(BatchExportData.hasNetManualChanges(initial, [item(1, 2)]), true);
  assert.equal(BatchExportData.hasNetManualChanges(initial, [...initial, item(3, 1, { manual: true })]), true);

  const restored = initial.map((entry) => ({ ...entry }));
  assert.equal(BatchExportData.hasNetManualChanges(initial, restored), false);
});

test("creates per-image summary counts that reconcile automatic, manual, and final totals", () => {
  const snapshot = {
    items: [
      {
        queueIndex: 0,
        sourceFilename: "已修正.jpg",
        status: "done",
        response: {},
        initialItems: [item(1, 3), item(2, 2)],
        finalItems: [item(1, 2), item(2, 2)],
        count: 4,
        dirty: true,
      },
      {
        queueIndex: 1,
        sourceFilename: "失败.jpg",
        status: "error",
        response: null,
        initialItems: [],
        count: null,
        dirty: false,
        error: "识别超时",
      },
      {
        queueIndex: 2,
        sourceFilename: "等待.jpg",
        status: "pending",
        response: null,
        initialItems: [],
        count: null,
        dirty: false,
      },
    ],
  };

  const rows = BatchExportData.createSummaryRows(snapshot, new Map([[0, "标注图/已修正-annotated.png"]]));
  assert.deepEqual(rows[0], {
    index: 1,
    originalFilename: "已修正.jpg",
    annotatedPath: "标注图/已修正-annotated.png",
    status: "成功",
    automaticCount: 5,
    clusterCount: 2,
    manualIncreaseCount: 0,
    manualDeletionCount: 1,
    manualPointCount: 0,
    manualNetAdjustment: -1,
    finalCount: 4,
    manuallyEdited: "是",
    error: "",
  });
  assert.deepEqual(rows.slice(1).map((row) => ({
    status: row.status,
    automaticCount: row.automaticCount,
    manualIncreaseCount: row.manualIncreaseCount,
    manualDeletionCount: row.manualDeletionCount,
    manualPointCount: row.manualPointCount,
    manualNetAdjustment: row.manualNetAdjustment,
    finalCount: row.finalCount,
    error: row.error,
  })), [
    {
      status: "失败", automaticCount: null, manualIncreaseCount: null, manualDeletionCount: null,
      manualPointCount: null, manualNetAdjustment: null, finalCount: null, error: "识别超时",
    },
    {
      status: "未处理", automaticCount: null, manualIncreaseCount: null, manualDeletionCount: null,
      manualPointCount: null, manualNetAdjustment: null, finalCount: null, error: "",
    },
  ]);
});

test("keeps split correction counts when additions and deletions offset", () => {
  const snapshot = {
    items: [{
      queueIndex: 0,
      sourceFilename: "抵消修正.jpg",
      status: "done",
      response: {},
      initialItems: [item(7, 2), item(8, 3), item(9, 1)],
      finalItems: [item(7, 3), item(8, 1), item(2, 2, { manual: true })],
      count: 6,
      dirty: true,
    }],
  };

  const [row] = BatchExportData.createSummaryRows(snapshot, new Map());
  assert.deepEqual({
    automaticCount: row.automaticCount,
    clusterCount: row.clusterCount,
    manualIncreaseCount: row.manualIncreaseCount,
    manualDeletionCount: row.manualDeletionCount,
    manualPointCount: row.manualPointCount,
    manualNetAdjustment: row.manualNetAdjustment,
    finalCount: row.finalCount,
    manuallyEdited: row.manuallyEdited,
  }, {
    automaticCount: 6,
    clusterCount: 2,
    manualIncreaseCount: 2,
    manualDeletionCount: 3,
    manualPointCount: 1,
    manualNetAdjustment: 0,
    finalCount: 6,
    manuallyEdited: "是",
  });
});

test("expands automatic corrections and manual points to one row per logical strand", () => {
  const initialItems = [
    item(7, 2, { center: [72.25, 24.5], confidence: 0.9, splitConfidence: 0.8 }),
    item(8, 3),
    item(9, 1),
  ];
  const finalItems = [
    item(7, 3, { center: [72.25, 24.5], confidence: 0.9, splitConfidence: 0.8 }),
    item(8, 1),
    item(2, 2, { manual: true, center: [150.5, 80.25], bbox: [146, 76, 9, 9] }),
  ];
  const snapshot = {
    items: [
      {
        queueIndex: 0,
        sourceFilename: "样本.jpg",
        status: "done",
        response: {},
        initialItems,
        finalItems,
        count: 6,
      },
      {
        queueIndex: 1,
        sourceFilename: "失败.jpg",
        status: "error",
        response: null,
        initialItems: [],
        finalItems: [],
        count: null,
      },
    ],
  };

  const rows = BatchExportData.createMarkerRows(snapshot, new Map([[0, "标注图/样本-annotated.png"]]));
  assert.equal(rows.length, 9);
  assert.equal(rows.filter((row) => row.finalCounted === "是").length, 6);
  assert.deepEqual(rows.map((row) => row.correction), [
    "自动保留", "自动保留", "人工增加数量",
    "自动保留", "人工删除", "人工删除",
    "人工删除", "人工补点", "人工增加数量",
  ]);

  assert.deepEqual(rows.slice(0, 3).map((row) => row.strandIndex), [1, 2, 3]);
  assert.ok(rows.slice(0, 3).every((row) => row.centerX === 72.25 && row.finalAnnotationId === 1));
  assert.equal(rows[0].confidence, 0.72);
  assert.equal(rows[6].finalAnnotationId, null);
  assert.equal(rows[7].originalClusterId, null);
  assert.equal(rows[7].finalAnnotationId, 3);
  assert.equal(rows[7].confidence, null);
  assert.equal(rows[7].annotatedPath, "标注图/样本-annotated.png");
});

test("rejects exports that do not reconcile to the final image count", () => {
  const snapshot = {
    items: [{
      queueIndex: 0,
      sourceFilename: "样本.jpg",
      status: "done",
      response: {},
      initialItems: [item(1, 1)],
      finalItems: [item(1, 1)],
      count: 2,
    }],
  };
  assert.throws(
    () => BatchExportData.createMarkerRows(snapshot, new Map()),
    /标记明细与最终数量不一致/,
  );
  assert.throws(
    () => BatchExportData.createSummaryRows(snapshot, new Map()),
    /修正汇总与最终数量不一致/,
  );
});
