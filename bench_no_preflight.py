# -*- coding: utf-8 -*-
"""无预判基准测试: 去掉 preflight_check, 只靠速度踢, 测真实飞行帧数分布。

物理层精确复制自 main.py。GUI(tkinter): 开始/停止 + 时长 + 线程数 + 实时进度。
输出: output/bench_no_preflight_*.md
"""

import sys, os, math, random, time, threading, multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed

# ======================== 物理常量(从 main.py 精确同步) ========================
CW, CH = 520, 660; WALL = 12; FLOOR = CH - WALL
LANE_W = 42; LANE_WALL_TH = 7; RIGHT_INNER = CW - WALL
LANE_L = RIGHT_INNER - LANE_W; FIELD_R = LANE_L - LANE_WALL_TH
FIELD_L = WALL; FIELD_W = FIELD_R - FIELD_L
NUM_SLOTS = 9; SLOT_W = FIELD_W / NUM_SLOTS; SLOT_H = 32
SLOT_TOP = FLOOR - SLOT_H; DIV_W = 6; DIV_TOP = SLOT_TOP - 10
PEG_TOP = 150; PEG_SY = 55; PEG_ROWS = 7; PEG_R = 6; BALL_R = 9
PEG_SX = FIELD_W / NUM_SLOTS; LANE_WALL_TOP = 160
PLUNGER_X = (LANE_L + RIGHT_INNER) / 2.0; PLUNGER_Y = FLOOR - BALL_R - 2
RISER_Y = PEG_TOP + (PEG_ROWS - 1) * PEG_SY + BALL_R + PEG_R
G = 1200.0; E = 0.20; WALL_E = 0.5; RAIL_E = 0.55; VMAX = 2400.0
E_FAST = 0.18; E_SLOW = 0.60; E_VREF = 700.0
STEER_MIN_VY = 320.0
FIXED_DT = 1.0 / 60.0; SUBSTEPS = 6; JITTER = 6.0
LAUNCH_MIN = 1180.0; LAUNCH_MAX = 1220.0; CHARGE_RATE = 0.9
CROSS_VX_MIN = 320.0; CROSS_VX_MAX = 460.0
CROSS_K = 80.0; CROSS_DAMP = 0.80; ALIGN_K = 60.0; ALIGN_H = 50.0
ALIGN_DAMP = 0.86; ALIGN_VX_MAX = 300.0; STEER_DVX_MAX = 200.0
STEER_VX_MAX = 800.0; STEER_MIN = 8.0; STEER_MAX = 20.0
ASCENT_PULL = 0.45; ENTRY_X = FIELD_L + 90
MISFIRE_POWER = 0.15; MISFIRE_E = 0.22; MISFIRE_BOUNCE_VY = 200.0
TEASE_START_Y = PEG_TOP + 2 * PEG_SY; TEASE_FRAC = 0.80
TEASE_END_Y = PEG_TOP + 4 * PEG_SY; TEASE_MIN_VY = 150.0
SLOT_BRAKE_VY = 0.65; SLOT_BRAKE_VX = 0.5
LAND_E = 0.42; LAND_BOUNCE_MIN_VY = 220.0; LAND_K = 16.0; LAND_DAMP = 0.80
EV_PEG = 1; EV_CEIL = 2; EV_WALL = 4; EV_DIV = 8
STALL_MAX_RETRY = 5; STALL_FRAMES_KICK = 72; STALL_FRAMES_SETTLE = 240
MAX_FLIGHT_FRAMES = 10000


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def power_u(power):
    return clamp((power - MISFIRE_POWER) / (1.0 - MISFIRE_POWER), 0.0, 1.0)


# ======================== Ball ========================
class Ball:
    __slots__ = ('x', 'y', 'vx', 'vy', 'item', 'born', 'events', 'amp',
                 'cross_vx', 'climb', 'misfire', 'tease_dx',
                 'launch_power', '_stall_retry', '_land_primed')

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)


# ======================== 几何(精确复制 main.py) ========================
def build_pegs():
    pegs = []
    for r in range(PEG_ROWS):
        y = PEG_TOP + r * PEG_SY
        if r % 2 == 0:
            xs = [FIELD_L + (i + 0.5) * PEG_SX for i in range(NUM_SLOTS)]
        else:
            xs = [FIELD_L + i * PEG_SX for i in range(1, NUM_SLOTS)]
        for x in xs:
            pegs.append((x, y))
    return pegs


