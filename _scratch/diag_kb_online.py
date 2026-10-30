# -*- coding: utf-8 -*-
"""只读诊断：线上(47.121.183.81) PG 里个人知识库 / 问答轨迹的真实状态。

只用 SELECT；通过 SSH 在 PG 容器里跑 psql，不写任何数据。
密码从仓库根 .env 的 SERVER_SSH_PASSWORD 读，不打印。
见 docs/04-计划/2026-10-29-个人知识库未生效与运行日志排查.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
HOST = '47.121.183.81'
USER = 'root'


def env(name: str) -> str:
    txt = (ROOT / '.env').read_text(encoding='utf-8')
    m = re.search(r'^%s\s*=\s*(.+)$' % re.escape(name), txt, re.M)
    return (m.group(1).strip().strip('"').strip("'") if m else '')


QUERIES = {
    '01_知识库清单': """
SELECT json_agg(t) FROM (
  SELECT k.id, k.name, k.created_by, k.qa_enabled, k.status, k.embedding_model,
         (SELECT count(*) FROM document d WHERE d.kb_id=k.id AND d.is_active) AS docs,
         (SELECT count(*) FROM document_chunk c WHERE c.kb_id=k.id) AS chunks,
         k.created_at::text AS created_at
  FROM knowledge_base k ORDER BY k.id) t;
""",
    '02_我的资料库_文档': """
SELECT json_agg(t) FROM (
  SELECT d.id, d.kb_id, d.filename, d.parse_status, d.chunk_count, d.visibility,
         d.owner_id, d.is_active, d.created_at::text AS created_at
  FROM document d JOIN knowledge_base k ON k.id=d.kb_id
  WHERE k.name='我的资料库' ORDER BY d.id DESC LIMIT 20) t;
""",
    '03_我的资料库_切片向量': """
SELECT json_agg(t) FROM (
  SELECT c.kb_id, c.embedding_model, count(*) AS n,
         count(*) FILTER (WHERE c.embedding IS NULL) AS null_vec,
         min(c.created_at)::text AS first_at, max(c.created_at)::text AS last_at
  FROM document_chunk c JOIN knowledge_base k ON k.id=c.kb_id
  WHERE k.name='我的资料库' GROUP BY c.kb_id, c.embedding_model) t;
""",
    '04_全库向量模型分布': """
SELECT json_agg(t) FROM (
  SELECT embedding_model, count(*) AS n FROM document_chunk GROUP BY 1 ORDER BY n DESC) t;
""",
    '05a_问答表列名': """
SELECT json_agg(t) FROM (
  SELECT column_name, data_type FROM information_schema.columns
  WHERE table_name='t_chat_query_trace' ORDER BY ordinal_position) t;
""",
    '05b_上传任务表': """
SELECT json_agg(t) FROM (
  SELECT table_name FROM information_schema.tables
  WHERE table_schema='public' AND (table_name LIKE '%task%' OR table_name LIKE '%upload%')
  ORDER BY 1) t;
""",
    '05c_用户表列名': """
SELECT json_agg(t) FROM (
  SELECT column_name FROM information_schema.columns
  WHERE table_name='t_user' ORDER BY ordinal_position) t;
""",
    '05d_kb_search_历史步骤': """
SELECT json_agg(t) FROM (
  SELECT trace_id, step_no, tool_name, left(coalesce(input_text,''),110) AS input,
         left(coalesce(output_text,''),80) AS output
  FROM t_chat_trace_step WHERE deleted_flag=0 AND tool_name='kb_search'
  ORDER BY trace_id DESC, step_no LIMIT 15) t;
""",
    '05e_kb2kb3_文档': """
SELECT json_agg(t) FROM (
  SELECT id, kb_id, filename, parse_status, chunk_count, visibility, owner_id, is_active,
         created_at::text AS created_at
  FROM document WHERE kb_id IN (2,3) ORDER BY id DESC LIMIT 20) t;
""",
    '06_最近步骤明细': """
