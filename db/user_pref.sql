-- 个人偏好（2026-10-21）：每个员工一套自己的写作文风/格式
-- 三级覆盖顺序：站点包 doc_defaults.yml（产品/单位默认） → 本表（个人） → 本次请求参数
CREATE TABLE IF NOT EXISTS t_user_pref (
    id          bigserial PRIMARY KEY,
    user_id     bigint NOT NULL UNIQUE,
    prefs       jsonb  NOT NULL DEFAULT '{}'::jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  t_user_pref       IS '个人偏好：文风与文档格式（覆盖站点默认）';
COMMENT ON COLUMN t_user_pref.prefs IS '白名单键：body_font_cn/body_pt/line_spacing_pt/amount_unit/date_format/sign_off';