def build_dividers():
    divs = []
    for k in range(1, NUM_SLOTS):
        x = FIELD_L + k * SLOT_W
        divs.append((x - DIV_W / 2.0, DIV_TOP, x + DIV_W / 2.0, FLOOR))
    return divs


def build_walls():
    return [
        (0, 0, CW, WALL),
        (0, 0, WALL, CH),
        (RIGHT_INNER, 0, CW, CH),
        (0, FLOOR, CW, CH),
        (FIELD_R, LANE_WALL_TOP, LANE_L, FLOOR),
    ]


def build_deflectors():
    cpts = [(510, 108), (492, 101), (480, 94), (466, 90), (450, 88)]
    segs = []
    for i in range(len(cpts) - 1):
        x1, y1 = cpts[i]; x2, y2 = cpts[i + 1]
        sub = 5
        for j in range(sub):
            t0, t1 = j / sub, (j + 1) / sub
            segs.append((x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0,
                         x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1))
    return segs


def build_geo():
    return {
        "pegs": build_pegs(),
        "dividers": build_dividers(),
        "walls": build_walls(),
        "deflectors": build_deflectors(),
    }


# ======================== 碰撞(精确复制 main.py) ========================
def _reflect(b, nx, ny, e):
    vn = b.vx * nx + b.vy * ny
    if vn < 0:
        b.vx -= (1 + e) * vn * nx
        b.vy -= (1 + e) * vn * ny
        return -vn
    return 0.0


def _mark(b, bit, sp):
    b.events = b.events | bit
    amp = b.amp
    if amp is None:
        amp = {}; b.amp = amp
    if sp > amp.get(bit, 0.0):
        amp[bit] = sp


def _collide_pegs(b, pegs):
    rr = BALL_R + PEG_R
    for px, py in pegs:
        dx, dy = b.x - px, b.y - py
        d2 = dx * dx + dy * dy
        if d2 < rr * rr:
            d = math.sqrt(d2)
            if d > 1e-9:
                nx, ny = dx / d, dy / d
            else:
                a = random.uniform(0, math.tau)
                nx, ny = math.cos(a), math.sin(a)
            b.x = px + nx * rr
            b.y = py + ny * rr
            vn = -(b.vx * nx + b.vy * ny)
            if vn > 0:
                E_eff = E_SLOW - (E_SLOW - E_FAST) * clamp(vn / E_VREF, 0.0, 1.0)
                g = random.gauss(0, 0.04)
                g = clamp(g, -0.15, 0.15)
                tx_, ty_ = -ny, nx
                njx = nx + tx_ * g; njy = ny + ty_ * g
                nrm = math.hypot(njx, njy)
                njx /= nrm; njy /= nrm
                _reflect(b, njx, njy, E_eff)
                _mark(b, EV_PEG, abs(vn))


def _collide_rect(b, rx1, ry1, rx2, ry2, e, ev=0):
    cx = max(rx1, min(b.x, rx2))
    cy = max(ry1, min(b.y, ry2))
    dx, dy = b.x - cx, b.y - cy
    d2 = dx * dx + dy * dy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = dx / d, dy / d
        else:
            left, right = b.x - rx1, rx2 - b.x
            top, bot = b.y - ry1, ry2 - b.y
            m = min(left, right, top, bot)
            if m == left:      nx, ny = -1.0, 0.0
            elif m == right:   nx, ny = 1.0, 0.0
            elif m == top:     nx, ny = 0.0, -1.0
            else:             nx, ny = 0.0, 1.0
        b.x = cx + nx * BALL_R
        b.y = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        if ev and hit > 0.0:
            _mark(b, ev, hit)


