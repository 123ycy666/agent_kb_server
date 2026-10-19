// guard.mjs —— 统一守卫：按事件分发，避免挂多个 hook 叠加延迟
//   PreToolUse  : read → 重复读取拦截(P4)；write/edit → 计划门(P1) + 新建文件询问(P3)
//   PostToolUse : write/edit → 根因提醒(P6)
//   UserPromptSubmit : 目标/验收/进度重锚(P5/P2)
// 所有策略在 <项目>/.dsh/guard.json 可配；缺省用这里的默认值。
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const STATE_DIR = 'D:/秘书智能体/output/hooks/state';

function readStdin() {
  return new Promise((resolve) => {
    let raw = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => (raw += c));
    process.stdin.on('end', () => resolve(raw));
    process.stdin.on('error', () => resolve(''));
    setTimeout(() => resolve(raw), 4000); // 兜底：别让 hook 挂死
  });
}

const DEFAULTS = {
  planDir: 'docs/plans',
  maxPlanAgeHours: 24,
  gateGlobs: ['*.py', '*.ts', '*.tsx', '*.js', '*.mjs', '*.vue', '*.sql', '*.ps1', '*.sh', '*.bat', '*.yml', '*.yaml', '*.json'],
  exemptGlobs: ['docs/*', 'output/*', 'hooks/*', '.dsh/*', '*.md'],
  planGlobs: ['*.md'],
  readRepeatLimit: 3,
  readDedupe: true,
  newFileAsk: true,
  reanchor: false, // 2026-10-19 关闭：additionalContext 每步重发，600 字×N 步 = 反向浪费
  rootCauseHintOnce: true,
  repeatEditLimit: 3, // 同一文件第 3 次 edit 且计划无"根因"段 → deny（0 = 关闭）
};

function loadConfig(cwd) {
  const cfg = { ...DEFAULTS };
  for (const p of [join(cwd, '.dsh', 'guard.json'), join(cwd, 'guard.json')]) {
    if (existsSync(p)) {
      try { Object.assign(cfg, JSON.parse(readFileSync(p, 'utf8'))); } catch {}
      break;
    }
  }
  return cfg;
}

