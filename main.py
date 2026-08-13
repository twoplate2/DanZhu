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
import json

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase, Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle, Line, Ellipse, RoundedRectangle, PushMatrix, PopMatrix, Rotate
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
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
# 深部奇数行右移(治"穿隙"): 球从 24.3° 对角通道直漏不碰钉。只挪深部(r>=PEG_DEEP_MIN_ROW)
# 的奇数行、往右移 PEG_DEEP_SHIFT 列宽 → 通道错开, 穿隙 ~9%→4%; 浅部奇数行不动 → 保住
# 首钉力度区分度(selftest 首钉跨度门禁 49px≥45, 无需改门禁)。贴墙钉固定不随移。
# 实测(1000发/态): 穿隙 9.1%→3.9%, 同钉滞留 max 104→43, 力度区分度 49 保持, 卡死0。
PEG_DEEP_SHIFT = 0.30
PEG_DEEP_MIN_ROW = 3

LANE_WALL_TOP = 160          # 通道隔墙顶部(进一步降低: 更宽敞的入场窗口)
PLUNGER_X = (LANE_L + RIGHT_INNER) / 2.0
PLUNGER_Y = FLOOR - BALL_R - 2   # 发射槽在井底(老版本): 弹簧 Z 字形贴画布底边框露出,
                                  # 像真实弹珠机的弹簧发射装置(球坐在弹簧上, 压缩/释放可见)
RISER_Y = PEG_TOP + (PEG_ROWS - 1) * PEG_SY + BALL_R + PEG_R   # 495: 无钉区起点

# ----------------------------- 物理常量 -----------------------------------
G = 1000.0                   # 重力 px/s^2(1200→1000: 恢复弹珠机节奏 —— 碰钉有可见
                             # 减速反弹, 行穿行 ~0.22s; 曾 1200 加速下落被玩家评为"嗖嗖穿过")
E = 0.20                     # 钉子弹性(低: 接近自由落体)
E_FAST = 0.40                # 高速撞击恢复系数(0.32→0.40: 用户实测弹高仍太低, 提到激进档。
                             # 实测 E_SLOW0.70+E_FAST0.40: ≥20px发占比62%, max35px<一行, 横跳0卡死0)
E_SLOW = 0.70                # 低速接触恢复系数(0.60→0.70: 用户实测弹高太低, 提到激进档,
                             # 低速更弹; 实测≥20px发62%, 仍在物理合理范围)
E_SIDE = 0.20                # 侧碰恢复系数(横滑治本: 侧碰擦面滑下不弹, 回弹感搬到冠碰; 0.20平衡机关枪)
PEG_BOUNCE_VY_MAX = 280.0      # 碰钉后向上速度上限(弹起限幅): 弹高 v²/2G≈39px, 球顶≈48px<行距55,
                             # 保证球弹跳后不会回到上层钉子高度(用户明确要求: 不能弹回上层)。
                             # 300 上限时实测球心最大弹高56px/球顶65px越上层(观感"白下行程")。
                             # 降到280: 弹高尾部收敛到~40px, 回弹可见性(≥10px)不受影响(多数碰撞vy<280)。
PEG_BOUNCE_VY_MIN = 70.0     # 碰钉后保底下落速度(vy<70 补到 70): 防"水平滑翔"横穿。
                             # 甜点扫描: ≤70 时碰后 vy 太低, 球横滑蹭钉(88% 磨蹭碰撞, 黏滞感);
                             # ≥80 磨蹭骤降为 0。70 滞留帧最少/波动最均匀, 减速比~0.5
                             # (碰钉保留一半动能, 轻快弹开)。曾试 30(黏滞投诉)/100(贴钉蹭感)。
PEG_REFLECT_VX_MAX = 300.0   # 碰钉反射横速上限(球碰钉后横速≤300, 横穿≤1钉距, 防"横向跳")
PEG_MIN_ESCAPE = 150.0       # 碰钉后最小逃逸速度(侧向碰撞离开速度不足时沿法线补到150): 防"机关枪"
                             # 密集碰撞(2帧一碰)与同钉黏滞(观众裁决 P0); 顺带消除失速
                             # (ratio<0.3 的"碰后几乎停住")
PEG_KEEP_VY = 0.35           # 比例保底系数: 碰后 vy 至少保留碰前 35%(防失速,
                             # 但不过强 —— 0.5 时碰钉总速保留 78% 像穿阵; 0.35 目标
                             # 减速比 0.55~0.65)
PEG_FRICTION = 0.95          # 碰钉切向摩擦(0.90→0.95: 物理专家组, 摩擦乘反射后的vy直接杀回弹,
                             # 降到0.95只损5%保留弹开, 累计0.95^10≈0.60 不贴钉滑行)。经典版无摩擦
PEG_FRICTION_VY = 0.97       # 法向摩擦(比 vx 轻: 摩擦乘反射后的 vy 直接杀回弹, 0.97 只损3%
                             # 保留弹起, 又防垂直分量越碰越快失控)
PEG_GLANCE_UP = 150.0        # 掠射向上保证(侧碰反射后若仍向下, 给 -150 向上 vy, 弹高~11px):
                             # 增加"回弹频率"(用户"碰一下就弹"), 每发向上 11.8 次/69%碰撞, 球离钉果断。
                             # 幅度有界(弹高11px + 顶击40%≥10px → 可见弹开), 不凭空反物理, 可调可关
PEG_CROWN_ESCAPE = 60.0      # 改法A crown: 顶冠再访时给球的最小横向逃逸速度(沿原 vx 方向)。
                             # 治"球冻在钉顶原地微弹": 给一点横向力让它滚开。软化版: vx==0 不硬给,
                             # 方向沿入射 vx 符号(有物理依据), 避免"凭空横向移动/看不见的手"。
PEG_SPRINT = True            # 570 隔板钉冲刺开关(末段横向刹住+垂直冲刺): False=经典自由弹跳
E_VREF = 700.0               # 过渡参考速度(px/s, 法向)
WALL_E = 0.5
VMAX = 2400.0                # 限速(需 >= 最大发射速度, 防穿透)
FIXED_DT = 1.0 / 60.0
FRAME_MS = 16
SUBSTEPS = 6                 # 子步数(增加: 高速下防穿透)
JITTER = 6.0                 # 撞钉切向随机扰动(大幅降低: 防方向突变 + 防卡死)

LAUNCH_MIN = 1077.0          # 最小发射(随 G=1000 回调, apex≈57 不撞顶)
LAUNCH_MAX = 1114.0          # 满蓄力发射(apex≈17, 不撞顶)
CHARGE_RATE = 0.9            # 蓄力速度(每秒充满比例)
ALIGN_DAMP = 0.86            # 入槽横向阻尼(临界附近防过冲)
ALIGN_VX_MAX = 800.0         # 横速硬上限(提高: 匹配高速下落)
# 转向机构已改为弧面物理导流(build_deflectors): 球碰弧面前纯竖直上升(零干预),
# 碰弧面后由弧面掠射反射改变方向, 之后靠碰钉一次性引导(PEG_STEER_K)+入槽 ALIGN
# 收尾。曾经的三代横向引导(弹簧-阻尼 CROSS_K / 恒定加速度 CROSS_A)全部移除——
# 无碰撞段的任何水平力都会造成"没经过导流槽就转向"的违和感。
ARC_E = 0.50                 # [死代码] 弧面法向反弹: 物理层已不用(电磁弹射器 ARC_EJECT_* 取代), 仅历史残留
                             # 曾试 0.2~0.4 想实现"沿弧面滑行": 弱档抖动(碰-弹-再碰),
                             # 中/满档出口散布 80px+ 且首钉出包络——滑行在此空间物理上不可行,
                             # 反弹系数必须 ≥0.5 出口才确定(首钉 390/350/301 单调稳定)。
ARC_VISUAL = 1.4             # 弧面碰撞半径系数(=渲染层 BALL_VIEW): 球视觉半径 12.6 比碰撞
                             # 半径 9 大 3.6px, 弧面碰撞必须用视觉半径, 球才"与弧面相切"而非
                             # 嵌进弧面 3.6px —— 曲线相切是常识, 球要给足运动空间
ARC_OUT_ANGLE = 35.0         # 弧面缓动出口角(相对竖直向左): 25°→35° 修复落格偏置
                             # (被动化后球总落右侧: 25° 右三槽63%/左5%; 35° 右47%/左14%;
                             # 37° 分布最好(26/34)但球沿钉缝直穿(行穿行0.10s 太急)——
                             # 35° 是分布改善与节奏的平衡点)
ARC_EASE_FRAMES = 3          # 弧面缓动帧数(接触帧缓动带球, 出口速度=入射速度不耗能)
# 电磁弹射器(用户思路): 弧面=航空母舰电磁弹射器, 每次球碰引流槽, 出口角度/力度都不同
ARC_EJECT_ANGLE = 12.0       # 出口角随机 ±12°(治"满力度首钉单一": 每次碰弧面角度不同)
ARC_EJECT_SPEED = (0.7, 1.0) # [已废弃→非线性增幅] 出口力度 ×0.7~1.0
ARC_EJECT_BOOST = 0.2        # 电磁弹射器非线性增幅: 满力度出口 ×(1+BOOST), 弱力度几乎不增(保力度区分)
ARC_EJECT_POW = 2.0          # 增幅非线性指数: 增幅 ∝ launch_power^POW(力度越小增幅越小)
CEIL_VX_KEEP = 1.2           # 天花板反射横向保留系数: 撞天花板后 vx×1.2(入射角≠反射角), 满蓄力首钉更左(vy不变, 下落不拖)
_ARC_FRAME = 0               # 物理帧计数(弧面缓动判定用; 预演/真发各自单调即可, 新球无状态)
LAND_K = 16.0                # 落袋横向软吸附刚度
LAND_DAMP = 0.80             # 落袋横向阻尼
LAND_E = 0.42                # 落袋地板恢复系数: 0.42→弹3~4次逐渐停住, 视觉明显
LAND_BOUNCE_MIN_VY = 220.0   # 落地最低初速: 低于此值就补到这么多, 保证每次都有可见回弹
LAND_BOUNCE_MAX_VY = 220.0   # 落地回弹vy上限(删SLOT_BRAKE后替代防穿帮): 反弹apex≤24px,
                             # 球顶恰=隔板顶DIV_TOP=606不越板。刹车的唯一合法用途(限冲击防弹飞)
                             # 该用一次性冲击上限实现, 而非全程每帧减速

# --------------------- 哑火: 球发射了, 但升不过隔墙顶 -----------------------
# h = v^2/(2G); 要 apex y > LANE_WALL_TOP(160) 需 v < sqrt(2*1000*477) ≈ 977
MISFIRE_POWER = 0.15         # 力度阈值: 低于此值球飞不出竖井
MISFIRE_V_MIN = 475.0        # power→0    的发射速度(apex y≈524, 刚离柱塞一点)
MISFIRE_V_MAX = 935.0        # power→阈值 的发射速度(apex y≈200, 离隔墙顶留余量)
MISFIRE_E = 0.22             # 落回柱塞的弹跳恢复系数
MISFIRE_BOUNCE_VY = 200.0    # 落地速度低于此值直接停住
MISFIRE_MAX_FRAMES = 180     # 兜底(实测最长 121 帧)

START_BEADS = 1000
PRESETS = [1, 10, 50, 100]
DEFAULT_BET = 10
MAX_FALL_SEC = 4.0           # 卡死兜底: 连续静止(无碰撞且 |v|<=40px/s)超过此值才强制结算。
                             # 旧语义"发射后 8s 强制结算"会在球晃动久未落袋时提前 settle ——
                             # 球还在屏幕上动, pocket/win/飘字/震动先出来了, 反馈与画面脱节。
STALL_RETRY_SEC = 1.2        # 卡死重掷阈值: 位置不动超过此值就把球退回柱塞、按同一力度重飞。
                             # 落点在发射前就预定了(choose_target), 重掷只换轨迹 —— 不重复扣珠、
                             # 不重复计局、RTP 一点不动, 所以可以比 MAX_FALL_SEC 早得多地介入。
                             # 比"定住 4s 再凭空结算"体验好: 玩家看到的是球卡了一下重来一次。
STALL_MAX_RETRY = 10          # 向下踢的次数上限; 还是不落才退回 240 步的强制结算(防死循环)
LAND_HOLD = 0.60             # 落袋后球停留展示时长(秒), 短暂展示即快速回准备区
REWARD_EV = 3.35             # _reward_value 的期望; RTP ≈ 非零格概率 x REWARD_EV

# ------------------- 碰撞事件位(物理层 -> GUI 音效层) ----------------------
EV_PEG = 1                   # 撞钉
EV_CEIL = 2                  # 撞天花板弧
EV_WALL = 4                  # 撞外墙/隔墙
EV_DIV = 8                   # 撞槽间隔板
EV_ARC = 16                  # 撞导流弧(静音接触, 只作检测/统计: 折角豁免 + 接触率门禁)

# ----------------------------- 配色(清爽现代) ----------------------------
COL_BG = "#0e1524"
COL_PANEL = "#15223c"
COL_CANVAS = "#0b1220"
COL_WALL = "#2b436e"
COL_LANE = "#0e1830"
COL_PEG = "#7b8fad"            # 调暗留出受击高亮空间(原#c9d6f5过亮)
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
COL_METER = "#f0b000"
COL_BUMPER = "#4a6aa8"       # 底部挡板(比隔板亮, 醒目)
COL_LAMP_OFF = "#243250"     # 指示灯熄灭色
HILITE = "#ffffff"
FONT = "Segoe UI"

def build_pegs():
    """板 B：偶数行钉在槽中心，奇数行钉在槽边界 + 两端贴墙钉(消除死走廊)。
    隔板上方钉(y=570)把落格决定推到最后一刻。"""
    rows = []
    for r in range(PEG_ROWS):
        y = PEG_TOP + r * PEG_SY
        if r % 2 == 0:
            xs = [FIELD_L + (i + 0.5) * PEG_SX for i in range(NUM_SLOTS)]
        else:
            off = PEG_DEEP_SHIFT * PEG_SX if r >= PEG_DEEP_MIN_ROW else 0.0
            # 最右钉(i=NUM_SLOTS-1)不偏移: 右移会把它推近右墙钉(x=450), 形成 <30px
            # 夹缝卡球(球需同时避开两钉 30px)。最右钉保持原位, 其余钉照常右移错开通道。
            xs = [FIELD_L + i * PEG_SX + off for i in range(1, NUM_SLOTS - 1)]
            xs.append(FIELD_L + (NUM_SLOTS - 1) * PEG_SX)
            # 贴墙钉: 圆心移进场区(钉缘距墙 ~3px), 不再嵌进墙 —— 修复"钉墙视觉融合"(用户报告 bug)。
            # 保留贴墙碰撞(消除死走廊), 但钉子在墙外完全可见。贴墙钉固定, 不随深部偏移。
            xs.insert(0, FIELD_L + PEG_R + 3)     # 左墙钉: 圆心=21, 钉缘=15, 距左墙内沿(12) 3px
            xs.append(FIELD_R - PEG_R - 3)         # 右墙钉: 圆心=450, 钉缘=456, 距隔墙内沿(466) 10px
        rows.append([(x, y) for x in xs])
    # 隔板上方钉：y=570，每个隔板正上方一颗(甜点位: 把悬念推到最后一刻)
    div_pegs = []
    for k in range(1, NUM_SLOTS):
        x = FIELD_L + k * SLOT_W
        div_pegs.append((x, 570))
    rows.append(div_pegs)
    return rows


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
    """发射区导流弧: 球纯竖直上升时, 以 20° 入射角碰接触段, 被反射向左上抛体进钉阵。

    整体轨迹 = 竖直上升(碰弧面前 vx=0) → 弧面反射(转向的唯一机构, 接触率 100%)
    → 抛体(纯重力)。弧面是真实碰撞体, 玩家看到"球被导流槽带过去"。

    几何(数值迭代得出, 实测 200 发 × 三档):
    - 弧面 = 右壁口部弧形导轨, 全程切线连续无转折(内外部平滑): 根部圆弧 R=8 平滑
      长出(切线 25°) + 25° 接触段(15px) + 30px 大半径微弯弧延长(R=400, 切线从 25°
      渐变到 19°, 末端 (482.1,94.5))。延长是"护送感": 球碰弧面后出射角 37.8°,
      抛体路径与弧面线夹角 12.8° 距离单调增 —— 球沿导轨方向飞 ~0.2s, 玩家看到球
      被导轨"护送"出去。实测每发 EV_ARC 恰好 1 次(零二次接触)。
    - 接触段 (504,145)→(497.7,131.4) 与竖直夹 24.9°: 球在段中部(t≈0.6)掠射。
      碰弧速度 397/455/578(弱/中/满) → 首钉 x 三档分居右/中/左: 381 / 343 / 284。
    - ARC_E=0.50: 柔和推开。弧面碰撞半径 = 视觉半径(BALL_R*ARC_VISUAL=12.6),
      球与弧面相切不嵌入。渲染 3~4px 金属细带(不锈钢导轨感), 接触时轻"擦"声(rail 0.18)。
    改形状必须重跑迭代验证(接触率100% / 首钉包络[250,450] / 无二次接触 / 卡死0)。"""
    cpts = [(508.0, 148.9), (506.8, 148.3), (505.6, 147.3),
            (504.7, 146.3), (504.0, 145.0), (497.7, 131.4), (495.5, 126.7),
            (492.5, 120.4), (489.8, 114.0), (487.2, 107.5),
            (484.6, 100.9), (482.1, 94.5)]
    return [(cpts[i][0], cpts[i][1], cpts[i + 1][0], cpts[i + 1][1])
            for i in range(len(cpts) - 1)]

