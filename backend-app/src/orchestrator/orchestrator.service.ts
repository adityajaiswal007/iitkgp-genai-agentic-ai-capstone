import {
  BadGatewayException,
  BadRequestException,
  Injectable,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

export type ConversationMessage = {
  role: 'user' | 'assistant';
  content: string;
};

export type AgentAttachment = {
  name: string;
  mimeType: string;
  data: Buffer;
  current?: boolean;
};

@Injectable()
export class OrchestratorService {
  // The inference mode is owned by the separate agent service.
  readonly mode = 'agent' as const;
  constructor(private readonly config: ConfigService) {}

  async reply(
    conversationId: string,
    content: string,
    attachments: AgentAttachment[] = [],
    history: ConversationMessage[] = [],
  ): Promise<string> {
    const baseUrl = this.config
      .get<string>('AGENT_BASE_URL', 'http://127.0.0.1:8000')
      .replace(/\/$/, '');
    const key = this.config.get<string>('AGENT_API_KEY', '');
    const metadata = {
      conversation_id: conversationId,
      question: content,
      history,
    };
    if (
      Buffer.byteLength(JSON.stringify(metadata), 'utf8') > 65536 ||
      history.length > 200
    )
      throw new BadRequestException(
        'This conversation exceeds the agent context limit. Start a new chat.',
      );
    if (
      attachments.length > 20 ||
      attachments.some((file) => file.data.length > 5 * 1024 * 1024) ||
      attachments.reduce((sum, file) => sum + file.data.length, 0) >
        25 * 1024 * 1024
    )
      throw new BadRequestException(
        'Conversation attachment limit exceeded: up to 20 files, 5 MB each and 25 MB total.',
      );
    const body = JSON.stringify({
      ...metadata,
      ...(attachments.length
        ? {
            attachments: attachments.map((file) => ({
              name: file.name,
              mime_type: file.mimeType,
              content_base64: file.data.toString('base64'),
              current: file.current ?? true,
            })),
          }
        : {}),
    });
    const endpoint = attachments.length
      ? '/v1/executions/attachments'
      : '/v1/executions';
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(key ? { 'X-API-Key': key } : {}),
        },
        body,
        signal: AbortSignal.timeout(
          this.config.get<number>('AGENT_REQUEST_TIMEOUT_MS', 130000),
        ),
      });
    } catch {
      throw new ServiceUnavailableException(
        'Agent service is unavailable or timed out.',
      );
    }
    if ([409, 429, 503].includes(response.status))
      throw new ServiceUnavailableException(
        'Agent service is busy. Retry shortly.',
      );
    if (attachments.length && [413, 422].includes(response.status)) {
      const detail = (await response.json().catch(() => null)) as {
        error?: { message?: string };
      } | null;
      throw new BadRequestException(
        detail?.error?.message ?? 'The attachment could not be processed.',
      );
    }
    if (!response.ok) throw new BadGatewayException('Agent execution failed.');
    let result: unknown;
    try {
      result = await response.json();
    } catch {
      throw new BadGatewayException('Invalid agent response.');
    }
    if (
      !result ||
      typeof result !== 'object' ||
      !('answer_text' in result) ||
      typeof result.answer_text !== 'string' ||
      !result.answer_text.trim() ||
      !('mode' in result) ||
      !['demo', 'qwen'].includes(String(result.mode))
    )
      throw new BadGatewayException('Invalid agent response.');
    const label =
      result.mode === 'demo' ? 'Demo inference (agent service).\n\n' : '';
    return label + result.answer_text;
  }
}
