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
  memoryInject: true,     // v5.1：提问时按内容捞相关记忆注入（否则那 40 条记忆永远进不来）
  memoryInjectTop: 5,     // 最多标星几条
  memoryInjectMinScore: 0.3,// 稀有度加权后的分数阈值（「预算」这种实词单独命中约 0.25~0.5）
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

// ── 记忆注入（v5.1）──────────────────────────────────────────────────
// 为什么要有：40 条记忆只有 AGENTS.md 那 37 行每轮注入，其余全靠模型主动去翻 —— 实际从不翻。
// 做法：读两份 MEMORY.md 索引（用户级 + 项目级），按提问的 2-gram 重合度捞最相关的几条。
// 只在命中时注入；没命中一个字都不加，避免污染上下文。
function bigrams(s) {
  const t = String(s).toLowerCase();
  const set = new Set();
  // 英文/数字按整词取（否则 dsh_pet 会被切成 sh/et/h_ 这种碎片，命中一堆无关条目）
  for (const m of t.matchAll(/[a-z0-9][a-z0-9_-]{1,}/g)) set.add('w:' + m[0]);
  // 中文按 2-gram
  const cn = t.replace(/[^\u4e00-\u9fa5]/g, '');
  for (let i = 0; i < cn.length - 1; i++) set.add(cn.slice(i, i + 2));
  return set;
}

function loadMemoryIndex(cwd) {
  const dirs = [];
  if (process.env.USERPROFILE) dirs.push(join(process.env.USERPROFILE, '.dsh', 'memory'));
  dirs.push(join(cwd, '.dsh', 'memory'));
  const rows = [];
  for (const d of dirs) {
    const f = join(d, 'MEMORY.md');
    if (!existsSync(f)) continue;
    let text = '';
    try { text = readFileSync(f, 'utf8'); } catch { continue; }
    for (const ln of text.split(/\r?\n/)) {
      const m = ln.match(/^\s*-\s*\[(.+?)\]\((.+?\.md)\)\s*[—–\-:]+\s*(.*)$/);
      if (!m) continue;
      rows.push({ title: m[1].trim(), file: m[2].trim(), desc: m[3].trim(), dir: d });
    }
  }
  return rows;
}

function pickMemories(cwd, prompt, top, minScore) {
  const rows = loadMemoryIndex(cwd);
  if (!rows.length) return { starred: [], titles: [] };
  const pm = bigrams(prompt);
  // 按"稀有度"加权（TF-IDF 思路）：在越多行里出现的 2-gram 越可能是虚词（怎么/这个/可以），权重越低。
  const df = new Map();
  const sets = rows.map((r) => {
    const s = bigrams(r.title + ' ' + r.desc);
    for (const g of s) df.set(g, (df.get(g) || 0) + 1);
    return s;
  });
  const scored = rows.map((r, i) => {
    let score = 0, why = '', whyW = 0;
    for (const g of pm) {
      if (!sets[i].has(g)) continue;
      const w = 1 / Math.max(1, df.get(g) || 1);
      score += w;
      if (w > whyW) { whyW = w; why = g; }
    }
    return { ...r, score, why };
  });
  scored.sort((a, b) => b.score - a.score);
  const picked = scored.filter((r) => r.score >= minScore).slice(0, top);
  const starred = picked.map((r) => {
    const d = r.desc.length > 80 ? r.desc.slice(0, 80) + '…' : r.desc;
    return `${r.title}（命中「${r.why}」）：${d}【${r.dir.replace(/\\/g, '/')}/${r.file}】`;
  });
  const starredTitles = new Set(picked.map((r) => r.title));
  const titles = rows.map((r) => (starredTitles.has(r.title) ? '★' + r.title : r.title));
  return { starred, titles };
}

const raw = await readStdin();
let p = {};
try { p = JSON.parse(raw); } catch { process.exit(0); }

const evt = p.hook_event_name || '';
const tool = String(p.tool_name || '').toLowerCase();
const cwd = p.cwd || process.cwd();
const cfg = loadConfig(cwd);

// ── SessionStart：注入一次记忆索引（安全，不随提问触发，不会自激）──────
if (evt === 'SessionStart') {
  if (cfg.memoryIndexOnStart === false) process.exit(0);
  try {
    const rows = loadMemoryIndex(cwd);
    if (rows.length) {
      const titles = rows.map((r) => r.title);
      context(evt, '【记忆索引】你以前记下的条目。要细节先 read 对应 md 文件，别凭印象：\n' + titles.join(' · '));
    }
  } catch {}
  process.exit(0);
}

// ── UserPromptSubmit：记忆注入 + 目标重锚 ─────────────────────────────
if (evt === 'UserPromptSubmit') {
  // 2026-11-03 事故：additionalContext 被宿主当成新的 inbox 投递，又触发一次 UserPromptSubmit，
  // 形成「注入→触发→再注入」的自激循环，把会话刷爆。整个分支硬关闭，等验证过不自触再开。
  if (cfg.memoryInjectHardOff !== false) process.exit(0);
  if (cfg.memoryInject) {
    const prompt = String(p.prompt || '');
    if (prompt.trim().length >= 4) {
      const { starred, titles } = pickMemories(cwd, prompt, Number(cfg.memoryInjectTop || 3), Number(cfg.memoryInjectMinScore || 0.3));
      const st = loadState(cwd, p.session_id);
      const parts = [];
      // 全量标题只在每个会话第一次注入：不占后续上下文，但保证「有哪些记忆」这件事不会再被忘掉
      if (!st.memIndexSent && titles.length) {
        parts.push('【记忆索引】你以前记下的条目。★=与本次提问可能相关。要用细节就 read 对应文件，别凭印象：\n' +
          titles.join(' · '));
        st.memIndexSent = true;
        saveState(cwd, p.session_id, st);
      }
      if (starred.length) {
        parts.push('【可能相关的记忆】★只是关键词重合、未必真相关，你自己判断；要用细节先 read 原文：\n- ' + starred.join('\n- '));
      }
      if (parts.length) {
        context(evt, parts.join('\n\n'));
        process.exit(0);
      }
    }
  }
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