def build_geo():
    peg_rows = build_pegs()
    return {
        "pegs": [p for row in peg_rows for p in row],  # 渲染用(平铺)
        "peg_rows": peg_rows,                            # 物理用(按行)
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
    vn = b.vx * nx + b.vy * ny
    if vn < 0:
        b.vx -= (1 + e) * vn * nx
        b.vy -= (1 + e) * vn * ny
        return -vn
    return 0.0


def _mark(b, bit, sp):
    """记录碰撞事件位 + 该类碰撞本帧的最大撞击速率(GUI 读后清零)。"""
    b.events = b.events | bit
    amp = b.amp
    if amp is None:
        amp = {}
        b.amp = amp
    if sp > amp.get(bit, 0.0):
        amp[bit] = sp


def _collide_pegs(b, pegs):
    rr = BALL_R + PEG_R
    rng = getattr(b, "_rng", None) or random    # 确定性: 预演/真发共享同一 rng
    for px, py in pegs:
        dx = b.x - px
        dy = b.y - py
        d2 = dx * dx + dy * dy
        if d2 < rr * rr:
            d = math.sqrt(d2)
            if d > 1e-9:
                nx, ny = dx / d, dy / d
            else:
                a = rng.uniform(0, math.tau)
                nx, ny = math.cos(a), math.sin(a)
            b.x = px + nx * rr
            b.y = py + ny * rr
            vn = -(b.vx * nx + b.vy * ny)           # 法向接近速率
            if vn > 0:                               # 真反弹才处理
                vy_pre = b.vy                        # 碰前 vy(比例保底用)
                # e(v): 低速弹得高(逃逸卡死), 高速粘(保持节奏)
                if abs(nx) > abs(ny):
                    E_eff = E_SIDE                  # 侧碰低弹: 擦面滑下不弹(治横滑), 回弹感搬到冠碰
                else:
                    E_eff = E_SLOW - (E_SLOW - E_FAST) * clamp(vn / E_VREF, 0.0, 1.0)
                E_eff *= rng.uniform(0.92, 1.08)   # 反弹高度 ±8% 随机(用户定稿: 每个反弹略不同, 更真实)
                # 法线扰动(模拟表面粗糙度): 幅度 0.04/±0.15。注意: 曾试加大到 0.08/±0.25
                # 想增加回弹, 但副作用是侧碰反射横向分量被放大 → "凭空横向移动"(用户报告 bug)。
                # 横向稳定性优先, 回弹靠 E_eff 提升, 不靠放大法线扰动。
                g = rng.gauss(0, 0.04)
                g = clamp(g, -0.15, 0.15)
                tx_, ty_ = -ny, nx                   # 切向
                njx = nx + tx_ * g
                njy = ny + ty_ * g
                nrm = math.hypot(njx, njy)
                njx /= nrm; njy /= nrm
                hit = _reflect(b, njx, njy, E_eff)   # 用扰动后法线+e(v)反射
                # 改法A crown: 同钉再访检测 —— 本颗钉是否与上次碰撞的是同一颗(治"同一颗钉反复碰")
                rehit = (b.hit_peg == (px, py))
                # [已删侧碰 GLANCE_UP] 侧碰不再被向上踢(那是"横滑"的发动机), 回弹感搬到冠碰(顶击)
                # crown: 顶冠再访强制分离(治"球冻在钉顶原地微弹")——vy 抬到≥70 向下离开,
                # vx 沿原方向抬到 ±PEG_CROWN_ESCAPE 给横向逃逸(软化: vx==0 不硬给, 避免"看不见的手")
                if rehit and abs(ny) >= abs(nx):
                    if b.vy < PEG_BOUNCE_VY_MIN:
                        b.vy = PEG_BOUNCE_VY_MIN
                    if b.vx != 0:
                        b.vx = math.copysign(max(abs(b.vx), PEG_CROWN_ESCAPE), b.vx)
                # 回弹限幅(QA 对照实验定稿): 允许向上弹起(回弹感), 150 限幅弹高≤11px≤一行
                # 钉距 + PEG_BOUNCE_VY_MIN=70 比例保底已防黏滞(QA 实测去守卫后滞留帧
                # 2.2/发 < 留守卫 4.1/发, 卡死仍 0; 历史黏滞的根因是弹高无上限反复碰同钉)。
                if b.vy < -PEG_BOUNCE_VY_MAX:
                    b.vy = -PEG_BOUNCE_VY_MAX    # 弹起限幅: 弹高≤11px≤一行钉距
                if b.vy >= 0 and b.vy < max(PEG_BOUNCE_VY_MIN, vy_pre * PEG_KEEP_VY):
                    b.vy = max(PEG_BOUNCE_VY_MIN, vy_pre * PEG_KEEP_VY)
                    # 比例保底: 碰后 vy 至少保留碰前一半(轻快弹开甜点 0.45~0.65),
                    # 且不注入能量(碰后≤碰前)。固定保底 70 对高速碰钉是"失速"(ratio 0.2)
                if abs(b.vx) > PEG_REFLECT_VX_MAX:   # 碰钉反射横速限幅: 球碰钉后横向速度
                    b.vx = PEG_REFLECT_VX_MAX * (1.0 if b.vx > 0 else -1.0)  # 受限, 横穿距离
                                                    # ≤1 钉距, 消除"横向跳"(真实弹珠机球不会横向滑翔)
                if abs(nx) > abs(ny) and b.vy >= 0 and b.vy < PEG_MIN_ESCAPE:
                    # 逃逸顺导(治横滑): 侧碰后沿重力向下补速, 不再沿法线横向推(撤掉"凭空横向移动"源)
                    b.vy = PEG_MIN_ESCAPE
                if PEG_SPRINT and py == 570:    # 隔板钉(末段): 软化冲刺 —— 保留 vy 下限
                    b.vx *= 0.7                  # 防贴钉+落袋干净, 但不再把横速刹死:
                    if 0.0 <= b.vy < 160.0:      # 0.5→0.7 保留末段横向多样性(末段决策迟到,
                        b.vy = 160.0             # 悬念落在玩家盯最紧的落袋区)。条件 0.0<=vy<160:
                                                  # 只兜底"仍在向下且不够快"的球, 不抹掉碰钉顶刚
                                                  # 反射的向上分量(治 SPRINT vy 下限压球的顶碰机关枪)
                b.vx *= PEG_FRICTION            # 碰钉摩擦(物理专家组): 摩擦乘反射后的 vy 直接杀
                b.vy *= PEG_FRICTION_VY          # 回弹, vx 用 0.95 防贴钉滑行, vy 用 0.97 少砍
                                                  # 法向(保留弹起), 避免垂直分量失控
                _mark(b, EV_PEG, hit)
                b.last_nx = njx; b.last_ny = njy      # 记录接触法线(兜底滚落用)
                b.hit_peg = (px, py)                   # 被撞钉子坐标(物理"同钉再访"判断用)
                b.peg_flash = (px, py)                 # 被撞钉子坐标(渲染高亮用, 每次碰撞都置)
                b.squash = 1.0 - 0.05 * clamp(vn / E_VREF, 0.0, 1.0)  # 压扁(高速5%,掠射≈0%)
                b.squash_nx = njx; b.squash_ny = njy
                b.spin += (b.vx * njy - b.vy * njx) * 0.02  # 自转积分



def _collide_rect(b, rx1, ry1, rx2, ry2, e, ev=0):
    cx = max(rx1, min(b.x, rx2))
    cy = max(ry1, min(b.y, ry2))
    dx = b.x - cx
    dy = b.y - cy
    d2 = dx * dx + dy * dy
    if d2 < BALL_R * BALL_R:
        d = math.sqrt(d2)
        if d > 1e-9:
            nx, ny = dx / d, dy / d
        else:                                   # 球心在矩形内: 朝最近边推出
            left, right = b.x - rx1, rx2 - b.x
            top, bot = b.y - ry1, ry2 - b.y
            m = min(left, right, top, bot)
            if m == left:
                nx, ny = -1.0, 0.0
            elif m == right:
                nx, ny = 1.0, 0.0
            elif m == top:
                nx, ny = 0.0, -1.0
            else:
                nx, ny = 0.0, 1.0
        b.x = cx + nx * BALL_R
        b.y = cy + ny * BALL_R
        hit = _reflect(b, nx, ny, e)
        if ev == EV_WALL and ry1 == 0 and ry2 == WALL and hit > 0.0:
            # 天花板非镜面反射(轻微倾斜): vx×CEIL_VX_KEEP(入射角≠反射角), vy不变(下落不拖)
            b.vx *= CEIL_VX_KEEP
        if ev and hit > 0.0:
            _mark(b, ev, hit)


def _collide_arc(b, x1, y1, x2, y2, frame=_ARC_FRAME):
    """弧面"接触帧缓动带球"(P5 方案, 新专家组设计): 球碰弧面瞬间不按反射弹开,
    而是被设定到弧面切线方向的出口速度, 方向在 ARC_EASE_FRAMES 帧内从竖直缓动到
    ARC_OUT_ANGLE(每帧 ~8.3°) —— 玩家看到球"滑过导轨逐渐转向", 而非一帧内
    39° 突变横移(投诉"刚开始就突然横向移动")。出口速度=入射速度(弧面不耗能),
    出口方向由几何切线决定(确定) → 轨迹确定性/修订不受影响。
    碰撞半径用视觉半径(球与弧面相切不嵌入)。EV_ARC 静音接触。"""
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else ((b.x - x1) * dx + (b.y - y1) * dy) / L2
    t = max(0.0, min(1.0, t))
    cx, cy = x1 + t * dx, y1 + t * dy
    ox, oy = b.x - cx, b.y - cy
    r = BALL_R * ARC_VISUAL
    if ox * ox + oy * oy >= r * r:
        return
    d = math.sqrt(ox * ox + oy * oy)
    nx, ny = (ox / d, oy / d) if d > 1e-9 else (0.0, -1.0)
    vn = b.vx * nx + b.vy * ny
    if vn >= 0:
        return
    b.x = cx + nx * r
    b.y = cy + ny * r
    st = getattr(b, "arc_ease", None)     # park_ball 等构造的球可能无此字段
    if st is None:
        rng = getattr(b, "_rng", None) or random
        p = getattr(b, "launch_power", 0.5)                    # 蓄力力度(非线性增幅输入)
        boost = 1.0 + ARC_EJECT_BOOST * (p ** ARC_EJECT_POW)   # 非线性增幅: 力度越大增幅越大
        st = [0, -1,                      # [缓动步数, 上次接触帧, 出口角抖动°, 出口力度系数]
              rng.uniform(-ARC_EJECT_ANGLE, ARC_EJECT_ANGLE),
              boost]
        b.arc_ease = st
    n, lf = st[0], st[1]
    if lf != frame:
        n += 1
        lf = frame
        if n == 1 and st[3] != 1.0:       # 电磁弹射器: 第一接触帧应用力度随机一次(不每帧连乘)
            b.vx *= st[3]
            b.vy *= st[3]
            st[3] = 1.0
    th = ARC_OUT_ANGLE * min(1.0, n / ARC_EASE_FRAMES) + st[2]
    a = math.radians(th)
    sp = math.hypot(b.vx, b.vy)
    b.vx = sp * (-math.sin(a))
    b.vy = sp * (-math.cos(a))
    st[0], st[1] = n, lf
    _mark(b, EV_ARC, -vn)


def physics_step(b, geo, dt):
    """推进一帧(拆 SUBSTEPS 子步)。落袋返回槽序号, 否则 None。"""
    sub = dt / SUBSTEPS
    for _ in range(SUBSTEPS):
        b.vy += G * sub
        sp = math.hypot(b.vx, b.vy)
        if sp > VMAX:
            f = VMAX / sp
            b.vx *= f
            b.vy *= f
        b.x += b.vx * sub
        b.y += b.vy * sub
        for w in geo["walls"]:
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _collide_arc(b, s[0], s[1], s[2], s[3], _ARC_FRAME)  # 缓动带球: 贴轨转向, 静音接触
        _collide_pegs(b, geo["pegs"])
        for d in geo["dividers"]:
            _collide_rect(b, d[0], d[1], d[2], d[3], E, EV_DIV)
        if b.y + BALL_R >= FLOOR - 0.5:
            i = int((b.x - FIELD_L) / SLOT_W)
            return max(0, min(NUM_SLOTS - 1, i))
    return None


def power_u(power):
    """有效蓄力区间 [MISFIRE_POWER, 1.0] 归一化到 [0, 1]。低于阈值的是哑火, 不走这里。"""
    return clamp((power - MISFIRE_POWER) / (1.0 - MISFIRE_POWER), 0.0, 1.0)


class Ball:
    """弹珠物理状态。__slots__ 消除 dict 哈希开销(每发 ~18000 次查找→0)。
    保留 __getitem__/__setitem__/get 兼容旧 b.x 语法, 同时支持 b.x 直接访问。"""
    __slots__ = ('x', 'y', 'vx', 'vy', 'item', 'born', 'events', 'amp',
                 'misfire',
                 'launch_power', '_stall_retry', '_rng',
                 'last_nx', 'last_ny',
                 'hit_peg', 'squash', 'squash_nx', 'squash_ny', 'spin',
                 'arc_ease', 'peg_flash')

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)


def launch_ball(power, rng=None):
    """按蓄力比例 power 生成一颗向上发射的球(位于弹簧柱塞处)。

    rng: 撞钉扰动的随机流(None=全局 random)。球完全被动:
    竖直上升 → 碰弧面(缓动带球) → 抛体穿钉阵 → 自然落袋。无任何引导/预定。

    竖直速度只在 1077~1114 的窄带内变化(3%), 是为了让越顶时刻只散 30ms —— 预烘的 1.5s
    连续飞行音(FLIGHT_ENV)靠这个前提才能对齐全过程。三段式轨迹: 竖直上升 → 弧面掠射
    (build_deflectors) → 抛体。转向由弧面物理完成, 发射阶段无任何横向引导。"""
    u = power_u(power)
    speed = LAUNCH_MIN + (LAUNCH_MAX - LAUNCH_MIN) * u
    return Ball(x=PLUNGER_X, y=PLUNGER_Y, vx=0.0, vy=-speed,
                item=None, born=time.time(), events=0, amp={},
                misfire=False,
                launch_power=power, _stall_retry=0, _rng=rng,
                last_nx=0.0, last_ny=-1.0,
                hit_peg=None, squash=1.0, squash_nx=0.0, squash_ny=-1.0, spin=0.0,
                arc_ease=None, peg_flash=None)


def misfire_speed(power):
    """哑火发射速度: 蓄力越小升得越低(线性)。上限 980 保证 apex y≈237 > 160。"""
    u = clamp(power / MISFIRE_POWER, 0.0, 1.0)
    return MISFIRE_V_MIN + (MISFIRE_V_MAX - MISFIRE_V_MIN) * u


def launch_misfire(power):
    """力度不足: 球照样弹出去, 只是升不过隔墙顶, 会掉回柱塞。"""
    b = launch_ball(power)
    b.vy = -misfire_speed(power)
    b.misfire = True
    return b


def advance_misfire(b):
    """竖井内一维升降(实测全程零碰撞, x 恒=PLUNGER_X)。归位返回 True。
    不能走 physics_step: 它的落袋判定没有 x<FIELD_R 保护, 会把落回柱塞的球报成 8 号槽。"""
    b.vy += G * FIXED_DT
    b.y += b.vy * FIXED_DT
    if b.vy > 0 and b.y >= PLUNGER_Y:
        b.y = PLUNGER_Y
        if b.vy > MISFIRE_BOUNCE_VY:
            b.vy = -b.vy * MISFIRE_E
            return False
        b.vy = 0.0
        return True
    return False


def advance_flight(b, geo):
    """推进一帧(GUI/selftest 共用): 弧形导轨越顶 + 物理。
    球完全被动: 纯重力+碰撞, 无任何引导/干预。落袋不减速(删 SLOT_BRAKE:
    球自然重力加速入袋, 撞击槽底后靠回弹强调槽位 —— 用户定稿)。"""
    global _ARC_FRAME
    _ARC_FRAME += 1                # 弧面缓动帧计数
    return physics_step(b, geo, FIXED_DT)


def benchmark_trajectories(duration=0.7, runs=5):
    """性能测试: 每次固定 duration 秒(短, 不触发 CPU 降频), 数帧数, 跑 runs 次取中位。
    返回 (total_flights, total_frames, fps_list) 实测值, 不反推。
    固定种子可复现。固定短时长(而非固定次数)设备无关: PC/手机都 <1s, 不触发 turbo 降频。"""
    geo = build_geo()
    rng = random.Random(12345)   # 固定种子, 不碰全局 random(结果可复现)

    def _run_once():
        flights = 0
        frames = 0
        t0 = time.time()
        while time.time() - t0 < duration:
            power = rng.uniform(MISFIRE_POWER, 1.0)
            b = launch_ball(power, rng=rng)
            for _ in range(4000):
                landed = advance_flight(b, geo)
                frames += 1
                if landed is not None:
                    flights += 1
                    break
        return flights, frames

    _run_once()   # 预热一次(让 CPU 升频/Python 热身), 不计数

    fps_list = []
    total_flights = 0
    total_frames = 0
    for _ in range(runs):
        flights, frames = _run_once()
        fps_list.append(frames / duration)
        total_flights += flights
        total_frames += frames
    return total_flights, total_frames, fps_list


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


def roll_multipliers(rtp=0.80):
    """每格独立: 概率 q=rtp/REWARD_EV 非零, 非零取 _reward_value(E≈3.35)。
    均匀落格下 E[赔付] = q×3.35 = rtp —— 数学期望精确, 无需 choose_target 修正
    (彻底被动方案: 球落格随机, 结算用物理落格, RTP 靠盘面期望)。"""
    q = rtp / REWARD_EV
    mult = [0] * NUM_SLOTS
    for i in range(NUM_SLOTS):
        if random.random() < q:
            mult[i] = _reward_value()
    return mult

# =============================================================================
# 音效层: 程序化合成 16bit PCM + winmm 多声道播放 (纯 stdlib, 无音频文件)
# =============================================================================
SR = 22050                   # 采样率
SFX_VOICES = 8               # 并发声道数(可同时叠加的音效数)
SFX_MASTER = 1.0             # 总音量 (0~1), 手机喇叭需要满幅
SFX_SEED = 20260727          # 合成用固定种子: 每次启动音色一致
SFX_RESULT_LEAD = 0.18       # 结果音(中奖/未中)前置静音: 让入袋声先落地 + 放大揭晓前定格
SFX_LAUNCH_GAIN = 0.60       # 发射音基准音量(=玩家点名要的"哑火那一声"的听感档位)
SFX_LAUNCH_GAIN_MAX = 0.80   # 满蓄力上限。曾经的 0.75~1.0 被评价为"像大炮发射, 太夸张",
                             # 所以顶不能再摸到 1.0(launch 是全库最响的非中奖音, rms .146);
                             # 但也不能像旧代码那样恒定 —— 蓄力没有听觉回报, 手感就少一半。
                             # 0.60~0.80 是"听得出差别但不炸"的折中, 要动请用耳朵校准。
SFX_MISFIRE_GAIN = 0.35      # 哑火发射音: 弱蓄力
SFX_MISFIRE_GAIN_MAX = 0.50  # 哑火发射音: 贴着阈值(差一点就飞出去了)
CHARGE_HOLD_SEC = 0.60       # 满蓄力后"还顶着"提示音的重复间隔
CHARGE_HOLD_GAIN = 0.40      # 该提示音的音量(轻版, 只是凭证不是事件)
SOUND_ENABLED = True         # --nosound / demo 可关

# 音效专用随机流: 与游戏随机流完全隔离(否则合成会扰乱盘面/落点的随机序列)
_ARNG = random.Random(SFX_SEED)

# 撞击音下限(法向速率 px/s): 低于此值视为轻微擦碰, 不发声
SFX_MIN_SP = {EV_PEG: 45.0, EV_CEIL: 60.0, EV_WALL: 70.0, EV_DIV: 45.0,
              EV_ARC: 1e9}   # 弧面接触静音: 永不"过阈值"
# 撞击音满音量参考速率(法向速率 px/s)
SFX_REF_SP = {EV_PEG: 900.0, EV_CEIL: 1300.0, EV_WALL: 700.0, EV_DIV: 700.0}

# 顶部碰撞音: 球冲到最高点"转向"时发声, 不等真撞墙 —— 实测只有满蓄力(apex y=22)才真撞到
# 顶墙, 且撞点就在 apex 上速率仅 77px/s, 按速率定音量必然听不见。转向(vy 由负转正)每发都有,
# 恒在 0.95~1.00s(竖直运动与 vx 无关, 150 发零方差), 正好落在 FLIGHT_ENV 的顶部谷里。
SFX_APEX_Y_LO = 58.0         # 最弱有效蓄力的转向高度(球心 y), 实测
SFX_APEX_Y_HI = 22.0         # 满蓄力的转向高度(球顶几乎贴上顶墙), 实测
SFX_TOP_Y = 30.0             # 低于此高度的撞墙事件不发闷咚: 同帧的 top 音已代表这一下

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
    return _pack(b, 0.72)


def _sfx_wall(f0):
    """撞墙: 闷"咚"(塑料/木质), 低频为主。"""
    b = _buf(0.13)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.045),
                               (1.87, 0.30, 0.018),
                               (3.10, 0.12, 0.008)])
    _add_noise(b, 0.0, 0.008, 0.35, 0.004, 0.18)
    return _pack(b, 0.59)


def _sfx_div(f0):
    """撞隔板: 中频"嗒"。"""
    b = _buf(0.105)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.026),
                               (2.31, 0.40, 0.013),
                               (3.91, 0.16, 0.007)])
    _add_noise(b, 0.0, 0.005, 0.30, 0.002, 0.35)
    return _pack(b, 0.65)


def _sfx_rail():
    """天花板金属弧: 钟形"锵", 带一点混响余韵。"""
    b = _buf(0.34)
    _add_partials(b, 0.0, 760.0, [(1.00, 1.00, 0.110),
                                  (2.76, 0.55, 0.070),
                                  (5.40, 0.30, 0.040),
                                  (8.93, 0.15, 0.022)])
    _add_noise(b, 0.0, 0.006, 0.30, 0.003, 0.70)
    _reverb(b, 0.18, 0.35)
    return _pack(b, 0.65)


def _sfx_launch():
    """发射: 柱塞"咔" + 弹簧下滑 boing(不含风声 — 风声交给 flight 连续音)。"""
    b = _buf(0.28)
    _add_noise(b, 0.000, 0.010, 0.55, 0.005, 0.85)          # 释放咔哒
    _add_chirp(b, 0.005, 640.0, 150.0, 0.11, 0.55, 0.085, 1.4)
    _add_partials(b, 0.005, 152.0, [(1.00, 0.80, 0.16),
                                    (2.40, 0.25, 0.06)])    # 弹簧余振
    return _pack(b, 0.75)


