@echo off
chcp 65001 >nul
cd /d "%~dp0secretary"
set SECRETARY_HOST=0.0.0.0
rem 换自建知识库后分数刻度变了（实测 0.165-0.856，与百炼刻度区间重叠，任何阈值都分不开
rem 分析题/查数题）。部署值取 0：有规则命中就注入分析框架+交卷自检，分流由模型按《判据规则》做。
rem 代码里的默认值还是 0.8，不设这一行会让分析题退化成“念数据”。
set SECRETARY_ANALYSIS_SCORE=0
echo 启动项目管理秘书（局域网可访问）...
echo 下面会打印“同事访问”的地址，把那行连同端口发给同事即可。
echo 若同事打不开，多半是 Windows 防火墙没放行 8200 端口（见 局域网访问说明.md）。
py -X utf8 server.py
pause
