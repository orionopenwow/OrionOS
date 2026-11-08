import type { AssetRecord } from '@orion/shared-types';

export const ORION_HTTP_URL = 'http://localhost:9120';

/** Extensions the asset pipeline has actions for (images, audio/video, 3D models). */
export const ASSET_EXT = /\.(png|jpe?g|webp|gif|bmp|tiff?|mp3|wav|flac|ogg|m4a|mp4|mov|mkv|webm|avi|glb|gltf|fbx|obj)$/i;

export function isPipelineAsset(file: File): boolean {
  return ASSET_EXT.test(file.name) || /^(image|audio|video)\//.test(file.type);
}

export async function uploadAsset(file: File): Promise<AssetRecord> {
  const res = await fetch(`${ORION_HTTP_URL}/assets/upload`, {
    method: 'POST',
    headers: { 'X-Filename': encodeURIComponent(file.name || 'pasted.png'), 'Content-Type': 'application/octet-stream' },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Upload failed (${res.status})`);
  return data.asset as AssetRecord;
}

/** A document dropped on the core: saved to the knowledge vault, indexed, and briefed aloud by the daemon. */
export async function dropToVault(file: File): Promise<{ name: string; briefing: string }> {
  const res = await fetch(`${ORION_HTTP_URL}/vault/drop`, {
    method: 'POST',
    headers: { 'X-Filename': encodeURIComponent(file.name || 'document.txt'), 'Content-Type': 'application/octet-stream' },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Upload failed (${res.status})`);
  return data;
}
