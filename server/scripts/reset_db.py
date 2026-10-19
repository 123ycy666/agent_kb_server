"""重置数据库：清空 public schema 并重新初始化。

⚠️ 这会**删除所有数据**（用户、知识库、文档、审计日志全部清空），且不可恢复。
   仅在以下场景使用：

   - 表结构发生不兼容变更（例如本次「权限/标签模型简化」重构把
     `document.visibility` 从 JSONB 改为 varchar、`tags` 从 JSONB 改为数组）
   - 需要一份干净的演示 / 验收环境

用法::

    cd server
    python scripts/reset_db.py          # 交互式二次确认
    python scripts/reset_db.py --yes    # 跳过确认（脚本化调用）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从 server/ 目录之外调用
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.database import engine  # noqa: E402
from app.db_init import init_database  # noqa: E402


def main() -> int:
    if "--yes" not in sys.argv:
        print("⚠️  此操作会清空数据库中的所有数据，且不可恢复。")
        answer = input("确认请输入 YES 后回车：").strip()
        if answer != "YES":
            print("已取消。")
            return 1

    print("→ 删除 public schema …")
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    print("→ 重新建表并写入种子数据 …")
    result = init_database(verbose=False)
    print("✅ 完成：", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
