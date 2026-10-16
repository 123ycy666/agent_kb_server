"""统一用户表服务 —— dlj_data.t_employee 认证与本地投影同步。

设计（2026-10-14 合并）
----------------------
**权威源在 MySQL**：`dlj_data.t_employee` 是唯一用户表（HR 系统维护，本系统只读）。
**本地投影在 PG**：`app_user` 保留为投影表，解决两个问题：
1. 存量数据兼容 —— document.owner_id / audit_log.user_id 等外键都指向 app_user.id；
   按 username 对齐复用旧投影行，历史数据的归属不变。
2. 权限体系复用 —— RBAC（role / permission / user_role）原样保留，
   投影行挂上角色后，全系统权限判定零改动。

同步时机：每次登录时 upsert（员工信息以 HR 为准，本地不提供修改入口）。

密码：t_employee.login_pwd 为 argon2id，由 security.verify_employee_password 校验；
本系统**不存也不改**员工密码。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_employee_password
from app.models import AppUser


@dataclass
class EmployeeInfo:
    """t_employee 行（只读视图）。"""

    employee_id: int
    login_name: str
    actual_name: str
    email: str | None
    phone: str | None
    administrator_flag: bool
    disabled: bool          # disabled_flag=1
    deleted: bool           # deleted_flag=1


def fetch_employee(mysql_db: Session, login_name: str) -> EmployeeInfo | None:
    """按登录名查员工。已删除（deleted_flag=1）视为不存在。"""
    from sqlalchemy import text

    row = mysql_db.execute(
        text(
            "SELECT employee_id, login_name, actual_name, email, phone, "
            "administrator_flag, disabled_flag, deleted_flag "
            "FROM t_employee WHERE login_name = :name LIMIT 1"
        ),
        {"name": login_name},
    ).mappings().first()
    if row is None or row["deleted_flag"]:
        return None
    return EmployeeInfo(
        employee_id=int(row["employee_id"]),
        login_name=row["login_name"],
        actual_name=row["actual_name"],
        email=row["email"],
        phone=row["phone"],
        administrator_flag=bool(row["administrator_flag"]),
        disabled=bool(row["disabled_flag"]),
        deleted=False,
    )


def authenticate(mysql_db: Session, username: str, password: str) -> EmployeeInfo | None:
    """员工登录认证。

    :return: 认证成功返回员工信息；失败（用户不存在 / 密码错 / 已停用）统一返回 None，
             由调用方返回同一报错，避免账号枚举。
    """
    emp = fetch_employee(mysql_db, username)
    if emp is None or emp.disabled:
        return None
    from sqlalchemy import text

    row = mysql_db.execute(
        text("SELECT login_pwd FROM t_employee WHERE employee_id = :eid LIMIT 1"),
        {"eid": emp.employee_id},
    ).scalar_one_or_none()
    if not row or not verify_employee_password(password, row):
        return None
    return emp



def sync_projection(pg_db: Session, emp: EmployeeInfo) -> AppUser:
    """把员工同步进 PG app_user 投影表（upsert + 角色对齐），返回投影行。

    规则：
    - username 已有投影行 → 复用（存量数据 owner_id 不变），刷新 external_id /
      display_name / status
    - 没有 → 新建投影行
    - 角色：administrator_flag=1 → admin；否则 → user（幂等补齐/修正）
    """
    user = pg_db.execute(
        select(AppUser).where(AppUser.username == emp.login_name)
    ).scalar_one_or_none()

    status = "disabled" if emp.disabled else "active"
    if user is None:
        user = AppUser(
            username=emp.login_name,
            display_name=emp.actual_name,
            external_id=str(emp.employee_id),
            email=emp.email,
            status=status,
        )
        pg_db.add(user)
        pg_db.flush()
    else:
        user.display_name = emp.actual_name
        user.external_id = str(emp.employee_id)
        user.email = emp.email or user.email
        user.status = status
        pg_db.flush()

    # 角色对齐（2026-10-15 RBAC 收敛）：administrator_flag 是 HR 侧权威，
    # 1 → admin，0 → user。权限码集合见 app/core/rbac_config.py 常量矩阵。
    user.role = "admin" if emp.administrator_flag else "user"

    pg_db.commit()
    return user


def count_active_employees(mysql_db: Session) -> int:
    """有效员工数（健康检查 / 启动日志用）。"""
    from sqlalchemy import text

    return int(
        mysql_db.execute(
            text("SELECT COUNT(*) FROM t_employee WHERE deleted_flag = 0 AND disabled_flag = 0")
        ).scalar_one()
    )