SELECT json_agg(t) FROM (
  SELECT s.trace_id, s.step_no, s.step_kind, s.tool_name, s.cost_ms, s.status,
         left(coalesce(s.input_text,''),90) AS input, left(coalesce(s.output_text,''),90) AS output
  FROM t_chat_trace_step s WHERE s.deleted_flag=0
  ORDER BY s.trace_id DESC, s.step_no LIMIT 40) t;
""",
    '07_个人库属主用户': """
SELECT json_agg(t) FROM (
  SELECT id, uid, name FROM t_user
  WHERE id IN (SELECT created_by FROM knowledge_base WHERE name='我的资料库')) t;
""",
    '08_问答数总览': """
SELECT json_build_object(
  'traces', (SELECT count(*) FROM t_chat_query_trace WHERE deleted_flag=0),
  'steps',  (SELECT count(*) FROM t_chat_trace_step WHERE deleted_flag=0),
  'kb_calls_on', (SELECT count(*) FROM t_chat_query_trace WHERE deleted_flag=0 AND kb_query_count>0),
  'docs_total', (SELECT count(*) FROM document WHERE is_active)) AS j;
""",
    '09_全部文档': """
SELECT json_agg(t) FROM (
  SELECT d.id, d.kb_id, d.filename, d.owner_id, d.visibility, d.parse_status,
         d.chunk_count, d.is_active, d.created_at::text AS created_at
  FROM document d ORDER BY d.id DESC LIMIT 20) t;
""",
    '10_async_task列名': """
SELECT json_agg(t) FROM (
  SELECT column_name FROM information_schema.columns
  WHERE table_name='async_task' ORDER BY ordinal_position) t;
""",
    '11_最近上传任务': """
SELECT json_agg(t) FROM (
  SELECT id, task_type, status, kb_id, created_by, created_at::text AS created_at,
         left(coalesce(error_msg,''),100) AS err
  FROM async_task ORDER BY id DESC LIMIT 15) t;
""",
    '12_最近问答_修正列名': """
SELECT json_agg(t) FROM (
  SELECT id, left(raw_question,50) AS question, kb_query_count, db_query_count, steps,
         model, create_time::text AS create_time
  FROM t_chat_query_trace WHERE deleted_flag=0 ORDER BY id DESC LIMIT 10) t;
""",
    '13_用户表': """
SELECT json_agg(t) FROM (
  SELECT id, username, display_name, role, status FROM app_user ORDER BY id LIMIT 12) t;
""",
    '14_线上SQL是否遵守排除草稿': """
SELECT json_build_object(
  '工单表SQL总数', count(*),
  '带work_status过滤', count(*) FILTER (WHERE input_text LIKE '%work_status%'),
  '带排除草稿(<>0)', count(*) FILTER (WHERE input_text ~ 'work_status\\s*(<>|!=)\\s*0'),
  '带时间条件', count(*) FILTER (WHERE input_text LIKE '%create_time%')) AS j
FROM t_chat_trace_step
WHERE deleted_flag=0 AND tool_name='run_sql' AND input_text LIKE '%t_power_work_order%';
""",
    '15_模型调用次数分布': """
SELECT json_agg(t) FROM (
  SELECT steps, count(*) AS n FROM t_chat_query_trace WHERE deleted_flag=0 GROUP BY steps ORDER BY steps) t;
""",
}


def main() -> int:
    pwd = env('SERVER_SSH_PASSWORD')
    if not pwd:
        print('[X] .env 里没有 SERVER_SSH_PASSWORD')
        return 2
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=pwd, timeout=20,
              banner_timeout=25, auth_timeout=25, look_for_keys=False, allow_agent=False)
    try:
        sql = '\n'.join('\\echo === %s ===\n%s' % (k, v) for k, v in QUERIES.items())
        chan = c.get_transport().open_session()
        chan.set_combine_stderr(True)
        chan.exec_command('docker exec -i kb-postgres psql -U kb -d ad_new -tA -f -')
        chan.sendall(sql.encode('utf-8'))
        chan.shutdown_write()
        out = b''
        while True:
            chunk = chan.recv(65535)
            if not chunk:
                break
            out += chunk
        code = chan.recv_exit_status()
        print(out.decode('utf-8', 'replace'))
        print('[psql exit]', code)
        return 0 if code == 0 else 1
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
