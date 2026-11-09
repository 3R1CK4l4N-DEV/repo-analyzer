import { describe, expect, it } from "vitest";

import { formatBytes, formatDate, shortCommit } from "../lib/format";

describe("formatBytes", () => {
  it("formatea bytes y unidades binarias", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(2 * 1024 * 1024)).toBe("2.0 MB");
  });
});

describe("shortCommit", () => {
  it("acorta commits y representa valores ausentes", () => {
    expect(shortCommit("abcdef123456")).toBe("abcdef1");
    expect(shortCommit(null)).toBe("—");
  });
});

describe("formatDate", () => {
  it("formatea fechas válidas y controla entradas inválidas", () => {
    const value = formatDate("2024-01-02T03:04:00Z");
    expect(value).toContain("2024");
    expect(value).toContain("ene");
    expect(formatDate("no-es-fecha")).toBe("Fecha desconocida");
  });
});
