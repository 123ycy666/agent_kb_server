# 服务端部署说明

> ## ⚠️ 这是 2.0 版，比 1.0 多一件事：**要先建记录库**
>
> 2.0 新增「对话记录 + 多轮上下文 + 历史会话」功能，问答会自动写入一个 **`agent_data`** 库。
>
> **升级步骤：**
> 1. 上传下面 8 个文件（或整个目录覆盖），**别覆盖 `.env`**。
> 2. 重启服务：`sudo systemctl restart secretary`。
> 3. 自检：`curl -s http://127.0.0.1:8200/health` → 应有 `"qa_log": true`。
>
> **记录库已经建好了**（`agent_data`，6 张表 + 2 个测试账号，就在同一台 MySQL 上），
> **不用再执行建表 SQL**。建表脚本 `agent_data_建表.sql` 留作参考：
> 换服务器 / 重建环境时才需要跑一次。
>
> ### 只上传这 8 个文件就够（其余 53 个完全没变）
>
> ```
> 【新增】
>   .env.example                        配置项说明（可选，方便以后查）
>   secretary/qa_log.py                 ★核心：落库 + 历史查询
>   secretary/给同事的接口对接说明.md       ← 注意：在 secretary/ 同级的根目录
>
> 【覆盖】
>   secretary/agent.py                  ★核心：多轮上下文 + 落库调用
>   secretary/server.py                 ★核心：3 个新接口
>   secretary/config.py                 新增 AGENT_DB / HISTORY_TURNS
>   secretary/time_scope.py             ★核心：本轮没说时沿用上一轮口径
>   DEPLOY.md                           本文档
> ```
>
> `__pycache__` / `*.pyc` 是运行时生成的，**不要传**。
> 验证方式：见「八、2.0 新增：对话记录库」。

## 一、上传什么

整个 `deploy_server2.0/` 目录（约 3.8 MB），传到你服务器的任意目录，例如 `/opt/secretary/`。

```
deploy_server2.0/
├── .env                  ← 【必须】数据库 + 模型凭据，含密码，别传到公开位置
├── .env.example          ← 配置项说明（可选项都在里面）
├── secretary/            ← 【必须】服务代码
│   ├── server.py         ← 入口（问答 + 历史会话接口）
│   ├── agent.py / config.py / semantic.py / time_scope.py / name_fix.py
│   ├── qa_log.py         ← 【2.0 新增】对话记录落库 + 历史查询
│   ├── tools_db.py / tools_kb.py / tools_asr.py / gaps.py / report.py
│   ├── libs/name_correction_lib/   ← 名称纠错与归一引擎（含 7 个 CSV 词典）
│   └── static/           ← 问答页面
├── start.sh              ← Linux 启动脚本
├── start.bat             ← Windows 启动脚本
├── install_service.sh    ← 一键装 systemd 常驻服务
└── 给同事的接口对接说明.md  ← 可直接转发给调用方
```

不需要 `docs/`、`skills/`、`verify/`、`output/` —— 那些是本地开发和测试用的。
`output/` 目录服务会自己建（缺口记录落盘）。

## 二、服务器装什么

**只要 Python 3.9+ 和一个包**：

```bash
# 1) Python（一般都自带，没有就装）
python3 --version            # 要 >= 3.9
yum install -y python3       # CentOS / 龙蜥
apt install -y python3       # Ubuntu / Debian

# 2) 唯一的三方依赖
python3 -m pip install pymysql
```

全部依赖就这一个。拼音是自己离线导的表，不装 pypinyin。
**不需要** MySQL 客户端——用 pymysql 纯 Python 驱动连库。

装完先自检一下能不能连上库和模型：

```bash
cd /opt/secretary/secretary
python3 -X utf8 -c "
import config, pymysql, json, urllib.request
c = pymysql.connect(host=config.DB['host'], port=config.DB['port'], user=config.DB['user'],
                    password=config.DB['password'], database='dlj_data', connect_timeout=10)
print('库 OK')
req = urllib.request.Request(config.LLM_BASE + '/models',
                             headers={'Authorization': 'Bearer ' + config.LLM_KEY})
print('模型 OK，共', len(json.loads(urllib.request.urlopen(req, timeout=20).read())['data']), '个')
"
```

## 三、启动

**端口是 8200**，可以用环境变量改：`SECRETARY_PORT=8300`。

### Linux

