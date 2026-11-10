import {
  ArgumentsHost,
  Catch,
  ExceptionFilter,
  HttpException,
  Logger,
} from '@nestjs/common';
import type { Response } from 'express';

@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger(ApiExceptionFilter.name);
  catch(exception: unknown, host: ArgumentsHost) {
    const response = host.switchToHttp().getResponse<Response>();
    const parserType =
      exception && typeof exception === 'object' && 'type' in exception
        ? exception.type
        : null;
    const status =
      exception instanceof HttpException
        ? exception.getStatus()
        : parserType === 'entity.too.large'
          ? 413
          : parserType === 'entity.parse.failed'
            ? 400
            : 500;
    const body =
      exception instanceof HttpException ? exception.getResponse() : null;
    const details =
      body && typeof body === 'object' && 'message' in body
        ? body.message
        : undefined;
    const message =
      status >= 500
        ? 'The service could not complete this request.'
        : typeof details === 'string'
          ? details
          : exception instanceof HttpException
            ? exception.message
            : 'Request failed';
    const codes: Record<number, string> = {
      400: 'VALIDATION_ERROR',
      404: 'NOT_FOUND',
      409: 'CONFLICT',
      413: 'PAYLOAD_TOO_LARGE',
      415: 'UNSUPPORTED_MEDIA_TYPE',
      429: 'RATE_LIMITED',
      503: 'SERVICE_UNAVAILABLE',
    };
    if (status >= 500)
      this.logger.error(
        'Request failed with a server error; request content omitted.',
      );
    response.status(status).json({
      error: {
        code: codes[status] ?? 'INTERNAL_ERROR',
        message,
        ...(Array.isArray(details) ? { details } : {}),
      },
    });
  }
}
