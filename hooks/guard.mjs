// guard.mjs —— 统一守卫（v5，2026-10-30）
//
// v5 相对 v4 的改动，全部是"减负"：
//   1. 删掉 read 去重（v4 读同一文件第 4 次就 deny）—— 诊断必须重读，拦它只会逼人瞎猜
//   2. 计划门从"第 1 个代码文件"改成"第 3 个不同代码文件"—— 改一个字不该写计划
//   3. planDir 默认值对齐到 docs/04-计划（v4 默认 docs/plans，与实际目录不一致，两套并存）
//   4. 状态目录不再硬编码 D:/秘书智能体，改为 <cwd>/.dsh/tmp/hook-state
//   5. 修掉 v4 的重复计数 bug：同一次 edit 会被 PreToolUse 和 PostToolUse 各加一次
//
// 保留：同文件第 3 次 edit 且计划无"根因"段 → deny（这条是对的）
//
// 判定只发生在"写文件"这一个动作上；读、答、聊一律不管。
// 所有策略在 <项目>/.dsh/guard.json 可配。
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

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
  planDir: 'docs/04-计划',
  maxPlanAgeHours: 24,
  gateGlobs: ['*.py', '*.ts', '*.tsx', '*.js', '*.mjs', '*.vue', '*.sql', '*.ps1', '*.sh', '*.bat', '*.yml', '*.yaml', '*.json'],
  exemptGlobs: ['docs/*', 'output/*', 'hooks/*', '.dsh/*', '*.md'],
  planGlobs: ['*.md'],
  readDedupe: false,      // v5：关掉
  planGateMinFiles: 3,    // v5：本会话第 3 个不同代码文件起才要计划
  newFileAsk: true,
  reanchor: false,        // 2026-10-19 关闭：additionalContext 每步重发，600 字×N 步 = 反向浪费
  rootCauseHintOnce: true,
  repeatEditLimit: 3,     // 同一文件第 3 次 edit 且计划无"根因"段 → deny（0 = 关闭）
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
  if (!p) return { abs: '', rel: '' };
  let abs = p.replace(/\\/g, '/');
  if (!/^[a-z]:\//i.test(abs) && !abs.startsWith('/')) abs = cwd.replace(/\\/g, '/') + '/' + abs;
  abs = abs.replace(/\/{2,}/g, '/');
  const base = cwd.replace(/\\/g, '/').replace(/\/+$/, '');
  const rel = abs.toLowerCase().startsWith(base.toLowerCase() + '/') ? abs.slice(base.length + 1) : abs;
  return { abs, rel };
}

