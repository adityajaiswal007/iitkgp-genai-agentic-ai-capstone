import 'reflect-metadata';
import 'dotenv/config';
import { DataSource, type DataSourceOptions } from 'typeorm';
import { validateEnvironment } from '../config/environment.js';
import { Attachment } from './attachment.entity.js';
import { AddAttachments1793894400000 } from './migrations/1793894400000-add-attachments.js';
import { Conversation } from './conversation.entity.js';
import { Message } from './message.entity.js';
import { CreateChatTables1793817600000 } from './migrations/1793817600000-create-chat-tables.js';

export function databaseOptions(env = process.env): DataSourceOptions {
  const config = validateEnvironment(env);
  return {
    type: 'postgres',
    host: config.DB_HOST,
    port: config.DB_PORT,
    username: config.DB_USER,
    password: config.DB_PASSWORD,
    database: config.DB_NAME,
    ssl: config.DB_SSL ? { rejectUnauthorized: true } : false,
    entities: [Conversation, Message, Attachment],
    migrations: [CreateChatTables1793817600000, AddAttachments1793894400000],
    synchronize: false,
    migrationsRun: false,
    logging: false,
    extra: { max: 10, connectionTimeoutMillis: 5000, statement_timeout: 10000 },
  };
}
export default new DataSource(databaseOptions());
