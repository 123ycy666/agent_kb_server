-- 会话人设（2026-10-21，P2）
-- 作用：让每个对话可以另设身份，只对该会话生效。
-- 幂等：重复执行不会报错。内网部署时随其它 db/*.sql 一起跑。

ALTER TABLE t_chat_session ADD COLUMN IF NOT EXISTS persona_name   varchar(32);
ALTER TABLE t_chat_session ADD COLUMN IF NOT EXISTS persona_prompt text;

COMMENT ON COLUMN t_chat_session.persona_name   IS '会话人设名（如「参谋长」），空=未设置';
COMMENT ON COLUMN t_chat_session.persona_prompt IS '会话人设正文（追加在系统提示尾部，不得覆盖红线）';