function stateFile(cwd, session) {
  const dir = join(cwd, '.dsh', 'tmp', 'hook-state');
  try { mkdirSync(dir, { recursive: true }); } catch {}
  return join(dir, `${String(session || 'unknown').replace(/[^\w.-]/g, '_')}.json`);
}
function loadState(cwd, session) {
  try { return JSON.parse(readFileSync(stateFile(cwd, session), 'utf8')); } catch { return {}; }
}
function saveState(cwd, session, s) {
  try { writeFileSync(stateFile(cwd, session), JSON.stringify(s)); } catch {}
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

// ── UserPromptSubmit：目标重锚（默认关闭）──────────────────────────────
if (evt === 'UserPromptSubmit') {
  if (!cfg.reanchor) process.exit(0);
  const plan = freshPlan(cwd, cfg);
  if (!plan) process.exit(0);
  const text = readFileSync(plan.path, 'utf8');
  const lines = text.split(/\r?\n/);
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
  const text2 = [
    `【目标重锚】计划：${plan.name}`,
    ...head,
    progress.length ? '最近进度：\n' + progress.join('\n') : '',
  ].filter(Boolean).join('\n');
  context(evt, text2.slice(0, 1800));
  process.exit(0);
}

// ── PostToolUse：根因提醒（只读计数，不重复累加）──────────────────────
if (evt === 'PostToolUse') {
  if (tool !== 'write' && tool !== 'edit') process.exit(0);
  if (!cfg.rootCauseHintOnce) process.exit(0);
  const { rel } = normPath(targetOf(p.tool_input), cwd);
  if (!rel) process.exit(0);
  const st = loadState(cwd, p.session_id);
  const count = (st.edits && st.edits[rel] && st.edits[rel].count) || 0;
  if (count >= 2) {
    const plan = freshPlan(cwd, cfg);
    const planText = plan ? readFileSync(plan.path, 'utf8') : '';
    if (!/根因|root cause/i.test(planText)) {
      context(evt, `【根因提醒】${rel} 本会话已改 ${count} 次，计划里还没有"根因"段。先写下根因（复现→证据→为什么）；找不到就停下来问 Fred，别加特例。`);
    }
  }
  process.exit(0);
}

// ── PreToolUse ──────────────────────────────────────────────────────
if (evt !== 'PreToolUse') process.exit(0);
const target = targetOf(p.tool_input);
if (!target) process.exit(0);
const { abs, rel } = normPath(target, cwd);

// 读文件：v5 一律放行（readDedupe 保留开关，默认 false）
if (tool === 'read') {
  if (!cfg.readDedupe) process.exit(0);
  const st = loadState(cwd, p.session_id);
  st.reads = st.reads || {};
  const n = (st.reads[rel] || 0) + 1;
  st.reads[rel] = n;
  saveState(cwd, p.session_id, st);
  if (n > Number(cfg.readRepeatLimit || 3)) {
    deny(evt, `本会话已读过 ${rel} 共 ${n - 1} 次。别整篇重读：用 offset/limit 看需要的片段。`);
  }
  process.exit(0);
}

if (tool !== 'write' && tool !== 'edit') process.exit(0);
if (globHit(cfg.exemptGlobs, rel)) process.exit(0);
if (!globHit(cfg.gateGlobs, rel)) process.exit(0);

// 本会话命中门的文件清单：前 planGateMinFiles-1 个直接放行
const st = loadState(cwd, p.session_id);
st.gated = Array.isArray(st.gated) ? st.gated : [];
if (!st.gated.includes(rel)) st.gated.push(rel);
saveState(cwd, p.session_id, st);

const minFiles = Number(cfg.planGateMinFiles || 1);
if (st.gated.length < minFiles) process.exit(0);

const plan = freshPlan(cwd, cfg);
if (!plan) {
  deny(evt, `这是本会话要动的第 ${st.gated.length} 个代码文件，动之前先写计划：在 ${cfg.planDir}/ 放一份（目标 / 验收判据 / 步骤→verify），${cfg.maxPlanAgeHours} 小时内更新过即可。目标文件：${rel}`);
}

// 新建文件必须先在计划里出现
if (cfg.newFileAsk && tool === 'write' && !existsSync(abs)) {
  const planText = readFileSync(plan.path, 'utf8');
  const name = rel.split('/').pop();
  if (!planText.includes(rel) && !planText.includes(name)) {
    deny(evt, `新建文件 ${rel} 不在计划里。先把它写进计划（产物清单/步骤），再建；或者这本来就不该建。`);
  }
}

// 同一文件反复改（打补丁）且计划里没有根因 → 拦一次
if (cfg.repeatEditLimit && tool === 'edit') {
  const st2 = loadState(cwd, p.session_id);
  st2.edits = st2.edits || {};
  const rec = st2.edits[rel] || { count: 0 };
  rec.count += 1;
  st2.edits[rel] = rec;
  saveState(cwd, p.session_id, st2);
  if (rec.count >= Number(cfg.repeatEditLimit)) {
    const planText = readFileSync(plan.path, 'utf8');
    if (!/根因|root cause/i.test(planText)) {
      deny(evt, `同一文件 ${rel} 本会话已改 ${rec.count} 次，计划里还没有"根因"段。先把根因写进计划（复现→证据→为什么），或换成从根因改；找不到根因就停下来问 Fred，别再打补丁。`);
    }
  }
}
process.exit(0);
