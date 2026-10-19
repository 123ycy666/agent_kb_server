-- ============================================================================
-- 智能问数 · 对话记录表（PostgreSQL 版）
--
-- 为什么有这个文件
--   平台线 2026-10-15 把对话记录从 MySQL `agent_data` 迁到 PG `ad_new` 时，
--   是在**服务器上手工迁移**的 —— 仓库里没有这 5 张表的 DDL。
--   结果：线上库早就有了，全新库部署时 `qa_log` 一查就 UndefinedTable，
--   问答落库 / 历史会话 / 统计 / 会话摘要全部不可用（/health 里 session_summary=false）。
--   2026-10-19 合流时补上，来源 = MySQL `agent_data` 的 `SHOW CREATE TABLE` 等价翻译。
--
-- 用法
--   psql -h 127.0.0.1 -p 5433 -U kb -d ad_new -f db/chat_tables.sql
--   幂等：全是 IF NOT EXISTS，可重复执行，不会删数据。
--
-- 翻译约定（MySQL → PG）
--   bigint AUTO_INCREMENT → bigserial            （保留 id 自增语义，兼容 pgdb 的 RETURNING id）
--   tinyint               → smallint             （代码里按 0/1 比较，不用 boolean）
--   mediumtext/longtext   → text
--   json                  → jsonb
--   datetime              → timestamp
--   ON UPDATE CURRENT_TIMESTAMP → 去掉（PG 没有；代码在 UPDATE 里自己写 update_time/NOW()）
--   反引号 / ENGINE / CHARSET / COLLATE → 去掉（PG 用库级编码，建库时已是 UTF8）
-- ============================================================================

