import { ConfigService } from '@nestjs/config';
import {
  BadGatewayException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { createServer, type Server } from 'node:http';
import { OrchestratorService } from './orchestrator.service.js';

let server: Server;
let baseUrl: string;
let status = 200;
let response: unknown;
let received: { path?: string; key?: string; body?: unknown };
beforeAll(async () => {
  server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    received = {
      path: req.url,
      key: req.headers['x-api-key'] as string,
      body: JSON.parse(body),
    };
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(response));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as { port: number };
  baseUrl = `http://127.0.0.1:${address.port}`;
});
afterAll(
  () =>
    new Promise<void>((resolve, reject) =>
      server.close((err) => (err ? reject(err) : resolve())),
    ),
);
beforeEach(() => {
  status = 200;
  response = {
    answer_text: 'Valve-B has a limit of 12 bar. [BOM:1]',
    mode: 'qwen',
  };
});
function service() {
  return new OrchestratorService(
    new ConfigService({
      AGENT_BASE_URL: baseUrl,
      AGENT_API_KEY: 'service-test-key',
    }),
  );
}
it('executes a real HTTP query with a stable conversation ID and preserves citations', async () => {
  const result = await service().reply(
    'conversation-1',
    'What is the pressure limit?',
  );
  expect(result).toBe('Valve-B has a limit of 12 bar. [BOM:1]');
  expect(received).toEqual({
    path: '/v1/executions',
    key: 'service-test-key',
    body: {
      conversation_id: 'conversation-1',
      question: 'What is the pressure limit?',
      history: [],
    },
  });
  expect(service().mode).toBe('agent');
});
it('forwards attachment bytes to the attachment execution API', async () => {
  const bytes = Buffer.from('metric,value\nRevenue,8472\n');
  const result = await service().reply('conversation-1', 'What is revenue?', [
    { name: 'report.csv', mimeType: 'text/csv', data: bytes },
  ]);
  expect(received.path).toBe('/v1/executions/attachments');
  expect((received.body as { attachments: unknown[] }).attachments).toEqual([
    {
      name: 'report.csv',
      mime_type: 'text/csv',
      content_base64: bytes.toString('base64'),
      current: true,
    },
  ]);
  expect(result).toBe('Valve-B has a limit of 12 bar. [BOM:1]');
});
it('reports upstream failures without leaking provider details', async () => {
  status = 500;
  response = { secret: 'provider-internal-detail' };
  await expect(service().reply('conversation-1', 'Pressure?')).rejects.toThrow(
    BadGatewayException,
  );
});
it('reports busy execution as temporarily unavailable', async () => {
  status = 409;
  await expect(service().reply('conversation-1', 'Pressure?')).rejects.toThrow(
    ServiceUnavailableException,
  );
});
it('rejects malformed successful responses', async () => {
  response = { answer_text: '', mode: 'qwen' };
  await expect(service().reply('conversation-1', 'Pressure?')).rejects.toThrow(
    BadGatewayException,
  );
});
it('reports a network failure without falling back to a sample answer', async () => {
  const client = new OrchestratorService(
    new ConfigService({ AGENT_BASE_URL: 'http://127.0.0.1:1' }),
  );
  await expect(client.reply('conversation-1', 'Pressure?')).rejects.toThrow(
    ServiceUnavailableException,
  );
});

it('sends the saved transcript in order alongside the follow-up', async () => {
  const history = [
    { role: 'user' as const, content: 'Regional leadership assignments' },
    {
      role: 'assistant' as const,
      content: 'Sarah Jenkins, Marcus Vance, Kenji Sato',
    },
  ];
  await service().reply('conversation-1', 'share their email', [], history);
  expect((received.body as { history: unknown }).history).toEqual(history);
});

it('rejects oversized transcripts explicitly instead of silently dropping context', async () => {
  await expect(
    service().reply(
      'conversation-1',
      'Follow-up',
      [],
      [{ role: 'assistant', content: '界'.repeat(24000) }],
    ),
  ).rejects.toThrow('exceeds the agent context limit');
});
