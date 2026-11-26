import {
  Injectable,
  Logger,
  ServiceUnavailableException,
  type OnModuleInit,
  type OnModuleDestroy,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { ThrottlerStorage } from '@nestjs/throttler';
import { createClient } from 'redis';

// One atomic operation per policy. Redis time avoids clock skew between API instances.
// Fixed windows start with the first request. Blocked retries never extend the cooldown.
const INCREMENT = `
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'count', 'resetAt', 'blockedUntil')
local count = tonumber(state[1]) or 0
local resetAt = tonumber(state[2]) or 0
local blockedUntil = tonumber(state[3]) or 0
if blockedUntil > now then
  return {count, math.max(0, math.ceil((resetAt - now) / 1000)), 1, math.ceil((blockedUntil - now) / 1000)}
end
if blockedUntil > 0 or resetAt <= now then
  count = 0
  resetAt = now + tonumber(ARGV[1])
  blockedUntil = 0
end
count = count + 1
if count > tonumber(ARGV[2]) then blockedUntil = now + tonumber(ARGV[3]) end
redis.call('HSET', KEYS[1], 'count', count, 'resetAt', resetAt, 'blockedUntil', blockedUntil)
redis.call('PEXPIRE', KEYS[1], math.max(resetAt, blockedUntil) - now)
return {count, math.ceil((resetAt - now) / 1000), blockedUntil > now and 1 or 0, math.max(0, math.ceil((blockedUntil - now) / 1000))}
`;

@Injectable()
export class RedisRateLimitStorage
  implements ThrottlerStorage, OnModuleInit, OnModuleDestroy
{
  private readonly logger = new Logger(RedisRateLimitStorage.name);
  private readonly client: ReturnType<typeof createClient>;
  private readonly prefix: string;
  private initialization?: Promise<void>;

  constructor(config: ConfigService) {
    this.prefix = config.getOrThrow<string>('RATE_LIMIT_KEY_PREFIX');
    this.client = createClient({
      url: config.getOrThrow<string>('REDIS_URL'),
      disableOfflineQueue: true,
      socket: {
        connectTimeout: 2000,
        reconnectStrategy: (retries) => Math.min(100 * (retries + 1), 2000),
      },
    });
    this.client.on('error', () =>
      this.logger.warn('Rate-limit Redis connection unavailable.'),
    );
  }

  onModuleInit() {
    // The same instance is exported to the app and provided to ThrottlerModule.
    return (this.initialization ??= this.connect());
  }

  private async connect() {
    const deadline = setTimeout(() => {
      if (this.client.isOpen) this.client.destroy();
    }, 5000);
    try {
      await this.client.connect();
    } catch {
      throw new Error(
        'Cannot connect to rate-limit Redis. Check REDIS_URL and Redis availability.',
      );
    } finally {
      clearTimeout(deadline);
    }
  }

  async ping() {
    if (!this.client.isReady) throw new ServiceUnavailableException();
    await this.client.withCommandOptions({ timeout: 2000 }).ping();
  }

  async increment(
    key: string,
    ttl: number,
    limit: number,
    blockDuration: number,
  ) {
    try {
      if (!this.client.isReady) throw new Error('Redis unavailable');
      const result = (await this.client
        .withCommandOptions({ timeout: 2000 })
        .eval(INCREMENT, {
          keys: [`${this.prefix}:${key}`],
          arguments: [String(ttl), String(limit), String(blockDuration)],
        })) as number[];
      return {
        totalHits: result[0],
        timeToExpire: result[1],
        isBlocked: result[2] === 1,
        timeToBlockExpire: result[3],
      };
    } catch {
      throw new ServiceUnavailableException(
        'Rate limiting is temporarily unavailable. Please try again shortly.',
      );
    }
  }

  onModuleDestroy() {
    if (this.client.isOpen) this.client.destroy();
  }
}
