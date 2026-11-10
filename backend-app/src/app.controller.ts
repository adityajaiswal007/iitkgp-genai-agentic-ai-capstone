import { Controller, Get, ServiceUnavailableException } from '@nestjs/common';
import { ApiTags } from '@nestjs/swagger';
import { SkipThrottle } from '@nestjs/throttler';
import { DataSource } from 'typeorm';
@ApiTags('health')
@Controller('health')
export class AppController {
  constructor(private readonly db: DataSource) {}
  @Get()
  @SkipThrottle()
  async health() {
    try {
      await this.db.query('SELECT 1');
    } catch {
      throw new ServiceUnavailableException();
    }
    return { status: 'ok', database: 'up', orchestrator: 'agent' };
  }
}
