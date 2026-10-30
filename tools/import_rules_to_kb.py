#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把站点包里的业务规则 md 导入知识库，落成 `doc_type='rule'` 文档（按章节切片）。

为什么要有它（2026-10-30）
--------------------------
规则原先住在 `config/sites/<站点>/prompts/*.md`（部署文件，改一个字要发版），
而模型侧又靠"向量检索《判据规则》"碰运气（实测相似度 0.17~0.19）。
现在规则统一进知识库、打 `doc_type='rule'` 标记：
- 模型侧：主题目录 + `get_rule(主题)` 精确取（见 `app/secretary/rule_doc.py`）；
- 人侧：知识库页面能看、能改、能回滚（有文档版本）。

用法：
    py -X utf8 tools/import_rules_to_kb.py --kb-id 1 --dry-run        # 只看会写什么
    py -X utf8 tools/import_rules_to_kb.py --kb-id 1 --apply          # 真的写库
    py -X utf8 tools/import_rules_to_kb.py --kb-id 1 --apply --site suizhou

⚠️ 切片不带向量（embedding=NULL）：规则不走向量检索，只走"按标题精确取"；
   这样也不会混进 `kb_search` 的资料召回里。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

# 默认导入这几份（顺序 = 注入顺序）；文件名 → 中文标题（目录里给人看）
DEFAULT_DOCS = ('business_rules', 'caliber_rules', 'sql_plan_rules',
                'work_order_rules', 'metrics_rules')
DOC_TITLES = {
    'business_rules': '业务规则',
    'caliber_rules': '固定口径',
    'sql_plan_rules': 'SQL 规范',
    'work_order_rules': '工单规则',
    'metrics_rules': '指标口径',
}

_HEAD = re.compile(r'^(#{1,4})\s+(.+?)\s*$', re.M)
_BANNER = re.compile(r'^=+\s*(.+?)\s*=+\s*$', re.M)      # ======== 业务规则 ========
_SECTION = re.compile(r'^【([^】]{1,40})】', re.M)         # 【数据库规范】
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.S)


def _clean(text: str) -> str:
    return _HTML_COMMENT.sub('', text or '').strip()


def split_sections(text: str, doc_title: str) -> list[tuple[str, str]]:
    """把规则 md 切成 [(heading_path, body)]。

    真实文件里三种写法都有，不能只认 markdown：
      1. `========== 业务规则（所有Agent通用） ==========` —— 文档标题
      2. `【数据库规范】` —— 小节标题（业务规则/固定口径用这个）
      3. `## 规则` —— markdown 标题（metrics_rules 用这个）
    """
    text = _clean(text)
    if not text:
        return []
    # ① 文档标题：优先 ====== 横幅，其次 # 标题，最后文件名
    title = doc_title
    m = _BANNER.search(text)
    if m:
        title = m.group(1).strip()
        # 横幅本身不是内容，切掉，否则会被当成"引言"混进目录
        text = _BANNER.sub('', text, count=1)
    else:
        h1 = re.search(r'^#\s+(.+)$', text, re.M)
        if h1:
            title = h1.group(1).strip()
            text = text[:h1.start()] + text[h1.end():]      # 标题行不是内容

    # ② 切点：所有 【…】 与 ##/### 标题
    marks = [(m.start(), m.end(), m.group(1).strip()) for m in _SECTION.finditer(text)]
    marks += [(m.start(), m.end(), m.group(2).strip())
              for m in _HEAD.finditer(text) if len(m.group(1)) >= 2]
    marks.sort()
    if not marks:
        body = text.strip()
        return [('%s > %s' % (doc_title, title), body)] if body else []

    out: list[tuple[str, str]] = []
    for i, (start, end, sec_title) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[end:nxt].strip()
        if not body:
            continue
        # 「规则」「口径」「说明」这类通用小节名会跨文档撞名 → 前面加文档标题
        leaf = sec_title
        if leaf in ('规则', '口径', '说明', '概述', '通用'):
            leaf = '%s：%s' % (title, leaf)
        if leaf == title:
            leaf = '%s：全文' % title
        out.append(('%s > %s' % (title, leaf), body))
    # 首个切点之前的内容（引言）单独成段
    head_body = text[: marks[0][0]].strip()
    if head_body and len(head_body) > 30:
        out.insert(0, ('%s > 说明' % title, head_body))
    return out


