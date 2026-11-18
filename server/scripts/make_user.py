#!/usr/bin/env python3
"""建 / 改 / 删一个本地账号（骨架交付用）。

密码存 bcrypt 到 app_user.password_hash，走登录里 LOCAL_LOGIN_ENABLED 那条通道；
不依赖 HR 的 t_employee。

    set LOCAL_LOGIN_ENABLED=true
    set DATABASE_URL=postgresql+psycopg://kb:kb_dev_pwd@127.0.0.1:5433/skeleton
    python server/scripts/make_user.py skeleton 你的密码 --role admin
    python server/scripts/make_user.py skeleton --delete
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import AppUser


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("username")
    p.add_argument("password", nargs="?", default="")
    p.add_argument("--role", default="admin", choices=["admin", "user"])
    p.add_argument("--delete", action="store_true")
    a = p.parse_args()

    db = SessionLocal()
    try:
        row = db.execute(select(AppUser).where(AppUser.username == a.username)).scalar_one_or_none()

        if a.delete:
            if row is None:
                print("没有这个账号：%s" % a.username)
                return 1
            db.delete(row)
            db.commit()
            print("已删除：%s" % a.username)
            return 0

        if not settings.local_login_enabled:
            print("LOCAL_LOGIN_ENABLED 没打开 —— 建出来的账号登不进去")
            return 1
        if len(a.password) < 6:
            print("密码至少 6 位")
            return 1

        if row is None:
            row = AppUser(username=a.username, display_name=a.username, role=a.role, status="active")
            db.add(row)
        row.password_hash = hash_password(a.password)
        row.role = a.role
        row.status = "active"
        db.commit()
        print("已保存：%s  role=%s" % (a.username, a.role))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
