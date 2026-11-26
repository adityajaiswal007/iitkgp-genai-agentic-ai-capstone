import { budgetDay } from './usage.service.js';
import { validateEnvironment } from '../config/environment.js';

it('resets the budget at midnight in the configured timezone', () => {
  expect(budgetDay(new Date('2026-11-15T18:29:59Z'), 'Asia/Kolkata')).toBe(
    '2026-11-15',
  );
  expect(budgetDay(new Date('2026-11-15T18:30:00Z'), 'Asia/Kolkata')).toBe(
    '2026-11-16',
  );
  expect(budgetDay(new Date('2026-11-15T18:30:00Z'), 'UTC')).toBe('2026-11-15');
});
it('rejects invalid daily limits and timezones at startup', () => {
  const env = { DB_USER: 'test', DB_PASSWORD: 'test', DB_NAME: 'test' };
  for (const limit of ['0', '-1', '1.5', 'abc'])
    expect(() =>
      validateEnvironment({ ...env, USER_DAILY_TOKEN_LIMIT: limit }),
    ).toThrow();
  expect(() =>
    validateEnvironment({ ...env, TOKEN_BUDGET_TIMEZONE: 'invalid' }),
  ).toThrow();
});
