# -*- coding: utf-8 -*-
# 跳跳的弹珠机 — Android (Kivy) 版。
# 本文件由 tools/build_android_main.py 自动拼接生成, 请勿直接编辑:
#   - 几何/物理/音效合成/自测段 原样抽取自 plinko.py
#   - 手写段在 tools/android_part_{head,backends,ui}.py
import sys
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")   # 自定义参数(--selftest/--smoke)自己解析, 别让 Kivy 抢

import math
import random
import array
import struct
import threading
import tempfile
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, Line, Ellipse
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.utils import platform

# 中文字体: 用 name="Roboto" 覆盖 Kivy 默认字体, 所有控件全局生效(否则 Android 上汉字全豆腐块)
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "NotoSansSC-Medium.otf")
try:
    if os.path.exists(_FONT_PATH):
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH)
except Exception:
    pass


def hex_rgb(h):
    """'#rrggbb' -> (r,g,b) 0~1 浮点(Kivy Color 用)。"""
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)

# ----------------------------- 尺寸 / 区域 --------------------------------
CW, CH = 520, 660            # 画布尺寸
WALL = 12                    # 外墙厚度
FLOOR = CH - WALL            # 地板 y

LANE_W = 42                  # 右侧发射通道宽度
LANE_WALL_TH = 7             # 通道与场区之间的隔墙厚度
RIGHT_INNER = CW - WALL      # 右外墙内沿
LANE_L = RIGHT_INNER - LANE_W        # 通道左沿 = 466
FIELD_R = LANE_L - LANE_WALL_TH      # 场区右沿 = 459
FIELD_L = WALL                       # 场区左沿 = 12
FIELD_W = FIELD_R - FIELD_L          # 场区宽 = 447

NUM_SLOTS = 9                # 底部倍率槽数量
SLOT_W = FIELD_W / NUM_SLOTS
SLOT_H = 32                  # 倍率槽高度(很矮)
SLOT_TOP = FLOOR - SLOT_H
DIV_W = 6                    # 槽隔板宽度(加宽, 更像参考的竖挡板)
DIV_TOP = SLOT_TOP - 10      # 隔板顶(高于槽口, 强化"过挡板"观感)

PEG_TOP = 150                # 钉阵首行 y(上方留出进球/弧线区)
PEG_SY = 55                  # 行距(增宽: 少碰撞加速下落)
PEG_ROWS = 7                 # 行数(再减: 更少碰撞)
PEG_R = 6                    # 钉子半径
BALL_R = 9                   # 小球半径
PEG_SX = FIELD_W / NUM_SLOTS # 钉子水平间距(交错均匀网格)

LANE_WALL_TOP = 160          # 通道隔墙顶部(进一步降低: 更宽敞的入场窗口)
PLUNGER_X = (LANE_L + RIGHT_INNER) / 2.0
PLUNGER_Y = FLOOR - BALL_R - 2
RISER_Y = PEG_TOP + (PEG_ROWS - 1) * PEG_SY + BALL_R + PEG_R   # 495: 无钉区起点

# ----------------------------- 物理常量 -----------------------------------
G = 1200.0                   # 重力 px/s^2(大幅提高: 加速下落, 缩短飞行时间)
E = 0.20                     # 钉子恢复系数(再降: 接近自由落体穿过钉阵)
WALL_E = 0.5
VMAX = 2400.0                # 限速(需 >= 最大发射速度, 防穿透)
FIXED_DT = 1.0 / 60.0
FRAME_MS = 16
SUBSTEPS = 6                 # 子步数(增加: 高速下防穿透)
JITTER = 6.0                 # 撞钉切向随机扰动(大幅降低: 防方向突变 + 防卡死)

LAUNCH_MIN = 1180.0          # 最小发射(匹配G=1200, apex≈58)
LAUNCH_MAX = 1220.0          # 满蓄力发射(apex≈18)
CHARGE_RATE = 0.9            # 蓄力速度(每秒充满比例)
STEER_MIN = 8.0             # 全程基础引导(增强: 高速下落需要更强引导)
STEER_MAX = 20.0            # 场内下部引导(增强)
ENTRY_X = FIELD_L + 90       # 越顶横向弹簧的目标 x; 只负责让弹簧饱和,
                             # 实际左移量由每球的 cross_vx 上限(随蓄力)决定
ALIGN_H = 50.0               # 对齐窗口(最后排钉之下的无钉区)
ALIGN_K = 60.0               # 入槽横向弹簧(增强: 高速下落需要更强收尾)
ALIGN_DAMP = 0.86            # 入槽横向阻尼(临界附近防过冲)
ALIGN_VX_MAX = 800.0         # 横速硬上限(提高: 匹配高速下落)
CROSS_K = 80.0               # 越顶横向弹簧刚度(增强: 高速需要更强越顶引导)
CROSS_DAMP = 0.80            # 越顶横向阻尼(降低: 让左向速度更快累积)
CROSS_VX_MIN = 320.0         # 弱蓄力(u=0)的越顶横速上限; 320 是悬崖边:
                             # 降到 300 时 100% 撞天花板弧、首钉从 87 帧提前到 61 帧
CROSS_VX_MAX = 460.0         # 满蓄力(u=1)的越顶横速上限; 500 时卡死率约 1/5000
ASCENT_PULL = 0.45           # 爬升期场内引导衰减: 保住入场横速不被背离阻尼擦掉
RAIL_E = 0.55                # 天花板反弹系数
LAND_K = 16.0                # 落袋横向软吸附刚度
LAND_DAMP = 0.80             # 落袋横向阻尼
LAND_E = 0.20                # 落袋地板恢复系数(弹一两下停住)
STEER_DVX_MAX = 200.0        # 场内每帧引导增量上限(提高: 匹配高速)
STEER_VX_MAX = 800.0         # 场内横速上限(提高)

# --------------------- 哑火: 球发射了, 但升不过隔墙顶 -----------------------
# h = v^2/(2G); 要 apex y > LANE_WALL_TOP(160) 需 v < sqrt(2*1200*477) ≈ 1070
MISFIRE_POWER = 0.15         # 力度阈值: 低于此值球飞不出竖井
MISFIRE_V_MIN = 520.0        # power→0    的发射速度(apex y≈524, 刚离柱塞一点)
MISFIRE_V_MAX = 980.0        # power→阈值 的发射速度(apex y≈237, 差一点就够)
MISFIRE_E = 0.22             # 落回柱塞的弹跳恢复系数
MISFIRE_BOUNCE_VY = 200.0    # 落地速度低于此值直接停住
MISFIRE_MAX_FRAMES = 180     # 兜底(实测最长 121 帧)

START_BEADS = 1000
PRESETS = [1, 10, 50, 100]
DEFAULT_BET = 10
MAX_FALL_SEC = 8.0           # 兜底超时(高速下飞行更短, 降时避免半空超时)
LAND_HOLD = 0.60             # 落袋后球停留展示时长(秒), 短暂展示即快速回准备区
SLOT_BRAKE_VY = 0.65         # 槽区可见减速(竖直)
SLOT_BRAKE_VX = 0.5          # 槽区可见减速(水平)
REWARD_EV = 3.35             # _reward_value 的期望; RTP ≈ 非零格概率 x REWARD_EV

# ------------------- 碰撞事件位(物理层 -> GUI 音效层) ----------------------
EV_PEG = 1                   # 撞钉
EV_CEIL = 2                  # 撞天花板弧
EV_WALL = 4                  # 撞外墙/隔墙
EV_DIV = 8                   # 撞槽间隔板

# ----------------------------- 配色(清爽现代) ----------------------------
COL_BG = "#0e1524"
COL_PANEL = "#15223c"
COL_CANVAS = "#0b1220"
COL_WALL = "#2b436e"
COL_LANE = "#0e1830"
COL_PEG = "#c9d6f5"
COL_BALL = "#ffd451"
COL_TEXT = "#e8eefc"
COL_SUB = "#8fa0c4"
COL_DIV = "#33507f"
COL_BTN = "#3563d1"
COL_BTN_HOVER = "#4a78ea"
COL_BTN_OFF = "#26324f"
COL_FIRE = "#e0533b"
COL_GREEN = "#39d98a"
COL_GRAY = "#5a6a8c"
COL_METER = "#ffb347"
COL_BUMPER = "#4a6aa8"       # 底部挡板(比隔板亮, 醒目)
COL_LAMP_OFF = "#243250"     # 指示灯熄灭色
HILITE = "#ffffff"
FONT = "Segoe UI"

def build_pegs():
    """相对均匀的交错网格: 偶数行钉在槽中心, 奇数行钉在槽边界。"""
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
    """底部矮槽之间的竖直隔板。"""
    divs = []
    for k in range(1, NUM_SLOTS):
        x = FIELD_L + k * SLOT_W
        divs.append((x - DIV_W / 2.0, DIV_TOP, x + DIV_W / 2.0, FLOOR))
    return divs


def build_walls():
    """轴对齐矩形墙: 上/左/右/下外墙 + 通道隔墙(部分高度, 顶部留开口)。"""
    return [
        (0, 0, CW, WALL),                       # 顶
        (0, 0, WALL, CH),                       # 左
        (RIGHT_INNER, 0, CW, CH),               # 右
        (0, FLOOR, CW, CH),                     # 底
        (FIELD_R, LANE_WALL_TOP, LANE_L, FLOOR),  # 通道隔墙(y>=110 才有)
    ]


def build_deflectors():
    """顶部天花板: 4段微弧细分, 右端稍延伸盖住墙壁接头。"""
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


# =============================================================================
# 纯物理层 (不依赖 tkinter)
# =============================================================================
def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _reflect(b, nx, ny, e):
    """沿法线反弹, 返回撞击前的法向接近速率(>0 表示真的撞上了, 供音效定音量)。"""
    vn = b["vx"] * nx + b["vy"] * ny
    if vn < 0:
        b["vx"] -= (1 + e) * vn * nx
        b["vy"] -= (1 + e) * vn * ny
        return -vn
    return 0.0


def _mark(b, bit, sp):
    """记录碰撞事件位 + 该类碰撞本帧的最大撞击速率(GUI 读后清零)。"""
    b["events"] = b.get("events", 0) | bit
    amp = b.get("amp")
    if amp is None:
        amp = {}
        b["amp"] = amp
    if sp > amp.get(bit, 0.0):
        amp[bit] = sp


def _collide_pegs(b, pegs):
    rr = BALL_R + PEG_R
    for px, py in pegs:
        dx = b["x"] - px
        dy = b["y"] - py
        d2 = dx * dx + dy * dy
        if d2 < rr * rr:
            sp0 = math.hypot(b["vx"], b["vy"])   # 撞前速率
            d = math.sqrt(d2)
            if d > 1e-9:
                nx, ny = dx / d, dy / d
            else:
                a = random.uniform(0, math.tau)
                nx, ny = math.cos(a), math.sin(a)
            b["x"] = px + nx * rr
            b["y"] = py + ny * rr
            hit = _reflect(b, nx, ny, E)
            if hit > 0.0:
                _mark(b, EV_PEG, hit)
            # 角度微扰(模拟表面粗糙度): 只旋转方向, 不注入能量(修"弹跳越弹越高")
            angle = math.atan2(b["vy"], b["vx"])
            angle += random.uniform(-0.06, 0.06)  # ±3.4°
            sp = math.hypot(b["vx"], b["vy"])
            if sp > sp0 and sp > 1e-9:             # 安全兜底: 绝不快于撞前
                sp = sp0
            b["vx"] = sp * math.cos(angle)
            b["vy"] = sp * math.sin(angle)



