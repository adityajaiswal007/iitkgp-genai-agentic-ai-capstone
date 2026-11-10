import 'dotenv/config';
import { randomUUID } from 'node:crypto';
import { createServer, type Server } from 'node:http';
import { Test } from '@nestjs/testing';
import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { DataSource } from 'typeorm';

let app: INestApplication;
let agent: Server;
const agentRequests: {
  conversation_id: string;
  history: { role: string; content: string }[];
}[] = [];
const ids: string[] = [];
async function startApp() {
  const { AppModule } = await import('../dist/app.module.js');
  const { configureApp } = await import('../dist/configure-app.js');
  const module = await Test.createTestingModule({
    imports: [AppModule],
  }).compile();
  const instance = module.createNestApplication({
    bodyParser: false,
    logger: false,
  });
  configureApp(instance);
  await instance.get(DataSource).runMigrations();
  await instance.init();
  return instance;
}
beforeAll(async () => {
  const testDatabase = process.env.TEST_DB_NAME ?? 'operations_copilot_test';
  if (testDatabase === process.env.DB_NAME)
    throw new Error('Tests require a distinct database');
  process.env.DB_NAME = testDatabase;
  // Exercise the backend over HTTP without consuming hosted model tokens.
  agent = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    agentRequests.push(JSON.parse(body));
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        mode: 'qwen',
        answer_text: 'Evidence-backed answer [test:1]',
      }),
    );
  });
  await new Promise<void>((resolve) => agent.listen(0, '127.0.0.1', resolve));
  process.env.AGENT_BASE_URL = `http://127.0.0.1:${(agent.address() as { port: number }).port}`;
  app = await startApp();
}, 30000);
afterAll(async () => {
  if (agent) await new Promise<void>((resolve) => agent.close(() => resolve()));
  if (!app) return;
  for (const id of ids)
    await request(app.getHttpServer()).delete(`/api/conversations/${id}`);
  await app.close();
});
async function create(title = `Integration ${randomUUID()}`) {
  const response = await request(app.getHttpServer())
    .post('/api/conversations')
    .send({ title })
    .expect(201);
  ids.push(response.body.id);
  return response.body as { id: string; title: string; version: number };
}