> **⚠️ 先看这一条：这个服务必须"常驻"，不能靠手动启动。**
>
> 用 `bash start.sh` 前台跑起来，**SSH 窗口一关、或会话超时，进程就会被系统杀掉**，
> 同事立刻调不通 —— 典型表现就是「昨天还能用，今天点开链接打不开，要重新启动一次」。

**对外提供服务，正确做法是装成 systemd 服务（一条命令）：**

```bash
cd /opt/secretary
chmod +x start.sh install_service.sh
sudo bash install_service.sh        # 开机自启 + 崩溃自动拉起 + 与 SSH 会话无关
```

装完就**再也不用管**：服务器重启会自动起来，进程挂了 5 秒内自动重启。

---

临时试跑（**只在调试时用**）：

```bash
bash start.sh          # 前台，报错直接看得见（关掉窗口就停）
bash start.sh -d       # 后台，但服务器重启后不会自启，仍需手动再跑一次
```

启动成功后窗口会打印：

```
model = qwen3.8-max
本机访问：http://127.0.0.1:8200
同事访问：http://<服务器IP>:8200      ← 把这一行发给同事
```

### Windows 服务器

双击 `start.bat`（同样会设好 `0.0.0.0` 并打印同事访问地址）。

### 常用运维命令（装完 install_service.sh 之后）

```bash
systemctl status secretary           # 看服务状态
systemctl restart secretary          # 重启（改完代码或 .env 后执行）
systemctl stop secretary             # 停止
journalctl -u secretary -f           # 看实时日志
tail -f server.log                   # 看程序输出日志
bash install_service.sh uninstall    # 卸载（停服务 + 取消开机自启）
```

`install_service.sh` 会自动生成 `/etc/systemd/system/secretary.service`
（`WorkingDirectory` 指向脚本所在目录，`Restart=always` 崩溃自动拉起），
**不需要你手写 systemd 配置文件**。

## 四、放行 8200 端口（**最容易漏的一步**）

服务本身没问题、同事却连不上，九成是这里。

```bash
# firewalld（CentOS / 龙蜥 / 红旗）
firewall-cmd --add-port=8200/tcp --permanent && firewall-cmd --reload
firewall-cmd --list-ports

# ufw（Ubuntu / Debian）
ufw allow 8200/tcp && ufw status

# 查看端口到底有没有在听
netstat -tlnp | grep 8200        # 要看到 0.0.0.0:8200，不是 127.0.0.1:8200
```

**如果是阿里云 / 腾讯云等云服务器，还要去控制台的「安全组」里放行 8200 入方向**——
系统防火墙放行了、安全组没放，一样连不上。

自检：在服务器上跑 `curl -s http://127.0.0.1:8200/health`

```json
{"ok": true, "model": "qwen3.8-max", "name_fix": true}
```

**三个字段都要看，不要只看第一个：**

| 字段 | 含义 |
|---|---|
| `ok` | 服务活着（能应答） |
| `model` | 当前默认模型 |
| `name_fix` | **纠错/归一是否生效** |

`name_fix` 为 `false` 时会附一个 `name_fix_reason` 说明原因。常见是
`libs/name_correction_lib/data/` 没跟着传上去（那 8 个 csv 是两万条真实台区名录，
**不在仓库里，必须随部署包一起传**）。缺了它服务照跑、问答照答，
**但错字纠正、简称补全、地点识别会静默失效**，很难发现。


## 五、两台机器之间的网络

- **同一个内网**：同事直接用 `http://<服务器内网IP>:8200/`
- **不在同一个内网**：需要服务器有公网 IP（或走公司 VPN）。公网暴露的话，
  见下面「六、安全性」——这个服务**没有登录校验**，任何人都能调。

## 六、安全性（部署前必须知道）

1. **服务没有任何鉴权**。只要端口可达，谁都能调用问答接口（虽然数据库是只读的）。
   建议：**只在内网开放**，或防火墙里限定只允许同事的网段访问：
   ```bash
   firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.1.0/24" port port="8200" protocol="tcp" accept'
   ```
2. **`.env` 里有数据库密码和模型 Key**，文件权限收紧：
   ```bash
   chmod 600 /opt/secretary/.env
   ```
3. **数据库连接是只读的**（`tools_db.py` 里做了表白名单 + 只允许 SELECT），
   但代码本身不防注入式的越权——它靠的是白名单机制。
4. 服务是**单进程多线程**（Python 标准库 http.server）。单次问答 5~30 秒，
   **并发能力有限**，适合几个人用，不适合当生产网关。要扛并发得上 gunicorn/uwsgi + Flask/FastAPI 重写外层。

