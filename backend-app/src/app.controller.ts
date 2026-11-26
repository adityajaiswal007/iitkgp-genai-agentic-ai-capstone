import { Controller, Get, ServiceUnavailableException } from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { SkipThrottle } from '@nestjs/throttler';
import { DataSource } from 'typeorm';
import { RedisRateLimitStorage } from './rate-limit/redis-rate-limit.storage.js';
@ApiTags('health')
@Controller('health')
export class AppController {
  constructor(
    private readonly db: DataSource,
    private readonly redis: RedisRateLimitStorage,
  ) {}
  @Get()
  @SkipThrottle({ api: true, chat: true })
  async health() {
    try {
      await this.db.query('SELECT 1');
      await this.redis.ping();
    } catch {
      throw new ServiceUnavailableException();
    }
    return { status: 'ok', database: 'up', redis: 'up', orchestrator: 'agent' };
  }
}