it('checks PostgreSQL readiness and exposes API documentation', async () => {
  await request(app.getHttpServer())
    .get('/api/health')
    .expect(200)
    .expect(({ body }) => expect(body.database).toBe('up'));
  await request(app.getHttpServer())
    .get('/api/docs-json')
    .expect(200)
    .expect(({ body }) =>
      expect(body.paths['/api/conversations']).toBeDefined(),
    );
});
it('creates, searches, paginates, and renames conversations', async () => {
  const title = `Search ${randomUUID()}`;
  const conversation = await create(title);
  const list = await request(app.getHttpServer())
    .get('/api/conversations')
    .query({ search: title, pageSize: 1 })
    .expect(200);
  expect(list.body.data.map((item: { id: string }) => item.id)).toEqual([
    conversation.id,
  ]);
  expect(list.body.pagination.totalItems).toBe(1);
  const renamed = await request(app.getHttpServer())
    .patch(`/api/conversations/${conversation.id}`)
    .send({ title: '  Renamed  ' })
    .expect(200);
  expect(renamed.body.title).toBe('Renamed');
  expect(renamed.body.version).toBe(1);
});
it('stores ordered turns, handles retries, and rejects conflicting or forged input', async () => {
  const conversation = await create();
  const url = `/api/conversations/${conversation.id}/messages`;
  const input = {
    content: 'Help me plan a vendor review',
    requestId: randomUUID(),
    expectedVersion: 0,
  };
  const first = await request(app.getHttpServer())
    .post(url)
    .send(input)
    .expect(201);
  expect(
    first.body.messages.map((item: { role: string }) => item.role),
  ).toEqual(['user', 'assistant']);
  expect(first.body.mode).toBe('agent');
  const retry = await request(app.getHttpServer())
    .post(url)
    .send(input)
    .expect(201);
  expect(retry.body.messages.map((item: { id: string }) => item.id)).toEqual(
    first.body.messages.map((item: { id: string }) => item.id),
  );
  await request(app.getHttpServer())
    .post(url)
    .send({ ...input, content: 'Changed content' })
    .expect(409);
  await request(app.getHttpServer())
    .post(url)
    .send({ ...input, requestId: randomUUID() })
    .expect(409);
  await request(app.getHttpServer())
    .post(url)
    .send({
      ...input,
      expectedVersion: 1,
      requestId: randomUUID(),
      role: 'assistant',
    })
    .expect(400);
  const list = await request(app.getHttpServer())
    .get(url)
    .query({ pageSize: 1, page: 2 })
    .expect(200);
  expect(list.body.pagination.totalItems).toBe(2);
  expect(list.body.data[0].role).toBe('assistant');
});
it('forwards both saved roles on a follow-up in the same conversation', async () => {
  const chat = await create();
  const url = `/api/conversations/${chat.id}/messages`;
  await request(app.getHttpServer())
    .post(url)
    .send({
      content: 'Regional leadership assignments',
      expectedVersion: 0,
      requestId: randomUUID(),
    })
    .expect(201);
  await request(app.getHttpServer())
    .post(url)
    .send({
      content: 'share their email',
      expectedVersion: 1,
      requestId: randomUUID(),
    })
    .expect(201);
  expect(agentRequests.at(-1)).toMatchObject({
    conversation_id: chat.id,
    history: [
      { role: 'user', content: 'Regional leadership assignments' },
      { role: 'assistant', content: 'Evidence-backed answer [test:1]' },
    ],
  });
});
it('allows only one writer for a shared conversation version', async () => {
  const conversation = await create();
  const url = `/api/conversations/${conversation.id}/messages`;
  const results = await Promise.all(
    ['First', 'Second'].map((content) =>
      request(app.getHttpServer())
        .post(url)
        .send({ content, requestId: randomUUID(), expectedVersion: 0 }),
    ),
  );
  expect(results.map((result) => result.status).sort((a, b) => a - b)).toEqual([
    201, 409,
  ]);
  const messages = await request(app.getHttpServer()).get(url).expect(200);
  expect(messages.body.pagination.totalItems).toBe(2);
});
it('persists across application restart and cascades deletion to messages', async () => {
  const conversation = await create();
  await request(app.getHttpServer())
    .post(`/api/conversations/${conversation.id}/messages`)
    .send({
      content: 'Persistent message',
      requestId: randomUUID(),
      expectedVersion: 0,
    })
    .expect(201);
  await app.close();
  app = await startApp();
  const saved = await request(app.getHttpServer())
    .get(`/api/conversations/${conversation.id}/messages`)
    .expect(200);
  expect(saved.body.data[0].content).toBe('Persistent message');
  await request(app.getHttpServer())
    .delete(`/api/conversations/${conversation.id}`)
    .expect(204);
  await request(app.getHttpServer())
    .get(`/api/conversations/${conversation.id}`)
    .expect(404);
  expect(
    await app
      .get(DataSource)
      .query('SELECT id FROM messages WHERE conversation_id = $1', [
        conversation.id,
      ]),
  ).toEqual([]);
  await request(app.getHttpServer())
    .delete(`/api/conversations/${conversation.id}`)
    .expect(204);
});
it('validates IDs, bodies, and pagination without leaking internal errors', async () => {
  await request(app.getHttpServer())
    .get('/api/conversations/not-a-uuid')
    .expect(400);
  await request(app.getHttpServer())
    .post('/api/conversations')
    .send({ title: null })
    .expect(400);
  await request(app.getHttpServer())
    .post('/api/conversations')
    .send({ title: ' ' })
    .expect(400);
  await request(app.getHttpServer())
    .get('/api/conversations?pageSize=101')
    .expect(400);
  const missing = await request(app.getHttpServer())
    .get(`/api/conversations/${randomUUID()}`)
    .expect(404);
  expect(missing.body.error.code).toBe('NOT_FOUND');
  expect(missing.body.stack).toBeUndefined();
});

it('rejects malformed JSON and oversized request bodies', async () => {
  await request(app.getHttpServer())
    .post('/api/conversations')
    .set('Content-Type', 'application/json')
    .send('{bad')
    .expect(400);
  await request(app.getHttpServer())
    .post('/api/conversations')
    .send({ title: 'a'.repeat(70000) })
    .expect(413);
});
it('limits message-generation requests', async () => {
  const url = `/api/conversations/${randomUUID()}/messages`;
  const statuses: number[] = [];
  for (let i = 0; i < 21; i++) {
    const response = await request(app.getHttpServer()).post(url).send({
      content: 'Rate-limit test',
      requestId: randomUUID(),
      expectedVersion: 0,
    });
    statuses.push(response.status);
  }
  expect(statuses.at(-1)).toBe(429);
});

