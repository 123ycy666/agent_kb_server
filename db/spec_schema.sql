-- =====================================================================
-- 说明书层（阶段 2）：kb.spec 一族
--   一篇文章 = 一类活怎么做（步骤 / 口径 / 格式 / 样板 / 交付前检查项）
--   配文档即加能力：上传 → 待发布 → 预览 → 发布（60 秒内生效，不重启不发版）
--   归属两级：owner_id 为空 = 全局（管理员设）；非空 = 本人
-- =====================================================================

CREATE TABLE IF NOT EXISTS kb.spec_role (
    code        varchar(40) PRIMARY KEY,           -- 角色代号，如 gongwen / minute / howto
    label       varchar(80) NOT NULL DEFAULT '',    -- 中文名
    description text NOT NULL DEFAULT '',           -- 这个角色干什么
    product     varchar(10) NOT NULL DEFAULT '',    -- 默认产物 docx / xlsx / pptx / 空
    sort_no     integer NOT NULL DEFAULT 100,
    enabled     boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE kb.spec_role IS '说明书角色：上传时必须选，选不到就落资料库（V12）';

CREATE TABLE IF NOT EXISTS kb.spec (
    id           bigserial PRIMARY KEY,
    name         varchar(120) NOT NULL,             -- 目录一行里的「名称」
    role_code    varchar(40) NOT NULL DEFAULT '',   -- 空 = 没选角色 → 落资料库
    space        varchar(16) NOT NULL DEFAULT 'spec',  -- spec=说明书库 / material=资料库
    when_to_use  text NOT NULL DEFAULT '',          -- 什么时候用
    applies      jsonb NOT NULL DEFAULT '[]',        -- 适用词表（数据，不是代码规则）
    product      varchar(10) NOT NULL DEFAULT '',   -- 产物
    owner_id     bigint,                            -- NULL=全局；否则本人
    status       varchar(16) NOT NULL DEFAULT 'pending',  -- pending/published/retired
    body         text NOT NULL DEFAULT '',          -- 当前版正文（读正文不 join）
    check_items  jsonb NOT NULL DEFAULT '[]',       -- 交付前检查项
    outline      jsonb NOT NULL DEFAULT '[]',
    fmt          jsonb NOT NULL DEFAULT '{}',       -- 版式（字体/字号/页边距/落款）
    doc_id       bigint,                            -- 原始文件（kb.document）
    storage_path varchar(500) NOT NULL DEFAULT '',  -- 原文件落盘路径（可下载）
    version_no   integer NOT NULL DEFAULT 1,
    conflict_with jsonb NOT NULL DEFAULT '[]',      -- 并存冲突的其它 spec id
    enabled      boolean NOT NULL DEFAULT true,
    created_by   varchar(64) NOT NULL DEFAULT '',
    updated_by   varchar(64) NOT NULL DEFAULT '',
    published_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_spec_name_owner
    ON kb.spec (name, coalesce(owner_id, 0)) WHERE enabled;
CREATE INDEX IF NOT EXISTS ix_spec_visible ON kb.spec (status, space, enabled);
COMMENT ON TABLE kb.spec IS '说明书：一行一篇，目录由它生成；发布后 60 秒内生效';

CREATE TABLE IF NOT EXISTS kb.spec_ref (
    id          bigserial PRIMARY KEY,
    spec_id     bigint NOT NULL REFERENCES kb.spec(id) ON DELETE CASCADE,
    ref_name    varchar(120) NOT NULL,              -- 正文里《书名号》里的名字
    ref_spec_id bigint,                             -- 解析到的说明书；解析不到为 NULL
    missing     boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (spec_id, ref_name)
);
COMMENT ON TABLE kb.spec_ref IS '引用：正文《书名号》自动成引用（V8）';

CREATE TABLE IF NOT EXISTS kb.spec_version (
    id         bigserial PRIMARY KEY,
    spec_id    bigint NOT NULL,
    version_no integer NOT NULL,
    name       varchar(120) NOT NULL DEFAULT '',
    body       text NOT NULL DEFAULT '',
    meta       jsonb NOT NULL DEFAULT '{}',
    saved_by   varchar(64) NOT NULL DEFAULT '',
    saved_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (spec_id, version_no)
);
COMMENT ON TABLE kb.spec_version IS '版本：同名上传=新版本，历史正文不可改（H9.1）';

-- 角色预置（页面可改；不选角色的上传落资料库）
INSERT INTO kb.spec_role (code, label, description, product, sort_no) VALUES
    ('gongwen', '公文', '通知/请示/函/纪要/报告这类正式文书', 'docx', 10),
    ('baogao',  '汇报材料', '汇报、总结、方案、简报这类材料', 'docx', 20),
    ('taizhang','台账表格', 'Excel 台账、明细表、汇总表、看板', 'xlsx', 30),
    ('kejian',  '汇报课件', 'PPT 演示、汇报课件', 'pptx', 40),
    ('sudu',    '速读速写', '长文档速读、要点提炼、清单化', '', 50),
    ('jiaodui', '校对纠错', '校对、纠错、合规检查', '', 60),
    ('koujing', '口径', '指标口径与算法（问数线也用，管理员维护）', '', 70)
ON CONFLICT (code) DO NOTHING;
