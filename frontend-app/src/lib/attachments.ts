export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
export const MAX_ATTACHMENTS = 5;
export const ATTACHMENT_ACCEPT =
  ".pdf,.docx,.xlsx,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.webp,.gif";
const extensions = new Set(ATTACHMENT_ACCEPT.split(","));

export function attachmentError(file: File): string | null {
  if (
    !file.name ||
    file.name.length > 180 ||
    /[\\/\x00-\x1f\x7f]/u.test(file.name)
  )
    return "Use file names up to 180 characters without path separators or control characters.";
  if (
    !extensions.has(file.name.slice(file.name.lastIndexOf(".")).toLowerCase())
  )
    return `${file.name}: use PDF, DOCX, XLSX, text/CSV/JSON, or a PNG/JPG/WEBP/GIF image.`;
  if (!file.size) return `${file.name} is empty.`;
  if (file.size > MAX_ATTACHMENT_BYTES)
    return `${file.name} exceeds the 5 MB limit.`;
  return null;
}
export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