def _collide_rect(b, rx1, ry1, rx2, ry2, e, ev=0):
    cx = max(rx1, min(b["x"], rx2))
    cy = max(ry1, min(b["y"], ry2))
    dx = b["x"] - cx
    dy = b["y"] - cy
    d2 = dx * dx + dy * dy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = dx / d, dy / d
        else:                                   # 球心在矩形内: 朝最近边推出
            left, right = b["x"] - rx1, rx2 - b["x"]
            top, bot = b["y"] - ry1, ry2 - b["y"]
            m = min(left, right, top, bot)
            if m == left:
                nx, ny = -1.0, 0.0
            elif m == right:
                nx, ny = 1.0, 0.0
            elif m == top:
                nx, ny = 0.0, -1.0
            else:
                nx, ny = 0.0, 1.0
        b["x"] = cx + nx * BALL_R
        b["y"] = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        if ev and hit > 0.0:
            _mark(b, ev, hit)


def _collide_segment(b, x1, y1, x2, y2, e, jitter=JITTER):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else ((b["x"] - x1) * dx + (b["y"] - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    cx, cy = x1 + t * dx, y1 + t * dy
    ox, oy = b["x"] - cx, b["y"] - cy
    d2 = ox * ox + oy * oy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = ox / d, oy / d
        else:
            nx, ny = 0.0, -1.0
        b["x"] = cx + nx * BALL_R
        b["y"] = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        if hit > 0.0:
            _mark(b, EV_CEIL, hit)
        if jitter:                          # 切向扰动(导轨传0=平滑滑行不散射)
            tx, ty = -ny, nx
            j = random.uniform(-jitter, jitter)
            b["vx"] += tx * j
            b["vy"] += ty * j


def physics_step(b, geo, dt):
    """推进一帧(拆 SUBSTEPS 子步)。落袋返回槽序号, 否则 None。"""
    sub = dt / SUBSTEPS
    for _ in range(SUBSTEPS):
        b["vy"] += G * sub
        sp = math.hypot(b["vx"], b["vy"])
        if sp > VMAX:
            f = VMAX / sp
            b["vx"] *= f
            b["vy"] *= f
        b["x"] += b["vx"] * sub
        b["y"] += b["vy"] * sub
        for w in geo["walls"]:
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _collide_segment(b, s[0], s[1], s[2], s[3], RAIL_E, JITTER)
        _collide_pegs(b, geo["pegs"])
        for d in geo["dividers"]:
            _collide_rect(b, d[0], d[1], d[2], d[3], E, EV_DIV)
        if b["y"] + BALL_R >= FLOOR - 0.5:
            i = int((b["x"] - FIELD_L) / SLOT_W)
            return max(0, min(NUM_SLOTS - 1, i))
    return None


def power_u(power):
    """有效蓄力区间 [MISFIRE_POWER, 1.0] 归一化到 [0, 1]。低于阈值的是哑火, 不走这里。"""
    return clamp((power - MISFIRE_POWER) / (1.0 - MISFIRE_POWER), 0.0, 1.0)


def launch_ball(power):
    """按蓄力比例 power 生成一颗向上发射的球(位于弹簧柱塞处)。

    竖直速度只在 1180~1220 的窄带内变化(3%), 是为了让越顶时刻只散 30ms —— 预烘的 1.5s
    连续飞行音(FLIGHT_ENV)靠这个前提才能对齐全过程。蓄力的观感差异全部由横向承担:
    cross_vx 是这颗球越顶时允许累积的最大左向速度, 蓄力越足冲得越左、横穿越多排钉。"""
    u = power_u(power)
    speed = LAUNCH_MIN + (LAUNCH_MAX - LAUNCH_MIN) * u
    return {"x": PLUNGER_X, "y": PLUNGER_Y, "vx": 0.0, "vy": -speed,
            "item": None, "born": time.time(), "events": 0, "amp": {},
            "cross_vx": CROSS_VX_MIN + (CROSS_VX_MAX - CROSS_VX_MIN) * u,
            "climb": True, "misfire": False}


def misfire_speed(power):
    """哑火发射速度: 蓄力越小升得越低(线性)。上限 980 保证 apex y≈237 > 160。"""
    u = clamp(power / MISFIRE_POWER, 0.0, 1.0)
    return MISFIRE_V_MIN + (MISFIRE_V_MAX - MISFIRE_V_MIN) * u


def launch_misfire(power):
    """力度不足: 球照样弹出去, 只是升不过隔墙顶, 会掉回柱塞。"""
    b = launch_ball(power)
    b["vy"] = -misfire_speed(power)
    b["misfire"] = True
    b["climb"] = False
    return b


def advance_misfire(b):
    """竖井内一维升降(实测全程零碰撞, x 恒=PLUNGER_X)。归位返回 True。
    不能走 physics_step: 它的落袋判定没有 x<FIELD_R 保护, 会把落回柱塞的球报成 8 号槽。"""
    b["vy"] += G * FIXED_DT
    b["y"] += b["vy"] * FIXED_DT
    if b["vy"] > 0 and b["y"] >= PLUNGER_Y:
        b["y"] = PLUNGER_Y
        if b["vy"] > MISFIRE_BOUNCE_VY:
            b["vy"] = -b["vy"] * MISFIRE_E
            return False
        b["vy"] = 0.0
        return True
    return False


def steer_ball(b, target_x):
    """引导(全程速度驱动: 只改 vx, 位置由 physics_step 积分, 单帧横移<=ALIGN_VX_MAX*FIXED_DT~5px, 杜绝瞬移)。
    上升不干预; 越顶弹簧绕过通道口; 入槽较强弹簧柔和收敛; 场内弱弹簧+限幅+背离阻尼(压撞钉反弹又不硬拽)。"""
    if b.get("misfire"):                             # 哑火球在竖井里自由升降, 一律不引导
        return
    if b["vy"] >= 0:
        b["climb"] = False        # 一次性闩锁: 只有发射后到冲顶那一段算爬升。
                                  # 不能用 vy<0 判断爬升 —— 每次撞钉向上反弹都满足,
                                  # 衰减会泄漏到整个下落段并造成卡死(histroy.md 第四轮第4条)
    if b["vy"] < 0 and b["y"] > LANE_WALL_TOP - BALL_R:
        return                    # 上升中且球底沿还没高过隔墙顶(160): 此时横推会让球以
                                  # 400px/s 的横速撞进 7px 厚的隔墙内部, 被"推出最近边"
                                  # 逻辑传送穿墙并白拿 9px 高度(实测 apex 41→21 贴天花板)
    if b["x"] >= FIELD_R:
        b["vx"] += (ENTRY_X - b["x"]) * CROSS_K * FIXED_DT
        b["vx"] *= CROSS_DAMP
        cv = b.get("cross_vx", CROSS_VX_MAX)         # 蓄力决定越顶能冲多左
        b["vx"] = clamp(b["vx"], -cv, cv)
        return
    if b["y"] > SLOT_TOP - ALIGN_H:
        b["vx"] += (target_x - b["x"]) * ALIGN_K * FIXED_DT
        b["vx"] *= ALIGN_DAMP
        b["vx"] = clamp(b["vx"], -ALIGN_VX_MAX, ALIGN_VX_MAX)
        return
    progress = (b["y"] - PEG_TOP) / max(1.0, SLOT_TOP - PEG_TOP)
    progress = max(0.0, min(1.0, progress))
    pull = STEER_MIN + (STEER_MAX - STEER_MIN) * progress
    climbing = b.get("climb", False)
    if climbing:
        pull *= ASCENT_PULL       # 爬升期弱引导, 否则入场横速 0.3s 内就被擦干净
    dvx = clamp((target_x - b["x"]) * pull * FIXED_DT, -STEER_DVX_MAX, STEER_DVX_MAX)
    b["vx"] += dvx
    if (not climbing) and ((b["vx"] > 0.0) != (target_x - b["x"] > 0.0)):
        b["vx"] *= 0.7            # 背离阻尼同样只在下落段生效
    b["vx"] = clamp(b["vx"], -STEER_VX_MAX, STEER_VX_MAX)


def advance_flight(b, geo, target_x):
    """推进一帧(GUI/selftest 共用): 弧形导轨越顶 + 分段速度引导 + 槽区减速 + 物理。
    引导层只改 vx, 位置一律由 physics_step 积分; 越顶入场改由弧形导轨(build_deflectors)物理导流, 不再注入种子横速。"""
    steer_ball(b, target_x)
    if b["y"] > SLOT_TOP and b["vy"] > 0:
        b["vy"] *= SLOT_BRAKE_VY
        b["vx"] *= SLOT_BRAKE_VX
    return physics_step(b, geo, FIXED_DT)


def choose_target(mult, rtp):
    """发射前预定落点槽, 使 RTP 精确 = rtp。
    命中奖励概率 w = min(1, rtp / 奖励格均值) -> E[目标倍率] = w x 均值 = rtp。"""
    reward = [i for i, m in enumerate(mult) if m > 0]
    zero = [i for i, m in enumerate(mult) if m == 0]
    if not reward:
        return random.randrange(len(mult))
    mean_r = sum(mult[i] for i in reward) / len(reward)
    w = min(1.0, rtp / mean_r)
    if zero and random.random() > w:
        return random.choice(zero)              # 判负 -> 落 0 格
    return random.choice(reward)                # 判胜 -> 落某奖励格


def _reward_value():
    """有奖励时的倍率取值(最小 x2, 整数, 越大越稀有)。E[value]≈3.35。"""
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


def roll_multipliers(rtp=0.90):
    """每格独立: 大概率为 0; 有奖励则最小 x2, 越大越稀有(偶有 10/20)。
    保底非零格数随档位提升(90%->3 / 100%->4 / 110%->5)让盘面有回本希望;
    并留至少 2 个零格(choose_target 需零格才能精确控 RTP)。真实 RTP 由 choose_target 决定, 与本函数(仅展示盘面)无关。"""
    q = rtp / REWARD_EV
    mult = [0] * NUM_SLOTS
    for i in range(NUM_SLOTS):
        if random.random() < q:
            mult[i] = _reward_value()
    min_reward = max(1, min(NUM_SLOTS - 2, int(round(rtp * 10)) - 6))  # 90->3/100->4/110->5
    max_reward = NUM_SLOTS - 2                        # 至少留 2 个零格(choose_target 需零格才能精确控 RTP)
    rewards = [i for i in range(NUM_SLOTS) if mult[i] > 0]
    zeros = [i for i in range(NUM_SLOTS) if mult[i] == 0]
    if len(rewards) < min_reward:
        for i in random.sample(zeros, min_reward - len(rewards)):
            mult[i] = _reward_value()
    elif len(rewards) > max_reward:
        for i in random.sample(rewards, len(rewards) - max_reward):
            mult[i] = 0
    return mult

# =============================================================================
# 音效层: 程序化合成 16bit PCM + winmm 多声道播放 (纯 stdlib, 无音频文件)
# =============================================================================
SR = 22050                   # 采样率
SFX_VOICES = 8               # 并发声道数(可同时叠加的音效数)
SFX_MASTER = 0.85            # 总音量 (0~1)
SFX_SEED = 20260727          # 合成用固定种子: 每次启动音色一致
SFX_RESULT_LEAD = 0.13       # 结果音(中奖/未中)前置静音: 让入袋声先落地
SOUND_ENABLED = True         # --nosound / demo 可关

# 音效专用随机流: 与游戏随机流完全隔离(否则合成会扰乱盘面/落点的随机序列)
_ARNG = random.Random(SFX_SEED)

# 撞击音下限(法向速率 px/s): 低于此值视为轻微擦碰, 不发声
SFX_MIN_SP = {EV_PEG: 45.0, EV_CEIL: 60.0, EV_WALL: 70.0, EV_DIV: 45.0}
# 撞击音满音量参考速率(法向速率 px/s)
SFX_REF_SP = {EV_PEG: 900.0, EV_CEIL: 1300.0, EV_WALL: 700.0, EV_DIV: 700.0}

NOTE = {"C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "G4": 392.00,
        "A4": 440.00, "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46,
        "G5": 783.99, "A5": 880.00, "C6": 1046.50, "D6": 1174.66, "E6": 1318.51,
        "G6": 1567.98, "C7": 2093.00}


# ----------------------------- 合成基元 -----------------------------------
def _buf(dur):
    return [0.0] * int(SR * dur)


def _noise(n, lp=0.5):
    """单极点低通白噪声: lp 越小越闷(1.0=白噪, 0.1=低频轰隆)。"""
    out = [0.0] * n
    z = 0.0
    comp = 1.0 / math.sqrt(max(0.02, lp))   # 补偿低通造成的能量损失
    for i in range(n):
        z += lp * (_ARNG.uniform(-1.0, 1.0) - z)
        out[i] = z * comp
    return out


def _add_partials(buf, t0, f0, parts, gain=1.0):
    """叠加指数衰减正弦分音。parts = [(频率倍数, 幅度, 衰减时间常数s)]。"""
    n = len(buf)
    i0 = int(t0 * SR)
    if i0 >= n:
        return
    for mul, amp, tau in parts:
        w = math.tau * f0 * mul / SR
        dec = math.exp(-1.0 / (tau * SR))
        a = amp * gain
        e = 1.0
        for i in range(i0, n):
            buf[i] += a * e * math.sin(w * (i - i0))
            e *= dec
            if e < 1e-4:                     # 衰减到 -80dB 以下, 提前收尾
                break


def _add_chirp(buf, t0, f1, f2, dur, amp, tau, curve=1.0):
    """扫频(弹簧/滑音): f1 -> f2, curve>1 前段变化快。"""
    n = len(buf)
    i0 = int(t0 * SR)
    nn = max(2, int(dur * SR))
    dec = math.exp(-1.0 / (tau * SR))
    ph = 0.0
    e = 1.0
    for j in range(nn):
        i = i0 + j
        if i >= n:
            break
        f = f1 + (f2 - f1) * ((j / (nn - 1.0)) ** curve)
        ph += math.tau * f / SR
        buf[i] += amp * e * math.sin(ph)
        e *= dec


def _add_noise(buf, t0, dur, amp, tau, lp=0.5):
    """噪声瞬态(撞击的"咔"/"沙")。"""
    n = len(buf)
    i0 = int(t0 * SR)
    ns = _noise(max(1, int(dur * SR)), lp)
    dec = math.exp(-1.0 / (max(1e-4, tau) * SR))
    e = 1.0
    for j, v in enumerate(ns):
        i = i0 + j
        if i >= n:
            break
        buf[i] += amp * e * v
        e *= dec


def _add_bell(buf, t0, f0, amp=1.0, tau=0.35, bright=1.0):
    """钟/马林巴音色: 谐波分音, 高次衰减更快 -> 温暖不刺耳。"""
    _add_partials(buf, t0, f0, [
        (1.00, 1.00 * amp, tau),
        (2.00, 0.42 * amp * bright, tau * 0.55),
        (3.00, 0.17 * amp * bright, tau * 0.34),
        (4.02, 0.08 * amp * bright, tau * 0.22),
    ])
    _add_noise(buf, t0, 0.003, 0.09 * amp, 0.0012, 0.85)   # 琴槌敲击感


def _reverb(buf, mix=0.20, rt=0.45):
    """极简梳状混响: 给铃声/中奖音一点空间感, 不再像干巴巴的蜂鸣。"""
    if mix <= 0.0:
        return
    n = len(buf)
    wet = [0.0] * n
    for dl in (0.0231, 0.0297, 0.0371, 0.0411):
        d = int(SR * dl)
        if d >= n:
            continue
        fb = 10.0 ** (-3.0 * d / (SR * rt))
        tmp = [0.0] * n
        for i in range(n):
            v = buf[i]
            if i >= d:
                v += fb * tmp[i - d]
            tmp[i] = v
            wet[i] += v * 0.25
    for i in range(n):
        buf[i] += mix * wet[i]


def _pack(buf, peak=0.6, fi=0.0006, fo=0.005):
    """归一化到 peak + 首尾淡入淡出(防爆音) -> 16bit 单声道 PCM 字节。
    淡入极短(0.6ms)以保住打击瞬态, 淡出较长(5ms)避免尾部断音"哒"。"""
    n = len(buf)
    pk = 0.0
    for v in buf:
        av = -v if v < 0.0 else v
        if av > pk:
            pk = av
    if pk < 1e-9:
        return b"\x00" * (2 * n)
    g = peak / pk
    ni = max(1, int(SR * fi))
    no = max(1, int(SR * fo))
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        v = buf[i] * g
        if i < ni:
            v *= i / ni
        r = n - 1 - i
        if r < no:
            v *= r / no
        if v > 1.0:
            v = 1.0
        elif v < -1.0:
            v = -1.0
        out[i] = int(v * 32767.0)
    return out.tobytes()


def pcm_to_wav(pcm):
    """裸 PCM -> 标准 WAV 容器字节(供 winsound.SND_MEMORY / --dumpwav)。"""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


# ----------------------------- 音效配方 -----------------------------------
def _sfx_tink(f0):
    """撞钉: 明亮金属"叮", 非谐分音 + 极短噪声瞬态。"""
    b = _buf(0.085)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.020),
                               (2.01, 0.45, 0.012),
                               (3.42, 0.20, 0.007)])
    _add_noise(b, 0.0, 0.004, 0.28, 0.0015, 0.60)
    return _pack(b, 0.55)


