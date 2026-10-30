# -*- coding: utf-8 -*-
"""看图：把图片交给多模态模型（qwen3-vl 系）回答关于它的问题。

为什么单开一条路（2026-10-21）
---------------------------
现有 `_chat` 只发纯文本消息，塞图片要改主循环的装配；而看图是**独立能力**，
单开一个模块最省事、也不会污染查数那套提示词。

模型怎么选：`SECRETARY_VISION_MODEL` 环境变量 > 站点包 `site.yml: vision.model` > 默认 `qwen3-vl-plus`。
模型不可用时**明确报错**（含网关原文），不要静默降级成"看不见"。

安全边界：只收图片 mime、单张 ≤ 8 MB；不落盘（直接 base64 进请求），避免照片留在临时目录。
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = 'qwen3-vl-plus'
MAX_BYTES = 8 * 1024 * 1024
OK_MIME = ('image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp')

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


class VisionError(RuntimeError):
    """看图不可用：没配模型 / 图太大 / 网关报错。消息是给人看的。"""


def model_id() -> str:
    env = (os.environ.get('SECRETARY_VISION_MODEL') or '').strip()
    if env:
        return env
    return DEFAULT_MODEL


def sniff_mime(data: bytes, filename: str = '') -> str:
    """按文件头判断类型（不信扩展名）；不认识的抛错。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:2] == b'BM':
        return 'image/bmp'
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    guess = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
             'webp': 'image/webp', 'gif': 'image/gif', 'bmp': 'image/bmp'}.get(ext)
    if guess:
        raise VisionError('文件头不像图片（扩展名是 .%s，但内容不是可识别的图片）' % ext)
    raise VisionError('不是支持的图片格式（支持 PNG/JPEG/WEBP/GIF/BMP）')


def describe(data: bytes, question: str = '', *, filename: str = '',
             model: str | None = None, max_tokens: int = 800) -> dict:
    """让多模态模型看图回答。返回 {'text','model','ms','mime','bytes'}。"""
    if not data:
        raise VisionError('图片是空的')
    if len(data) > MAX_BYTES:
        raise VisionError('图片太大（%.1f MB，上限 %d MB）；请压缩后再传'
                          % (len(data) / 1048576.0, MAX_BYTES // 1048576))
    mime = sniff_mime(data, filename)
    if mime not in OK_MIME:
        raise VisionError('不支持的图片类型：%s' % mime)

    from app.secretary import config as sec_config
    if not sec_config.LLM_BASE:
        raise VisionError('没有配置模型端点（.env 的 LLM 地址为空）')
    mid = model or model_id()
    q = (question or '').strip() or '请用中文客观描述这张图里的内容；图里有文字就逐条抄出来。'

    body = {
        'model': mid,
        'temperature': 0,
        'max_tokens': max_tokens,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': q},
                {'type': 'image_url',
                 'image_url': {'url': 'data:%s;base64,%s'
                                      % (mime, base64.b64encode(data).decode('ascii'))}},
            ],
        }],
    }
    raw_body = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        sec_config.LLM_BASE + '/chat/completions', data=raw_body,
        headers={'Authorization': 'Bearer ' + sec_config.LLM_KEY,
                 'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        raw = urllib.request.urlopen(req, timeout=120, context=_ctx).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read().decode('utf-8', 'replace')[:300]
        except Exception:                          # noqa: BLE001
            pass
        if exc.code == 404 or 'model' in detail.lower():
            raise VisionError('视觉模型「%s」在当前网关不可用（HTTP %d）。%s'
                              % (mid, exc.code, detail)) from exc
        raise VisionError('看图请求被拒（HTTP %d）：%s' % (exc.code, detail)) from exc
    except Exception as exc:                       # noqa: BLE001
        raise VisionError('看图请求失败：%s: %s' % (type(exc).__name__, str(exc)[:160])) from exc

    try:
        payload = json.loads(raw)
        text = (payload['choices'][0]['message'].get('content') or '').strip()
    except Exception as exc:                       # noqa: BLE001
        raise VisionError('网关返回无法解析：%s' % raw[:200]) from exc
    if not text:
        raise VisionError('模型没有给出内容（可能这张图不清晰，或问题太抽象）')
    return {'text': text, 'model': mid, 'ms': int((time.time() - t0) * 1000),
            'mime': mime, 'bytes': len(data)}


def describe_file(path, question: str = '', **kwargs) -> dict:
    from pathlib import Path
    p = Path(str(path))
    if not p.is_file():
        raise VisionError('图片不存在：%s' % p.name)
    return describe(p.read_bytes(), question, filename=p.name, **kwargs)
