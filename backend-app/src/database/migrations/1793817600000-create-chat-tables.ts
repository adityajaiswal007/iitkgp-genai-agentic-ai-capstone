import type { MigrationInterface, QueryRunner } from 'typeorm';

export class CreateChatTables1793817600000 implements MigrationInterface {
  async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`CREATE TABLE conversations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      title varchar(120) NOT NULL CHECK (length(trim(title)) > 0),
      version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    )`);
    await queryRunner.query(
      'CREATE INDEX conversations_updated_at_idx ON conversations (updated_at, id)',
    );
    await queryRunner.query(`CREATE TABLE messages (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      request_id uuid NOT NULL,
      role varchar(16) NOT NULL CHECK (role IN ('user', 'assistant')),
      content text NOT NULL CHECK (length(trim(content)) > 0),
      sequence integer NOT NULL CHECK (sequence >= 0),
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT messages_conversation_sequence_key UNIQUE (conversation_id, sequence),
      CONSTRAINT messages_request_role_key UNIQUE (conversation_id, request_id, role)
    )`);
  }
  async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE messages');
    await queryRunner.query('DROP TABLE conversations');
  }
}
