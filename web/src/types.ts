export interface Me {
  id: number;
  telegram_user_id: number;
  language: "uk" | "en";
  timezone: string;
  daily_calorie_goal: number | null;
  birth_year: number | null;
  sex: "male" | "female" | null;
  height_cm: number | null;
  journal_enabled: boolean;
  journal_time_1: string | null;
  journal_time_2: string | null;
  profile_version: string | null;
  fatsecret_connected: boolean;
  whoop_connected: boolean;
  is_admin: boolean;
  features: Record<string, boolean>;
}

export interface TodayStats {
  today_calories_in: number;
  calories_partial: boolean;
  today_calories_out: number;
  calories_burned_source: string;
  today_strain: number;
  today_workout_count: number;
  cycle_score_state: string;
  whoop_sleep: string;
  whoop_recovery: string;
  whoop_activities: string;
  apple_health_steps: number;
  apple_health_active_energy_kcal: number;
  apple_health_avg_heart_rate: number;
  apple_health_sleep_hours: number;
  apple_health_distance_km: number;
  apple_health_exercise_minutes: number;
  apple_health_body_mass_kg: number;
  apple_health_workout_count: number;
  apple_health_workouts: string;
  bmr_kcal: number | null;
  timezone: string;
  expired_services: string[];
}

export interface FoodEntry {
  id: number | null;
  name: string | null;
  grams: string | number | null;
  meal_type: string | null;
  energy_kcal: string | number | null;
  protein_g: string | number | null;
  fat_g: string | number | null;
  carbs_g: string | number | null;
  source: string;
  sync_status: string | null;
  version: number | null;
}

export interface DayView {
  local_date: string;
  total_kcal: string;
  protein_g: string;
  fat_g: string;
  carbs_g: string;
  partial: boolean;
  entries: FoodEntry[];
  goal: { calories?: number; protein_g?: number | null; fat_g?: number | null; carbs_g?: number | null } | null;
}

export interface Product {
  product_id: number;
  label: string;
  brand: string | null;
  usual_portion_g: string | number | null;
  kcal_per_100g: string | number | null;
  incomplete: boolean;
  version: number;
}

export interface SearchItem {
  product_id: number | null;
  provider: string;
  external_id: string | null;
  label: string;
  kcal_per_100g: string | null;
  suggested_portion_g: string | null;
}

export interface DraftItem {
  text?: string;
  grams?: string | number | null;
  status?: string;
  selected?: { label?: string; product_id?: number };
}

export interface Draft {
  id: number;
  version: number;
  state: string;
  meal_type?: string | null;
  items: DraftItem[];
}

export interface Preferences {
  recording_policy: "auto_confirmed" | "review_all";
  catalog_auto_add: boolean;
  fatsecret_export: boolean;
  briefing_morning_enabled: boolean;
  briefing_morning_time: string;
  briefing_evening_enabled: boolean;
  briefing_evening_time: string;
  history_import_days: number;
}

export interface Integrations {
  fatsecret: {
    connected: boolean;
    outbox: Record<string, number>;
    last_successful_sync: string | null;
    history_import: { id?: number; status?: string } | null;
  };
  whoop: { connected: boolean };
  apple_health: { connected: boolean; last_sync_at: string | null; setup_hint: string };
}

export interface DefaultRule {
  id: number;
  alias_display: string;
  product_id: number;
  product_label: string;
  version: number;
  suggested_portion_g: string | number | null;
}

export type Page = "dashboard" | "food" | "activity" | "history" | "profile" | "admin";
export const USER_PAGES: readonly Page[] = ["dashboard", "food", "activity", "history", "profile"];

export interface DayRange {
  days: DayView[];
}

export interface GoalsResponse {
  current: { calories: number; effective_date: string | null } | null;
  history: { effective_date: string; calories: number }[];
}

export interface PreviewCandidate {
  label?: string;
  source?: string;
  product_id?: number | null;
}

export interface PreviewResult {
  decision: string;
  reason: string | null;
  candidates: PreviewCandidate[];
}
