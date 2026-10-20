/** JSON logs: one object per line, the same format as the payments service. */
type Level = 'INFO' | 'WARN' | 'ERROR'

function write(level: Level, msg: string, fields: Record<string, unknown> = {}): void {
  const entry = { ts: new Date().toISOString(), level, logger: 'notifications', msg, ...fields }
  const line = JSON.stringify(entry)
  if (level === 'INFO') {
    console.log(line)
  } else {
    console.error(line)
  }
}

export const log = {
  info: (msg: string, fields?: Record<string, unknown>) => write('INFO', msg, fields),
  warn: (msg: string, fields?: Record<string, unknown>) => write('WARN', msg, fields),
  error: (msg: string, fields?: Record<string, unknown>) => write('ERROR', msg, fields),
}