def _sfx_wall(f0):
    """撞墙: 闷"咚"(塑料/木质), 低频为主。"""
    b = _buf(0.13)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.045),
                               (1.87, 0.30, 0.018),
                               (3.10, 0.12, 0.008)])
    _add_noise(b, 0.0, 0.008, 0.35, 0.004, 0.18)
    return _pack(b, 0.45)


def _sfx_div(f0):
    """撞隔板: 中频"嗒"。"""
    b = _buf(0.105)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.026),
                               (2.31, 0.40, 0.013),
                               (3.91, 0.16, 0.007)])
    _add_noise(b, 0.0, 0.005, 0.30, 0.002, 0.35)
    return _pack(b, 0.50)


def _sfx_rail():
    """天花板金属弧: 钟形"锵", 带一点混响余韵。"""
    b = _buf(0.34)
    _add_partials(b, 0.0, 760.0, [(1.00, 1.00, 0.110),
                                  (2.76, 0.55, 0.070),
                                  (5.40, 0.30, 0.040),
                                  (8.93, 0.15, 0.022)])
    _add_noise(b, 0.0, 0.006, 0.30, 0.003, 0.70)
    _reverb(b, 0.18, 0.35)
    return _pack(b, 0.50)


def _sfx_launch():
    """发射: 柱塞"咔" + 弹簧下滑 boing(不含风声 — 风声交给 flight 连续音)。"""
    b = _buf(0.28)
    _add_noise(b, 0.000, 0.010, 0.55, 0.005, 0.85)          # 释放咔哒
    _add_chirp(b, 0.005, 640.0, 150.0, 0.11, 0.55, 0.085, 1.4)
    _add_partials(b, 0.005, 152.0, [(1.00, 0.80, 0.16),
                                    (2.40, 0.25, 0.06)])    # 弹簧余振
    return _pack(b, 0.58)


# 飞行音包络: 实测 400 次飞行的中位速度曲线(归一化), 每 0.1s 一点。
# 形状 = 出膛最快 -> 越顶(0.62s)减速变薄 -> 顶部滞空(1.1s 谷) -> 俯冲加速 -> 首次撞钉(1.45s)收尾。
FLIGHT_ENV = [1.00, 0.90, 0.80, 0.70, 0.59, 0.50, 0.47, 0.48,
              0.53, 0.58, 0.59, 0.22, 0.24, 0.33, 0.42, 0.44]
FLIGHT_DUR = 1.50


def _sfx_flight():
    """一条连续飞行音: 从发射贯穿到首次撞钉, 亮度/音量跟着球的实际速度呼吸。
    替代原来"发射内置风声 + 越顶 swoosh"两段断裂的设计(见 histroy.md)。"""
    n = int(SR * FLIGHT_DUR)
    b = [0.0] * n
    seg = (len(FLIGHT_ENV) - 1) / FLIGHT_DUR
    z = 0.0
    for i in range(n):
        t = i / SR
        u = t * seg                                  # 包络控制点插值
        k = int(u)
        if k >= len(FLIGHT_ENV) - 1:
            e = FLIGHT_ENV[-1]
        else:
            f = u - k
            e = FLIGHT_ENV[k] * (1.0 - f) + FLIGHT_ENV[k + 1] * f
        lp = 0.05 + 0.40 * e                         # 越快越亮
        z += lp * (_ARNG.uniform(-1.0, 1.0) - z)
        b[i] = z * (e ** 1.3) / math.sqrt(lp)
    _add_chirp(b, 0.00, 150.0, 96.0, 0.34, 0.10, 0.26, 1.0)  # 竖井内的低频管腔感
    return _pack(b, 0.38, fi=0.004, fo=0.110)


def _sfx_ratchet(lev):
    """蓄力棘轮: lev 0..5, 越高越亮越响(配合间隔变密 = 越蓄越急)。"""
    b = _buf(0.05)
    _add_noise(b, 0.0, 0.003, 0.45, 0.0012, 0.55)
    _add_partials(b, 0.0, 340.0 + lev * 95.0, [(1.00, 1.00, 0.010),
                                               (2.70, 0.30, 0.005)])
    return _pack(b, 0.26 + lev * 0.028)


def _sfx_charge_full():
    """满蓄力"顶到底": 弹簧压实的闷响 + 一声高音扣锁 → 听到就知道可以松手了。"""
    b = _buf(0.19)
    _add_partials(b, 0.000, 178.0, [(1.00, 1.00, 0.050), (2.05, 0.30, 0.018)])
    _add_noise(b, 0.000, 0.009, 0.40, 0.004, 0.30)
    _add_partials(b, 0.014, 1260.0, [(1.00, 0.45, 0.020), (2.02, 0.18, 0.010)])
    return _pack(b, 0.46)


