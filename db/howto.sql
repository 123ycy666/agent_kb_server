-- 做法库参数表（2026-11-11 办公线）
-- 正文仍在 document（doc_type='howto'），这里只放能直接喂生成器的结构化参数。
CREATE TABLE IF NOT EXISTS howto_task (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        BIGINT       NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    task          VARCHAR(64)  NOT NULL,
    product       VARCHAR(16)  NOT NULL DEFAULT 'text',
    applies       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    fmt           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    outline       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    length        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    tone          TEXT         NOT NULL DEFAULT '',
    check_items   JSONB        NOT NULL DEFAULT '[]'::jsonb,
    enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    version_no    INTEGER      NOT NULL DEFAULT 1,
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_howto_task_task ON howto_task (task);
