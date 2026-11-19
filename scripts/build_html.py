"""Build a single-file HTML report from reports/<run>/summary.json.
Usage: python scripts/build_html.py final  ->  reports/final/mem-eva-report.html"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
run = sys.argv[1] if len(sys.argv) > 1 else "final"
S = json.loads((ROOT / "reports" / run / "summary.json").read_text())
W = S["datasets"]["web3_zh"]
L = S["datasets"].get("lme", {})
P = W.get("probes_full", {})

ARM_LABEL = {"none": "无记忆", "tdam_full": "TDAM full（每用户一个 team）", "tdam_l1": "TDAM L1（每用户一个 team）",
             "tdam_shared_full": "TDAM full（共享 team）", "tdam_shared_l1": "TDAM L1（共享 team）",
             "agentcore_spu": "AgentCore SPU", "agentcore_path": "AgentCore 单次检索"}
FAMILY = {a: ("agentcore" if a.startswith("agentcore") else "tdam" if a.startswith("tdam") else "none") for a in ARM_LABEL}
CATS = [("abstention", "拒答"), ("cross_lingual", "跨语言"), ("fact", "事实"), ("multi_session", "跨会话"),
        ("preference", "偏好"), ("temporal", "时间"), ("update", "更新")]

acc = [{"arm": a, "label": ARM_LABEL[a], "family": FAMILY[a], "k": s["correct"], "n": s["n"], "p": s["accuracy"],
        "lo": s["ci95"][0], "hi": s["ci95"][1], "cov": s.get("fact_coverage_4k"), "ctx": s["context_tokens_mean"]}
       for a, s in W["arms"].items() if a in ARM_LABEL and a != "agentcore_path"]
acc.sort(key=lambda r: -r["p"])
heat = [{"arm": a, "label": ARM_LABEL[a], "cells": [{"cat": c, "name": n, **W["by_category"][a][c]} for c, n in CATS]}
        for a in [r["arm"] for r in acc]]
perf = P.get("perf", {})
perf_series = [{"arm": a, "label": ARM_LABEL[a], "pts": [{"c": int(c), **v} for c, v in sorted(perf[a].items(), key=lambda x: int(x[0]))]}
               for a in ["agentcore_spu", "tdam_l1", "agentcore_path", "tdam_full"] if a in perf]
lag = P.get("lag", {})
lme = {a: f"{s['correct']}/{s['n']}" for a, s in L.get("arms", {}).items()}
DATA = {"acc": acc, "heat": heat, "perf": perf_series, "lag": lag, "lme": lme, "pair": W.get("pairwise", {}),
        "secrets": {k: v.get("exact_secret_stored") for k, v in P.get("secrets", {}).items()},
        "iso": {k: [v.get("leaking_probes"), v.get("probes")] for k, v in P.get("isolation", {}).items()},
        "sharing": P.get("profile_sharing", []), "failover": P.get("failover", {}),
        "cost": W.get("cost_estimate", {})}

HTML = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>mem-eva：TencentDB Agent Memory vs AgentCore Memory 评测</title>
<style>
.viz-root{color-scheme:light;--page:#f9f9f7;--surface-1:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
 --ring:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--none:#898781;--good:#006300;--bad:#c62828;--warn:#8a5a00;
 --seq0:#f1f6fd;--seq1:#cde2fb;--seq2:#9ec5f4;--seq3:#5598e7;--seq4:#256abf;--seq5:#104281}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--page:#0d0d0d;--surface-1:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
 --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--good:#0ca30c;--bad:#e66767;--warn:#c98500}}
*{box-sizing:border-box}body{margin:0}
.viz-root{background:var(--page);color:var(--ink);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;min-height:100vh}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:19px;margin:40px 0 12px}.sub{color:var(--ink2);margin:0 0 24px}
.card{background:var(--surface-1);border:1px solid var(--ring);border-radius:12px;padding:18px 20px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.tile .k{color:var(--ink2);font-size:13px}.tile .v{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums}.tile .d{color:var(--muted);font-size:13px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}th,td{padding:7px 8px;border-bottom:1px solid var(--grid);text-align:right}
th:first-child,td:first-child{text-align:left}th{color:var(--ink2);font-weight:600}
.badge{display:inline-flex;gap:4px;align-items:center;font-size:13px;font-weight:600}.ok{color:var(--good)}.no{color:var(--bad)}.mid{color:var(--warn)}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin:4px 0 8px}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--ink);border:1px solid var(--ring);border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9;max-width:320px}
svg text{fill:var(--ink2);font-size:12px;pointer-events:none}.note{color:var(--ink2);font-size:13px}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;margin-top:8px}a{color:var(--s1)}
</style></head><body><div class="viz-root"><div class="wrap">
<h1>Agent 记忆系统评测：TencentDB Agent Memory vs Amazon Bedrock AgentCore Memory</h1>
<p class="sub">mem-eva · us-east-1 · 2026-11-19 · 自建 web3_zh 数据集 120 题 + LongMemEval-S 7 题 · 回答模型 Haiku 5.5 · 评分模型 Sonnet 5.5 · 上下文 4K · <a href="https://github.com/chinapanpan/mem-eva">GitHub</a></p>
<div class="tiles" id="tiles"></div>

<h2>记忆问答正确率（web3_zh，120 题，误差线为 95% Wilson CI）</h2>
<div class="card"><div class="legend"><span><i class="sw" style="background:var(--s1)"></i>AgentCore</span><span><i class="sw" style="background:var(--s2)"></i>TDAM</span><span><i class="sw" style="background:var(--none)"></i>无记忆基线</span></div>
<svg id="acc" width="100%" height="270" role="img" aria-label="各方案正确率"></svg>
<details><summary>数据表</summary><table id="accT"></table></details>
<p class="note">LongMemEval-S（7 题，仅供参考）：AgentCore __LME_AC__，TDAM L1 __LME_T1__，TDAM full __LME_TF__，无记忆 __LME_N__。</p>
<p class="note">配对 McNemar 检验（AgentCore SPU vs TDAM 最佳 arm `tdam_shared_l1`）：只有 AgentCore 答对 26 题，只有 TDAM 答对 2 题，p &lt; 0.001。</p></div>

<h2>分类别答对题数</h2>
<div class="card"><svg id="heat" width="100%" height="250" role="img" aria-label="分类别热力图"></svg>
<p class="note">颜色越深表示答对越多（每类 17–18 题）。时间题受"下周X"歧义影响：按确定性日期匹配，AgentCore 有 13/17 题给出了标准日期或标准日期 −7 天，TDAM 为 5–7 题。</p></div>

<h2>多租户、安全与删除</h2>
<div class="card"><table id="sec"></table></div>

<h2>写入→可检索 &amp; 检索性能</h2>
<div class="card"><div class="legend" id="perfLeg"></div><svg id="perf" width="100%" height="280" role="img" aria-label="并发与 p95 时延"></svg>
<p class="note">纵轴为 p95 检索时延（对数刻度）。AgentCore 在并发 ≥8 时受账户默认配额限制（RetrieveMemoryRecords 30 TPS，可申请提升）：SPU 每个请求调用 3 次 API，单次分层检索只调用 1 次。CloudWatch 在这些时间窗口记录到了 Throttles，boto3 adaptive 重试会在客户端降速，所以表现为高延迟、0 错误。提额后的表现本次未测。</p>
<details><summary>数据表</summary><table id="perfT"></table></details></div>

<h2>成本（本次运行，按公开价格估算）</h2>
<div class="card"><table id="cost"></table><p class="note">TDAM 还需另计 EC2 m7i.xlarge（$0.2016/小时，约 $147/月）、EBS、高可用副本和运维人力。</p></div>
<p class="note">完整报告、配置差异和复现方法见 GitHub 仓库 <code>docs/REPORT.md</code>。本页所有数字都来自 <code>reports/final/summary.json</code>。</p>
</div><div id="tip"></div></div>
<script>
const D=__DATA__;const css=v=>getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(v).trim();
const fam={agentcore:'--s1',tdam:'--s2',none:'--none'};const pct=x=>(x*100).toFixed(1)+'%';
const tip=document.getElementById('tip');function show(e,h){tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-330)+'px';tip.style.top=(e.clientY+14)+'px'}function hide(){tip.style.display='none'}
const NS='http://www.w3.org/2000/svg';function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.appendChild(e);return e}
// tiles
const ac=D.acc.find(r=>r.arm==='agentcore_spu'),tb=D.acc.filter(r=>r.family==='tdam').sort((a,b)=>b.p-a.p)[0];
const lagA=D.lag.agentcore||{},lagT=D.lag.tdam||{};
document.getElementById('tiles').innerHTML=[
['AgentCore 正确率',pct(ac.p),`${ac.k}/${ac.n} · 4K 事实覆盖 ${pct(ac.cov)}`],
['TDAM 最佳 arm 正确率',pct(tb.p),`${tb.k}/${tb.n} · ${tb.label}`],
['写入→可检索 p50（6 个用户）',`${(lagA.p50||0).toFixed(1)} s`,`AgentCore ${lagA.ready}/${lagA.n}（p95 ${(lagA.p95||0).toFixed(0)}s）；TDAM 30 分钟内 ${lagT.ready}/${lagT.n}`],
['密钥进入长期记忆',`${D.secrets.agentcore}/30`,`AgentCore（需要脱敏）；TDAM ${D.secrets.tdam}/30`]].map(t=>`<div class="card tile"><div class="k">${t[0]}</div><div class="v">${t[1]}</div><div class="d">${t[2]}</div></div>`).join('');
// accuracy bars with CI
(function(){const s=document.getElementById('acc'),w=s.clientWidth||900,lw=230,rw=w-lw-60,bh=22,gap=14,top=10;s.setAttribute('height',top+D.acc.length*(bh+gap)+26);
const x=v=>lw+v*rw;for(let t=0;t<=1.001;t+=.2){el('line',{x1:x(t),x2:x(t),y1:top-4,y2:top+D.acc.length*(bh+gap),stroke:css('--grid')},s);el('text',{x:x(t),y:top+D.acc.length*(bh+gap)+16,'text-anchor':'middle'},s).textContent=Math.round(t*100)+'%'}
D.acc.forEach((r,i)=>{const y=top+i*(bh+gap);el('text',{x:lw-10,y:y+bh*.68,'text-anchor':'end',style:'fill:'+css('--ink')},s).textContent=r.label;
const b=el('rect',{x:lw,y,width:Math.max(2,r.p*rw),height:bh,rx:4,fill:css(fam[r.family])},s);
el('line',{x1:x(r.lo),x2:x(r.hi),y1:y+bh/2,y2:y+bh/2,stroke:css('--ink'),'stroke-width':1.5},s);[r.lo,r.hi].forEach(v=>el('line',{x1:x(v),x2:x(v),y1:y+5,y2:y+bh-5,stroke:css('--ink'),'stroke-width':1.5},s));
el('text',{x:x(r.hi)+6,y:y+bh*.68,style:'fill:'+css('--ink')+';font-weight:600'},s).textContent=pct(r.p);
const hit=el('rect',{x:0,y:y-gap/2,width:w,height:bh+gap,fill:'transparent'},s);hit.addEventListener('mousemove',e=>show(e,`<b>${r.label}</b><br>答对 ${r.k}/${r.n}（${pct(r.p)}）<br>95% CI ${pct(r.lo)}–${pct(r.hi)}<br>4K 事实覆盖 ${r.cov==null?'—':pct(r.cov)}<br>平均上下文 ${Math.round(r.ctx)} tokens`));hit.addEventListener('mouseleave',hide)});
document.getElementById('accT').innerHTML='<tr><th>方案</th><th>答对</th><th>正确率</th><th>95% CI</th><th>4K 事实覆盖</th><th>上下文 tokens</th></tr>'+D.acc.map(r=>`<tr><td>${r.label}</td><td>${r.k}/${r.n}</td><td>${pct(r.p)}</td><td>${pct(r.lo)}–${pct(r.hi)}</td><td>${r.cov==null?'—':pct(r.cov)}</td><td>${Math.round(r.ctx)}</td></tr>`).join('')})();
// heatmap
(function(){const s=document.getElementById('heat'),w=s.clientWidth||900,lw=230,cw=(w-lw)/7,ch=30,top=26;s.setAttribute('height',top+D.heat.length*(ch+2)+4);
const seq=['--seq0','--seq1','--seq2','--seq3','--seq4','--seq5'];D.heat[0].cells.forEach((c,j)=>el('text',{x:lw+j*cw+cw/2,y:16,'text-anchor':'middle'},s).textContent=c.name);
D.heat.forEach((r,i)=>{const y=top+i*(ch+2);el('text',{x:lw-10,y:y+ch*.65,'text-anchor':'end',style:'fill:'+css('--ink')},s).textContent=r.label;
r.cells.forEach((c,j)=>{const f=c.correct/c.n,k=Math.min(5,Math.round(f*5));const g=el('rect',{x:lw+j*cw+1,y,width:cw-2,height:ch,rx:4,fill:css(seq[k])},s);
el('text',{x:lw+j*cw+cw/2,y:y+ch*.65,'text-anchor':'middle',style:'font-variant-numeric:tabular-nums;fill:'+(k>=3?'#ffffff':css('--ink'))},s).textContent=`${c.correct}/${c.n}`;
g.addEventListener('mousemove',e=>show(e,`<b>${r.label}</b><br>${c.name}：${c.correct}/${c.n}（${pct(f)}）`));g.addEventListener('mouseleave',hide)})})})();
// security table
(function(){const B=(ok,t)=>`<span class="badge ${ok===true?'ok':ok===false?'no':'mid'}">${ok===true?'✓':ok===false?'✗':'!'} ${t}</span>`;
const sh=(D.sharing||[]).find(x=>x.arm==='tdam_shared_full')||{};const rows=[
['调用方配置正确时的跨租户泄漏（90 次探测）',B(true,`${D.iso.tdam?.[0]}/90（两种拓扑）`),B(true,`${D.iso.agentcore?.[0]}/90`)],
['共享 team+agent 时的画像（L2/L3）',B(false,`30 个用户拿到 ${sh.distinct_preambles} 份相同的 persona`),B(true,'按 actor 划分 namespace')],
['伪造 user_id 越权读取',B(false,'可以（共享 Bearer key，读到了别人的 12 条记录）'),B(true,'IAM namespace 条件拒绝（AccessDenied）')],
['假 API secret 写入长期记忆',B(true,`${D.secrets.tdam}/30`),B(false,`${D.secrets.agentcore}/30（需要在写入前脱敏）`)],
['删除后残留（0/30/120 秒）',B(true,'即时清零'),B('mid','0 秒仍可检索；30 秒 4/5、120 秒 5/5 清零（最终一致）')],
['写入重试是否幂等',B(false,'否（实测出现过重复写入）'),B(true,'是（clientToken）')],
['容器重启 / 节点故障',B('mid',`重启时 ${D.failover.failed}/${D.failover.requests} 失败，数据无丢失；单机，高可用需自建`),B(true,'托管服务')]];
document.getElementById('sec').innerHTML='<tr><th>测试</th><th style="text-align:left">TDAM</th><th style="text-align:left">AgentCore</th></tr>'+rows.map(r=>`<tr><td>${r[0]}</td><td style="text-align:left">${r[1]}</td><td style="text-align:left">${r[2]}</td></tr>`).join('')})();
// perf lines (log y, p95)
(function(){const s=document.getElementById('perf'),w=s.clientWidth||900,l=60,r=200,t=12,h=230,cols=['--s1','--s2','--s3','--s4'];
document.getElementById('perfLeg').innerHTML=D.perf.map((p,i)=>`<span><i class="sw" style="background:var(${cols[i]})"></i>${p.label}</span>`).join('');
const cs=[1,8,32],X=c=>l+cs.indexOf(c)*(w-l-r)/2,lo=Math.log10(.05),hi=Math.log10(50),Y=v=>t+h-(Math.log10(Math.max(v,.05))-lo)/(hi-lo)*h;
[.1,.3,1,3,10,30].forEach(v=>{el('line',{x1:l,x2:w-r,y1:Y(v),y2:Y(v),stroke:css('--grid')},s);el('text',{x:l-8,y:Y(v)+4,'text-anchor':'end'},s).textContent=v+'s'});
cs.forEach(c=>el('text',{x:X(c),y:t+h+18,'text-anchor':'middle'},s).textContent='并发 '+c);
D.perf.forEach((p,i)=>{const col=css(cols[i]);el('polyline',{points:p.pts.map(q=>X(q.c)+','+Y(q.p95)).join(' '),fill:'none',stroke:col,'stroke-width':2},s);
p.pts.forEach(q=>{const m=el('circle',{cx:X(q.c),cy:Y(q.p95),r:5,fill:col,stroke:css('--surface-1'),'stroke-width':2},s);const hit=el('circle',{cx:X(q.c),cy:Y(q.p95),r:14,fill:'transparent'},s);
hit.addEventListener('mousemove',e=>show(e,`<b>${p.label}</b> · 并发 ${q.c}<br>p50 ${q.p50.toFixed(3)}s · p95 ${q.p95.toFixed(3)}s · p99 ${q.p99.toFixed(3)}s<br>${q.throughput_rps.toFixed(1)} rps · 错误 ${q.errors}`));hit.addEventListener('mouseleave',hide)});
});
// end labels with de-collision (min 14px apart)
const labs=D.perf.map((p,i)=>{const q=p.pts[p.pts.length-1];return {t:p.label,x:X(q.c)+10,y:Y(q.p95)+4}}).sort((a,b)=>a.y-b.y);
for(let k=1;k<labs.length;k++)if(labs[k].y-labs[k-1].y<14)labs[k].y=labs[k-1].y+14;
labs.forEach(l=>el('text',{x:l.x,y:l.y,style:'fill:'+css('--ink')},s).textContent=l.t);
document.getElementById('perfT').innerHTML='<tr><th>方案</th><th>并发</th><th>p50</th><th>p95</th><th>p99</th><th>rps</th><th>错误</th></tr>'+D.perf.flatMap(p=>p.pts.map(q=>`<tr><td>${p.label}</td><td>${q.c}</td><td>${q.p50.toFixed(3)}</td><td>${q.p95.toFixed(3)}</td><td>${q.p99.toFixed(3)}</td><td>${q.throughput_rps.toFixed(1)}</td><td>${q.errors}</td></tr>`)).join('')})();
// cost
(function(){const a=D.cost.agentcore||{},t=D.cost.tdam||{};document.getElementById('cost').innerHTML=`<tr><th>项</th><th>AgentCore</th><th>TDAM</th></tr>
<tr><td>记忆抽取 / 存储</td><td>${a.ltm_records} 条长期记录 · $${(a.ltm_storage_usd_month||0).toFixed(2)}/月</td><td>内部 LLM + embedding $${(t.internal_llm_embedding_usd||0).toFixed(2)}（两种拓扑共 60 份历史）</td></tr>
<tr><td>写入</td><td>${a.events} 个事件 · $${(a.stm_ingest_usd||0).toFixed(3)}</td><td>包含在主机费用中</td></tr>
<tr><td>检索</td><td>${a.retrieve_calls_eval} 次 · $${(a.retrieval_usd||0).toFixed(2)}</td><td>包含在主机费用中</td></tr>
<tr><td>主机</td><td>无</td><td>$${t.ec2_hourly_usd}/小时 + EBS + 高可用</td></tr>`})();
</script></body></html>"""
out = ROOT / "reports" / run / "mem-eva-report.html"
HTML = HTML.replace("__LME_AC__", lme.get("agentcore_spu", "—")).replace("__LME_T1__", lme.get("tdam_l1", "—")).replace(
    "__LME_TF__", lme.get("tdam_full", "—")).replace("__LME_N__", lme.get("none", "—"))
out.write_text(HTML.replace("__DATA__", json.dumps(DATA, ensure_ascii=False)))
print("wrote", out)
