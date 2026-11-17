# -*- coding: utf-8 -*-
"""生成 20 篇假文档并入库，带上目录/文种/文号/时效/密级（知识库预设数据）。"""
import sys, os, tempfile, json
sys.path.insert(0, r"D:\秘书智能体\server")
import psycopg
import requests
from psycopg.rows import dict_row
from app.secretary import pgdb
from app.core.security import create_access_token

API = "http://127.0.0.1:8200"
TOK = create_access_token(1)
H = {"Authorization": "Bearer " + TOK}

DOCS = [
 ("公文处理办法.md", "综合/公文处理", "办法", "国网办〔2024〕12号", "2024-03-01", "internal",
  "公文处理办法", ["总则：公文是公司对外对内行文的唯一载体，行文须一事一文、主送明确。",
   "种类：决议、决定、命令、公报、公告、通告、意见、通知、通报、报告、请示、批复、议案、函、纪要共十五种。",
   "流程：拟稿→复核→签发→编号→印制→发出；紧急件须标注急缓程度与办理时限。",
   "归档：办结后 30 日内移交档案室，电子件同步保存 PDF/A 格式。"]),
 ("公文格式规范.md", "综合/公文处理", "规范", "国网办〔2023〕7号", "2023-06-01", "public",
  "国网公文格式规范", ["用纸：A4，天头 37mm、订口 28mm；版心 156mm×225mm。",
   "字体：标题二号小标宋，正文三号仿宋_GB2312，行距 28 磅。",
   "层次：一、→（一）→1.→（1），四级不得混用。",
   "成文日期用阿拉伯数字，年份不得省略；印章压成文日期。"]),
 ("请示报告写作要求.md", "综合/公文处理", "通知", "综办〔2025〕2号", "2025-01-15", "internal",
  "关于规范请示与报告写作的通知", ["请示一文一事，不得多头主送；报告中不得夹带请示事项。",
   "标题由发文机关+事由+文种构成，事由用动宾结构，不用书名号。",
   "请示须写明事项背景、拟办意见、需要批复的具体问题。"]),
 ("会议管理实施细则.md", "综合/会议管理", "细则", "综办〔2024〕19号", "2024-07-01", "internal",
  "会议管理实施细则", ["会议分三类：党委会、总经理办公会、专业会；分别由办公室、分管领导组织。",
   "会前 2 日报议题，临时议题须经主要领导同意。",
   "会议纪要 2 个工作日内成文，明确责任单位与时限。"]),
 ("周例会纪要模板.md", "综合/会议管理", "模板", "—", "2025-03-10", "internal",
  "周例会纪要格式", ["抬头：时间、地点、主持、参会、记录、议题。",
   "正文：逐项写议定事项、责任人、完成时限。",
   "结尾：下次会议时间；纪要分发范围。"]),
 ("值班管理规定.md", "综合/值班应急", "规定", "综办〔2023〕11号", "2023-05-20", "internal",
  "值班管理规定", ["值班分日常值班与节假日值班，24 小时在岗。",
   "交接班须双方签字，重要事项须在交接记录中写明。",
   "值班电话不得转移，遇突发事件 15 分钟内报告带班领导。"]),
 ("突发事件信息报送流程.md", "综合/值班应急", "流程", "综办〔2025〕6号", "2025-02-01", "internal",
  "突发事件信息报送流程", ["首报：事发 30 分钟内向公司应急办口头报告，1 小时内书面报告。",
   "续报：每小时一次，直至事态稳定。",
   "终报：事件处置结束后 3 个工作日内提交处置报告。"]),
 ("保密工作规定.md", "综合/保密档案", "规定", "综办〔2024〕3号", "2024-01-20", "internal",
  "保密工作规定", ["涉密文件按三级管理：绝密、机密、秘密；明确定密责任人。",
   "涉密载体不得接入互联网，不得使用无线设备拍摄。",
   "销毁须登记造册、双人监销。"]),
 ("涉密文件管理办法.md", "综合/保密档案", "办法", "综办〔2024〕4号", "2024-01-20", "sensitive",
  "涉密文件管理办法", ["涉密文件编号由保密办统一分配，台账逐份登记。",
   "借阅须审批，当日归还；不得复制、摘抄未获批准。"]),
 ("安全生产责任制.md", "安监/安全责任", "制度", "安监〔2024〕9号", "2024-04-01", "internal",
  "安全生产责任制", ["党政同责、一岗双责；主要负责人为第一责任人。",
   "按专业条线划分责任清单，逐级签订责任书。"]),
 ("隐患排查治理办法.md", "安监/隐患排查", "办法", "安监〔2025〕1号", "2025-01-05", "internal",
  "隐患排查治理办法", ["隐患分一般、重大两级；重大隐患须挂牌督办。",
   "排查频次：班组每周、部门每月、公司每季度。"]),
 ("事故调查报告模板.md", "安监/事故调查", "模板", "—", "2024-11-01", "internal",
  "事故调查报告格式", ["内容：事故经过、原因分析、责任认定、整改措施、处理建议。",
   "附件：现场照片、询问笔录、技术鉴定意见。"]),
 ("年度检修计划编制要求.md", "运检/检修计划", "通知", "运检〔2025〕8号", "2025-03-15", "internal",
  "年度检修计划编制要求", ["按设备状态评价结果排序，优先安排家族性缺陷设备。",
   "停电计划须与营销部门会签，避免重复停电。"]),
 ("设备缺陷管理流程.md", "运检/缺陷管理", "流程", "运检〔2024〕14号", "2024-09-01", "internal",
  "设备缺陷管理流程", ["缺陷分危急、严重、一般三级，危急缺陷 24 小时内消缺。",
   "缺陷录入、审核、派工、验收闭环管理。"]),
 ("供电服务规范.md", "营销/服务规范", "规范", "营销〔2023〕21号", "2023-08-01", "public",
  "供电服务规范", ["营业厅实行首问负责制，受理业务一次告知。",
   "抢修到达时限：城区 45 分钟，农村 90 分钟。"]),
 ("供电服务规范(2025版).md", "营销/服务规范", "规范", "营销〔2025〕5号", "2025-04-01", "public",
  "供电服务规范（2025 版）", ["新增线上办电时限要求：低压居民 5 个工作日办结。",
   "抢修到达时限：城区 40 分钟，农村 80 分钟。"]),
 ("电费回收考核办法.md", "营销/电费回收", "办法", "营销〔2024〕18号", "2024-12-01", "internal",
  "电费回收考核办法", ["考核指标：回收率、欠费额、账龄结构。",
   "月度排名后三名由分管领导约谈。"]),
 ("预算管理办法.md", "财务/预算管理", "办法", "财务〔2024〕6号", "2024-05-01", "internal",
  "预算管理办法", ["预算编制实行零基预算，按项目逐项测算。",
   "预算调整须报预算委员会审议，年度内原则上不超过两次。"]),
 ("差旅费报销标准.md", "财务/报销差旅", "制度", "财务〔2025〕3号", "2025-02-10", "internal",
  "差旅费报销标准", ["住宿标准：一般人员 350 元/晚，中层 500 元/晚。",
   "市内交通费凭票报销，每日不超过 80 元。"]),
 ("财务报销台账样表.md", "财务/报销差旅", "台账", "—", "2025-02-10", "internal",
  "财务报销台账", ["字段：日期、报销人、部门、事由、金额、单据张数、审批人。"]),
]

