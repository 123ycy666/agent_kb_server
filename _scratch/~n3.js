(async()=>{
 const urls=[
  "https://data.jsdelivr.com/v1/packages/gh/HuanzhiMao/BFCL-Result@main?structure=flat",
  "https://data.jsdelivr.com/v1/packages/gh/ShishirPatil/gorilla@gh-pages?structure=flat"
 ];
 for(const u of urls){try{const r=await fetch(u,{headers:{"User-Agent":"Mozilla/5.0"}});const t=await r.text();console.log("==",r.status,u,"bytes",t.length);console.log(t.slice(0,600));}catch(e){console.log("FAIL",u,e.message)}}
})();