# 飞行音包络: 实测 400 次飞行的中位速度曲线(归一化), 每 0.1s 一点。
# 形状 = 出膛最快 -> 碰弧面缓动转向(0.63s) -> 抛体上升减速 -> 顶部滞空(0.97s 谷)
#      -> 俯冲加速 -> 首次撞钉收尾淡出。电磁弹射器(出口×0.7~1.0)后重测:
#      碰弧面 0.63s / 谷 0.97s / 首钉中位 1.33s(p5~p95=1.22~1.62s)。
FLIGHT_ENV = [1.00, 0.91, 0.82, 0.72, 0.63, 0.54, 0.45, 0.30,
              0.23, 0.18, 0.18, 0.21, 0.27, 0.32, 0.34, 0.25]
FLIGHT_DUR = 1.50
FLIGHT_GRAIN_END = 0.65      # 颗粒(滚动感)淡出时刻: 球此时已碰弧面离开竖井钢轨, 之后是空中气流


def _sfx_flight():
    """一条连续飞行音: 球压着竖井钢轨滚上去 -> 越顶离轨后化为气流, 一直铺到首次撞钉。
    "滚"的听觉线索有两条, 缺一不可:
      1. 窄带共振噪声(二阶谐振器) = 硬球压硬轨的沙沙, 共振频率跟球速走(越快越亮);
      2. 低频颗粒调制 = 轨道纹理的碾过感, 颗粒密度跟球速走(越快越密), 0.58s 后淡出(球已碰弧面离轨)。
    包络仍是 FLIGHT_ENV(实测中位速度), 所以 1.5s 的音画锚点一个都没动。
    每样本恰好取 1 个随机数(与上一版风声相同), 后面音效的 _ARNG 序列不受影响。"""
    n = int(SR * FLIGHT_DUR)
    b = [0.0] * n
    seg = (len(FLIGHT_ENV) - 1) / FLIGHT_DUR
    blk = 128                                        # 参数按块更新: 省掉 33k 次 cos/插值, 听不出
    r = 0.93                                         # 谐振器极点半径(带宽≈420Hz, 有管腔感又不啸叫)
    rr = r * r
    z1 = z2 = 0.0
    ph = 0.0
    dph = 0.0
    cosw = 1.0
    e = FLIGHT_ENV[0]
    grain = 0.0
    for i in range(n):
        if i % blk == 0:
            t = i / SR
            u = t * seg                              # 包络控制点插值
            k = int(u)
            if k >= len(FLIGHT_ENV) - 1:
                e = FLIGHT_ENV[-1]
            else:
                f = u - k
                e = FLIGHT_ENV[k] * (1.0 - f) + FLIGHT_ENV[k + 1] * f
            cosw = math.cos(math.tau * (330.0 + 560.0 * e) / SR)
            dph = math.tau * (46.0 + 88.0 * e) / SR
            grain = 0.34 * max(0.0, 1.0 - t / FLIGHT_GRAIN_END)
        ph += dph
        y = _ARNG.uniform(-1.0, 1.0) + 2.0 * r * cosw * z1 - rr * z2
        z2 = z1
        z1 = y
        b[i] = y * (1.0 - r) * (e ** 1.2) * (1.0 - grain + grain * math.sin(ph))
    _add_chirp(b, 0.00, 150.0, 96.0, 0.34, 0.10, 0.26, 1.0)  # 竖井内的低频管腔感
    return _pack(b, 0.57, fi=0.004, fo=0.110)


def _sfx_top(hard):
    """球冲到顶点转向: 顶部一声碰撞。中频金属"铛", 比撞墙的闷咚亮得多(手机小喇叭也听得清)。
    hard=1 是接近满蓄力那档(真撞上顶墙): 更亮、余韵更长。"""
    b = _buf(0.17 if hard else 0.13)
    f0 = 610.0 if hard else 520.0
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.048 if hard else 0.036),
                               (2.13, 0.55, 0.026),
                               (3.79, 0.26, 0.013),
                               (6.11, 0.11, 0.007)])
    _add_noise(b, 0.0, 0.007, 0.36, 0.0028, 0.72)
    _reverb(b, 0.10, 0.22)
    return _pack(b, 0.78 if hard else 0.52)


def _sfx_ratchet(lev):
    """蓄力棘轮: lev 0..5, 越高越亮越响(配合间隔变密 = 越蓄越急)。
    峰值/亮度都比初版高一截: 初版低档 340Hz、峰值 0.22、有效时长仅 20ms, 手机喇叭低频响应
    差 + 短音听觉积分不足, 蓄力前半段直接掉到可闻阈下(实测反馈"蓄满了才听见")。"""
    b = _buf(0.05)
    _add_noise(b, 0.0, 0.003, 0.45, 0.0012, 0.70)
    _add_partials(b, 0.0, 380.0 + lev * 98.0, [(1.00, 1.00, 0.010),
                                               (2.70, 0.52, 0.005)])
    return _pack(b, 0.6 + lev * 0.032)


def _sfx_charge_full():
    """满蓄力"顶到底": 弹簧压实的闷响 + 一声高音扣锁 → 听到就知道可以松手了。"""
    b = _buf(0.19)
    _add_partials(b, 0.000, 178.0, [(1.00, 1.00, 0.050), (2.05, 0.30, 0.018)])
    _add_noise(b, 0.000, 0.009, 0.40, 0.004, 0.30)
    _add_partials(b, 0.014, 1260.0, [(1.00, 0.45, 0.020), (2.02, 0.18, 0.010)])
    return _pack(b, 0.6)


def _sfx_pocket():
    """入袋确认: 深长闷响 + 金属锁扣"咔哒"(球坐进槽底卡住; 复用撞钉 tink 的非谐词汇)。"""
    b = _buf(0.24)
    _add_partials(b, 0.0, 120.0, [(1.00, 1.00, 0.085), (2.03, 0.25, 0.028)])
    _add_noise(b, 0.0, 0.010, 0.30, 0.008, 0.15)            # 闷噪声: "沉进去"不是"撞"
    _add_partials(b, 0.006, 1480.0, [                        # 金属锁扣(非谐=tink 同款"叮")
        (1.00, 0.30, 0.010),
        (2.01, 0.18, 0.006),
        (3.42, 0.09, 0.004),
    ])
    _add_noise(b, 0.006, 0.003, 0.18, 0.0012, 0.75)         # 锁扣的清脆瞬态
    return _pack(b, 0.74)


def _sfx_bounce():
    """落地弹跳: 钢珠撞槽底, 短亮带金属"叮"瞬态(逐跳渐弱在播放层按 vy 做)。"""
    b = _buf(0.11)
    _add_partials(b, 0.0, 180.0, [(1.00, 1.00, 0.026), (2.05, 0.30, 0.011)])
    _add_partials(b, 0.0, 3200.0, [(1.00, 0.18, 0.008)])    # 金属"叮"(非谐高频, 钢珠指纹)
    _add_noise(b, 0.0, 0.005, 0.30, 0.0022, 0.45)           # 亮噪声攻击瞬态
    return _pack(b, 0.50)


def _sfx_riser():
    """入袋前铺垫: 球穿出最后一排钉进入无钉区(y>495)时响。
    下行滑音(球在下落, 上滑是"起飞"语法会违和), 尾音落到 140Hz 正好接进落地 thud。"""
    n = int(SR * 0.16)
    b = [0.0] * n
    ph = 0.0
    for i in range(n):
        t = i / (n - 1.0)
        f = 520.0 - 380.0 * (t ** 1.5)               # 520 -> 140Hz 下行(前段慢后段快坠)
        ph += math.tau * f / SR
        env = t ** 1.5                                # 渐强
        b[i] = (math.sin(ph) + 0.15 * math.sin(2.03 * ph)) * env
    z = 0.0                                          # 一层很轻的气声托底
    for i in range(n):
        t = i / (n - 1.0)
        z += 0.5 * (_ARNG.uniform(-1.0, 1.0) - z)
        b[i] += 0.12 * z * (t ** 2.0)
    return _pack(b, 0.40, fi=0.003, fo=0.015)


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
    return _pack(b, 0.44)


def _sfx_click():
    """UI 按键: 极短软咔。"""
    b = _buf(0.035)
    _add_noise(b, 0.0, 0.0025, 0.50, 0.0012, 0.75)
    _add_partials(b, 0.0, 940.0, [(1.00, 0.50, 0.006), (2.60, 0.20, 0.003)])
    return _pack(b, 0.52)


def _sfx_error():
    """珠子不足: 低频颤音"嗡"。"""
    b = _buf(0.28)
    w = math.tau * 155.0 / SR
    for i in range(len(b)):
        trem = 0.55 + 0.45 * math.sin(math.tau * 19.0 * i / SR)
        env = min(1.0, i / (SR * 0.006)) * math.exp(-i / (SR * 0.16))
        b[i] = (math.sin(w * i) + 0.34 * math.sin(3 * w * i) +
                0.16 * math.sin(5 * w * i)) * trem * env
    return _pack(b, 0.44)


def _sfx_coin():
    """计分滚动的细碎"叮"(数字翻滚时连播)。"""
    b = _buf(0.035)
    _add_partials(b, 0.0, 2280.0, [(1.00, 1.00, 0.007), (2.02, 0.40, 0.004)])
    _add_noise(b, 0.0, 0.002, 0.18, 0.001, 0.90)
    return _pack(b, 0.47)


def _sfx_ready():
    """新球滚进柱塞就位。"""
    b = _buf(0.16)
    _add_partials(b, 0.000, 255.0, [(1.00, 1.00, 0.022), (2.30, 0.30, 0.010)])
    _add_noise(b, 0.000, 0.050, 0.14, 0.030, 0.25)
    _add_partials(b, 0.075, 300.0, [(1.00, 0.50, 0.016)])
    return _pack(b, 0.49)


def _sfx_cash():
    """重置珠子: 一串硬币落盘。"""
    b = _buf(0.55)
    for k in range(7):
        t = 0.02 + k * 0.06 + _ARNG.uniform(-0.012, 0.012)
        _add_partials(b, t, 1900.0 + _ARNG.uniform(-260.0, 520.0),
                      [(1.00, 0.80, 0.010), (2.03, 0.30, 0.005)])
        _add_noise(b, t, 0.002, 0.12, 0.001, 0.90)
    _reverb(b, 0.14, 0.25)
    return _pack(b, 0.59)


def iter_bank():
    """按固定顺序逐个合成 (名字, PCM)。**顺序即音色**: 所有配方共用 _ARNG 一条随机流,
    换了顺序噪声实例就变, 所以安卓端"边烘边加载"必须走这同一个顺序。
    新增音效一律追加在末尾, 免得扰动既有音色。"""
    _ARNG.seed(SFX_SEED)                    # 每次烘焙音色完全一致
    for i, f0 in enumerate((1040.0, 1180.0, 1330.0, 1500.0, 1680.0, 1880.0)):
        yield "peg%d" % i, _sfx_tink(f0)
    for i, f0 in enumerate((185.0, 225.0)):
        yield "wall%d" % i, _sfx_wall(f0)
    for i, f0 in enumerate((430.0, 505.0)):
        yield "div%d" % i, _sfx_div(f0)
    for lev in range(6):
        yield "ratchet%d" % lev, _sfx_ratchet(lev)
    yield "charge_full", _sfx_charge_full()
    yield "rail", _sfx_rail()
    yield "launch", _sfx_launch()
    yield "flight", _sfx_flight()
    yield "riser", _sfx_riser()
    yield "pocket", _sfx_pocket()
    yield "bounce", _sfx_bounce()
    for tier in range(len(WIN_TIERS)):
        yield "win%d" % tier, _sfx_win(tier)
    yield "lose", _sfx_lose()
    yield "click", _sfx_click()
    yield "error", _sfx_error()
    yield "coin", _sfx_coin()
    yield "ready", _sfx_ready()
    yield "cash", _sfx_cash()
    for hard in (0, 1):
        yield "top%d" % hard, _sfx_top(hard)


def bake_bank():
    """合成全部音效 -> {名字: PCM字节}。约 11.5s 素材, 耗时 ~350ms(后台线程跑)。"""
    return dict(iter_bank())

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
#
# 两种接口模式:
#   "pcm"   —— winmm: Sfx 把缩放后的 PCM 直接送声卡
#   "named" —— SoundPool/SoundLoader: 音效先落盘成 WAV, 按名字播, gain 就是音量
def _sfx_cache_dir():
    """音效 WAV 落盘目录(named 后端要文件路径; winmm 直接播 PCM 用不到)。"""
    if platform == "android":
        try:
            base = App.get_running_app().user_data_dir
        except Exception:
            base = tempfile.gettempdir()
    else:
        base = tempfile.gettempdir()
    d = os.path.join(base, "plinko_sfx")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _sfx_code_tag():
    """缓存指纹: 本文件的 mtime+size(装了新 APK 就变) + 合成种子 + 采样率。
    音效配方改了 -> main.py 变了 -> 指纹变 -> 旧 WAV 整目录作废, 不会拿旧配方冒充新的。
    比手工维护版本号可靠 —— 那种早晚会忘记 bump。"""
    try:
        st = os.stat(os.path.abspath(__file__))
        return "%d.%d.%d.%d" % (SFX_SEED, SR, int(st.st_mtime), st.st_size)
    except Exception:
        return "%d.%d.nofile" % (SFX_SEED, SR)


def _wav_write(path, pcm):
    """原子写: 先写 .tmp 再 replace。半截文件绝不能留在缓存里被下次启动当成有效音效。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(pcm_to_wav(pcm))
    os.replace(tmp, path)


def _wav_wipe(d):
    for fn in os.listdir(d):
        if fn.endswith(".wav") or fn.endswith(".tmp") or fn == "stamp":
            try:
                os.remove(os.path.join(d, fn))
            except Exception:
                pass


def _voice_dir():
    """预录语音目录(与 main.py 同级; 目录不存在时静默为空 —— 语音是安卓版附加功能)。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice")


def _voice_files():
    """{语音名: wav 路径}。语音是 edge-tts 预录文件(tools/generate_voice.py 生成),
    不是 bake_bank 的合成品, 不参与 iter_bank 的"顺序即音色"体系。"""
    out = {}
    try:
        for fn in os.listdir(_voice_dir()):
            if fn.endswith(".wav"):
                out[fn[:-4]] = os.path.join(_voice_dir(), fn)
    except Exception:
        pass
    return out


def _read_wav_pcm(path):
    """读 22050Hz 16bit mono wav -> 裸 PCM 字节(winmm pcm 模式用; 格式不符直接拒)。"""
    import wave
    with wave.open(path, "rb") as wf:
        if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (1, 2, SR):
            raise ValueError("voice wav 不是 %dHz 16bit mono: %s" % (SR, path))
        return wf.readframes(wf.getnframes())