## 七、排查表

| 现象 | 原因 | 怎么办 |
|---|---|---|
| 同事连不上、一直转圈 | 防火墙 / 安全组没放行 | 第四节 |
| `Connection refused` | 服务没起来，或绑的是 127.0.0.1 | 看有没有 `0.0.0.0:8200`；确认 `SECRETARY_HOST=0.0.0.0` |
| 启动报 `ModuleNotFoundError: pymysql` | 没装依赖 | `python3 -m pip install pymysql` |
| 启动报 `.env` 找不到 | `.env` 没传或位置不对 | `.env` 要和 `secretary/` 平级 |
| 能访问但答「服务异常」 | 库或模型连不上 | 跑第二节那个自检脚本 |
| 换了网络后同事打不开 | 服务器 IP 变了 | 重新看启动窗口打印的地址 |
| 关掉窗口就断 | 前台进程 | 用 `bash start.sh -d` 或 systemd |
| **`/health` 里 `qa_log: false`** | **记录库没建 / 连不上** | **见下面第八节**；此时问答正常，只是不留记录 |
| **`/api/sessions` 返回空** | 记录库刚建、还没人问过 ／ `qa_log` 是 false | 先问一句再看 |

---

## 八、2.0 新增：对话记录库

### 8.1 这是干什么的

每次问答会自动往 `agent_data` 库写：

| 表 | 一行代表什么 |
|---|---|
| `t_chat_session` | 一场会话 |
| `t_chat_message` | 一条消息（一问一答算 2 条） |
| `t_chat_query_trace` | 一次问答的口径与质量（纠错命中 / 时间地点 / 查库次数 / 是否可信 / 用户评价） |
| `t_chat_trace_step` | 问答内部的每一步（纠错 / 补全 / 工具调用 / SQL / 作答） |

有了它，问答页面之外还能对外提供**历史对话列表**和**历史对话记录**接口（见《给同事的接口对接说明.md》）。

### 8.2 建库（一次性）

```bash
mysql -h 47.121.183.81 -u <账号> -p < agent_data_建表.sql
```

脚本会：
- 建 `agent_data` 库（**排序规则固定 `utf8mb4_general_ci`**，与 `dlj_data` 一致，否则跨库 JOIN 会报 `1267`）
- 建 6 张表
- 预置 2 个测试账号（`PROV-FIN-001` / `STATION-HEAD-001`）

> 脚本最后有一条**故意报错**的语句（验证唯一约束真的拦得住重复消息），
> 看到 `Duplicate entry '1-1' for key 'uk_session_seq'` **是正常的**。

**幂等**：脚本可以重复执行（表会 DROP 重建，测试账号靠唯一键不会插成 4 条）。
但**注意会清掉已有记录** —— 生产上重跑前先备份。

### 8.3 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `SECRETARY_AGENT_DB` | `agent_data` | 记录写到哪个库 |
| `SECRETARY_QA_LOG` | 开 | 设 `0` 则整个记录功能关闭（服务照跑，不留记录） |
| `SECRETARY_HISTORY_TURNS` | `5` | **多轮上下文**：同一会话带最近 N 轮问答。设 `0` = 关闭，回到「每次提问完全独立」 |

用的是**同一台 MySQL、同一个账号**，只是库名不同，所以 `.env` 不用改也能用。

### 8.4 关掉记录功能

```bash
SECRETARY_QA_LOG=0 bash start.sh -d
```

用途：记录库还没建好、或临时不想写库时。关掉后 `/health` 里 `qa_log` 为 `false` 并附原因。

### 8.5 自检

```bash
# 1) 服务侧
curl -s http://127.0.0.1:8200/health        # 要有 "qa_log": true

# 2) 问一句，再看历史
curl -s -X POST http://127.0.0.1:8200/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"环潭供电所的线损率","uid":"PROV-FIN-001"}' --max-time 90
curl -s "http://127.0.0.1:8200/api/sessions?uid=PROV-FIN-001"

# 3) 记录库侧
mysql -h 47.121.183.81 -u <账号> -p -e "
SELECT COUNT(*) FROM agent_data.t_chat_session;
SELECT id,uid,username,role FROM agent_data.t_user;"
```

**关键：落库失败不会影响问答。** 记录库连不上时，问答照常返回，
只是响应里 `qa_id` 为 `null`、`/health` 里 `qa_log` 为 `false`，日志里会有一行
`[qa_log] 落库失败（不影响回答）：…`。

---

