/** Energy balance colouring (docs/design/pages/01-dashboard.md):
 * deficit → green, on target → blue, surplus → orange, large surplus → red. */

export const ON_TARGET_KCAL = 50;
export const LARGE_SURPLUS_KCAL = 300;

export type BalanceTone = "deficit" | "on-target" | "surplus" | "large-surplus";

export function balanceTone(balanceKcal: number): BalanceTone {
  if (!Number.isFinite(balanceKcal) || Math.abs(balanceKcal) <= ON_TARGET_KCAL) return "on-target";
  if (balanceKcal < 0) return "deficit";
  return balanceKcal > LARGE_SURPLUS_KCAL ? "large-surplus" : "surplus";
}

/** Share of the goal eaten, clamped to 0..100 (0 when there is no goal). */
export function goalProgress(eatenKcal: number, goalKcal: number | null | undefined): number {
  if (!goalKcal || goalKcal <= 0 || !Number.isFinite(eatenKcal)) return 0;
  return Math.max(0, Math.min(100, (eatenKcal / goalKcal) * 100));
}

/** Goal for the day: the date-effective goal, then the profile goal. `0` is a real value. */
export function effectiveGoal(dayGoal: number | null | undefined, profileGoal: number | null | undefined): number | null {
  if (typeof dayGoal === "number") return dayGoal;
  if (typeof profileGoal === "number") return profileGoal;
  return null;
}