const globHit = (globs, p) => (Array.isArray(globs) ? globs : [globs]).some((g) => {
  const re = new RegExp('^' + String(g).replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$', 'i');
  return re.test(p);
});

function normPath(p, cwd) {
  if (!p) return '';
  let abs = p.replace(/\\/g, '/');
  if (!/^[a-z]:\//i.test(abs) && !abs.startsWith('/')) abs = cwd.replace(/\\/g, '/') + '/' + abs;
  abs = abs.replace(/\/{2,}/g, '/');
  const base = cwd.replace(/\\/g, '/').replace(/\/+$/, '');
  const rel = abs.toLowerCase().startsWith(base.toLowerCase() + '/') ? abs.slice(base.length + 1) : abs;
  return { abs, rel };
}

function stateFile(session) {
  mkdirSync(STATE_DIR, { recursive: true });
  return join(STATE_DIR, `${String(session || 'unknown').replace(/[^\w.-]/g, '_')}.json`);
}
function loadState(session) {
  try { return JSON.parse(readFileSync(stateFile(session), 'utf8')); } catch { return {}; }
}
function saveState(session, s) {
  try { writeFileSync(stateFile(session), JSON.stringify(s)); } catch {}
}

function freshPlan(cwd, cfg) {
  const dir = join(cwd, cfg.planDir);
  if (!existsSync(dir)) return null;
  const cutoff = Date.now() - Number(cfg.maxPlanAgeHours) * 3600 * 1000;
  let best = null;
  for (const name of readdirSync(dir)) {
    if (!globHit(cfg.planGlobs, name)) continue;
    const st = statSync(join(dir, name));
    if (st.mtimeMs > cutoff && (!best || st.mtimeMs > best.mtimeMs)) best = { name, path: join(dir, name), mtimeMs: st.mtimeMs };
  }
  return best;
}

function emit(obj) { process.stdout.write(JSON.stringify(obj)); }

function deny(evt, reason) {
  emit({ hookSpecificOutput: { hookEventName: evt, permissionDecision: 'deny', permissionDecisionReason: reason } });
}
function ask(evt, reason) {
  emit({ hookSpecificOutput: { hookEventName: evt, permissionDecision: 'ask', permissionDecisionReason: reason } });
}
function context(evt, text) {
  emit({ hookSpecificOutput: { hookEventName: evt, additionalContext: text } });
}

function targetOf(input) {
  if (!input || typeof input !== 'object') return '';
  for (const k of ['file_path', 'path', 'filePath', 'target_file']) {
    if (input[k]) return String(input[k]);
  }
  return '';
}

const raw = await readStdin();
let p = {};
try { p = JSON.parse(raw); } catch { process.exit(0); }

const evt = p.hook_event_name || '';
const tool = String(p.tool_name || '').toLowerCase();
const cwd = p.cwd || process.cwd();
const cfg = loadConfig(cwd);

// ── UserPromptSubmit：目标重锚（P5/P2）────────────────────────────────
if (evt === 'UserPromptSubmit') {
  if (!cfg.reanchor) process.exit(0);
  const plan = freshPlan(cwd, cfg);
  if (!plan) process.exit(0);
  const text = readFileSync(plan.path, 'utf8');
  const lines = text.split(/\r?\n/);
  // 取"目标""验收判据"小节的首行正文（不只是标题）
  const section = (titleRe, max) => {
    const out = [];
    for (let i = 0; i < lines.length && out.length < max; i++) {
      if (titleRe.test(lines[i])) {
        const body = lines.slice(i + 1, i + 4).find((l) => l.trim() && !/^#/.test(l.trim()));
        out.push(lines[i].trim() + (body ? '：' + body.trim() : ''));
      }
    }
    return out;
  };
  const head = [...section(/^##\s*目标/, 1), ...section(/^##\s*验收/, 1)];
  if (!head.length) head.push(...lines.filter((l) => /^#\s/.test(l)).slice(0, 2));
  const progress = lines.filter((l) => /Progress|^\s*-\s*20\d\d-\d\d-\d\d/.test(l)).slice(-3);
  const goalFile = join(cwd, '.dsh', 'goal.txt');
  const goal = existsSync(goalFile) ? readFileSync(goalFile, 'utf8').trim() : '';
  const text2 = [
    `【目标重锚】计划：${plan.name}`,
    goal ? `当前目标：${goal}` : '',
    ...head,
    progress.length ? '最近进度：\n' + progress.join('\n') : '',
    '提醒：按计划的验收判据推进；改动超出计划范围先更新计划。',
  ].filter(Boolean).join('\n');
  context(evt, text2.slice(0, 1800));
  process.exit(0);
}

// ── PostToolUse：根因提醒（P6）───────────────────────────────────────
if (evt === 'PostToolUse') {
  if (tool !== 'write' && tool !== 'edit') process.exit(0);
  const { rel } = normPath(targetOf(p.tool_input), cwd);
  const st = loadState(p.session_id);
  st.edits = st.edits || {};
  const rec = st.edits[rel] || { count: 0, denyHint: 0 };
  rec.count += 1;
  st.edits[rel] = rec;
  saveState(p.session_id, st);
  if (rec.count >= 2) {
    const plan = freshPlan(cwd, cfg);
    const planText = plan ? readFileSync(plan.path, 'utf8') : '';
    if (!/根因|root cause/i.test(planText)) {
      context(evt, `【根因提醒】${rel} 本会话已改 ${rec.count} 次，计划里还没有"根因"段。先写下根因（复现→证据→为什么），再继续打补丁；找不到根因就停下来问，别加特例。`);
    }
  }
  process.exit(0);
}

// ── PreToolUse ──────────────────────────────────────────────────────
if (evt !== 'PreToolUse') process.exit(0);
const target = targetOf(p.tool_input);
if (!target) process.exit(0);
const { abs, rel } = normPath(target, cwd);

// P4：同一文件重复读取
if (tool === 'read') {
  if (!cfg.readDedupe) process.exit(0);
  const st = loadState(p.session_id);
  st.reads = st.reads || {};
  const n = (st.reads[rel] || 0) + 1;
  st.reads[rel] = n;
  saveState(p.session_id, st);
  if (n > Number(cfg.readRepeatLimit)) {
    deny(evt, `本会话已读过 ${rel} 共 ${n - 1} 次。别整篇重读：用 offset/limit 看需要的片段，或用已读到的内容。`);
  }
  process.exit(0);
}

// P1 / P3：写文件
if (tool !== 'write' && tool !== 'edit') process.exit(0);
if (globHit(cfg.exemptGlobs, rel)) process.exit(0);
if (!globHit(cfg.gateGlobs, rel)) process.exit(0);

const plan = freshPlan(cwd, cfg);
if (!plan) {
  deny(evt, `动代码前先写计划：在 ${cfg.planDir}/ 放一份计划（目标 / 验收判据 / 步骤→verify），${cfg.maxPlanAgeHours} 小时内更新过即可。目标文件：${rel}`);
}

if (cfg.newFileAsk && tool === 'write' && !existsSync(abs)) {
  const planText = readFileSync(plan.path, 'utf8');
  const name = rel.split('/').pop();
  const mentioned = planText.includes(rel) || planText.includes(name);
  if (!mentioned) {
    deny(evt, `新建文件 ${rel} 不在计划里。先把它写进计划（产物清单/步骤），再建；或者这本来就不该建。`);
  }
}

// P6：同一文件反复改（打补丁）且计划里没有根因 → 拦一次，要求先写根因
// 用 deny 而不是注入 context：additionalContext 会每步重发，成本反向。
if (cfg.repeatEditLimit && tool === 'edit') {
  const st = loadState(p.session_id);
  st.edits = st.edits || {};
  const rec = st.edits[rel] || { count: 0 };
  rec.count += 1;
  st.edits[rel] = rec;
  saveState(p.session_id, st);
  if (rec.count >= Number(cfg.repeatEditLimit)) {
    const planText = readFileSync(plan.path, 'utf8');
    if (!/根因|root cause/i.test(planText)) {
      deny(evt, `同一文件 ${rel} 本会话已改 ${rec.count} 次，计划里还没有"根因"段。先把根因写进计划（复现→证据→为什么），或换成从根因改；找不到根因就停下来问 Fred，别再打补丁。`);
    }
  }
}
process.exit(0);
