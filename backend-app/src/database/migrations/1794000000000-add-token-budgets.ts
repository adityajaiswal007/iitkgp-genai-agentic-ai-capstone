import type { MigrationInterface, QueryRunner } from 'typeorm';
export class AddTokenBudgets1794000000000 implements MigrationInterface {
  async up(runner: QueryRunner) {
    await runner.query(`CREATE TABLE token_executions (
      client_id uuid NOT NULL, conversation_id uuid NOT NULL, request_id uuid NOT NULL,
      budget_day date NOT NULL, charged integer NOT NULL CHECK (charged >= 0),
      estimated integer NOT NULL CHECK (estimated >= 0),
      status text NOT NULL CHECK (status IN ('pending', 'settled', 'uncertain')),
      created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (conversation_id, request_id)
    )`);
    // Deleting a conversation must not refund spending.
    await runner.query(
      'CREATE INDEX token_executions_client_day ON token_executions (client_id, budget_day)',
    );
  }
  async down(runner: QueryRunner) {
    await runner.query('DROP TABLE token_executions');
  }
}