## 九、知识库：只读检索（2026-10-13 管理面已移除）

> ⚠️ **本服务不再有知识库管理接口**：`/api/kb/**` 与 `/kb` 控制台页面已删除，
> 阿里云百炼 SDK、AK/SK、`WORKSPACE_ID` / `DEFAULT_INDEX_ID` 全部不再需要。
> 建库 / 上传 / 预览 / 标签 / 冲突处理请用**知识库系统自己的 Web 界面**（`http://47.121.183.81/`）。

本服务与知识库只剩**一条链路：检索**——`secretary/kb_client.py` 调 `POST /api/v1/search`
拿回切片，答案仍由本服务的模型生成（**不用知识库生成的答案**）。

### 9.1 配置（一次性）

知识库的对接账号由知识库管理员在「用户管理」里分配。写进部署目录的 `.env`：

```ini
SECRETARY_KB_BASE=http://47.121.183.81/api/v1   # ⚠️ 必须公网 IP，用域名/127.0.0.1 会 502
SECRETARY_KB_USER=integration
SECRETARY_KB_PASSWORD=<管理员分配>               # 不配 = 检索必失败
SECRETARY_KB_IDS=5,3                            # 检索哪些库（数字 id）：5=秘书问答规则、3=公司知识库
```

> ★ 这几个键**环境变量优先、其次读 `.env`**（`.env` 里写就生效）。
> 想在 systemd 里覆盖，加 `Environment=SECRETARY_KB_IDS=5,3` 后 `daemon-reload` + `restart`。

### 9.2 建表：不需要

原 `tools/apply_kb_file_schema.py`（`t_kb_file` / `t_kb_file_page` / `t_kb_upload_task`）
与 `tools/kb_sync.py` 已随管理面删除。**本服务不再往 agent_data 写任何知识库相关表**。
（`t_chat_session` 的 `summary` 三列仍由会话摘要使用，见 10.4。旧表留着不影响运行。）

### 9.3 验证

```bash
# ① 检索连通性：/health 里 kb 应为 true，并带 kb_source / kb_ids
curl -s http://127.0.0.1:8200/health
# {"ok":true,…,"kb":true,"kb_source":"http://47.121.183.81/api/v1","kb_ids":[5,3]}

# ② 端到端：问一个需要判据的问题，响应 trace 里应出现 kb_search 且能取到切片
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"question\":\"项目执行进度异常的判断标准是什么\"}" \
  http://127.0.0.1:8200/api/ask | python3 -c "import sys,json;d=json.load(sys.stdin);print([t['tool'] for t in d['trace']])"
```

> 排障：`kb:false` 时看 `kb_reason`（多为密码错 / 网络不通 / `KB_BASE` 用了域名）。

---

## 十、会话摘要（L2）与相关开关

> ⚠️ 本节原含「单库收口 / 异步上传」两小节，已随知识库管理面**一并移除**（2026-10-13）。

### 10.1 L2 会话摘要（长会话也能记住）

**默认就是开的**，不用配。后台 daemon 线程异步生成，**不阻塞问答**。

原来「继续对话」只带最近 5 轮、每轮回答只留前 400 字 —— 翻出一场很长的旧会话继续聊，
更早的内容模型是空白。L2 把更早的轮次压成一段 ≤150 字的「上下文交接条」补上：

```
user_content = 本轮问题
             +【本场会话摘要】   ← 更早轮次的浓缩（L2 新增，**要过两道闸**，见下）
             +【对话历史】       ← 最近 5 轮原话（其中最近 2 轮始终保留原话）
             +【统计范围】+【名称归一说明】+【补全参考】+ 用户画像
```

**两道注入闸**（2026-09-22 加）：

| 闸 | 条件 | 为什么 |
|---|---|---|
| ① | `history_turns=0` | 调用方写 0 就是"本轮**完全**不要记忆"，摘要不能绕过它溜进来 |
| ② | 会话总轮数 < `SECRETARY_SUMMARY_MIN_TURNS`（默认 6）| 短会话的 history 已覆盖全场，注入是纯重复 |

> 闸门只影响**注入**，不影响**生成**。响应里的 `summary_skip` 会写明为什么没注入，排障先看它。

★ **摘要只写"还没过期的关注对象与固定叫法"，三样东西一律不写**（2026-09-22 收紧，理由见下）：
① 时间范围与统计主体 —— 那是【统计范围】的活，摘要再写一份只会和它打架；
② 任何结论、判断或状态（"未查到""未生成工单""处于草稿状态""数据缺失已改用累计"…）——
   这类话**没有数字**，躲得过"不许写数字"的纪律，却会被模型当成现成答案而不再查数；
