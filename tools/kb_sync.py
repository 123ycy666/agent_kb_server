# -*- coding: utf-8 -*-
"""把 skills/*.md 同步到百炼知识库（检索面索引 r57xtq9ypm）。

用法（仓库根目录）：
    py -X utf8 tools/kb_sync.py --check    # 只列出本地与库里不一致的文档，不写
    py -X utf8 tools/kb_sync.py            # 不一致的重传（先删旧的再传，避免重名两份），
                                           # 本地已删的也从库里清掉

为什么要它：本地 skills/*.md 是**一处维护**的标准，但问答链路是从知识库检索标准的。
本地改了 md 只跟部署脚本上服务器，**知识库不会自动更新** —— 之前就漏传过两篇。
本工具用 md5 清单判断变化，只重传变了的。
**本地是唯一维护源**：本地删掉哪篇，库里那篇也就删掉（只删清单里记过的名字），
否则废弃的旧规则留在库里照样被检索到，等于同时有两份打架的标准。

清单：output/kb_sync_manifest.json（本地，不入库）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'secretary'))
os.chdir(str(ROOT / 'secretary'))

INDEX_ID = 'r57xtq9ypm'
MANIFEST = ROOT / 'output' / 'kb_sync_manifest.json'
# ★ 进知识库的**只有规则文档**。
#   为什么：技能文档不是「判断标准」，它们只在服务器文件系统上给技能接口用；
#   放进知识库会和规则文档抢检索名额 —— 实测问「是哪些项目」，技能文档《各层级审批》
#   拿 0.972、规则文档《判据规则》只有 0.785，模型就照着技能文档的模板答，
#   而不是照规则答（这正是"预检索判据会挑错标准"的根因）。
#   对话就是对话、接口就是接口：两条路各读各的，不互相挤。
# 例外：**数据表说明书**属于「检索面」资料（什么数据对应什么问题、怎么关联、坑），
#   它不跟规则抢标准，反而是分析题必须检索到的，所以放进来。
KB_DOCS = ['判据规则.md', '数据表说明书.md', '分析方向.md']


def local_docs() -> dict:
    out = {}
    for name in KB_DOCS:
        p = ROOT / 'skills' / name
        if not p.exists():
            print('   [警告] 本地缺 %s，本轮跳过' % name)
            continue
        out[p.stem] = (p, hashlib.md5(p.read_bytes()).hexdigest())
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='同步 skills/*.md 到知识库')
    ap.add_argument('--check', action='store_true', help='只列差异，不上传')
    args = ap.parse_args()

    import kb_bailian
    import kb_config
    kb = kb_bailian.BailianKnowledgeBase(kb_config.settings)

    local = local_docs()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
    docs = kb.list_index_documents(INDEX_ID, page_size=100).get('documents') or []
    in_kb = {}
    for d in docs:
        in_kb.setdefault((d.get('name') or '').strip(), d)

    todo = [(n, p, h) for n, (p, h) in local.items()
            if manifest.get(n) != h or n not in in_kb]
    # 本地已删、清单里还记着的 → 库里那篇是废弃稿，删掉。
    # 为什么默认就删：**本地 skills/*.md 是唯一维护源**，知识库必须跟它一致；
    # 否则旧规则留在库里照样被检索到，等于同时有两份互相打架的标准。
    # 只删「清单里记过的」名字 —— 别人的库不经手。
    stale = [n for n in manifest if n not in local]
    print('本地规则文档 %d 篇；库里 %d 篇；要传 %d 篇；要清 %d 篇'
          % (len(local), len(in_kb), len(todo), len(stale)))
    for n, p, h in todo:
        why = '库里没有' if n not in in_kb else '本地改过'
        print('   %-30s %s' % (p.name, why))
    for n in stale:
        print('   %-30s 本地已删，从库里清掉' % n)
    if not todo and not stale:
        print('已一致，无需上传')
        return 0
    if args.check:
        return 0

    for n in stale:
        d = in_kb.get(n)
        if d:
            try:
                kb.delete_index_document(INDEX_ID, d.get('file_id'))
                print('   已清理 %s' % n)
            except Exception as e:                      # noqa: BLE001
                print('   清理失败（留在清单里，下次再试）：%s' % str(e)[:120])
                continue
        manifest.pop(n, None)

    for n, p, h in todo:
        old = in_kb.get(n)
        if old:
            try:
                kb.delete_index_document(INDEX_ID, old.get('file_id'))
                print('   删旧版 %s' % n)
            except Exception as e:                      # noqa: BLE001
                print('   删旧版失败（继续传新的）：%s' % str(e)[:120])
        r = kb.upload_document(INDEX_ID, file_name=p.name, file_bytes=p.read_bytes(),
                               wait_parse=True, submit_index=True, skip_if_exists=False)
        print('   上传 %-30s %s' % (p.name, r.get('parse_status')))
        manifest[n] = h
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    print('清单已更新：%s' % MANIFEST)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
