@echo off
chcp 65001 >nul
cd /d "%~dp0secretary"
rem 换自建知识库后分数刻度变了（实测 0.165-0.856，与百炼刻度区间重叠，任何阈值都分不开
rem 分析题/查数题）。部署值取 0：有规则命中就注入分析框架+交卷自检，分流由模型按《判据规则》做。
rem 代码里的默认值还是 0.8，不设这一行会让分析题退化成“念数据”。
set SECRETARY_ANALYSIS_SCORE=0
echo 启动项目管理秘书演示页面...
echo 打开浏览器访问 http://127.0.0.1:8200
py -X utf8 server.py
pause
