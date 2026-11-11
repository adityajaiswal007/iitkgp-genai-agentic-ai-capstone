/** Central defaults. Deployment-specific values belong in .env. All durations are seconds. */
export function rateLimitConfig(env: Record<string, unknown>) {
  function positiveInteger(key: string, fallback: number, maximum: number) {
    const raw = env[key] ?? fallback;
    const value = Number(raw);
    if (
      (typeof raw !== 'string' && typeof raw !== 'number') ||
      !/^\d+$/.test(String(raw)) ||
      !Number.isSafeInteger(value) ||
      value < 1 ||
      value > maximum
    )
      throw new Error(`${key} must be an integer between 1 and ${maximum}`);
    return value;
  }
  const redisUrl = env.REDIS_URL ?? 'redis://127.0.0.1:6380';
  if (typeof redisUrl !== 'string')
    throw new Error('REDIS_URL must be a string');
  try {
    const url = new URL(redisUrl);
    if (
      !['redis:', 'rediss:'].includes(url.protocol) ||
      !url.hostname ||
      url.search ||
      url.hash
    )
      throw new Error();
  } catch {
    throw new Error('REDIS_URL must be a valid redis:// or rediss:// URL');
  }
  const prefix =
    env.RATE_LIMIT_KEY_PREFIX ?? 'operations-copilot:rate-limit:v1';
  if (typeof prefix !== 'string' || !/^[a-zA-Z0-9:_-]{1,100}$/.test(prefix))
    throw new Error(
      'RATE_LIMIT_KEY_PREFIX must contain 1–100 letters, digits, colons, underscores, or hyphens',
    );
  return {
    REDIS_URL: redisUrl,
    RATE_LIMIT_KEY_PREFIX: prefix,
    RATE_LIMIT_API_COUNT: positiveInteger('RATE_LIMIT_API_COUNT', 120, 1000000),
    RATE_LIMIT_API_WINDOW_SECONDS: positiveInteger(
      'RATE_LIMIT_API_WINDOW_SECONDS',
      60,
      86400,
    ),
    RATE_LIMIT_CHAT_COUNT: positiveInteger(
      'RATE_LIMIT_CHAT_COUNT',
      20,
      1000000,
    ),
    RATE_LIMIT_CHAT_WINDOW_SECONDS: positiveInteger(
      'RATE_LIMIT_CHAT_WINDOW_SECONDS',
      60,
      86400,
    ),
    RATE_LIMIT_BLOCK_SECONDS: positiveInteger(
      'RATE_LIMIT_BLOCK_SECONDS',
      60,
      86400,
    ),
  };
}
