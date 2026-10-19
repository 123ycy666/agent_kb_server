# -*- coding: utf-8 -*-
"""部署连通性自检：只测「这台机器能不能摸到那三个地址」，不测业务逻辑。

用途：内网 / 新机器部署时先跑这个。逐项打印 ✓ / ✗ 和原始报错，一眼看出卡在哪一层。
只读：不写库、不改配置、不落任何文件。

    python tools/net_check.py
    （包里的 1-环境自检.bat 会调它）
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'secretary'))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                   # noqa: BLE001
    pass

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

OK, BAD = '[√]', '[×]'


def tcp(host, port, timeout=6):
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, '通（%.2fs）' % (time.time() - t0)
    except Exception as e:                          # noqa: BLE001
        return False, '%s: %s' % (type(e).__name__, str(e)[:120])


def http(url, headers=None, data=None, timeout=15):
    t0 = time.time()
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return True, r.status, r.read(200000), time.time() - t0
    except urllib.error.HTTPError as e:
        return False, e.code, (e.read() or b'')[:200], time.time() - t0
    except Exception as e:                          # noqa: BLE001
        return False, None, ('%s: %s' % (type(e).__name__, str(e)[:150])).encode('utf-8'), time.time() - t0


def main():
    try:
        import config
    except SystemExit as e:
        print(BAD, '读配置失败：', e)
        return 2
    except Exception as e:                          # noqa: BLE001
        print(BAD, '读配置失败：%s: %s' % (type(e).__name__, e))
        return 2

    fails = []
    print('配置文件 : %s' % config.ENV_PATH)
    print('=' * 66)

    # 1) 业务库（MySQL）
    print('1. 业务库 MySQL  %s:%s' % (config.DB['host'], config.DB['port']))
    ok, why = tcp(config.DB['host'], config.DB['port'])
    print('   TCP     %s %s' % (OK if ok else BAD, why))
    if not ok:
        fails.append('业务库 TCP')
    else:
        try:
            import pymysql
            c = pymysql.connect(host=config.DB['host'], port=config.DB['port'],
                                user=config.DB['user'], password=config.DB['password'],
                                database='dlj_data', connect_timeout=8)
            with c.cursor() as cur:
                cur.execute('SELECT COUNT(*) FROM ai_data.ai_table_metadata')
                n = cur.fetchone()[0]
            c.close()
            print('   登录+字典 %s 可读，ai_table_metadata 有 %s 行' % (OK, n))
        except Exception as e:                      # noqa: BLE001
            print('   登录+字典 %s %s: %s' % (BAD, type(e).__name__, str(e)[:150]))
            fails.append('业务库登录/字典')

    # 2) 知识库（自建系统）
    print('2. 知识库        %s' % config.KB_BASE)
    ok, status, body, dt = http(config.KB_BASE + '/auth/login',
                                headers={'Content-Type': 'application/json'},
                                data=json.dumps({'username': config.KB_USER,
                                                 'password': config.KB_PASSWORD}).encode('utf-8'))
    if ok and status == 200:
        tok = (json.loads(body.decode('utf-8', 'replace')).get('access_token') or '')
        print('   登录     %s 拿到 token（%s 字，%.2fs）' % (OK, len(tok), dt))
    else:
        print('   登录     %s HTTP=%s %s' % (BAD, status, body[:160]))
        fails.append('知识库登录')

    # 3) 模型端点
    print('3. 模型端点      %s' % config.LLM_BASE)
    ok, status, body, dt = http(config.LLM_BASE + '/models',
                                headers={'Authorization': 'Bearer ' + config.LLM_KEY}, timeout=20)
    if ok and status == 200:
        try:
            ids = [m.get('id') for m in json.loads(body.decode('utf-8', 'replace'))['data']]
            print('   GET /models %s 可达，返回 %d 个模型（%.2fs）' % (OK, len(ids), dt))
            if config.MODEL in ids:
                print('   要用的模型 %s %s 在端点列表里' % (config.MODEL, OK))
            else:
                print('   要用的模型 %s %s **不在**端点列表里 —— 换端点后模型名可能不一样，'
                      '改 .env 的 LLM_MODEL_ANSWER' % (config.MODEL, BAD))
                fails.append('模型名 %s 不在端点列表里' % config.MODEL)
        except Exception:                           # noqa: BLE001
            print('   GET /models %s 可达但响应不是预期的 JSON（有些网关不实现 /models，可忽略）' % OK)
    else:
        print('   GET /models %s HTTP=%s %s' % (BAD, status, body[:160]))
        fails.append('模型端点')

    print('=' * 66)
    print('当前要用的模型 : %s' % config.MODEL)
    print('知识库检索范围 : %s' % config.KB_IDS)
    if fails:
        print('%s 有 %d 项不通：%s' % (BAD, len(fails), '、'.join(fails)))
        print('   → 内网部署时先解决这几项；换地址改包根目录的 .env。')
        return 1
    print('%s 三项全通，可以起服务。' % OK)
    return 0


if __name__ == '__main__':
    sys.exit(main())
