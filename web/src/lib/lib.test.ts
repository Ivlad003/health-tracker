import { describe, expect, it } from "vitest";
import { balanceTone, effectiveGoal, goalProgress } from "./balance";
import { fitWithin } from "./image";
import { mealForHour, portionFor } from "./meal";
import { parseGoal } from "../pages/profile/GoalCard";

describe("balanceTone", () => {
  it("uses all four design tones", () => {
    expect(balanceTone(-400)).toBe("deficit");
    expect(balanceTone(-50)).toBe("on-target");
    expect(balanceTone(0)).toBe("on-target");
    expect(balanceTone(50)).toBe("on-target");
    expect(balanceTone(120)).toBe("surplus");
    expect(balanceTone(301)).toBe("large-surplus");
    expect(balanceTone(Number.NaN)).toBe("on-target");
  });
});

describe("goals", () => {
  it("keeps a zero goal instead of falling through", () => {
    expect(effectiveGoal(0, 2000)).toBe(0);
    expect(effectiveGoal(undefined, 1800)).toBe(1800);
    expect(effectiveGoal(null, null)).toBeNull();
  });

  it("clamps progress and handles a missing goal", () => {
    expect(goalProgress(1000, 2000)).toBe(50);
    expect(goalProgress(5000, 2000)).toBe(100);
    expect(goalProgress(1000, 0)).toBe(0);
    expect(goalProgress(1000, null)).toBe(0);
  });

  it("validates the calorie goal range", () => {
    expect(parseGoal("2000")).toBe(2000);
    expect(parseGoal(" 500 ")).toBe(500);
    expect(parseGoal("499")).toBeNull();
    expect(parseGoal("10001")).toBeNull();
    expect(parseGoal("")).toBeNull();
    expect(parseGoal("1500.5")).toBeNull();
  });
});

describe("meals and portions", () => {
  it("matches the backend meal boundaries", () => {
    expect(mealForHour(3)).toBe("snack");
    expect(mealForHour(4)).toBe("breakfast");
    expect(mealForHour(11)).toBe("lunch");
    expect(mealForHour(16)).toBe("dinner");
    expect(mealForHour(21)).toBe("snack");
  });

  it("prefers typed grams over the suggested portion", () => {
    expect(portionFor("250", "150")).toBe("250");
    expect(portionFor("12,5", "150")).toBe("12.5");
    expect(portionFor("", "150")).toBe("150");
    expect(portionFor("  ", null)).toBe("100");
    expect(portionFor("", "0")).toBe("100");
  });
});

describe("fitWithin", () => {
  it("scales the longest side down and keeps small images", () => {
    expect(fitWithin(4000, 3000, 1600)).toEqual({ width: 1600, height: 1200 });
    expect(fitWithin(3000, 4000, 1600)).toEqual({ width: 1200, height: 1600 });
    expect(fitWithin(800, 600, 1600)).toEqual({ width: 800, height: 600 });
  });
});
