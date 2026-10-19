# ══════════════════════════════════════════════════════════════
#  后端镜像（统一 FastAPI 服务：知识库 API + 问数 agent + 前端页面托管）
#
#  构建上下文：server/ 目录
#  用法见项目根目录 docker-compose.prod.yml
#  ──────────────────────────────────────────────────────────────
#  ⚠️ 为什么不直接把整个仓库拷进来：
#     本镜像只承载后端。前端在本地 `npm run build` 后由 nginx 托管，
#     不进这个镜像（服务器无需装 Node）。
# ══════════════════════════════════════════════════════════════
FROM python:3.11-slim

# 中文与编码：容器默认 locale 可能不是 UTF-8，会导致中文文件名乱码；
# 时区固定为东八区，避免审计日志时间偏移。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai

WORKDIR /app

# ① 先只拷依赖清单并安装 —— 利用镜像层缓存：
#    只改业务代码时，这一层不重建，构建快很多。
# ⚠️ 国内服务器直连 pypi.org 常 DNS 失败/超时 → 默认走阿里云镜像；
#    可用 `--build-arg PIP_MIRROR=...` 覆盖（如清华 https://pypi.tuna.tsinghua.edu.cn/simple）。
ARG PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt ./
RUN pip install --no-cache-dir -i ${PIP_MIRROR} -r requirements.txt

# ② 再拷业务代码（.env 已被 .dockerignore 排除，密钥不进镜像）
COPY . .

# 上传目录占位；运行时由 compose 挂载持久化卷覆盖为 /data/uploads
RUN mkdir -p /data/uploads

EXPOSE 8200

# 默认命令 = 统一后端进程（单进程承载知识库 API + 问数 agent + 页面托管）。
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
