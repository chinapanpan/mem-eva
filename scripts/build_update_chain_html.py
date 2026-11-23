"""Render runs/<run>/update_chain/results.json as a standalone HTML page.
Usage: python scripts/build_update_chain_html.py chain1 [out.html]"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
run = sys.argv[1] if len(sys.argv) > 1 else "chain1"
src = ROOT / "runs" / run / "update_chain" / "results.json"
out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "reports" / run / "update-chain.html"
S = json.loads(src.read_text())
STEPS = S["steps"]
N = len(STEPS)
ARMS = [("agentcore", "agentcore_spu", "AgentCore Memory（SPU）"), ("tdam", "tdam_full", "TDAM（原生 recall：L3+L2+L1）"),
        ("tdam", "tdam_l1", "TDAM（仅 L1）")]


def scrub(t: str) -> str:
    t = re.sub(r"\b\d{12}\b", "<account-id>", t)
    return re.sub(r"memeva_\d{8}_\d{6}-\w+", "<memory-id>", t)


def esc(t) -> str:
    return html.escape(scrub(str(t)))


def stat(sys_key, arm):
    rows = [st["systems"].get(sys_key, {}).get(arm) for st in STEPS]
    rows = [r for r in rows if r]
    eff = [st["systems"].get(sys_key, {}).get("effective_seconds") for st in STEPS]
    ok_eff = sorted(e for e in eff if e is not None)
    med = ok_eff[len(ok_eff) // 2] if ok_eff else None
    return {"correct": sum(r["correct"] for r in rows), "n": len(rows),
            "stale_in_answer": sum(1 for r in rows if r["answer_mentions_old"] and not r["correct"]),
            "ctx_current": sum(r["context_has_current"] for r in rows),
            "effective": sum(e is not None for e in eff), "median_eff": med}


ST = {arm: stat(k, arm) for k, arm, _ in ARMS}
chart = [{"step": st["step"], "agentcore": st["systems"].get("agentcore", {}).get("effective_seconds"),
          "tdam": st["systems"].get("tdam", {}).get("effective_seconds")} for st in STEPS]
max_wait = S["config"]["max_wait_s"]


def badge(ok: bool, yes="正确", no="错误"):
    return f'<span class="b {"ok" if ok else "no"}">{"✓ " + yes if ok else "✗ " + no}</span>'


def cell(st, sys_key, arm):
    s = st["systems"].get(sys_key, {})
    r = s.get(arm)
    if not r:
        return "<td>—</td>"
    eff = s.get("effective_seconds")
    eff_t = f"{eff:.0f} 秒后生效" if eff is not None else f"{max_wait // 60} 分钟内未生效"
    olds = r["context_old_values"]
    ctx_line = ("上下文含新值" if r["context_has_current"] else "上下文<b>不含</b>新值") + (f"；含旧值 {len(olds)} 个" if olds else "；不含旧值")
    return (f'<td>{badge(r["correct"])}<div class="m">{esc(eff_t)} · {ctx_line}</div>'
            f'<div class="ans">{esc(r["answer"])}</div>'
            f'<details><summary>检索上下文（{r["context_chars"]} 字符）与评分</summary><pre>{esc(r["context"][:6000])}</pre>'
            f'<div class="m">评分：{esc(r["judge"][-300:])}</div>'
            + (f'<div class="m">第一次回答（400 tokens 上限）：{"正确" if r["first_attempt"]["correct"] else "判错"}，'
               f'{esc(r["first_attempt"]["answer"][:120]) or "（空白，被截断）"}</div>' if r.get("first_attempt") else "")
            + '</details></td>')


rows = []
for st in STEPS:
    prev = f'<div class="m">上一个值：<code>{esc(st["previous"])}</code></div>' if st["previous"] else '<div class="m">首次写入</div>'
    conv = "".join(f'<div class="turn"><b>{"用户" if m["role"] == "user" else "助手"}</b>：{esc(m["content"])}</div>' for m in st["messages"])
    rows.append(f'<tr><td class="step">{st["step"]}</td><td><div><code class="addr">{esc(st["address"])}</code></div>{prev}'
                f'<div class="fact">“{esc(st["fact_utterance"])}”</div><details><summary>本轮写入的完整对话</summary>{conv}</details></td>'
                + "".join(cell(st, k, arm) for k, arm, _ in ARMS) + "</tr>")

final = S.get("final_memory", {})
final_html = "".join(
    f'<div class="card"><h3>{esc(name)}</h3><ul>' + "".join(f"<li>{esc(t[:300])}</li>" for t in final.get(k, [])[:40]) +
    ("" if final.get(k) else "<li>（无）</li>") + "</ul></div>"
    for k, name in (("agentcore", "AgentCore Memory 长期记忆中与地址相关的记录"), ("tdam", "TDAM 长期记忆（L1/L2/L3）中与地址相关的内容")))


def tile(k, arm, name):
    s = ST[arm]
    med = f'{s["median_eff"]:.0f} 秒' if s["median_eff"] is not None else "—"
    return (f'<div class="card tile"><div class="k">{esc(name)}</div><div class="v">{s["correct"]}/{s["n"]}</div>'
            f'<div class="d">答对轮数 · 新值生效 {STEPS and stat_eff(k)} · 生效中位 {med}</div></div>')


def stat_eff(k):
    eff = [st["systems"].get(k, {}).get("effective_seconds") for st in STEPS]
    return f"{sum(e is not None for e in eff)}/{len(eff)} 轮"


tiles = "".join(tile(k, arm, name) for k, arm, name in ARMS)
DATA = json.dumps({"chart": chart, "max": max_wait}, ensure_ascii=False)
started = STEPS[0]["written_at"][:16].replace("T", " ") if STEPS else ""

page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>连续更新测试：同一事实改 {N} 次 · AgentCore Memory vs TDAM</title>
<style>
.viz-root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--ring:rgba(11,11,11,.10);
 --s1:#2a78d6;--s2:#eb6834;--good:#006300;--bad:#c62828}}
@media (prefers-color-scheme:dark){{:root:where(:not([data-theme="light"])) .viz-root{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--good:#0ca30c;--bad:#e66767}}}}
*{{box-sizing:border-box}}body{{margin:0}}.viz-root{{background:var(--page);color:var(--ink);font:15px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;min-height:100vh}}
.wrap{{max-width:1240px;margin:0 auto;padding:28px 20px 60px}}h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:18px;margin:32px 0 10px}}h3{{font-size:15px;margin:0 0 8px}}
.sub,.m,.note{{color:var(--ink2);font-size:13px}}.card{{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px 18px;margin:12px 0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.tile .k{{color:var(--ink2);font-size:13px}}.tile .v{{font-size:32px;font-weight:650;font-variant-numeric:tabular-nums}}.tile .d{{color:var(--muted);font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{border-bottom:1px solid var(--grid);padding:10px 8px;vertical-align:top;text-align:left}}th{{color:var(--ink2);font-weight:600;position:sticky;top:0;background:var(--surface)}}
td.step{{font-weight:650;font-variant-numeric:tabular-nums;width:36px}}code{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}}.addr{{font-size:14px;font-weight:600}}
.fact{{margin-top:4px}}.ans{{margin-top:6px;white-space:pre-wrap}}.turn{{margin:4px 0;font-size:13px}}pre{{white-space:pre-wrap;font-size:12px;background:var(--page);padding:8px;border-radius:6px;max-height:320px;overflow:auto}}
.b{{font-weight:650;font-size:13px}}.ok{{color:var(--good)}}.no{{color:var(--bad)}}details summary{{cursor:pointer;color:var(--ink2);font-size:12.5px;margin-top:6px}}
.legend{{display:flex;gap:16px;font-size:13px;color:var(--ink2)}}.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
svg text{{fill:var(--ink2);font-size:12px;pointer-events:none}}#tip{{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:6px 9px;font-size:13px;display:none;z-index:9;box-shadow:0 4px 14px rgba(0,0,0,.12)}}
ul{{margin:0;padding-left:18px}}li{{margin:3px 0;font-size:13px}}
</style></head><body><div class="viz-root"><div class="wrap">
<h1>连续更新测试：同一条事实改 {N} 次，记忆系统还能答对吗？</h1>
<p class="sub">mem-eva 补充测试 · {esc(started)} UTC 起 · 一个独立 actor，提币白名单地址先写入、再连续修改 {N - 1} 次 · AgentCore Memory（us-east-1，SPU 三策略）vs TencentDB Agent Memory（同一镜像与配置）· 回答 {esc(S["config"]["answer_model"])} · 评分 {esc(S["config"]["judge_model"])} · <a href="https://github.com/chinapanpan/mem-eva">GitHub</a></p>
<div class="tiles">{tiles}</div>

<div class="card"><b>测试方法</b>
<ul>
<li>每一轮写入一段 3 轮的短对话：先问一个日常问题，中间一句说出新的提币白名单地址（并提到旧地址不再使用），最后一句收尾。地址由固定种子生成，{N} 个互不相同。</li>
<li>写入后每 {S["config"]["poll_s"]} 秒检索一次，直到检索结果里出现新地址（视为“长期记忆已生效”），最多等 {max_wait // 60} 分钟；到时仍未出现就照常提问。</li>
<li>然后提问“{esc(S["question"])}”：检索结果截断到 4K tokens 交给回答模型作答，再由评分模型按 LongMemEval 的“知识更新”模板判分（必须给出最新地址；顺带提到旧地址不扣分）。</li>
<li>同时记录：检索上下文里是否有新地址、残留了几个旧地址；回答里是否把旧地址当成当前地址。</li>
</ul></div>

<div class="card"><b>测试过程中发现并修正的两个问题</b>
<ul>
<li><b>写入节奏。</b>第一次运行时，脚本在一秒内连写一轮的 3 段对话，触发了 TDAM 抽取调度的时序问题：预热抽取只处理了第一句闲聊（结果为空），计数被清零，空闲超时后整段会话被跳过，包含地址的那一句从未被抽取。真实聊天不会以亚秒级间隔到达，因此改为每一句间隔 {S["config"].get("turn_gap_s", 20)} 秒写入，两个系统采用同样的节奏。本页是改正后的第二次运行。</li>
<li><b>回答长度上限。</b>回答模型最初的输出上限是 400 tokens。上下文里旧地址越积越多时，模型会先做较长的推理，输出被截断成空白，被判为答错，三个方案都受影响。改为 2,000 tokens 后，用每轮已保存的同一份检索上下文重新回答并重新评分；记忆系统本身没有重新写入或检索。每格展开后可以看到第一次的截断结果。</li>
</ul></div>

<h2>每轮新值多久生效</h2>
<div class="card"><div class="legend"><span><i class="sw" style="background:var(--s1)"></i>AgentCore Memory</span><span><i class="sw" style="background:var(--s2)"></i>TDAM</span><span>× = {max_wait // 60} 分钟内未生效</span></div>
<svg id="eff" width="100%" height="260" role="img" aria-label="每轮生效时间"></svg>
<p class="note">纵轴是从写入完成到检索结果中出现新地址的秒数（每 {S["config"]["poll_s"]} 秒检查一次，精度约 ±{S["config"]["poll_s"]} 秒）。</p></div>

<h2>逐轮结果</h2>
<div class="card" style="overflow-x:auto"><table>
<tr><th>轮</th><th style="min-width:230px">本轮写入的数据（期望答案）</th>{"".join(f'<th style="min-width:260px">{esc(n)}</th>' for _, _, n in ARMS)}</tr>
{"".join(rows)}
</table></div>

<h2>最终长期记忆里留下了什么</h2>
<p class="note">全部 {N} 轮结束后，导出每个系统长期记忆中提到任一测试地址的内容（最多列 40 条）。可以看出新旧值是被覆盖、合并，还是并存。</p>
{final_html}
<p class="note">数据文件：<code>reports/{esc(run)}/results.json</code> · 脚本：<code>scripts/update_chain_test.py</code>（可用相同命令复跑）</p>
</div><div id="tip"></div></div>
<script>
const D={DATA};const NS='http://www.w3.org/2000/svg';const css=v=>getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(v).trim();
function el(t,a,p){{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);p&&p.appendChild(e);return e}}
const tip=document.getElementById('tip');function show(e,h){{tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-260)+'px';tip.style.top=(e.clientY+14)+'px'}}function hide(){{tip.style.display='none'}}
(function(){{const s=document.getElementById('eff'),w=s.clientWidth||1000,l=52,r=20,t=12,h=200,n=D.chart.length;const X=i=>l+(n<=1?0:i*(w-l-r)/(n-1)),Y=v=>t+h-Math.min(v,D.max)/D.max*h;
for(let v=0;v<=D.max;v+=D.max/4){{el('line',{{x1:l,x2:w-r,y1:Y(v),y2:Y(v),stroke:css('--grid')}},s);el('text',{{x:l-6,y:Y(v)+4,'text-anchor':'end'}},s).textContent=Math.round(v)+'s'}}
D.chart.forEach((c,i)=>el('text',{{x:X(i),y:t+h+18,'text-anchor':'middle'}},s).textContent='第'+c.step+'轮');
[['agentcore','--s1','AgentCore Memory'],['tdam','--s2','TDAM']].forEach(([k,col,name])=>{{const c=css(col);const pts=D.chart.map((d,i)=>[X(i),d[k]==null?Y(D.max):Y(d[k])]);
el('polyline',{{points:pts.map(p=>p.join(',')).join(' '),fill:'none',stroke:c,'stroke-width':2}},s);
D.chart.forEach((d,i)=>{{const[x,y]=pts[i];if(d[k]==null){{el('text',{{x,y:y+4,'text-anchor':'middle',style:'fill:'+c+';font-size:15px;font-weight:700'}},s).textContent='×'}}else el('circle',{{cx:x,cy:y,r:5,fill:c,stroke:css('--surface'),'stroke-width':2}},s);
const hit=el('circle',{{cx:x,cy:y,r:14,fill:'transparent'}},s);hit.addEventListener('mousemove',e=>show(e,`<b>${{name}}</b> · 第${{d.step}}轮<br>${{d[k]==null?'未在 '+Math.round(D.max/60)+' 分钟内生效':d[k]+' 秒后生效'}}`));hit.addEventListener('mouseleave',hide)}})}})}})();
</script></body></html>"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page)
print("wrote", out)
