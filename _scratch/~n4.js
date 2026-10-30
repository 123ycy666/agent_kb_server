const fs=require("fs");
const cands=[
 "https://ghfast.top/https://raw.githubusercontent.com/ShishirPatil/gorilla/gh-pages/leaderboard.html",
 "https://ghfast.top/https://raw.githubusercontent.com/ShishirPatil/gorilla/main/berkeley-function-call-leaderboard/README.md"
];
(async()=>{for(let i=0;i<cands.length;i++){const u=cands[i];try{const r=await fetch(u,{headers:{"User-Agent":"Mozilla/5.0"}});const t=await r.text();const f="dl"+i+".txt";fs.writeFileSync(f,t);console.log("OK",r.status,"bytes",t.length,"->",f,"|",u);}catch(e){console.log("FAIL",u,e.message)}}})();
