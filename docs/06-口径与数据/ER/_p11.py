# -*- coding: utf-8 -*-
"""用 Pillow 精确丈量字段名/中文名宽度，彻底解决「字段名与中文名叠字」。"""
import io

p = "build_er.py"
s = io.open(p, encoding="utf-8").read()

# 1) 顶部引入测量器
s = s.replace(
    "ROW_H = 19\nHEAD_H = 54",
    '''ROW_H = 19
HEAD_H = 54

# 文本宽度用 Pillow 实测（估算法会把「administrator_flag」量短，导致中文名压到字段名上）
try:
    from PIL import ImageFont as _IF

    _MEASURE_CACHE: dict = {}

    def text_w(txt: str, size: float, font_path: str = "C:/Windows/Fonts/msyh.ttc") -> float:
        key = (round(size, 1), font_path)
        f = _MEASURE_CACHE.get(key)
        if f is None:
            f = _IF.truetype(font_path, max(6, int(round(size))))
            _MEASURE_CACHE[key] = f
        return float(f.getlength(txt))
except Exception:  # pragma: no cover - 没有 Pillow 时退回估算
    def text_w(txt: str, size: float, font_path: str = "") -> float:
        return sum((size if ord(ch) > 127 else size * 0.55) for ch in txt)''')

# 2) _fit 用实测宽度
a = s.index("    def _fit(self, name, typ, mark):")
b = s.index("    def rect(self):")
new_fit = '''    def _fit(self, name, typ, mark):
        cn = CN.get((self.en, name), "")
        cnw = text_w(cn, 10.5) + (10 if cn else 0)
        typw = text_w(typ, 9.5) + 8
        avail = BOX_W - 36 - 14 - cnw - typw
        size = 12.0
        while size > 8.5 and text_w(name, size) > avail:
            size -= 0.5
        if text_w(name, size) > avail:
            while len(name) > 4 and text_w(name + "…", size) > avail:
                name = name[:-1]
            name = name + "…"
        return (name, typ, mark, size, cn, text_w(name, size))

'''
s = s[:a] + new_fit + s[b:]

# 3) 两个渲染器都吃 7 元组
s = s.replace("for name, typ, mark, size, cn, namew in self.rows:",
              "for name, typ, mark, size, cn, namew in self.rows:")
s = s.replace("for name, typ, mark, size, cn, namew in b.rows:",
              "for name, typ, mark, size, cn, namew in b.rows:")

io.open(p, "w", encoding="utf-8").write(s)
print("measure patched:", "def text_w" in s)
