import { Module, SetMetadata } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { APP_GUARD } from '@nestjs/core';
import { ThrottlerModule } from '@nestjs/throttler';
import { BrowserRateLimitGuard } from './browser-rate-limit.guard.js';
import { RedisRateLimitStorage } from './redis-rate-limit.storage.js';

const CHAT_POLICY = 'copilot:chat-rate-limit';
export const ChatRateLimit = () => SetMetadata(CHAT_POLICY, true);

@Module({
  providers: [RedisRateLimitStorage],
  exports: [RedisRateLimitStorage],
})
class RateLimitStorageModule {}

@Module({
  imports: [
    RateLimitStorageModule,
    ThrottlerModule.forRootAsync({
      imports: [RateLimitStorageModule],
      inject: [ConfigService, RedisRateLimitStorage],
      useFactory: (config: ConfigService, storage: RedisRateLimitStorage) => ({
        storage,
        throttlers: [
          {
            name: 'api',
            ttl:
              config.getOrThrow<number>('RATE_LIMIT_API_WINDOW_SECONDS') * 1000,
            limit: config.getOrThrow<number>('RATE_LIMIT_API_COUNT'),
            blockDuration:
              config.getOrThrow<number>('RATE_LIMIT_BLOCK_SECONDS') * 1000,
          },
          {
            name: 'chat',
            ttl:
              config.getOrThrow<number>('RATE_LIMIT_CHAT_WINDOW_SECONDS') *
              1000,
            limit: config.getOrThrow<number>('RATE_LIMIT_CHAT_COUNT'),
            blockDuration:
              config.getOrThrow<number>('RATE_LIMIT_BLOCK_SECONDS') * 1000,
            skipIf: (context) =>
              Reflect.getMetadata(CHAT_POLICY, context.getHandler()) !== true,
          },
        ],
      }),
    }),
  ],
  providers: [{ provide: APP_GUARD, useClass: BrowserRateLimitGuard }],
  exports: [RateLimitStorageModule],
})
export class RateLimitModule {}
