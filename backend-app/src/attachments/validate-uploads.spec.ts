import { validateUploads, MAX_FILE_BYTES } from './validate-uploads.js';
function upload(name: string, buffer: Buffer) {
  return {
    originalname: name,
    buffer,
    size: buffer.length,
  } as Express.Multer.File;
}
describe('attachment validation', () => {
  it('accepts UTF-8 text, derives MIME and hash, and retains bytes', async () => {
    const result = await validateUploads([
      upload('report.csv', Buffer.from('name,value\nRevenue,42')),
    ]);
    expect(result[0].mimeType).toBe('text/csv');
    expect(result[0].sha256).toHaveLength(64);
    expect(result[0].data.toString()).toContain('Revenue,42');
  });
  it('rejects an executable renamed to PDF and mismatched image signatures', async () => {
    await expect(
      validateUploads([upload('fake.pdf', Buffer.from('not a PDF'))]),
    ).rejects.toThrow();
    await expect(
      validateUploads([upload('fake.png', Buffer.from('%PDF-1.7\nexample'))]),
    ).rejects.toThrow();
  });
  it('rejects empty, oversized, unsupported, and excessive files', async () => {
    await expect(
      validateUploads([upload('empty.txt', Buffer.alloc(0))]),
    ).rejects.toThrow();
    await expect(
      validateUploads([upload('large.txt', Buffer.alloc(MAX_FILE_BYTES + 1))]),
    ).rejects.toThrow();
    await expect(
      validateUploads([
        upload('script.html', Buffer.from('<script>bad()</script>')),
      ]),
    ).rejects.toThrow();
    await expect(
      validateUploads(
        Array.from({ length: 6 }, () => upload('a.txt', Buffer.from('text'))),
      ),
    ).rejects.toThrow();
  });
  it('rejects binary text and unsafe filenames', async () => {
    await expect(
      validateUploads([upload('binary.txt', Buffer.from([0xff, 0, 1]))]),
    ).rejects.toThrow();
    await expect(
      validateUploads([upload('../escape.txt', Buffer.from('text'))]),
    ).rejects.toThrow();
    await expect(
      validateUploads([upload('bad\nname.txt', Buffer.from('text'))]),
    ).rejects.toThrow();
  });
});
