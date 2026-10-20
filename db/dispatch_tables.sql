-- ============================================================================
-- 预警与任务流转 · 建表脚本（PostgreSQL / ad_new）
--
-- 生成方式：server/app/models/dispatch.py → output/gen_dispatch_sql.py 自动生成
-- （不要手改；改模型后重跑脚本，保证 DDL 与 ORM 永不脱节）
--
-- 用法：
--   psql -h 127.0.0.1 -p 5433 -U kb -d ad_new -f db/dispatch_tables.sql
--   幂等：全部 IF NOT EXISTS，可重复执行，不删数据。
--
-- 约定：
--   · 时间 timestamptz；JSONB 存明细；不建外键（与本项目其他表一致）
--   · 业务库 dlj_data 只读：这 10 张表全在我们的 PG 库里
-- ============================================================================

CREATE TABLE IF NOT EXISTS alert_scan_run (
	id BIGSERIAL NOT NULL, 
	run_key VARCHAR(64) NOT NULL, 
	trigger_type VARCHAR(16) NOT NULL, 
	cadence VARCHAR(8) NOT NULL, 
	account VARCHAR(64), 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(16) NOT NULL, 
	found_n INTEGER NOT NULL, 
	new_n INTEGER NOT NULL, 
	resolved_n INTEGER NOT NULL, 
	error TEXT, 
	detail JSONB DEFAULT '{}'::jsonb NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_scan_run_key ON alert_scan_run (run_key);

CREATE TABLE IF NOT EXISTS alert_item (
	id BIGSERIAL NOT NULL, 
	alert_type VARCHAR(16) NOT NULL, 
	subtype VARCHAR(32) NOT NULL, 
	level VARCHAR(8) NOT NULL, 
	dedupe_key VARCHAR(220) NOT NULL, 
	scope_type VARCHAR(16) NOT NULL, 
	scope_key VARCHAR(64) NOT NULL, 
	scope_name VARCHAR(128) NOT NULL, 
	object_type VARCHAR(16) NOT NULL, 
	object_key VARCHAR(120) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	detail TEXT, 
	data JSONB DEFAULT '{}'::jsonb NOT NULL, 
	verify_hint JSONB DEFAULT '{}'::jsonb NOT NULL, 
	assignee_role VARCHAR(16), 
	first_seen TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	last_seen TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	seen_times INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	task_id BIGINT, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	close_reason VARCHAR(32), 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	update_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_alert_type CHECK (alert_type IN ('budget','project','review','metric')), 
	CONSTRAINT ck_alert_level CHECK (level IN ('high','mid','low'))
);
CREATE INDEX IF NOT EXISTS idx_alert_scope ON alert_item (scope_key, object_key);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alert_item (status, level, last_seen);
CREATE INDEX IF NOT EXISTS idx_alert_type ON alert_item (alert_type, subtype);
CREATE UNIQUE INDEX IF NOT EXISTS uk_alert_dedupe ON alert_item (dedupe_key);

CREATE TABLE IF NOT EXISTS dispatch_task (
	id BIGSERIAL NOT NULL, 
	task_no VARCHAR(32) NOT NULL, 
	task_type VARCHAR(16) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	requirement TEXT NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	level VARCHAR(8) NOT NULL, 
	alert_id BIGINT, 
	root_task_id BIGINT, 
	parent_task_id BIGINT, 
	scope_type VARCHAR(16), 
	scope_key VARCHAR(64), 
	scope_name VARCHAR(128), 
	biz_type VARCHAR(16), 
	biz_keys JSONB DEFAULT '[]'::jsonb NOT NULL, 
	creator_id BIGINT NOT NULL, 
	creator_name VARCHAR(64) NOT NULL, 
	creator_login VARCHAR(64) NOT NULL, 
	creator_dept_id BIGINT, 
	creator_session_id VARCHAR(64), 
	reply_due_at TIMESTAMP WITH TIME ZONE, 
	due_at TIMESTAMP WITH TIME ZONE, 
	require_plan SMALLINT NOT NULL, 
	verifier_type VARCHAR(16) NOT NULL, 
	verify_rule JSONB DEFAULT '{}'::jsonb NOT NULL, 
	verify_state VARCHAR(16) NOT NULL, 
	last_check_at TIMESTAMP WITH TIME ZONE, 
	escalate_level SMALLINT NOT NULL, 
	overdue_flag SMALLINT NOT NULL, 
	escalated_to BIGINT, 
	archived_flag SMALLINT NOT NULL, 
	closed_at TIMESTAMP WITH TIME ZONE, 
	close_reason VARCHAR(32), 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	update_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_flag SMALLINT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_task_status CHECK (status IN ('draft','open','doing','verify','closed','cancelled')), 
	CONSTRAINT ck_task_verifier CHECK (verifier_type IN ('auto','human'))
);
CREATE INDEX IF NOT EXISTS idx_task_alert ON dispatch_task (alert_id);
CREATE INDEX IF NOT EXISTS idx_task_assignee_due ON dispatch_task (reply_due_at);
CREATE INDEX IF NOT EXISTS idx_task_creator ON dispatch_task (creator_id, create_time);
CREATE INDEX IF NOT EXISTS idx_task_root ON dispatch_task (root_task_id);
-- ⚠️ 索引名不能叫 idx_task_status：PG 的索引名在 schema 内唯一，而 async_task 表
--    上已经有一个同名索引，`IF NOT EXISTS` 会让这条**静默跳过** ⇒ dispatch_task 上
--    这个复合索引根本没建上（2026-10-20 实测发现）。改名为 idx_dispatch_status_due。
CREATE INDEX IF NOT EXISTS idx_dispatch_status_due ON dispatch_task (status, reply_due_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_task_no ON dispatch_task (task_no);

CREATE TABLE IF NOT EXISTS dispatch_task_assignee (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	part VARCHAR(16) NOT NULL, 
	user_id BIGINT, 
	employee_id BIGINT NOT NULL, 
	login_name VARCHAR(64) NOT NULL, 
	name VARCHAR(64), 
	dept_id BIGINT, 
	dept_name VARCHAR(128), 
	role_code VARCHAR(16), 
	state VARCHAR(16) NOT NULL, 
	session_id VARCHAR(64), 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	replied_at TIMESTAMP WITH TIME ZONE, 
	declined_reason VARCHAR(500), 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	update_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_assignee_part CHECK (part IN ('owner','collaborator','cc')), 
	CONSTRAINT ck_assignee_state CHECK (state IN ('pending','accepted','replied','done','declined'))
);
CREATE INDEX IF NOT EXISTS idx_assignee_emp ON dispatch_task_assignee (employee_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS uk_assignee ON dispatch_task_assignee (task_id, employee_id, part);

CREATE TABLE IF NOT EXISTS dispatch_task_event (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	action VARCHAR(32) NOT NULL, 
	actor_id BIGINT, 
	actor_name VARCHAR(64), 
	actor_role VARCHAR(16), 
	from_status VARCHAR(16), 
	to_status VARCHAR(16), 
	content TEXT, 
	payload JSONB DEFAULT '{}'::jsonb NOT NULL, 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_event_task ON dispatch_task_event (task_id, create_time);

CREATE TABLE IF NOT EXISTS dispatch_task_plan (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	version INTEGER NOT NULL, 
	title VARCHAR(200), 
	content TEXT NOT NULL, 
	items JSONB, 
	source VARCHAR(8) NOT NULL, 
	author_id BIGINT, 
	author_name VARCHAR(64), 
	accepted SMALLINT NOT NULL, 
	accepted_by BIGINT, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_plan_kind CHECK (kind IN ('ai_advice','decision','owner_plan','report'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_plan_ver ON dispatch_task_plan (task_id, kind, version);

CREATE TABLE IF NOT EXISTS dispatch_check (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	check_kind VARCHAR(8) NOT NULL, 
	rule JSONB DEFAULT '{}'::jsonb NOT NULL, 
	measured JSONB DEFAULT '{}'::jsonb NOT NULL, 
	passed BOOLEAN, 
	message VARCHAR(500), 
	check_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_check_task ON dispatch_check (task_id, check_time);

CREATE TABLE IF NOT EXISTS dispatch_notice (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT, 
	notice_type VARCHAR(16) NOT NULL, 
	receiver_id BIGINT, 
	receiver_login VARCHAR(64), 
	receiver_name VARCHAR(64), 
	title VARCHAR(200) NOT NULL, 
	content TEXT, 
	channel VARCHAR(16) NOT NULL, 
	sent_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	read_flag SMALLINT NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	dedupe_key VARCHAR(220), 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_notice_rcv ON dispatch_notice (receiver_login, read_flag, sent_at);
CREATE INDEX IF NOT EXISTS idx_notice_task ON dispatch_notice (task_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_notice_dedupe ON dispatch_notice (dedupe_key);

CREATE TABLE IF NOT EXISTS dispatch_attachment (
	id BIGSERIAL NOT NULL, 
	task_id BIGINT NOT NULL, 
	event_id BIGINT, 
	file_name VARCHAR(255) NOT NULL, 
	storage_path VARCHAR(500) NOT NULL, 
	size_bytes BIGINT, 
	md5 VARCHAR(32), 
	uploader_id BIGINT, 
	uploader_name VARCHAR(64), 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_attach_task ON dispatch_attachment (task_id);

CREATE TABLE IF NOT EXISTS alert_chat_link (
	id BIGSERIAL NOT NULL, 
	alert_id BIGINT NOT NULL, 
	employee_id BIGINT NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	create_time TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_alert_chat_session ON alert_chat_link (session_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_alert_chat ON alert_chat_link (alert_id, employee_id);

