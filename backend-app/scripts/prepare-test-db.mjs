import 'dotenv/config';
import pg from 'pg';
const database = process.env.TEST_DB_NAME ?? 'operations_copilot_test';
if (!/^[a-z][a-z0-9_]*$/.test(database) || database === process.env.DB_NAME) throw new Error('Use a distinct test database name');
const client = new pg.Client({ host: process.env.DB_HOST ?? '127.0.0.1', port: Number(process.env.DB_PORT ?? 5433), user: process.env.DB_USER, password: process.env.DB_PASSWORD, database: process.env.DB_NAME, ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: true } : false });
await client.connect();
try {
  if (!(await client.query('SELECT 1 FROM pg_database WHERE datname = $1', [database])).rowCount) await client.query(`CREATE DATABASE "${database}"`);
  console.log('Dedicated integration-test database is ready.');
} finally { await client.end(); }