def _sfx_pocket():
    """入袋: 闷响 + 一声轻脆(球坐进槽底)。"""
    b = _buf(0.20)
    _add_partials(b, 0.0, 138.0, [(1.00, 1.00, 0.055), (2.10, 0.28, 0.020)])
    _add_noise(b, 0.0, 0.012, 0.40, 0.006, 0.20)
    _add_partials(b, 0.004, 330.0, [(1.00, 0.35, 0.018)])
    return _pack(b, 0.55)


def _sfx_bounce():
    """落地弹跳(入袋后弹一两下)。"""
    b = _buf(0.12)
    _add_partials(b, 0.0, 172.0, [(1.00, 1.00, 0.032), (2.20, 0.22, 0.012)])
    _add_noise(b, 0.0, 0.006, 0.28, 0.003, 0.22)
    return _pack(b, 0.40)


def _sfx_riser():
    """入袋前紧张感上滑: 球穿出最后一排钉、进入无钉区(y>495)时响, 0.26s 后正好撞上入袋声。
    钉声刚停 -> 上滑 -> "咚", 制造"要落袋了"的期待。
    包络从零渐强(而非全程等响), 既是"riser"该有的形状, 也压住持续正弦的平均能量。"""
    n = int(SR * 0.26)
    b = [0.0] * n
    ph = 0.0
    for i in range(n):
        t = i / (n - 1.0)
        f = 300.0 + 480.0 * (t ** 1.4)               # 300 -> 780Hz
        ph += math.tau * f / SR
        env = t ** 2.0
        b[i] = (math.sin(ph) + 0.22 * math.sin(3.0 * ph)) * env
    z = 0.0                                          # 一层很轻的气声托底
    for i in range(n):
        t = i / (n - 1.0)
        z += 0.35 * (_ARNG.uniform(-1.0, 1.0) - z)
        b[i] += 0.16 * z * (t ** 2.0)
    return _pack(b, 0.30, fi=0.004, fo=0.020)


WIN_TIERS = [
    # (音符序列, 音间隔, 总长, 混响, 峰值, 低音支撑)
    (["C5", "E5", "G5"], 0.085, 0.72, 0.14, 0.50, None),
    (["C5", "E5", "G5", "C6"], 0.080, 0.85, 0.18, 0.55, None),
    (["C5", "E5", "G5", "C6", "E6"], 0.075, 1.00, 0.24, 0.60, None),
    (["C5", "E5", "G5", "C6", "E6", "G6"], 0.070, 1.20, 0.28, 0.65, 130.8),
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7"], 0.068, 1.40, 0.32, 0.70, 82.0),
]


def _sfx_win(tier):
    """中奖琶音 5 档: 0=x2 1=x3 2=x5 3=x10 4=x20, 音数/混响/低音支撑随档位递增,
    中奖时"听得出中了多大"。开头留 SFX_RESULT_LEAD 静音让入袋"咚"先落地。"""
    tier = max(0, min(len(WIN_TIERS) - 1, tier))
    seq, step, dur, rv, peak, bass = WIN_TIERS[tier]
    lead = SFX_RESULT_LEAD
    b = _buf(dur + lead)
    last = len(seq) - 1
    for k, nm in enumerate(seq):
        _add_bell(b, lead + k * step, NOTE[nm], 0.90 - 0.05 * k,
                  0.62 if k == last else 0.38)
    if bass is not None:                                    # 大奖档的低音支撑
        _add_partials(b, lead, bass, [(1.00, 0.90, 0.30), (2.00, 0.25, 0.13)])
    if tier >= 4:
        for nm in ("C6", "E6", "G6"):                       # 收尾大三和弦
            _add_bell(b, lead + 0.50, NOTE[nm], 0.45, 0.90)
        for k in range(6):                                  # 尾部碎星
            _add_partials(b, lead + 0.62 + k * 0.075, 1900.0 + k * 185.0,
                          [(1.00, 0.22, 0.030), (2.40, 0.10, 0.015)])
    _reverb(b, rv, 0.70 if tier >= 4 else 0.45)
    return _pack(b, peak)


def _sfx_lose():
    """未中: 柔和下行两音(F4 -> C4), 轻描淡写地过去 — 别反复强调失败。"""
    lead = SFX_RESULT_LEAD
    b = _buf(0.34 + lead)
    _add_bell(b, lead + 0.00, NOTE["F4"], 0.70, 0.18, 0.5)
    _add_bell(b, lead + 0.11, NOTE["C4"], 0.70, 0.24, 0.5)
    _reverb(b, 0.10, 0.26)
    return _pack(b, 0.22)


def _sfx_click():
    """UI 按键: 极短软咔。"""
    b = _buf(0.035)
    _add_noise(b, 0.0, 0.0025, 0.50, 0.0012, 0.75)
    _add_partials(b, 0.0, 940.0, [(1.00, 0.50, 0.006), (2.60, 0.20, 0.003)])
    return _pack(b, 0.24)


def _sfx_error():
    """珠子不足: 低频颤音"嗡"。"""
    b = _buf(0.28)
    w = math.tau * 155.0 / SR
    for i in range(len(b)):
        trem = 0.55 + 0.45 * math.sin(math.tau * 19.0 * i / SR)
        env = min(1.0, i / (SR * 0.006)) * math.exp(-i / (SR * 0.16))
        b[i] = (math.sin(w * i) + 0.34 * math.sin(3 * w * i) +
                0.16 * math.sin(5 * w * i)) * trem * env
    return _pack(b, 0.34)


def _sfx_coin():
    """计分滚动的细碎"叮"(数字翻滚时连播)。"""
    b = _buf(0.035)
    _add_partials(b, 0.0, 2280.0, [(1.00, 1.00, 0.007), (2.02, 0.40, 0.004)])
    _add_noise(b, 0.0, 0.002, 0.18, 0.001, 0.90)
    return _pack(b, 0.20)


def _sfx_ready():
    """新球滚进柱塞就位。"""
    b = _buf(0.16)
    _add_partials(b, 0.000, 255.0, [(1.00, 1.00, 0.022), (2.30, 0.30, 0.010)])
    _add_noise(b, 0.000, 0.050, 0.14, 0.030, 0.25)
    _add_partials(b, 0.075, 300.0, [(1.00, 0.50, 0.016)])
    return _pack(b, 0.26)


def _sfx_cash():
    """重置珠子: 一串硬币落盘。"""
    b = _buf(0.55)
    for k in range(7):
        t = 0.02 + k * 0.06 + _ARNG.uniform(-0.012, 0.012)
        _add_partials(b, t, 1900.0 + _ARNG.uniform(-260.0, 520.0),
                      [(1.00, 0.80, 0.010), (2.03, 0.30, 0.005)])
        _add_noise(b, t, 0.002, 0.12, 0.001, 0.90)
    _reverb(b, 0.14, 0.25)
    return _pack(b, 0.45)


def bake_bank():
    """合成全部音效 -> {名字: PCM字节}。约 7s 素材, 耗时 ~200ms(后台线程跑)。"""
    _ARNG.seed(SFX_SEED)                    # 每次烘焙音色完全一致
    bank = {}
    for i, f0 in enumerate((1040.0, 1180.0, 1330.0, 1500.0, 1680.0, 1880.0)):
        bank["peg%d" % i] = _sfx_tink(f0)
    for i, f0 in enumerate((185.0, 225.0)):
        bank["wall%d" % i] = _sfx_wall(f0)
    for i, f0 in enumerate((430.0, 505.0)):
        bank["div%d" % i] = _sfx_div(f0)
    for lev in range(6):
        bank["ratchet%d" % lev] = _sfx_ratchet(lev)
    bank["charge_full"] = _sfx_charge_full()
    bank["rail"] = _sfx_rail()
    bank["launch"] = _sfx_launch()
    bank["flight"] = _sfx_flight()
    bank["riser"] = _sfx_riser()
    bank["pocket"] = _sfx_pocket()
    bank["bounce"] = _sfx_bounce()
    for tier in range(len(WIN_TIERS)):
        bank["win%d" % tier] = _sfx_win(tier)
    bank["lose"] = _sfx_lose()
    bank["click"] = _sfx_click()
    bank["error"] = _sfx_error()
    bank["coin"] = _sfx_coin()
    bank["ready"] = _sfx_ready()
    bank["cash"] = _sfx_cash()
    return bank

try:                                        # winmm: 唯一能做多声道叠加的 stdlib 路径
    import ctypes

    class _WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
                    ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                    ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
                    ("cbSize", ctypes.c_uint16)]

    class _WAVEHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
                    ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
                    ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p)]

    _winmm = ctypes.WinDLL("winmm")
    _winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                   ctypes.POINTER(_WAVEFORMATEX), ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_uint32]
    for _fn in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
        getattr(_winmm, _fn).argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(_WAVEHDR), ctypes.c_uint]
    _winmm.waveOutReset.argtypes = [ctypes.c_void_p]
    _winmm.waveOutClose.argtypes = [ctypes.c_void_p]
    if _winmm.waveOutGetNumDevs() <= 0:     # 无声卡: 别白费力气开设备
        _winmm = None
except Exception:
    _winmm = None

_WAVE_MAPPER = 0xFFFFFFFF
_WHDR_DONE = 1


class _WaveOut:
    """winmm 多声道输出: 撞钉/中奖/滚分可以真正同时响, 且写入不阻塞 GUI。

    两条慢路径必须避开(实测): waveOutOpen 8.8ms/次 -> 后台 warm() 预开;
    waveOutReset 抢占 10ms/次 -> 声道全忙时直接丢弃(丢一声听不出, 卡一帧看得出)。"""

    def __init__(self, voices=SFX_VOICES):
        if _winmm is None:
            raise OSError("winmm unavailable")
        self._fmt = _WAVEFORMATEX(1, 1, SR, SR * 2, 2, 16, 0)
        self._hsz = ctypes.sizeof(_WAVEHDR)
        self._n = voices
        self._h = [None] * voices
        self._hdr = [None] * voices
        self._buf = [None] * voices
        self._lock = threading.Lock()
        self.drops = 0
        h = self._open()
        if h is None:
            raise OSError("waveOutOpen failed")
        self._h[0] = h

    def _open(self):
        h = ctypes.c_void_p()
        if _winmm.waveOutOpen(ctypes.byref(h), _WAVE_MAPPER,
                              ctypes.byref(self._fmt), None, None, 0) != 0:
            return None
        return h

    def warm(self):
        """预开所有声道(后台线程调用): 避免游戏中途 8.8ms 的开设备卡顿。"""
        with self._lock:
            for i in range(self._n):
                if self._h[i] is None:
                    h = self._open()
                    if h is None:
                        break
                    self._h[i] = h

    def _alloc(self):
        for i in range(self._n):
            if self._h[i] is not None and (self._hdr[i] is None or
                                           (self._hdr[i].dwFlags & _WHDR_DONE)):
                return i
        return None                         # 全忙: 丢弃(不抢占, 抢占要 10ms)

    def play_pcm(self, pcm):
        with self._lock:
            i = self._alloc()
            if i is None:
                self.drops += 1
                return
            h = self._h[i]
            if self._hdr[i] is not None:
                _winmm.waveOutUnprepareHeader(h, ctypes.byref(self._hdr[i]), self._hsz)
                self._hdr[i] = None
            buf = ctypes.create_string_buffer(pcm, len(pcm))
            hdr = _WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(pcm)
            if _winmm.waveOutPrepareHeader(h, ctypes.byref(hdr), self._hsz) != 0:
                return
            if _winmm.waveOutWrite(h, ctypes.byref(hdr), self._hsz) != 0:
                _winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), self._hsz)
                return
            self._hdr[i] = hdr              # 保活: 缓冲区必须活到播完
            self._buf[i] = buf

    def close(self):
        with self._lock:
            for i in range(self._n):
                h = self._h[i]
                if h is None:
                    continue
                try:
                    _winmm.waveOutReset(h)
                    if self._hdr[i] is not None:
                        _winmm.waveOutUnprepareHeader(h, ctypes.byref(self._hdr[i]), self._hsz)
                    _winmm.waveOutClose(h)
                except Exception:
                    pass
                self._h[i] = self._hdr[i] = self._buf[i] = None

    @property
    def name(self):
        return "winmm(%d声道)" % self._n

