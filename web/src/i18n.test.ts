import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { errorMessage } from "./errors";
import { DICTIONARIES, formatDay, mealLabel, shiftDay, t } from "./i18n";

describe("dictionaries", () => {
  it("have the same keys and no empty strings", () => {
    const uk = Object.keys(DICTIONARIES.uk).sort();
    const en = Object.keys(DICTIONARIES.en).sort();
    expect(en).toEqual(uk);
    for (const lang of ["uk", "en"] as const) {
      for (const value of Object.values(DICTIONARIES[lang])) expect(value.trim()).not.toBe("");
    }
  });

  it("spells grams correctly in Ukrainian", () => {
    expect(t("uk", "grams")).toBe("Грами");
  });
});

describe("dates and labels", () => {
  it("names today and yesterday and formats other days", () => {
    expect(formatDay("en", "2026-09-26", "2026-09-26")).toBe("Today");
    expect(formatDay("uk", "2026-09-25", "2026-09-26")).toBe("Вчора");
    expect(formatDay("en", "2026-09-20", "2026-09-26")).toMatch(/Sep/);
    expect(shiftDay("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("translates meal types and ignores unknown ones", () => {
    expect(mealLabel("uk", "lunch")).toBe("Обід");
    expect(mealLabel("en", "brunch")).toBe("");
    expect(mealLabel("en", null)).toBe("");
  });
});

describe("errorMessage", () => {
  it("translates known codes and never shows raw ones", () => {
    expect(errorMessage("en", new ApiError(409, "version_conflict"))).toBe(t("en", "errConflict"));
    expect(errorMessage("uk", new ApiError(413, "image_too_large"))).toBe(t("uk", "errImageTooLarge"));
    expect(errorMessage("en", new ApiError(503, "whatever"))).toBe(t("en", "errServer"));
    expect(errorMessage("en", new ApiError(404, "rule_not_found"))).toBe(t("en", "errNotFound"));
    expect(errorMessage("en", new ApiError(400, "weird_code"))).toBe(t("en", "errGeneric"));
    expect(errorMessage("en", new SyntaxError("Unexpected token '<'"))).toBe(t("en", "errGeneric"));
  });
});
