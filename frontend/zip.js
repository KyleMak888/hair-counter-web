(function (root) {
  "use strict";

  const encoder = new TextEncoder();
  const crcTable = new Uint32Array(256);

  for (let value = 0; value < 256; value += 1) {
    let crc = value;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) ? (0xedb88320 ^ (crc >>> 1)) : (crc >>> 1);
    crcTable[value] = crc >>> 0;
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function zipDateTime(date) {
    const year = Math.max(1980, date.getFullYear());
    return {
      date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
      time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
    };
  }

  function create(entries, modifiedAt = new Date()) {
    if (!Array.isArray(entries) || entries.length === 0) throw new Error("ZIP 中没有可下载的文件");
    if (entries.length > 0xffff) throw new Error("ZIP 文件数量超过限制");

    const localParts = [];
    const centralParts = [];
    const { date, time } = zipDateTime(modifiedAt);
    let localOffset = 0;
    let centralSize = 0;

    for (const entry of entries) {
      const name = encoder.encode(String(entry.name || "file"));
      const data = entry.data instanceof Uint8Array ? entry.data : new Uint8Array(entry.data);
      if (name.byteLength > 0xffff) throw new Error("ZIP 文件名过长");
      if (data.byteLength > 0xffffffff) throw new Error("ZIP 中的单个文件超过 4 GB");

      const checksum = crc32(data);
      const localHeader = new Uint8Array(30 + name.byteLength);
      const localView = new DataView(localHeader.buffer);
      localView.setUint32(0, 0x04034b50, true);
      localView.setUint16(4, 20, true);
      localView.setUint16(6, 0x0800, true);
      localView.setUint16(8, 0, true);
      localView.setUint16(10, time, true);
      localView.setUint16(12, date, true);
      localView.setUint32(14, checksum, true);
      localView.setUint32(18, data.byteLength, true);
      localView.setUint32(22, data.byteLength, true);
      localView.setUint16(26, name.byteLength, true);
      localView.setUint16(28, 0, true);
      localHeader.set(name, 30);
      localParts.push(localHeader, data);

      const centralHeader = new Uint8Array(46 + name.byteLength);
      const centralView = new DataView(centralHeader.buffer);
      centralView.setUint32(0, 0x02014b50, true);
      centralView.setUint16(4, 20, true);
      centralView.setUint16(6, 20, true);
      centralView.setUint16(8, 0x0800, true);
      centralView.setUint16(10, 0, true);
      centralView.setUint16(12, time, true);
      centralView.setUint16(14, date, true);
      centralView.setUint32(16, checksum, true);
      centralView.setUint32(20, data.byteLength, true);
      centralView.setUint32(24, data.byteLength, true);
      centralView.setUint16(28, name.byteLength, true);
      centralView.setUint16(30, 0, true);
      centralView.setUint16(32, 0, true);
      centralView.setUint16(34, 0, true);
      centralView.setUint16(36, 0, true);
      centralView.setUint32(38, 0, true);
      centralView.setUint32(42, localOffset, true);
      centralHeader.set(name, 46);
      centralParts.push(centralHeader);

      localOffset += localHeader.byteLength + data.byteLength;
      centralSize += centralHeader.byteLength;
    }

    const end = new Uint8Array(22);
    const endView = new DataView(end.buffer);
    endView.setUint32(0, 0x06054b50, true);
    endView.setUint16(4, 0, true);
    endView.setUint16(6, 0, true);
    endView.setUint16(8, entries.length, true);
    endView.setUint16(10, entries.length, true);
    endView.setUint32(12, centralSize, true);
    endView.setUint32(16, localOffset, true);
    endView.setUint16(20, 0, true);

    return new Blob([...localParts, ...centralParts, end], { type: "application/zip" });
  }

  const api = Object.freeze({ create });
  root.ZipArchive = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis === "object" ? globalThis : this);
