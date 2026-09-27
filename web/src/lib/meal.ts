import type { Meal } from "../i18n";

/** Local-hour boundaries, same as app/services/food_logging.default_meal_type. */
export const BREAKFAST_FROM = 4;
export const LUNCH_FROM = 11;
export const DINNER_FROM = 16;
export const SNACK_FROM = 21;

export function mealForHour(hour: number): Meal {
  if (hour >= BREAKFAST_FROM && hour < LUNCH_FROM) return "breakfast";
  if (hour >= LUNCH_FROM && hour < DINNER_FROM) return "lunch";
  if (hour >= DINNER_FROM && hour < SNACK_FROM) return "dinner";
  return "snack";
}

export function localHour(timezone: string, now: Date = new Date()): number {
  try {
    return Number(new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone, hour: "2-digit", hourCycle: "h23",
    }).format(now));
  } catch {
    return now.getHours();
  }
}

export function mealNow(timezone: string, now: Date = new Date()): Meal {
  return mealForHour(localHour(timezone, now));
}

export const DEFAULT_PORTION_G = 100;

/**
 * Grams to log: what the user typed wins; an empty field means "auto" —
 * the product's suggested/usual portion, then 100 g.
 */
export function portionFor(typed: string, suggested: string | number | null | undefined): string {
  const value = typed.trim().replace(",", ".");
  if (value) return value;
  const fallback = Number(suggested);
  return Number.isFinite(fallback) && fallback > 0 ? String(fallback) : String(DEFAULT_PORTION_G);
}
