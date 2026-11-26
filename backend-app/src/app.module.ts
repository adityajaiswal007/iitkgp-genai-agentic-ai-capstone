import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { RateLimitModule } from './rate-limit/rate-limit.module.js';
import { TypeOrmModule } from '@nestjs/typeorm';
import { validateEnvironment } from './config/environment.js';
import { databaseOptions } from './database/data-source.js';
import { ConversationsModule } from './conversations/conversations.module.js';
import { AppController } from './app.controller.js';
@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true, validate: validateEnvironment }),
    TypeOrmModule.forRootAsync({
      useFactory: () => ({
        ...databaseOptions(),
        retryAttempts: 3,
        retryDelay: 1000,
      }),
    }),
    RateLimitModule,
    ConversationsModule,
  ],
  controllers: [AppController],
})
export class AppModule {}
