import { rateLimitConfig } from './rate-limit.config.js';

it('provides documented rate-limit and Redis defaults', () => {
  expect(rateLimitConfig({})).toMatchObject({
    RATE_LIMIT_API_COUNT: 120,
    RATE_LIMIT_CHAT_COUNT: 20,
    RATE_LIMIT_API_WINDOW_SECONDS: 60,
    RATE_LIMIT_CHAT_WINDOW_SECONDS: 60,
    RATE_LIMIT_BLOCK_SECONDS: 60,
    REDIS_URL: 'redis://127.0.0.1:6380',
  });
});

it('accepts deployment overrides and rejects unsafe configuration', () => {
  expect(
    rateLimitConfig({ RATE_LIMIT_CHAT_COUNT: '5' }).RATE_LIMIT_CHAT_COUNT,
  ).toBe(5);
  for (const value of ['0', '-1', '1.5', 'NaN', '', '1e3']) {
    expect(() => rateLimitConfig({ RATE_LIMIT_API_COUNT: value })).toThrow(
      'RATE_LIMIT_API_COUNT',
    );
  }
  expect(() => rateLimitConfig({ RATE_LIMIT_BLOCK_SECONDS: '86401' })).toThrow(
    'RATE_LIMIT_BLOCK_SECONDS',
  );
  expect(() =>
    rateLimitConfig({ REDIS_URL: 'https://redis.example.com' }),
  ).toThrow('REDIS_URL');
  expect(() => rateLimitConfig({ RATE_LIMIT_KEY_PREFIX: '' })).toThrow(
    'RATE_LIMIT_KEY_PREFIX',
  );
});
