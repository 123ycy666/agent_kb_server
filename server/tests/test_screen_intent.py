# -*- coding: utf-8 -*-
"""大屏跳转意图接入的回归测试（不依赖 pytest，直接跑）：

    python tests/test_screen_intent.py

覆盖 6 件事：
  1. 正常返回 → 只透传 layer / method / mainId / data 四个字段
  2. 对方返回 ok=false → None（调用方不加字段）
  3. 对方 500 → None
  4. **超时** → None，且按配置的超时时间返回（不把问答拖死）
  5. 未配置 URL → enabled()=False、preview()=None（功能关闭）
  6. **不阻塞事件循环**：通过 run_in_threadpool 调用时，事件循环仍能推进
     （这是 2026-10-16 踩过的坑：async def 里同步调外部 HTTP 会卡住全站）

不连真实对方服务：本地起一个可切换行为的 mock。
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = 18799
ok_n, fail_n = 0, 0


def check(label, cond, extra=''):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print('  [ OK ] %-46s %s' % (label, extra))
    else:
        fail_n += 1
        print('  [FAIL] %-46s %s' % (label, extra))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try:
            req = json.loads(body or b'{}')
        except Exception:
            req = {}
        mode = self.path.strip('/')
        if mode == 'slow':
            time.sleep(3)                      # 超过测试里设的 1 秒超时
        if mode == '500':
            self.send_response(500); self.end_headers(); return
        if mode == 'notok':
            payload = {'code': 1, 'ok': False, 'msg': '意图识别失败', 'data': None}
        else:
            payload = {'code': 0, 'ok': True, 'msg': '操作成功', 'data': {
                'text': None, 'markdown': None,
                'layer': 'lineLossAnalysis', 'method': 'stationBudgetInfo',
                'data': [{'stationName': '两水供电所', 'year': '2026'}],
                'stepResults': {}, 'allMethods': [], 'allData': [],
                'mainId': 123,
            }}
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass        # 超时用例里客户端已断开，属预期，不算错误


srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d' % PORT

from app.core.config import settings            # noqa: E402
from app.services import screen_intent as si    # noqa: E402

print('=' * 74)
print('大屏跳转意图 · 回归测试')
print('=' * 74)

# ── 5. 未配置 = 关闭 ───────────────────────────────────────────────
print('\n[5] 未配置 URL（默认状态）')
settings.screen_preview_url = ''
check('enabled() 为 False', si.enabled() is False)
check('preview() 直接返回 None', si.preview('随便问问') is None)

# ── 1. 正常 ───────────────────────────────────────────────────────
print('\n[1] 正常返回')
settings.screen_preview_url = BASE + '/ok'
settings.screen_preview_timeout = 5
check('enabled() 为 True', si.enabled() is True)
r = si.preview('2026年两水供电所线损率情况')
check('返回不是 None', r is not None)
if r:
    check('layer 透传正确', r.get('layer') == 'lineLossAnalysis', r.get('layer'))
    check('method 透传正确', r.get('method') == 'stationBudgetInfo', r.get('method'))
    check('mainId 透传正确', r.get('mainId') == 123, r.get('mainId'))
    check('data 是数组且带 year', isinstance(r.get('data'), list) and r['data'][0].get('year') == '2026')
    check('只透传 4 个字段', set(r.keys()) == {'layer', 'method', 'mainId', 'data'}, sorted(r.keys()))

# ── 2. ok=false ──────────────────────────────────────────────────
print('\n[2] 对方返回 ok=false')
settings.screen_preview_url = BASE + '/notok'
check('返回 None', si.preview('问一句') is None)

# ── 3. HTTP 500 ──────────────────────────────────────────────────
print('\n[3] 对方 500')
settings.screen_preview_url = BASE + '/500'
check('返回 None', si.preview('问一句') is None)

# ── 4. 超时 ──────────────────────────────────────────────────────
print('\n[4] 超时（对方 3 秒不回，超时设 1 秒）')
settings.screen_preview_url = BASE + '/slow'
settings.screen_preview_timeout = 1
t0 = time.time()
r = si.preview('问一句')
used = time.time() - t0
check('返回 None', r is None)
check('按超时返回（1s ≤ 用时 < 2.5s）', 1.0 <= used < 2.5, '%.2fs' % used)

# ── 6. 不阻塞事件循环 ─────────────────────────────────────────────
print('\n[6] 通过 run_in_threadpool 调用时不阻塞事件循环')
import asyncio                                  # noqa: E402
from starlette.concurrency import run_in_threadpool   # noqa: E402

settings.screen_preview_url = BASE + '/slow'
settings.screen_preview_timeout = 3


async def main():
    t = time.time()
    task = asyncio.create_task(run_in_threadpool(si.preview, '问一句'))
    await asyncio.sleep(0.4)                    # 若被阻塞，这行会等到 3s 后才返回
    slept = time.time() - t
    await task
    return slept


slept = asyncio.run(main())
check('等待期间事件循环可推进（sleep 0.4s 实测 < 1s）', slept < 1.0, '%.2fs' % slept)

# ── 收尾 ─────────────────────────────────────────────────────────
settings.screen_preview_url = ''
srv.shutdown()
print('\n' + '=' * 74)
print('结果：%d 通过 / %d 失败' % (ok_n, fail_n))
print('=' * 74)
sys.exit(1 if fail_n else 0)
