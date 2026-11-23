"""Render the actor-isolation analysis + test results as a standalone HTML page.
Usage: python scripts/build_isolation_html.py [iso3] [iso2] [out.html]"""
from __future__ import annotations

import base64
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
main_run = sys.argv[1] if len(sys.argv) > 1 else "iso3"
demo_run = sys.argv[2] if len(sys.argv) > 2 else "iso2"
out = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "reports" / "actor-isolation" / "actor-isolation.html"
M = json.loads((ROOT / "runs" / main_run / "actor_isolation" / "results.json").read_text())
Dm = json.loads((ROOT / "runs" / demo_run / "actor_isolation" / "results.json").read_text())
img = ROOT / "docs" / "images" / "actor-isolation.png"
img_tag = (f'<img alt="推荐架构" style="width:100%;border-radius:8px" src="data:image/png;base64,{base64.b64encode(img.read_bytes()).decode()}">'
           if img.exists() else "")


def esc(t) -> str:
    t = re.sub(r"\b\d{12}\b", "<account-id>", str(t))
    return html.escape(t)


def short(actor: str) -> str:
    return re.sub(r"^iso\d+-", "", actor)


def badge(c):
    o = c["outcome"]
    if c["group"] == "自己的数据":
        ok = o == "allowed"
        txt = "✓ 允许" if ok else "! 拒绝（需传 namespace）"
        cls = "ok" if ok else "mid"
    else:
        blocked = o in ("denied", "error", "rejected") and not c.get("leaked_other_users_data")
        txt = {"denied": "✓ 拒绝（AccessDenied）", "error": f"✓ 拒绝（{c.get('code', '')}）", "rejected": "✓ 逐条拒绝"}.get(o, "✗ 允许") if blocked else "✗ 越权成功"
        cls = "ok" if blocked else "no"
    return f'<span class="b {cls}">{esc(txt)}</span>'


def detail(c):
    d = c.get("detail")
    if c["outcome"] == "allowed" and isinstance(d, dict):
        seen = d.get("tokens_seen")
        parts = []
        if "records" in d:
            parts.append(f'{d["records"]} 条记录')
        if seen is not None:
            parts.append("看到：" + ("、".join(seen) if seen else "无"))
        if "sessions" in d:
            parts.append(f'{d["sessions"]} 个会话')
        if "eventId" in d:
            parts.append("事件已写入")
        return "；".join(parts)
    code = c.get("code", "")
    if "AssumeRole" in c["case"] and code in ("AccessDeniedException", "AccessDenied"):
        return "STS 拒绝：信任策略要求必须带 actorId 标签，且只有后端主体能 AssumeRole，Alice 的凭证无法给自己换标签"
    if code in ("AccessDeniedException", "AccessDenied"):
        return "IAM 拒绝：请求的 actorId / namespace 与凭证标签不匹配，或该 API 被显式禁止"
    if code == "ValidationException":
        return "参数校验拒绝：namespacePath 不允许出现“.”"
    if code == "ResourceNotFoundException":
        return "服务端校验记录不在所声明的 namespace 下，按“不存在”处理，没有返回内容"
    if code == "PerRecordRejected":
        return "接口返回成功，但这条记录被逐条拒绝（Memory record not found），记录未被删除"
    return esc((d or "")[:140]) if isinstance(d, str) else ""


ORDER = ["自己的数据", "读别人的长期记忆", "按记录 ID 直接访问", "改/删别人的数据", "读别人的原始对话", "枚举与提权"]
groups = [(g, [c for c in M["cases"] if c["group"] == g]) for g in ORDER]
groups = [(g, cs) for g, cs in groups if cs]
case_rows = "".join(
    f'<tr class="g"><td colspan="3">{esc(g)}</td></tr>' + "".join(
        f'<tr><td>{esc(c["case"].replace("alice", "alice").replace("bob", "bob"))}</td><td>{badge(c)}</td><td class="m">{detail(c)}</td></tr>' for c in cs)
    for g, cs in groups)