describe('attachments', () => {
  beforeAll(async () => {
    await app.close();
    app = await startApp();
  });
  const csv = Buffer.from('metric,value\nRevenue,42\n');
  const pdf = Buffer.from(
    '%PDF-1.7\n' + 'Synthetic PDF fixture\n'.repeat(4) + '%%EOF',
  );

  it('stores multipart files, returns metadata, and downloads the original bytes', async () => {
    const conversation = await create();
    const sent = await request(app.getHttpServer())
      .post(`/api/conversations/${conversation.id}/messages`)
      .field('content', 'Review the attachments')
      .field('requestId', randomUUID())
      .field('expectedVersion', '0')
      .attach('files', csv, {
        filename: "report's.csv",
        contentType: 'text/plain',
      })
      .attach('files', pdf, {
        filename: 'report.pdf',
        contentType: 'application/pdf',
      })
      .expect(201);
    const files = sent.body.messages[0].attachments;
    expect(files.map((file: { name: string }) => file.name)).toEqual([
      "report's.csv",
      'report.pdf',
    ]);
    expect(files[0].mimeType).toBe('text/csv');
    expect(files[0].data).toBeUndefined();
    expect(files[0].sha256).toBeUndefined();
    expect(sent.body.messages[1].content).toContain('not read or analysed');
    const saved = await request(app.getHttpServer())
      .get(`/api/conversations/${conversation.id}/messages`)
      .expect(200);
    expect(saved.body.data[0].attachments[0].id).toBe(files[0].id);
    const download = await request(app.getHttpServer())
      .get(`/api/conversations/${conversation.id}/attachments/${files[0].id}`)
      .expect(200)
      .expect('Content-Type', /application\/octet-stream/);
    expect(download.body).toEqual(csv);
    expect(download.headers['content-disposition']).toContain('report%27s.csv');
    const other = await create();
    await request(app.getHttpServer())
      .get(`/api/conversations/${other.id}/attachments/${files[0].id}`)
      .expect(404);
  });

  it('deduplicates attachment retries and rejects reused keys with different files', async () => {
    const conversation = await create();
    const requestId = randomUUID();
    const send = (bytes = csv, name = 'report.csv') =>
      request(app.getHttpServer())
        .post(`/api/conversations/${conversation.id}/messages`)
        .field('content', 'Review')
        .field('requestId', requestId)
        .field('expectedVersion', '0')
        .attach('files', bytes, { filename: name });
    const first = await send().expect(201);
    const retry = await send().expect(201);
    expect(retry.body.messages[0].attachments[0].id).toBe(
      first.body.messages[0].attachments[0].id,
    );
    await send(Buffer.from('changed bytes')).expect(409);
    await send(csv, 'renamed.csv').expect(409);
    const result = await app
      .get(DataSource)
      .query('SELECT id FROM attachments WHERE message_id = $1', [
        first.body.messages[0].id,
      ]);
    expect(result).toHaveLength(1);
  });

  it('accepts an attachment-only message', async () => {
    const conversation = await create();
    const result = await request(app.getHttpServer())
      .post(`/api/conversations/${conversation.id}/messages`)
      .field('content', '')
      .field('requestId', randomUUID())
      .field('expectedVersion', '0')
      .attach('files', csv, { filename: 'data.csv' })
      .expect(201);
    expect(result.body.messages[0].content).toBe('Attached files');
    expect(result.body.messages[0].attachments).toHaveLength(1);
  });

  it('rejects invalid file content, excessive counts, and oversized files without saving messages', async () => {
    const conversation = await create();
    const send = () =>
      request(app.getHttpServer())
        .post(`/api/conversations/${conversation.id}/messages`)
        .field('content', 'Check')
        .field('requestId', randomUUID())
        .field('expectedVersion', '0');
    await send()
      .attach('files', Buffer.from('not a PDF'), { filename: 'fake.pdf' })
      .expect(415);
    await send()
      .attach('files', Buffer.from('<html>example</html>'), {
        filename: 'page.html',
      })
      .expect(415);
    await send()
      .attach('files', Buffer.from([0, 255, 3]), { filename: 'binary.txt' })
      .expect(415);
    await send()
      .attach('files', Buffer.alloc(0), { filename: 'empty.txt' })
      .expect(400);
    await send()
      .attach('files', Buffer.alloc(5 * 1024 * 1024 + 1), {
        filename: 'large.txt',
      })
      .expect(413);
    const excessive = send();
    for (let index = 0; index < 6; index++)
      excessive.attach('files', csv, { filename: `report-${index}.csv` });
    await excessive.expect(400);
    const result = await request(app.getHttpServer())
      .get(`/api/conversations/${conversation.id}/messages`)
      .expect(200);
    expect(result.body.pagination.totalItems).toBe(0);
  });

  it('preserves files across application restart and deletes them with the conversation', async () => {
    const conversation = await create();
    const sent = await request(app.getHttpServer())
      .post(`/api/conversations/${conversation.id}/messages`)
      .field('content', 'Persist this file')
      .field('requestId', randomUUID())
      .field('expectedVersion', '0')
      .attach('files', csv, { filename: 'persistent.csv' })
      .expect(201);
    const fileId = sent.body.messages[0].attachments[0].id;
    await app.close();
    app = await startApp();
    const downloaded = await request(app.getHttpServer())
      .get(`/api/conversations/${conversation.id}/attachments/${fileId}`)
      .expect(200);
    expect(downloaded.body).toEqual(csv);
    await request(app.getHttpServer())
      .delete(`/api/conversations/${conversation.id}`)
      .expect(204);
    expect(
      await app
        .get(DataSource)
        .query('SELECT id FROM attachments WHERE id = $1', [fileId]),
    ).toEqual([]);
    await request(app.getHttpServer())
      .get(`/api/conversations/${conversation.id}/attachments/${fileId}`)
      .expect(404);
  });
});
