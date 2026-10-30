const t=(u)=>fetch(u,{headers:{"User-Agent":"Mozilla/5.0"}}).then(r=>r.text().then(b=>console.log("OK",r.status,u,"::",b.slice(0,150).replace(/\s+/g," ")))).catch(e=>console.log("FAIL",u,e.message));
(async()=>{await t("https://api.github.com/repos/HuanzhiMao/BFCL-Result");await t("https://raw.githubusercontent.com/HuanzhiMao/BFCL-Result/main/README.md");})();