class _SoundPoolOut:
    """Android SoundPool: 短音效全部解压进内存, 并发交给硬件 mixer。
    不再用 OnLoadCompleteListener 做"加载完才准播"的门禁: 那个 PythonJavaClass 代理是
    从 SoundPool 自己的线程回调过来的, 一旦失灵(或被 GC)就是全库永久静音, 而 play() 对
    尚未加载完的 sample 本来就只是返回 0 什么都不做 —— 用不着这个单点故障。"""
    mode = "named"
    name = "SoundPool"

    def __init__(self, voices=SFX_VOICES):
        from jnius import autoclass
        SoundPool = autoclass("android.media.SoundPool")
        AudioAttributes = autoclass("android.media.AudioAttributes")
        attrs = (AudioAttributes.Builder()
                 .setUsage(AudioAttributes.USAGE_GAME)
                 .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                 .build())
        self._sp = (SoundPool.Builder()
                    .setMaxStreams(voices)
                    .setAudioAttributes(attrs)
                    .build())
        self._ids = {}

    def prime(self, name, path):
        sid = self._sp.load(path, 1)
        if not sid:                          # 0 = 加载失败(文件坏了/格式不对)
            raise RuntimeError("SoundPool.load failed: " + path)
        self._ids[name] = sid

    def play_named(self, name, gain01):
        sid = self._ids.get(name)
        if sid is None:
            return False
        self._sp.play(sid, gain01, gain01, 1, 0, 1.0)
        return True

    def pause(self):
        try:
            self._sp.autoPause()            # 切后台/静音: 暂停所有流
        except Exception:
            pass

    def resume(self):
        try:
            self._sp.autoResume()
        except Exception:
            pass

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

    def prime(self, name, path):
        snd = self._loader.load(path)
        if snd is None:
            raise RuntimeError("SoundLoader.load failed: " + path)
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
    """按优先级选后端: Android SoundPool > winmm > Kivy SoundLoader > 静音。
    环境变量 PLINKO_SFX_BACKEND=kivy|winmm|none 可在桌面强制指定 —— 安卓走的是 named
    这条路径(缓存/落盘/按名播), 桌面默认走 winmm 的 pcm 路径, 不强制就没法在开发机上验它。"""
    want = os.environ.get("PLINKO_SFX_BACKEND", "").lower()
    if want == "none":
        return None
    if platform == "android" and want not in ("kivy", "winmm"):
        try:
            return _SoundPoolOut()
        except Exception:
            pass
    if want != "kivy":
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
    named 后端(SoundPool/SoundLoader): WAV 落盘 + 按名字播, gain 直接给后端当音量。"""

    def __init__(self, enabled=True, sync=False):
        self.enabled = bool(enabled)
        self.out = None
        self.bank = {}
        self.named = set()          # named 后端里已经可播的音效名
        self.bake_ms = 0.0
        self.cached = False         # 本次启动是否命中磁盘缓存(没现场合成)
        self._scaled = {}
        self._last = {}
        self._last_voice = 0.0       # 全局语音间隔: 防重叠
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
        try:
            if getattr(self.out, "mode", "pcm") == "pcm":
                self._bake_pcm()
            else:
                self._bake_named()
        except Exception:
            pass
        self.bake_ms = (time.perf_counter() - t0) * 1000.0

    def _bake_pcm(self):
        self.bank = bake_bank()             # 整体赋值(引用切换), 读侧只会看到空或全量
        for name, path in _voice_files().items():   # 预录语音并入 bank, winmm 同路径可播
            try:
                self.bank[name] = _read_wav_pcm(path)
            except Exception:
                continue
        for name in ("win0", "win1", "win2", "win3", "win4", "lose",
                     "launch", "riser", "top0", "top1"):
            for g in (1.0, 0.9, 0.85):     # 预热长音效的音量缓存
                self.play_prepare(name, g)
        warm = getattr(self.out, "warm", None)
        if warm is not None:
            warm()                          # 预开所有声道(每个 8.8ms, 放后台)

    def _bake_named(self):
        """命中缓存就直接加载 WAV; 否则边合成边落盘边加载 —— 每烘好一个立刻能播。
        手机上整库合成要好几秒(纯 Python 浮点循环), 若等整库烘完才 prime, 开局第一次
        蓄力必然一声不响; 而 iter_bank 的顺序里 ratchet 排在前 15%, 边烘边用就赶得上。
        stamp 记的是 "指纹 / 名字:字节数", 逐个核对大小 —— 只查存在性的话, 一个被截断的
        WAV 会被当成有效缓存永久加载失败(实测踩到), 而这正是最难发现的一类静默故障。"""
        d = _sfx_cache_dir()
        tag = _sfx_code_tag()
        stamp = os.path.join(d, "stamp")
        if self._load_cached(d, stamp, tag):
            self.cached = True
            self._prime_voice()
            return
        _wav_wipe(d)
        lines = []
        for name, pcm in iter_bank():
            if name == "flight":           # flight 音效已移除, 但 iter_bank 必须保留(顺序即音色)
                continue
            path = os.path.join(d, name + ".wav")
            try:
                _wav_write(path, pcm)
                self.out.prime(name, path)
            except Exception:
                continue
            self.named.add(name)
            lines.append("%s:%d" % (name, os.path.getsize(path)))
        self._prime_voice()
        try:
            with open(stamp, "w") as f:
                f.write(tag + "\n" + "\n".join(lines))
        except Exception:
            pass
        time.sleep(0.5)   # 等 SoundPool 异步解码完第一批音效, 否则首次运行必然全静音

    def _prime_voice(self):
        """预录语音直接 prime APK 内原文件(voice/*.wav), 不落缓存不进 stamp 指纹:
        每次启动都重新加载, 语音文件更新即生效; play() 对未加载完的 sample 本来就返回 0。"""
        for name, path in _voice_files().items():
            try:
                self.out.prime(name, path)
            except Exception:
                continue
            self.named.add(name)

    def _load_cached(self, d, stamp, tag):
        """缓存有效(指纹一致 + 每个 WAV 大小对得上)则全部加载并返回 True。"""
        try:
            with open(stamp, "r") as f:
                head, _, body = f.read().partition("\n")
        except Exception:
            return False
        if head != tag:
            return False
        want = []
        for line in body.split("\n"):
            name, _, size = line.partition(":")
            if not name or not size.isdigit():
                return False
            if name == "flight":               # flight 已移除, 跳过缓存加载
                continue
            path = os.path.join(d, name + ".wav")
            try:
                if os.path.getsize(path) != int(size):
                    return False
            except OSError:
                return False
            want.append((name, path))
        if not want:
            return False
        for name, path in want:
            try:
                self.out.prime(name, path)
            except Exception:
                continue
            self.named.add(name)
        return True

    def play_prepare(self, name, gain):
        pcm = self.bank.get(name)
        if pcm is None:
            return
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        key = (name, lvl)
        if lvl > 0 and key not in self._scaled:
            self._scaled[key] = pcm if lvl >= 10 else _scale_pcm(pcm, lvl / 10.0)

    def play(self, name, gain=1.0, throttle=0.0):
        if not self.enabled:
            return False
        now = time.time()
        # UI交互语音互斥: 上一个没播完(≤3s)前新的不出声; 结果/轮次语音不在此限
        if name.startswith(("voice_rtp_", "voice_bet_", "voice_mode_")):
            if now - self._last_voice < 3.0:
                return False
            self._last_voice = now
        pcm_mode = getattr(self.out, "mode", "pcm") == "pcm"
        if pcm_mode:
            pcm = self.bank.get(name)
            if pcm is None:                  # 还没烘焙好(启动后 ~350ms 内)
                return False
        elif name not in self.named:          # 还没落盘/加载好
            return False
        lvl = int(round(clamp(SFX_MASTER * gain, 0.0, 1.0) * 10.0))
        if lvl <= 0:
            return False
        if throttle > 0.0:
            if now - self._last.get(name, 0.0) < throttle:
                return False
            self._last[name] = now
        if pcm_mode:
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

    def top(self, y):
        """顶部碰撞: 球冲到最高点转向时发声(y = 转向高度, 越小=蓄力越足=撞得越实)。
        不走 impact 是因为 apex 处法向速率≈0, 按速率定音量就等于不发声。"""
        t = clamp((SFX_APEX_Y_LO - y) / (SFX_APEX_Y_LO - SFX_APEX_Y_HI), 0.0, 1.0)
        return self.play("top%d" % (1 if t > 0.5 else 0), 0.62 + 0.38 * t)

    def voice_duration(self, name):
        """语音片段时长(秒), 用于队列播放的调度间隔。
        pcm 后端: bank 中有 PCM → 按字节数算; named 后端: 从 voice 目录的 WAV 文件大小推算。"""
        pcm = self.bank.get(name)
        if pcm:
            return len(pcm) / (SR * 2.0)
        # named 后端: bank 里没有 PCM, 查 voice 目录文件大小
        path = _voice_files().get(name)
        if path:
            try:
                return (os.path.getsize(path) - 44) / (SR * 2.0)
            except OSError:
                pass
        return 0.15  # 回落值(典型单字约 200ms)

    def close(self):
        if self.out is not None:
            self.out.close()
            self.out = None
        self.enabled = False

    def pause_out(self):
        """切后台: 暂停输出(winmm 无暂停概念, 跳过)。"""
        m = getattr(self.out, "pause", None)
        if m is not None:
            m()

    def resume_out(self):
        m = getattr(self.out, "resume", None)
        if m is not None:
            m()

    def set_enabled(self, on):
        """静音开关: 关时立即暂停输出, 开时恢复。"""
        self.enabled = bool(on)
        if not on:
            self.pause_out()
        else:
            self.resume_out()

def selftest(n=40000):
    """验证: (1) 各档 RTP 精确=档位; (2) 引导飞行落点=预定槽、不卡死;
    (3) 碰撞事件覆盖率(音效触发源); (4) 音效库体检。

    n 必须够大: 单发赔付方差很大(取值 0/2/3/5/10/20, σ≈3), n=4000 时均值标准误 ≈0.047,
    与 ±0.05 的门禁同量级 -> 假失败率实测 30%。n=40000 使标准误降到 ≈0.015(3σ 门禁),
    代价只有 0.4 秒。"""
    geo = build_geo()
    ok = True

    # (1) 盘面 RTP 期望: 均匀落格下 E[赔付]=档位(彻底被动, 无 choose_target 修正)
    print("== 返还率精确性(均匀落格盘面期望) ==")
    for rtp in (0.80, 1.20, 2.00, 3.00):
        tot = 0.0
        for _ in range(n):
            board = roll_multipliers(rtp)
            tot += board[random.randrange(NUM_SLOTS)]
        realized = tot / n
        good = abs(realized - rtp) < 0.05
        ok = ok and good
        print("  档位 %.2f -> 实测 RTP %.3f  %s" % (rtp, realized, "OK" if good else "偏差!"))

    # (2) 被动飞行: 升过通道顶(apex) -> 越入场区 -> 落袋, 且不卡死
    print("== 被动飞行(升到顶->越顶入场->落袋 & 不卡死) ==")
    m = 1500
    stuck = no_top = no_enter = 0
    ev_flights = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0, EV_ARC: 0}
    ev_audible = {EV_PEG: 0, EV_CEIL: 0, EV_WALL: 0, EV_DIV: 0}
    for _ in range(m):
        b = launch_ball(random.uniform(MISFIRE_POWER, 1.0))   # 低于阈值的是哑火, 由 (2b) 覆盖
        min_y = b.y
        entered = False
        landed = None
        seen = 0
        loud = 0
        stall_frames = 0
        last_xy = (b.x, b.y)
        for _ in range(4000):
            landed = advance_flight(b, geo)
            lx, ly = last_xy
            if (b.x - lx) ** 2 + (b.y - ly) ** 2 > 1.0:
                stall_frames = 0
            else:
                stall_frames += 1
            if stall_frames > 72 and getattr(b, "_stall_retry", 0) < STALL_MAX_RETRY:
                # 踢球(与 GUI 一致, 不退回重发)
                nx, ny = getattr(b, "last_nx", 0.0), getattr(b, "last_ny", -1.0)
                tx_, ty_ = -ny, nx
                if ty_ > 0: tx_, ty_ = -tx_, -ty_
                b.vx += tx_ * 120.0
                b.vy += ty_ * 120.0
                b._stall_retry = getattr(b, "_stall_retry", 0) + 1
                stall_frames = 0
                entered = False
            last_xy = (b.x, b.y)
            ev = b.events
            if ev:                             # 模拟 GUI: 每帧读事件位后清零
                seen |= ev
                for bit, spd in b.amp.items():   # spd=振幅(改名避免shadow kivy sp单位)
                    if spd >= SFX_MIN_SP[bit]:
                        loud |= bit
                b.events = 0
                b.amp.clear()
            min_y = min(min_y, b.y)
            if b.x < FIELD_R:
                entered = True
            if landed is not None:
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
    ok = ok and stuck == 0 and no_top == 0 and no_enter == 0
    print("  升过通道顶失败: %d/%d   越顶入场失败: %d/%d   卡死: %d" %
          (no_top, m, no_enter, m, stuck))

    # (2b) 哑火: 力度 < MISFIRE_POWER 时球照样弹出去, 但必须升不过隔墙顶并原路掉回柱塞
    print("== 哑火(发射了但升不过隔墙顶) ==")
    mf = 500
    bad_apex = bad_x = bad_home = 0
    apex_hi_y, apex_lo_y = 1e9, -1e9     # apex_hi_y = 升得最高(y 最小)的那一发
    frames_max = 0
    for k in range(mf):
        power = MISFIRE_POWER * k / (mf - 1.0)
        b = launch_misfire(power)
        apex = b.y
        home = False
        used = 0
        for used in range(1, MISFIRE_MAX_FRAMES + 1):
            done = advance_misfire(b)
            apex = min(apex, b.y)
            if abs(b.x - PLUNGER_X) > 1e-9:
                break
            if done:
                home = True
                break
        frames_max = max(frames_max, used)
        apex_hi_y = min(apex_hi_y, apex)
        apex_lo_y = max(apex_lo_y, apex)
        if apex <= LANE_WALL_TOP + 40:       # 离隔墙顶(160)留 40px 安全余量
            bad_apex += 1
        if abs(b.x - PLUNGER_X) > 1e-9:   # 竖井内不该有任何横向位移
            bad_x += 1
        if not (home and b.y == PLUNGER_Y and b.vy == 0.0):
            bad_home += 1
    mf_ok = (bad_apex == 0 and bad_x == 0 and bad_home == 0
             and frames_max <= MISFIRE_MAX_FRAMES)
    ok = ok and mf_ok
    print("  apex y 区间 %.0f~%.0f (隔墙顶 %d, 越过即失败)   最长归位 %d/%d 帧"
          % (apex_hi_y, apex_lo_y, LANE_WALL_TOP, frames_max, MISFIRE_MAX_FRAMES))
    print("  越顶泄漏: %d/%d   横向漂移: %d/%d   未归位: %d/%d"
          % (bad_apex, mf, bad_x, mf, bad_home, mf))

    # (2b') 下落节奏门禁: 弹珠机手感 —— 碰钉要有可见减速, 球在钉阵里慢慢滚落。
    #       历史教训: G=1200/VY_MIN=200 时球"嗖嗖穿过"钉阵(行穿行 0.17s, 碰钉不减
    #       反加速), 玩家投诉"下落加速极快"。G=1000/E_SLOW=0.55/VY_MIN=100 后
    #       行穿行 0.22s、碰钉减速比 0.83。
    print("== 下落节奏(弹珠机手感: 碰钉轻快弹开) ==")
    mn = 250
    row_gaps = []
    peg_ratios = []
    sticky = 0
    # 口径与专家组测量一致: power=0.8 固定 + 每发固定 rng 种子。
    # 滞留帧=碰钉间隔恰 1 帧(同钉连续碰撞)。
    for i in range(mn):
        b = launch_ball(0.8, rng=random.Random(1000 + i))
        prev_y = b.y
        row_t = {}
        t = 0.0
        last_peg_f = -10
        prev_sp = None
        for _f in range(4000):
            sp0 = math.hypot(b.vx, b.vy)
            landed = advance_flight(b, geo)
            t += FIXED_DT
            if b.events & EV_PEG:
                if _f - last_peg_f == 1:
                    sticky += 1              # 同钉连续碰撞(1 帧内再次碰同一颗钉)
                else:
                    if prev_sp is not None and prev_sp > 50:
                        peg_ratios.append(math.hypot(b.vx, b.vy) / prev_sp)  # 总速比
                last_peg_f = _f
                b.events = 0
                b.amp.clear()
            elif b.events:
                b.events = 0
                b.amp.clear()
            prev_sp = sp0
            for r in range(1, PEG_ROWS):      # 行穿行: 相邻钉行间的下落耗时
                y = PEG_TOP + r * PEG_SY
                if y not in row_t and b.vy > 0 and prev_y < y <= b.y:
                    row_t[y] = t
            prev_y = b.y
            if landed is not None:
                break
        rows = sorted(row_t)
        for a, c in zip(rows, rows[1:]):
            row_gaps.append(row_t[c] - row_t[a])
    row_gaps.sort()
    peg_ratios.sort()
    gap_med = row_gaps[len(row_gaps) // 2]
    ratio_med = peg_ratios[len(peg_ratios) // 2]
    # 减速比门禁区间 0.45~0.80: 低于 0.45 黏滞(VMIN=30 实测 0.32 被投诉); 上限 0.80
    # (用户接受 0.77 轻快弹开; 0.70 过严会逼出穿阵手感)。
    # 行穿行 0.15 只是极端哨兵(防整体过快)。
    # 滞留帧门禁 ≤30/发: 摩擦后球到槽口慢是"损失能量"的一致表现(实测 ~26);
    # 30 防"卡在槽口"(黏滞类异常)。
    rhythm_ok = (gap_med >= 0.15 and 0.45 <= ratio_med <= 0.80
                 and sticky <= mn * 30)
    ok = ok and rhythm_ok
    print("  行穿行 p50=%.2fs (须>=0.15)   碰钉减速比 p50=%.2f (须0.45~0.80, 轻快弹开)"
          % (gap_med, ratio_med))
    print("  滞留帧 %d (须<=%d/发, 摩擦后槽口慢速落袋为正常)" % (sticky, 30))

    # (2b''') 转向平滑门禁: 碰弧面是唯一大转向, 必须"滑过导轨逐渐转向"而非一帧突变横移。
    #        玩家投诉"刚开始就突然横向移动"; P5 缓动带球后突变 39°→8°。
    print("== 转向平滑(弧面缓动带球) ==")
    ts = 100
    arc_turns = []
    for _ in range(ts):
        b = launch_ball(random.uniform(MISFIRE_POWER, 1.0))
        for _f in range(400):
            a0 = math.degrees(math.atan2(b.vy, b.vx))
            landed = advance_flight(b, geo)
            if b.events & EV_ARC:
                a1 = math.degrees(math.atan2(b.vy, b.vx))
                da = abs(a1 - a0)
                if da > 180:
                    da = 360 - da
                arc_turns.append(da)
                b.events = 0
                b.amp.clear()
            elif b.events:
                b.events = 0
                b.amp.clear()
            if landed is not None:
                break
    arc_turns.sort()
    turn_med = arc_turns[len(arc_turns) // 2]
    arc_ok = turn_med <= 15.0
    ok = ok and arc_ok
    print("  碰弧面帧方向角突变 p50=%.0f° (须<=15, 一帧横移=玩家投诉的荒谬感)" % turn_med)

    # (2c) 蓄力观感区分度: 蓄力必须可见地改变冲顶位置/穿钉路径, 同时竖直时序一帧都不能动
    #      (首钉时刻是 FLIGHT_ENV 那条 1.5s 预烘飞行音的对齐锚点, 漂了音画就脱节)
    print("== 蓄力观感区分度(竖直时序必须不变) ==")
    apexx_med = {}
    fp_x_med = {}
    turny_med = {}
    kink_max = {}
    kink_delta = {}
    fp_bad = []
    turn_bad = []
    for power in (MISFIRE_POWER, 0.5, 1.0):
        axs, npegs, fps = [], [], []
        fpxs = []
        turns, turn_ys, kinks, kdeltas = [], [], [], []
        for k in range(100):
            b = launch_ball(power)
            best_y, best_x = b.y, b.x
            npeg, fp = 0, -1
            fp_x = 0
            crossed = False
            turn = -1
            mk = 0.0
            mkd = 0.0
            prev_da = None
            pvx, pvy = b.vx, b.vy
            for f in range(4000):
                landed = advance_flight(b, geo)
                if b.y < best_y:
                    best_y, best_x = b.y, b.x
                if not crossed and b.x < FIELD_R and b.y < LANE_WALL_TOP:
                    crossed = True
                if turn < 0 and crossed and b.vy >= 0.0:
                    turn = f                   # 顶部碰撞音的触发帧(GUI 用同一判据)
                    turn_ys.append(b.y)
                if b.events & EV_PEG:
                    npeg += 1
                    if fp < 0:
                        fp = f
                        fp_x = b.x            # 首钉 x(玩家看到的"进钉阵位置")
                # 空中折角(均匀平滑门禁): 没有任何碰撞(含弧面接触 EV_ARC)的那一帧里方向
                # 变了多少。低速段方向本就抖(vx 过零即 180°), 所以只看 |v|>300 的帧。
                # 空间限定: 只统计球心在首钉平面(y=141)之上的飞行段 —— 碰第一个钉子
                # 之前才是"均匀平滑"的主战场; 钉阵内被 STEER 拉向目标槽是正常引导。
                if (not b.events) and b.y < PEG_TOP - BALL_R and math.hypot(b.vx, b.vy) > 300.0:
                    da = abs(math.degrees(math.atan2(b.vy, b.vx) -
                                          math.atan2(pvy, pvx)))
                    if da > 180.0:
                        da = 360.0 - da
                    if da > mk:
                        mk = da
                    if prev_da is not None:
                        dd = abs(da - prev_da)
                        if dd > mkd:
                            mkd = dd
                    prev_da = da
                else:
                    prev_da = None          # 碰撞帧(弧面/钉/墙)打断 da 序列, 碰后重新开始:
                                            # Δ 只在连续无碰撞帧之间比较, 不跨碰撞
                pvx, pvy = b.vx, b.vy
                b.events = 0
                b.amp.clear()
                if landed is not None:
                    break
            axs.append(best_x)
            npegs.append(npeg)
            kinks.append(mk)
            kdeltas.append(mkd)
            if fp >= 0:
                fps.append(fp)
                fpxs.append(fp_x)
            if turn >= 0:
                turns.append(turn)
        axs.sort(); npegs.sort(); fps.sort(); turns.sort(); turn_ys.sort()
        fpxs.sort()
        kinks.sort(); kdeltas.sort()
        apexx_med[power] = axs[len(axs) // 2]
        fp_x_med[power] = fpxs[len(fpxs) // 2] if fpxs else 0
        kink_max[power] = max(kinks)
        kink_delta[power] = max(kdeltas)
        fp_med = fps[len(fps) // 2] if fps else -1
        if not (75 <= fp_med <= 110):
            fp_bad.append((power, fp_med))
        turn_med = turns[len(turns) // 2] if turns else -1
        turn_y_med = turn_ys[len(turn_ys) // 2] if turn_ys else -1
        turn_covered = len(turns) == 100        # 顶部碰撞音必须每发都触发
        if not (50 <= turn_med <= 65) or not turn_covered:
            turn_bad.append((power, turn_med, len(turns)))
        turny_med[power] = turn_y_med
        print("  力度 %3.0f%% (u=%.2f): 冲顶 x 中位 %3.0f   撞钉 %d 次   首钉 %d 帧   "
              "转向 %d 帧 @y%.0f   空中折角 %.1f°(Δ%.1f°)"
              % (power * 100, power_u(power), apexx_med[power],
                 npegs[len(npegs) // 2], fp_med, turn_med, turn_y_med,
                 kink_max[power], kink_delta[power]))
    spread = apexx_med[MISFIRE_POWER] - apexx_med[1.0]
    # 35° 出口角下冲顶 x 对速度不敏感(顶点 x 差小), 但首钉 x(进钉阵位置)区分度大
    # —— 玩家看到的是首钉位置差异, 门禁用首钉 x 跨度(>=45px, 实测 ~50)。
    fpx_spread = fp_x_med[MISFIRE_POWER] - fp_x_med[1.0]
    spread_ok = fpx_spread >= 45.0
    tspread = turny_med[MISFIRE_POWER] - turny_med[1.0]
    tspread_ok = tspread >= 8.0
    kink_worst = max(kink_max.values())
    kink_delta_worst = max(kink_delta.values())
    kink_ok = kink_worst <= 4.0 and kink_delta_worst <= 1.0
    ok = ok and spread_ok and not fp_bad and not turn_bad and tspread_ok and kink_ok
    print("  首钉 x 跨度(弱→满): %.0f px  %s (>=45 玩家看得出力度差异; 冲顶 x 跨度 %.0f px)"
          % (fpx_spread, "OK" if spread_ok else "区分度不足!", spread))
    print("  首钉时刻: %s (须恒在 75~110 帧, 否则飞行音与画面脱节)"
          % ("OK" if not fp_bad else "漂了! %s" % fp_bad))
    print("  转向(顶部碰撞音触发): %s (须每发都有且恒在 50~65 帧)  转向高度跨度 %.0f px %s"
          % ("OK" if not turn_bad else "异常! %s" % turn_bad,
             tspread, "OK" if tspread_ok else "(<8 顶部音分不出蓄力档!)"))
    print("  空中折角(无碰撞段, 弧面接触帧豁免): 单帧最大 %.1f°  %s (须 <=4;"
          % (kink_worst, "OK" if kink_ok else "不达标!"))
    print("           相邻帧折角差最大 %.1f°  %s (须 <=1; 均匀平滑, 无阶跃)"
          % (kink_delta_worst, "OK" if kink_ok else "不达标!"))

    # (3) 碰撞事件覆盖率: 该响的地方有没有事件位(历史 bug: 撞钉位从未置位 -> 全程静音)
    print("== 碰撞事件覆盖率(音效触发源) ==")
    names = {EV_PEG: "撞钉", EV_CEIL: "天花板", EV_WALL: "撞墙", EV_DIV: "撞隔板",
             EV_ARC: "导流弧"}
    for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
        print("  %-8s 有事件 %5.1f%%   过音量阈值 %5.1f%%" %
              (names[bit], 100.0 * ev_flights[bit] / m, 100.0 * ev_audible[bit] / m))
    print("  导流弧   接触率 %5.1f%%   (静音事件位: 每次有效发射都必须接触弧面,"
          % (100.0 * ev_flights[EV_ARC] / m))
    print("           这是'转向都发生在导流槽上'的量化——没经过导流槽就转向 = 违规)")
    peg_rate = 100.0 * ev_audible[EV_PEG] / m
    ev_ok = peg_rate > 90.0                 # 撞钉是下落段的主音效, 必须几乎每发都有
    ok = ok and ev_ok
    if not ev_ok:
        print("  异常: 撞钉音效触发率 %.1f%% < 90%%, 玩家会觉得没声音" % peg_rate)
    arc_rate = 100.0 * ev_flights[EV_ARC] / m
    arc_ok = arc_rate >= 99.0               # 弧面接触率: 三段式轨道的第二段, 必须每发都走
    ok = ok and arc_ok
    if not arc_ok:
        print("  异常: 导流弧接触率 %.1f%% < 99%%, 存在'没经过导流槽就转向'的飞行" % arc_rate)
    ceil_rate = 100.0 * ev_flights[EV_CEIL] / m
    ceil_ok = ceil_rate < 10.0              # 真顶墙: 三段式下 apex 最低 y=57, 不撞顶, 恒0
    ok = ok and ceil_ok                     # 留着当哨兵: 一旦弧面把球反射向顶部, 立刻报警
    if not ceil_ok:
        print("  异常: 天花板撞击率 %.1f%% >= 10%%, 弧面把球反射向顶了? 见 build_deflectors 注释"
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
# 布局: 5 行全宽上下结构(上设定/下信息, 无右侧面板, 无历史行) —
#   [顶栏] 标题+喇叭图标+状态  [返还] RTP三档左对齐  [投入] 弹珠单位左对齐
#   [游戏区 全宽]
#   [信息] 弹珠 + 每次投x珠,累计x投x中(x%)  [底行] 重置 —长距离— 力度+蓄力发射
# 字体层级体系(6 级, 对齐 PC 版比例关系; 手机端基准正文 14sp):
#   Hero 48sp  中奖金额大字(未中 36sp) — 按屏宽占比设计, 不跟场景缩
#   Key  18sp  弹珠金色主数字 + 顶栏标题
#   Act  16sp  全部按钮(发射/投入/重置/RTP)
#   Body 14sp  正文标签/统计/力度
#   Aux  13sp  状态栏/"投入弹珠单位"/"返还率"
# 场景内文字(槽位倍率/落袋浮字)不用 sp, 用逻辑 px 跟盘面一起缩放: 逻辑 20px(手机上≈11sp)。
H_TOP = 44                   # 顶栏(标题+喇叭+状态)
H_RTP = 44                   # 返还率行(左对齐, 降低以增大游戏区间隙)
H_BETS = 44                  # 投入弹珠单位行(左对齐, 降低以增大游戏区间隙)
H_INFO = 26                  # 弹珠 + 统计(缩高, 腾空间给底部留白)
H_BOTTOM = 64                # 重置 + 力度 + 蓄力发射
BALL_VIEW = 1.4              # 小球视觉放大倍数(仅渲染; 碰撞半径 BALL_R 是物理常量不能动)


def slot_color(m):
    if m <= 0:
        return "#2a3550"
    if m <= 2:
        return "#1e8a5a"
    if m <= 3:
        return "#3d8bfd"
    if m <= 5:
        return "#e0533b"
    if m <= 10:
        return "#a335ee"
    return "#c88800"


_BALL_TEX = None


def ball_texture():
    """程序化径向渐变小球贴图(对应 tkinter 版 PIL 渐变, 纯 Python 生成, 零依赖)。"""
    global _BALL_TEX
    if _BALL_TEX is not None:
        return _BALL_TEX
    d = 64
    r = d / 2.0
    stops = [
        (0.00, (254, 240, 138)), (0.20, (250, 220, 80)), (0.40, (234, 179, 8)),
        (0.65, (202, 138, 4)), (0.85, (160, 100, 10)), (0.94, (120, 65, 10)),
        (0.99, (50, 25, 5)),
    ]
    buf = bytearray(d * d * 4)
    for y in range(d):
        for x in range(d):
            dx = x - r + 0.5
            dy = y - r + 0.5
            dist = math.hypot(dx, dy) / (r - 0.5)
            if dist >= 1.0:
                continue
            rr, gg, bb = stops[-1][1]
            for j in range(len(stops) - 1):
                if stops[j][0] <= dist <= stops[j + 1][0]:
                    s0, c0 = stops[j]
                    s1, c1 = stops[j + 1]
                    f = (dist - s0) / (s1 - s0) if s1 > s0 else 0
                    rr = int(c0[0] + (c1[0] - c0[0]) * f)
                    gg = int(c0[1] + (c1[1] - c0[1]) * f)
                    bb = int(c0[2] + (c1[2] - c0[2]) * f)
                    break
            alpha = 255
            if dist > 0.97:                      # 边缘抗锯齿
                alpha = int(255 * (1.0 - dist) / 0.03)
            i = (y * d + x) * 4
            buf[i] = rr
            buf[i + 1] = gg
            buf[i + 2] = bb
            buf[i + 3] = alpha
    # 猫眼色带(旋转可见): 焦糖色眼睛形带, 深色带形成明暗对比
    # 1) 猫眼色带(焦糖, 眼睛形, 偏离圆心): 深色带形成明暗对比, 旋转可见
    ba = math.radians(-32.0)
    off = 0.08 * d                # 中心线偏离圆心(偏右下, 与左上高光错开)
    band_w = 0.11 * d             # 中部半宽
    band_c = (178, 108, 22)       # 焦糖色
    strength = 0.50               # 最大混入强度(变暗五成)
    cos_a, sin_a = math.cos(ba), math.sin(ba)
    for y in range(d):
        for x in range(d):
            i = (y * d + x) * 4
            if buf[i + 3] == 0:   # 跳过透明像素, 防边缘渗色
                continue
            dx = x - r
            dy = y - r
            s = dx * cos_a + dy * sin_a          # 沿带方向(-r..r)
            v = -dx * sin_a + dy * cos_a         # 垂直带方向
            if abs(s) < r:
                wmax = band_w * math.sqrt(1.0 - (s / r) ** 2)   # 眼睛形: 中间宽两端尖
                dv = abs(v - off)
                if dv < wmax:
                    t = dv / wmax
                    w = (1.0 - t * t) ** 2 * strength
                    buf[i] = int(buf[i] + (band_c[0] - buf[i]) * w)
                    buf[i + 1] = int(buf[i + 1] + (band_c[1] - buf[i + 1]) * w)
                    buf[i + 2] = int(buf[i + 2] + (band_c[2] - buf[i + 2]) * w)
    # [高光已删] 用户要求去掉高光, 只保留焦糖色带(球身径向渐变已够立体)
    tex = Texture.create(size=(d, d), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _BALL_TEX = tex
    return tex


def _vibrate(ms):
    """中奖震动(仅 Android; 其它平台静默)。需要 buildozer.spec 的 VIBRATE 权限。
    取服务必须用 Context.VIBRATOR_SERVICE 字符串 —— 传 autoclass("android.os.Vibrator")
    那个 Class 对象在 pyjnius 下匹配不到 getSystemService(Class<T>) 重载, 会静默失败
    (整段被 try/except 吞掉, 表现为"权限也给了、代码也跑了, 就是不震")。"""
    if platform != "android":
        return
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        vib = activity.getSystemService(Context.VIBRATOR_SERVICE)
        if vib is None:
            return
        try:
            VibrationEffect = autoclass("android.os.VibrationEffect")
            vib.vibrate(VibrationEffect.createOneShot(
                ms, 255))     # 最大振幅; DEFAULT_AMPLITUDE(-1) 约 50%, 太弱
        except Exception:
            vib.vibrate(ms)                  # API < 26: 没有 VibrationEffect
    except Exception:
        pass


def number_voice_names(n):
    """整数 → 中文朗读的语音名列表(队列拼接用, 对标 Clac 项目方案)。
    1250 → ['voice_d_1','voice_u_1000','voice_d_2','voice_u_100','voice_d_5','voice_u_10']
    20   → ['voice_d_2','voice_u_10']
    2000 → ['voice_liang','voice_u_1000']"""
    if n == 0:
        return ["voice_d_0"]
    names = []
    wan = n // 10000
    rest = n % 10000
    if wan > 0:
        names.extend(_read_4digits(wan, is_highest=(wan == n // 10000)))
        names.append("voice_u_10000")
    names.extend(_read_4digits(rest, is_highest=(wan == 0)))
    return names or ["voice_d_0"]


def _read_4digits(n, is_highest=True):
    """朗读 0~9999, 返回语音名列表。二/两规则: 千位的 2 读"两"。"""
    if n == 0:
        return []
    qian, rem = divmod(n, 1000)
    bai, rem = divmod(rem, 100)
    shi, ge = divmod(rem, 10)
    parts = []
    need_zero = False
    if qian > 0:
        parts.append("voice_liang" if qian == 2 else "voice_d_%d" % qian)
        parts.append("voice_u_1000")
    else:
        need_zero = is_highest is False
    if bai > 0:
        if need_zero and not parts:
            pass
        elif need_zero and parts:
            parts.append("voice_d_0")
        parts.append("voice_d_%d" % bai)
        parts.append("voice_u_100")
    elif qian > 0:
        need_zero = True
    if shi > 0:
        if need_zero and parts:
            parts.append("voice_d_0")
        if shi == 1 and not parts:
            parts.append("voice_u_10")          # 10~19: "十" 不读 "一十"
        else:
            parts.append("voice_d_%d" % shi)
            parts.append("voice_u_10")
    elif bai > 0 and ge > 0:
        parts.append("voice_d_0")
    if ge > 0:
        if shi == 0 and (qian > 0 or bai > 0) and parts:
            parts.append("voice_d_0")
        parts.append("voice_d_%d" % ge)
    return parts


class GameArea(FloatLayout):
    """520x660 逻辑场景(坐标系沿用 tkinter 版: y 向下), 绘制时等比缩放居中。
    静态元素(墙/钉/槽/弧)重绘只在尺寸变化或换盘面时; 球/力度条/柱塞每帧只改 pos;
    特效(浮字/中奖大字)是 FloatLayout 子 Label, 每帧在 tick_draw 里驱动。"""

    def __init__(self, game, **kw):
        super().__init__(**kw)
        self.game = game
        self._s = 1.0
        self._ox = 0.0
        self._oyt = 0.0
        self._slot_cols = []
        self._lamp_cols = []
        self._peg_cols = {}            # (px,py)→Color 钉子受击高亮
        self._peg_ellipses = {}        # (px,py)→Ellipse 钉子半径形变
        self._peg_flash = {}           # (px,py)→born_time 动画计时
        self._ball_e = None
        self._meter_fill = None
        self._meter_col = None
        self._spring_bars = []
        self._spring_power = 0.0           # 弹簧显示用力度(平滑衰减)
        self._spring_vel = 0.0             # 弹簧回弹速度(阻尼振荡用)
        self._spring_bar_col = None
        self._pulse = None            # (槽号, 结束时刻)
        self._effects = []            # 浮字/中奖大字
        self._last_size = None        # 上次尺寸: 变了才清特效
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
        size_changed = (self.width, self.height) != self._last_size
        self._last_size = (self.width, self.height)
        self._s = s
        self._ox = self.x + (self.width - CW * s) / 2.0
        self._oyt = self.y + (self.height + CH * s) / 2.0
        g = self.game
        self._pulse = None
        # 仅尺寸真变了才清特效(否则 park_ball 重掷盘面会把中奖大字一起杀了)
        if size_changed:
            for e in self._effects:
                for w in e["ws"]:
                    self.remove_widget(w)
            self._effects = []
        self.canvas.clear()
        with self.canvas:
            Color(*hex_rgb(COL_CANVAS))
            Rectangle(pos=self.pos, size=self.size)
            Color(*hex_rgb(COL_LANE))
            Rectangle(**self._rect(LANE_L, 0, RIGHT_INNER, FLOOR))
            Color(*hex_rgb(COL_WALL))
            for w in g.geo["walls"]:
                Rectangle(**self._rect(*w))
            # 发射区导流弧(3~4px 金属细带, 右壁口部弧形导轨, 比钉略细但可见)
            if g.geo["deflectors"]:
                pts = []
                for (x1, y1, x2, y2) in g.geo["deflectors"]:
                    pts.extend([self._px(x1), self._py(y1)])
                x1, y1, x2, y2 = g.geo["deflectors"][-1]
                pts.extend([self._px(x2), self._py(y2)])
                Color(*hex_rgb(COL_WALL))
                Line(points=pts, width=max(1.0, 3.5 * s), cap="round", joint="round")
            # 钉阵(每颗独立 Color+Ellipse, 支持单颗受击高亮/形变)
            self._peg_cols.clear()
            self._peg_ellipses.clear()
            for px, py in g.geo["pegs"]:
                col = Color(*hex_rgb(COL_PEG))
                e = Ellipse(**self._circle(px, py, PEG_R))
                self._peg_cols[(px, py)] = col
                self._peg_ellipses[(px, py)] = e
            # 槽隔板
            Color(*hex_rgb(COL_BUMPER))
            for d in g.geo["dividers"]:
                Rectangle(**self._rect(*d))
            # 倍率槽(圆角, 颜色随盘面)
            self._slot_cols = []
            for i in range(NUM_SLOTS):
                col = Color(*hex_rgb(slot_color(g.multipliers[i])))
                self._slot_cols.append(col)
                RoundedRectangle(radius=[max(1.0, 6 * s)],
                                 **self._rect(FIELD_L + i * SLOT_W + 2, SLOT_TOP + 3,
                                              FIELD_L + (i + 1) * SLOT_W - 2, FLOOR - 3))
            # 槽倍率文字(CoreLabel 烘成纹理; 逻辑 20px 跟盘面缩放, 手机上≈11sp)
            fs = max(12, int(20 * s))
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
            # 弹簧凹槽(发射槽下方暗色井区, 跟随 PLUNGER_Y; 从球底延伸到画布底)
            Color(*hex_rgb("#060e18"))
            Rectangle(**self._rect(LANE_L, PLUNGER_Y + BALL_R, RIGHT_INNER, CH))
            # 弹簧: 2 条横线(在凹槽内, 间距=松弛, 贴紧=压缩)
            self._spring_bar_col = Color(*hex_rgb("#8fa0c4"))
            self._spring_bars = []
            for _ in range(3):
                self._spring_bars.append(
                    Line(points=[0, 0, 0, 0], width=max(0.8, 1.5 * s),
                         cap="round"))
            # 球(动态, 程序化渐变贴图; 视觉 BALL_VIEW 倍放大, 碰撞半径不变)
            Color(1, 1, 1)
            self._ball_push = PushMatrix()
            self._ball_rot = Rotate(angle=0.0, origin=(0, 0))
            self._ball_e = Rectangle(texture=ball_texture(), pos=(0, 0),
                                     size=(2 * BALL_R * BALL_VIEW * s,
                                           2 * BALL_R * BALL_VIEW * s))
            self._ball_pop = PopMatrix()
        self.tick_draw()

    # ------------------------------ 特效 ------------------------------
    def big_result_text(self, m, payout):
        """画布中央中奖大字: 缩放+淡出+上浮(对应 tkinter _big_result_text)。
        Hero 层级按屏宽占比设计(48sp), 不跟场景缩 — 手机上画布=整块屏, 跟场景缩就太小了。
        字号**必须过 sp()**: Label(font_size=48) 是裸物理像素, 桌面 density=1 时正好 48sp
        看着对, 手机 density 2.5~3 时只剩 16~19sp, 比旁边 18sp 的余额数字还小(实测反馈
        "远远没有 PC 上大")。UI 其余文字都是 "18sp" 这种带单位字符串, 只有这里漏了。"""
        if self._ball_e is None:
            return
        if m > 0:
            text = "+%d" % payout
            if m <= 2:       hexcolor = COL_GREEN
            elif m <= 3:     hexcolor = "#3d8bfd"   # 蓝
            elif m <= 5:     hexcolor = COL_FIRE    # 红
            elif m <= 10:    hexcolor = "#a335ee"   # 紫
            else:            hexcolor = COL_METER   # 金
            size = sp(48)
        else:
            text = "未中"
            hexcolor = COL_FIRE
            size = sp(36)
        main = Label(text=text, font_size=size, bold=True,
                     color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
        main.bind(size=lambda w, _: setattr(w, "text_size", w.size))
        shadow = Label(text=text, font_size=size, bold=True,
                       color=(0, 0, 0, 0.6), size_hint=(None, None))
        shadow.bind(size=lambda w, _: setattr(w, "text_size", w.size))
        self.add_widget(shadow)
        self.add_widget(main)
        self._effects.append({"kind": "big", "ws": [main, shadow], "born": time.time(),
                              "life": 3.0, "size": size, "rgb": hex_rgb(hexcolor),
                              "cx": self._px(CW / 2.0) - self.x,
                              "cy": self._py(CH / 2.0 - 80) - self.y})

    def pulse_slot(self, i):
        if 0 <= i < len(self._slot_cols):
            self._pulse = (i, time.time() + 0.30)

    def center_toast(self, text, hexcolor=COL_FIRE, size=26, life=2.0):
        """画布中央两行警示飘字(如余额不足): 上浮+淡出。size 单位是 sp(见 big_result_text)。
        老 toast 未消失前不再弹新的(连点发射会瞬间叠一排); 创建即摆位到中央 ——
        默认 pos=(0,0) 是 GameArea 左下角(=重置按钮附近), 下一帧才被 tick_draw 摆位,
        连点时会在那里闪出"第二个提示"。"""
        if self._ball_e is None:
            return
        for e in self._effects:
            if e["kind"] == "toast":
                return                            # 已有 toast 存活, 不重复弹
        lbl = Label(text=text, font_size=sp(size), bold=True, halign="center",
                    color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
        lbl.texture_update()                      # 立刻出纹理, 尺寸跟文字(center 才摆得准)
        lbl.size = lbl.texture_size
        cx = self._px(CW / 2.0) - self.x
        cy = self._py(CH / 2.0 - 40) - self.y
        lbl.center = (cx, cy)
        self.add_widget(lbl)
        self._effects.append({"kind": "toast", "ws": [lbl], "born": time.time(),
                              "life": life, "rgb": hex_rgb(hexcolor),
                              "cx": cx, "cy": cy})

    def set_lamp(self, i, hex_color):
        if 0 <= i < len(self._lamp_cols):
            self._lamp_cols[i].rgb = hex_rgb(hex_color)

    def lamps_off(self):
        for col in self._lamp_cols:
            col.rgb = hex_rgb(COL_LAMP_OFF)

    # ------------------------------ 帧驱动 ------------------------------
    def tick_draw(self):
        """每帧只更新动态元素(ball/meter/plunger) + 特效, 不重排 canvas。"""
        if self._ball_e is None:
            return
        g = self.game
        now = time.time()
        b = g.ball
        if b is not None:
            br = BALL_R * BALL_VIEW
            bs = 2 * br * self._s
            # 受击压扁(借鉴经典版): 沿法线缩、切向胀, 渐回正圆(2~3帧≈50ms 硬钢感)
            sq = getattr(b, "squash", 1.0)
            if sq < 0.99:
                b.squash += (1.0 - sq) * 0.5
                if b.squash > 0.99:
                    b.squash = 1.0
                sq = b.squash
            self._ball_e.pos = (self._ox + (b.x - br) * self._s + bs * (1 - sq) * 0.5,
                                self._oyt - (b.y + br) * self._s + bs * (1 - sq) * 0.5)
            self._ball_e.size = (bs * (2 - sq), bs * sq)
            # 球自转(让横滑看起来是"滚动"而非"悬浮"): spin 是碰钉切向速度积分, 之前算了没画
            self._ball_rot.angle = math.degrees(getattr(b, "spin", 0.0) % (2.0 * math.pi))
            self._ball_rot.origin = (self._ox + b.x * self._s, self._oyt - b.y * self._s)
            # 钉子受击高亮: 消费物理层 peg_flash 信号(每次碰撞都置, 此处清空)
            pf = getattr(b, "peg_flash", None)
            if pf is not None and pf in self._peg_cols:
                self._peg_flash[pf] = now
                b.peg_flash = None
        # 钉子高亮动画: 60ms 电光金 + 240ms 渐回原色 + 半径微扩 1.2x(经典版 30+120 太短, 加长一倍)
        for (px, py), t0 in list(self._peg_flash.items()):
            col = self._peg_cols.get((px, py))
            e = self._peg_ellipses.get((px, py))
            if col is None:
                del self._peg_flash[(px, py)]
                continue
            elapsed = now - t0
            if elapsed > 0.30:
                col.rgb = hex_rgb(COL_PEG)
                if e is not None:
                    r = PEG_R * self._s
                    e.size = (2 * r, 2 * r)
                del self._peg_flash[(px, py)]
            else:
                flash = (1.0, 0.898, 0.0)     # 电光金 #ffe500
                base = hex_rgb(COL_PEG)
                if elapsed < 0.06:
                    col.rgb = flash
                else:
                    f = (elapsed - 0.06) / 0.24
                    col.rgb = (flash[0] + (base[0] - flash[0]) * f,
                               flash[1] + (base[1] - flash[1]) * f,
                               flash[2] + (base[2] - flash[2]) * f)
                scale = 1.0 if elapsed < 0.06 else (1.2 - 0.2 * (elapsed - 0.06) / 0.24)
                if e is not None:
                    r = PEG_R * self._s * scale
                    e.size = (2 * r, 2 * r)
        if g.power > 0.01:
            top = (SLOT_TOP - 8) - g.power * 200
            kw = self._rect(RIGHT_INNER - 9, top, RIGHT_INNER - 4, SLOT_TOP - 8)
            self._meter_fill.pos = kw["pos"]
            self._meter_fill.size = kw["size"]
            weak = g.power < MISFIRE_POWER
            self._meter_col.rgb = hex_rgb(COL_FIRE if weak else COL_METER)
        else:
            self._meter_fill.size = (0, 0)
        # 弹簧: 蓄力时跟随, 释放后阻尼振荡回弹(过冲→往复→停止)
        if g.state == "charging":
            self._spring_power = g.power
            self._spring_vel = 0.0
        elif abs(self._spring_power) > 0.0005 or abs(self._spring_vel) > 0.005:
            k, damp = 120.0, 3.2
            self._spring_vel += (-k * self._spring_power - damp * self._spring_vel) * FIXED_DT
            self._spring_power += self._spring_vel * FIXED_DT
        else:
            self._spring_power = 0.0
            self._spring_vel = 0.0
        sp = max(-0.25, self._spring_power)  # 过冲到 -0.25(回弹约 11px), 视觉明显
        # 弹簧 Z 字形: 上横线→斜线→下横线
        bar_top = PLUNGER_Y + BALL_R
        bar_bot = bar_top + 9 + sp * 45
        lx = self._px(LANE_L + 5)
        rx = self._px(RIGHT_INNER - 5)
        y0 = self._py(bar_top)
        y1 = self._py(bar_bot)
        bars = self._spring_bars
        bars[0].points = [lx, y0, rx, y0]
        bars[1].points = [rx, y0, lx, y1]
        bars[2].points = [lx, y1, rx, y1]
        # 弹簧颜色: 哑火→红, 正常蓄力→灰蓝渐变至金黄(10档颗粒度)
        weak = sp < MISFIRE_POWER
        if sp < 0.01:
            self._spring_bar_col.rgb = hex_rgb("#8fa0c4")
        elif weak:
            self._spring_bar_col.rgb = hex_rgb("#c45a4a")
        else:
            u = power_u(sp)
            r0, g0, b0 = 0x8f, 0xa0, 0xc4
            r1, g1, b1 = 0xff, 0xd7, 0x00
            r = int(r0 + (r1 - r0) * u)
            g = int(g0 + (g1 - g0) * u)
            b = int(b0 + (b1 - b0) * u)
            self._spring_bar_col.rgb = (r / 255.0, g / 255.0, b / 255.0)
        # 槽位白闪
        if self._pulse is not None:
            i, end = self._pulse
            if now >= end or i >= len(self._slot_cols):
                if i < len(self._slot_cols):
                    self._slot_cols[i].rgb = hex_rgb(slot_color(g.multipliers[i]))
                self._pulse = None
            else:
                self._slot_cols[i].rgb = (1, 1, 1)
        # 特效推进
        for e in list(self._effects):
            p = (now - e["born"]) / e["life"]
            if p >= 1.0:
                for w in e["ws"]:
                    self.remove_widget(w)
                self._effects.remove(e)
                continue
            if e["kind"] == "toast":
                alpha = max(0.0, 1.0 - max(0.0, p - 0.65) / 0.35)   # 前 65% 实色, 后 35% 淡出
                w = e["ws"][0]
                w.color = e["rgb"] + (alpha,)
                w.center = (e["cx"], e["cy"] + 20 * (now - e["born"]))
            else:
                if p < 0.5:
                    sc = 1.0 + (p / 0.5) * 0.2       # 前50%生命(1.5s): 1.0→1.2 弹入
                else:
                    sc = 1.2 - ((p - 0.5) / 0.5) * 0.2  # 后50%: 1.2→1.0 慢收
                alpha = max(0.0, 1.0 - max(0.0, p - 0.55) / 0.45)
                fs = max(8, int(e["size"] * sc))
                rise = 38 * (now - e["born"])
                main, shadow = e["ws"]
                if fs != e.get("_last_fs", 0):     # 仅值变了才写 font_size, 跳过冗余纹理重建
                    main.font_size = fs
                    shadow.font_size = fs
                    e["_last_fs"] = fs
                main.color = e["rgb"] + (alpha,)
                shadow.color = (0, 0, 0, alpha * 0.6)
                main.center = (e["cx"], e["cy"] + rise)
                shadow.center = (e["cx"] + 2, e["cy"] + rise - 2)


class RootWidget(BoxLayout):
    """游戏状态机 + 全部控件。逻辑与 tkinter 版 PlinkoApp 一一对应。"""

    def __init__(self, sfx=None, **kw):
        super().__init__(orientation="vertical", spacing=dp(10), **kw)
        self.sfx = sfx if sfx is not None else Sfx(SOUND_ENABLED)
        self.geo = build_geo()
        self._base_deflectors = list(self.geo["deflectors"])   # 原始弧面(每发射前按 arc_dy 重建)
        self.multipliers = roll_multipliers()
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self._anim_start_time = 0.0
        self._anim_dur = 0.5
        self._coin_until = 0.0
        self._land_hold = LAND_HOLD
        self._result_until = 0.0       # 结算结果窗口: 期内抑制UI语音, 让结果音优先
        self.bet = DEFAULT_BET
        self.state = "ready"          # ready | charging | flying | misfire | landing | landed
        self.power = 0.0
        self._last_charge_sound = 0.0
        self._charge_topped = False
        self._crossed = False
        self._risen = False
        self._topped = False          # 本次飞行是否已播顶部碰撞音
        self._misfire_frames = 0
        self._accumulator = 0.0       # 固定步长累加器(适配任意刷新率)
        self._space_held = False
        self._release_power = None
        self.plays = 0
        self.hits = 0
        self.rtp_target = 0.80
        self.sound_mode = "voice"     # voice(语音已开,默认) | sfx(音效已开) | off(音效已关)
        self.max_plays = 50            # 每轮次数上限
        self.round_plays = 0           # 本轮已玩次数
        self.round_history = []        # 最近完成的轮次记录
        self._load_history()           # 从磁盘恢复(跨启动持久化)
        self._auto_reset_on_start = False
        self._load_config()            # 恢复上次的游戏设定
        self._round_end_shown = False  # 本轮结束弹窗是否已弹出
        self._landing_primed = False   # landing首帧标记(防每帧重置vy)
        self._settle_slot = 0          # 本发物理落格槽(结算延迟到回弹落定后)
        self._settled = False          # 本发是否已结算(防重复)
        self._last_motion = 0.0       # flying 帧内刷新; 卡死兜底看"位置不动"而非发射时长
        self._last_ball_xy = (PLUNGER_X, PLUNGER_Y)
        self.landed_at = 0.0
        self.land_target_x = PLUNGER_X
        self.target_slot = 0
        self.target_x = PLUNGER_X
        self.ball = None
        self._last_win_size = None    # 窗口尺寸轮询快照(bind(size) 对程序启动期的 resize 不可靠)
        self._build_ui()
        if self._auto_reset_on_start:
            self.reset_balance(notify=False)  # 上轮打满被kill: UI就绪后静默重置
        self.set_bet(self.bet, silent=True)
        self.set_rtp(self.rtp_target, silent=True)
        self.park_ball(reroll=False, silent=True)
        Window.bind(on_key_down=self._on_key_down, on_key_up=self._on_key_up)
        Window.bind(on_touch_down=self._on_title_touch_down,
                    on_touch_up=self._on_title_touch_up)
        self._bench_running = False
        # 跑分置灰层: 第2轮物理benchmark时全屏置灰(半透明深色矩形盖住整个界面含游戏区)
        self._bench_dim_shown = False
        with self.canvas.after:
            self._bench_dim_col = Color(0.05, 0.06, 0.09, 0.0)
            self._bench_dim_rect = Rectangle(pos=(0, 0), size=(0, 0))
        self.bind(size=self._relayout_bench_dim, pos=self._relayout_bench_dim)
        Clock.schedule_interval(self._frame, FIXED_DT)

    def _fit_width(self):
        """内容最大宽度 = 让 520:660 场景恰好填满可用高度。
        窄屏(手机竖屏)直接铺满宽度; 宽屏(16:10 桌面)内容列居中、两侧留深色边。
        横屏容错: 宽高比>1.2 时以宽度为限, 防止内容列缩成细条。"""
        self._ui_scale = min(1.0, Window.height / dp(680))
        us = self._ui_scale
        # 缩放后的固定高度(行高+间距), 与 _apply_sizes() 一致
        scaled_fixed = (dp(H_TOP + H_RTP + H_BETS + H_INFO + H_BOTTOM) * us
                        + dp(10) * 5 * us * us + dp(12) * us)  # +底部留白
        avail_h = max(100.0, Window.height - scaled_fixed)
        want = avail_h * (CW / CH) + dp(8)
        if Window.width > Window.height * 1.2:            # 横屏容错
            want = min(want, Window.width * 0.55)
        self.width = min(Window.width, want)
        self._font_scale = min(1.0, self.width / dp(360)) # 宽度缩放因子: 窄屏时字体等比缩小

    # ------------------------------ UI ------------------------------
    def _mk_label(self, text, font_size, hexcolor, halign="left", bold=False, **kw):
        lbl = Label(text=text, font_size=font_size, bold=bold,
                    color=hex_rgb(hexcolor) + (1,), halign=halign, valign="middle", **kw)
        lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        return lbl

    def _mk_button(self, text, cb, bg=COL_BTN_OFF):
        b = Button(text=text, background_normal="", background_down="",
                   background_color=hex_rgb(bg) + (1,), color=(1, 1, 1, 1),
                   font_size="16sp", bold=True)
        if cb is not None:
            b.bind(on_release=cb)
        return b

    def _row_bg(self, row, hexcolor):
        with row.canvas.before:
            Color(*hex_rgb(hexcolor))
            row._bg_rect = Rectangle(pos=row.pos, size=row.size)
        row.bind(pos=lambda w, *_: setattr(w._bg_rect, "pos", w.pos),
                 size=lambda w, *_: setattr(w._bg_rect, "size", w.size))

    def _build_ui(self):
        # 顶栏: [左容器 flex] [标题 固定宽·居中] [右容器 flex]
        # 左右等 flex → 标题严格全栏居中, 与两侧内容长短无关
        top = BoxLayout(size_hint_y=None, height=dp(H_TOP),
                        padding=[dp(10), dp(4), dp(10), dp(4)], spacing=dp(6))
        self._row_top = top
        self._row_bg(top, COL_PANEL)
        left_box = BoxLayout(spacing=dp(6))
        self.mute_btn = self._mk_button("", lambda _b: self.toggle_mute())
        self.mute_btn.size_hint_x = None
        self.mute_btn.width = dp(64)
        self.mute_btn.font_size = "13sp"
        self._refresh_mute_btn()
        left_box.add_widget(self.mute_btn)
        self.round_btn = self._mk_button("每轮%d次" % self.max_plays,
            lambda _b: self._show_round_settings(), bg=COL_GREEN)
        self.round_btn.size_hint_x = None
        self.round_btn.width = dp(72)
        self.round_btn.font_size = "13sp"
        self.round_btn.color = (0, 0, 0, 1)          # 黑字配绿底
        left_box.add_widget(self.round_btn)
        left_box.add_widget(Widget())
        top.add_widget(left_box)
        top.add_widget(Widget(size_hint_x=None, width=dp(36)))  # 标题右移防移动端重叠
        self.title_lbl = self._mk_label("跳跳的弹珠机", "18sp", COL_TEXT,
                                        "center", True, size_hint_x=None, width=dp(112))
        top.add_widget(self.title_lbl)
        right_box = BoxLayout()
        self.status_lbl = self._mk_label("按住蓄力发射", "13sp", COL_SUB, "right", False)
        right_box.add_widget(self.status_lbl)
        top.add_widget(right_box)
        self.add_widget(top)
        # ---- 设定区(左对齐, 不撑满) ----
        # 返还率行: 返还率 + 三档(固定宽)
        rtp = BoxLayout(size_hint_y=None, height=dp(H_RTP),
                        padding=[dp(24), dp(4)], spacing=dp(5))
        self._row_rtp = rtp
        self._rtp_title_lbl = self._mk_label("期望返还比例：", "14sp", COL_TEXT, "left", False,
                                      size_hint_x=None, width=dp(115))
        rtp.add_widget(self._rtp_title_lbl)
        self.rtp_btns = {}
        for label, val in (("80%", 0.80), ("120%", 1.20), ("200%", 2.00), ("300%", 3.00)):
            b = self._mk_button(label, lambda _b, t=val: self.set_rtp(t))
            b.size_hint_x = None
            b.width = dp(56)
            b.size_hint_y = 1.0
            self.rtp_btns[val] = b
            rtp.add_widget(b)
        rtp.add_widget(Widget())   # 右侧留空(和投入弹珠行一致)
        self.add_widget(rtp)
        # 投入行: 投入弹珠单位 + 1/10/50/100(固定宽)
        bets = BoxLayout(size_hint_y=None, height=dp(H_BETS),
                         padding=[dp(24), dp(4)], spacing=dp(5))
        self._row_bets = bets
        self._bet_title_lbl = self._mk_label("每次投入弹珠：", "14sp", COL_TEXT, "left", False,
                                       size_hint_x=None, width=dp(115))
        bets.add_widget(self._bet_title_lbl)
        self.bet_btns = {}
        for v in PRESETS:
            b = self._mk_button(str(v) + "个", lambda _b, x=v: self.set_bet(x))
            b.size_hint_x = None
            b.width = dp(56)
            self.bet_btns[v] = b
            bets.add_widget(b)
        bets.add_widget(Widget())   # 右侧留空
        self.add_widget(bets)
        # 游戏区(全宽)
        self.game_area = GameArea(self)
        self.add_widget(self.game_area)
        # ---- 信息区 ----
        # 信息行: 弹珠(对齐重置按钮左沿 6dp+12dp=18dp) + 累计x投x中(x%)
        info = BoxLayout(size_hint_y=None, height=dp(H_INFO))
        self._row_info = info
        info.add_widget(Widget(size_hint_x=None, width=dp(24)))  # 对齐重置按钮(6+12+6=24)
        self._bead_lbl = self._mk_label("弹珠：", "15sp", COL_TEXT, "left", True,
                                       size_hint_x=None, width=dp(48))
        info.add_widget(self._bead_lbl)
        self.balance_lbl = self._mk_label(str(self.balance), "19sp", COL_BALL,
                                          "left", True, size_hint_x=None, width=dp(80))
        info.add_widget(self.balance_lbl)
        self.stats_lbl = self._mk_label("", "15sp", COL_TEXT, "center", True,
                                        size_hint_x=0.70)
        info.add_widget(self.stats_lbl)
        self.add_widget(info)
        # 底行: [重置 96] —长距离— [力度 100] [蓄力发射 弹性]
        fire = BoxLayout(size_hint_y=None, height=dp(H_BOTTOM),
                         padding=[dp(6), dp(4), dp(12), dp(4)], spacing=dp(6))
        self._row_bottom = fire
        self.reset_btn = self._mk_button("重置", lambda _b: self.reset_balance(), bg="#2a2a35")
        self.reset_btn.size_hint_x = None
        self.reset_btn.width = dp(96)
        self.reset_btn.bind(on_press=lambda _b: setattr(self.reset_btn, "background_color",
            hex_rgb("#4a5a6a") + (1,)),
            on_release=lambda _b: setattr(self.reset_btn, "background_color",
            hex_rgb("#2a2a35") + (1,)))
        fire.add_widget(Widget(size_hint_x=None, width=dp(12)))     # 重置按钮右移
        fire.add_widget(self.reset_btn)
        fire.add_widget(Widget(size_hint_x=0.95))                 # 弹簧(让出少量给右侧)
        self.power_lbl = self._mk_label("", "14sp", COL_METER, "center", True,
                                        size_hint_x=None, width=dp(100))
        fire.add_widget(self.power_lbl)
        self.fire_btn = self._mk_button("蓄力发射", None, bg=COL_FIRE)
        self.fire_btn.size_hint_x = None
        self.fire_btn.width = dp(110)
        self.fire_btn.bind(on_press=lambda _b: self.start_charge(),
                           on_release=lambda _b: self.launch())
        fire.add_widget(self.fire_btn)
        fire.add_widget(Widget(size_hint_x=0.05))                 # 右侧弹簧(蓄力左移≈2dp)
        self.add_widget(fire)
        self.padding = [0, 0, 0, dp(12)]  # 底部留白
        self._refresh_stats()

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
        self.round_btn.disabled = not enabled
        self.mute_btn.disabled = not enabled
        if enabled:
            self.fire_btn.background_color = hex_rgb(COL_FIRE) + (1,)
            self.reset_btn.background_color = hex_rgb("#2a2a35") + (1,)
            self._restyle_selects()
            self._refresh_mute_btn()
            self.round_btn.background_color = hex_rgb(COL_GREEN) + (1,)
            bright = hex_rgb(COL_SUB) + (1,)
            white = hex_rgb(COL_TEXT) + (1,)
            self._rtp_title_lbl.color = bright
            self._bet_title_lbl.color = bright
            self.stats_lbl.color = white
        else:
            off = hex_rgb(COL_BTN_OFF) + (1,)
            dim = hex_rgb(COL_GRAY) + (0.6,)
            self.fire_btn.background_color = off
            self.reset_btn.background_color = hex_rgb("#1a1a22") + (1,)
            for btn in list(self.bet_btns.values()) + list(self.rtp_btns.values()):
                btn.background_color = off
            self.round_btn.background_color = off
            self.mute_btn.background_color = off
            self._rtp_title_lbl.color = dim
            self._bet_title_lbl.color = dim
            self.stats_lbl.color = dim

    # ------------------------------ 交互 ------------------------------
    def _on_key_down(self, win, key, *rest):
        if key == 32:                     # 空格: 按住蓄力
            if not self._space_held:
                self._space_held = True
                self.start_charge()
            return True
        if key == 27:                     # Android 返回键 / ESC: 吞掉, 防止误触退出
            return True
        return False

    def _on_key_up(self, win, key, *rest):
        if key == 32:
            self._space_held = False
            if self.state == "charging":
                # 冻结松手瞬间的力度, 50ms 去抖后发射(与 tkinter 版一致)
                self._release_power = self.power
                Clock.schedule_once(self._space_fire, 0.05)
            return True
        return False

    def _space_fire(self, dt):
        if self._release_power is not None:
            self.power = self._release_power
            self._release_power = None
        self.launch()

    def _on_title_touch_down(self, win, touch):
        if (self.title_lbl.collide_point(*touch.pos)
                and not getattr(self, "_bench_running", False)):
            self._bench_start = time.time()
            self._bench_triggered = False

    def _on_title_touch_up(self, win, touch):
        self._bench_start = 0.0

    def _show_bench_dim(self):
        self._bench_dim_shown = True
        self._bench_dim_col.rgba = (0.05, 0.06, 0.09, 0.72)
        self._relayout_bench_dim()

    def _relayout_bench_dim(self, *_):
        if getattr(self, "_bench_dim_shown", False):
            # 盖全屏: RootWidget 在平板是 AnchorLayout 居中, self.size 只是内容列
            self._bench_dim_rect.pos = (-self.x, -self.y)
            self._bench_dim_rect.size = Window.size

    def _hide_bench_dim(self):
        self._bench_dim_shown = False
        self._bench_dim_col.rgba = (0, 0, 0, 0)
        self._bench_dim_rect.size = (0, 0)

    def _bench_toast_tick(self, dt):
        for e in self.game_area._effects:
            if e["kind"] == "toast":
                return   # 还有 toast 存活, 不重复弹
        self.game_area.center_toast("测试设备性能中", hexcolor=COL_TEXT, size=30, life=3.0)

    def _check_title_hold(self):
        t = getattr(self, "_bench_start", 0)
        if t > 0 and not self._bench_triggered and time.time() - t >= 3.0:
            self._bench_triggered = True
            self._bench_running = True
            self._bench_saved_status = self.status_lbl.text
            self.status_lbl.text = "性能测试中…"
            self._set_controls_enabled(False)
            self._show_bench_dim()   # 第1阶段就开始: 全屏置灰
            self.game_area.center_toast("测试设备性能中", hexcolor=COL_TEXT, size=30, life=3.0)
            self._bench_toast_evt = Clock.schedule_interval(self._bench_toast_tick, 0.5)
            self._start_benchmark()

    def _start_benchmark(self):
        """阶段1: 真实屏幕采样(on_flip, 自动发球3发), 发满后切阶段2物理吞吐。"""
        self._flip_times = []
        Window.bind(on_flip=self._on_flip)
        self._launch_count = 0
        self._target_launches = 3
        self._auto_evt = Clock.schedule_interval(self._auto_launch_tick, 0.5)

    def _on_flip(self, win):
        self._flip_times.append(time.time())

    def _auto_launch_tick(self, dt):
        if self._launch_count >= self._target_launches:
            self._finish_render_sample(0)
            return
        if self.state == "ready":
            self.start_charge()
            self._launch_count += 1
            Clock.schedule_once(lambda _: (setattr(self, "power", 0.8), self.launch()), 0.1)

    def _finish_render_sample(self, dt):
        """停止屏幕采样, 统计真实 FPS/掉帧, 等球落地后启动物理 benchmark。"""
        if getattr(self, "_auto_evt", None):
            self._auto_evt.cancel()
            self._auto_evt = None
        Window.unbind(on_flip=self._on_flip)
        flips = self._flip_times or []
        if len(flips) >= 2:
            gaps = [flips[i + 1] - flips[i] for i in range(len(flips) - 1)]
            s = sorted(gaps)
            self._render_fps = 1.0 / s[len(s) // 2] if s[len(s) // 2] > 0 else 0.0
            n = max(1, int(len(s) * 0.01))
            self._render_1low = 1.0 / (sum(s[-n:]) / n)  # 1% low FPS(最慢1%帧的平均帧率)
        else:
            self._render_fps = 0.0
            self._render_1low = 0.0
        self._wait_idle_then_bench()

    def _wait_idle_then_bench(self, dt=0):
        """等球落地(主线程空闲)再启动物理 benchmark, 避免抢 CPU 干扰结果。"""
        if self.state == "ready":
            threading.Thread(target=self._run_benchmark, daemon=True).start()
        else:
            Clock.schedule_once(self._wait_idle_then_bench, 0.5)

    def _run_benchmark(self):
        flights, frames, fps_list = benchmark_trajectories()
        Clock.schedule_once(lambda dt: self._bench_done(flights, frames, fps_list), 0)

    def _device_info(self):
        if platform == 'android':
            try:
                from jnius import autoclass
                b = autoclass('android.os.Build')
                return '%s / Android %s' % (b.MODEL, b.VERSION.RELEASE)
            except Exception:
                try:
                    import subprocess
                    p = subprocess.run(['getprop','ro.product.model'], capture_output=True, text=True)
                    model = p.stdout.strip() or 'Android'
                    p2 = subprocess.run(['getprop','ro.build.version.release'], capture_output=True, text=True)
                    ver = p2.stdout.strip() or '?'
                    return '%s / Android %s' % (model, ver)
                except Exception:
                    return 'Android 设备'
        import platform as pf
        return '%s / %s / Python %s' % (pf.node(), pf.system(), pf.python_version())

    def _bench_done(self, flights, frames, fps_list):
        if getattr(self, "_bench_toast_evt", None):
            self._bench_toast_evt.cancel()
            self._bench_toast_evt = None
        self._hide_bench_dim()   # 第2轮结束: 恢复界面
        phys_fps = sorted(fps_list)[len(fps_list) // 2]   # 物理吞吐中位数
        avg_frames = frames / max(1, flights)
        cost_ms = avg_frames / phys_fps * 1000.0 if phys_fps > 0 else 0.0  # 每发纯物理耗时
        render_fps = getattr(self, "_render_fps", 0.0)
        render_1low = getattr(self, "_render_1low", 0.0)
        dev = self._device_info()
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        title_lbl = Label(text='性能测试', font_size='20sp', bold=True,
                          halign='center', color=hex_rgb(COL_TEXT) + (1,),
                          size_hint_y=None, height=dp(28))
        title_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(title_lbl)
        data = ('%s\n'
                '运算速度：每秒 %d 步模拟\n'
                '每次发射：需 %.0f 步模拟(用时 %.1f 毫秒)\n'
                '平均帧率： %.1f\n'
                '1%%Low帧率：%.1f') % (
                    dev, int(phys_fps), avg_frames, cost_ms, render_fps, render_1low)
        data_lbl = Label(text=data, font_size='15sp', halign='left', valign='top',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(140))
        data_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(data_lbl)
        popup = Popup(title='', content=content, size_hint=(0.90, None), height=dp(280),
                      auto_dismiss=True, separator_height=0)
        popup.open()
        self.status_lbl.text = getattr(self, '_bench_saved_status', '按住蓄力发射')
        self._set_controls_enabled(True)
        self._bench_running = False
        self._bench_start = 0.0

    SOUND_MODES = ("voice", "sfx", "off")   # 顶栏音效钮三态循环顺序

    def toggle_mute(self):
        i = self.SOUND_MODES.index(self.sound_mode)
        self.sound_mode = self.SOUND_MODES[(i + 1) % len(self.SOUND_MODES)]
        if self.sound_mode == "off":
            # "关闭声音"(0.84s)必须在静音前播; 延迟真正关闭, 让播报收尾后再停输出
            self.sfx.play("voice_mode_off")
            Clock.schedule_once(self._apply_sound_off, 1.0)
        else:
            self.sfx.set_enabled(True)
            self.sfx.play("voice_mode_" + self.sound_mode)
        self._refresh_mute_btn()
        self._save_config()

    def _apply_sound_off(self, dt):
        if self.sound_mode == "off":      # 延迟窗口内玩家又切回 voice/sfx 则取消关闭
            self.sfx.set_enabled(False)

    def _refresh_mute_btn(self):
        # 语音=绿底深字 / 音效=蓝底白字 / 关=深底亮灰字, 三态一眼可辨
        if self.sound_mode == "voice":
            self.mute_btn.text = "语音已开"
            self.mute_btn.background_color = hex_rgb(COL_GREEN) + (1,)
            self.mute_btn.color = hex_rgb("#0e1524") + (1,)
        elif self.sound_mode == "sfx":
            self.mute_btn.text = "音效已开"
            self.mute_btn.background_color = hex_rgb(COL_BTN) + (1,)
            self.mute_btn.color = (1, 1, 1, 1)
        else:
            self.mute_btn.text = "音效已关"
            self.mute_btn.background_color = hex_rgb("#3d3828") + (1,)
            self.mute_btn.color = hex_rgb("#c0c8e4") + (1,)

    def _refresh_stats(self):
        rate = 100.0 * self.hits / self.plays if self.plays > 0 else 0
        self.stats_lbl.text = "累计%d投%d中(%.0f%%)" % (
            self.plays, self.hits, rate)

    def set_bet(self, v, silent=False):
        self.bet = v
        self._restyle_selects()
        self._refresh_stats()
        self.sfx.play("click", throttle=0.08)
        if not silent and time.time() >= self._result_until:
            self.sfx.play("voice_bet_%d" % v, throttle=0.6)
        self._save_config()

    def set_rtp(self, t, silent=False):
        self.rtp_target = t
        self._restyle_selects()
        self.sfx.play("click", throttle=0.08)
        if not silent and time.time() >= self._result_until:
            pct = int(t * 100)
            self.sfx.play("voice_rtp_%d" % pct, throttle=0.6)
        if self.state == "ready":
            self.multipliers = roll_multipliers(self.rtp_target)
            self.game_area._redraw()
        self._save_config()

    def reset_balance(self, notify=True):
        # 强制中断当前操作(充电/飞行/哑火/着陆), 回到 ready
        if self.state in ("charging", "flying", "misfire", "landing"):
            self.state = "ready"
            self.power = 0.0
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self._anim_start_time = time.time()
        self.plays = 0
        self.hits = 0
        self._refresh_stats()
        self.status_lbl.text = "已重置"
        self.round_plays = 0
        self._round_end_shown = False
        self.sfx.play("cash")
        self._set_controls_enabled(True)
        if notify:
            # 清理旧 toast(先移除 widget 再从列表过滤, 防控件泄漏)
            for e in self.game_area._effects:
                if e["kind"] == "toast":
                    for w in e["ws"]:
                        self.game_area.remove_widget(w)
            self.game_area._effects = [e for e in self.game_area._effects if e["kind"] != "toast"]
            Clock.schedule_once(lambda dt: self.game_area.center_toast(
                "弹珠数量已调整到1000个", hexcolor=COL_GREEN, size=28, life=1.5), 0.05)
            self.sfx.play("voice_reset_progress", throttle=1.5)
        # 恢复按钮颜色
        self.reset_btn.background_color = hex_rgb("#2a2a35") + (1,)
        self._save_config()

    def start_charge(self):
        if self.state != "ready":
            return
        if self.balance < self.bet:
            if self.sound_mode == "voice":
                # 语音档: 播报替换 error 嗡声; 语音全长 2.9s, 节流到播完才许重播
                self.sfx.play("voice_nomoney", throttle=3.0)
            else:
                self.sfx.play("error", throttle=0.4)
            self.game_area.center_toast("弹珠数量不足\n请重置或降低投入")
            return
        if self.round_plays >= self.max_plays:
            self._show_round_end()
            return
        self.state = "charging"
        self.power = 0.0
        self._last_charge_sound = 0.0        # 立刻响第一声棘轮
        self._charge_topped = False
        self.status_lbl.text = "蓄力中"

    def launch(self):
        if self.state != "charging":
            return
        if self.power < MISFIRE_POWER:
            # 哑火: 球照样弹出去, 只是升不过隔墙顶 -> 掉回柱塞。不扣弹珠、不计一局、不换盘面
            frozen_power = self.power  # 在清零前保存, 用于音量/震动分级
            self.ball = launch_misfire(self.power)
            self.state = "misfire"
            self._accumulator = 0.0
            self.power = 0.0                  # 哑火后清除蓄力显示
            self._misfire_frames = 0
            self.sfx.play("launch", SFX_MISFIRE_GAIN + (SFX_MISFIRE_GAIN_MAX -
                          SFX_MISFIRE_GAIN) * clamp(frozen_power / MISFIRE_POWER, 0.0, 1.0))
            _vibrate(8)
            self._set_controls_enabled(False)
            self.status_lbl.text = "力度不足,未扣弹珠"
            return
        frozen_power = self.power  # 在清零前保存, 用于音量/震动分级
        self.balance -= self.bet
        # 发射: 弧面垂直抖动 ±6px(每发随机), 纯物理飞行(无预演/无渲染修正)。
        arc_dy = random.uniform(-6.0, 6.0)
        self.geo["deflectors"] = [(x1, y1 + arc_dy, x2, y2 + arc_dy)
                                  for (x1, y1, x2, y2) in self._base_deflectors]
        self.ball = launch_ball(frozen_power)
        self._settled = False                 # 新发射重置结算标记(结算延迟到回弹后)
        self.state = "flying"
        self._accumulator = 0.0
        self.power = 0.0                      # 发射后清除蓄力显示
        self.plays += 1
        self.round_plays += 1
        self._crossed = False
        self._risen = False
        self._topped = False
        self._last_motion = time.time()   # 卡死兜底的运动锚点(之后由帧内位移检测刷新)
        self._last_ball_xy = (self.ball["x"], self.ball["y"])
        self.sfx.play("launch", SFX_LAUNCH_GAIN + (SFX_LAUNCH_GAIN_MAX -
                      SFX_LAUNCH_GAIN) * power_u(frozen_power))
        _vibrate(14)
        self._set_controls_enabled(False)
        self.status_lbl.text = "发射!"

    # ------------------------------ 结算 ------------------------------
    def settle(self, i):
        m = self.multipliers[i]
        payout = self.bet * m
        self.balance += payout
        if m > 0:
            self.hits += 1
        self._refresh_stats()
        # 结果只在画布中央报一次(Hero 大字)。槽位上方那行小飘字撤了: 手机屏上两处同时飘
        # "+50"/"0" 是重复信息, 而且下面那行按场景缩放只有 15sp, 小得只剩干扰。
        self.status_lbl.text = ("中奖!  +%d (x%d)" % (payout, m)) if payout > 0 else "未中"
        if m <= 0:    lamp = COL_FIRE
        elif m <= 2:  lamp = COL_GREEN
        elif m <= 3:  lamp = "#3d8bfd"
        elif m <= 5:  lamp = COL_FIRE
        elif m <= 10: lamp = "#a335ee"
        else:         lamp = COL_METER
        self.game_area.set_lamp(i, lamp)
        self.game_area.pulse_slot(i)
        self._play_result_sound(m, payout)
        self.game_area.big_result_text(m, payout)
        self._result_until = time.time() + 2.5   # 结果窗口: 期内抑制UI语音
        if m > 0:                             # 只要中奖就震, 按倍率分档(x2/x3 轻点一下)
            _vibrate(150 if m >= 20 else (110 if m >= 10 else (75 if m >= 5 else 45)))
        # 数字滚动动画 + 大奖节奏分档(x10 以上滚更久, 看得清中大奖)
        big = m >= 10
        self._land_hold = 0.7 if big else max(0.3, LAND_HOLD - 0.5)  # 提前0.5s可发射
        self._anim_dur = 1.2 if big else 0.5
        self._anim_start_balance = self.display_balance
        self._anim_target_balance = float(self.balance)
        self._anim_start_time = time.time()
        self._coin_until = (time.time() + (1.2 if big else 0.5)) if payout > 0 else 0.0
        self._save_config()

    def park_ball(self, reroll=True, silent=False):
        """重掷盘面(reroll=True), 新球停到柱塞, 回 ready。哑火 reroll=False 防免费刷盘。"""
        if reroll:
            self.multipliers = roll_multipliers(self.rtp_target)
            self.game_area._redraw()
        else:
            self.game_area.lamps_off()
        self.ball = Ball(x=PLUNGER_X, y=PLUNGER_Y, vx=0.0, vy=0.0,
                         item=None, born=time.time(), events=0, amp={},
                         misfire=False)
        self.state = "ready"
        self.power = 0.0
        self._set_controls_enabled(True)
        if self.round_plays >= self.max_plays and not self._round_end_shown:
            self._show_round_end()
        if not silent:
            self.sfx.play("ready", 0.8)

    # ------------------------------ 轮次结束 ------------------------------
    @staticmethod
    def _history_path():
        """轮次历史 JSON 文件路径(持久化到 user_data_dir, Android 上为应用私有目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_round_history.json")

    def _load_history(self):
        try:
            with open(self._history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.round_history = data[-100:]
        except Exception:
            pass

    def _save_history(self):
        try:
            with open(self._history_path(), "w") as f:
                json.dump(self.round_history[-100:], f)
        except Exception:
            pass

    @staticmethod
    def _config_path():
        """游戏设定 JSON 文件路径(与轮次历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_config.json")

    def _load_config(self):
        try:
            with open(self._config_path(), "r") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                if cfg.get("sound_mode") in ("voice", "sfx", "off"):
                    self.sound_mode = cfg["sound_mode"]
                if isinstance(cfg.get("max_plays"), int) and cfg["max_plays"] in (20, 50, 100):
                    self.max_plays = cfg["max_plays"]
                if isinstance(cfg.get("rtp_target"), (int, float)) and cfg["rtp_target"] in (0.80, 1.20, 2.00, 3.00):
                    self.rtp_target = float(cfg["rtp_target"])
                if isinstance(cfg.get("bet"), int) and cfg["bet"] in PRESETS:
                    self.bet = cfg["bet"]
                if isinstance(cfg.get("balance"), (int, float)) and cfg["balance"] >= 0:
                    self.balance = int(cfg["balance"])
                    self.display_balance = float(self.balance)
                    self._anim_target_balance = float(self.balance)
                    self._anim_start_balance = float(self.balance)
                if isinstance(cfg.get("round_plays"), int) and 0 <= cfg["round_plays"] <= self.max_plays:
                    self.round_plays = cfg["round_plays"]
                if isinstance(cfg.get("plays"), int) and cfg["plays"] >= 0:
                    self.plays = cfg["plays"]
                if isinstance(cfg.get("hits"), int) and cfg["hits"] >= 0:
                    self.hits = cfg["hits"]
        except Exception:
            pass
        if self.sound_mode == "off":
            self.sfx.set_enabled(False)
        if self.round_plays >= self.max_plays:
            self._auto_reset_on_start = True   # UI还没建, 延后到 _build_ui 之后

    def _save_config(self):
        try:
            cfg = {
                "sound_mode": self.sound_mode,
                "max_plays": self.max_plays,
                "rtp_target": self.rtp_target,
                "bet": self.bet,
                "balance": self.balance,
                "round_plays": self.round_plays,
                "plays": self.plays,
                "hits": self.hits,
            }
            with open(self._config_path(), "w") as f:
                json.dump(cfg, f)
        except Exception:
            pass

    def _voice_duration(self, name):
        """查语音片段时长(秒), 用于队列播放的调度间隔。"""
        return self.sfx.voice_duration(name)

    def _play_voice_sequence(self, names, gap=0.005, on_done=None):
        """依次播放语音片段列表。on_done 在整个序列播完后回调(用于解锁弹窗按钮等)。
        返回总时长(秒), 供调用方设定兜底定时器。"""
        delay = 0.0
        total = 0.0
        for name in names:
            dur = self._voice_duration(name)
            Clock.schedule_once(lambda dt, n=name: self.sfx.play(n), delay)
            delay += dur + gap
            total = delay
        if on_done:
            Clock.schedule_once(lambda dt: on_done(), total)
        return total

    def _play_round_end_voice(self, on_done=None):
        """组装并播放轮次结束语音: 模板 + 当前弹珠数 + 后缀。返回总时长(秒)。"""
        prefix_key = "voice_round_end_%d" % self.max_plays
        voices = [prefix_key]
        voices.extend(number_voice_names(self.balance))
        voices.append("voice_round_suffix")
        return self._play_voice_sequence(voices, on_done=on_done)

    def _show_round_end(self):
        """本轮游戏结束弹窗: 恭喜文案 + 统计 + 语音播报(播完自动重置并关闭)。"""
        if self._round_end_shown:
            return
        self._round_end_shown = True
        self._set_controls_enabled(False)
        self.round_history.append({
            "plays": self.round_plays,
            "balance": self.balance,
            "time": time.time(),
        })
        if len(self.round_history) > 100:
            self.round_history.pop(0)
        self._save_history()
        content = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14))
        msg = "本轮游戏 %d 次已结束\n剩余 %d 个弹珠\n弹珠数量已调整到1000个\n欢迎你再次挑战" % (
            self.round_plays, self.balance)
        lbl = Label(text=msg, font_size="18sp", halign="center", valign="middle",
                    color=hex_rgb(COL_TEXT) + (1,))
        lbl.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        content.add_widget(lbl)
        popup = Popup(title="本轮游戏结束", content=content,
                      size_hint=(0.82, None), height=dp(260),
                      auto_dismiss=False,
                      title_color=hex_rgb(COL_TEXT) + (1,),
                      title_size="19sp",
                      separator_color=hex_rgb(COL_DIV) + (1,))
        popup.open()
        # 语音播完后自动重置并关闭弹窗
        _done = [False]                            # 防重复调用
        def _auto_reset():
            if _done[0]:
                return
            _done[0] = True
            self.reset_balance(notify=False)
            popup.dismiss()
        voice_total = self._play_round_end_voice(on_done=_auto_reset)
        # 兜底定时器: 语音回调若因任何原因没触发, 在总时长+3秒后强制重置
        Clock.schedule_once(lambda dt: _auto_reset(), voice_total + 3.0)

    def _show_round_settings(self):
        """轮次设定弹窗: 选择 20/50/100 + 最近完成的轮次历史。"""
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12))

        # 每轮次数选择(纵向: 标签一行, 按钮一行, 全自适应防溢出)
        lbl = Label(text="每轮游戏次数：", font_size="16sp", halign="left", valign="middle",
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(28))
        lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(lbl)
        sel_box = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        sel_btns = {}
        for val in (20, 50, 100):
            b = Button(text="%d次" % val, font_size="16sp", bold=True,
                       background_normal="", background_down="",
                       color=(1, 1, 1, 1))
            b.bind(on_release=lambda _b, v=val: self._set_max_plays(v, sel_btns))
            sel_btns[val] = b
            sel_box.add_widget(b)
        content.add_widget(sel_box)

        def _refresh_sel():
            for v, b in sel_btns.items():
                b.background_color = hex_rgb(COL_BTN if self.max_plays == v else COL_BTN_OFF) + (1,)
        _refresh_sel()

        # 历史记录(ScrollView 可滚动, 最多显示最近 100 条)
        hist_lbl = Label(text="最近完成的轮次：", font_size="15sp", halign="left", valign="middle",
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(28))
        hist_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(hist_lbl)
        if self.round_history:
            lines = []
            for i, r in enumerate(reversed(self.round_history[-100:])):
                lines.append("最近第%d轮  每轮%d次  剩 %d 个弹珠" %
                            (i + 1, r["plays"], r["balance"]))
            text = "\n".join(lines)
        else:
            text = "暂无完成的轮次记录"
        hist_text = Label(text=text, font_size="15sp", halign="left", valign="top",
                          color=hex_rgb(COL_TEXT) + (0.7,),
                          size_hint_y=None)
        hist_text.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)),
                       texture_size=lambda w, *_: setattr(w, "height", w.texture_size[1] + dp(8)))
        scroll = ScrollView(size_hint=(1, 1), bar_width=dp(6))
        scroll.add_widget(hist_text)
        content.add_widget(scroll)

        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        popup = Popup(title="每轮游戏次数设定", content=content,
                      size_hint=(0.84, None), height=dp(500),
                      auto_dismiss=True,
                      title_color=hex_rgb(COL_TEXT) + (1,),
                      title_size="19sp",
                      separator_color=hex_rgb(COL_DIV) + (1,))
        ok_btn.bind(on_release=popup.dismiss)
        content.add_widget(ok_btn)
        popup.open()

    def _set_max_plays(self, val, sel_btns=None):
        """切换每轮次数上限: 更换即重置(弹珠/游玩次数/轮次全部清零, 从头开始)。"""
        if self.max_plays == val:
            return
        self.max_plays = val
        self.round_btn.text = "每轮%d次" % val
        self.balance = START_BEADS
        self.display_balance = float(START_BEADS)
        self._anim_target_balance = float(START_BEADS)
        self._anim_start_balance = float(START_BEADS)
        self.plays = 0
        self.hits = 0
        self.round_plays = 0
        self._round_end_shown = False
        self._refresh_stats()
        if self.state == "ready":
            self.multipliers = roll_multipliers(self.rtp_target)
            self.game_area._redraw()
        # 刷新弹窗内选中高亮
        if sel_btns:
            for v, b in sel_btns.items():
                b.background_color = hex_rgb(COL_BTN if self.max_plays == v else COL_BTN_OFF) + (1,)
        # toast + 语音提示
        self.game_area.center_toast("每轮已设定为%d次" % val, hexcolor=COL_GREEN, size=20, life=1.5)
        self.sfx.play("voice_round_set_%d" % val)
        self._save_config()

    # ------------------------------ 音效 ------------------------------
    def _play_events(self, ev, amp, b):
        """播放本渲染帧收集到的碰撞事件(ev/amp 由累加器循环内逐物理帧消费汇总,
        与预演 _sim_flight 的逐帧清事件同构 —— 否则残留 events 会让注入计数错位)。"""
        if not ev:
            return
        amp = amp or {}
        for bit in (EV_PEG, EV_CEIL, EV_WALL, EV_DIV):
            if ev & bit:
                if bit == EV_WALL and b.y < SFX_TOP_Y:
                    continue          # 顶墙撞击与 apex 转向同帧发生, 交给 top 音, 不再叠闷咚
                self.sfx.impact(bit, amp.get(bit, 0.0))
        if ev & EV_ARC:
            self.sfx.play("rail", 0.18, throttle=0.05)   # 弧面接触: 轻金属"擦"声,
                                                         # 转向瞬间的听觉反馈(不能是幽灵装置)

    def _play_charge_sound(self, power):
        if power >= 1.0:
            now = time.time()
            if not self._charge_topped:
                self._charge_topped = True
                self._last_charge_sound = now
                self.sfx.play("charge_full")
            elif now - self._last_charge_sound >= CHARGE_HOLD_SEC:
                self._last_charge_sound = now
                self.sfx.play("charge_full", CHARGE_HOLD_GAIN)
            return
        now = time.time()
        if now - self._last_charge_sound < 0.25 - 0.18 * clamp(power, 0.0, 1.0):
            return
        self._last_charge_sound = now
        self.sfx.play("ratchet%d" % int(clamp(power, 0.0, 1.0) * 5.99))

    def _play_result_sound(self, m, payout):
        self.sfx.play("pocket")
        if m <= 0:
            self.sfx.play("lose", 0.9)    # "好遗憾"语音已制作(voice_lose), 暂不接入
            return
        if self.sound_mode == "voice":
            # 语音档: "弹珠加xx"替换 win 琶音(语音与琶音同播会互相盖, 见 BUILD 讨论)
            self.sfx.play("voice_win%d" % payout)
            return
        tier = 0 if m <= 2 else (1 if m <= 3 else (2 if m <= 5 else (3 if m < 20 else 4)))
        self.sfx.play("win%d" % tier)

    # ------------------------------ 帧循环 ------------------------------
    def _apply_sizes(self):
        """将 _ui_scale / _font_scale 写到所有固定 UI 元素的尺寸和字号上。
        横屏时缩小所有固定行高/按钮宽/字号/边距, 把垂直空间还给游戏区。
        纵向边距用平方衰减(us²), 横屏时更激进地挤掉空白。"""
        us = self._ui_scale
        uv = us * us                                    # 纵向: 平方衰减, 激进挤空白
        fs = self._font_scale * us

        self._row_top.height    = dp(H_TOP)    * us
        self._row_rtp.height    = dp(H_RTP)    * us
        self._row_bets.height   = dp(H_BETS)   * us
        self._row_info.height   = dp(H_INFO)   * us
        self._row_bottom.height = dp(H_BOTTOM) * us
        self.spacing = dp(10) * uv                      # 行间距: 激进衰减

        self._row_top.padding    = [dp(10), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_rtp.padding    = [dp(24), dp(4) * uv]
        self._row_bets.padding   = [dp(24), dp(4) * uv]
        self._row_bottom.padding = [dp(6), dp(4) * uv, dp(12), dp(4) * uv]
        self.padding = [0, 0, 0, dp(12)]  # 底部留白

        self.title_lbl.font_size       = sp(18) * fs
        self.status_lbl.font_size      = sp(13) * fs
        self._rtp_title_lbl.font_size  = sp(14) * fs
        self._bet_title_lbl.font_size  = sp(14) * fs
        self._bead_lbl.font_size       = sp(15) * fs
        self.balance_lbl.font_size     = sp(19) * fs
        self.stats_lbl.font_size       = sp(15) * fs
        self.power_lbl.font_size       = sp(14) * fs

        self.mute_btn.font_size = sp(13) * fs
        self.round_btn.font_size = sp(13) * fs
        for b in self.rtp_btns.values():
            b.font_size = sp(16) * fs
        for b in self.bet_btns.values():
            b.font_size = sp(16) * fs
        self.reset_btn.font_size = sp(16) * fs
        self.fire_btn.font_size  = sp(16) * fs

        self.mute_btn.width    = dp(64)  * us
        self.round_btn.width   = dp(72)  * us
        self.title_lbl.width    = dp(112) * us
        self.reset_btn.width   = dp(96)  * us
        self.fire_btn.width    = dp(110) * us
        self.power_lbl.width   = dp(100) * us
        self._rtp_title_lbl.width  = dp(115) * us
        self._bet_title_lbl.width  = dp(115) * us
        for b in self.rtp_btns.values():
            b.width = dp(56) * us
        for b in self.bet_btns.values():
            b.width = dp(56) * us

    def _frame(self, dt):
        self._check_title_hold()
        ws = (Window.width, Window.height)
        if ws != self._last_win_size:
            self._last_win_size = ws
            self._fit_width()
            self._apply_sizes()
        if self.state == "charging":
            self.power = min(1.0, self.power + CHARGE_RATE * dt)  # 用真实dt, 适配30fps设备
            self._play_charge_sound(self.power)
            weak = self.power < MISFIRE_POWER
            self.fire_btn.background_color = hex_rgb(COL_FIRE if weak else "#8B6914") + (1,)
        elif self.state == "flying" and self.ball is not None:
            b = self.ball
            self._accumulator += dt
            landed = None
            tick_ev = 0
            tick_amp = {}
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                landed = advance_flight(b, self.geo)
                # 逐物理帧消费事件: 汇总给音效(残留位会让撞钉计数虚高, 实测 35% 偏差)
                if b.events:
                    tick_ev |= b.events
                    for bit, spd in (b.amp or {}).items():   # spd=振幅(改名避免shadow kivy sp单位)
                        if spd > tick_amp.get(bit, 0.0):
                            tick_amp[bit] = spd
                    b.events = 0
                    b.amp.clear()
            if not self._crossed and b.x < FIELD_R and b.y < LANE_WALL_TOP:
                self._crossed = True
            elif self._crossed and not self._risen and b.y > RISER_Y:
                self._risen = True
                self.sfx.play("riser", 0.9)
            if self._crossed and not self._topped and b.vy >= 0.0:
                self._topped = True           # 冲到顶点转向(恒在 0.95~1.00s): 顶部碰撞声
                self.sfx.top(b.y)
            if b.y > SLOT_TOP - 40:
                self.status_lbl.text = "即将入袋…"
            elif b.y > PEG_TOP:
                self.status_lbl.text = "弹跳中…"
            else:
                self.status_lbl.text = "入场中…"
            if landed is None:
                lx, ly = self._last_ball_xy
                if (b.x - lx) ** 2 + (b.y - ly) ** 2 > 1.0:
                    self._last_motion = time.time()     # 位移>1px/帧: 还在动, 不是卡死
                elif (time.time() - self._last_motion > STALL_RETRY_SEC
                      and getattr(b, "_stall_retry", 0) < STALL_MAX_RETRY):
                    # 踢球(不退回柱塞重发): 沿接触法线向下踢, 玩家看不到"发射失败重发"
                    nx, ny = getattr(b, "last_nx", 0.0), getattr(b, "last_ny", -1.0)
                    tx_, ty_ = -ny, nx
                    if ty_ > 0: tx_, ty_ = -tx_, -ty_
                    b.vx += tx_ * 120.0
                    b.vy += ty_ * 120.0
                    b._stall_retry = getattr(b, "_stall_retry", 0) + 1
                    self._last_motion = time.time()
                    self._last_ball_xy = (b.x, b.y)
                    self._crossed = self._risen = self._topped = False
                elif time.time() - self._last_motion > MAX_FALL_SEC:
                    landed = max(0, min(NUM_SLOTS - 1,      # 真卡死兜底: 物理槽
                                        int((b.x - FIELD_L) / SLOT_W)))
                self._last_ball_xy = (b.x, b.y)
                # 判据必须用位移而非速度/碰撞事件: 卡死球的
                # 速度数值和微碰撞(被推向障碍)从未停过, 但位置被碰撞钉死 —— 位置不说谎。
            if landed is not None:
                i = max(0, min(NUM_SLOTS - 1,              # 物理落格结算(球落到哪算哪)
                               int((b.x - FIELD_L) / SLOT_W)))
                self.land_target_x = FIELD_L + (i + 0.5) * SLOT_W
                self._settle_slot = i              # 记录落格槽, 结算延迟到回弹落定后(用户定稿)
                self.landed_at = time.time()
                self.state = "landing"
                self._accumulator = 0.0
                self._landing_primed = True   # 首帧补初速, 之后交给物理
                # 不立即 settle: 先做落地回弹展示(回弹→停留→再结算), 结算槽=物理落格槽零穿帮
            if tick_ev:
                self._play_events(tick_ev, tick_amp, self.ball)
        elif self.state == "misfire":
            self._misfire_frames += 1
            self._accumulator += dt
            stepped = False
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                stepped = True
            if stepped:
                if advance_misfire(self.ball) or self._misfire_frames > MISFIRE_MAX_FRAMES:
                    self.ball.x = PLUNGER_X
                    self.ball.y = PLUNGER_Y
                    self.ball.vx = 0.0
                    self.ball.vy = 0.0
                    self.sfx.play("bounce", 0.55)
                    self.park_ball(reroll=False)
        elif self.state == "landing":
            b = self.ball
            if self._landing_primed:
                self._landing_primed = False
                if b.vy < LAND_BOUNCE_MIN_VY:
                    b.vy = LAND_BOUNCE_MIN_VY + random.uniform(-30, 30)
            self._accumulator += dt
            floor_y = FLOOR - BALL_R
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                b.vx += (self.land_target_x - b.x) * LAND_K * FIXED_DT
                b.vx *= LAND_DAMP
                b.vx = clamp(b.vx, -ALIGN_VX_MAX, ALIGN_VX_MAX)
                b.vy += G * FIXED_DT
                b.x += b.vx * FIXED_DT
                b.y += b.vy * FIXED_DT
                if b.y >= floor_y:
                    b.y = floor_y
                    if b.vy > 0:
                        if b.vy > 60.0:
                            self.sfx.play("bounce", clamp(b.vy / 500.0, 0.3, 1.0), 0.05)
                        b.vy = -b.vy * LAND_E * random.uniform(0.92, 1.08)
                        if b.vy < -LAND_BOUNCE_MAX_VY:     # 回弹vy上限(删刹车后防弹越隔板)
                            b.vy = -LAND_BOUNCE_MAX_VY
            if (abs(b.x - self.land_target_x) < SLOT_W * 0.45 and abs(b.vy) < 10.0
                    and b.y >= floor_y - 0.5):
                b.vx = 0.0
                b.vy = 0.0
                self.state = "landed"
                self.landed_at = time.time()
                if not self._settled:          # 回弹落定后结算(用户定稿: 先回弹强调槽位, 再弹结算)
                    self._settled = True
                    self.settle(self._settle_slot)
            elif time.time() - self.landed_at >= 0.5:
                b.y = floor_y
                b.vx = 0.0
                b.vy = 0.0
                self.state = "landed"
                self.landed_at = time.time()
                if not self._settled:          # 超时兜底也结算(防永不落定)
                    self._settled = True
                    self.settle(self._settle_slot)
        elif self.state == "landed":
            if time.time() - self.landed_at >= self._land_hold:
                self.park_ball()
        if self.state != "charging" and self.power <= 0.01 and self.power_lbl.text:
            self.power_lbl.text = ""
        # 余额数字滚动(老虎机式翻滚再落定), 中奖滚分时连播 coin; 大奖(x>=10)滚 1.2s
        now = time.time()
        if now < self._coin_until:
            self.sfx.play("coin", 0.8, 0.055)
        elapsed = now - self._anim_start_time
        if elapsed < self._anim_dur:
            t = elapsed / self._anim_dur
            ease = 1.0 - (1.0 - t) ** 3
            noise = (1.0 - t) * random.uniform(-0.15, 0.15) if t < 0.6 else 0
            f = max(0.0, min(1.0, ease + noise))
            self.display_balance = (self._anim_start_balance +
                                    (self._anim_target_balance - self._anim_start_balance) * f)
        else:
            self.display_balance += (self.balance - self.display_balance) * 0.5
        self.balance_lbl.text = str(int(round(self.display_balance)))
        self.game_area.tick_draw()


# =============================================================================
# App 入口 / 冒烟
# =============================================================================
class PlinkoApp(App):
    def build(self):
        Window.clearcolor = hex_rgb(COL_BG) + (1,)
        if platform == "android":
            try:                                            # 运行时锁定竖屏: 部分设备系统级自动旋转
                from jnius import autoclass                 # 会覆盖 manifest 的 portrait 声明, 强制锁定
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                activity.setRequestedOrientation(1)         # SCREEN_ORIENTATION_PORTRAIT
            except Exception:
                pass
        else:
            Window.size = (540, 960)       # 桌面预览 9:16; 宽屏可最大化, 内容自适应居中
        self.title = "跳跳的弹珠机"
        sfx = Sfx(SOUND_ENABLED, sync=True)   # 同步烘焙: 全部音效就绪后才建 UI, 冷启动不空窗
        anchor = AnchorLayout(anchor_x="center", anchor_y="center")
        self.rootw = RootWidget(sfx=sfx, size_hint_x=None)
        anchor.add_widget(self.rootw)
        self.rootw._fit_width()
        self.rootw._apply_sizes()
        return anchor

    # Android 生命周期: on_pause 必须返回 True 保持 GL 上下文
    def on_pause(self):
        try:
            self.rootw.sfx.pause_out()       # 切后台静音(SoundPool.autoPause)
        except Exception:
            pass
        return True

    def on_resume(self):
        try:
            self.rootw.sfx.resume_out()
        except Exception:
            pass
        return True

    def on_stop(self):
        try:
            self.rootw.sfx.close()
        except Exception:
            pass
        return True


def _smoke():
    """桌面自动冒烟: 建窗 -> 蓄力发射 -> 截图 -> 必中盘(验证中奖特效) -> 哑火。"""
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
            r.multipliers = [2, 3, 5, 10, 20, 2, 3, 5, 10]   # 必中盘: 验证中奖特效
            r.game_area._redraw()
            r.start_charge()

    def s5(dt):
        r = app.rootw
        r.power = 1.0
        r.launch()

    def s6(dt):
        shot("05_win_effect.png")

    def s7(dt):
        shot("06_win_done.png")
        r = app.rootw
        # 直接调 settle 定格特效: x20 大奖 -> 金色大字 + 浮字 + 槽闪 + 滚分
        r.multipliers = [0, 0, 0, 0, 20, 0, 0, 0, 0]
        r.game_area._redraw()
        r.settle(4)

    def s7b(dt):
        shot("06b_fx_bigtext.png")
        app.rootw.toggle_mute()              # 静音: 截一张"音效已关"看对比
        shot("06c_mute_off.png")
        app.rootw.toggle_mute()              # 恢复

    def s8(dt):
        r = app.rootw
        print("SMOKE s8: state=%s" % r.state)
        if r.state == "ready":
            r.start_charge()
            r.power = 0.05                 # 哑火
            r.launch()
            print("SMOKE s8 after launch: state=%s" % r.state)

    def s9(dt):
        shot("07_misfire_done.png")
        r = app.rootw
        print("SMOKE s9: state=%s balance=%s bet=%s" % (r.state, r.balance, r.bet))
        if r.state == "ready":
            r.balance = 5                    # 余额 < 投注 -> 触发飘字
            r.start_charge()
            print("SMOKE s9 after start_charge: state=%s" % r.state)

    def s9b(dt):
        r = app.rootw
        print("SMOKE s9b: state=%s balance=%s" % (r.state, r.balance))
        shot("08_no_beads.png")
        print("SMOKE DONE ->", outdir)
        App.get_running_app().stop()

    Clock.schedule_once(s1, 1.5)
    Clock.schedule_once(s2, 2.5)
    Clock.schedule_once(s3, 4.0)
    Clock.schedule_once(s4, 8.5)
    Clock.schedule_once(s5, 9.3)
    Clock.schedule_once(s6, 13.6)
    Clock.schedule_once(s7, 15.0)
    Clock.schedule_once(s7b, 15.4)
    Clock.schedule_once(s8, 17.0)
    Clock.schedule_once(s9, 20.0)
    Clock.schedule_once(s9b, 20.5)
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