③ 过程叙述（"后续轮次中又增加了…"）。

> **为什么要收紧**：旧版要求写"关注对象 + 已确认口径 + **结论性事实**"，实测生成出来的摘要
> 变成"对象清单 + 口径枚举 + 时效断言"的堆叠 —— 例如"统计主体**先后使用过**全市和两水供电所"、
> "包括**两个**时间范围"、"工单**尚在**实施中"。续聊时这些和本轮的【统计范围】打架，
> 把回答带偏。收紧后重新生成 9 场：**口径枚举 0 场、时效断言 0 场，字数从平均 136 降到 43**。

**相关开关**（都在 systemd 单元里加 `Environment=`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SECRETARY_SESSION_SUMMARY` | `1` | 总开关；`0` = 既不生成也不注入 |
| `SECRETARY_SUMMARY_EVERY` | `5` | 未摘要的轮数攒够多少才触发一次（**不是每轮都抽**，省 token）|
| `SECRETARY_SUMMARY_KEEP_RECENT` | `2` | 最近几轮**始终原样带**（保证追问的精细度）。★ 这几轮**不会再被写进摘要**，避免同一件事在提示里出现两遍 |
| `SECRETARY_SUMMARY_MIN_TURNS` | `6` | 会话**总轮数**达到这个值才注入摘要。短会话（≤5 轮）的 history 本来就覆盖全场，注入纯属重复。设 `0` = 不设门槛 |
| `SECRETARY_SUMMARY_MODEL` | 空 | 摘要用哪个模型；留空 = 同主模型（想省钱可填 flash 级）|
| `SECRETARY_SUMMARY_MAX_CHARS` | `150` | 摘要正文长度上限（字）★ 与提示词里写的 150 对齐 |

### 10.2 相关开关一览

| 变量 | 默认 | 加在哪 |
|---|---|---|
| `SECRETARY_SESSION_SUMMARY` | `1` | 会话摘要总开关 |
| `SECRETARY_SUMMARY_EVERY` | `5` | 摘要触发阈值（轮）|
| `SECRETARY_SUMMARY_KEEP_RECENT` | `2` | 最近几轮始终带原话（且不再写进摘要）|
| `SECRETARY_SUMMARY_MIN_TURNS` | `6` | 会话总轮数达到才注入摘要（`0`=不设门槛）|
| `SECRETARY_SUMMARY_MODEL` | 空 | 摘要模型（空=同主模型）|
| `SECRETARY_SUMMARY_MAX_CHARS` | `150` | 摘要长度上限（与提示词对齐）|

> ⚠️ **知识库检索配置（`SECRETARY_KB_*`）既读环境变量、也读 `.env`**（见 9.1）；
> 而上面这几个 `SECRETARY_SUMMARY_*` **写在 `.env` 里不生效** ——
> 只能走 **systemd 单元里的 `Environment=`**（`config.py` 读的是 `os.environ`）。
> 改完必须 `systemctl daemon-reload` 再 `restart`。

### 10.3 验证

```bash
# ① 能力字段（kb 的连通性检查见 9.3）
curl -s http://127.0.0.1:8200/health
# {"ok":true,…,"kb":true,"name_fix":true,"qa_log":true,
#  "session_summary":true,"session_summary_source":"mysql:agent_data"}

# ② 会话摘要：同一 session_id 连问 6 轮以上，第 6 轮起响应里 summary_used=true
```

启动日志里应能看到会话摘要那一行：

```
[启动] 会话摘要就绪（mysql:agent_data，每 5 轮触发一次，本次补跑 N 场）
```

### 10.4 四个坑

1. ★ **必须 `daemon-reload` + `restart`** —— 若在 systemd 里加了 `Environment=`（如 `SECRETARY_SUMMARY_*`），
   只 `restart` 不会让新环境变量生效。
   复核：`systemctl show secretary -p Environment | tr ' ' '\n' | grep -i 'SUMMARY\|KB'`
2. **摘要是异步的** —— 回答响应当轮 `summary_queued=true` 只表示"已投递"，
   摘要文本要等下一次提问才会被注入（不要以为当轮就该有）。
3. **知识库地址必须用公网 IP**（`http://47.121.183.81/api/v1`）——
   用域名或 `127.0.0.1` 会落到 nginx 默认站 → **502**，`/health` 里 `kb:false`。
