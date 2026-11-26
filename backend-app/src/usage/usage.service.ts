import { ConflictException, HttpException, Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { DataSource, type EntityManager } from 'typeorm';

export function budgetDay(now: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

@Injectable()
export class UsageService {
  constructor(
    private readonly db: DataSource,
    private readonly config: ConfigService,
  ) {}

  async daily(clientId: string) {
    const timezone = this.config.get<string>(
      'TOKEN_BUDGET_TIMEZONE',
      'Asia/Kolkata',
    );
    const day = budgetDay(new Date(), timezone);
    const limit = this.config.get<number>('USER_DAILY_TOKEN_LIMIT', 10000);
    const [row] = await this.db.query(
      `SELECT COALESCE(sum(charged),0)::int AS used, COALESCE(sum(estimated),0)::int AS estimated
       FROM token_executions WHERE client_id=$1 AND budget_day=$2`,
      [clientId, day],
    );
    return {
      day,
      timezone,
      limit,
      used: row.used as number,
      estimated: row.estimated as number,
      remaining: Math.max(0, limit - row.used),
    };
  }

  async conversations(ids: string[], manager = this.db.manager) {
    if (!ids.length)
      return new Map<
        string,
        { tokensCharged: number; estimatedTokens: number }
      >();
    const rows: {
      id: string;
      tokensCharged: number;
      estimatedTokens: number;
    }[] = await manager.query(
      `SELECT conversation_id AS id, sum(charged)::int AS "tokensCharged", sum(estimated)::int AS "estimatedTokens"
       FROM token_executions WHERE conversation_id=ANY($1::uuid[]) GROUP BY conversation_id`,
      [ids],
    );
    return new Map(rows.map((row) => [row.id, row]));
  }

  async conversation(id: string, manager = this.db.manager) {
    return (
      (await this.conversations([id], manager)).get(id) ?? {
        tokensCharged: null,
        estimatedTokens: null,
      }
    );
  }

  async reserve(clientId: string, conversationId: string, requestId: string) {
    const timezone = this.config.get<string>(
      'TOKEN_BUDGET_TIMEZONE',
      'Asia/Kolkata',
    );
    const limit = this.config.get<number>('USER_DAILY_TOKEN_LIMIT', 10000);
    return this.db.transaction(async (manager) => {
      // Stable order prevents deadlocks; cross-process locks protect all API replicas.
      await manager.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
        [`token-client:${clientId}`],
      );
      await manager.query(
        `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`,
        [`token-chat:${conversationId}`],
      );
      const day = budgetDay(new Date(), timezone);
      const existing = await manager.query(
        `SELECT 1 FROM token_executions WHERE conversation_id=$1 AND
         (request_id=$2 OR (status='pending' AND created_at > now() - interval '10 minutes'))`,
        [conversationId, requestId],
      );
      if (existing.length)
        throw new ConflictException(
          'This request is running or its outcome is already recorded. Reload the conversation before retrying.',
        );
      const [row] = await manager.query(
        `SELECT COALESCE(sum(charged),0)::int AS used FROM token_executions WHERE client_id=$1 AND budget_day=$2`,
        [clientId, day],
      );
      const remaining = Math.max(0, limit - row.used);
      if (!remaining)
        throw new HttpException(
          {
            message: `Daily token allowance reached. It resets at midnight (${timezone}).`,
            code: 'TOKEN_BUDGET_EXCEEDED',
          },
          429,
        );
      await manager.query(
        `INSERT INTO token_executions
        (client_id, conversation_id, request_id, budget_day, charged, estimated, status)
        VALUES ($1,$2,$3,$4,$5,$5,'pending')`,
        [clientId, conversationId, requestId, day, remaining],
      );
      return remaining;
    });
  }

  async release(conversationId: string, requestId: string) {
    // Only use when the agent is known not to have started any model calls.
    await this.db.query(
      `DELETE FROM token_executions WHERE conversation_id=$1 AND request_id=$2 AND status='pending'`,
      [conversationId, requestId],
    );
  }

  async settle(
    conversationId: string,
    requestId: string,
    usage?: { tokensCharged: number; estimatedTokens: number },
    manager: EntityManager = this.db.manager,
  ) {
    // Unknown outcomes retain the entire reservation; never refund a possibly paid call.
    await manager.query(
      usage
        ? `UPDATE token_executions SET charged=$3, estimated=$4, status='settled' WHERE conversation_id=$1 AND request_id=$2 AND status='pending'`
        : `UPDATE token_executions SET status='uncertain' WHERE conversation_id=$1 AND request_id=$2 AND status='pending'`,
      usage
        ? [
            conversationId,
            requestId,
            usage.tokensCharged,
            usage.estimatedTokens,
          ]
        : [conversationId, requestId],
    );
  }
}
