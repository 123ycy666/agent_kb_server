# -*- coding: utf-8 -*-
"""配置：读合并项目根目录的 .env（与知识库后端同一份），模型通道跟随 MODEL_CHANNEL。

2026-10-19 合流（本地秘书线 + 平台线）说明：
- .env 位置：优先合并项目根目录（server/ 的上一级），其次 app/secretary/.env；
  SECRETARY_ENV 仍可强制指定。
- MySQL 连接：新键 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD，
  兼容旧键 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD（两边 .env 都认）。
- 模型端点：不再单独读 LLM_BASE_URL/LLM_API_KEY，改由 MODEL_CHANNEL（local/online）
  派生；旧键仍可作为覆盖。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), '.env')
_LOCAL_ENV = os.path.join(HERE, '.env')
ENV_PATH = os.environ.get('SECRETARY_ENV',
                          _ROOT_ENV if os.path.exists(_ROOT_ENV) else _LOCAL_ENV)

# 只用于「.env 文件整个不存在」时的提示文案；真正的必填校验在 DB 建好之后做
# （两套键名都认，不在这里二选一）。
REQUIRED = ('MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_USER', 'MYSQL_PASSWORD')

_TEMPLATE_HINT = (
    '\n[配置] 缺少 %s\n'
    '       这个文件不在仓库里（含密码，刻意不入库），需要自己建一份：\n'
    '\n'
    '         cp .env.example .env        # Linux / macOS\n'
    '         copy .env.example .env      # Windows\n'
    '\n'
    '       然后把 6 项填上：%s\n'
    '       各项说明见 .env.example 里的注释。\n'
)


def load_env(path=ENV_PATH):
    if not os.path.exists(path):
        raise SystemExit(_TEMPLATE_HINT % (path, ' / '.join(REQUIRED)))
    d = {}
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        # 剥离行尾注释：仅当 '#' 前有空白时才算注释，避免截断含 '#' 的密码
        v = re.split(r'\s+#', v, maxsplit=1)[0]
        d[k.strip()] = v.strip()
    return d


ENV = load_env()

# MySQL 连接：新键 MYSQL_* 优先，兼容旧键 DB_*
DB = {
    'host': ENV.get('MYSQL_HOST') or ENV.get('DB_HOST') or '',
    'port': int(ENV.get('MYSQL_PORT') or ENV.get('DB_PORT') or 3306),
    'user': ENV.get('MYSQL_USER') or ENV.get('DB_USER') or '',
    'password': ENV.get('MYSQL_PASSWORD') or ENV.get('DB_PASSWORD') or '',
    'db': 'dlj_data',
}
SCHEMA = 'dlj_data'

_missing_db = [k for k in ('host', 'user', 'password') if not DB[k]]
if _missing_db:
    raise SystemExit('[配置] %s 里 MySQL 连接项为空：%s\n'
                     '       填 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD'
                     '（或旧键 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD）。'
                     % (ENV_PATH, ' / '.join(_missing_db)))

# 对话记录/词典库名（与知识库后端 .env 保持一致）
AGENT_DB = (os.environ.get('SECRETARY_AGENT_DB')
            or ENV.get('MYSQL_DB_AGENT') or ENV.get('SECRETARY_AGENT_DB') or 'agent_data')

# 纠错词典的数据源：
#   db   = 读数据库（默认）。词典在 agent_data 的 6 张 t_nc_* 表里，
#          **服务启动时一次性全量读进内存**，之后纠错全程不查库。
#   file = 读 libs/name_correction_lib/data/ 下的 CSV（零依赖，供独立部署/回退用）。
# 建表见 tools/lexicon_schema.sql，导入见 tools/import_lexicon.py。
# 环境变量和 .env 都可以配，环境变量优先。
LEXICON_SOURCE = (os.environ.get('SECRETARY_LEXICON')
                  or ENV.get('SECRETARY_LEXICON') or 'db').strip().lower()

# 纠错词典库：与对话记录库同一台 MySQL、同一账号，只是库名不同。
# 键名必须用 database 而不是 db —— pymysql 1.1+ 已把 db 标为废弃，传进去会刷警告。
LEXICON_DB_NAME = (os.environ.get('SECRETARY_LEXICON_DB')
                   or ENV.get('SECRETARY_LEXICON_DB') or AGENT_DB)
LEXICON_DB = {
    'host': DB['host'],
    'port': DB['port'],
    'user': DB['user'],
    'password': DB['password'],
    'database': LEXICON_DB_NAME,
}

# 词典热更新：每隔这么久（秒）查一次版本指纹，发现词典被改过就重建纠错引擎。
# 这样人工 UPDATE 完词典不用重启服务。设 0 = 关闭探测（只在启动时加载一次）。
LEXICON_RECHECK_SEC = int(os.environ.get('SECRETARY_LEXICON_RECHECK')
                          or ENV.get('SECRETARY_LEXICON_RECHECK') or '60')

# 多轮上下文：同一场会话里，把最近 N 轮问答带上作为模型上下文。
# 作用：理解「那全市的呢」这类省略主语的问法；问题没提时间/地点时沿用上一轮口径。
# 设 0 = 关闭（每次提问完全独立，与 2.0 之前行为一致）。
# 2026-09-21 由 3 调到 5：长会话里「上一轮」常常超出 3 轮就丢失指代，
# 5 轮的上下文增量可接受（每轮最多带 400 字回答，见 qa_log.load_context）。
HISTORY_TURNS = int(os.environ.get('SECRETARY_HISTORY_TURNS', '5'))

# 规则文档（2026-09-24）：全系统**只维护这一篇**规则 —— 判断标准 + 比较 + 问法归一 + 回答方向
# （skills/判据规则.md，同一篇同步进知识库）。普通问答不带任何技能 ID，每次先按
# 「本轮问句 + 上一轮问句」检索它，命中的切片直接当本次的判断标准与回答方向交给模型；
# 技能调用把整篇注入，两边的规则永远一致。
# ★ 为什么由服务先检索、而不是等模型自己查：实测让模型"要判断时自己查"会时查时不查，
#   不查就给不出判断。SECRETARY_RULES=0 可关掉（回到模型自己按需 kb_search）。
RULE_DOC = (os.environ.get('SECRETARY_RULE_DOC') or '判据规则').strip()
# 每次注入几条规则切片。2026-09-24 由 3 调到 4：零节（分析框架）相关度高时会排在最前，
# 只取 3 条容易把场景段（如第二十节的审批类必答四段）挤掉 —— 框架和场景要同时在场。
RULE_TOP_K = int(os.environ.get('SECRETARY_RULE_TOP_K', '4'))
# qtype（分析/查数）的判定线：预检索到的规则相关度达到它就算分析题。
# ★ 它只决定**要不要额外生成分析页面**，不影响回答本身 —— 分流由模型按《规则》第零节做。
ANALYSIS_SCORE = float(os.environ.get('SECRETARY_ANALYSIS_SCORE', '0.8'))
RULES_ON = (os.environ.get('SECRETARY_RULES') or '1').strip().lower() \
    not in ('0', 'false', 'no', 'off')

# ══════════════════════════════════════════════════════════
#  模型通道（与知识库后端同一套 .env：MODEL_CHANNEL = local | online）
# ══════════════════════════════════════════════════════════
# 问答必须用真实模型，没有 mock 档：
#   local  = 内网自建（vLLM / Xinference / Ollama，OpenAI 兼容）
#   online = 线上 API（阿里云百炼 compatible-mode）
# 旧键 LLM_BASE_URL / LLM_API_KEY / SECRETARY_MODEL 仍可作为覆盖。
MODEL_CHANNEL = (os.environ.get('MODEL_CHANNEL') or ENV.get('MODEL_CHANNEL') or 'local').strip().lower()
if MODEL_CHANNEL not in ('local', 'online'):
    raise SystemExit(
        '[配置] MODEL_CHANNEL=%s 不支持：问答服务需要真实模型（local / online）。\n'
        '       知识库后端可以用 mock 演示，但问数 agent 不行 —— 没有模型就没有答案。' % MODEL_CHANNEL)

if MODEL_CHANNEL == 'online':
    LLM_BASE = (os.environ.get('LLM_BASE_URL') or ENV.get('LLM_BASE_URL')
                or ENV.get('ONLINE_LLM_BASE_URL') or '').rstrip('/')
    LLM_KEY = (os.environ.get('LLM_API_KEY') or ENV.get('LLM_API_KEY')
               or ENV.get('ONLINE_LLM_API_KEY') or '')
    MODEL = (os.environ.get('SECRETARY_MODEL') or ENV.get('ONLINE_LLM_MODEL')
             or ENV.get('LLM_MODEL_ANSWER') or ENV.get('LLM_MODEL_ROUTER') or 'deepseek-v4.1-flash-1017').strip()
else:  # local
    LLM_BASE = (os.environ.get('LLM_BASE_URL') or ENV.get('LLM_BASE_URL')
                or ENV.get('LOCAL_LLM_BASE_URL') or '').rstrip('/')
    LLM_KEY = (os.environ.get('LLM_API_KEY') or ENV.get('LLM_API_KEY')
               or ENV.get('LOCAL_LLM_API_KEY') or '')
    MODEL = (os.environ.get('SECRETARY_MODEL') or ENV.get('LOCAL_LLM_MODEL')
             or ENV.get('LLM_MODEL_ANSWER') or ENV.get('LLM_MODEL_ROUTER') or 'deepseek-chat').strip()

if not LLM_BASE:
    raise SystemExit(
        '[配置] MODEL_CHANNEL=%s 但 LLM 端点为空：\n'
        '       local  → 填 LOCAL_LLM_BASE_URL\n'
        '       online → 填 ONLINE_LLM_BASE_URL\n'
        '       各项说明见 .env.example。' % MODEL_CHANNEL)
if MODEL_CHANNEL == 'online' and not LLM_KEY:
    raise SystemExit(
        '[配置] MODEL_CHANNEL=online 但 ONLINE_LLM_API_KEY 为空。\n'
        '       线上 API 必须填 Key；本地 vLLM/Ollama 一般无需 Key，可切 local。')

# 公网通道（online）候选清单：挑自网关 GET /models 里适合本场景的。
# group 用于前端分组；note 写清"什么时候该用它"。
# ⚠️ 这只是 online 档的候选，最终对外清单见文件末尾的 MODELS（要按通道过滤）。
_MODELS_ONLINE = [
    # 通义千问 · 3.8（最新一代）
    {'id': 'qwen3.8-max', 'label': 'Qwen3.8-Max', 'group': '通义千问', 'note': '旗舰；工具调用与长回答最稳（贵）'},
    {'id': 'qwen3.8-max-0902', 'label': 'Qwen3.8-Max-0902', 'group': '通义千问', 'note': '旗舰快照版（版本固定，便于复现）'},
    {'id': 'qwen3.8-flash', 'label': 'Qwen3.8-Flash', 'group': '通义千问', 'note': '同代轻量；默认已切 DeepSeek-V4.1-Flash'},
    {'id': 'qwen3.8-27b', 'label': 'Qwen3.8-27B', 'group': '通义千问', 'note': '开源尺寸，可内网自部署对照'},
    # 通义千问 · 3.7 / 3.6 / 3.5
    {'id': 'qwen3.7-max', 'label': 'Qwen3.7-Max', 'group': '通义千问（上一代）', 'note': '上一代旗舰'},
    {'id': 'qwen3.7-plus', 'label': 'Qwen3.7-Plus', 'group': '通义千问（上一代）', 'note': '上一代均衡档'},
    {'id': 'qwen3.7-flash', 'label': 'Qwen3.7-Flash', 'group': '通义千问（上一代）', 'note': '上一代轻量'},
    {'id': 'qwen3.6-max-preview', 'label': 'Qwen3.6-Max-Preview', 'group': '通义千问（上一代）', 'note': '更早一代旗舰，可做降级备用'},
    {'id': 'qwen3.6-plus', 'label': 'Qwen3.6-Plus', 'group': '通义千问（上一代）', 'note': '上一代均衡档；实测比 flash 慢（曾 42s/6步）'},
    {'id': 'qwen3.6-flash', 'label': 'Qwen3.6-Flash', 'group': '通义千问（上一代）', 'note': '⚠️ 本网关于 2026-10-19 实测 HTTP 400，不可用'},
    {'id': 'qwen3.5-plus', 'label': 'Qwen3.5-Plus', 'group': '通义千问（上一代）', 'note': ''},
    {'id': 'qwen3.5-flash', 'label': 'Qwen3.5-Flash', 'group': '通义千问（上一代）', 'note': ''},
    # 专用
    {'id': 'qwen3-max', 'label': 'Qwen3-Max', 'group': '通义千问（专用）', 'note': '更早旗舰'},
    {'id': 'qwen3-coder-plus', 'label': 'Qwen3-Coder-Plus', 'group': '通义千问（专用）', 'note': '代码向，写 SQL 可能更强'},
    {'id': 'qwen3-vl-plus', 'label': 'Qwen3-VL-Plus', 'group': '通义千问（多模态）', 'note': '能看图，适合带图表的问法'},
    {'id': 'qwen3.5-omni-plus', 'label': 'Qwen3.5-Omni-Plus', 'group': '通义千问（多模态）', 'note': '全模态'},
    # 其它厂商
    {'id': 'deepseek-v4.1-flash-1017', 'label': 'DeepSeek-V4.1-Flash-1017', 'group': 'DeepSeek', 'note': '默认；简单题 6.7s、复杂题 25.7s，回答比 qwen-flash 更全'},
    {'id': 'deepseek-v4-pro', 'label': 'DeepSeek-V4-Pro', 'group': 'DeepSeek', 'note': '强推理，适合复杂口径推导'},
    {'id': 'deepseek-v4-flash', 'label': 'DeepSeek-V4-Flash', 'group': 'DeepSeek', 'note': 'DeepSeek 轻量档'},
    {'id': 'deepseek-v3.2', 'label': 'DeepSeek-V3.2', 'group': 'DeepSeek', 'note': ''},
    {'id': 'kimi-k3', 'label': 'Kimi-K3', 'group': 'Kimi', 'note': '长上下文强'},
    {'id': 'kimi-k2-thinking', 'label': 'Kimi-K2-Thinking', 'group': 'Kimi', 'note': '思考型；本服务关掉了 thinking，慎用'},
    {'id': 'kimi-k2.7-code', 'label': 'Kimi-K2.7-Code', 'group': 'Kimi', 'note': '代码向'},
    {'id': 'glm-5.2', 'label': 'GLM-5.2', 'group': '智谱 GLM', 'note': ''},
    {'id': 'glm-5', 'label': 'GLM-5', 'group': '智谱 GLM', 'note': ''},
    {'id': 'MiniMax/MiniMax-M3', 'label': 'MiniMax-M3', 'group': 'MiniMax', 'note': ''},
    {'id': 'MiniMax/MiniMax-M2.7', 'label': 'MiniMax-M2.7', 'group': 'MiniMax', 'note': ''},
]

# 2026-10-16 逐个实测：当前公网网关未部署下面 8 个（选中返 404/400，且会导致整轮
# 问答失败、连历史记录都不留）。换网关 / 网关上线新模型后请重新实测再更新本清单。
# 实测方法：逐个 POST /api/ask {"question":"你好","model":"<id>"}，看返回的 qa_id 是否为数字。
_MODELS_UNAVAILABLE = {
    'qwen3.6-max-preview',   # HTTP 404
    'qwen3-max',             # HTTP 404
    'qwen3-coder-plus',      # HTTP 404
    'deepseek-v4-flash',     # HTTP 404
    'deepseek-v3.2',         # HTTP 404
    'kimi-k2-thinking',      # HTTP 404
    'MiniMax/MiniMax-M3',    # HTTP 400
    'MiniMax/MiniMax-M2.7',  # HTTP 400
}

# ⚠️ 对外模型清单必须**按通道区分**（2026-10-16 修）：
#   online → 公网网关实测可用的（上面那份，剔除不可用的）；
#   local  → 内网自建（vLLM / Xinference / Ollama）**实际部署**的那几个，
#            由 LOCAL_MODELS 指定（逗号分隔）；不配则只有 LOCAL_LLM_MODEL(=MODEL)。
#   为什么必须区分：内网模型服务上**根本没有** qwen/kimi/glm/MiniMax 这些公网模型，
#   如果清单不区分，内网部署时下拉框里会列出一堆选了就报错的模型。
if MODEL_CHANNEL == 'local':
    _local_ids = [x.strip() for x in
                  (os.environ.get('LOCAL_MODELS') or ENV.get('LOCAL_MODELS') or MODEL).split(',')
                  if x.strip()]
    MODELS = [{'id': _mid, 'label': _mid, 'group': '内网自建',
               'note': '内网已部署模型'} for _mid in _local_ids]
else:
    MODELS = [m for m in _MODELS_ONLINE if m['id'] not in _MODELS_UNAVAILABLE]

MODEL_IDS = [m['id'] for m in MODELS]

# ---------------------------------------------------------------------------
# 知识库（企业级本地知识库系统 · 自建 RAG）
#
# 2026-10-13 起**不再使用阿里云百炼知识库**；2026-10-15 单进程合并后，
# 检索改为**进程内直调**（app/secretary/kb_adapter.py），不再有 HTTP 往返。
# kb_client.py 已随合并退役；下面 KB_BASE/USER/PASSWORD 只为「连外部知识库」的
# 独立部署场景保留，进程内模式下不参与连接。
# ---------------------------------------------------------------------------
KB_BASE = (os.environ.get('SECRETARY_KB_BASE')
           or ENV.get('SECRETARY_KB_BASE') or 'http://127.0.0.1:8200/api/v1').strip()
# 内部服务令牌（与知识库后端 .env 的 INTERNAL_SERVICE_TOKEN 一致）；
# 预警等「无登录身份的外部定时任务」也用它放行，见 app/api/v1/agent.py。
KB_INTERNAL_TOKEN = (os.environ.get('INTERNAL_SERVICE_TOKEN')
                     or ENV.get('INTERNAL_SERVICE_TOKEN') or '').strip()
# 兼容保留：连外部知识库（非本地）时才需要账号密码登录
KB_USER = (os.environ.get('SECRETARY_KB_USER')
           or ENV.get('SECRETARY_KB_USER') or '').strip()
KB_PASSWORD = (os.environ.get('SECRETARY_KB_PASSWORD')
               or ENV.get('SECRETARY_KB_PASSWORD') or '').strip()
# 问答检索哪些库：新知识库用的是**数字 id**（逗号分隔）。默认 [5, 3]：
#   5 = 秘书问答规则（判据 / 口径）　3 = 公司知识库（业务背景）
KB_IDS = [int(x) for x in
          (os.environ.get('SECRETARY_KB_IDS') or ENV.get('SECRETARY_KB_IDS') or '5,3')
          .replace(' ', '').split(',') if x.strip().isdigit()]

# AI 只能查这些表（留空 = 用业务字典里登记的全部）。
# 六项指标已统一到这两张预计算表：快照（本期值）+ 对比（同比/环比）。
TABLES = [x for x in os.environ.get('SECRETARY_TABLES', '').replace(' ', '').split(',') if x]

# 单题时间预算（秒）：超过就停止继续查数，用已查到的内容作答，并在回答里说明还缺什么。
# 实测「不指定对象」的问题（「这单钱都花哪了」）会一直翻表到客户端超时（600 s 无响应）。
QUESTION_BUDGET_S = float(ENV.get('SECRETARY_QUESTION_BUDGET') or '240')

# 数字闸：回答里的金额必须能在本轮 SQL 结果（含 summary、成对和差比）里找到出处，
# 找不到就重问一次。关掉设 SECRETARY_NUMBER_GUARD=0。
NUMBER_GUARD_ON = (ENV.get('SECRETARY_NUMBER_GUARD') or '1') not in ('0', 'false', 'False')

# 系统提示从 ai_data.ai_prompt 注入（取 IS_new=1 的最新版），不在代码里写死。
# 列表里不存在的 key 会自动跳过，所以可以先接好槽位、等内容补上就自动生效。
#   answer_agent   回答规则（含那 10 条必须遵守）
#   business_rules 共享业务规则（deleted_flag / 单位 / 层级 / 时间处理）
#   sql_plan_rules ←【待他们新增】「先拆解任务、再一次性发出互不依赖的查询」这条约束
#                    （原 `question_splitter` 目前是“原样透传、不做补全”，不能直接用）
#   work_order_rules 缺陷工单取数口径（默认排除草稿 work_status=0）
#                    2026-09-23 新增，行在 ai_prompt id=40；改这条规则只改库，不改代码
PROMPTS = [x for x in os.environ.get('SECRETARY_PROMPTS',
    'answer_agent,business_rules,sql_plan_rules,work_order_rules,caliber_rules').replace(' ', '').split(',') if x]

# 上游「信息补全」工作流应用（含机构名自动纠错，如 凉水供电所→两水供电所）。
# 已不在问答链路里，但 tools_app.py 还在用它，保留以免打断那个工具。
COMPLETION_APP_ID = os.environ.get('SECRETARY_COMPLETION_APP', 'e207644fd37247af957b7fbc613e6efc')
COMPLETION_URL = (LLM_BASE.split('/compatible-mode')[0] +
                  '/api/v1/apps/' + COMPLETION_APP_ID + '/completion')

# 是否在系统提示**最前面**加一段「规则优先级声明」（内容见 agent.RULES_PRIORITY，默认开）。
# 为什么需要：system 里的通用规则会和 user 段【统计范围】撞车 —— 典型是
# `business_rules【时间处理】`写着"未指定年份时默认使用当前年份"，而代码在多轮会话里
# 会按"沿用上一轮"给出**别的**年份；原文任何地方都没声明谁优先。
# ★ 这是**纯新增**，不删改任何既有规则，出问题设 0 即恢复原状。
RULES_PRIORITY = (os.environ.get('SECRETARY_RULES_PRIORITY') or '1').strip().lower() \
    not in ('0', 'false', 'no', 'off')

# 工具调用步数上限：0 = 不设上限（由模型自己决定什么时候收手）。
# ★ 2026-10-08 撤回默认值 14：Fred 明确「不要随意加上限，优化的是过程，想办法让他快点，
#   不是限制他」。要提速就改链路（页面移出关键路径、减少每轮上下文、换更快的思考档），
#   不许靠砍模型能查多少次来提速。保留这个开关只为压测用，默认必须是不限。
MAX_STEPS = int(os.environ.get('SECRETARY_MAX_STEPS', '0'))

# HTTP 接口的**防呆上限**：历史对话类接口（/api/sessions、/api/history）在「要求全部返回」时，
# 一次最多给这么多条，超出的截断，并在响应里带 truncated=true 让调用方知道被截了。
# 为什么要它：这两个接口现在默认就是「全部返回」，历史数据涨到几万条时一次塞进一个 JSON 响应，
# 服务端序列化与前端渲染都会很难受。要更多就用 limit+offset 分页取。
API_MAX_ROWS = int(os.environ.get('SECRETARY_API_MAX_ROWS', '5000'))

# 说明：原「知识库文件台账 / 本地原文件目录 / 单库模式 / 异步上传默认」这几个开关
# （SECRETARY_KB_FILES、SECRETARY_KB_SINGLE、SECRETARY_KB_ASYNC_DEFAULT）已随
# **管理面（/api/kb/**）一并移除**（2026-10-13）—— 上传、预览、删除都改用
# 新知识库系统自己的 Web 界面，本服务只保留「检索」这一个动作。

# L2 会话摘要（2026-09-22 新增）：把一场会话的早期轮次压成一段话，续聊时注进提示，
# 这样"翻出一场很久以前的会话继续聊"时，模型不至于只知道最近的几轮。
#   SESSION_SUMMARY  ：总开关，0 = 关（既不生成也不注入）
#   SUMMARY_EVERY    ：未摘要的问答轮数攒够多少才触发一次
#   SUMMARY_MODEL    ：摘要用哪个模型；留空 = 用主模型（想省钱可填 flash 级）
#   SUMMARY_MAX_CHARS：摘要正文长度上限（字）。★ 与 SUMMARIZE_SYSTEM 里写的 150 对齐。
#                     2026-09-22 从 300 降到 150：实测 300 字时摘要会堆到 6~7 条并列断言，
#                     在 user_content 里占比过大，反而稀释了本轮问题与【统计范围】。
SESSION_SUMMARY = (os.environ.get('SECRETARY_SESSION_SUMMARY') or '1').strip().lower() \
    not in ('0', 'false', 'no', 'off')
SUMMARY_EVERY = int(os.environ.get('SECRETARY_SUMMARY_EVERY', '5'))
SUMMARY_MODEL = (os.environ.get('SECRETARY_SUMMARY_MODEL') or '').strip()
SUMMARY_MAX_CHARS = int(os.environ.get('SECRETARY_SUMMARY_MAX_CHARS', '150'))
# 最近几轮**无论有没有被摘要覆盖，都原样带进【对话历史】**。
# 为什么要这个：摘要再准也是压缩过的，"那售电量呢"这种紧跟上一轮的追问，
# 看原话比看摘要可靠得多。实测发现：只靠摘要时，紧邻几轮的明细会被完全替掉
# （history_injected=0），追问的精细度会下降。
# ★ 这两个"始终原样带"的轮次**不会再被写进摘要**（见 session_summary._summarize），
#   否则同一件事在提示里出现两遍：白烧 token，还让摘要里的旧口径更容易干扰本轮。
SUMMARY_KEEP_RECENT = int(os.environ.get('SECRETARY_SUMMARY_KEEP_RECENT', '2'))
# 会话**总轮数**达到这个值，才值得把摘要注进提示。
# 为什么需要：HISTORY_TURNS 默认 5，短会话（≤5 轮）的 history 本来就把全场带上了，
# 再注入一段摘要纯属重复 —— 实测短会话里摘要与【对话历史】内容几乎完全重叠。
# 设 0 = 不设门槛（老行为）。
SUMMARY_MIN_TURNS = int(os.environ.get('SECRETARY_SUMMARY_MIN_TURNS', '6'))

SQL_MAX_ROWS = 200
SQL_TIMEOUT_MS = 25000
HTTP_TIMEOUT = 120

# 知识库检索的超时（秒）：检索只是取切片，正常 <1 秒，不该占满 HTTP_TIMEOUT。
# 实测两个"卡到超时、一次库都没查"的问句（「今年立的项目都干到哪一步了？」600s、
# 「我新报的单子能过吗？」420s），就是被这类下游调用拖住的。给它短超时：失败按"没检索到"处理，
# 不影响作答；也免得把整轮问答拖死。
KB_TIMEOUT = int(os.environ.get('SECRETARY_KB_TIMEOUT', '15'))

# 分析题的「分析页面」：额外调一次模型把结论渲染成单页 HTML 数据大屏。
# **2026-10-15 起默认关闭**（Fred：正常流程不需要这个生成页面，把它关掉）。
# 需要时设 SECRETARY_PAGE=1 打开；关掉后 /api/ask 不再返回 page_html，回答完全不受影响。
PAGE_ON = (os.environ.get('SECRETARY_PAGE') or ENV.get('SECRETARY_PAGE') or '0').strip().lower() in ('1', 'true', 'yes', 'on')
# 生成分析页面用的模型：页面是纯前端代码活，用快档就够，实测比旗舰快一倍（48.7s→23.4s）
PAGE_MODEL = (os.environ.get('SECRETARY_PAGE_MODEL') or ENV.get('SECRETARY_PAGE_MODEL') or 'qwen3.8-flash').strip()