4. **摘要生成的模型调用是额外成本** —— 每 `SUMMARY_EVERY` 轮一次；
   会话量大了想省钱，把 `SECRETARY_SUMMARY_MODEL` 指到便宜模型上。

---

## 十一、4.4 新增：系统提示词的「规则优先级」声明

> 接口没变、也不需要建表。**只有一处新配置**，其余是提示词内容层面的调整。

### 11.1 为什么要加

`business_rules【时间处理】` 写着「**未指定年份时默认使用当前年份**」，而代码在多轮会话里
会按「沿用上一轮」给出**别的**年份，并把结果放进 user 段的【统计范围】。
**两处规则撞车，而原文任何地方都没声明谁优先** —— 模型只能自己猜，表现就是"回答被带偏"。

### 11.2 做了什么

| 改动 | 位置 | 说明 |
|---|---|---|
| 在 system **最前面**加一段【规则优先级】 | **代码** `agent.RULES_PRIORITY` | 纯新增，不删任何原有规则 |
| 新开关 `SECRETARY_RULES_PRIORITY` | `secretary/config.py` | 默认 `1`；设 `0` 即恢复原状 |
| 修 `business_rules【时间处理】` 的自相矛盾表述 | **`ai_data.ai_prompt`** 新增一版 | 见 11.3 |

★ **注意措辞**：优先级 ① 写的是「**本轮问题 +【统计范围】+【名称归一说明】**」，
**不是「用户原话」** —— `user_content` 第一行是**代码纠错（名称/字面纠错）+ 时间地点补全之后**的问题，
提示词里**没有**原话。别按"以用户原话为准"去理解。

```text
========== 规则优先级（冲突时一律按此顺序，前面覆盖后面） ==========
① 本轮问题 +【统计范围】+【名称归一说明】—— 这三样都由代码确定，**是唯一权威**：
   问题里的时间、地点、名称都不要再改动，不要自己推算日期，不要另立一套口径。
   （问题已按词典纠正过名称，请直接用它的写法，不要再改回去）
② 本轮检索到的业务规则切片（【补全参考】里那些）—— 它**只是参考**。
③ 本 system 里的各节通用规则（下面所有【】开头的小节）—— 与前两条冲突时让位。
④ 【本场会话摘要】与【对话历史】—— 只用来理解"本轮在问什么"，
   **既不是数据来源、也不是结论来源**，里面的数字和结论一律不得直接引用。

冲突时**直接按前者执行**：不要"综合两者"，也不要在回答里出现两个版本。
```

### 11.3 提示词表那一版（**不是代码改动**）

```sql
-- 已在 ai_prompt 里执行过，这里只是记录，便于回滚与在新环境复现
-- 新行 id=39 / version=2 / IS_new=1：business_rules 的【时间处理】里
--   原文：- 年份筛选使用 create_time 范围：create_time >= '年份-01-01' AND create_time < '(年份+1)-01-01'
--   改后：- 年份筛选**按表取不同字段**（见下面【年份字段对照】），不要一律套用 create_time
-- 旧行 id=21 保留、IS_new 置 0
```

**回滚**：

```sql
UPDATE ai_prompt SET IS_new=0 WHERE id=39;
UPDATE ai_prompt SET IS_new=1 WHERE id=21;
```

> ⚠️ `business_rules` 的表头写着「业务规则（**所有Agent通用**）」——
> 若有别的 Agent 也读这个 key，会同时看到这处改动。
> 若只想影响本项目，应给本项目单开一个 key 并加进 `SECRETARY_PROMPTS`。

### 11.4 验证

```bash
# ① 服务起得来、能力字段照旧
curl -s http://127.0.0.1:8200/health

# ② 优先级声明确实在最前面（★ 用探针看"真实 system 段"，别只读代码）
#    期望：第 1 行是 "========== 规则优先级..."，且它在 "===== answer_agent =====" 之前

# ③ 回归：纠错与时间口径
printf '%s' '{"question":"环谈供电所的线损率","history_turns":0}' > /tmp/q.json
curl -s -X POST http://127.0.0.1:8200/api/ask -H 'Content-Type: application/json' \
     --data-binary @/tmp/q.json --max-time 180
#    期望 fixed_question 含「环潭」，回答给出具体线损率
```

> ⚠️ `SECRETARY_RULES_PRIORITY` 也是 `SECRETARY_*` 系列 ⇒ **写在 `.env` 里不生效**，
> 只能走 systemd `Environment=`，改完 `daemon-reload` + `restart`。
