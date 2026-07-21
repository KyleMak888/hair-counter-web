(function (root) {
  "use strict";

  const MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const encoder = new TextEncoder();
  const summaryHeaders = ["序号", "原图文件名", "标注图路径", "处理状态", "最终数量", "是否人工修正", "失败原因"];
  const markerHeaders = [
    "图片序号", "原图文件名", "标注图路径", "原始簇编号", "最终标注编号", "簇内序号",
    "中心X(px)", "中心Y(px)", "边界框X(px)", "边界框Y(px)", "边界框宽(px)", "边界框高(px)",
    "修正结果", "最终是否计数", "原始簇置信度",
  ];

  function cleanText(value) {
    return String(value ?? "")
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "")
      .slice(0, 32767);
  }

  function escapeXml(value) {
    return cleanText(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&apos;",
    })[character]);
  }

  function textCell(reference, value, style = 0) {
    return `<c r="${reference}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${escapeXml(value)}</t></is></c>`;
  }

  function numberCell(reference, value, style = 2) {
    const number = Number(value);
    if (!Number.isFinite(number)) return textCell(reference, "", style);
    return `<c r="${reference}" s="${style}" t="n"><v>${number}</v></c>`;
  }

  function optionalNumberCell(reference, value, style = 2) {
    return value === null || value === undefined || value === ""
      ? textCell(reference, "", style)
      : numberCell(reference, value, style);
  }

  function headerCells(headers) {
    return headers.map((value, index) => textCell(`${String.fromCharCode(65 + index)}1`, value, 1)).join("");
  }

  function summaryWorksheetXml(rows) {
    const rowCount = rows.length + 1;
    const dataRows = rows.map((row, index) => {
      const rowNumber = index + 2;
      const cells = [
        numberCell(`A${rowNumber}`, row.index, 4),
        textCell(`B${rowNumber}`, row.originalFilename),
        textCell(`C${rowNumber}`, row.annotatedPath),
        textCell(`D${rowNumber}`, row.status),
        optionalNumberCell(`E${rowNumber}`, row.finalCount),
        textCell(`F${rowNumber}`, row.manuallyEdited),
        textCell(`G${rowNumber}`, row.error, 3),
      ].join("");
      return `<row r="${rowNumber}" ht="22" customHeight="1">${cells}</row>`;
    }).join("");

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:G${rowCount}"/>
  <sheetViews>
    <sheetView showGridLines="0" workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="22"/>
  <cols>
    <col min="1" max="1" width="8" customWidth="1"/>
    <col min="2" max="2" width="34" customWidth="1"/>
    <col min="3" max="3" width="44" customWidth="1"/>
    <col min="4" max="4" width="13" customWidth="1"/>
    <col min="5" max="5" width="14" customWidth="1"/>
    <col min="6" max="6" width="16" customWidth="1"/>
    <col min="7" max="7" width="48" customWidth="1"/>
  </cols>
  <sheetData>
    <row r="1" ht="28" customHeight="1">${headerCells(summaryHeaders)}</row>
    ${dataRows}
  </sheetData>
  <autoFilter ref="A1:G${rowCount}"/>
  <pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>
</worksheet>`;
  }

  function markerWorksheetXml(rows) {
    const rowCount = rows.length + 1;
    const dataRows = rows.map((row, index) => {
      const rowNumber = index + 2;
      const cells = [
        numberCell(`A${rowNumber}`, row.imageIndex, 4),
        textCell(`B${rowNumber}`, row.originalFilename),
        textCell(`C${rowNumber}`, row.annotatedPath),
        optionalNumberCell(`D${rowNumber}`, row.originalClusterId, 4),
        optionalNumberCell(`E${rowNumber}`, row.finalAnnotationId, 4),
        numberCell(`F${rowNumber}`, row.strandIndex, 4),
        numberCell(`G${rowNumber}`, row.centerX, 5),
        numberCell(`H${rowNumber}`, row.centerY, 5),
        numberCell(`I${rowNumber}`, row.bboxX),
        numberCell(`J${rowNumber}`, row.bboxY),
        numberCell(`K${rowNumber}`, row.bboxWidth),
        numberCell(`L${rowNumber}`, row.bboxHeight),
        textCell(`M${rowNumber}`, row.correction),
        textCell(`N${rowNumber}`, row.finalCounted, 7),
        optionalNumberCell(`O${rowNumber}`, row.confidence, 6),
      ].join("");
      return `<row r="${rowNumber}" ht="22" customHeight="1">${cells}</row>`;
    }).join("");

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:O${rowCount}"/>
  <sheetViews>
    <sheetView showGridLines="0" workbookViewId="0">
      <pane xSplit="2" ySplit="1" topLeftCell="C2" activePane="bottomRight" state="frozen"/>
      <selection pane="bottomRight" activeCell="C2" sqref="C2"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="22"/>
  <cols>
    <col min="1" max="1" width="10" customWidth="1"/>
    <col min="2" max="2" width="32" customWidth="1"/>
    <col min="3" max="3" width="42" customWidth="1"/>
    <col min="4" max="6" width="14" customWidth="1"/>
    <col min="7" max="8" width="13" customWidth="1"/>
    <col min="9" max="12" width="15" customWidth="1"/>
    <col min="13" max="13" width="18" customWidth="1"/>
    <col min="14" max="15" width="17" customWidth="1"/>
  </cols>
  <sheetData>
    <row r="1" ht="30" customHeight="1">${headerCells(markerHeaders)}</row>
    ${dataRows}
  </sheetData>
  <autoFilter ref="A1:O${rowCount}"/>
  <pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>`;
  }

  function create(data) {
    if (!root.ZipArchive?.create) throw new Error("ZIP 组件未加载");
    if (!data || !Array.isArray(data.summaryRows) || !Array.isArray(data.markerRows)) {
      throw new Error("Excel 数据格式无效");
    }

    const files = [
      {
        name: "[Content_Types].xml",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>`),
      },
      {
        name: "_rels/.rels",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`),
      },
      {
        name: "docProps/core.xml",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>毛发计数批量结果</dc:title>
  <dc:creator>毛发计数器</dc:creator>
  <cp:lastModifiedBy>毛发计数器</cp:lastModifiedBy>
</cp:coreProperties>`),
      },
      {
        name: "docProps/app.xml",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>毛发计数器</Application>
  <TitlesOfParts><vt:vector size="2" baseType="lpstr"><vt:lpstr>逐图结果</vt:lpstr><vt:lpstr>标记明细</vt:lpstr></vt:vector></TitlesOfParts>
</Properties>`),
      },
      {
        name: "xl/workbook.xml",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews><workbookView xWindow="0" yWindow="0" windowWidth="22000" windowHeight="12000"/></bookViews>
  <sheets><sheet name="逐图结果" sheetId="1" r:id="rId1"/><sheet name="标记明细" sheetId="2" r:id="rId2"/></sheets>
</workbook>`),
      },
      {
        name: "xl/_rels/workbook.xml.rels",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>`),
      },
      {
        name: "xl/styles.xml",
        data: encoder.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><color theme="1"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/><family val="2"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1D4ED8"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left/><right/><top/><bottom style="thin"><color rgb="FFD7DFEB"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="8">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="10" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
  <tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>`),
      },
      { name: "xl/worksheets/sheet1.xml", data: encoder.encode(summaryWorksheetXml(data.summaryRows)) },
      { name: "xl/worksheets/sheet2.xml", data: encoder.encode(markerWorksheetXml(data.markerRows)) },
    ];

    const archive = root.ZipArchive.create(files);
    return new Blob([archive], { type: MIME_TYPE });
  }

  const api = Object.freeze({ create });
  root.XlsxWorkbook = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : this);
