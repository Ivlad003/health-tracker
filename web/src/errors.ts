import { ApiError } from "./api";
import { t, type Key, type Lang } from "./i18n";

const CODE_KEYS: Record<string, Key> = {
  network_error: "errNetwork",
  server_error: "errServer",
  bad_response: "errServer",
  session_invalid: "errSession",
  init_data_expired: "errSession",
  init_data_replayed: "errSession",
  init_data_missing: "errSession",
  csrf_failed: "errSession",
  version_conflict: "errConflict",
  not_found: "errNotFound",
  draft_not_found: "errNotFound",
  item_not_found: "errNotFound",
  validation_error: "errValidation",
  date_invalid: "errValidation",
  date_out_of_range: "errValidation",
  grams_invalid: "errValidation",
  image_too_large: "errImageTooLarge",
  image_type_unsupported: "errImageType",
  fatsecret_not_connected: "errFatsecretNotConnected",
  provider_unavailable: "errProvider",
  forbidden: "errForbidden",
  not_importable: "errNotImportable",
  date_range_too_long: "errRange",
  feature_unavailable: "errFeatureUnavailable",
};

/** Error code for any thrown value. */
export function errorCode(err: unknown): string {
  if (err instanceof ApiError) return err.code;
  if (err instanceof Error) return err.message || "error";
  return "error";
}

/** User-facing, translated message; unknown codes fall back to a generic text. */
export function errorMessage(lang: Lang, err: unknown): string {
  const code = errorCode(err);
  const key = CODE_KEYS[code];
  if (key) return t(lang, key);
  if (err instanceof ApiError && err.status >= 500) return t(lang, "errServer");
  if (code.endsWith("_not_found")) return t(lang, "errNotFound");
  return t(lang, "errGeneric");
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
