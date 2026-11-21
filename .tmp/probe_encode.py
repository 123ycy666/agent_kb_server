# -*- coding: utf-8 -*-
import io
import os
import sys

out = io.StringIO()
sys.path.insert(0, r'D:\秘书智能体\server')
os.chdir(r'D:\秘书智能体')
from dotenv import load_dotenv
load_dotenv(r'D:\秘书智能体\.env')

from app.secretary import name_fix
name_fix._corrector()                       # 让 db 数据源挂上、拼音表入缓存
from app.secretary.libs.name_correction_lib import name_corrector as nc

for name in ('夷陵区供电公司', '西陵区供电公司', '五峰渔洋关供电所', '环潭供电所', '当阳玉阳供电所'):
    out.write('%s -> full=%s | strict=%s | light=%s\n'
              % (name, nc.encode(name, True), nc.encode(name, False), nc.encode_light(name)))

# 独立进程命令行（文档要给的版本）
out.write('\n--- one-liner test ---\n')
open(r'D:\秘书智能体\.tmp\encode_probe.txt', 'w', encoding='utf-8').write(out.getvalue())
print('ok')
