import type { MigrationInterface, QueryRunner } from 'typeorm';
export class AddAttachments1793894400000 implements MigrationInterface {
  async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`CREATE TABLE attachments (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
      name varchar(180) NOT NULL,
      mime_type varchar(120) NOT NULL,
      size integer NOT NULL CHECK (size > 0 AND size <= 5242880),
      position integer NOT NULL CHECK (position >= 0 AND position < 5),
      sha256 varchar(64) NOT NULL,
      data bytea NOT NULL CHECK (octet_length(data) = size),
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT attachments_message_position_key UNIQUE (message_id, position)
    )`);
  }
  async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query('DROP TABLE attachments');
  }
}