def ensure_kb():
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as c, c.cursor() as cur:
        cur.execute("select id from kb.knowledge_base where name='单位制度库'")
        r = cur.fetchone()
        if r:
            return r["id"]
        cur.execute("""insert into kb.knowledge_base (name, description, embedding_model, embedding_dim,
                          chunk_size, chunk_overlap, status, qa_enabled)
                       values ('单位制度库', '国网某公司制度文件（演示数据）', 'text-embedding-v4', 1024,
                               512, 64, 'active', true) returning id""")
        return cur.fetchone()["id"]

def main():
    kb = ensure_kb()
    print("kb:", kb)
    ok = 0
    tmp = tempfile.mkdtemp()
    for fn, path, kind, no, signed, secrecy, title, paras in DOCS:
        body = "# %s\n\n%s\n\n" % (title, "\n\n".join(paras))
        p = os.path.join(tmp, fn)
        open(p, "w", encoding="utf-8").write(body)
        with open(p, "rb") as fh:
            r = requests.post(API + "/api/v1/kb/%d/documents" % kb,
                              headers=H, files={"file": (fn, fh, "text/markdown")},
                              data={"visibility": "public"}, timeout=120)
        if r.status_code >= 300:
            print("UPLOAD FAIL", fn, r.status_code, r.text[:120]); continue
        ok += 1
    print("uploaded:", ok)
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as c, c.cursor() as cur:
        cur.execute("select id, path from kb.catalog")
        cat = {r["path"]: r["id"] for r in cur.fetchall()}
        for fn, path, kind, no, signed, secrecy, title, paras in DOCS:
            cid = cat.get(path)
            cur.execute("""update kb.document set catalog_id=%s, doc_kind=%s, issuer=%s, doc_no=%s,
                                  signed_on=%s, effective_from=%s, status=%s, secrecy=%s
                            where filename=%s and kb_id=%s""",
                        (cid, kind, path.split("/")[0] + "供电公司", no, signed, signed,
                         "superseded" if "2023" in signed else "active", secrecy, fn, kb))
        cur.execute("""select count(*) n, count(*) filter (where catalog_id is not null) c,
                              count(*) filter (where parse_status='ready') r
                         from kb.document where kb_id=%s""", (kb,))
        print("docs:", dict(cur.fetchall()[0]))

main()