def _collide_segment(b, x1, y1, x2, y2, e, jitter=JITTER):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else ((b.x - x1) * dx + (b.y - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    cx, cy = x1 + t * dx, y1 + t * dy
    ox, oy = b.x - cx, b.y - cy
    d2 = ox * ox + oy * oy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = ox / d, oy / d
        else:
            nx, ny = 0.0, -1.0
        b.x = cx + nx * BALL_R
        b.y = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        if hit > 0.0:
            _mark(b, EV_CEIL, hit)
        if jitter:
            tx, ty = -ny, nx
            j = random.uniform(-jitter, jitter)
            b.vx += tx * j
            b.vy += ty * j


# ======================== 物理步进(精确复制 main.py) ========================
def physics_step(b, geo, dt):
    sub = dt / SUBSTEPS
    for _ in range(SUBSTEPS):
        b.vy += G * sub
        sp = math.hypot(b.vx, b.vy)
        if sp > VMAX:
            f = VMAX / sp
            b.vx *= f; b.vy *= f
        b.x += b.vx * sub
        b.y += b.vy * sub
        for w in geo["walls"]:
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _collide_segment(b, s[0], s[1], s[2], s[3], RAIL_E, JITTER)
        _collide_pegs(b, geo["pegs"])
        for d in geo["dividers"]:
            _collide_rect(b, d[0], d[1], d[2], d[3], E, EV_DIV)
        if b.y + BALL_R >= FLOOR - 0.5:
            i = int((b.x - FIELD_L) / SLOT_W)
            return max(0, min(NUM_SLOTS - 1, i))
    return None


# ======================== 引导(精确复制 main.py) ========================
def steer_ball(b, target_x):
    if b.misfire:
        return
    if b.vy >= 0:
        b.climb = False
    if b.vy < 0 and b.y > LANE_WALL_TOP - BALL_R:
        return
    if b.x >= FIELD_R:
        b.vx += (ENTRY_X - b.x) * CROSS_K * FIXED_DT
        b.vx *= CROSS_DAMP
        cv = getattr(b, "cross_vx", CROSS_VX_MAX)
        b.vx = clamp(b.vx, -cv, cv)
        return
    if b.y > SLOT_TOP - ALIGN_H:
        b.vx += (target_x - b.x) * ALIGN_K * FIXED_DT
        b.vx *= ALIGN_DAMP
        b.vx = clamp(b.vx, -ALIGN_VX_MAX, ALIGN_VX_MAX)
        return
    progress = (b.y - PEG_TOP) / max(1.0, SLOT_TOP - PEG_TOP)
    progress = max(0.0, min(1.0, progress))
    pull = STEER_MIN + (STEER_MAX - STEER_MIN) * progress
    climbing = b.climb
    if climbing:
        pull *= ASCENT_PULL
    tx = target_x
    if ((not climbing) and TEASE_START_Y < b.y < TEASE_END_Y
            and b.vy > TEASE_MIN_VY):
        tx += b.tease_dx
    dvx = clamp((tx - b.x) * pull * FIXED_DT, -STEER_DVX_MAX, STEER_DVX_MAX)
    # 慢速撤引导: vy→0时缩dvx
    if not climbing:
        f = clamp(b.vy / STEER_MIN_VY, 0.0, 1.0) if b.vy > 0 else 0.0
        dvx *= f
    b.vx += dvx
    if (not climbing) and ((b.vx > 0.0) != (tx - b.x > 0.0)):
        b.vx *= 0.7
    b.vx = clamp(b.vx, -STEER_VX_MAX, STEER_VX_MAX)


def advance_flight(b, geo, target_x):
    steer_ball(b, target_x)
    if b.y > SLOT_TOP and b.vy > 0:
        b.vy *= SLOT_BRAKE_VY
        b.vx *= SLOT_BRAKE_VX
    return physics_step(b, geo, FIXED_DT)


# ======================== 盘面/落点(精确复制 main.py) ========================
MULTIPLIER_POOL = [2, 3, 5, 10, 20]
MULTIPLIER_WEIGHTS = [0.55, 0.25, 0.13, 0.055, 0.015]


def roll_multipliers(rtp_target):
    max_reward = NUM_SLOTS - 2
    min_reward = max(1, int(max_reward * 0.3))
    n_reward = random.randint(min_reward, max_reward)
    reward_slots = set(random.sample(range(NUM_SLOTS), n_reward))
    mult = [0] * NUM_SLOTS
    for i in reward_slots:
        mult[i] = random.choices(MULTIPLIER_POOL, weights=MULTIPLIER_WEIGHTS, k=1)[0]
    return mult


def choose_target(mult, rtp_target):
    n_pos = sum(1 for m in mult if m > 0)
    n_zero = NUM_SLOTS - n_pos
    w = min(1.0, rtp_target / (sum(mult) / NUM_SLOTS))
    weights = []
    for m in mult:
        if m > 0:
            weights.append(w / n_pos if n_pos > 0 else 0)
        else:
            weights.append((1.0 - w) / n_zero if n_zero > 0 else 0)
    total_w = sum(weights)
    weights = [x / total_w for x in weights]
    return random.choices(range(NUM_SLOTS), weights=weights, k=1)[0]


def tease_dx(mult, target_slot):
    nb = target_slot - 1
    if nb >= 0 and mult[nb] > mult[target_slot]:
        return -TEASE_FRAC * SLOT_W
    nb = target_slot + 1
    if nb < NUM_SLOTS and mult[nb] > mult[target_slot]:
        return TEASE_FRAC * SLOT_W
    return 0.0


def launch_ball(power):
    u = power_u(power)
    speed = LAUNCH_MIN + (LAUNCH_MAX - LAUNCH_MIN) * u
    return Ball(x=PLUNGER_X, y=PLUNGER_Y, vx=0.0, vy=-speed,
                item=None, born=time.time(), events=0, amp={},
                cross_vx=CROSS_VX_MIN + (CROSS_VX_MAX - CROSS_VX_MIN) * u,
                climb=True, misfire=False, tease_dx=0.0,
                launch_power=power, _stall_retry=0)


# ======================== 单次飞行(无预判 + 速度踢) ========================
def simulate_one_flight(geo, rtp_target):
    """发射→落地, 无 preflight_check, 含速度踢。返回 (帧数, 踢次数)。"""
    mult = roll_multipliers(rtp_target)
    target_slot = choose_target(mult, rtp_target)
    target_x = FIELD_L + (target_slot + 0.5) * SLOT_W
    power = random.uniform(MISFIRE_POWER, 1.0)
    b = launch_ball(power)
    b.tease_dx = tease_dx(mult, target_slot)

    stall_frames = 0
    kick_count = 0
    lx, ly = b.x, b.y
    total_frames = 0

    while total_frames < MAX_FLIGHT_FRAMES:
        total_frames += 1
        landed = advance_flight(b, geo, target_x)
        if landed is not None:
            return (total_frames, kick_count, "landed")

        # 速度踢(与 main.py _frame 同步)
        if (b.x - lx) ** 2 + (b.y - ly) ** 2 > 1.0:
            stall_frames = 0
        else:
            stall_frames += 1
        nudge = getattr(b, "_stall_retry", 0)
        if stall_frames > STALL_FRAMES_KICK and nudge < STALL_MAX_RETRY:
            b.vy = max(abs(b.vy) + 80.0, 400.0)
            b.vx += random.uniform(-150.0, 150.0)
            b._stall_retry = nudge + 1
            stall_frames = 0
            kick_count += 1
        elif stall_frames > STALL_FRAMES_SETTLE:
            return (total_frames, kick_count, "settle")
        lx, ly = b.x, b.y

    return (total_frames, kick_count, "max_frames")


# ======================== 工作线程 ========================
# ======================== 工作进程(供 ProcessPoolExecutor) ========================
def _worker_task(args):
    """(duration, rtp_target) → results list. 模块级函数, Windows spawn 可 pickle。"""
    duration, rtp_target = args
    random.seed()
    geo = build_geo()
    results = []
    deadline = time.time() + duration
    while time.time() < deadline:
        f, k, outcome = simulate_one_flight(geo, rtp_target)
        results.append((f, k, outcome))
    return results


def worker(duration, result_queue, stop_event, rtp_target):
    """兼容旧接口(threading 版): duration 秒内不断飞行, 结果放入队列。"""
    results = _worker_task((duration, rtp_target))
    result_queue.put(results)


# ======================== 统计 ========================
def calc_percentiles(data):
    """data: list of frames (int). 返回分位数 dict。"""
    if not data:
        return {}
    s = sorted(data)
    n = len(s)

    def p(pct):
        idx = int(math.ceil(pct / 100.0 * n)) - 1
        return s[max(0, min(n - 1, idx))]

    return {
        "count": n, "min": s[0], "max": s[-1], "mean": sum(s) / n,
        "p50": p(50), "p90": p(90), "p95": p(95),
        "p99": p(99), "p99_9": p(99.9), "p99_99": p(99.99),
    }


def kick_distribution(results):
    """results: list of (frames, kicks). 返回 {kick_count: [frames,...]}。"""
    dist = {}
    for f, k in results:
        dist.setdefault(k, []).append(f)
    return dist


# ======================== MD 报告 ========================
def write_md_report(stats, total, valid, stalled_settle, stalled_max, kick_dist, num_threads, elapsed):
    os.makedirs("output", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = f"output/bench_no_preflight_{ts}.md"

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 无预判基准测试报告\n\n")
        f.write(f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}  ")
        f.write(f"**时长**: {elapsed:.0f}s  **线程**: {num_threads}  ")
        f.write(f"**步长**: {FIXED_DT*1000:.0f}ms (1/60s)\n\n")

        f.write(f"## 配置\n\n")
        f.write(f"| 参数 | 值 |\n|------|----|\n")
        f.write(f"| preflight_check | off |\n")
        f.write(f"| 速度踢 | {STALL_FRAMES_KICK}步不动→vy=max(abs(vy)+80,400), vx+=±150 |\n")
        f.write(f"| 最大重踢 | {STALL_MAX_RETRY} |\n")
        f.write(f"| 强制结算 | {STALL_FRAMES_SETTLE}步不动→直接结算 |\n")
        f.write(f"| 绝对上限 | {MAX_FLIGHT_FRAMES}帧 |\n\n")

        f.write(f"## 总览\n\n")
        f.write(f"| 指标 | 值 |\n|------|----|\n")
        f.write(f"| 总发射 | {total} |\n")
        f.write(f"| 速率 | {total/elapsed:.0f} 次/s (耗时{elapsed:.0f}s) |\n")
        f.write(f"| 正常落地 | {valid} |\n")
        f.write(f"| 4秒保底强制结算 | {stalled_settle} |\n")
        f.write(f"| 超绝对上限(>{MAX_FLIGHT_FRAMES}帧) | {stalled_max} |\n")
        total_stalled = stalled_settle + stalled_max
        f.write(f"| 异常率 | {total_stalled/total*100:.4f}% |\n\n")

        f.write(f"## 飞行帧数分布(仅正常落地)\n\n")
        f.write(f"| 百分位 | 帧数 | 时间(s) |\n|--------|------|--------|\n")
        for label, key in [("P50", "p50"), ("P90", "p90"), ("P95", "p95"),
                            ("P99", "p99"), ("P99.9", "p99_9"), ("P99.99", "p99_99")]:
            frames = stats[key]
            f.write(f"| {label} | {frames} | {frames*FIXED_DT:.2f} |\n")

        f.write(f"\n| 指标 | 值 |\n|------|----|\n")
        f.write(f"| 最小 | {stats['min']}帧 ({stats['min']*FIXED_DT:.2f}s) |\n")
        f.write(f"| 最大 | {stats['max']}帧 ({stats['max']*FIXED_DT:.2f}s) |\n")
        f.write(f"| 平均 | {stats['mean']:.1f}帧 ({stats['mean']*FIXED_DT:.2f}s) |\n")

        # 速度踢分布(0~10完整)
        f.write(f"\n## 速度踢分布 (0~10)\n\n")
        f.write(f"| 踢次数 | 飞行数 | 占比 | 平均帧数 | P50帧 | P99帧 | 最大帧 |\n")
        f.write(f"|--------|--------|------|----------|-------|-------|--------|\n")
        for k in range(11):
            frames_list = kick_dist.get(k, [])
            nk = len(frames_list)
            pk = nk / total * 100 if total else 0
            if nk == 0:
                f.write(f"| {k} | 0 | - | - | - | - | - |\n")
            else:
                avg_k = sum(frames_list) / nk
                sk = sorted(frames_list)
                p50_k = sk[len(sk)//2]
                p99_k = sk[int(len(sk)*0.99)] if len(sk) > 1 else sk[0]
                max_k = sk[-1]
                f.write(f"| {k} | {nk} | {pk:.2f}% | {avg_k:.0f} | {p50_k} | {p99_k} | {max_k} |\n")

        f.write(f"\n## 结论\n\n")
        if total_stalled == 0:
            f.write(f"无异常。4秒保底和绝对上限均未触发。\n\n")
        else:
            f.write(f"4秒保底: {stalled_settle}次, 绝对上限: {stalled_max}次\n\n")
        worst = stats["p99_99"]
        if worst <= 1000:
            f.write(f"P99.99={worst}帧 <= 1000帧(preflight阈值)。速度踢可替代 preflight_check。\n")
        else:
            f.write(f"P99.99={worst}帧 > 1000帧。preflight_check 仍有必要。\n")

    print(f"报告: {path}")


# ======================== tkinter GUI ========================
def run_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("无预判基准测试 — 速度踢 vs preflight")
    root.geometry("740x620")
    root.configure(bg="#0b1220")

    style = ttk.Style(); style.theme_use("clam")
    style.configure("TFrame", background="#0b1220")
    style.configure("TLabel", background="#0b1220", foreground="#e8eefc",
                    font=("Microsoft YaHei", 10))
    style.configure("TRadiobutton", background="#0b1220", foreground="#e8eefc",
                    font=("Microsoft YaHei", 10))
    style.configure("blue.Horizontal.TProgressbar", troughcolor="#15223c",
                    background="#3d8bfd", bordercolor="#15223c", lightcolor="#3d8bfd",
                    darkcolor="#2a6fd1")

    mf = ttk.Frame(root, padding=20); mf.pack(fill="both", expand=True)

    tk.Label(mf, text="无预判基准测试", font=("Microsoft YaHei", 18, "bold"),
             fg="#f0b000", bg="#0b1220").pack()
    tk.Label(mf, text="去掉 preflight_check, 仅靠速度踢, 多线程实测飞行帧数分布",
             font=("Microsoft YaHei", 9), fg="#8fa0c4", bg="#0b1220").pack(pady=(2, 14))

    ctrl = ttk.Frame(mf); ctrl.pack(fill="x", pady=(0, 10))
    ttk.Label(ctrl, text="时长(s):").pack(side="left"); dur_var = tk.StringVar(value="30")
    ttk.Entry(ctrl, textvariable=dur_var, width=5).pack(side="left", padx=(2, 14))
    ttk.Label(ctrl, text="线程:").pack(side="left")
    thread_var = tk.IntVar(value=min(8, os.cpu_count() or 4))
    ttk.Spinbox(ctrl, from_=1, to=32, textvariable=thread_var, width=3).pack(side="left", padx=(2, 14))

    self_btn = tk.Button(ctrl, text="开始", font=("Microsoft YaHei", 11, "bold"),
                         bg="#39d98a", fg="#0b1220", relief="flat", padx=18, cursor="hand2")
    self_btn.pack(side="left", padx=(0, 8))
    stop_btn = tk.Button(ctrl, text="停止", font=("Microsoft YaHei", 11),
                         bg="#e0533b", fg="#fff", relief="flat", padx=14, cursor="hand2",
                         state="disabled")
    stop_btn.pack(side="left")

    status_var = tk.StringVar(value="就绪"); prog = ttk.Progressbar(mf, length=700,
        style="blue.Horizontal.TProgressbar")
    tk.Label(mf, textvariable=status_var, font=("Microsoft YaHei", 10),
             fg="#8fa0c4", bg="#0b1220").pack(pady=(6, 2))
    prog.pack(pady=(0, 8))

    live_var = tk.StringVar(value="发射: 0  卡死: 0  最新: --")
    tk.Label(mf, textvariable=live_var, font=("Consolas", 10),
             fg="#39d98a", bg="#0b1220").pack(pady=(0, 8))

    rf = ttk.Frame(mf); rf.pack(fill="both", expand=True)
    tree = ttk.Treeview(rf, columns=("k", "v", "n"), show="headings", height=14)
    tree.heading("k", text="指标"); tree.heading("v", text="值"); tree.heading("n", text="说明")
    tree.column("k", width=110); tree.column("v", width=110); tree.column("n", width=460)
    tree.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(rf, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")

    running = [False]

    def upd_prog(t0, dur):
        if not running[0]: return
        prog["value"] = min(100, (time.time()-t0)/dur*100)
        status_var.set(f"测试中… {time.time()-t0:.0f}s / {dur}s")
        root.after(500, lambda: upd_prog(t0, dur))

    def on_start():
        try:
            dur = float(dur_var.get())
            if dur <= 0: status_var.set("时长>0"); return
        except ValueError: status_var.set("时长格式错"); return
        n = thread_var.get(); running[0] = True
        self_btn["state"] = "disabled"; stop_btn["state"] = "normal"
        tree.delete(*tree.get_children()); prog["value"] = 0
        tree.insert("", "end", values=("测试中…", "", ""))
        t0 = time.time(); upd_prog(t0, dur)

        # 后台线程等结果, 不阻塞 GUI
        def run_and_collect():
            all_results = []
            try:
                with ProcessPoolExecutor(max_workers=n) as executor:
                    futures = [executor.submit(_worker_task, (dur, 1.0)) for _ in range(n)]
                    for future in as_completed(futures):
                        try:
                            all_results.extend(future.result())
                        except Exception as e:
                            print(f"进程错误: {e}")
            except Exception as e:
                print(f"多进程启动失败, 回退单进程: {e}")
                all_results = _worker_task((dur, 1.0))

            # 回到主线程更新 GUI
            root.after(0, lambda: show_results(all_results, t0))

        threading.Thread(target=run_and_collect, daemon=True).start()

    def show_results(all_results, start_time):
        running[0] = False; self_btn["state"] = "normal"; stop_btn["state"] = "disabled"
        prog["value"] = 100
        if not all_results:
            status_var.set("无数据"); return

        landed = [(f,k) for f,k,o in all_results if o == "landed"]
        settled = [(f,k) for f,k,o in all_results if o == "settle"]
        maxed = [(f,k) for f,k,o in all_results if o == "max_frames"]
        valid_frames = [f for f,k in landed]
        if not valid_frames:
            status_var.set("无有效飞行"); return

        kick_dist = kick_distribution(landed)
        stats = calc_percentiles(valid_frames)
        elapsed = time.time() - start_time
        tree.delete(*tree.get_children())

        def a(k, v, n=""): tree.insert("", "end", values=(k, str(v), n))
        a("总发射", len(all_results))
        a("每秒发射", f"{len(all_results)/elapsed:.0f} 次/s", f"耗时{elapsed:.0f}s")
        a("正常落地", len(landed))
        a("4s保底强制结算", len(settled), "240步不动→直接结算")
        a("超绝对上限", len(maxed), f">{MAX_FLIGHT_FRAMES}帧")
        a("---", "---", "")
        for lb, k in [("P50", "p50"), ("P90", "p90"), ("P95", "p95"),
                      ("P99", "p99"), ("P99.9", "p99_9"), ("P99.99", "p99_99")]:
            a(lb, f"{stats[k]:d}帧", f"{stats[k]*FIXED_DT:.2f}s")
        a("---", "---", "")
        a("最小", f"{stats['min']:d}帧", f"{stats['min']*FIXED_DT:.2f}s")
        a("最大", f"{stats['max']:d}帧", f"{stats['max']*FIXED_DT:.2f}s")
        a("平均", f"{stats['mean']:.1f}帧", f"{stats['mean']*FIXED_DT:.2f}s")
        a("---", "速度踢分布(0~10)", "---")
        for k in range(11):
            fk = kick_dist.get(k, [])
            nk = len(fk)
            pk = nk / len(all_results) * 100
            if nk == 0:
                a(f"踢{k}次", "0", "-")
            else:
                sk = sorted(fk)
                avg_k = sum(fk) / nk
                a(f"踢{k}次", f"{nk}次 ({pk:.2f}%)",
                  f"平均{avg_k:.0f}帧  P50={sk[len(sk)//2]}  P99={sk[int(len(sk)*0.99)] if len(sk)>1 else sk[0]}  最大{sk[-1]}")
        n_procs = thread_var.get()
        status_var.set(f"完成! P50={stats['p50']} P99.99={stats['p99_99']} 保底={len(settled)}")
        write_md_report(stats, len(all_results), len(landed), len(settled),
                        len(maxed), kick_dist, n_procs, elapsed)

    def on_stop():
        running[0] = False
        self_btn["state"] = "normal"; stop_btn["state"] = "disabled"
        status_var.set("已停止(进行中的进程将继续)")

    self_btn["command"] = on_start; stop_btn["command"] = on_stop
    root.mainloop()


if __name__ == "__main__":
    run_gui()