def _scale_pcm(pcm, g):
    a = array.array("h")
    a.frombytes(pcm)
    for i in range(len(a)):
        a[i] = int(a[i] * g)
    return a.tobytes()

# -*- coding: utf-8 -*-
# ======================= 输出后端(三级降级) =======================
# Android:  pyjnius 调 SoundPool(多路并发, 低延迟, 游戏音效专用 API)
# Windows:  winmm _WaveOut(从 plinko.py 原样抽取, 8 声道)
# 其它桌面: Kivy SoundLoader(SDL2, 能响就行)
def _sfx_cache_dir():
    """音效 WAV 落盘目录(SoundPool/SoundLoader 都要文件路径; winmm 直接播 PCM 不用)。"""
    if platform == "android":
        try:
            d = App.get_running_app().user_data_dir
        except Exception:
            d = tempfile.gettempdir()
    else:
        d = os.path.join(tempfile.gettempdir(), "plinko_sfx")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


class _SoundPoolOut:
    """Android SoundPool: 34 个短音效全部解压进内存, 并发由硬件 mixer 处理。
    mode="named": Sfx 按名字播放, 音量用 play() 自带的 leftVol/rightVol(免 PCM 缩放)。"""
    mode = "named"
    name = "SoundPool"

    def __init__(self, voices=SFX_VOICES):
        from jnius import autoclass, PythonJavaClass, java_method
        SoundPool = autoclass("android.media.SoundPool")
        AudioAttributes = autoclass("android.media.AudioAttributes")
        attrs = (AudioAttributes.Builder()
                 .setUsage(AudioAttributes.USAGE_MEDIA)
                 .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                 .build())
        self._sp = (SoundPool.Builder()
                    .setMaxStreams(voices)
                    .setAudioAttributes(attrs)
                    .build())
        self._ids = {}
        self._loaded = set()
        loaded = self._loaded

        class _Listener(PythonJavaClass):
            __javainterfaces__ = ["android/media/SoundPool$OnLoadCompleteListener"]
            __javacontext__ = "app"

            @java_method("(Landroid/media/SoundPool;II)V")
            def onLoadComplete(self, soundpool, sample_id, status):
                if status == 0:
                    loaded.add(sample_id)

        # 必须存实例属性: PythonJavaClass 被 GC 后 Java 回调会崩
        self._listener = _Listener()
        self._sp.setOnLoadCompleteListener(self._listener)
        self._dir = _sfx_cache_dir()

    def prime(self, name, pcm):
        path = os.path.join(self._dir, name + ".wav")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(pcm_to_wav(pcm))
        self._ids[name] = self._sp.load(path, 1)

    def play_named(self, name, gain01):
        sid = self._ids.get(name)
        if sid is None or sid not in self._loaded:
            return False                    # 未加载完: 静默丢弃(同桌面版"未烘焙完跳过")
        self._sp.play(sid, gain01, gain01, 1, 0, 1.0)
        return True

    def close(self):
        try:
            self._sp.release()
        except Exception:
            pass


class _KivySoundOut:
    """桌面后备: Kivy SoundLoader(SDL2)。能同时响, 但延迟/叠加不如 winmm/SoundPool。"""
    mode = "named"
    name = "Kivy-SoundLoader"

    def __init__(self):
        from kivy.core.audio import SoundLoader
        self._loader = SoundLoader
        self._sounds = {}
        self._dir = _sfx_cache_dir()

    def prime(self, name, pcm):
        path = os.path.join(self._dir, name + ".wav")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(pcm_to_wav(pcm))
        snd = self._loader.load(path)
        if snd is not None:
            self._sounds[name] = snd

    def play_named(self, name, gain01):
        snd = self._sounds.get(name)
        if snd is None:
            return False
        try:
            if snd.state == "play":
                snd.stop()
            snd.volume = gain01
            snd.play()
            return True
        except Exception:
            return False

    def close(self):
        for snd in self._sounds.values():
            try:
                snd.stop()
            except Exception:
                pass
        self._sounds.clear()


def open_output():
    """按优先级选后端: Android SoundPool > winmm > Kivy SoundLoader > 静音。"""
    if platform == "android":
        try:
            return _SoundPoolOut()
        except Exception:
            pass
    try:
        return _WaveOut()
    except Exception:
        pass
    try:
        return _KivySoundOut()
    except Exception:
        pass
    return None


