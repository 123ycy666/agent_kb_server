# -*- coding: utf-8 -*-
"""在所有 DSH 会话里找标题含关键词的会话（只读，不改任何东西）。"""
import json
import os
import sys

import zstandard as zstd

ROOT = os.path.expanduser(r'~\.dsh\sessions')
KEY = sys.argv[1] if len(sys.argv) > 1 else '提示词'


def read_lines(path):
    try:
        with open(path, 'rb') as f:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(f) as r:
                data = r.read()
        return data.decode('utf-8', 'replace').splitlines()
    except Exception as exc:                       # noqa: BLE001
        print('  [read fail] %s: %s' % (path, exc))
        return []


def main():
    for ws in sorted(os.listdir(ROOT)):
        wsd = os.path.join(ROOT, ws)
        if not os.path.isdir(wsd):
            continue
        for sid in sorted(os.listdir(wsd)):
            p = os.path.join(wsd, sid, 'session.v3.jsonl.zstd')
            if not os.path.isfile(p):
                continue
            lines = read_lines(p)
            if not lines:
                continue
            title = ''
            hit = ''
            for ln in lines[:5]:
                try:
                    obj = json.loads(ln)
                except Exception:                  # noqa: BLE001
                    continue
                blob = json.dumps(obj, ensure_ascii=False)
                for k in ('title', 'summary', 'name'):
                    if obj.get(k) and not title:
                        title = str(obj.get(k))[:80]
            if KEY in title:
                print('TITLE-HIT %s / %s :: %s' % (ws, sid, title))
                continue
            for ln in lines:
                if KEY in ln:
                    hit = ln
                    break
            if hit:
                try:
                    obj = json.loads(hit)
                    txt = json.dumps(obj, ensure_ascii=False)[:260]
                except Exception:                  # noqa: BLE001
                    txt = hit[:260]
                print('BODY-HIT  %s / %s :: %s' % (ws, sid, txt))


if __name__ == '__main__':
    main()
