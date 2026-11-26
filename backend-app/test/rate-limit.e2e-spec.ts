import 'dotenv/config';
import 'reflect-metadata';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import {
  Controller,
  Get,
  Post,
  SetMetadata,
  type INestApplication,
} from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { Test } from '@nestjs/testing';
import { SkipThrottle } from '@nestjs/throttler';
import request from 'supertest';

let handledMessages = 0;
@Controller()
class Routes {
  @Get('a') a() {
    return { ok: true };
  }
  @Get('b') b() {
    return { ok: true };
  }
  @Post('conversations/:id/messages')
  @SetMetadata('copilot:chat-rate-limit', true)
  send() {
    handledMessages += 1;
    return { ok: true };
  }
  @Get('health')
  @SkipThrottle({ api: true, chat: true })
  health() {
    return { ok: true };
  }
}

const apps: INestApplication[] = [];
async function start(prefix: string, overrides: Record<string, string> = {}) {
  // Use emitted Nest metadata, just like the existing application integration suite.
  const { RateLimitModule } =
    await import('../dist/rate-limit/rate-limit.module.js');
  const { rateLimitConfig } =
    await import('../dist/config/rate-limit.config.js');
  const { configureApp } = await import('../dist/configure-app.js');
  const module = await Test.createTestingModule({
    imports: [
      ConfigModule.forRoot({
        isGlobal: true,
        ignoreEnvFile: true,
        load: [
          () => ({
            ...rateLimitConfig({
              ...process.env,
              RATE_LIMIT_KEY_PREFIX: prefix,
              RATE_LIMIT_API_COUNT: '4',
              RATE_LIMIT_CHAT_COUNT: '2',
              RATE_LIMIT_BLOCK_SECONDS: '1',
              ...overrides,
            }),
            FRONTEND_ORIGINS: ['http://127.0.0.1:3000'],
          }),
        ],
      }),
      RateLimitModule,
    ],
    controllers: [Routes],
  }).compile();
  const app = module.createNestApplication({ logger: false });
  configureApp(app);
  await app.init();
  apps.push(app);
  return app;
}
afterEach(async () => {
  await Promise.all(apps.splice(0).map((app) => app.close()));
});

it('aggregates requests across routes and instances, survives an API restart, and isolates browser IDs', async () => {
  const prefix = `test:rate-limit:${randomUUID()}`;
  const first = await start(prefix);
  const second = await start(prefix);
  const id = randomUUID();
  for (let i = 0; i < 4; i++)
    await request((i % 2 ? first : second).getHttpServer())
      .get(i % 2 ? '/api/a' : '/api/b')
      .set('X-Client-ID', id)
      .expect(200);
  await first.close();
  apps.splice(apps.indexOf(first), 1);
  const restarted = await start(prefix);
  const blocked = await request(restarted.getHttpServer())
    .get('/api/a')
    .set('X-Client-ID', id.toUpperCase())
    .expect(429);
  expect(blocked.body.error).toMatchObject({
    code: 'RATE_LIMITED',
    retryAfterSeconds: 1,
  });
  expect(blocked.body.error.message).toContain('try again in 1 second');
  expect(blocked.headers['retry-after']).toBe('1');
  await request(second.getHttpServer())
    .get('/api/b')
    .set('X-Client-ID', randomUUID())
    .expect(200);
  await delay(600);
  await request(second.getHttpServer())
    .get('/api/b')
    .set('X-Client-ID', id)
    .expect(429);
  await delay(550);
  await request(second.getHttpServer())
    .get('/api/b')
    .set('X-Client-ID', id)
    .expect(200);
});

it('limits chat across conversations before handler execution while keeping reads available', async () => {
  const app = await start(`test:rate-limit:${randomUUID()}`, {
    RATE_LIMIT_API_COUNT: '100',
  });
  const id = randomUUID();
  const before = handledMessages;
  for (let i = 0; i < 2; i++)
    await request(app.getHttpServer())
      .post(`/api/conversations/${randomUUID()}/messages`)
      .set('X-Client-ID', id)
      .expect(201);
  await request(app.getHttpServer())
    .post(`/api/conversations/${randomUUID()}/messages`)
    .set('X-Client-ID', id)
    .expect(429);
  expect(handledMessages - before).toBe(2);
  await request(app.getHttpServer())
    .get('/api/a')
    .set('X-Client-ID', id)
    .expect(200);
});

it('does not overshoot the quota under concurrent requests', async () => {
  const prefix = `test:rate-limit:${randomUUID()}`;
  const app = await start(prefix);
  const second = await start(prefix);
  const id = randomUUID();
  const results = await Promise.all(
    Array.from({ length: 15 }, (_, index) =>
      request((index % 2 ? app : second).getHttpServer())
        .get('/api/a')
        .set('X-Client-ID', id),
    ),
  );
  expect(results.filter((result) => result.status === 200)).toHaveLength(4);
  expect(results.filter((result) => result.status === 429)).toHaveLength(11);
});

it('resets usage when the configured window expires', async () => {
  const app = await start(`test:rate-limit:${randomUUID()}`, {
    RATE_LIMIT_API_COUNT: '1',
    RATE_LIMIT_API_WINDOW_SECONDS: '1',
  });
  const id = randomUUID();
  await request(app.getHttpServer())
    .get('/api/a')
    .set('X-Client-ID', id)
    .expect(200);
  await delay(1100);
  await request(app.getHttpServer())
    .get('/api/b')
    .set('X-Client-ID', id)
    .expect(200);
});

it('validates identity, exempts health and preflight, and permits the browser header through CORS', async () => {
  const app = await start(`test:rate-limit:${randomUUID()}`);
  await request(app.getHttpServer()).get('/api/a').expect(400);
  await request(app.getHttpServer())
    .get('/api/a')
    .set('X-Client-ID', 'invalid')
    .expect(400);
  await request(app.getHttpServer()).get('/api/health').expect(200);
  const preflight = await request(app.getHttpServer())
    .options('/api/a')
    .set('Origin', 'http://127.0.0.1:3000')
    .set('Access-Control-Request-Method', 'GET')
    .set('Access-Control-Request-Headers', 'x-client-id')
    .expect(204);
  expect(preflight.headers['access-control-allow-headers']).toContain(
    'X-Client-ID',
  );
});

it('rejects protected requests with 503 when Redis is unavailable', async () => {
  const app = await start(`test:rate-limit:${randomUUID()}`);
  const { RedisRateLimitStorage } =
    await import('../dist/rate-limit/redis-rate-limit.storage.js');
  app.get(RedisRateLimitStorage).onModuleDestroy();
  const response = await request(app.getHttpServer())
    .get('/api/a')
    .set('X-Client-ID', randomUUID())
    .expect(503);
  expect(response.body.error.code).toBe('SERVICE_UNAVAILABLE');
});
