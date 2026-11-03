# -*- coding: utf-8 -*-
"""把指定 DSH 会话的可见对话（user 原文 + assistant 正文）导出成 md。只读。"""
import json
import os
import sys

import zstandard as zstd

ROOT = os.path.expanduser(r'~\.dsh\sessions')
WS = sys.argv[1]
SID = sys.argv[2]
OUT = sys.argv[3]


def strip_reminder(t):
    i = t.find('</system-reminder>')
    return t[i + len('</system-reminder>'):].strip() if i >= 0 else t


blocks = []
path = os.path.join(ROOT, WS, SID, 'session.v3.jsonl.zstd')
with open(path, 'rb') as f:
    dctx = zstd.ZstdDecompressor()
    with dctx.stream_reader(f) as r:
        raw = r.read().decode('utf-8', 'replace')

for ln in raw.splitlines():
    try:
        obj = json.loads(ln)
    except Exception:                              # noqa: BLE001
        continue
    typ = obj.get('type')
    data = obj.get('data') or {}
    msg = data.get('message') or {}
    content = msg.get('content')
    if not isinstance(content, list):
        continue
    texts = []
    for c in content:
        if isinstance(c, dict) and c.get('type') == 'text':
            texts.append(c.get('text') or '')
    body = '\n'.join(t for t in texts if t).strip()
    if not body:
        continue
    if typ == 'user/message':
        body = strip_reminder(body)
        if not body:
            continue
        blocks.append(('USER  seq=%s' % obj.get('seq'), body))
    elif typ == 'assistant/message':
        blocks.append(('ASSISTANT seq=%s' % obj.get('seq'), body))

with open(OUT, 'w', encoding='utf-8') as f:
    for head, body in blocks:
        f.write('\n\n===== %s =====\n%s\n' % (head, body))
print('blocks=%d chars=%d -> %s' % (len(blocks), sum(len(b) for _, b in blocks), OUT))
