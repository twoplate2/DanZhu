# -*- coding: utf-8 -*-
"""余额模拟器 — 验证不同 RTP 档位下 1000 珠起始、每发投 50 珠的余额分布。

规则(与游戏 plinko.py 完全一致):
  - 盘面 9 槽, 每格独立 q = rtp/3.35 概率非零, 非零倍率取 _reward_value(E≈3.35)
  - 每发均匀落格(1/9), 赔付 = 50 × 落格倍率(0 槽 = 0)
  - 余额 += 赔付 - 50; 余额 < 50 无法下注 → 破产停止

用法:
  python sim_balance.py            # GUI
  python sim_balance.py --headless  # 无界面跑默认档位×5000次, 出HTML到 output/
输出: output/sim_balance_*.html (零依赖内联JS, 浏览器可开)
"""
import io, os, sys, math, random, time, argparse
import statistics

# 与 plinko.py 对齐的赔付逻辑(独立实现, 便于本工具单独跑)
NUM_SLOTS = 9
REWARD_EV = 3.35
DEFAULT_BALANCE = 1000
BET = 50
MAX_PLAYS = 500                  # 固定玩这么多发(统一口径: 所有档位玩N发后看余额分布,
                                 # 中途余额<50 判破产提前停)。不受"跑到破产"截断影响,
                                 # 高收益档也不会因无限增长拖慢。

RTP_LEVELS = [0.80, 1.00, 1.20, 2.00]
RTP_LABEL = {0.80: "80%", 1.00: "100%", 1.20: "120%", 2.00: "200%"}


_SLOT_DIST_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "slot_dist.json")