-- ── 1. 会话表 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_chat_session (
    id                  bigserial PRIMARY KEY,
    session_id          varchar(64)  NOT NULL,
    user_id             bigint,
    user_code           varchar(64),
    ai_model            varchar(50),
    title               varchar(255),
    channel             varchar(20)  NOT NULL DEFAULT 'web',
    client_ip           varchar(64),
    system_prompt_hash  char(32),
    model_params        jsonb,
    msg_count           integer      NOT NULL DEFAULT 0,
    total_tokens        bigint       NOT NULL DEFAULT 0,
    total_cost_ms       bigint       NOT NULL DEFAULT 0,
    status              smallint     NOT NULL DEFAULT 1,
    end_reason          varchar(20),
    deleted_flag        smallint     NOT NULL DEFAULT 0,
    create_time         timestamp    NOT NULL DEFAULT now(),
    last_msg_at         timestamp,
    ended_at            timestamp,
    update_time         timestamp    NOT NULL DEFAULT now(),
    summary             text,
    summary_upto_seq    integer      NOT NULL DEFAULT 0,
    summary_time        timestamp
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_session_id ON t_chat_session (session_id);
CREATE INDEX IF NOT EXISTS idx_chat_session_user_time   ON t_chat_session (user_id, create_time);
CREATE INDEX IF NOT EXISTS idx_chat_session_status_time ON t_chat_session (status, create_time);
COMMENT ON TABLE t_chat_session IS '智能体会话表';

-- ── 2. 消息表 ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_chat_message (
    id                bigserial PRIMARY KEY,
    session_id        bigint       NOT NULL,          -- 关联 t_chat_session.id
    chat_session_id   varchar(64)  NOT NULL,          -- 冗余业务会话 ID，统计免 JOIN
    seq_no            bigint       NOT NULL,
    direction         smallint     NOT NULL,
    sender_type       smallint     NOT NULL,          -- 1用户 2AI 3系统 4人工坐席
    sender_id         bigint,
    reply_to_id       bigint,
    msg_type          smallint     NOT NULL DEFAULT 1,
    content           text,
    media_url         varchar(512),
    meta_json         jsonb,
    model             varchar(50),
    prompt_tokens     integer,
    completion_tokens integer,
    total_tokens      integer,
    latency_ms        integer,
    first_token_ms    integer,
    finish_reason     varchar(30),
    gen_status        smallint     NOT NULL DEFAULT 0,
    send_status       smallint     NOT NULL DEFAULT 0,
    error_msg         varchar(1000),
    deleted_flag      smallint     NOT NULL DEFAULT 0,
    create_time       timestamp    NOT NULL DEFAULT now(),
    update_time       timestamp    NOT NULL DEFAULT now()
);
-- ⚠️ 这条唯一键是 qa_log 的重试依据：并发写同一 seq_no 会撞键，撞了重试一次。
CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_message_session_seq ON t_chat_message (session_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_chat_message_session_time ON t_chat_message (session_id, create_time);
CREATE INDEX IF NOT EXISTS idx_chat_message_chatseq      ON t_chat_message (chat_session_id, seq_no);
CREATE INDEX IF NOT EXISTS idx_chat_message_create_time  ON t_chat_message (create_time);
CREATE INDEX IF NOT EXISTS idx_chat_message_reply        ON t_chat_message (reply_to_id);
COMMENT ON TABLE t_chat_message IS '智能体消息表';

-- ── 3. 问数执行明细表（一轮问答一行）────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_chat_query_trace (
    id                  bigserial PRIMARY KEY,
    message_id          bigint       NOT NULL,        -- 关联 t_chat_message.id（AI 回答那条）
    chat_session_id     varchar(64)  NOT NULL,
    raw_question        varchar(1000),
    fixed_question      varchar(1000),
    completed_question  varchar(1000),
    user_profile        varchar(500),
    name_fix_used       smallint     NOT NULL DEFAULT 0,
    completion_used     smallint     NOT NULL DEFAULT 0,
    period_type         varchar(20),
    period_key          varchar(10),
    time_text           varchar(40),
    time_is_default     smallint     NOT NULL DEFAULT 0,
    place               varchar(100),
    place_is_default    smallint     NOT NULL DEFAULT 0,
    model               varchar(50),
    db_query_count      integer      NOT NULL DEFAULT 0,
    sql_query_count     integer      NOT NULL DEFAULT 0,
    kb_query_count      integer      NOT NULL DEFAULT 0,
    steps               integer      NOT NULL DEFAULT 0,
    no_db_query         smallint     NOT NULL DEFAULT 0,
    unverified          smallint     NOT NULL DEFAULT 0,
    is_gap              smallint     NOT NULL DEFAULT 0,
    elapsed_ms          integer      NOT NULL DEFAULT 0,
    namefix_ms          integer      NOT NULL DEFAULT 0,
    completion_ms       integer      NOT NULL DEFAULT 0,
    loop_ms             integer      NOT NULL DEFAULT 0,
    feedback            smallint,
    feedback_note       varchar(500),
    feedback_time       timestamp,
    deleted_flag        smallint     NOT NULL DEFAULT 0,
    create_time         timestamp    NOT NULL DEFAULT now(),
    update_time         timestamp    NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_query_trace_message ON t_chat_query_trace (message_id);
CREATE INDEX IF NOT EXISTS idx_query_trace_session  ON t_chat_query_trace (chat_session_id);
CREATE INDEX IF NOT EXISTS idx_query_trace_create   ON t_chat_query_trace (create_time);
CREATE INDEX IF NOT EXISTS idx_query_trace_flags    ON t_chat_query_trace (unverified, is_gap, feedback);
CREATE INDEX IF NOT EXISTS idx_query_trace_scope    ON t_chat_query_trace (place, period_key);
COMMENT ON TABLE t_chat_query_trace IS '智能问数执行明细表';

-- ── 4. 执行步骤明细表（一轮问答多步）──────────────────────────────────
CREATE TABLE IF NOT EXISTS t_chat_trace_step (
    id            bigserial PRIMARY KEY,
    trace_id      bigint      NOT NULL,               -- 关联 t_chat_query_trace.id
    step_no       integer     NOT NULL,
    step_kind     varchar(20),                        -- namefix/scope/complete/tool/nudge/answer/blocked
    round_no      integer,
    tool_name     varchar(50),
    input_text    text,
    output_text   text,
    sql_text      text,
    result_rows   integer,
    cost_ms       bigint,
    model_ms      bigint,
    status        smallint    NOT NULL DEFAULT 0,
    error_msg     varchar(500),
    deleted_flag  smallint    NOT NULL DEFAULT 0,
    create_time   timestamp   NOT NULL DEFAULT now(),
    update_time   timestamp   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trace_step_trace ON t_chat_trace_step (trace_id, step_no);
CREATE INDEX IF NOT EXISTS idx_trace_step_tool  ON t_chat_trace_step (tool_name, create_time);
CREATE INDEX IF NOT EXISTS idx_trace_step_kind  ON t_chat_trace_step (step_kind, create_time);
COMMENT ON TABLE t_chat_trace_step IS '智能问数执行步骤明细表';

-- ── 5. 智能体用户表（qa_log 用 uid → t_user.id 的映射；平台账号在 app_user，这张只做兼容）──
CREATE TABLE IF NOT EXISTS t_user (
    id           bigserial PRIMARY KEY,
    uid          varchar(64) NOT NULL,
    username     varchar(50) NOT NULL,
    role         varchar(20),
    email        varchar(100),
    phone        varchar(20),
    source       varchar(20) NOT NULL DEFAULT 'local',
    source_id    varchar(64),
    status       smallint    NOT NULL DEFAULT 0,
    remark       varchar(200),
    deleted_flag smallint    NOT NULL DEFAULT 0,
    create_time  timestamp   NOT NULL DEFAULT now(),
    update_time  timestamp   NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_t_user_uid ON t_user (uid);
CREATE INDEX IF NOT EXISTS idx_t_user_username ON t_user (username);
CREATE INDEX IF NOT EXISTS idx_t_user_source   ON t_user (source, source_id);
COMMENT ON TABLE t_user IS '智能体用户表（兼容层；平台登录账号在 app_user）';
