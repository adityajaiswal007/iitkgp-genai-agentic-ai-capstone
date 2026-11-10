/* oxlint-disable no-control-regex -- Intentionally reject control bytes in uploaded names and text. */
import {
  BadRequestException,
  PayloadTooLargeException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { createHash } from 'node:crypto';
import { extname } from 'node:path';
import { fileTypeFromBuffer } from 'file-type';

export const MAX_FILE_BYTES = 5 * 1024 * 1024;
export const MAX_FILES = 5;
export type ValidatedUpload = {
  name: string;
  mimeType: string;
  size: number;
  sha256: string;
  data: Buffer;
  position: number;
};
const textTypes: Record<string, string> = {
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.csv': 'text/csv',
  '.json': 'application/json',
};
const binaryTypes: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.docx':
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
};

export async function validateUploads(
  files: Express.Multer.File[],
): Promise<ValidatedUpload[]> {
  if (files.length > MAX_FILES)
    throw new BadRequestException('Attach up to 5 files per message.');
  return Promise.all(
    files.map(async (file, position) => {
      const name = file.originalname.normalize('NFC');
      if (!name || name.length > 180 || /[\\/\x00-\x1f\x7f]/u.test(name))
        throw new BadRequestException(
          'File names must be at most 180 characters without path separators or control characters.',
        );
      const size = file.buffer.length;
      if (!size)
        throw new BadRequestException('Empty files cannot be attached.');
      if (size > MAX_FILE_BYTES)
        throw new PayloadTooLargeException(
          'Each attachment must be 5 MB or smaller.',
        );
      const extension = extname(name).toLowerCase();
      let mimeType = textTypes[extension];
      if (mimeType) {
        try {
          const text = new TextDecoder('utf-8', { fatal: true }).decode(
            file.buffer,
          );
          if (/[\x00-\x08\x0b\x0c\x0e-\x1f]/u.test(text))
            throw new Error('Binary content');
          if (extension === '.json') JSON.parse(text);
        } catch {
          throw new UnsupportedMediaTypeException(
            'Text attachments must contain valid UTF-8 text; JSON files must contain valid JSON.',
          );
        }
      } else {
        const allowed = binaryTypes[extension];
        if (!allowed)
          throw new UnsupportedMediaTypeException(
            'Supported files: PDF, DOCX, XLSX, TXT, MD, CSV, JSON, PNG, JPG, WEBP, GIF.',
          );
        let detected: Awaited<ReturnType<typeof fileTypeFromBuffer>>;
        try {
          detected = await fileTypeFromBuffer(file.buffer);
        } catch {
          detected = undefined;
        }
        if (detected?.mime !== allowed)
          throw new UnsupportedMediaTypeException(
            'File content does not match its extension.',
          );
        mimeType = allowed;
      }
      return {
        name,
        mimeType,
        size,
        data: file.buffer,
        position,
        sha256: createHash('sha256').update(file.buffer).digest('hex'),
      };
    }),
  );
}