def measure_slot_dist(n=800, power=0.8, use_cache=True):
    """实测当前物理的真实落格分布(power=0.8, 与节奏门禁同口径)。
    直接跑 plinko.py 物理到落袋, 返回 [槽0..槽8] 概率。RTP 期望与落格分布无关
    (每格倍率独立随机), 但余额轨迹/方差应反映真实落格而非假设均匀。
    落格分布是纯物理性质, 缓存到 output/slot_dist.json, 物理改动才需重测。"""
    if use_cache and os.path.exists(_SLOT_DIST_CACHE):
        try:
            import json as _json
            with open(_SLOT_DIST_CACHE, encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    import plinko as P
    slots = [0] * P.NUM_SLOTS
    for seed in range(2000, 2000 + n):
        r = random.Random(seed)
        arc_dy = r.uniform(-6.0, 6.0)
        b = P.launch_ball(power, rng=r)
        geo = P.build_geo()
        geo["deflectors"] = [(x1, y1 + arc_dy, x2, y2 + arc_dy)
                             for (x1, y1, x2, y2) in geo["deflectors"]]
        b._stall_retry = 0
        for f in range(4000):
            landed = P.advance_flight(b, geo)
            if landed is not None:
                slots[landed] += 1
                break
            b.events = 0
            if b.amp:
                b.amp.clear()
    total = sum(slots)
    dist = [c / total for c in slots]
    if use_cache:
        try:
            os.makedirs(os.path.dirname(_SLOT_DIST_CACHE), exist_ok=True)
            import json as _json
            with open(_SLOT_DIST_CACHE, "w", encoding="utf-8") as f:
                _json.dump(dist, f)
        except Exception:
            pass
    return dist


def reward_value():
    r = random.random()
    if r < 0.55:
        return 2
    if r < 0.80:
        return 3
    if r < 0.93:
        return 5
    if r < 0.985:
        return 10
    return 20


def roll_multipliers(rtp):
    """9 槽盘面: 每格 q=rtp/REWARD_EV 概率非零, 非零取 reward_value。"""
    q = rtp / REWARD_EV
    return [reward_value() if random.random() < q else 0 for _ in range(NUM_SLOTS)]


def play_once(balance, rtp, plays_target=MAX_PLAYS, slot_dist=None):
    """固定玩 plays_target 发(或中途破产), 返回 (结局, 实际发数, 余额, 轨迹)。
    结局: 'bankrupt'(中途破产, 余额<BET) / 'played'(玩满N发)。
    slot_dist: 落格概率分布(实测物理), None=均匀。RTP 期望与落格分布无关
    (每格倍率独立随机, E[赔付]=ΣP(i)·E[倍率_i]=rtp·50), 但方差/轨迹会反映真实分布。"""
    plays = 0
    trail = [(0, balance)]
    while plays < plays_target:
        if balance < BET:
            return "bankrupt", plays, balance, trail
        board = roll_multipliers(rtp)
        if slot_dist:
            slot = random.choices(range(NUM_SLOTS), weights=slot_dist, k=1)[0]
        else:
            slot = random.randrange(NUM_SLOTS)
        payout = BET * board[slot]
        balance += payout - BET
        plays += 1
        if plays % 10 == 0 or plays == plays_target:
            trail.append((plays, balance))
    return "played", plays, balance, trail


def run_simulation(rtp, n_runs, slot_dist=None):
    """跑 n 局, 汇总统计。slot_dist 为落格分布(实测或None=均匀)。"""
    balances = []
    lifetimes = []
    profits = []                      # 净利润 = 终局余额 - 起始
    bankrupt_lifetimes = []           # 破产者的寿命(高收益档存续者被截断, 破产寿命才是有意义寿命)
    bankrupts = 0
    trails = []
    trail_sample = max(1, n_runs // 200)
    for i in range(n_runs):
        outcome, plays, bal, trail = play_once(DEFAULT_BALANCE, rtp, slot_dist=slot_dist)
        balances.append(bal)
        lifetimes.append(plays)
        profits.append(bal - DEFAULT_BALANCE)
        if outcome == "bankrupt":
            bankrupts += 1
            bankrupt_lifetimes.append(plays)
        if i % trail_sample == 0:
            trails.append(trail)
    bl_stats = _stats(bankrupt_lifetimes) if bankrupt_lifetimes else {"p50": None, "mean": None}
    return {
        "rtp": rtp,
        "n_runs": n_runs,
        "bankrupt_rate": bankrupts / n_runs,
        "survive_rate": (n_runs - bankrupts) / n_runs,   # 存续率(达上限未破产)
        "lifetime": _stats(lifetimes),
        "bankrupt_lifetime": bl_stats,                    # 破产者寿命(不受上限截断)
        "final_balance": _stats(balances),
        "net_profit": _stats(profits),
        "trails": trails,
        "histogram": _histogram(balances, rtp),
        "lifetime_hist": _histogram(lifetimes, None, bins=12),
    }


def _stats(data):
    s = sorted(data)
    n = len(s)
    return {
        "min": s[0], "max": s[-1], "mean": sum(s) / n,
        "p10": s[max(0, int(n * 0.10))],
        "p50": s[max(0, int(n * 0.50))],
        "p90": s[min(n - 1, int(n * 0.90))],
        "p99": s[min(n - 1, int(n * 0.99))],
    }


def _histogram(data, rtp, bins=16):
    """余额/寿命直方图(HTML 用)。余额长尾用对数分箱(破产集中 vs 存活长尾都清晰):
    余额<50(破产区间)合并为前几桶, 其余 log10 分箱。寿命线性分箱。"""
    lo, hi = min(data), max(data)
    if hi - lo < 1e-9:
        hi = lo + 1
    if rtp is not None:
        # 余额: log10 分箱, 偏移 +1 避免 log(0)
        import math as _m
        lo_l, hi_l = _m.log10(max(1.0, lo)), _m.log10(max(1.0, hi + 1))
        counts = [0] * bins
        for v in data:
            if v <= 0:
                idx = 0                       # 破产(余额0)归入第一桶
            else:
                idx = min(bins - 1, int((_m.log10(v) - lo_l) / (hi_l - lo_l) * bins))
            counts[idx] += 1
        # 边界标签: 用真实余额刻度(首桶=0, 末桶=hi)
        edges = [0.0]
        for i in range(1, bins):
            edges.append(round(10 ** (lo_l + (hi_l - lo_l) * i / bins), 1))
        edges.append(round(hi, 1))
        return {"edges": edges, "counts": counts}
    # 寿命: 线性分箱
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in data:
        idx = min(bins - 1, int((v - lo) / width))
        counts[idx] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return {"edges": [round(e, 1) for e in edges], "counts": counts}


# ============================ HTML 报告 ============================
def build_html(results, meta):
    cards = []
    for r in results:
        b = r["final_balance"]
        lt = r["lifetime"]
        bl = r["bankrupt_lifetime"]
        np = r["net_profit"]
        cards.append(f"""
  <div class="card">
    <h3>档位 {RTP_LABEL[r['rtp']]}</h3>
    <table class="mini">
      <tr><td>破产率</td><td class="num">{r['bankrupt_rate']*100:.1f}%</td></tr>
      <tr><td>存续率(玩满{MAX_PLAYS}发)</td><td class="num">{r['survive_rate']*100:.1f}%</td></tr>
      <tr><td>破产前发数 中位</td><td class="num">{bl['p50'] if bl['p50'] is not None else '—'} 发</td></tr>
      <tr><td>终局余额 中位</td><td class="num">{b['p50']:.0f}</td></tr>
      <tr><td>终局余额 P90</td><td class="num">{b['p90']:.0f}</td></tr>
      <tr><td>净赚 中位</td><td class="num">{np['p50']:+.0f}</td></tr>
      <tr><td>净赚 均值</td><td class="num">{np['mean']:+.0f}</td></tr>
    </table>
    <canvas id="h_{RTP_LABEL[r['rtp']].replace('%','p')}" width="420" height="180"></canvas>
  </div>""")

    rows = ""
    for r in results:
        b = r["final_balance"]; lt = r["lifetime"]; bl = r["bankrupt_lifetime"]
        np_ = r["net_profit"]
        rows += f"""
    <tr>
      <td>{RTP_LABEL[r['rtp']]}</td>
      <td>{r['bankrupt_rate']*100:.1f}%</td>
      <td>{r['survive_rate']*100:.1f}%</td>
      <td>{bl['p50'] if bl['p50'] is not None else '—'}</td>
      <td>{b['p10']:.0f}</td><td>{b['p50']:.0f}</td><td>{b['p90']:.0f}</td><td>{b['max']:.0f}</td>
      <td>{np_['mean']:+.0f}</td>
    </tr>"""

    js = _build_js(results)

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>弹珠机余额模拟报告 — {meta['n_runs']}局/档</title>
<style>
  body{{font-family:'Segoe UI',微软雅黑,sans-serif;background:#0e1524;color:#e8eefc;margin:24px}}
  h1{{color:#f0b000}} h2{{color:#39d98a;margin-top:28px}} h3{{color:#7fb0ff;margin:8px 0}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}
  .card{{background:#15223c;border-radius:10px;padding:16px}}
  table{{border-collapse:collapse;width:100%;font-size:13px}}
  td,th{{padding:4px 8px;border-bottom:1px solid #2b436e;text-align:right}}
  th{{color:#8fa0c4;font-weight:normal}} td:first-child,th:first-child{{text-align:left}}
  .num{{color:#39d98a}} .meta{{color:#8fa0c4;font-size:13px}}
  canvas{{width:100%;height:180px;margin-top:8px}}
</style></head><body>
<h1>弹珠机余额模拟报告</h1>
<p class="meta">{meta['note']}<br>
起始 <b>{DEFAULT_BALANCE}</b> 珠 · 每发投 <b>{BET}</b> 珠 · 每档 <b>{meta['n_runs']}</b> 局 · 固定玩 <b>{MAX_PLAYS}</b> 发</p>

<h2>关键指标对比</h2>
<table>
  <tr><th>档位</th><th>破产率</th><th>存续率</th><th>破产前发数</th><th>终局P10</th><th>终局P50</th><th>终局P90</th><th>终局max</th><th>净赚均值</th></tr>
  {rows}
</table>

<h2>终局余额分布</h2>
<div class="grid">{''.join(cards)}</div>

<h2>余额轨迹样例(每档 3 局)</h2>
<div class="grid" id="trails"></div>
<script>{js}</script>
</body></html>"""
    return html


def _build_js(results):
    parts = []
    for r in results:
        label = RTP_LABEL[r['rtp']].replace('%', 'p')
        h = r["histogram"]
        parts.append(f"""
  (function(){{
    var c=document.getElementById('h_{label}'),x=c.getContext('2d');
    c.width=420;c.height=180;x.fillStyle='#15223c';x.fillRect(0,0,420,180);
    var edges={h['edges']},counts={h['counts']};
    var maxC=Math.max(...counts,1),n=edges.length;
    x.fillStyle='#3d8bfd';
    for(var i=0;i<n-1;i++){{
      var w=420/n,hh=counts[i]/maxC*150;
      x.fillRect(i*w+1,175-hh,w-2,hh);
    }}
    x.fillStyle='#e8eefc';x.font='11px sans-serif';
    x.fillText('余额 →',350,168);x.fillText('0',4,170);
    x.fillText(edges[n-1],420-60,168);
  }})();""")
    # 轨迹
    parts.append("""
  (function(){
    var grid=document.getElementById('trails');
    """)
    for r in results:
        label = RTP_LABEL[r['rtp']].replace('%', 'p')
        for ti, tr in enumerate(r["trails"][:3]):
            xs = [p[0] for p in tr]; ys = [p[1] for p in tr]
            maxx = max(xs, default=1); maxy = max(ys, default=DEFAULT_BALANCE)
            _cols = ["#39d98a", "#7fb0ff", "#ffd451"]
            parts.append(f"""
    var d=document.createElement('canvas');d.width=420;d.height=160;
    d.style.height='160px';grid.appendChild(d);
    var x=d.getContext('2d');x.fillStyle='#15223c';x.fillRect(0,0,420,160);
    x.strokeStyle='{_cols[ti % 3]}';x.beginPath();
    var xs={xs},ys={ys},maxx={maxx},maxy={maxy};
    for(var i=0;i<xs.length;i++){{
      var px=xs[i]/maxx*420,py=160-ys[i]/maxy*150;
      if(i==0)x.moveTo(px,py);else x.lineTo(px,py);
    }}
    x.stroke();x.fillStyle='#e8eefc';x.font='11px sans-serif';
    x.fillText('{RTP_LABEL[r['rtp']]} 样例{ti+1}',6,14);
    """)
    parts.append("  })();")
    return "\n".join(parts)


def write_report(results, meta, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, "sim_balance_%s.html" % ts)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(results, meta))
    return path


# ============================ GUI ============================
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog

    root = tk.Tk()
    root.title("余额模拟器 — 弹珠机 RTP 验证")
    root.geometry("760x620")
    root.configure(bg="#0e1524")
    style = ttk.Style(); style.theme_use("clam")
    style.configure("TFrame", background="#0e1524")
    style.configure("TLabel", background="#0e1524", foreground="#e8eefc",
                    font=("Microsoft YaHei", 10))
    style.configure("TButton", font=("Microsoft YaHei", 10))

    mf = ttk.Frame(root, padding=16); mf.pack(fill="both", expand=True)

    tk.Label(mf, text="余额模拟器", font=("Microsoft YaHei", 18, "bold"),
             fg="#f0b000", bg="#0e1524").pack()
    tk.Label(mf, text="起始 1000 珠 · 每发投 50 珠 · 各 RTP 档位余额分布",
             font=("Microsoft YaHei", 9), fg="#8fa0c4", bg="#0e1524").pack(pady=(2, 10))

    # 档位选择
    rtp_frame = ttk.Frame(mf); rtp_frame.pack(fill="x", pady=6)
    tk.Label(rtp_frame, text="档位: ", font=("Microsoft YaHei", 11),
             fg="#e8eefc", bg="#0e1524").pack(side="left")
    rtp_vars = {}
    for lv in RTP_LEVELS:
        v = tk.BooleanVar(value=True)
        rtp_vars[lv] = v
        tk.Checkbutton(rtp_frame, text=RTP_LABEL[lv], variable=v,
                       bg="#0e1524", fg="#e8eefc", selectcolor="#15223c",
                       font=("Microsoft YaHei", 11)).pack(side="left", padx=6)

    # 局数
    ctrl = ttk.Frame(mf); ctrl.pack(fill="x", pady=6)
    tk.Label(ctrl, text="每档局数:", font=("Microsoft YaHei", 11),
             fg="#e8eefc", bg="#0e1524").pack(side="left")
    n_var = tk.StringVar(value="5000")
    tk.Entry(ctrl, textvariable=n_var, width=8).pack(side="left", padx=6)

    run_btn = tk.Button(ctrl, text="运行", font=("Microsoft YaHei", 12, "bold"),
                        bg="#39d98a", fg="#0e1524", relief="flat", padx=20, cursor="hand2")
    run_btn.pack(side="left", padx=10)
    open_btn = tk.Button(ctrl, text="打开HTML", font=("Microsoft YaHei", 11),
                         bg="#3563d1", fg="#fff", relief="flat", padx=12, cursor="hand2",
                         state="disabled")
    open_btn.pack(side="left")

    status_var = tk.StringVar(value="就绪"); last_path = [None]
    tk.Label(mf, textvariable=status_var, font=("Microsoft YaHei", 10),
             fg="#39d98a", bg="#0e1524").pack(pady=(4, 2))

    tree = ttk.Treeview(mf, columns=("rtp", "br", "sv", "bll", "p50", "p90", "np"),
                        show="headings", height=10)
    for c, t, w in [("rtp", "档位", 70), ("br", "破产率", 85), ("sv", "存续率", 85),
                    ("bll", "破产寿命", 85), ("p50", "终局P50", 90), ("p90", "终局P90", 90),
                    ("np", "净赚均值", 95)]:
        tree.heading(c, text=t); tree.column(c, width=w, anchor="center")
    tree.pack(fill="both", expand=True, pady=8)

    def on_run():
        try:
            n = int(n_var.get())
            if n <= 0: status_var.set("局数>0"); return
        except ValueError: status_var.set("局数格式错"); return
        selected = [lv for lv in RTP_LEVELS if rtp_vars[lv].get()]
        if not selected: status_var.set("至少选一个档位"); return
        run_btn["state"] = "disabled"; open_btn["state"] = "disabled"
        tree.delete(*tree.get_children())
        status_var.set("实测落格分布..."); root.update()
        slot_dist = measure_slot_dist()
        status_var.set("运行中...")
        root.update()
        results = []
        t0 = time.time()
        for i, lv in enumerate(selected):
            res = run_simulation(lv, n, slot_dist=slot_dist)
            results.append(res)
            b = res["final_balance"]; bl = res["bankrupt_lifetime"]
            np_ = res["net_profit"]
            tree.insert("", "end", values=(
                RTP_LABEL[lv], "%.1f%%" % (res["bankrupt_rate"] * 100),
                "%.1f%%" % (res["survive_rate"] * 100),
                "%d" % (bl["p50"] if bl["p50"] is not None else 0),
                "%.0f" % b["p50"], "%.0f" % b["p90"], "%+.0f" % np_["mean"]))
            status_var.set("档位 %s 完成 (%d/%d)..." % (RTP_LABEL[lv], i + 1, len(selected)))
            root.update()
        meta = {"n_runs": n, "note": "GUI 手动运行 · 落格=实测物理分布 · 耗时 %.0fs"
                % (time.time() - t0)}
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        path = write_report(results, meta, out_dir)
        last_path[0] = path
        status_var.set("完成! 报告: %s" % os.path.basename(path))
        open_btn["state"] = "normal"; run_btn["state"] = "normal"

    def on_open():
        if last_path[0] and os.path.exists(last_path[0]):
            os.startfile(last_path[0])

    run_btn["command"] = on_run; open_btn["command"] = on_open
    root.mainloop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true", help="无界面跑默认档位")
    ap.add_argument("--runs", type=int, default=5000, help="每档局数")
    ap.add_argument("--rtp", default="0.8,1.0,1.2,2.0", help="档位列表")
    args = ap.parse_args()
    if args.headless:
        levels = [float(x) for x in args.rtp.split(",")]
        print("无界面模拟: 档位 %s × %d 局" % (levels, args.runs))
        print("实测落格分布(缓存优先)...")
        slot_dist = measure_slot_dist()
        print("  落格分布:", ["%.1f%%" % (p * 100) for p in slot_dist])
        results = [run_simulation(lv, args.runs, slot_dist=slot_dist) for lv in levels]
        meta = {"n_runs": args.runs, "note": "headless 自动运行 · 落格=实测物理分布"}
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        p = write_report(results, meta, out_dir)
        print("报告: %s" % p)
        for r in results:
            bl = r["bankrupt_lifetime"]
            print("  %s: 破产率%.1f%% 存续率%.1f%% 破产寿命中位%s 终局P50=%.0f 净赚均值=%+.0f"
                  % (RTP_LABEL[r["rtp"]], r["bankrupt_rate"] * 100, r["survive_rate"] * 100,
                     ("%d" % bl["p50"]) if bl["p50"] is not None else "—",
                     r["final_balance"]["p50"], r["net_profit"]["mean"]))
    else:
        run_gui()