def _hint(body: str, n: int = 26) -> str:
    """目录里的一句话说明：去掉 markdown 噪声。"""
    s = re.sub(r'[*`|>\-]{1,}', ' ', body or '')
    s = re.sub(r'\s+', ' ', s).strip()
    return (s[:n] + '…') if len(s) > n else s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb-id', type=int, required=True, help='目标知识库 id')
    ap.add_argument('--site', default='', help='站点目录名（默认取 .env 的 SITE 或 suizhou）')
    ap.add_argument('--apply', action='store_true', help='真的写库（默认只预览）')
    ap.add_argument('--dry-run', action='store_true', help='只预览（默认行为）')
    args = ap.parse_args()

    from app.secretary import pgdb, site_pack
    from app.core.text import to_search_text
    from app.providers import current_embedding_model_name

    try:
        site = args.site or site_pack.load(strict=False).get('site') or 'suizhou'
    except Exception:                              # noqa: BLE001
        site = args.site or 'suizhou'
    base = site_pack.sites_root() / site / 'prompts'
    if not base.is_dir():
        print('[X] 找不到站点提示词目录：%s' % base)
        return 2

    files = []
    for stem in DEFAULT_DOCS:
        p = base / (stem + '.md')
        if p.is_file():
            files.append(p)
    if not files:
        print('[X] %s 下没有可导入的规则文件' % base)
        return 2

    model_name = current_embedding_model_name()
    print('站点 = %s ｜ 目标知识库 = %s ｜ 规则文件 %d 份 ｜ 向量模型 = %s'
          % (site, args.kb_id, len(files), model_name))

    conn = pgdb.connect(autocommit=True)
    written_docs = 0
    written_chunks = 0
    try:
        cur = conn.cursor()
        try:
            for p in files:
                text = p.read_text(encoding='utf-8')
                title = DOC_TITLES.get(p.stem, p.stem)
                # 用中文文件名入库：既好在知识库页面里认，也让"按文件名判 rule"这条
                # 口径天然成立（business_rules.md 这种英文名会被判成资料）。
                filename = title + '.md'
                secs = split_sections(text, title)
                print('  - %-24s 章节 %d 个' % (p.name, len(secs)))
                if not args.apply:
                    for hp, body in secs[:3]:
                        print('      · %s（%d 字）' % (hp, len(body)))
                    continue
                # 已有同名规则文档 → 更新（version+1），原先置为 inactive
                cur.execute(
                    "SELECT id, version_no FROM document WHERE kb_id=%s AND filename=%s "
                    "AND doc_type='rule' AND is_active ORDER BY version_no DESC LIMIT 1",
                    (args.kb_id, filename))
                row = cur.fetchone()
                if row:
                    old_id = int(row['id'])
                    cur.execute("UPDATE document SET is_active=false WHERE id=%s", (old_id,))
                    cur.execute("UPDATE document_chunk SET is_retrievable=false WHERE doc_id=%s",
                                (old_id,))
                    ver = int(row['version_no'] or 1) + 1
                    root_id = old_id
                else:
                    ver = 1
                    root_id = None
                cur.execute(
                    "INSERT INTO document (kb_id, version_no, is_active, filename, file_ext, "
                    "file_size, storage_path, content_hash, parse_status, has_conflict, "
                    "chunk_count, visibility, doc_type, owner_id) "
                    "VALUES (%s,%s,true,%s,'.md',%s,'',%s,'ready',false,%s,'public','rule',NULL)",
                    (args.kb_id, ver, filename, len(text.encode('utf-8')),
                     'ruleimport-' + filename + '-' + str(ver), len(secs)))
                doc_id = cur.lastrowid
                if root_id is None:
                    cur.execute("UPDATE document SET root_id=%s WHERE id=%s", (doc_id, doc_id))
                for i, (hp, body) in enumerate(secs, start=1):
                    cur.execute(
                        "INSERT INTO document_chunk (doc_id, kb_id, chunk_index, content, "
                        "heading_path, embedding_model, tags, is_retrievable, search_text) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'{}',true,to_tsvector('simple', %s))",
                        (doc_id, args.kb_id, i, '【%s】\n%s' % (hp, body), hp,
                         model_name, to_search_text(body)))
                written_docs += 1
                written_chunks += len(secs)
        finally:
            cur.close()
    finally:
        conn.close()

    if args.apply:
        print('\n✓ 已导入 %d 份规则文档、%d 个章节切片' % (written_docs, written_chunks))
        print('  验证：py -X utf8 -c "from app.secretary import rule_doc;print(rule_doc.menu()[:400])"')
    else:
        print('\n（预览模式，未写入。加 --apply 才真正写库）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
