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

@Injectable()
export class OrchestratorService {
  // The inference mode is owned by the separate agent service.
  readonly mode = 'agent' as const;
  constructor(private readonly config: ConfigService) {}

  async reply(
    conversationId: string,
    content: string,
    attachmentCount = 0,
    history: ConversationMessage[] = [],
  ): Promise<string> {
    const baseUrl = this.config
      .get<string>('AGENT_BASE_URL', 'http://127.0.0.1:8000')
      .replace(/\/$/, '');
    const key = this.config.get<string>('AGENT_API_KEY', '');
    const body = JSON.stringify({
      conversation_id: conversationId,
      question: content,
      history,
    });
    if (Buffer.byteLength(body, 'utf8') > 65536 || history.length > 200)
      throw new BadRequestException(
        'This conversation exceeds the agent context limit. Start a new chat.',
      );
    let response: Response;
    try {
      response = await fetch(`${baseUrl}/v1/executions`, {
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
    const attachments = attachmentCount
      ? '\n\nAttachments were saved but not read or analysed. Answers use the indexed retrieval corpus only.'
      : '';
    return label + result.answer_text + attachments;
  }
}