attacks = [c for c in M["cases"] if c["group"] != "自己的数据"]
blocked = sum(c["outcome"] in ("denied", "error", "rejected") and not c.get("leaked_other_users_data") for c in attacks)
own = [c for c in M["cases"] if c["group"] == "自己的数据"]
own_ok = sum(c["outcome"] == "allowed" for c in own)
demo = [c for c in Dm["cases"] if c.get("leaked_other_users_data") or c["group"] == "风险演示"]
def demo_label(c):
    if c["case"].startswith("精确 namespace 检索自己的会话摘要"):
        return "精确 namespace 检索 /u/alice/ 前缀下列出的一个会话摘要（该 namespace 实际属于 alice/x）"
    return c["case"]


demo_rows = "".join(f'<tr><td>{esc(demo_label(c))}</td><td class="m">{detail(c)}</td></tr>' for c in demo)
policy = json.dumps(M["policy"], indent=2, ensure_ascii=False)
tokens = M["tokens"]
actors = "、".join(f'{short(k)}（推荐码 <code>{esc(v)}</code>）' for k, v in tokens.items())

page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>让用户只能访问自己的 actorId：AgentCore Memory 隔离方案与实测</title>
<style>
.viz-root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--good:#006300;--bad:#c62828;--warn:#8a5a00;--s1:#2a78d6}}
@media (prefers-color-scheme:dark){{:root:where(:not([data-theme="light"])) .viz-root{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--good:#0ca30c;--bad:#e66767;--warn:#c98500;--s1:#3987e5}}}}
*{{box-sizing:border-box}}body{{margin:0}}.viz-root{{background:var(--page);color:var(--ink);font:15px/1.65 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;min-height:100vh}}
.wrap{{max-width:1120px;margin:0 auto;padding:28px 20px 60px}}h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:18px;margin:34px 0 10px}}
.sub,.m,.note{{color:var(--ink2);font-size:13px}}.card{{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px 18px;margin:12px 0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}}.tile .k{{color:var(--ink2);font-size:13px}}.tile .v{{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums}}.tile .d{{color:var(--muted);font-size:13px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}th,td{{border-bottom:1px solid var(--grid);padding:8px;vertical-align:top;text-align:left}}th{{color:var(--ink2);font-weight:600}}
tr.g td{{font-weight:650;background:var(--page)}}.b{{font-weight:650;font-size:13px;white-space:nowrap}}.ok{{color:var(--good)}}.no{{color:var(--bad)}}.mid{{color:var(--warn)}}
code{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}}pre{{white-space:pre;overflow:auto;font-size:12px;background:var(--page);padding:10px;border-radius:8px;max-height:520px}}
ol li,ul li{{margin:4px 0}}
</style></head><body><div class="viz-root"><div class="wrap">
<h1>让用户只能访问自己的 actorId：AgentCore Memory 隔离方案与实测</h1>
<p class="sub">mem-eva 补充测试 · us-east-1 · 方法：IAM ABAC（带 actorId 标签的短期凭证）· 完整分析见 GitHub <code>docs/ACTOR_ISOLATION.md</code> · 测试脚本 <code>scripts/actor_isolation_test.py</code></p>

<div class="tiles">
<div class="card tile"><div class="k">越权尝试被拒</div><div class="v">{blocked}/{len(attacks)}</div><div class="d">读、写、删、枚举、提权、路径与前缀混淆</div></div>
<div class="card tile"><div class="k">访问自己数据</div><div class="v">{own_ok}/{len(own)}</div><div class="d">另 1 项：不传 namespace 读自己记录会被拒（需传参）</div></div>
<div class="card tile"><div class="k">受害者数据</div><div class="v">{"完好" if M["bob_record_intact_after_test"] else "被改动"}</div><div class="d">测试结束后 bob 的记录仍在</div></div>
<div class="card tile"><div class="k">风险演示</div><div class="v">actorId 含 /</div><div class="d">alice 的凭证能读到 alice/x：必须用不透明 ID</div></div>
</div>

<div class="card"><b>结论</b>
<p>在 AgentCore Memory 上可以做到“即使知道别人的 actorId，也读不到、写不进、删不掉别人的记忆”，并且由 AWS 在服务端强制：给每个用户签发带 <code>actorId</code> 标签的短期凭证，IAM 策略用 <code>${{aws:PrincipalTag/actorId}}</code> 同时约束 <code>actorId</code>、<code>namespace</code>、<code>namespacePath</code> 三个条件键，并显式禁止没有 actor 级条件键的 API。</p>
<p>三个前提缺一不可：</p>
<ol><li><b>actorId 用不透明 ID，不含 <code>/</code></b>，否则 <code>/u/alice/*</code> 会匹配到 <code>alice/x</code>。</li>
<li><b>标签从已验签的用户 JWT 派生</b>，不由业务代码随手填，否则后端传错 actorId 的 bug 会原样变成打错标签。</li>
<li><b>Agent 自主调用记忆工具时走 AgentCore Gateway + Cedar 策略</b>：Runtime 里所有用户共用一个执行角色，需要在 Gateway 按用户 JWT 校验。</li></ol></div>

<h2>推荐架构</h2>
<div class="card">{img_tag}</div>

<h2>要防的六类威胁</h2>
<div class="card"><table>
<tr><th>#</th><th>威胁</th><th>例子</th></tr>
<tr><td>T1</td><td>客户端篡改参数</td><td>前端请求带了 <code>actorId=bob</code>，后端直接透传</td></tr>
<tr><td>T2</td><td>后端代码 bug</td><td>缓存串号、并发变量被覆盖，用 Alice 的会话查了 Bob</td></tr>
<tr><td>T3</td><td>提示注入</td><td>用户让 Agent“查一下 actor bob 的记忆”，工具调用拼出了 Bob 的 ID</td></tr>
<tr><td>T4</td><td>枚举</td><td>调 <code>ListActors</code> 拿到全部 actorId</td></tr>
<tr><td>T5</td><td>前缀 / 路径混淆</td><td><code>alice</code> 读到 <code>alice2</code>、<code>alice/x</code>；用 <code>/u/</code>、<code>/</code> 一次查所有人</td></tr>
<tr><td>T6</td><td>按记录 ID 直接访问</td><td>拿到 Bob 的记录 ID，调 Get/Delete 并谎报 namespace</td></tr>
</table></div>

<h2>方案对比</h2>
<div class="card"><table>
<tr><th>方案</th><th>做法</th><th>挡住</th><th>代价 / 挡不住</th></tr>
<tr><td><b>A 应用层强制</b></td><td>后端从登录态推导 actorId，客户端不传；Agent 工具不暴露 actorId 参数</td><td>T1，T3（前提是工具不暴露参数）</td><td>T2 无兜底；所有调用共用高权限角色</td></tr>
<tr><td><b>B IAM ABAC</b>（本次实测）</td><td>按用户签发带 actorId 标签的短期凭证，策略按标签约束三个条件键并禁止缺口 API</td><td>T1、T3–T6 由 AWS 强制；T2 取决于标签来源</td><td>需要凭证签发并缓存（STS 默认共享配额约 600 RPS/账号/区域）；actorId 不能含 /</td></tr>
<tr><td><b>C Gateway + Cedar</b></td><td>用户 JWT 进 Gateway，Cedar 要求 actorId == JWT sub、namespacePath == JWT namespace；Memory 只接受来自 Gateway 的请求</td><td>T1–T6，以用户自己的 token 为准</td><td>多一层 Gateway；IdP 需签发 namespace claim</td></tr>
<tr><td><b>D Cognito 主体标签</b></td><td>客户端用 Cognito 换 AWS 凭证，属性映射为 principal tag，策略同 B</td><td>同 B，标签来自 IdP</td><td>客户端直连 AWS，暴露面更大</td></tr>
<tr><td><b>E 每租户一个 Memory</b></td><td>按 Memory ARN 授权</td><td>同 B</td><td>只适合少量 B2B 租户</td></tr>
</table>
<p class="note">推荐：业务后端确定性读写用 <b>A + B</b>（标签由独立组件从 JWT 派生）；Agent 运行中自主调用记忆工具用 <b>C</b>。设计初稿经过一轮独立的反方评审，以上取舍来自那轮评审。</p></div>

<h2>实测：{len(M["cases"])} 种访问方式逐项结果（actorId 为不透明 ID）</h2>
<div class="card"><p class="note" style="margin-top:0">参与者：{actors}。每人先写入一段含独有推荐码的对话，等长期记忆生成后开始测试。后端以带标签 <code>actorId=alice</code> 的凭证代表 Alice 发起以下所有请求；“看到”一栏列出返回内容中出现了谁的推荐码。</p>
<table><tr><th>操作</th><th>结果</th><th>细节</th></tr>{case_rows}</table></div>

<h2>风险演示：actorId 含 “/” 时会越界</h2>
<div class="card"><p class="note" style="margin-top:0">AgentCore 的 actorId 允许 <code>/</code>。额外创建 actor <code>alice/x</code> 后，它的记忆落在 <code>/u/alice/x/…</code>，正好被 Alice 的 <code>/u/alice/*</code> 匹配。以下请求都带着 Alice 的凭证：</p>
<table><tr><th>操作</th><th>返回</th></tr>{demo_rows}</table>
<p class="note">这不是策略写错，而是 namespace 模板与 actorId 字符集的组合问题。解决办法是用 UUID 或 HMAC 生成不含 <code>/</code> 的 actorId，并在应用层强制校验。</p></div>

<h2>实测用的 IAM 策略</h2>
<div class="card"><p class="note" style="margin-top:0">信任策略：只有后端主体可以 <code>sts:AssumeRole</code> + <code>sts:TagSession</code>，并且必须带 actorId 标签（<code>{esc(json.dumps(M["trust_condition"]))}</code>）。同一份策略同时支持精确 namespace 检索和单次分层检索（namespacePath）。</p>
<pre>{esc(policy)}</pre></div>

<h2>对照：TDAM 能做到吗</h2>
<div class="card"><p>TencentDB Agent Memory 的数据面只认一个共享的网关 Bearer key，<code>user_id</code> 由调用方自报。主评测中，持有这个 key 的调用方改一下 <code>user_id</code>，就读到了另一个用户的 12 条记录。要达到同等效果，只能在 TDAM 前面自建鉴权代理：从 JWT 推导 <code>user_id</code> 并覆盖请求，同时网关 key 不能下发到任何业务以外的组件。这相当于方案 A，没有服务端兜底。</p></div>

<h2>落地清单</h2>
<div class="card"><ul>
<li>actorId 改为不透明 ID（UUID / HMAC），应用层正则校验，禁止 <code>/</code>、<code>*</code>、<code>:</code></li>
<li>凭证签发：从已验签 JWT 派生 actorId 标签（AssumeRoleWithWebIdentity / Cognito 主体标签 / 独立签发服务），按用户缓存到过期前 5 分钟</li>
<li>上线上述 IAM 策略，配合 SCP 兜底禁止控制面操作</li>
<li>Agent 路径：Runtime 入站 JWT → Gateway Memory target + Cedar 策略；Memory 资源策略只允许 Gateway 访问；先用 LOG_ONLY 模式观察</li>
<li>记录类 API 始终带 namespace 参数（不传会被拒）</li>
<li>容量：估算峰值 STS 请求数并提前提额；监控 STS 与 Memory 的 Throttles</li>
<li>把测试脚本纳入 CI，每次改策略都回归一遍</li>
</ul></div>
</div></div></body></html>"""
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(page)
print("wrote", out)
