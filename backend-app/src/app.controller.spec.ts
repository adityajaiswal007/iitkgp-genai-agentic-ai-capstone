import { validateEnvironment } from './config/environment.js';
const valid = { DB_USER: 'test', DB_PASSWORD: 'test', DB_NAME: 'test' };
describe('configuration boundary', () => {
  it('requires database credentials', () => {
    expect(() => validateEnvironment({})).toThrow('DB_USER');
  });
  it('rejects invalid ports and SSL configuration', () => {
    expect(() => validateEnvironment({ ...valid, DB_PORT: '0' })).toThrow(
      'DB_PORT',
    );
    expect(() => validateEnvironment({ ...valid, DB_SSL: 'maybe' })).toThrow(
      'DB_SSL',
    );
  });
  it('uses loopback defaults and validates CORS origins', () => {
    expect(validateEnvironment(valid).HOST).toBe('127.0.0.1');
    expect(() =>
      validateEnvironment({ ...valid, FRONTEND_ORIGINS: '*' }),
    ).toThrow('FRONTEND_ORIGINS');
  });
});

it('rejects invalid agent URLs and deadlines', () => {
  expect(() =>
    validateEnvironment({ ...valid, AGENT_BASE_URL: 'file:///etc/passwd' }),
  ).toThrow('AGENT_BASE_URL');
  expect(() =>
    validateEnvironment({ ...valid, AGENT_REQUEST_TIMEOUT_MS: 'NaN' }),
  ).toThrow('AGENT_REQUEST_TIMEOUT_MS');
  expect(validateEnvironment(valid).AGENT_REQUEST_TIMEOUT_MS).toBe(130000);
});
