import { ValidationPipe, type INestApplication } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { DocumentBuilder, SwaggerModule } from '@nestjs/swagger';
import { json } from 'express';
import helmet from 'helmet';
import { ApiExceptionFilter } from './common/api-exception.filter.js';

export function configureApp(app: INestApplication) {
  const config = app.get(ConfigService);
  app.setGlobalPrefix('api');
  app.use(helmet());
  app.use(json({ limit: '64kb' }));
  app.enableCors({
    origin: config.get<string[]>('FRONTEND_ORIGINS'),
    methods: ['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'X-Client-ID'],
    exposedHeaders: [
      'Retry-After',
      'X-RateLimit-Limit-api',
      'X-RateLimit-Remaining-api',
      'X-RateLimit-Reset-api',
      'X-RateLimit-Limit-chat',
      'X-RateLimit-Remaining-chat',
      'X-RateLimit-Reset-chat',
    ],
  });
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      validationError: { target: false, value: false },
    }),
  );
  app.useGlobalFilters(new ApiExceptionFilter());
  SwaggerModule.setup('api/docs', app, () =>
    SwaggerModule.createDocument(
      app,
      new DocumentBuilder()
        .setTitle('Operations Copilot API')
        .setDescription(
          'Local single-workspace conversation API backed by PostgreSQL. Queries are forwarded to the agent-orchestrator service.',
        )
        .setVersion('1.0')
        .build(),
    ),
  );
}