# ======================= 音效总线 =======================
class Sfx:
    """合成一次(后台线程), 之后每次发声只做取样+送声卡。
    pcm 后端(winmm): gain 量化 10 档缓存缩放后的 PCM。
    named 后端(SoundPool/SoundLoader): 直接按名字 + 浮点音量播。"""

    def __init__(self, enabled=True, sync=False):
        self.enabled = bool(enabled)
        self.out = None
        self.bank = {}
        self.bake_ms = 0.0
        self._scaled = {}
        self._last = {}
        self._thread = None
        if not self.enabled:
            return
        self.out = open_output()
        if self.out is None:
            self.enabled = False
            return
        if sync:
            self._bake()
        else:
            self._thread = threading.Thread(target=self._bake, daemon=True)
            self._thread.start()

    def _bake(self):
        t0 = time.perf_counter()
        bank = bake_bank()                  # 整体赋值(引用切换), 读侧只会看到空或全量
        self.bank = bank
        self.bake_ms = (time.perf_counter() - t0) * 1000.0
        if getattr(self.out, "mode", "pcm") == "pcm":
            for name in ("win0", "win1", "win2", "win3", "win4", "lose",
                         "launch", "flight", "riser"):
                for g in (1.0, 0.9, 0.85):  # 预热长音效的音量缓存
                    self.play_prepare(name, g)
            warm = getattr(self.out, "warm", None)
            if warm is not None:
                warm()                      # 预开所有声道(每个 8.8ms, 放后台)
        else:
            for nm, pcm in bank.items():    # 写 WAV + 加载进 SoundPool/SoundLoader
                try:
                    self.out.prime(nm, pcm)
                except Exception:
                    pass

    def play_prepare(self, name, gain):
        pcm = self.bank.get(name)
        if pcm is None:
            return
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        key = (name, lvl)
        if lvl > 0 and key not in self._scaled:
            self._scaled[key] = pcm if lvl >= 10 else _scale_pcm(pcm, lvl / 10.0)

    def wait_ready(self, timeout=10.0):
        if self._thread is not None:
            self._thread.join(timeout)
        return bool(self.bank)

    @property
    def backend(self):
        return self.out.name if self.out is not None else "静音"

    def play(self, name, gain=1.0, throttle=0.0):
        if not self.enabled:
            return False
        pcm = self.bank.get(name)
        if pcm is None:                      # 还没烘焙好(启动后 ~200ms 内)
            return False
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        if lvl <= 0:
            return False
        if throttle > 0.0:
            now = time.time()
            if now - self._last.get(name, 0.0) < throttle:
                return False
            self._last[name] = now
        if getattr(self.out, "mode", "pcm") == "pcm":
            key = (name, lvl)
            data = self._scaled.get(key)
            if data is None:
                data = pcm if lvl >= 10 else _scale_pcm(pcm, lvl / 10.0)
                self._scaled[key] = data
            self.out.play_pcm(data)
            return True
        return self.out.play_named(name, lvl / 10.0)

    def impact(self, bit, sp):
        """碰撞音: 撞得越猛越响越亮; 低于阈值不发声。"""
        if not self.enabled or sp < SFX_MIN_SP.get(bit, 0.0):
            return False
        t = clamp(sp / SFX_REF_SP.get(bit, 900.0), 0.0, 1.0)
        if bit == EV_PEG:
            idx = int(clamp(int(t * 5.99) + _ARNG.randint(-1, 1), 0, 5))
            return self.play("peg%d" % idx, 0.30 + 0.70 * t, 0.038)
        if bit == EV_CEIL:
            return self.play("rail", 0.45 + 0.55 * t, 0.22)
        if bit == EV_WALL:
            return self.play("wall%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
        if bit == EV_DIV:
            return self.play("div%d" % (1 if t > 0.5 else 0), 0.35 + 0.65 * t, 0.055)
        return False

    def close(self):
        if self.out is not None:
            self.out.close()
            self.out = None
        self.enabled = False

def selftest(n=40000):
    """验证: (1) 各档 RTP 精确=档位; (2) 引导飞行落点=预定槽、不卡死;
    (3) 碰撞事件覆盖率(音效触发源); (4) 音效库体检。

    n 必须够大: 单发赔付方差很大(取值 0/2/3/5/10/20, σ≈3), n=4000 时均值标准误 ≈0.047,
    与 ±0.05 的门禁同量级 -> 假失败率实测 30%。n=40000 使标准误降到 ≈0.015(3σ 门禁),
    代价只有 0.4 秒。"""
    geo = build_geo()
    ok = True

    # (1) 落点预定的 RTP 精确性(不依赖物理, 快)
    print("== 返还率精确性(预定落点) ==")
    for rtp in (0.90, 1.00, 1.10):
        tot = 0.0
        for _ in range(n):
            board = roll_multipliers(rtp)
            t = choose_target(board, rtp)
            tot += board[t]
        realized = tot / n
        good = abs(realized - rtp) < 0.05
        ok = ok and good
        print("  档位 %.2f -> 实测 RTP %.3f  %s" % (rtp, realized, "OK" if good else "偏差!"))

    # (2) 引导飞行: 升过通道顶(apex) -> 越入场区 -> 落到预定槽, 且不卡死
    print("== 引导飞行(升到顶->越顶入场->落预定 & 不卡死) ==")
    m = 1500
    hit = stuck = no_top = no_enter = 0
    ev_flights = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0}
    ev_audible = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0}
    for _ in range(m):
        target = random.randrange(NUM_SLOTS)
        tx = FIELD_L + (target + 0.5) * SLOT_W
        b = launch_ball(random.uniform(MISFIRE_POWER, 1.0))   # 低于阈值的是哑火, 由 (2b) 覆盖
        min_y = b["y"]
        entered = False
        landed = None
        seen = 0
        loud = 0
        for _ in range(4000):
            landed = advance_flight(b, geo, tx)
            ev = b["events"]
            if ev:                             # 模拟 GUI: 每帧读事件位后清零
                seen |= ev
                for bit, sp in b["amp"].items():
                    if sp >= SFX_MIN_SP[bit]:
                        loud |= bit
                b["events"] = 0
                b["amp"].clear()
            min_y = min(min_y, b["y"])
            if b["x"] < FIELD_R:
                entered = True
            if landed is not None:
                actual = max(0, min(NUM_SLOTS - 1, int((b["x"] - FIELD_L) / SLOT_W)))
                if actual == target:
                    hit += 1
                break
        else:
            stuck += 1
            continue
        for bit in ev_flights:
            if seen & bit:
                ev_flights[bit] += 1
            if loud & bit:
                ev_audible[bit] += 1
        if min_y > LANE_WALL_TOP:          # 没升过通道隔墙口 = apex 太低(会掉回通道)
            no_top += 1
        if not entered:                    # 没越入场区
            no_enter += 1
    hit_rate = 100.0 * hit / m
    ok = ok and stuck == 0 and no_top == 0 and no_enter == 0 and hit_rate > 90.0
    print("  升过通道顶失败: %d/%d   越顶入场失败: %d/%d   卡死: %d" %
          (no_top, m, no_enter, m, stuck))
    print("  引导命中(物理x==目标): %.1f%%  (实际结算恒用预定槽, 此项仅衡量动画自然度)"
          % hit_rate)

    # (2b) 哑火: 力度 < MISFIRE_POWER 时球照样弹出去, 但必须升不过隔墙顶并原路掉回柱塞
    print("== 哑火(发射了但升不过隔墙顶) ==")
    mf = 500
    bad_apex = bad_x = bad_home = 0
    apex_hi_y, apex_lo_y = 1e9, -1e9     # apex_hi_y = 升得最高(y 最小)的那一发
    frames_max = 0
    for k in range(mf):
        power = MISFIRE_POWER * k / (mf - 1.0)
        b = launch_misfire(power)
        apex = b["y"]
        home = False
        used = 0
        for used in range(1, MISFIRE_MAX_FRAMES + 1):
            done = advance_misfire(b)
            apex = min(apex, b["y"])
            if abs(b["x"] - PLUNGER_X) > 1e-9:
                break
            if done:
                home = True
                break
        frames_max = max(frames_max, used)
        apex_hi_y = min(apex_hi_y, apex)
        apex_lo_y = max(apex_lo_y, apex)
        if apex <= LANE_WALL_TOP + 40:       # 离隔墙顶(160)留 40px 安全余量
            bad_apex += 1
        if abs(b["x"] - PLUNGER_X) > 1e-9:   # 竖井内不该有任何横向位移
            bad_x += 1
        if not (home and b["y"] == PLUNGER_Y and b["vy"] == 0.0):
            bad_home += 1
    probe = {"x": PLUNGER_X, "y": 400.0, "vx": 0.0, "vy": 300.0, "misfire": True}
    steer_ball(probe, FIELD_L + 100.0)       # 回归守卫: 引导层绝不能碰哑火球
    steer_ok = probe["vx"] == 0.0
    mf_ok = (bad_apex == 0 and bad_x == 0 and bad_home == 0 and steer_ok
             and frames_max <= MISFIRE_MAX_FRAMES)
    ok = ok and mf_ok
    print("  apex y 区间 %.0f~%.0f (隔墙顶 %d, 越过即失败)   最长归位 %d/%d 帧"
          % (apex_hi_y, apex_lo_y, LANE_WALL_TOP, frames_max, MISFIRE_MAX_FRAMES))
    print("  越顶泄漏: %d/%d   横向漂移: %d/%d   未归位: %d/%d   引导未碰哑火球: %s"
          % (bad_apex, mf, bad_x, mf, bad_home, mf, "OK" if steer_ok else "失败!"))

    # (2c) 蓄力观感区分度: 蓄力必须可见地改变冲顶位置/穿钉路径, 同时竖直时序一帧都不能动
    #      (首钉时刻是 FLIGHT_ENV 那条 1.5s 预烘飞行音的对齐锚点, 漂了音画就脱节)
    print("== 蓄力观感区分度(竖直时序必须不变) ==")
    apexx_med = {}
    fp_bad = []
    for power in (MISFIRE_POWER, 0.5, 1.0):
        axs, npegs, fps = [], [], []
        for k in range(100):
            tx = FIELD_L + (k % NUM_SLOTS + 0.5) * SLOT_W
            b = launch_ball(power)
            best_y, best_x = b["y"], b["x"]
            npeg, fp = 0, -1
            for f in range(4000):
                landed = advance_flight(b, geo, tx)
                if b["y"] < best_y:
                    best_y, best_x = b["y"], b["x"]
                if b["events"] & EV_PEG:
                    npeg += 1
                    if fp < 0:
                        fp = f
                b["events"] = 0
                b["amp"].clear()
                if landed is not None:
                    break
            axs.append(best_x)
            npegs.append(npeg)
            if fp >= 0:
                fps.append(fp)
        axs.sort(); npegs.sort(); fps.sort()
        apexx_med[power] = axs[len(axs) // 2]
        fp_med = fps[len(fps) // 2] if fps else -1
        if not (80 <= fp_med <= 95):
            fp_bad.append((power, fp_med))
        print("  力度 %3.0f%% (u=%.2f): 冲顶 x 中位 %3.0f   撞钉 %d 次   首钉 %d 帧"
              % (power * 100, power_u(power), apexx_med[power],
                 npegs[len(npegs) // 2], fp_med))
    spread = apexx_med[MISFIRE_POWER] - apexx_med[1.0]
    spread_ok = spread >= 50.0
    ok = ok and spread_ok and not fp_bad
    print("  冲顶 x 跨度(弱→满): %.0f px  %s (>=50 玩家才看得出来)"
          % (spread, "OK" if spread_ok else "区分度不足!"))
    print("  首钉时刻: %s (须恒在 80~95 帧, 否则飞行音与画面脱节)"
          % ("OK" if not fp_bad else "漂了! %s" % fp_bad))

    # (3) 碰撞事件覆盖率: 该响的地方有没有事件位(历史 bug: 撞钉位从未置位 -> 全程静音)
    print("== 碰撞事件覆盖率(音效触发源) ==")
    names = {EV_PEG: "撞钉", EV_CEIL: "天花板", EV_WALL: "撞墙", EV_DIV: "撞隔板"}
    for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
        print("  %-8s 有事件 %5.1f%%   过音量阈值 %5.1f%%" %
              (names[bit], 100.0 * ev_flights[bit] / m, 100.0 * ev_audible[bit] / m))
    print("  越顶入场 100.0% (=越顶入场失败 0, swoosh 必响)  注: 引导使球从天花板弧左端外侧擦过,")
    print("           所以天花板音本就罕见, 上升段的音效锚点是 swoosh 而非撞弧")
    peg_rate = 100.0 * ev_audible[EV_PEG] / m
    ev_ok = peg_rate > 90.0                 # 撞钉是下落段的主音效, 必须几乎每发都有
    ok = ok and ev_ok
    if not ev_ok:
        print("  异常: 撞钉音效触发率 %.1f%% < 90%%, 玩家会觉得没声音" % peg_rate)
    ceil_rate = 100.0 * ev_flights[EV_CEIL] / m
    ceil_ok = ceil_rate < 10.0              # 守住 CROSS_VX_MIN 的悬崖: 撞弧会把首钉时刻
    ok = ok and ceil_ok                     # 从 87 帧提前到 61 帧, 预烘的飞行音当场脱节
    if not ceil_ok:
        print("  异常: 天花板弧撞击率 %.1f%% >= 10%%, 越顶引导偏弱(CROSS_VX_MIN 掉到悬崖下?)"
              % ceil_rate)

    # (4) 音效库体检(不需要声卡)
    print("== 音效库体检 ==")
    cnt, bad = sfx_check(verbose=False)
    print("  音效数: %d   异常: %s" % (cnt, bad if bad else "无(削波/直流/爆音/静音 全部通过)"))
    ok = ok and not bad and cnt >= 20

    print("结果:", "OK" if ok else "存在异常, 需修复")
    return ok

def sfx_check(verbose=True):
    """无声卡也能跑的音效库体检: 削波/直流/首尾爆音/静音。"""
    bank = bake_bank()
    bad = []
    if verbose:
        print("  %-10s %7s %6s %6s %7s" % ("name", "ms", "peak", "rms", "dc"))
    for name in sorted(bank):
        a = array.array("h")
        a.frombytes(bank[name])
        n = len(a)
        if n == 0:
            bad.append((name, "空"))
            continue
        pk = max(max(a), -min(a)) / 32767.0
        rms = math.sqrt(sum(v * v for v in a) / n) / 32767.0
        dc = sum(a) / n / 32767.0
        if verbose:
            print("  %-10s %7.0f %6.3f %6.3f %7.4f" % (name, n / SR * 1000, pk, rms, dc))
        if pk > 0.999:
            bad.append((name, "削波"))
        if pk < 0.10 or rms < 0.005:
            bad.append((name, "太轻/静音"))
        if abs(dc) > 0.02:
            bad.append((name, "直流偏移"))
        if abs(a[0]) > 500 or abs(a[-1]) > 500:
            bad.append((name, "首尾爆音"))
    return len(bank), bad

# -*- coding: utf-8 -*-
# ======================= Kivy UI 层 =======================
def slot_color(m):
    if m <= 0:
        return "#2a3550"
    if m <= 2:
        return "#2fae74"
    if m <= 5:
        return "#f0a63a"
    if m <= 10:
        return "#f0563a"
    return "#ffd451"


class GameArea(Widget):
    """520x660 逻辑场景(坐标系沿用 tkinter 版: y 向下), 绘制时等比缩放居中。
    静态元素(墙/钉/槽/弧)重绘只在尺寸变化或换盘面时; 球/力度条/柱塞每帧只改 pos。"""

    def __init__(self, game, **kw):
        super().__init__(**kw)
        self.game = game
        self._s = 1.0
        self._ox = 0.0
        self._oyt = 0.0
        self._lamp_cols = []
        self._ball_e = None
        self._meter_fill = None
        self._meter_col = None
        self._plunger = None
        self.bind(size=self._redraw, pos=self._redraw)

    # ---- 坐标换算: 逻辑(x, y向下) -> 控件像素(Kivy y向上); 返回 kwargs 便于 ** 展开 ----
    def _rect(self, x1, y1, x2, y2):
        w = (x2 - x1) * self._s
        h = (y2 - y1) * self._s
        return {"pos": (self._ox + x1 * self._s, self._oyt - y2 * self._s),
                "size": (w, h)}

    def _circle(self, cx, cy, r):
        return {"pos": (self._ox + (cx - r) * self._s,
                        self._oyt - (cy + r) * self._s),
                "size": (2 * r * self._s, 2 * r * self._s)}

    def _px(self, x):
        return self._ox + x * self._s

    def _py(self, y):
        return self._oyt - y * self._s

    def _redraw(self, *_):
        if self.width < 20 or self.height < 20:
            return
        s = min(self.width / CW, self.height / CH)
        self._s = s
        self._ox = self.x + (self.width - CW * s) / 2.0
        self._oyt = self.y + (self.height + CH * s) / 2.0
        g = self.game
        self.canvas.clear()
        with self.canvas:
            Color(*hex_rgb(COL_CANVAS))
            Rectangle(pos=self.pos, size=self.size)
            Color(*hex_rgb(COL_LANE))
            Rectangle(**self._rect(LANE_L, 0, RIGHT_INNER, FLOOR))
            Color(*hex_rgb(COL_WALL))
            for w in g.geo["walls"]:
                Rectangle(**self._rect(*w))
            # 天花板弧(4段微弧细分折线)
            Color(*hex_rgb(COL_WALL))
            pts = []
            for (x1, y1, x2, y2) in g.geo["deflectors"]:
                pts.extend([self._px(x1), self._py(y1)])
            x1, y1, x2, y2 = g.geo["deflectors"][-1]
            pts.extend([self._px(x2), self._py(y2)])
            Line(points=pts, width=max(1.0, 7 * s), cap="round", joint="round")
            # 钉阵
            Color(*hex_rgb(COL_PEG))
            for px, py in g.geo["pegs"]:
                Ellipse(**self._circle(px, py, PEG_R))
            # 槽隔板
            Color(*hex_rgb(COL_BUMPER))
            for d in g.geo["dividers"]:
                Rectangle(**self._rect(*d))
            # 倍率槽(颜色随盘面)
            for i in range(NUM_SLOTS):
                Color(*hex_rgb(slot_color(g.multipliers[i])))
                Rectangle(**self._rect(FIELD_L + i * SLOT_W + 2, SLOT_TOP + 3,
                                      FIELD_L + (i + 1) * SLOT_W - 2, FLOOR - 3))
            # 槽倍率文字(CoreLabel 烘成纹理)
            fs = max(9, int(12 * s))
            for i in range(NUM_SLOTS):
                m = g.multipliers[i]
                if m <= 0:
                    continue
                cl = CoreLabel(text="x%d" % m, font_size=fs, font_name="Roboto", bold=True)
                cl.refresh()
                tex = cl.texture
                cx = FIELD_L + (i + 0.5) * SLOT_W
                cy = (SLOT_TOP + FLOOR) / 2.0
                if m >= 20:
                    Color(*hex_rgb("#0b1220"))
                else:
                    Color(1, 1, 1)
                Rectangle(texture=tex,
                          pos=(self._px(cx) - tex.width / 2.0,
                               self._py(cy) - tex.height / 2.0),
                          size=tex.size)
            # 投中指示灯(中奖绿/未中红, 结算时变色, 换盘面熄灭)
            self._lamp_cols = []
            ly = SLOT_TOP - 9
            for i in range(NUM_SLOTS):
                col = Color(*hex_rgb(COL_LAMP_OFF))
                self._lamp_cols.append(col)
                cx = FIELD_L + (i + 0.5) * SLOT_W
                Ellipse(**self._circle(cx, ly, 5))
            # 力度条底槽 + 哑火红线(玩家必须看得见阈值在哪)
            Color(*hex_rgb("#1b2b4a"))
            Rectangle(**self._rect(RIGHT_INNER - 9, SLOT_TOP - 210,
                                  RIGHT_INNER - 4, SLOT_TOP - 6))
            Color(*hex_rgb(COL_FIRE))
            ty = (SLOT_TOP - 8) - MISFIRE_POWER * 200
            Rectangle(**self._rect(RIGHT_INNER - 12, ty - 1, RIGHT_INNER - 1, ty + 1))
            # 力度填充(动态)
            self._meter_col = Color(*hex_rgb(COL_METER))
            self._meter_fill = Rectangle(pos=(0, 0), size=(0, 0))
            # 柱塞(动态, 蓄力下压)
            Color(*hex_rgb("#7f8cb0"))
            self._plunger = Rectangle(**self._rect(LANE_L + 6, FLOOR - 6,
                                                  RIGHT_INNER - 6, FLOOR - 2))
            # 球(动态)
            Color(*hex_rgb(COL_BALL))
            self._ball_e = Ellipse(pos=(0, 0), size=(2 * BALL_R * s, 2 * BALL_R * s))
        self.tick_draw()

    def set_lamp(self, i, hex_color):
        if 0 <= i < len(self._lamp_cols):
            self._lamp_cols[i].rgb = hex_rgb(hex_color)

    def lamps_off(self):
        for col in self._lamp_cols:
            col.rgb = hex_rgb(COL_LAMP_OFF)

    def tick_draw(self):
        """每帧只更新动态元素(ball/meter/plunger)的位置和颜色, 不重排 canvas。"""
        if self._ball_e is None:
            return
        g = self.game
        b = g.ball
        if b is not None:
            self._ball_e.pos = (self._ox + (b["x"] - BALL_R) * self._s,
                                self._oyt - (b["y"] + BALL_R) * self._s)
        if g.power > 0.01:
            top = (SLOT_TOP - 8) - g.power * 200
            kw = self._rect(RIGHT_INNER - 9, top, RIGHT_INNER - 4, SLOT_TOP - 8)
            self._meter_fill.pos = kw["pos"]
            self._meter_fill.size = kw["size"]
            weak = g.power < MISFIRE_POWER
            self._meter_col.rgb = hex_rgb(COL_FIRE if weak else COL_METER)
        else:
            self._meter_fill.size = (0, 0)
        py = FLOOR - 6 + g.power * 10
        kw = self._rect(LANE_L + 6, py, RIGHT_INNER - 6, py + 4)
        self._plunger.pos = kw["pos"]
        self._plunger.size = kw["size"]


class RootWidget(BoxLayout):
    """游戏状态机 + 全部控件。逻辑与 tkinter 版 PlinkoApp 一一对应(MVP: 无特效/历史/滚动动画)。"""

    def __init__(self, **kw):
        super().__init__(orientation="vertical", **kw)
        self.geo = build_geo()
        self.multipliers = roll_multipliers()
        self.balance = START_BEADS
        self.bet = DEFAULT_BET
        self.state = "ready"          # ready | charging | flying | misfire | landing | landed
        self.power = 0.0
        self._last_charge_sound = 0.0
        self._charge_topped = False
        self._crossed = False
        self._risen = False
        self._misfire_frames = 0
        self.plays = 0
        self.hits = 0
        self.rtp_target = 0.90
        self.landed_at = 0.0
        self.land_target_x = PLUNGER_X
        self.target_slot = 0
        self.target_x = PLUNGER_X
        self.ball = None
        self.sfx = Sfx(SOUND_ENABLED)
        self._build_ui()
        self.set_bet(self.bet)
        self.set_rtp(self.rtp_target)
        self.park_ball(reroll=False, silent=True)
        Clock.schedule_interval(self._frame, FIXED_DT)

    # ------------------------------ UI ------------------------------
    def _mk_label(self, text, font_size, hexcolor, halign="left", bold=False, **kw):
        lbl = Label(text=text, font_size=font_size, bold=bold,
                    color=hex_rgb(hexcolor) + (1,), halign=halign, valign="middle", **kw)
        lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        return lbl

    def _mk_button(self, text, cb, bg=COL_BTN_OFF):
        b = Button(text=text, background_normal="", background_down="",
                   background_color=hex_rgb(bg) + (1,), color=(1, 1, 1, 1),
                   font_size="15sp", bold=True)
        if cb is not None:
            b.bind(on_release=cb)
        return b

    def _build_ui(self):
        # 顶栏: 标题 + 状态
        top = BoxLayout(size_hint_y=None, height=dp(44), padding=[dp(10), 0])
        top.add_widget(self._mk_label("跳跳的弹珠机", "16sp", COL_TEXT,
                                      "left", True, size_hint_x=0.42))
        self.status_lbl = self._mk_label("按住「发射」蓄力, 松开弹射", "12sp", COL_SUB,
                                         "right", False, size_hint_x=0.58)
        top.add_widget(self.status_lbl)
        self.add_widget(top)
        # 信息栏: 珠子 / 投中统计 / 投入
        info = BoxLayout(size_hint_y=None, height=dp(32))
        info.add_widget(self._mk_label("珠子", "14sp", COL_TEXT, "right", True,
                                       size_hint_x=0.13))
        self.balance_lbl = self._mk_label(str(self.balance), "14sp", COL_BALL,
                                          "left", True, size_hint_x=0.17)
        info.add_widget(self.balance_lbl)
        self.stats_lbl = self._mk_label("0投0中", "13sp", COL_TEXT, "center", True,
                                        size_hint_x=0.34)
        info.add_widget(self.stats_lbl)
        info.add_widget(self._mk_label("投入", "14sp", COL_TEXT, "right", True,
                                       size_hint_x=0.15))
        self.bet_lbl = self._mk_label(str(self.bet), "14sp", COL_BALL, "left", True,
                                      size_hint_x=0.21)
        info.add_widget(self.bet_lbl)
        self.add_widget(info)
        # 游戏区 + 右侧返还率面板
        mid = BoxLayout()
        self.game_area = GameArea(self)
        mid.add_widget(self.game_area)
        rtp = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(86),
                        padding=[dp(8), dp(8)], spacing=dp(10))
        rtp.add_widget(self._mk_label("返还率", "13sp", COL_TEXT, "center", True,
                                      size_hint_y=None, height=dp(28)))
        self.rtp_btns = {}
        for label, val in (("90%", 0.90), ("100%", 1.00), ("110%", 1.10)):
            b = self._mk_button(label, lambda _b, t=val: self.set_rtp(t))
            self.rtp_btns[val] = b
            rtp.add_widget(b)
        rtp.add_widget(Widget())   # 底部弹簧, 把按钮顶到上方
        mid.add_widget(rtp)
        self.add_widget(mid)
        # 投入行
        bets = BoxLayout(size_hint_y=None, height=dp(50),
                         padding=[dp(6), dp(4)], spacing=dp(6))
        bets.add_widget(self._mk_label("投入珠子单位:", "12sp", COL_SUB, "center", False,
                                       size_hint_x=None, width=dp(96)))
        self.bet_btns = {}
        for v in PRESETS:
            b = self._mk_button(str(v), lambda _b, x=v: self.set_bet(x))
            self.bet_btns[v] = b
            bets.add_widget(b)
        self.reset_btn = self._mk_button("重置", lambda _b: self.reset_balance(), bg="#2a2a35")
        bets.add_widget(self.reset_btn)
        self.add_widget(bets)
        # 发射行
        fire = BoxLayout(size_hint_y=None, height=dp(58),
                         padding=[dp(6), dp(4), dp(6), dp(8)], spacing=dp(8))
        self.power_lbl = self._mk_label("", "13sp", COL_METER, "center", True,
                                        size_hint_x=None, width=dp(118))
        fire.add_widget(self.power_lbl)
        self.fire_btn = self._mk_button("按住蓄力发射", None, bg=COL_FIRE)
        self.fire_btn.bind(on_press=lambda _b: self.start_charge(),
                           on_release=lambda _b: self.launch())
        fire.add_widget(self.fire_btn)
        self.add_widget(fire)

    # ------------------------------ 控件状态 ------------------------------
    def _restyle_selects(self):
        for pv, btn in self.bet_btns.items():
            btn.background_color = hex_rgb(COL_BTN if pv == self.bet else COL_BTN_OFF) + (1,)
        for tv, btn in self.rtp_btns.items():
            btn.background_color = hex_rgb(COL_BTN if abs(tv - self.rtp_target) < 1e-6
                                           else COL_BTN_OFF) + (1,)

    def _set_controls_enabled(self, enabled):
        self.fire_btn.disabled = not enabled
        self.reset_btn.disabled = not enabled
        for btn in list(self.bet_btns.values()) + list(self.rtp_btns.values()):
            btn.disabled = not enabled
        if enabled:
            self.fire_btn.background_color = hex_rgb(COL_FIRE) + (1,)
            self.reset_btn.background_color = hex_rgb("#2a2a35") + (1,)
            self._restyle_selects()
        else:
            off = hex_rgb(COL_BTN_OFF) + (1,)
            self.fire_btn.background_color = off
            self.reset_btn.background_color = hex_rgb("#1a1a22") + (1,)
            for btn in list(self.bet_btns.values()) + list(self.rtp_btns.values()):
                btn.background_color = off

    # ------------------------------ 交互 ------------------------------
    def set_bet(self, v):
        self.bet = v
        self._restyle_selects()
        self.bet_lbl.text = str(self.bet)
        self.sfx.play("click")

    def set_rtp(self, t):
        self.rtp_target = t
        self._restyle_selects()
        self.sfx.play("click")
        if self.state == "ready":
            self.multipliers = roll_multipliers(self.rtp_target)
            self.game_area._redraw()

    def reset_balance(self):
        if self.state in ("flying", "misfire", "landing"):
            return
        self.balance = START_BEADS
        self.plays = 0
        self.hits = 0
        self.stats_lbl.text = "0投0中"
        self.balance_lbl.text = str(self.balance)
        self.status_lbl.text = "已重置"
        self.sfx.play("cash")

    def start_charge(self):
        if self.state != "ready":
            return
        if self.balance < self.bet:
            self.status_lbl.text = "珠子不足, 请调低投入或点「重置」"
            self.sfx.play("error", throttle=0.4)
            return
        self.state = "charging"
        self.power = 0.0
        self._last_charge_sound = 0.0        # 立刻响第一声棘轮
        self._charge_topped = False
        self.status_lbl.text = "蓄力中... 松开发射"

    def launch(self):
        if self.state != "charging":
            return
        if self.power < MISFIRE_POWER:
            # 哑火: 球照样弹出去, 只是升不过隔墙顶 -> 掉回柱塞。不扣珠子、不计一局、不换盘面
            self.ball = launch_misfire(self.power)
            self.state = "misfire"
            self._misfire_frames = 0
            self.sfx.play("launch", 0.45 + 0.30 * (self.power / MISFIRE_POWER))
            self._set_controls_enabled(False)
            self.status_lbl.text = "力度不足, 球没飞出竖井 — 未扣珠子"
            return
        self.balance -= self.bet
        self.balance_lbl.text = str(self.balance)
        self.target_slot = choose_target(self.multipliers, self.rtp_target)  # 发射前预定落点
        self.target_x = FIELD_L + (self.target_slot + 0.5) * SLOT_W
        self.ball = launch_ball(self.power)
        self.state = "flying"
        self.plays += 1
        self._crossed = False
        self._risen = False
        self.sfx.play("launch", 0.75 + 0.25 * self.power)
        self.sfx.play("flight", 0.9)          # 一条连续飞行音铺满上升段
        self._set_controls_enabled(False)
        self.status_lbl.text = "发射!"

    # ------------------------------ 结算 ------------------------------
    def settle(self, i):
        m = self.multipliers[i]
        payout = self.bet * m
        self.balance += payout
        if m > 0:
            self.hits += 1
        rate = 100.0 * self.hits / self.plays if self.plays > 0 else 0
        self.stats_lbl.text = "%d投%d中 %.0f%%" % (self.plays, self.hits, rate)
        if payout > 0:
            self.status_lbl.text = "中奖!  +%d 珠 (x%d)" % (payout, m)
        else:
            self.status_lbl.text = "未中"
        self.game_area.set_lamp(i, COL_GREEN if m > 0 else COL_FIRE)
        self.balance_lbl.text = str(self.balance)
        self._play_result_sound(m)

    def park_ball(self, reroll=True, silent=False):
        """重掷盘面(reroll=True), 新球停到柱塞, 回 ready。哑火 reroll=False 防免费刷盘。"""
        if reroll:
            self.multipliers = roll_multipliers(self.rtp_target)
            self.game_area._redraw()
        else:
            self.game_area.lamps_off()
        self.ball = {"x": PLUNGER_X, "y": PLUNGER_Y, "vx": 0.0, "vy": 0.0,
                     "item": None, "born": time.time(), "events": 0, "amp": {},
                     "climb": False, "misfire": False}
        self.state = "ready"
        self.power = 0.0
        self._set_controls_enabled(True)
        if not silent:
            self.sfx.play("ready", 0.8)

    # ------------------------------ 音效 ------------------------------
    def _play_events(self, b):
        ev = b.get("events", 0)
        if not ev:
            return
        amp = b.get("amp") or {}
        for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
            if ev & bit:
                self.sfx.impact(bit, amp.get(bit, 0.0))
        b["events"] = 0
        amp.clear()

    def _play_charge_sound(self, power):
        if power >= 1.0:
            if not self._charge_topped:
                self._charge_topped = True
                self.sfx.play("charge_full")
            return
        now = time.time()
        if now - self._last_charge_sound < 0.25 - 0.18 * clamp(power, 0.0, 1.0):
            return
        self._last_charge_sound = now
        self.sfx.play("ratchet%d" % int(clamp(power, 0.0, 1.0) * 5.99))

    def _play_result_sound(self, m):
        self.sfx.play("pocket")
        if m <= 0:
            self.sfx.play("lose", 0.9)
            return
        tier = 0 if m <= 2 else (1 if m <= 3 else (2 if m <= 5 else (3 if m < 20 else 4)))
        self.sfx.play("win%d" % tier)

    # ------------------------------ 帧循环 ------------------------------
    def _frame(self, dt):
        if self.state == "charging":
            self.power = min(1.0, self.power + CHARGE_RATE * FIXED_DT)
            self._play_charge_sound(self.power)
            weak = self.power < MISFIRE_POWER
            self.power_lbl.text = "力度 %d%%%s" % (int(round(self.power * 100)),
                                                   " 不足" if weak else "")
            self.power_lbl.color = hex_rgb(COL_FIRE if weak else COL_METER) + (1,)
        elif self.state == "flying" and self.ball is not None:
            b = self.ball
            landed = advance_flight(b, self.geo, self.target_x)
            if not self._crossed and b["x"] < FIELD_R and b["y"] < LANE_WALL_TOP:
                self._crossed = True
            elif self._crossed and not self._risen and b["y"] > RISER_Y:
                self._risen = True
                self.sfx.play("riser", 0.9)
            if b["y"] > SLOT_TOP - 40:
                self.status_lbl.text = "即将入袋…"
            elif b["y"] > PEG_TOP:
                self.status_lbl.text = "弹跳中…"
            else:
                self.status_lbl.text = "入场中…"
            if landed is None and time.time() - b["born"] > MAX_FALL_SEC:
                landed = self.target_slot
            if landed is not None:
                i = self.target_slot
                self.land_target_x = FIELD_L + (i + 0.5) * SLOT_W
                self.landed_at = time.time()
                self.state = "landing"
                self.settle(i)
            if self.ball is not None:
                self._play_events(self.ball)
        elif self.state == "misfire":
            self._misfire_frames += 1
            if advance_misfire(self.ball) or self._misfire_frames > MISFIRE_MAX_FRAMES:
                self.ball["x"] = PLUNGER_X
                self.ball["y"] = PLUNGER_Y
                self.ball["vx"] = 0.0
                self.ball["vy"] = 0.0
                self.sfx.play("bounce", 0.55)
                self.park_ball(reroll=False)
        elif self.state == "landing":
            b = self.ball
            b["vx"] += (self.land_target_x - b["x"]) * LAND_K * FIXED_DT
            b["vx"] *= LAND_DAMP
            b["vx"] = clamp(b["vx"], -ALIGN_VX_MAX, ALIGN_VX_MAX)
            b["vy"] += G * FIXED_DT
            b["x"] += b["vx"] * FIXED_DT
            b["y"] += b["vy"] * FIXED_DT
            floor_y = FLOOR - BALL_R
            if b["y"] >= floor_y:
                b["y"] = floor_y
                if b["vy"] > 0:
                    if b["vy"] > 60.0:
                        self.sfx.play("bounce", clamp(b["vy"] / 500.0, 0.3, 1.0), 0.05)
                    b["vy"] = -b["vy"] * LAND_E
            if (abs(b["x"] - self.land_target_x) < 0.8 and abs(b["vy"]) < 10.0
                    and b["y"] >= floor_y - 0.5):
                b["x"] = self.land_target_x
                b["vx"] = 0.0
                b["vy"] = 0.0
                self.state = "landed"
                self.landed_at = time.time()
            elif time.time() - self.landed_at >= 0.5:
                b["x"] = self.land_target_x
                b["y"] = floor_y
                b["vx"] = 0.0
                b["vy"] = 0.0
                self.state = "landed"
                self.landed_at = time.time()
        elif self.state == "landed":
            if time.time() - self.landed_at >= LAND_HOLD:
                self.park_ball()
        if self.state != "charging" and self.power <= 0.01 and self.power_lbl.text:
            self.power_lbl.text = ""
        self.game_area.tick_draw()


# =============================================================================
# App 入口 / 冒烟
# =============================================================================
class PlinkoApp(App):
    def build(self):
        Window.clearcolor = hex_rgb(COL_BG) + (1,)
        if platform != "android":
            Window.size = (420, 780)       # 桌面预览模拟手机竖屏
        self.title = "跳跳的弹珠机"
        self.rootw = RootWidget()
        return self.rootw

    # Android 生命周期: on_pause 必须返回 True 保持 GL 上下文
    def on_pause(self):
        return True

    def on_resume(self):
        return True

    def on_stop(self):
        try:
            self.rootw.sfx.close()
        except Exception:
            pass
        return True


def _smoke():
    """桌面自动冒烟: 建窗 -> 蓄力 -> 发射 -> 截图 -> 哑火 -> 截图。"""
    outdir = os.path.join(tempfile.gettempdir(), "plinko_smoke")
    os.makedirs(outdir, exist_ok=True)
    app = PlinkoApp()

    def shot(name):
        try:
            Window.screenshot(os.path.join(outdir, name))
        except Exception as e:
            print("screenshot fail:", e)

    def s1(dt):
        shot("01_ready.png")
        app.rootw.start_charge()

    def s2(dt):
        app.rootw.power = 0.85
        shot("02_charging.png")
        app.rootw.launch()

    def s3(dt):
        shot("03_flying.png")

    def s4(dt):
        shot("04_after_settle.png")
        r = app.rootw
        if r.state == "ready":
            r.start_charge()
            r.power = 0.05                 # 哑火
            r.launch()

    def s5(dt):
        shot("05_misfire_done.png")
        print("SMOKE DONE ->", outdir)
        App.get_running_app().stop()

    Clock.schedule_once(s1, 1.5)
    Clock.schedule_once(s2, 2.5)
    Clock.schedule_once(s3, 4.0)
    Clock.schedule_once(s4, 8.5)
    Clock.schedule_once(s5, 12.0)
    app.run()


def main():
    global SOUND_ENABLED
    if "--nosound" in sys.argv:
        SOUND_ENABLED = False
    if "--selftest" in sys.argv:
        if not selftest():
            sys.exit(1)
        return
    if "--smoke" in sys.argv:
        _smoke()
        return
    PlinkoApp().run()


if __name__ == "__main__":
    main()
