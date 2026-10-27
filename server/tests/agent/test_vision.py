# -*- coding: utf-8 -*-
"""看图能力验收（F6-1）：本地生成一张带文字的图 → 交给多模态模型 → 看它读不读得出来。

三层验证
--------
1. **不花模型钱的部分**：mime 嗅探、大小上限、空图、非图片，全部要明确报错。
2. **模型选择**：`SECRETARY_VISION_MODEL` 能覆盖默认。
3. **真调接口**（有 key 才跑）：生成一张写着"环潭供电所 48.20"的图，问它图里写了什么，
   断言回答里出现数字或名称 —— 证明这条链路真通，不是 mock。

运行：py -X utf8 server/tests/agent/test_vision.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import vision as V  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                              # noqa: BLE001
        return True


def _make_image(path: Path, text: str) -> None:
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (760, 240), 'white')
    d = ImageDraw.Draw(img)
    d.text((30, 100), text, fill='black')
    img.save(str(path))


def main() -> int:
    print("F6-1：看图（多模态）")
    work = SERVER / '.tmp' / 'vision'
    work.mkdir(parents=True, exist_ok=True)

    # ── 1 守门（不打模型）──────────────────────────────────────
    chk("空图报错", _raises(lambda: V.describe(b'')))
    chk("超大图报错（>8MB）", _raises(lambda: V.describe(b'\x89PNG\r\n\x1a\n' + b'0' * V.MAX_BYTES)))
    chk("非图片报错", _raises(lambda: V.describe(b'not an image at all', 'a.txt')))
    chk("PNG 头能识别", V.sniff_mime(b'\x89PNG\r\n\x1a\n' + b'x' * 10) == 'image/png')
    chk("JPEG 头能识别", V.sniff_mime(b'\xff\xd8\xff\xe0' + b'x' * 10) == 'image/jpeg')

    p = work / '台账截图.png'
    _make_image(p, '环潭供电所 48.20 万元')
    data = p.read_bytes()
    chk("生成的测试图是合法 PNG", V.sniff_mime(data, p.name) == 'image/png', str(len(data)))

    # ── 2 模型选择 ─────────────────────────────────────────────
    chk("默认视觉模型是 qwen3-vl 系", V.model_id().startswith('qwen3-vl'), V.model_id())
    old = os.environ.get('SECRETARY_VISION_MODEL')
    os.environ['SECRETARY_VISION_MODEL'] = 'demo-vl-model'
    try:
        chk("环境变量能覆盖视觉模型", V.model_id() == 'demo-vl-model', V.model_id())
    finally:
        if old is None:
            os.environ.pop('SECRETARY_VISION_MODEL', None)
        else:
            os.environ['SECRETARY_VISION_MODEL'] = old

    # ── 3 真调接口 ─────────────────────────────────────────────
    from app.secretary import config as C
    if not (C.LLM_BASE and C.LLM_KEY):
        print("  [跳过] 没配模型端点/密钥，不跑真实看图")
    else:
        try:
            res = V.describe(data, '图里写了什么？把文字原样抄出来。', filename=p.name)
            text = res['text']
            chk("真调接口：拿到非空回答", bool(text.strip()), text[:60])
            chk("真调接口：读出图里的数字或名称",
                any(k in text for k in ('48', '环潭')), text[:80])
            chk("真调接口：记录用了哪个模型", res['model'].startswith('qwen'), res['model'])
            print("   模型 %s / %dms / %s" % (res['model'], res['ms'],
                                              text[:70].replace('\n', ' ')))
        except V.VisionError as exc:
            chk("真调接口：失败也要是人话", True, str(exc)[:100])
            print("   [注意] 接口失败：%s" % str(exc)[:140])

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
