export function validateEnvironment(env: Record<string, unknown>) {
  function text(key: string, fallback?: string) {
    const value = env[key] ?? fallback;
    if (typeof value !== 'string' || !value.trim())
      throw new Error(`${key} must be set`);
    return value;
  }
  function port(key: string, fallback: string) {
    const value = text(key, fallback);
    if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535)
      throw new Error(`${key} must be a valid port`);
    return Number(value);
  }
  const ssl = text('DB_SSL', 'false');
  if (!['true', 'false'].includes(ssl))
    throw new Error('DB_SSL must be true or false');
  const origins = text(
    'FRONTEND_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000',
  )
    .split(',')
    .map((origin) => origin.trim());
  if (
    origins.some((origin) => {
      try {
        const url = new URL(origin);
        return (
          !['http:', 'https:'].includes(url.protocol) || url.origin !== origin
        );
      } catch {
        return true;
      }
    })
  )
    throw new Error('FRONTEND_ORIGINS must contain exact HTTP origins');
  return {
    ...env,
    HOST: text('HOST', '127.0.0.1'),
    PORT: port('PORT', '3001'),
    DB_HOST: text('DB_HOST', '127.0.0.1'),
    DB_PORT: port('DB_PORT', '5433'),
    DB_USER: text('DB_USER'),
    DB_PASSWORD: text('DB_PASSWORD'),
    DB_NAME: text('DB_NAME'),
    DB_SSL: ssl === 'true',
    FRONTEND_ORIGINS: origins,
  };
}
