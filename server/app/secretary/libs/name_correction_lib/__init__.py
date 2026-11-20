# -*- coding: utf-8 -*-
"""名称纠错 / 名称归一库 —— 中文实体名的确定性处理。

    from app.secretary.libs.name_correction_lib import Corrector, configure

    configure(words)                                 # ★ 先注入词表（词面在库）
    c = Corrector()                                  # 全局建一次，常驻复用

首次构造会加载约 2 万条词典索引（200~300ms），
建议全局只建一个实例、常驻复用（无状态，可安全并发）。

两个数据源：
- **词面**（单位类型 / 台区类型 / 停用词 / 前后缀）：`configure()` 注入，来自
  `app/secretary/lex_words.py`（库表 `cfg.t_nc_word`）。不注入就是空的，
  此时只做错别字层，不做单位/台区归一。
- **名称表**（catalog / typo / pinyin …）：`use_source()` 注入，见
  `app/secretary/lex_source.py`。

⚠️ 两者都必须发生在 `Corrector()` 之前 —— 索引是在构造函数里建的。
"""
from .name_corrector import (
    Correction,
    Corrector,
    NameResolution,
    Resolution,
    configure,
    data_source_name,
    describe_data,
    use_source,
)

__all__ = [
    "Corrector",
    "Correction",       # correct() 的返回：保守纠错
    "Resolution",       # resolve() 的返回：名称归一
    "NameResolution",
    "describe_data",
    "configure",        # 注入词表（单位/台区类型、停用词、前后缀）
    "use_source",       # 注入数据库数据源（名称表）
    "data_source_name",
]
