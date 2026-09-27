/** Client-side photo downscale before upload (phone photos are 3–8 MB). */

export const MAX_SIDE_PX = 1600;
export const JPEG_QUALITY = 0.85;
export const SKIP_BELOW_BYTES = 1_500_000;

export function fitWithin(width: number, height: number, maxSide: number = MAX_SIDE_PX): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxSide || longest <= 0) return { width, height };
  const scale = maxSide / longest;
  return { width: Math.round(width * scale), height: Math.round(height * scale) };
}

/**
 * Returns a JPEG no larger than MAX_SIDE_PX on its longest side. Small files
 * and anything the browser cannot decode are returned unchanged (the server
 * validates type and size anyway). Barcodes stay readable at 1600 px.
 */
export async function downscaleImage(file: File): Promise<Blob> {
  if (file.size < SKIP_BELOW_BYTES || typeof createImageBitmap !== "function") return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file;
  }
  try {
    const size = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = size.width;
    canvas.height = size.height;
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, size.width, size.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    return blob && blob.size < file.size ? blob : file;
  } finally {
    bitmap.close();
  }
}
