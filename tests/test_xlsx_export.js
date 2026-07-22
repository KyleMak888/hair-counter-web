const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const test = require("node:test");

const ZipArchive = require("../frontend/zip.js");
const XlsxWorkbook = require("../frontend/xlsx.js");

async function zipEntries(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder();
  const entries = new Map();
  let offset = 0;

  while (offset + 4 <= bytes.byteLength && view.getUint32(offset, true) === 0x04034b50) {
    const method = view.getUint16(offset + 8, true);
    const dataLength = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    const extraLength = view.getUint16(offset + 28, true);
    assert.equal(method, 0, "test parser expects stored ZIP entries");
    const nameStart = offset + 30;
    const dataStart = nameStart + nameLength + extraLength;
    const name = decoder.decode(bytes.subarray(nameStart, nameStart + nameLength));
    entries.set(name, bytes.slice(dataStart, dataStart + dataLength));
    offset = dataStart + dataLength;
  }

  return entries;
}

test("creates an Excel-compatible batch summary with typed counts", async () => {
  const workbook = XlsxWorkbook.create({
    summaryRows: [
      {
        index: 1,
        originalFilename: "样本<&>.jpg",
        annotatedPath: "标注图/样本___-annotated.png",
        status: "成功",
        automaticCount: 20,
        manualIncreaseCount: 1,
        manualDeletionCount: 4,
        manualPointCount: 1,
        manualNetAdjustment: -2,
        finalCount: 18,
        manuallyEdited: "是",
        error: "",
      },
      {
        index: 2,
        originalFilename: "失败图片.png",
        annotatedPath: "",
        status: "失败",
        automaticCount: null,
        manualIncreaseCount: null,
        manualDeletionCount: null,
        manualPointCount: null,
        manualNetAdjustment: null,
        finalCount: null,
        manuallyEdited: "",
        error: "余额不足\u0001，已跳过",
      },
    ],
    markerRows: [
      {
        imageIndex: 1,
        originalFilename: "样本<&>.jpg",
        annotatedPath: "标注图/样本___-annotated.png",
        originalClusterId: 3,
        finalAnnotationId: 2,
        strandIndex: 1,
        centerX: 42.25,
        centerY: 88.5,
        bboxX: 39,
        bboxY: 84,
        bboxWidth: 8,
        bboxHeight: 9,
        correction: "自动保留",
        finalCounted: "是",
        confidence: 0.875,
      },
      {
        imageIndex: 1,
        originalFilename: "样本<&>.jpg",
        annotatedPath: "标注图/样本___-annotated.png",
        originalClusterId: 4,
        finalAnnotationId: null,
        strandIndex: 1,
        centerX: 102,
        centerY: 72,
        bboxX: 98,
        bboxY: 68,
        bboxWidth: 8,
        bboxHeight: 8,
        correction: "人工删除",
        finalCounted: "否",
        confidence: 0.64,
      },
    ],
  });

  assert.equal(workbook.type, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  const entries = await zipEntries(workbook);
  assert.deepEqual([...entries.keys()].sort(), [
    "[Content_Types].xml",
    "_rels/.rels",
    "docProps/app.xml",
    "docProps/core.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/styles.xml",
    "xl/workbook.xml",
    "xl/worksheets/sheet1.xml",
    "xl/worksheets/sheet2.xml",
  ].sort());

  const decoder = new TextDecoder();
  const workbookXml = decoder.decode(entries.get("xl/workbook.xml"));
  const sheetXml = decoder.decode(entries.get("xl/worksheets/sheet1.xml"));
  const markerSheetXml = decoder.decode(entries.get("xl/worksheets/sheet2.xml"));
  const stylesXml = decoder.decode(entries.get("xl/styles.xml"));
  assert.match(workbookXml, /sheet name="逐图结果"/);
  assert.match(workbookXml, /sheet name="标记明细"/);
  assert.match(sheetXml, /<pane ySplit="1"[^>]+state="frozen"\/>/);
  assert.match(sheetXml, /<autoFilter ref="A1:L3"\/>/);
  assert.match(sheetXml, /<c r="A2" s="4" t="n"><v>1<\/v><\/c>/);
  assert.match(sheetXml, /<c r="E2" s="2" t="n"><v>20<\/v><\/c>/);
  assert.match(sheetXml, /<c r="F2" s="2" t="n"><v>1<\/v><\/c>/);
  assert.match(sheetXml, /<c r="G2" s="2" t="n"><v>4<\/v><\/c>/);
  assert.match(sheetXml, /<c r="H2" s="2" t="n"><v>1<\/v><\/c>/);
  assert.match(sheetXml, /<c r="I2" s="8" t="n"><v>-2<\/v><\/c>/);
  assert.match(sheetXml, /<c r="J2" s="2" t="n"><v>18<\/v><\/c>/);
  assert.match(sheetXml, /人工增加数量/);
  assert.match(sheetXml, /人工删除数量/);
  assert.match(sheetXml, /人工补点数量/);
  assert.match(sheetXml, /人工净修正数量/);
  assert.match(stylesXml, /numFmtId="164" formatCode="\+#,##0;-#,##0;0"/);
  assert.match(sheetXml, /样本&lt;&amp;&gt;\.jpg/);
  assert.match(sheetXml, /余额不足，已跳过/);
  assert.doesNotMatch(sheetXml, /\u0001/);
  assert.match(markerSheetXml, /<pane xSplit="2" ySplit="1"[^>]+state="frozen"\/>/);
  assert.match(markerSheetXml, /<autoFilter ref="A1:O3"\/>/);
  assert.match(markerSheetXml, /<c r="G2" s="5" t="n"><v>42\.25<\/v><\/c>/);
  assert.match(markerSheetXml, /<c r="O2" s="6" t="n"><v>0\.875<\/v><\/c>/);
  assert.match(markerSheetXml, /<c r="E3" s="4" t="inlineStr">/);
  assert.match(markerSheetXml, /人工删除/);
  if (process.env.XLSX_FIXTURE_PATH) {
    await fs.writeFile(process.env.XLSX_FIXTURE_PATH, new Uint8Array(await workbook.arrayBuffer()));
  }
});

test("supports the complete package directory structure and Unicode paths", async () => {
  const workbook = XlsxWorkbook.create({ summaryRows: [], markerRows: [] });
  const archive = ZipArchive.create([
    { name: "批量结果.xlsx", data: new Uint8Array(await workbook.arrayBuffer()) },
    { name: "批量结果.json", data: new TextEncoder().encode("{}") },
    { name: "标注图/样本-annotated.png", data: new Uint8Array([137, 80, 78, 71]) },
  ]);
  const entries = await zipEntries(archive);
  assert.deepEqual([...entries.keys()], [
    "批量结果.xlsx",
    "批量结果.json",
    "标注图/样本-annotated.png",
  ]);
});
