import { appendFileSync, mkdirSync } from 'node:fs';

const dir = 'D:/秘书智能体/output/hooks';
const out = dir + '/hook-log.jsonl';
mkdirSync(dir, { recursive: true });

let raw = '';
for await (const chunk of process.stdin) raw += chunk;

const rec = { at: new Date().toISOString(), arg: process.argv[2] ?? '', bytes: raw.length };
try {
  const p = JSON.parse(raw || '{}');
  rec.hook_event_name = p.hook_event_name ?? p.event ?? null;
  rec.tool = p.tool_name ?? null;
  rec.session = p.session_id ?? null;
  rec.cwd = p.cwd ?? null;
  rec.keys = Object.keys(p).slice(0, 20);
  if (p.tool_input && typeof p.tool_input === 'object') {
    rec.inputKeys = Object.keys(p.tool_input).slice(0, 12);
    rec.input = JSON.stringify(p.tool_input).slice(0, 300);
  }
} catch (err) {
  rec.parseError = String(err);
  rec.head = raw.slice(0, 500);
}
appendFileSync(out, JSON.stringify(rec) + '\n');
process.exit(0);
