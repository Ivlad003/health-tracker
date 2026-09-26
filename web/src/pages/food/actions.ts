import { api, ApiError, idempotencyKey } from "../../api";
import type { Meal } from "../../i18n";
import { downscaleImage } from "../../lib/image";
import type { Draft, SearchItem } from "../../types";

export async function logProduct(productId: number, grams: string, meal: Meal): Promise<void> {
  await api("/api/v1/webapp/food-entries", {
    method: "POST",
    body: JSON.stringify({ product_id: productId, grams, meal_type: meal, idempotency_key: idempotencyKey() }),
  });
}

/** Search hits may be provider-only: import into My Products first. */
export async function ensureProduct(item: SearchItem): Promise<number> {
  if (item.product_id) return item.product_id;
  if (item.provider !== "fatsecret" || !item.external_id) throw new ApiError(400, "not_importable");
  const imported = await api<{ product_id: number }>("/api/v1/webapp/products/import", {
    method: "POST",
    body: JSON.stringify({ provider: "fatsecret", external_id: item.external_id, display_name: item.label }),
  });
  return imported.product_id;
}

export async function deleteEntry(id: number, version: number): Promise<void> {
  await api(`/api/v1/webapp/food-entries/${id}?version=${version}`, { method: "DELETE" });
}

export async function uploadPhoto(file: File, caption: string): Promise<{ draft: Draft | null; message: string | null }> {
  const body = await downscaleImage(file);
  const params = new URLSearchParams({ idempotency_key: idempotencyKey(), caption });
  return api(`/api/v1/webapp/uploads?${params.toString()}`, {
    method: "POST",
    body,
    headers: { "Content-Type": body.type || file.type || "image/jpeg" },
  });
}

export async function commitDraft(draft: Draft, grams: Record<number, string>, meal: Meal): Promise<void> {
  const items = Object.entries(grams)
    .filter(([, value]) => value.trim() !== "")
    .map(([index, value]) => ({ index: Number(index), grams: value.trim().replace(",", ".") }));
  const saved = await api<Draft>(`/api/v1/webapp/food-drafts/${draft.id}`, {
    method: "PATCH",
    body: JSON.stringify({ version: draft.version, items, meal_type: meal }),
  });
  await api(`/api/v1/webapp/food-drafts/${draft.id}/commit`, {
    method: "POST",
    body: JSON.stringify({ version: saved.version }),
  });
}

export async function cancelDraft(id: number): Promise<void> {
  await api(`/api/v1/webapp/food-drafts/${id}/cancel`, { method: "POST" });
}
