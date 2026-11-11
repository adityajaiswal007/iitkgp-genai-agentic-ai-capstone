import {
  BadRequestException,
  HttpException,
  Injectable,
  type ExecutionContext,
} from '@nestjs/common';
import { ThrottlerGuard, type ThrottlerLimitDetail } from '@nestjs/throttler';
import type { Request, Response } from 'express';

@Injectable()
export class BrowserRateLimitGuard extends ThrottlerGuard {
  protected override async getTracker(req: Request) {
    const id = req.headers['x-client-id'];
    if (
      typeof id !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        id,
      )
    )
      throw new BadRequestException(
        'X-Client-ID must contain a valid browser UUID v4.',
      );
    return id.toLowerCase();
  }

  protected override generateKey(
    _context: ExecutionContext,
    tracker: string,
    name: string,
  ) {
    // Deliberately omit route/conversation IDs so switching endpoints cannot reset usage.
    return `${tracker}:${name}`;
  }

  protected override async throwThrottlingException(
    context: ExecutionContext,
    detail: ThrottlerLimitDetail,
  ): Promise<void> {
    const retryAfterSeconds = Math.max(1, detail.timeToBlockExpire);
    context
      .switchToHttp()
      .getResponse<Response>()
      .setHeader('Retry-After', retryAfterSeconds);
    throw new HttpException(
      {
        message: `Too many requests. Please try again in ${retryAfterSeconds} second${retryAfterSeconds === 1 ? '' : 's'}.`,
        retryAfterSeconds,
      },
      429,
    );
  }
}
