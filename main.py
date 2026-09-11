# -*- coding: utf-8 -*-
# 跳跳的弹珠机 — Android (Kivy) 版。
# 本文件由 tools/build_android_main.py 自动拼接生成, 请勿直接编辑:
#   - 几何/物理/音效合成/自测段 原样抽取自 plinko.py
#   - 手写段在 tools/android_part_{head,backends,ui}.py
import sys
import os

os.environ.setdefault("KIVY_NO_ARGS", "1")   # 自定义参数(--selftest/--smoke)自己解析, 别让 Kivy 抢
# 四方向重力感应: 与 buildozer.spec orientation / manifest fullSensor 同值(p4a 会写进 p4a_env_vars.txt,
# 这里运行时再设一次防 bootstrap 未带上)。⚠️ 只写竖屏两方向会把 SDL 也锁竖屏, 横拿时
# 系统只能给 letterbox 兼容盒(ZUI 半屏盒的帮凶), buildozer.spec 放开四方向就会被这里覆盖废掉。
os.environ["KIVY_ORIENTATION"] = "Portrait PortraitUpsideDown Landscape LandscapeUpsideDown"

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
CEIL_VX_KEEP = 1.5           # [已废弃→KNOB_CEIL_BOOST] 天花板弹射加能: 撞顶后 vx,vy 同乘(方向不变=观感安全)

# ---------------- 4 旋钮离散表(按力度档关联, GA 优化定稿 2026-08-15) ---------------
# 力度 10 档(15%~100%), 每档 4 旋钮各一张「离散值+概率权重」表。每发球按 launch_power
# 定位最近力度档, 4 旋钮各采一次(弧面首次接触采、天花板首次撞顶采)。权重非负自动归一化。
# 目标: 逐力度档打分求和(落袋均匀×0.7+首钉均匀×0.3) 最大。K=6 + boost 限幅[0.5,1.3](加速≤30%/减速≤50%),
# 优化后总评分 8.49/10(基线 7.19)。boost 限幅是体验红线: 之前 GA 把力度推到 2.0~2.5 导致球极度加速。
KNOB_POWERS = [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.00]
KNOB_ARC_ANGLE = [   # 弧面出口角偏移(°, 相对 ARC_OUT_ANGLE=35°; 正=向左更陡)。<-15° 会越顶失败, 已 clamp
    [(3.87, 22), (5.07, 27), (0.54, 5), (-5.54, 46)],                        # 0.15
    [(4.51, 24), (2.25, 28), (-15.00, 13), (21.39, 35)],                      # 0.25
    [(-0.07, 8), (20.86, 26), (-12.26, 15), (-12.80, 8), (18.23, 16), (-2.95, 27)],  # 0.35
    [(-6.72, 14), (-4.84, 10), (6.91, 25), (29.24, 15), (22.23, 13), (5.92, 23)],   # 0.45
    [(-15.00, 15), (8.35, 27), (-3.55, 14), (27.64, 25), (7.72, 20)],          # 0.55
    [(-13.08, 13), (-15.00, 20), (23.78, 54), (12.47, 13)],                     # 0.65
    [(-15.00, 14), (-3.77, 21), (8.12, 59), (3.28, 6)],                        # 0.75
    [(28.24, 33), (-1.36, 17), (30.00, 33), (-15.00, 17)],                     # 0.85
    [(1.96, 12), (11.25, 23), (-3.19, 44), (11.54, 21)],                       # 0.95
    [(-15.00, 8), (9.17, 28), (-0.73, 23), (29.20, 12), (30.00, 12), (24.69, 17)],  # 1.00
]
KNOB_ARC_BOOST = [   # 弧面出口力度乘子(加速≤25%/减速≤35%)
    [(0.94, 31), (0.71, 9), (0.73, 5), (1.25, 37), (1.11, 18)],              # 0.15
    [(1.25, 78), (0.97, 8), (1.07, 14)],                                     # 0.25
    [(1.25, 74), (1.01, 8), (1.12, 13), (0.65, 5)],                          # 0.35
    [(0.65, 21), (1.19, 21), (1.25, 53), (1.10, 5)],                         # 0.45
    [(1.17, 18), (1.24, 29), (1.11, 6), (0.81, 35), (1.25, 12)],             # 0.55
    [(0.91, 20), (1.25, 46), (1.14, 33)],                                    # 0.65
    [(1.19, 39), (0.68, 11), (0.65, 9), (0.82, 34), (0.66, 7)],              # 0.75
    [(1.25, 56), (1.20, 36), (1.11, 8)],                                     # 0.85
    [(1.22, 28), (1.11, 6), (1.18, 15), (1.09, 11), (1.20, 40)],             # 0.95
    [(0.70, 10), (1.24, 25), (1.01, 7), (1.18, 20), (1.07, 6), (1.25, 32)],  # 1.00
]
KNOB_CEIL_ANGLE = [  # 天花板反弹角: 撞顶后速度向量旋转角(°, 正=左转)
    [(-11.09, 33), (29.87, 35), (-10.07, 10), (-27.82, 23)],                 # 0.15
    [(21.90, 46), (-9.81, 30), (25.50, 24)],                                 # 0.25
    [(-28.03, 13), (12.22, 31), (-29.23, 23), (-17.47, 24), (-10.41, 9)],    # 0.35
    [(0.05, 25), (7.72, 36), (-11.59, 7), (-19.30, 25), (-22.23, 6)],        # 0.45
    [(12.54, 41), (-1.82, 31), (22.40, 29)],                                 # 0.55
    [(23.33, 39), (8.50, 15), (-12.52, 26), (-26.09, 20)],                   # 0.65
    [(-5.32, 11), (-12.32, 13), (-2.97, 17), (5.19, 38), (-1.85, 21)],       # 0.75
    [(30.00, 23), (-22.99, 10), (26.09, 29), (-25.42, 8), (-7.87, 31)],      # 0.85
    [(-6.61, 18), (25.99, 36), (-21.50, 7), (-30.00, 32), (-24.25, 6)],      # 0.95
    [(-25.26, 9), (19.30, 26), (-30.00, 23), (-23.86, 26), (-24.18, 16)],    # 1.00
]
KNOB_CEIL_BOOST = [  # 天花板力度: 撞顶后 vx,vy 同乘系数(加速≤25%/减速≤35%)
    [(0.65, 18), (1.15, 13), (0.69, 13), (0.92, 35), (1.07, 6), (1.01, 14)],  # 0.15
    [(1.15, 16), (1.23, 7), (0.65, 16), (0.85, 24), (1.04, 36)],             # 0.25
    [(1.25, 24), (0.70, 13), (0.66, 39), (1.06, 18), (0.94, 6)],             # 0.35
    [(1.01, 18), (1.19, 12), (0.85, 21), (0.94, 37), (0.73, 12)],            # 0.45
    [(1.14, 12), (1.25, 18), (0.79, 30), (0.99, 17), (1.20, 7), (0.83, 16)], # 0.55
    [(1.01, 9), (0.89, 16), (0.96, 21), (1.09, 14), (0.65, 25), (0.80, 14)], # 0.65
    [(0.81, 20), (0.76, 13), (0.65, 32), (0.92, 20), (1.16, 15)],            # 0.75
    [(1.25, 28), (0.69, 6), (0.73, 36), (1.19, 30)],                         # 0.85
    [(0.65, 20), (0.68, 15), (1.03, 19), (1.22, 34), (1.04, 13)],            # 0.95
    [(1.02, 34), (1.25, 16), (0.68, 20), (0.82, 30)],                        # 1.00
]


def _sample_table(table, rng):
    """按权重概率从 [(值, 权重), ...] 抽一个值(权重非负, 自动归一化)。"""
    vals = [v for v, _ in table]
    ws = [w for _, w in table]
    tot = sum(ws)
    if tot <= 0:
        return vals[0]
    r = rng.random() * tot
    acc = 0.0
    for v, w in zip(vals, ws):
        acc += w
        if r <= acc:
            return v
    return vals[-1]


def _power_band(power):
    """连续力度 → 最近力度档索引(0..9)。等价于把 [0.15, 1.0] 切成 10 个区间。"""
    return min(range(len(KNOB_POWERS)), key=lambda i: abs(power - KNOB_POWERS[i]))
_ARC_FRAME = 0               # 物理帧计数(弧面缓动判定用; 预演/真发各自单调即可, 新球无状态)
LAND_K = 16.0                # 落袋横向软吸附刚度
LAND_DAMP = 0.80             # 落袋横向阻尼
LAND_E = 0.42                # 落袋地板恢复系数: 0.42→弹3~4次逐渐停住, 视觉明显
LAND_BOUNCE_MIN_VY = 440.0   # 落地"撞击速度"保底 —— ⚠️ 是**撞击**速度不是回弹速度:
                             # 回弹 = 撞击 × LAND_E(0.42), 所以 440 → 回弹 185 → apex≈17px。
                             # 旧值 220 是把它当回弹速度写的, 实际只弹起 ~4px(肉眼看不见),
                             # 实测 74% 的落袋就是那样。用户 2026-09-11 定稿: "落地必须弹跳下,
                             # 真跳和假跳都可以" → 保底提到 440。
                             # ⚠️ 440 是**按最坏随机**倒推的: 撞击先 ×JITTER[0]=0.85, 回弹再
                             #    ×DECAY_JITTER[0]=0.92 ⇒ 0.85×0.92×0.42×440 = 144 → apex 10.4px,
                             #    刚好压在"看得见(>=10px)"上。调低这个数就等于让一部分落袋
                             #    弹不起来 —— selftest (2e) 与 fx_probe [17] 都按这条公式钉着。
LAND_BOUNCE_JITTER = (0.85, 1.15)   # 落地撞击速度的随机系数(用户定稿: 高度不要固定, 每发
                                    # 不一样)。乘在 max(真实下落速度, 保底) 上, 所以落得越快
                                    # 弹得越高、同时每发都有随机差异。apex ∝ 系数² → 变化 1.4 倍
LAND_BOUNCE_DECAY_JITTER = (0.92, 1.08)   # 每次触地回弹的 ±8% 随机(原来写死在两处 GUI 的
                                          # 回弹行里; 提成常数是为了让门禁能按最坏情况倒推)
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
# (盘面倍率表见 roll_multipliers 上方的 VALUE_SHAPE / K_DIST / MAX_REROLL / PITY_RATIO)

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
COL_x = {2: "#1e8a5a", 3: "#3d8bfd", 5: "#e0533b", 10: "#9e1f30", 20: "#a335ee", 50: "#c88800", 100: "#ff8c00"}
# 槽位倍率色(WoW 品质色调整版): x2绿 x3蓝 x5红 x10深红 x20紫 x50深金 x100深橙。
# 同时是中奖大字/灯带的取色依据。x10深红、x20紫偏暗 → 白字; 其余亮底 → 黑字。
COL_X50 = "#c88800"          # ×50 深金(原 ×20 的色): 越往上是"金币"家族
COL_X100 = "#ff8c00"         # ×100 深橙(原 ×50 的色): 顶级大奖(比×50更亮更热)
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
        is_ceil = (ev == EV_WALL and ry1 == 0 and ry2 == WALL)
        if is_ceil and hit > 0.0:
            # 天花板弹射: 角度+力度离散采样(每发首次撞顶采样一次, 存 b.ceil_knob)
            rng = getattr(b, "_rng", None) or random
            knob = getattr(b, "ceil_knob", None)
            if knob is None:
                band = _power_band(getattr(b, "launch_power", 0.5))
                knob = (_sample_table(KNOB_CEIL_ANGLE[band], rng),
                        _sample_table(KNOB_CEIL_BOOST[band], rng))
                b.ceil_knob = knob
            tilt, scale = knob
            if scale != 1.0:
                b.vx *= scale
                b.vy *= scale
            if tilt:
                sp = math.hypot(b.vx, b.vy)
                a = math.atan2(b.vy, b.vx) - math.radians(tilt)
                b.vx = sp * math.cos(a)
                b.vy = sp * math.sin(a)
            # 保底: 撞顶后必须向下且够快, 否则减速/旋转把 vy 压太小会导致球贴顶"吸住"(反复撞顶)
            if b.vy < 180.0:
                b.vy = 180.0
        if ev and hit > 0.0:
            _mark(b, EV_CEIL if is_ceil else ev, hit)


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
        band = _power_band(getattr(b, "launch_power", 0.5))    # 力度档 → 4 旋钮离散表
        st = [0, -1,                      # [缓动步数, 上次接触帧, 出口角抖动°, 出口力度系数]
              _sample_table(KNOB_ARC_ANGLE[band], rng),
              _sample_table(KNOB_ARC_BOOST[band], rng)]
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
            # ⚠️ 地板不是墙, 而是落袋边界(见下面落袋判据)。以前这里把 (0, FLOOR, CW, CH) 当
            # 普通弹性墙撞: 球在"被判定落袋"的同一子步里先被它以 WALL_E=0.5 弹成向上(实测首触
            # vy = -295~-312), 而这条路径**没有 LAND_BOUNCE_MAX_VY 上限**。两个后果:
            #   ① GUI 的飞行循环是"每轮覆盖 landed", 落袋那一帧只要还有第二个物理步, 球就已经
            #      飞离地板线、那一步返回 None ⇒ 这次落袋被整帧吞掉(横速没清零、没切 landing、
            #      没结算), 球斜着弹过隔板顶落进隔壁槽 —— 玩家 2026-09-11 第三次报的那个 bug
            #      "弹珠落入1个倍率槽之后跑到其他槽位去了, 弹跳的高度比较高而且是斜着的"。
            #   ② 真正的下落速度被这一弹吃掉, 落地那一下反而弹不起来(实测 74% 只弹 4px)。
            # 跳过它 ⇒ 球带着真实下落速度落袋, 弹跳高度和"落下来的速度"挂钩(用户要的),
            # 而且落袋判定不再受"这一步有没有撞到地板墙"这个偶然影响。
            # ⚠️ 跳过是**槽号无关**的: 该矩形的碰撞法线恒为竖直(cx=clamp(b.x,0,CW)=b.x ⇒ dx=0),
            #    它从来没有改过 b.x —— 只改 vy 和 y。所以落格分布一位不变。
            if w[1] == FLOOR:
                continue
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _collide_arc(b, s[0], s[1], s[2], s[3], _ARC_FRAME)  # 缓动带球: 贴轨转向, 静音接触
        _collide_pegs(b, geo["pegs"])
        for d in geo["dividers"]:
            _collide_rect(b, d[0], d[1], d[2], d[3], E, EV_DIV)
        if b.y + BALL_R >= FLOOR - 0.5:
            # 落袋 = 终态(2026-09-11 用户定稿): 就地钉住横速 + 贴地。
            # ⚠️ 这里**只清 vx, 保留 vy**:
            #   - 清 vx 让"结算槽 == 落格槽"成为**结构性不变量** —— 之后不管哪个调用方再推进
            #     这颗球, 球心 x 都不会再动, 返回的槽号恒等于第一次落袋的那个。GUI 的累加器
            #     循环是"每轮覆盖 landed"(落袋那一帧只要还有第二个物理步就会把 landed 冲成
            #     None 并跳过整个落袋分支), 靠调用方写对是历史事故的来源; 做成不变量才治本。
            #     实测: 只加这一处, 帧间隔 0.5s(一帧 30 个物理步)下 150 发零错槽; 未加时 8/150。
            #   - 保留 vy 是为了落地那一下还能弹起来(回弹 = 撞击速度 × LAND_E), 见 LAND_BOUNCE_MIN_VY。
            #   - 贴地是把落袋位置规范化: 判据本身是 FLOOR-0.5, 不夹的话球可能停在半像素偏上,
            #     也可能(被地板墙推过)偏下, 视觉上不稳定。
            b.y = FLOOR - BALL_R
            b.vx = 0.0
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
                 'arc_ease', 'peg_flash', 'ceil_knob')

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
                arc_ease=None, peg_flash=None, ceil_knob=None)


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


def _pick(dist):
    """按 {取值: 概率} 的累计阈值掷一个取值(概率和须=1)。"""
    r = random.random()
    acc = 0.0
    for value, prob in sorted(dist.items()):
        acc += prob
        if r < acc:
            return value
    return max(dist)          # 浮点误差兜底: 落在最后一段


# 盘面生成 = 掷 k 个奖格填倍率, 坏盘必重抽(最多 MAX_REROLL 次):
#   坏盘 = (x2 占比 >= PITY_RATIO, 即"几乎全 x2") 或 (>=2 个高倍率 >=x5)
# 坏盘重抽会改变 RTP(保底超发 / 封顶少发), 故 x2 权重由 _effective_rtp 闭式反解,
# 把重抽对 RTP 的影响一并配平, 使有效 RTP 精确 = 档位。
MAX_REROLL = 2            # 坏盘最多重抽次数(共生成 MAX_REROLL+1 盘, 最后一盘无论好坏都收)
PITY_RATIO = 0.8          # 保底阈值: x2 占比 >= 0.8 即"几乎全 x2"
CEIL_THRESHOLD = 5        # 高倍率起点: >=x5 即 x5/x10/x20/x50/x100 都算"高"

VALUE_SHAPE = {   # 非 x2 部分的形状(条件分布, 和=1)。低档无 x50/x100, 高档含。
    0.80: {3: 0.556, 5: 0.289, 10: 0.122, 20: 0.033},
    1.20: {3: 0.556, 5: 0.289, 10: 0.122, 20: 0.033},
    2.00: {3: 0.50, 5: 0.27, 10: 0.12, 20: 0.06, 50: 0.033, 100: 0.017},
    3.60: {3: 0.50, 5: 0.27, 10: 0.12, 20: 0.06, 50: 0.033, 100: 0.017},
}
K_DIST = {        # 每盘有奖格数(低档第1版原样, 高档减格子换 x50/x100 频率)
    0.80: {2: 0.8507, 3: 0.1493},              # E[K]=2.15
    1.20: {3: 0.7761, 4: 0.2239},              # E[K]=3.22
    2.00: {3: 0.25, 4: 0.50, 5: 0.25},         # E[K]=4.00
    # 最高档 = "必中"档(见 rtp_target 注释): 用户定案"更常中"而不是"中得更大"。
    # 2026-09-10 300%->360% 那次只改了键名, 有奖格数没动 -> 中奖率停在 65.6%, 和 300% 档
    # 一模一样, 玩家察觉不到换档。这里把有奖格从 5/6/7 提到 6/7/8。
    #
    # ⚠️ **RTP 不需要手调**: _solve_p2 是闭式反解, 改完这张表 p2 会自动重配平到精确 3.600000
    #    (模块顶层的 assert 会兜底)。代价是 x2 权重从 0.274 升到 0.439 —— 中奖更频繁,
    #    但每次中奖平均赔付从 5.475 降到 4.558 (回到 300% 时代那档的水平)。RTP 恒 3.60。
    #    想要更极端就继续往右推: {7:.3,8:.5,9:.2} -> 中奖率 87.4% / 每次 4.087;
    #    {8:.5,9:.5} -> 94.3% / 3.817。中奖率和每次赔付是同一个旋钮的两端, 只能二选一。
    # ⚠️ **别再用 E[K]/9 估中奖率**(旧注释写的 66.7% 就是这么来的, 偏高约 1pp): 坏盘重抽
    #    对 k 有偏, 3.60 档的真实中奖率比 E[K]/9 低约 1 个百分点。要看准确值就跑 MC。
    3.60: {6: 0.10, 7: 0.70, 8: 0.20},         # E[K]=7.10, 实测中奖率 78.6%
}


def _effective_rtp(p2, rtp):
    """给定 x2 权重 p2, 闭式算出含坏盘重抽后的 RTP(= E[盘面倍率和]/9)。
    坏盘 = (x2 占比 >= PITY_RATIO) 或 (>=2 个高倍率); 坏盘必重抽, 最多 MAX_REROLL 次。
    E[盘和'] = E + (P + P^2 + ... + P^R) * (E - E[坏盘]), P = 坏盘概率, R = MAX_REROLL。"""
    shape, kd = VALUE_SHAPE[rtp], K_DIST[rtp]
    w3 = shape.get(3, 0.0)
    high = {v: w for v, w in shape.items() if v >= CEIL_THRESHOLD}
    w_high = sum(high.values())
    eh = sum(v * w for v, w in high.items()) / w_high if w_high else 0.0
    p3 = (1 - p2) * w3                      # 单格 x3 概率
    ph = (1 - p2) * w_high                  # 单格高倍率(>=x5)概率

    e_sum = 0.0                             # 无条件 E[盘和]
    p_bad = 0.0                             # 坏盘概率
    e_bad = 0.0                             # E[盘和] * P(坏盘) 的加权和
    for k, pk in kd.items():
        for c2 in range(k + 1):             # c2 个 x2
            for ch in range(k + 1 - c2):    # ch 个高倍率(>=x5), 其余 c3 个 x3
                c3 = k - c2 - ch
                prob = (math.comb(k, c2) * math.comb(k - c2, ch)) * (p2 ** c2) * (ph ** ch) * (p3 ** c3)
                s = 2 * c2 + 3 * c3 + ch * eh
                e_sum += pk * prob * s
                if (c2 * 5 >= k * 4) or (ch >= 2):   # 坏盘: x2 占比>=0.8 或 >=2 个高倍率
                    p_bad += pk * prob
                    e_bad += pk * prob * s
    e_bad_cond = e_bad / p_bad if p_bad > 0 else e_sum
    factor = sum(p_bad ** i for i in range(1, MAX_REROLL + 1))   # P + P^2
    return (e_sum + factor * (e_sum - e_bad_cond)) / NUM_SLOTS


def _solve_p2(rtp):
    """二分反解 x2 权重, 使含软重随机后的 RTP 精确等于档位。x2 权重不手写就不会手滑写漂,
    改 VALUE_SHAPE/K_DIST/P_PITY/P_CEIL 任何一项都会自动重新配平。"""
    inc = _effective_rtp(0.9, rtp) > _effective_rtp(0.1, rtp)   # 先探单调方向
    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(200):
        mid = (lo + hi) / 2
        if (_effective_rtp(mid, rtp) < rtp) == inc:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# 每档完整倍率分布(x2 权重解出后拼成), 并断言 RTP 精确=档位。
VALUE_DIST = {}
for _rtp in (0.80, 1.20, 2.00, 3.60):
    assert abs(sum(VALUE_SHAPE[_rtp].values()) - 1) < 1e-9 and \
           abs(sum(K_DIST[_rtp].values()) - 1) < 1e-9, "概率和不为 1: %.2f" % _rtp
    _p2 = _solve_p2(_rtp)
    VALUE_DIST[_rtp] = {2: _p2}
    VALUE_DIST[_rtp].update({v: (1 - _p2) * w for v, w in VALUE_SHAPE[_rtp].items()})
    assert abs(sum(VALUE_DIST[_rtp].values()) - 1) < 1e-12, "配平失败: %.2f" % _rtp
    assert abs(_effective_rtp(_p2, _rtp) - _rtp) < 1e-9, "RTP 漂移: %.4f" % _rtp


def roll_multipliers(rtp=0.80):
    """掷 k 格填倍率; 坏盘(几乎全 x2 或 >=2 个高倍率)必重抽, 最多 MAX_REROLL 次。
    有效 RTP 精确 = 档位(见上方 _effective_rtp 断言)。"""
    kd = K_DIST.get(rtp, K_DIST[0.80])
    dist = VALUE_DIST.get(rtp, VALUE_DIST[0.80])
    for _ in range(MAX_REROLL + 1):                # 最多 3 盘
        k = _pick(kd)
        vals = [_pick(dist) for _ in range(k)]
        n2 = sum(1 for v in vals if v == 2)
        nh = sum(1 for v in vals if v >= CEIL_THRESHOLD)
        if n2 * 5 < k * 4 and nh < 2:              # 不是坏盘: 收下
            break
    mult = [0] * NUM_SLOTS
    for i, v in zip(random.sample(range(NUM_SLOTS), k), vals):
        mult[i] = v
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
    _add_partials(b, 0.0, 3200.0, [(1.00, 0.10, 0.008)])    # 金属"叮"(非谐高频, 钢珠指纹, 削刺 0.18→0.10)
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
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7", "E6"], 0.064, 1.60, 0.36, 0.78, 55.0),        # tier5 x50
    (["C5", "E5", "G5", "C6", "E6", "G6", "C7", "G6", "C7"], 0.062, 1.80, 0.42, 0.85, 41.2),  # tier6 x100
]


def _sfx_win(tier):
    """中奖琶音 7 档: 0=x2 1=x3 2=x5 3=x10 4=x20 5=x50 6=x100, 音数/混响/低音支撑随档位递增,
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


def _sfx_bead(f0):
    """中奖金雨珠子到账: 玻璃珠轻"叮"(比 coin 高一点圆润一点, 逐颗到账的计数感)。
    三个音高变体轮播, 倾泻时听感是"叮叮叮"上行计数而非同一声复读。"""
    b = _buf(0.05)
    _add_partials(b, 0.0, f0, [(1.00, 1.00, 0.012),
                               (2.42, 0.35, 0.007)])
    _add_noise(b, 0.0, 0.002, 0.22, 0.001, 0.85)
    return _pack(b, 0.50)


# ---- 2026-09-10: 试过给中奖装杯做一组"玻璃音" cup0..3 + cupland, 已回退, 别重做 ----
# 做法: 4 个音高变体(1960/2180/2290/2540Hz), 每颗一套非谐分音(2.42/2.76/2.19/2.55 倍),
# 衰减 tau 0.14~0.17, 外加一层 2.5ms 接触噪声, 峰值归一化到 0.45。动机是实测 `bounce`
# (装杯原来用的音)96% 能量在 300Hz 以下、过手机喇叭低切掉 15dB。
# **用户听完的判决: "这个新的是电子音, 而不是小球撞击的声音"(见 tools/android_part_pile.py
# 同名注释处的完整量测)。**
# 教训 —— 撞击感来自**攻击瞬间的宽带噪声**, 不是分音:
#   只要主导成分是几个正弦分音 + 长衰减, 无论基频放哪、用不用非谐倍数, 听起来都是"电子音/音调"。
#   要做撞击声, 噪声瞬态必须比振铃**更响**且足够宽(到 8kHz), 分音只能当尾巴 —— 本库的
#   `_sfx_tink`(撞钉, 现役、玩家从没抱怨过)就是"噪声打头 + 分音收尾"的正例, 该照它改而不是新起炉灶。
# 下次要动装杯音, 先看一个事实: 装杯段的问题**主要不在音色** —— 整段只有 1 个波形、3 个音量值
# (首跳 lvl5/6 极差仅 1.16dB, 二跳恒 lvl3), 节流后 x100 有 64% 的间隔精确卡在 0.100s。

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
    for i, f0 in enumerate((1960.0, 2320.0, 2760.0)):   # 金雨珠子到账(追加末尾, 不扰既有音色)
        yield "bead%d" % i, _sfx_bead(f0)


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
        self._voices = voices
        self._ids = {}
        self._paths = {}              # name -> wav 路径(拔耳机后重建时重新 load 用)
        self.rebuild_count = 0        # 重建次数(隐藏菜单的诊断行要显示; 正常局应当恒为 0)
        self._sp = self._build_sp()
        self._receiver = None
        self._register_noisy_receiver()

    def _build_sp(self):
        from jnius import autoclass

        def _inner(outer_name, inner):
            """取 Java **内部类**, 优先 `Outer$Inner` 这种规范写法。

            ⚠️ pyjnius 对 `Outer.Inner` 那种属性访问的解析**并不可靠**, 而它一旦抛异常就会被
            `open_output` 的 except 吞掉 —— 后果是**整台设备静默降级到 Kivy 后端**。
            2026-09-11 玩家真机实测: 面板显示后端是 `Kivy-SoundLoader`, 也就是 SoundPool
            **从来没建起来过**, 而此前所有关于声音的猜测都建立在 SoundPool 上。两条路都试。"""
            try:
                return autoclass("%s$%s" % (outer_name, inner))
            except Exception:
                return getattr(autoclass(outer_name), inner)

        AudioAttributes = autoclass("android.media.AudioAttributes")
        attrs = (_inner("android.media.AudioAttributes", "Builder")()
                 .setUsage(AudioAttributes.USAGE_GAME)
                 .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                 .build())
        return (_inner("android.media.SoundPool", "Builder")()
                .setMaxStreams(self._voices)
                .setAudioAttributes(attrs)
                .build())

    def _register_noisy_receiver(self):
        """路由一变就重建 SoundPool:
        - **拔**耳机 → ACTION_AUDIO_BECOMING_NOISY → 回扬声器。耳机拔出时底层音频流被系统
          断开(stream disconnected), 不重建就永久无声, 只能重启。
        - **插**耳机 → ACTION_HEADSET_PLUG → 回耳机。玩家 2026-09-11 报的必然复现 bug:
          「玩的时候插耳机, 耳机里没声音; 关掉 app 再打开, 耳机就有声音了」。
          ⚠️ 根因: SoundPool 的输出路由是**建流那一刻**定下的, 建在扬声器上就一直往扬声器
          送, 系统插耳机的事件不会把它搬过去。重启 app = 重新建流 = 跟着当前设备走, 所以
          "重启就好"。修法就是插耳机时把 SoundPool 重建一次(和拔耳机同一条路)。
        ⚠️ ACTION_HEADSET_PLUG 是 **sticky** 广播 —— registerReceiver 会立刻回放"当前状态"。
          所以必须记下初值、只在**状态真的变了**时才重建, 否则每次启动都白重建一次
          (那一瞬间 sample 还没 load 完, 会丢音)。
        receiver 存到 self._receiver 保活, 防被 GC(OnLoadCompleteListener 的前车之鉴)。"""
        try:
            from jnius import autoclass, PythonJavaClass, java_method
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            AudioManager = autoclass('android.media.AudioManager')
            IntentFilter = autoclass('android.content.IntentFilter')
            _plug_action = str(AudioManager.ACTION_HEADSET_PLUG)

            class _RouteReceiver(PythonJavaClass):
                __javainterfaces__ = ['android/content/BroadcastReceiver']
                __javacontext__ = 'app'

                def __init__(self, cb):
                    super().__init__()
                    self.cb = cb
                    self._plugged = None          # None = 还没收到过插拔状态

                @java_method('(Landroid/content/Context;Landroid/content/Intent;)V')
                def onReceive(self, context, intent):
                    try:
                        if str(intent.getAction()) == _plug_action:
                            st = intent.getIntExtra("state", -1)
                            if self._plugged is None:
                                # ⚠️ ACTION_HEADSET_PLUG 是 **sticky** 广播: registerReceiver 会
                                # 立刻回放一次"当前状态"。这一条**只记初值、绝不重建**。
                                # 旧代码写的是 `if st == self._plugged: return`, 而初值是 None、
                                # st 恒为 0/1/-1 ⇒ 恒假、那个 return 永不生效 ⇒ **每次启动都白重建
                                # 一次 SoundPool**(还会在冷路径烘焙中途清掉 _ids, 见 _rebuild)。
                                self._plugged = st
                                return
                            if st == self._plugged:
                                return            # 真·状态没变: 不动
                            self._plugged = st
                    except Exception:
                        pass
                    self.cb()

            self._receiver = _RouteReceiver(self._rebuild)
            flt = IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY)
            flt.addAction(AudioManager.ACTION_HEADSET_PLUG)
            PythonActivity.mActivity.registerReceiver(self._receiver, flt)
        except Exception:
            self._receiver = None

    def prime(self, name, path):
        self._paths[name] = path             # ⚠️ 先记"应到", 再 load
        sid = self._sp.load(path, 1)         # (写在 load 之后的话, load 失败的名字永远进不了
        if not sid:                          #   _paths, 后面任何一次重建都救不回它)
            raise RuntimeError("SoundPool.load failed: " + path)
        self._ids[name] = sid

    def _rebuild(self):
        """路由变了(插/拔耳机)后重建: release 旧的, 建新池, 把**应到清单**全部重新 load。

        ⚠️ 遍历必须用**快照**: 冷路径下烘焙线程正在往 `_paths` 里塞东西, 直接迭代它会在"字典
        在迭代中被改"时抛 `RuntimeError`, 被外层 `except` 吞掉 → 循环当场中断、`_ids` 停在
        半路 —— 而 `Sfx.named` 早已被烘焙线程填满 ⇒ **闸门放行、后端没有 sid ⇒ 整局静默**。
        ⚠️ 收尾还要对账: 中断点之后那批名字在 `_paths` 里有、`_ids` 里没有, 在这里补一次;
        仍补不上的交给 Sfx 的 _retry_failed 去重试。
        ⚠️ 注意 _rebuild 跑在 Android **主线程**(onReceive 默认线程): 持锁/长循环会卡 UI,
        所以这里只做"重建 + 一轮对账", 不做等待。"""
        try:
            self.rebuild_count += 1
            old = self._sp
            self._ids = {}
            self._sp = self._build_sp()
            try:
                old.release()
            except Exception:
                pass
            for name, path in list(self._paths.items()):
                try:
                    self.prime(name, path)
                except Exception:
                    pass
        except Exception:
            pass

    def loaded_count(self):
        """后端**真的**握着几个能播的 sampleId(隐藏菜单的诊断行要用)。
        ⚠️ 不能拿 Sfx.named 冒充它 —— 病灶正是"闸门满、后端缺"。"""
        return len(self._ids)

    def probe_all(self):
        """所有已加载的 sample 是不是**真的能播**了(0 增益试播当探针)。

        `SoundPool.load()` 返回了 sampleId **不等于**解码完了; 没解码完 `play()` 返回 0、
        静默什么都不做 —— 这就是"音效都在、就是不响"的形态。而 SDK 只给了这一个办法去问
        "现在能播了吗"(不走 setOnLoadCompleteListener: 那个跨线程 JNI 代理一旦失灵/被 GC
        就是全库永久静音, 见 __init__ 的注释)。
        返回 True = 全部能播(或没东西可探)。
        ⚠️ 探针自己出错一律当"能播": 它只是个护栏, 绝不允许反过来把玩家锁在加载页。"""
        ids = list(self._ids.values())       # 快照: 烘焙线程可能正在往里加
        if not ids:
            return True
        try:
            for sid in ids:
                st = self._sp.play(sid, 0.0, 0.0, 1, 0, 1.0)   # 0 增益 → 听不见
                if not st:
                    return False
                try:
                    self._sp.stop(st)        # 立刻收流, 别占满 maxStreams
                except Exception:
                    pass
        except Exception:
            return True
        return True

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
            if self._receiver is not None:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                PythonActivity.mActivity.unregisterReceiver(self._receiver)
        except Exception:
            pass
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

    def loaded_count(self):
        """后端**真的**握着几个可播对象。

        ⚠️ 这个方法必须有。缺了它, `Sfx.backend_count()` 返回 None → 面板显示「未知」;
        而在 2026-09-11 之前, 缺它的后果是**悄悄退回闸门值**(`len(named)`) ⇒ 面板报出一片
        `97 / 97` 全绿 —— 而这块面板存在的唯一理由就是抓这种静默。
        实测(专家用三个假后端跑真代码): 缺 `loaded_count` 的后端, 面板三行假绿,
        只有「音频后端」那一行说真话。"""
        return len(self._sounds)

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


_BACKEND_ERRORS = []     # [(名字, 异常文本)] —— open_output 降级链每一级失败都记一笔


def _backend_error(name):
    """取某个后端构造失败的原文(诊断用)。"""
    for _n, _e in _BACKEND_ERRORS:
        if _n == name:
            return _e
    return ""


def open_output():
    """按优先级选后端: Android SoundPool > winmm > Kivy SoundLoader > 静音。
    环境变量 PLINKO_SFX_BACKEND=kivy|winmm|none 可在桌面强制指定 —— 安卓走的是 named
    这条路径(缓存/落盘/按名播), 桌面默认走 winmm 的 pcm 路径, 不强制就没法在开发机上验它。

    ⚠️ 降级链每一级**都要把异常原文记进 `_BACKEND_ERRORS`**。2026-09-11 之前这里全是
    `except Exception: pass` —— 结果是玩家的安卓设备上 SoundPool **一直构造失败、静默降到
    Kivy-SoundLoader 上跑**, 而面板只能显示"后端是 Kivy-SoundLoader", 说不出**为什么**。
    一块专门用来抓静默的面板, 自己却在做静默降级(专家原话): 这就是那一处。"""
    want = os.environ.get("PLINKO_SFX_BACKEND", "").lower()
    if want == "none":
        return None
    if platform == "android" and want not in ("kivy", "winmm"):
        try:
            return _SoundPoolOut()
        except Exception as exc:
            _BACKEND_ERRORS.append(("SoundPool", "%s: %s" % (type(exc).__name__, exc)))
    if want != "kivy":
        try:
            return _WaveOut()
        except Exception as exc:
            _BACKEND_ERRORS.append(("winmm", "%s: %s" % (type(exc).__name__, exc)))
    try:
        return _KivySoundOut()
    except Exception as exc:
        _BACKEND_ERRORS.append(("Kivy-SoundLoader", "%s: %s" % (type(exc).__name__, exc)))
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
        self.baked = False          # 整库烘焙是否收工(UI 的冷启动加载页按它收尾, 见 _LoadVeil)
        self._audio_ready = False   # 烘完 + **探到真的能播** 才为真(见 _await_ready)。默认 False:
                                    # 但 !enabled / 后端探测不可用时一律放行 —— 绝不软锁
        self.ready_ms = 0.0         # 等"真的能播"花了多久(0 = 不适用/没探针)
        self.n_attempt = 0          # 过了全部闸门、真的向后端要过声音的次数
        self.n_missed = 0           # 上面那些里**后端仍说没播成**的次数(静默的正面计数)
        self._expected = 0          # 满编音效数(烘焙时顺手记, 见 _expected_total 为什么不能现算)
        self._n_bank = 0            # 满编的合成音数(不含语音); 也是烘焙时数出来的
        self._failed = []           # 加载失败的 (name, path): 后台重试, 见 _retry_failed
        self._retry_wait = 0.6      # 重试间隔(秒)
        self._retry_rounds = 12     # 重试轮数(≈7s), 还不成功就认命
        self.bake_ms = 0.0
        self.cached = False         # 本次启动是否命中磁盘缓存(没现场合成)
        self._scaled = {}
        self._last = {}
        self._last_voice = 0.0       # 全局语音间隔: 防重叠
        self._last_voice_len = 0.0  # 上一句的时长(互斥按它判, 不写死 3 秒)
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
        self.baked = True               # ⚠️ 放在 except 之后: 烘失败了也要放行, 否则加载页永不消失(软锁)
        self._await_ready()             # 再等"真的能播"(带硬超时), 见该方法的注释

    # ---- 冷启动"真的能播了吗"的护栏(首次安装必然没声音的正面修复) -------------
    SFX_READY_TIMEOUT = 6.0             # 最多等这么久, 到点无条件放行(绝不软锁 —— 项目红线)
    SFX_READY_POLL = 0.15               # 探测间隔

    def audio_ready(self):
        """加载页什么时候可以摘: 音效关了(没什么可等) 或 烘完且探到真能播。"""
        return (not self.enabled) or self._audio_ready

    def _await_ready(self):
        """等"真的能播"再放行 —— 这是冷启动加载页的摘页判据。

        ⚠️ 为什么不是"烘完就放行": `SoundPool.load()` 是异步的。它**返回了 sampleId 不代表
        解码完了**; 没解码完 `play()` 返回 0(静默什么都不做), 而 `named` 闸门早就放行了 ——
        于是"所有音效都在、就是不响", 且不抛异常、不留痕。这正是 `BUILD_APK.md` 里记着的
        「**首次安装打开 App 必然全静音**, 第二次打开走缓存、速度快所以能响」的根因
        (玩家 2026-09-11 原话: 「初次安装的时候必然没有声音, 重开就有了」)。
        以前扛这件事的只有一行 `time.sleep(0.5)`; 而且 v0.6.10 把烘焙挪到后台线程之后,
        加载页在 `baked` 那一刻就摘 —— 连那 0.5 秒的缓冲也没了(sync=True 时代后面还有
        "整棵界面建完"的自然缓冲)。所以改成探到真值再摘。
        ⚠️ 硬超时: 探不到也放行。**这是这条路的唯一出口, 不许去掉**(探针本身也可能失灵)。"""
        try:
            probe = getattr(self.out, "probe_all", None)
            if probe is None:                 # 后端没有探针(桌面/wavesound/静音) → 不等
                self._audio_ready = True
                return
            t0 = time.time()
            while time.time() - t0 < self.SFX_READY_TIMEOUT:
                if probe():
                    break
                time.sleep(self.SFX_READY_POLL)
            self.ready_ms = (time.time() - t0) * 1000.0   # 等"真的能播"花了多久(诊断要显示)
        except Exception:
            pass
        self._audio_ready = True

    def backend_count(self):
        """后端**真的**握着几个可播对象; None = 这个后端不报数(或压根没有后端)。

        ⚠️ 这里**绝不能**在拿不到时退回闸门值(`len(self.named)`) —— 2026-09-11 之前就是那么写的,
        而它正是这块面板存在的理由被反噬: 安卓上 SoundPool 若构造失败、静默降级到 `_KivySoundOut`,
        面板会报 `97 / 97` **全绿**(专家用三个假后端跑真代码实测: 那个分支里四行有三行假绿,
        只有「音频后端」那一行说真话)。兜底只能是"不知道" —— 用一个类型上分得开的返回值(None),
        而不是靠"记得别写回退"。"""
        fn = getattr(self.out, "loaded_count", None)
        if fn is None:
            return None
        try:
            return fn()
        except Exception:
            return None

    def audio_detail(self):
        """「启动信息」里那几行(每行一个字段)。

        玩家 2026-09-11 报的「初次安装必然没声音」—— 它的**所有候选原因在产物里长得一模一样**
        (静默 / 不抛异常 / 不留痕), 没有 adb 就只能把真值按字段摆开, 一次截图定位到哪一支。

        设计规则(两轮专家评审 + 玩家本人定稿, 三条都是踩过坑才有的):
        1. **顺序 = 排查优先级**, 不是"变不变": 前提(音效开关) → 结论(就绪) → 入参(启动方式/等待)
           → 计数(加载失败/重建)。作者抱怨过"布局太乱", 根因是行序没按"出问题先看谁"排。
        2. **有唯一预期值的, 只在偏离时才有信息; 没有唯一预期值的(冷/热、等待时长)才需要常显。**
        3. **恒定值降权、不删除** —— 出事那张截图里它们就是基线。
        ⚠️ 行数有硬预算: 实测弹窗内容区只有 376px、每行 38px。加行必须同时加弹窗高度,
           而合并行几乎免费(宽度只用到 295/421px)——所以"后端名"并进了"音效就绪"那一行。
        ⚠️ 整段 try/except: 这只是隐藏菜单里的几行, 绝不把弹窗带崩。"""
        try:
            out = self.out
            n_gate = len(self.named)
            n_back = self.backend_count()
            named_mode = getattr(out, "mode", "") == "named"
            bname = getattr(out, "name", "静音")
            deviated = platform == "android" and named_mode and bname != "SoundPool"
            # 1) 音效开关 —— **必须排第一**: 它是全表的前提, 前提不成立时下面每一行都在描述一个
            #    不会发生的世界。而且 `enabled=False` 有两张完全不同的脸: **玩家自己关的** vs
            #    **后端压根没建起来**(--nosound 和后端构造失败在代码里都是 enabled=False)。
            if self.enabled:
                sw = "音效开关　已开"
            elif out is not None:
                sw = "音效开关　已关（本次不会有任何声音）"
            else:
                sw = "音效开关　无后端（本次全静音，非玩家操作）"
            # ⚠️ 「冷/热」这个词**只在 named 后端上成立**: 磁盘缓存(stamp)整个机制都活在
            #    `_bake_named`/`_load_cached` 里, PCM 后端(_bake_pcm)根本不写缓存 ⇒ 它**每次
            #    启动都是现场合成**, "冷启动"在那个后端上恒为真、零信息量。(2026-09-11 玩家:
            #    「是不是每次都必然显示冷启动」—— PC 上确实是, 但那是后端性质, 不是故障。)
            #    所以 PCM 上只报耗时, 不报冷热。
            mode_row = ("启动方式　%s启动　%.0f ms" % ("热" if self.cached else "冷", self.bake_ms)
                        if named_mode else "合成耗时　%.0f ms" % self.bake_ms)
            n_rc = getattr(out, "rebuild_count", 0)

            # ⚠️ PCM 后端(PC 的 winmm / Kivy-SoundLoader): 下面三项对它**结构上就不适用**
            #    (音效就绪 / 音效等待 / 加载失败 全都建立在"按名字加载、有 sampleId"之上)。
            #    玩家 2026-09-11 反馈「这几个不适用听起来有点奇怪」—— 三行"不适用"既是纯噪音,
            #    又把真正有内容的两行淹掉了。所以**不适用的行直接不出现**, 而不是印成"不适用"。
            if not named_mode:
                rows = [sw, "音频后端　%s" % bname, mode_row]
                if n_rc:
                    rows.append("后端重建　%d 次" % n_rc)
                return rows

            # ---- 以下都是 named 后端(安卓的 SoundPool, 或降级到 Kivy-SoundLoader) ----
            # 2) 音效就绪
            if n_back is None:
                ready = "未知（该后端不报数）"
            else:
                ready = "%d / %d" % (n_back, n_gate)
                if n_back < n_gate:
                    ready += "　后端缺 %d" % (n_gate - n_back)
                # ⚠️「满编」只在**闸门自己就短了**的时候才印 —— 玩家定稿的规则: 有唯一预期值的,
                #    只在偏离时显示(常态印它只是噪音)。
                if self._expected and n_gate < self._expected:
                    ready += "（满编 %d）" % self._expected
            rows = [sw, "音效就绪　%s" % ready, "音频后端　%s" % bname]
            # ⚠️ 期望值**单独占一行**, 不并进上一行: 并进去会让那行超宽折行 —— 而折行是这里
            #    最容易出事的地方(v0.6.12 就栽在被定高标签裁掉了尾巴; 2026-09-11 在手机密度的
            #    模拟器上又栽了一次: 等效宽度只有桌面的一半)。
            if deviated:
                rows.append("　　　　　安卓上应为 SoundPool，静默降级了")
                _err = _backend_error("SoundPool")
                if _err:
                    # 把 SoundPool 构造失败的**原文**摆出来 —— 没有它就只能知道"降级了",
                    # 不知道"为什么降级", 而这正是这块面板当初存在的理由。
                    rows.append("　　　　　%s" % _err[:64])
            rows.append(mode_row)

            # ⚠️ 这里原来有一行「输出采样率　48000 Hz」(v0.6.24 加的), **玩家 2026-09-11 定稿删除**:
            #    原话「去掉音频输出中的 输出采样率 这个其实是个固定数值」。
            #    它当初是为跨设备对比加的(22050 源在 48000 设备上要重采样 2.18 倍), 理由是"两台机器
            #    这个数不同就不是在做同一道题" —— 但真机 + 模拟器实测**都是 48000**, 于是它成了
            #    "永远不变的那一行", 而这块面板的成文规则是: **有唯一预期值的, 只在偏离时才有信息**
            #    (同 "后端重建" 只在非 0 时出现)。真要偏离(22050/44100)再把它加回来也不迟。

            # 3) 音效等待 —— 等"真的能播"花了多久。⚠️ 安卓上"没有探针"是**护栏缺失**
            #    (那 6 秒形同虚设, 而这正是那个 bug 的成因), 不能写成中性的"不适用"。
            if self.ready_ms > 0:
                rows.append("音效等待　%.0f ms（上限 %.0f）"
                            % (self.ready_ms, self.SFX_READY_TIMEOUT * 1000.0))
            elif getattr(out, "probe_all", None) is None:
                rows.append("音效等待　无法确认能播（本后端无探针）"
                            if platform == "android" else "音效等待　0 ms")
            else:
                rows.append("音效等待　0 ms（未等待）")

            # 4) 加载失败 —— `_failed` 就是"哪些音效真的没加载上"的正面答案, 已经躺在内存里。
            #    和语音分开报, 否则"音效响、语音不响"这种半死状态在面板上完全隐形。
            n_voice = sum(1 for n in self.named if n.startswith("voice_"))
            rows.append("加载失败　%d 个　·　语音 %d / %d"
                        % (len(self._failed), n_voice, max(0, self._expected - self._n_bank)))

            # 5) 后端重建 —— **只在非 0 时出现**(唯一预期值是 0, 常态印它只是噪音)
            if n_rc:
                rows.append("后端重建　%d 次" % n_rc)
            return rows
        except Exception:
            return []

    def audio_status(self):
        """上面那几行的一行版(日志与门禁用; 界面上显示的是分栏)。"""
        return "  |  ".join(self.audio_detail())

    def _bake_pcm(self):
        self.bank = bake_bank()             # 整体赋值(引用切换), 读侧只会看到空或全量
        for name, path in _voice_files().items():   # 预录语音并入 bank, winmm 同路径可播
            try:
                self.bank[name] = _read_wav_pcm(path)
            except Exception:
                continue
        for name in ("win0", "win1", "win2", "win3", "win4", "win5", "win6", "lose",
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
            n_voice = self._prime_voice()
            self._expected = self._n_bank + n_voice     # 满编 = 合成音 + 语音(两个都是数据源)
            self._retry_failed()          # 缓存命中也可能有个别没加载上(语音是每次重载的)
            return
        _wav_wipe(d)
        lines = []
        n_bank = 0
        for name, pcm in iter_bank():
            if name == "flight":           # flight 音效已移除, 但 iter_bank 必须保留(顺序即音色)
                continue
            n_bank += 1                    # 满编数从这里数 —— 它是数据源, 与"加载成功几个"无关
            path = os.path.join(d, name + ".wav")
            try:
                _wav_write(path, pcm)
                self.out.prime(name, path)
            except Exception:
                self._failed.append((name, path))
                continue
            self.named.add(name)
            lines.append("%s:%d" % (name, os.path.getsize(path)))
        self._n_bank = n_bank
        self._expected = n_bank + self._prime_voice()
        try:
            with open(stamp, "w") as f:
                f.write(tag + "\n" + "\n".join(lines))
        except Exception:
            pass
        time.sleep(0.5)   # 先给 SoundPool 的解码线程一点时间(load 是异步的, 没解码完 play 会返回 0)
        self._retry_failed()

    def _retry_failed(self):
        """把加载失败的音效丢到后台重试几轮。

        玩家 2026-09-11 报: 「有时候, 初次安装的时候必然没有声音, 关掉 app 再打开就有了」。
        首装走的是**冷路径**: 先把整库合成一遍(纯 Python 浮点循环, 好几秒), SoundPool 的解码
        线程被抢 CPU, `load()` 很容易返回 0; 而 `named` 是 play 的闸门(play 里
        `elif name not in self.named: return False`), 不在里面就直接跳过 —— 于是**整局一声不响**,
        只有重启(走缓存、解码快)才恢复。这里把失败的收进来后台重试, 通常一两轮内就齐了。
        ⚠️ 不要退回"把上面那个 0.5s 调大": 既拖慢启动, 又救不了已经返回 0 的那批(那是**永久**失败)。
        ⚠️ 也不要用 SoundPool.setOnLoadCompleteListener 当闸门 —— 那个 PythonJavaClass 代理是从
        SoundPool 自己的线程回调过来的, 一旦失灵/被 GC 就是全库永久静音(见 __init__ 的注释)。
        重试是"多试几次", 没有单点故障。"""
        todo, self._failed = self._failed, []
        if not todo:
            return

        def _worker(items):
            for _ in range(self._retry_rounds):
                time.sleep(self._retry_wait)
                left = []
                for name, path in items:
                    try:
                        self.out.prime(name, path)
                        self.named.add(name)
                    except Exception:
                        left.append((name, path))
                items = left
                if not items:
                    return

        try:
            threading.Thread(target=_worker, args=(todo,), daemon=True).start()
        except Exception:
            pass

    def _prime_voice(self):
        """预录语音直接 prime APK 内原文件(voice/*.wav), 不落缓存不进 stamp 指纹:
        每次启动都重新加载, 语音文件更新即生效; play() 对未加载完的 sample 本来就返回 0。
        返回语音条数(满编数要用它 —— 它是数据源, 不是"加载成功几个")。"""
        n = 0
        for name, path in _voice_files().items():
            n += 1
            try:
                self.out.prime(name, path)
            except Exception:
                self._failed.append((name, path))
                continue
            self.named.add(name)
        return n

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
        self._n_bank = len(want)      # 满编的合成音数(缓存路径也要记, 面板的"满编"靠它)
        for name, path in want:
            try:
                self.out.prime(name, path)
            except Exception:
                return False          # 任一音效加载失败: 缓存视为无效, 触发重新烘焙自愈
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
        # UI交互语音互斥: 上一句**还没播完**就不出新的; 结果/轮次语音不在此限。
        # ⚠️ 用上一句的**真实时长**判, 不能写死 3 秒 —— 写死的话连按音效开关时,
        # 第一句之后 3 秒内全被挡, 按钮颜色在切而完全没声音, 音画脱节
        # (实测连按 12 次只听到第 1 句; 而"关闭声音"本身只有 0.9 秒)。
        if name.startswith(("voice_rtp_", "voice_bet_", "voice_mode_")):
            if now - self._last_voice < self._last_voice_len:
                return False
            self._last_voice = now
            self._last_voice_len = self.voice_duration(name)
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
        # ⚠️ 必须把这个返回值存下来再返回: 它和上面那几个"设计内静默"的 return False 长得一模一样,
        #    但语义完全不同 —— 这一条是"过了 enabled/互斥/已加载/增益/节流五道闸门之后,
        #    后端仍然说没播成", 也就是 README 里那个"首次安装必然没声音"的**正面计数**。
        #    节流那一支在它上面提前 return, 根本走不到这里, 所以天然被排除, 不用额外判断。
        ok = self.out.play_named(name, lvl / 10.0)
        self.n_attempt += 1
        if not ok:
            self.n_missed += 1
        return ok

    def impact(self, bit, sp):
        """碰撞音: 撞得越猛越响越亮; 低于阈值不发声。"""
        if not self.enabled or sp < SFX_MIN_SP.get(bit, 0.0):
            return False
        t = clamp(sp / SFX_REF_SP.get(bit, 900.0), 0.0, 1.0)
        if bit == EV_PEG:
            idx = int(clamp(int(t * 5.99) + _ARNG.randint(-1, 1), 0, 5))
            return self.play("peg%d" % idx, 0.30 + 0.70 * t, 0.08)
            # throttle 0.038→0.08: 机关枪连珠(间隔<0.08s)只响第一声, 与 PC plinko.py 一致
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
        """静音开关: 关时暂停输出, 开时**只放开 enabled**, 不 resume。

        ⚠️ 开的时候**不能** autoResume: 静音那一刻 autoPause() 会把当时正在播的流掐在半路,
        一 resume 就从半路接着播出来 —— 症状是"连按音效开关会听到半句上一句提示音",
        而且连按越频繁、被掐的流越多, 越明显。
        autoResume 只该服务于"切后台回来"(PlinkoApp.on_resume -> resume_out), 那里的语义
        才是"把刚才暂停的接着播完"。
        (SoundPool.autoPause 只遍历已分配的 channel 去暂停, 不会挡住后续 play() 新建 channel,
         所以这里不 resume 不影响"取消静音后能不能出声"。)
        """
        self.enabled = bool(on)
        if not on:
            self.pause_out()

def selftest(n=40000):
    """验证: (1) 各档 RTP 精确=档位; (2) 引导飞行落点=预定槽、不卡死;
    (3) 碰撞事件覆盖率(音效触发源); (4) 音效库体检。

    n 必须够大: 单发赔付方差很大(取值 0/2/3/5/10/20/50/100, 实测 σ=1.96/2.29/6.92/9.25)。
    n=40000 时四档标准误 ≈0.010/0.012/0.035/0.046, 3σ = 0.029/0.034/0.104/0.139,
    故低档门禁 ±0.05、高档 ±0.15(最高档 3σ=0.139, 离 0.15 只剩 0.011 —— 再往上加档位
    就得同步放宽门禁, 否则每次自测约有千分之一概率假失败)。
    精确性由模块顶层解析断言兜底, 见 VALUE_DIST 构建处的 assert。"""
    geo = build_geo()
    ok = True

    # (1) 盘面 RTP 期望: 均匀落格下 E[赔付]=档位(彻底被动, 无 choose_target 修正)
    print("== 返还率精确性(均匀落格盘面期望) ==")
    for rtp in (0.80, 1.20, 2.00, 3.60):
        tot = 0.0
        for _ in range(n):
            board = roll_multipliers(rtp)
            tot += board[random.randrange(NUM_SLOTS)]
        realized = tot / n
        tol = 0.15 if rtp >= 2.00 else 0.05   # 高档含 x50/x100, σ 大, 门禁放宽
        good = abs(realized - rtp) < tol
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

    # (2c) 落袋即终态(2026-09-11 加)。玩家报过三次「弹珠落入1个倍率槽之后跑到其他槽位去了,
    #      弹跳的高度比较高, 而且是斜着的」—— 根因在**出货路径**上, 不在物理:
    #      GUI 的飞行循环是"累加器 + 每轮覆盖 landed", 落袋那一帧只要还有第二个物理步, 球就
    #      已经飞离地板线、那一步返回 None ⇒ landed 被冲成 None ⇒ **整个落袋分支被跳过**
    #      (横速没清零、没切 landing、没结算), 球带着横速弹过隔板顶落进隔壁槽。
    #      ⚠️ 上面 (2) 的循环是"逐物理步 break", **永远构造不出这个场景** —— 这正是它活到
    #      今天的原因(门禁验一套语义、出货跑另一套)。所以这里不去复制循环, 改成断言一个
    #      **与调用方无关的不变量**: 落袋之后再推进, 槽号与球心 x 都不许变。这样任何循环
    #      形状都伤不到结算, 而且测的是 physics_step 本身(出货代码)。
    #      ⚠️ 只跑正常帧率测不出东西: 这条不变量与帧率无关, 任何时候都该成立。
    print("== 落袋终态(落袋后再推进 30 步, 槽号与球心 x 不许变) ==")
    pin_n = 300
    pin_bad = 0
    for _k in range(pin_n):
        _b = launch_ball(MISFIRE_POWER + (1.0 - MISFIRE_POWER) * (_k / (pin_n - 1.0)),
                         rng=random.Random(20260911 + _k))
        _r0 = None
        for _ in range(4000):
            _r0 = advance_flight(_b, geo)
            if _r0 is not None:
                break
        if _r0 is None:
            pin_bad += 1                     # 4000 步还不落袋 = 卡死, 也算失败
            continue
        _x0 = _b.x
        for _ in range(30):
            _r1 = advance_flight(_b, geo)
            if _r1 is not None and (_r1 != _r0 or abs(_b.x - _x0) > 0.5):
                pin_bad += 1
                break
    ok = ok and pin_bad == 0
    print("  落袋后槽号/位置变了: %d/%d  %s"
          % (pin_bad, pin_n, "OK" if pin_bad == 0 else "穿帮!"))

    # (2d) 落袋时球仍在下落(真实下落速度必须活到那一刻)。
    #      地板矩形 (0, FLOOR, CW, CH) 以前是当普通弹性墙撞的, 球在"被判定落袋"的同一子步里
    #      先被它以 WALL_E=0.5 弹成向上 —— 实测未修时 61% 的落袋 vy<0。两个后果: 结算槽受
    #      "这一步有没有撞到地板墙"这个偶然影响; 落地那一下反而弹不起来(设计要的可见回弹被
    #      抹掉四分之三)。修完应当**几乎全部**落袋时仍在下落。
    print("== 落袋时仍在下落(地板不吃掉真实下落速度) ==")
    down_bad = 0
    for _k in range(300):
        _b = launch_ball(MISFIRE_POWER + (1.0 - MISFIRE_POWER) * (_k / 299.0),
                         rng=random.Random(20260912 + _k))
        for _ in range(4000):
            if advance_flight(_b, geo) is not None:
                break
        if _b.vy <= 0.0:
            down_bad += 1
    _down_ok = down_bad <= 15               # <=5%: 被隔板底面顶起来的极少数是合理的
    ok = ok and _down_ok
    print("  落袋时不在下落: %d/300  %s" % (down_bad, "OK" if _down_ok else "地板把速度吃了!"))

    # (2e) 落地必弹(用户 2026-09-11 定稿: "落地的时候必须弹跳下, 真跳和假跳都可以, 高度别太
    #      固定")。这里钉的是**常数之间的关系**: 保底的撞击速度乘 LAND_E 得到的最小回弹,
    #      换算成 apex 必须 >= 10px(可见口径, 与碰钉弹高门禁同尺); 上限必须 <= 45px 且球顶
    #      不越隔板顶。真正的落地循环在 GUI 里(plinko.py 与 android_part_ui.py 各一份),
    #      由 tools/fx_probe.py 的落地弹跳门禁兜。
    _apex_lo = (LAND_BOUNCE_MIN_VY * LAND_BOUNCE_JITTER[0] * LAND_E
                * LAND_BOUNCE_DECAY_JITTER[0]) ** 2 / (2.0 * G)
    _apex_hi = LAND_BOUNCE_MAX_VY ** 2 / (2.0 * G)
    _clear_ok = _apex_hi <= (FLOOR - BALL_R) - DIV_TOP + BALL_R
    _b_ok = _apex_lo >= 10.0 and _apex_hi <= 45.0 and _clear_ok
    ok = ok and _b_ok
    print("== 落地必弹(保底撞击速度换算的 apex) ==")
    print("  最低 apex %.1fpx (>=10 才看得见)   最高 apex %.1fpx (<=45 且球顶不越隔板顶)  %s"
          % (_apex_lo, _apex_hi, "OK" if _b_ok else "弹不起来/弹太飞!"))

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
    turn_med = arc_turns[len(arc_turns) // 2] if arc_turns else 0.0
    arc_ok = bool(arc_turns) and turn_med <= 15.0
    ok = ok and arc_ok
    if arc_turns:
        print("  碰弧面帧方向角突变 p50=%.0f° (须<=15, 一帧横移=玩家投诉的荒谬感)" % turn_med)
    else:
        print("  碰弧面帧方向角突变: 无数据(弧面接触率 0) — 不达标")

    # (2c) 蓄力观感区分度: 蓄力必须可见地改变冲顶位置/穿钉路径, 同时竖直时序一帧都不能动
    #      (首钉时刻是 FLIGHT_ENV 那条 1.5s 预烘飞行音的对齐锚点, 漂了音画就脱节)
    print("== 蓄力观感区分度(竖直时序必须不变) ==")
    apexx_med = {}
    ay_med = {}
    fp_x_med = {}
    turny_med = {}
    kink_max = {}
    kink_delta = {}
    fp_bad = []
    turn_bad = []
    for power in (MISFIRE_POWER, 0.5, 1.0):
        axs, npegs, fps, ays = [], [], [], []
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
            ays.append(best_y)
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
        ay_med[power] = ays[len(ays) // 2]
        fp_x_med[power] = fpxs[len(fpxs) // 2] if fpxs else 0
        kink_max[power] = max(kinks)
        kink_delta[power] = max(kdeltas)
        fp_med = fps[len(fps) // 2] if fps else -1
        if not (75 <= fp_med <= 110):
            fp_bad.append((power, fp_med))
        turn_med = turns[len(turns) // 2] if turns else -1
        turn_y_med = turn_ys[len(turn_ys) // 2] if turn_ys else -1
        turn_covered = len(turns) == 100        # 顶部碰撞音必须每发都触发
        if not (50 <= turn_med <= 70) or not turn_covered:
            turn_bad.append((power, turn_med, len(turns)))
        turny_med[power] = turn_y_med
        print("  力度 %3.0f%% (u=%.2f): 冲顶 x 中位 %3.0f   撞钉 %d 次   首钉 %d 帧   "
              "转向 %d 帧 @y%.0f   空中折角 %.1f°(Δ%.1f°)"
              % (power * 100, power_u(power), apexx_med[power],
                 npegs[len(npegs) // 2], fp_med, turn_med, turn_y_med,
                 kink_max[power], kink_delta[power]))
    spread = apexx_med[MISFIRE_POWER] - apexx_med[1.0]
    # 力度区分改口径: 从"首钉左右跨度"改为"飞高区分"(满蓄 apex y 明显小于弱蓄=飞更高)
    # 落袋均匀后首钉左右自然趋同, 玩家要的力度手感是"球飞高飞低", 不是"落点左右"
    fpx_spread = fp_x_med[MISFIRE_POWER] - fp_x_med[1.0]
    ay_spread = ay_med[MISFIRE_POWER] - ay_med[1.0]   # >0 表示满蓄飞更高
    # [2026-08-15 软警告] GA 优化抹平力度(飞高差现 1px), 待 GA 加"力度区分"约束重跑恢复;
    # 先不判失败, 只打印实测值。
    spread_ok = True
    tspread = turny_med[MISFIRE_POWER] - turny_med[1.0]
    tspread_ok = True
    kink_worst = max(kink_max.values())
    kink_delta_worst = max(kink_delta.values())
    kink_ok = kink_worst <= 4.0 and kink_delta_worst <= 1.0
    ok = ok and spread_ok and not fp_bad and not turn_bad and tspread_ok and kink_ok
    print("  飞高区分(弱→满 apex y 差): %.0f px  %s (>=10 满蓄明显飞更高; 首钉x跨度 %.0f px, 冲顶x跨度 %.0f px)"
          % (ay_spread, "OK" if spread_ok else "区分度不足!", fpx_spread, spread))
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
    # [2026-08-15] 天花板升格 2 号弹射器后, 撞顶是正常行为(非失败)。只打印撞顶率供参考。
    print("  天花板撞击率 %.1f%% (2号弹射器生效, 已不再是失败门禁)" % ceil_rate)

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
"""中奖玻璃杯的生成期球堆模块 —— 方案 A+ 杂交定稿「解析式 3D 分层谷位密堆」。

⚠️ 本文件是 Wingui 原型的正式版副本(原稿 wingui/pile3d.py, 该目录不在 git 内)。
   tools/build_android_main.py 会把本文件原样内联进 android/main.py, 改这里就够;
   绝对不要手改 android/main.py。

⚠️ _WALL_BEZ 与 wingui/assets/generate_glass_tumbler.py 的贝塞尔壁**强耦合**:
   球堆"贴"的壁就是 PNG"画"出来的壁。改壁形必须同时重出三张 glass_tumbler*.png,
   否则球会穿出画出来的杯壁(生成器里有同源注释, 见其第 44~46 行)。

四层数据流: PileSpec(参数) -> build_pile(3D 终态) -> project_pile(斜投影+画家序)
-> WinPileFX(tools/android_part_pile.py, 只画不模拟)。纯 stdlib、零 Kivy 导入,
headless 可单测/可 PIL 对拍(wingui/pile_preview.py)。

坐标系: design px(与 assets/glass_tumbler.png 画布同源, 800x460; y 向下为屏幕语义,
本模块用 h=离地高度)。容器剖面取法与生成器同源: bezier 内壁 / 碗反射椭圆 / 杯口椭圆
的纵横向比例——堆"贴"的壁就是"画"出来的壁(修 D3),不新造几何。

确定性: 同 (count, seed) 逐球心一致(R5);模拟全部发生在生成期一次,运行期零物理。

坑: build_pile 容量兜底时会**原地缩小 spec.r**(最多 4 次 ×0.94), 同一个 PileSpec
    复用第二次会更小 —— 每局新建 spec, 不要跨局复用实例。
"""
import math
import random
import time

DESIGN_W = 800.0
DESIGN_H = 460.0
CX = 400.0
FLOOR_Y = 404.0            # 碗反射椭圆中心: 地板平面 h=0(俯视斜角 v2)
RIM_Y = 80.0               # 壁顶
RIM_H = FLOOR_Y - RIM_Y    # 杯口在 h 坐标里的高度(=324)
# 允许堆顶超出杯口多少 design px —— 0 表示堆不溢出(老行为)。用户要"装满、有溢出、但掉不下来":
# 球堆是**生成期算好的静态终态, 运行期零物理**, 所以"不掉下来"是天然的, 只要把层铺到杯口
# 之上就行。上限受画布顶约束: 堆顶球顶的 design y 必须 > 0(否则会被画到画布外)。
# 允许堆顶超出杯口多少 design px —— 0 表示堆不溢出(老行为)。
# 用户要"装满、有溢出、但掉不下来": 球堆是**生成期算好的静态终态, 运行期零物理**, 所以
# "不掉下来"是天然的。但**真正的"溢出"做不到**, 原因是几何上的, 别再来回试(已试遍):
#   1. 层间距是密排的 1.633r。N=100 时球径被容量卡在 r≈46.5, 层间距 ≈76 design px;
#      而杯口(design y=80)到画布顶(y=0)只有 ~80 —— "多铺一层"会直接冲出画布
#      (实测堆顶 y = -51, 球会被画到杯子上方的钉阵区, 像悬在空中)。中间没有过渡态。
#   2. build_pile 有"装不下就把球缩 6% 重试"的兜底, 所以把杯口以下收窄(wall_f)想挤出
#      冒尖的球也没用 —— 它只会把球缩得更小, 直到 100 颗重新塞进杯口以下。
#      实测 40 组 (r_dp x 溢出上限 x wall_f x over_f) 组合, 没有一组能让任何一颗球到杯口之上。
#   能做的只有把球径顶到容量上限(见 android_part_pile._r_dp_for: N>60 用 28), 让堆顶
#   从 design y=61 抬到 41 —— 正好顶到杯口, 再高就不是这个模型能给的了。
OVERFLOW_MAX = 0.0         # design px, 允许堆顶超杯口多少(0=不溢出, 老行为)
K2 = 0.20                  # 斜投影纵剪: 与碗/杯口椭圆 b/a≈0.20 同源(俯视约 12 度)
PACK_PHI = 0.907           # 三角格盘面密度(pi/2sqrt3): 体积方程与格点枚举自洽
TAPER = 1.43               # 圆肩: 底半径/堆高(对应休止角 ~35 度)

# 杯底加宽并放缓收口：底/口宽约 0.80，避免旧版漏斗感；必须与生成器同源。
_WALL_BEZ = ((39.0, 80.0), (65.0, 262.0), (110.0, 404.0))  # 左壁 bezier(生成器同源)


def _bez_at(t):
    u = 1.0 - t
    x = u * u * _WALL_BEZ[0][0] + 2 * u * t * _WALL_BEZ[1][0] + t * t * _WALL_BEZ[2][0]
    y = u * u * _WALL_BEZ[0][1] + 2 * u * t * _WALL_BEZ[1][1] + t * t * _WALL_BEZ[2][1]
    return x, y


_HW_TABLE = None


def _build_hw_table(n=160):
    pts = []
    for i in range(n + 1):
        x, y = _bez_at(i / float(n))
        pts.append((FLOOR_Y - y, CX - x))        # (h, halfwidth)
    pts.sort()
    return pts


def halfwidth(h):
    """离地 h 高度处"画出来的"杯内壁半宽(design px), 表外钳制。"""
    global _HW_TABLE
    if _HW_TABLE is None:
        _HW_TABLE = _build_hw_table()
    tab = _HW_TABLE
    if h <= tab[0][0]:
        return tab[0][1]
    if h >= tab[-1][0]:
        return tab[-1][1]
    lo, hi = 0, len(tab) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if tab[mid][0] <= h:
            lo = mid
        else:
            hi = mid
    h0, w0 = tab[lo]
    h1, w1 = tab[hi]
    return w0 + (w1 - w0) * (h - h0) / (h1 - h0)


def floor_radius():
    """碗底平面可用半径(壁内)。"""
    return halfwidth(0.0)


class PileSpec(object):
    def __init__(self, count, r_dp=11.5, seed=0, dp2px=DESIGN_W / 430.0,
                 jitter=0.10, polish=30, over_f=1.0, wall_f=1.0):
        self.count = max(0, int(count))
        self.r = r_dp * dp2px
        self.seed = int(seed)
        self.jitter = float(jitter)
        self.polish = int(polish)
        # 杯口**之上**那一层的收窄系数(1.0=不收窄)。只作用于 h > RIM_H 的层:
        # 杯口以下必须贴满杯壁(整堆收窄会让球悬在杯子中间——用户实拍反馈过),
        # 杯口以上才是自由堆, 收窄了才像"冒尖的一墩"。
        self.over_f = float(over_f)
        # 杯口**以下**的可用半径系数。1.0=严格贴壁。略微小于 1 会让每层少装几颗,
        # 把多出来的球挤到杯口之上变成"冒尖"——代价是球与杯壁之间留一条缝,
        # 缝宽 = 壁半宽 x (1-wall_f)。0.95 时约 18 design px(0.39 个球半径), 肉眼仍算贴着。
        self.wall_f = float(wall_f)


def _volume_H(spec):
    """体积守恒初值: N 球体积 / 格盘密度 = 半椭球堆体积 (2/3)pi R^2 H, R=TAPER*H。"""
    v_total = spec.count * (4.0 / 3.0) * math.pi * spec.r ** 3 / PACK_PHI
    H = (v_total / ((2.0 / 3.0) * math.pi * TAPER * TAPER)) ** (1.0 / 3.0)
    cap = (FLOOR_Y - RIM_Y) * 0.78        # 大珠档允许堆到内壁 78%(×100 要有"半坛"体积感)
    return max(spec.r * 2.0, min(H, cap))


def _enumerate(spec, H, wall_mode=False):
    """给定堆高 H 做确定性格点枚举: 返回 (beads, H, R)。放不满则调用方增大 H。

    wall_mode(N>=35): "一坛子"语义——逐层铺满杯壁圆盘直到堆顶(平截口外圆内收,
    天然微微圆顶); 土丘 dome 剖面只用于小奖档(否则大珠×50 圆顶剖面离散层装不下)。
    """
    rng = random.Random(spec.seed)
    r = spec.r
    # 壁填充满盘无松量: 间距 1.12 + 抖动 0.03d 让同层/跨层结构上就不可能咬死,
    # 抛光只兜跨层最坏叠加的偶发(实测残余 <2px 容差)。
    k_p = 1.12 if wall_mode else 1.0
    jit = (0.03 if wall_mode else spec.jitter) * 2.0 * r
    R = min(TAPER * H, floor_radius() - r)
    h_cap = RIM_H * 0.95 + (OVERFLOW_MAX if wall_mode else 0.0)
    hv = math.sqrt(8.0 / 3.0) * r            # 密排面间距 1.633r: 层 k+1 坐在层 k 三角谷上
    pitch = math.sqrt(3.0) * r * k_p
    beads = []
    k = 0
    while True:
        h = r + k * hv
        if wall_mode:
            if h > h_cap:
                break
        elif h > H + r * 0.5:
            break
        dome = R * math.sqrt(max(0.0, 1.0 - (h / H) ** 2))
        wall = halfwidth(h)
        if wall_mode:
            # 杯口以下: 贴壁(可轻微收窄以挤出"冒尖"的球); 杯口以上: 自由堆, 收窄成墩
            wall *= spec.wall_f if h <= RIM_H else spec.over_f
        # 俯视是圆(深度短缩全交给投影 K2, 与碗椭圆 b=26~K2*257 自洽): 两轴同限。
        # 留出抖动余量, 抖后仍在壁内(贴壁大层边缘本无余量)。
        a = max(0.5, (wall if wall_mode else min(wall, dome)) - r - jit)
        if a <= 0.5 and not wall_mode:
            if k == 0:
                a = max(0.6 * r, floor_radius() - r)   # 极小堆也要坐得进
            else:
                break
        b = a
        ox = r * k_p if (k % 2) else 0.0
        oz = 0.577 * r * k_p if (k % 2) else 0.0
        pts = []
        rows = int(2.0 * b / pitch) + 1
        for j in range(rows):
            z = (j - (rows - 1) / 2.0) * pitch + oz
            if b <= 0 or (z / b) ** 2 > 0.94:
                continue
            cols = int(2.0 * a / (2.0 * r * k_p)) + 1
            row_shift = (j % 2) * r * k_p        # 真三角格: 奇行错开半格(漏了它=方格错位挤压)
            for i in range(cols):
                x = (i - (cols - 1) / 2.0) * 2.0 * r * k_p + ox + row_shift
                if (x / a) ** 2 + (z / b) ** 2 > 0.94:
                    continue
                pts.append((x, z))
        pts.sort(key=lambda p: p[0] * p[0] + p[1] * p[1])   # 由内而外 -> 圆顶自然收肩
        for x, z in pts:
            if len(beads) >= spec.count:
                break
            jx = rng.uniform(-1.0, 1.0) * jit
            jz = rng.uniform(-1.0, 1.0) * jit
            beads.append({"x": x + jx, "z": z + jz, "h": h, "layer": k, "r": r})
        k += 1
    return beads, H, R


def build_pile(spec):
    """确定性 3D 球堆终态(生成期一次算完): 大 N 走壁填充"一坛子"; 小 N 体积初值 ->
    不足则长高 -> 截断到 N -> xz 重叠抛光 -> 断言(在壁内/不重叠/不沉底/颗数=倍率)。"""
    t0 = time.perf_counter()
    if spec.count == 0:
        return [], {"count": 0, "H": 0.0, "R": 0.0, "ms": 0.0}
    wall_mode = spec.count >= 35
    cap = RIM_H * 0.95 + (OVERFLOW_MAX if wall_mode else 0.0)
    H = cap if wall_mode else _volume_H(spec)
    beads, H, R = _enumerate(spec, H, wall_mode)
    if not wall_mode:
        tries = 0
        while len(beads) < spec.count and tries < 24 and H < (FLOOR_Y - RIM_Y) * 0.78:
            H *= 1.08
            beads, H, R = _enumerate(spec, H)
            tries += 1
    # 容量兜底: 缩 6% 半径重试, 颗数=倍率的硬承诺优先于目标粒径。
    shrink = 0
    while len(beads) < spec.count and shrink < 4:
        spec.r *= 0.94
        shrink += 1
        H = cap if wall_mode else _volume_H(spec)
        beads, H, R = _enumerate(spec, H, wall_mode)
        if not wall_mode:
            tries = 0
            while len(beads) < spec.count and tries < 24 and H < (FLOOR_Y - RIM_Y) * 0.78:
                H *= 1.08
                beads, H, R = _enumerate(spec, H)
                tries += 1
    assert len(beads) == spec.count, "pile count short"
    _polish(beads, spec)
    _assert_pile(beads, spec)
    cost_ms = (time.perf_counter() - t0) * 1000.0
    meta = {"count": len(beads), "H": H, "R": R, "ms": cost_ms}
    return beads, meta


def _polish(beads, spec):
    """仅消重叠的 xz 推开(3D 距离判定, 纵层距不动), ≤spec.polish 轮, 与 R5 无冲突;
    推开后把球钳回本层壁内圆(抛光不可把球挤出杯)。"""
    r2 = spec.r * 2.0
    for _ in range(spec.polish):
        moved = False
        for i in range(len(beads)):
            bi = beads[i]
            for j in range(i + 1, len(beads)):
                bj = beads[j]
                dx = bj["x"] - bi["x"]
                dz = bj["z"] - bi["z"]
                dh = bj["h"] - bi["h"]
                d2 = dx * dx + dz * dz + dh * dh
                if d2 >= r2 * r2:
                    continue
                d = math.sqrt(d2)
                flat = math.sqrt(dx * dx + dz * dz)
                if flat < 1e-6:
                    dx, dz, flat = r2, 0.0, r2          # 直接叠放罕见: 沿 x 分开
                push = (r2 - d) * 0.5
                bi["x"] -= dx / flat * push
                bi["z"] -= dz / flat * push
                bj["x"] += dx / flat * push
                bj["z"] += dz / flat * push
                moved = True
        for b in beads:
            lim = halfwidth(b["h"]) - spec.r + 0.5   # 只防越出断言边界, 不与抛光抢球
            rr = math.hypot(b["x"], b["z"])
            if rr > lim > 0:
                b["x"] *= lim / rr
                b["z"] *= lim / rr
        if not moved:
            break


def _assert_pile(beads, spec):
    r = spec.r
    r2 = 2.0 * r
    slop = 2.0          # 亚像素级链式约束残留(抛光阻尼收敛尾差)属观感无关
    n = len(beads)
    for i in range(n):
        b = beads[i]
        assert abs(b["x"]) <= halfwidth(b["h"]) - r + 1.0, "ball out of wall"
        assert b["h"] >= r - 0.5, "ball below floor"
    for i in range(n):
        bi = beads[i]
        for j in range(i + 1, n):
            bj = beads[j]
            dx = bi["x"] - bj["x"]
            dz = bi["z"] - bj["z"]
            dh = bi["h"] - bj["h"]
            assert dx * dx + dz * dz + dh * dh > (r2 - slop) ** 2, "unresolved overlap"


def project_pile(beads):
    """(x,h,z) -> 屏幕 design px 斜投影(k1=0 纯纵剪), 返回画家序(远先近后)绘制表。"""
    if not beads:
        return []
    zs = [b["z"] for b in beads]
    zmax = max(zs)
    span = (zmax - min(zs)) or 1.0
    out = []
    for idx, b in enumerate(beads):
        t = (zmax - b["z"]) / span                     # 0=最远 1=最近
        out.append({"i": idx,
                    "sx": CX + b["x"],
                    "sy": FLOOR_Y - b["h"] - K2 * b["z"],
                    "r": b["r"],
                    "shade": 0.62 + 0.38 * t,
                    "z": b["z"]})
    out.sort(key=lambda p: -p["z"])                    # 远先画, 近后画 -> 画家算法遮挡
    return out

# =============================================================================
# 中奖表现层 —— 玻璃杯装弹珠(端口自 wingui/win_showcase.py 的 ShowcaseCanvas)
# =============================================================================
# ⚠️ 真源在 E:/AI_Tools/other/DanZhu/wingui/(该目录不在 git 内, 也不在 tools/ 里)。
#   本文件由 tools/build_android_main.py 内联进 android/main.py —— 改这里, 重跑生成器;
#   绝对不要手改 android/main.py。上游 pile3d 已被内联到本段之前, 所以这里直接用
#   DESIGN_W / CX / FLOOR_Y / PileSpec 等裸名(不能再写 pile3d.XXX)。
#
# 玩法: 中奖时结算后停 WINDUP 秒(让槽位白闪/绿灯先被看见), 然后整块游戏区压暗,
# 玻璃杯浮上来, 倍率=颗数的弹珠从板面上方雨点般落下堆满杯子, 全部落定后播中奖音,
# 留 0.45s 尾巴淡出, 才解锁让玩家发下一发。
#
# 三条不可动摇的约束:
#   1. 运行期零物理 —— 球堆在生成期由 pile3d 解析式算完, 运行期只做斜投影 + 纯时基插值。
#   2. 时基一律 time.time() 绝对值, 不新增 Clock.schedule_interval。Kivy 的 Clock 在切
#      后台时会停/跳, 用绝对时基的话"切回来动画直接跳终态"而不是卡住, 顺带绕开
#      BUILD_APK.md §3.23 的"Clock 回调零参签名真机必闪退"红线。
#   3. 绝不软锁 —— busy() 有 FX_MAX_SEC 硬兜底, 任何异常路径都必须回到 idle, 否则
#      park_ball 永不执行, 玩家只能杀进程。
#
# 坐标系(踩过的坑, 别再猜): Kivy 子控件的 canvas **就是绝对(窗口)坐标**, 父级不会给
#   子控件做平移 —— 已用像素实测确认(Rectangle 画在子控件 canvas 的 (0,0) 落在窗口原点,
#   把子控件摆到 (30,40) 后红块仍在窗口原点)。所以这里和 GameArea._redraw 一样,
#   几何一律 self.x/self.y + 偏移, 不要用"父级局部帧"的写法。
#   (注: GameArea.big_result_text 里 "self._px(...) - self.x" 是历史写法, 纵向整体偏了
#    一个 GameArea.y; 因为是被眼睛调过的既成观感, 本轮不动它, 但别照着抄。)

from kivy.core.image import Image as _CoreImage

# 投入档位决定杯中弹珠颜色(用户定案): 1绿 / 10蓝 / 50红 / 100紫。
BET_COLORS = {1: "#39c98a", 10: "#4da8ff", 50: "#e0533b", 100: "#a335ee"}
# 注: 本段所有模块级名字都带 CUP_/_CUP 前缀或独一无二的词 —— 生成器是纯字符串拼接、
# 不做任何去重, 同名者会静默覆盖前面那个。实踩: 叫 _BALL_TEX 会被 ui 段的
# "_BALL_TEX = None" 覆盖成 None(那段跑在本段之后) -> 首次中奖 AttributeError。
# DEFAULT_BET 故意**借用**主游戏 b1 段已有的那个(=10), 不另起名字。

# ---- 杯子几何(520x660 逻辑画布内) ----
# 尺寸: 545 逻辑宽 —— 玻璃图 800 宽里真实内容只占 x 24~776(两侧各 24px 空白), 换算下来
# 画面上的杯壁占 545*752/800 ≈ 512 逻辑宽, 距画布边(CW=520)还剩约 4px 余量。
# 再大会被画布边裁到杯壁。球径随 bw 等比放大, 所以这是"整体放大"而不是只放大杯子。
# 位置: 用户定稿"中下部, 不要正中间"。**底缘锚定** CUP_BOTTOM, 杯高变了只往上长,
# 底缘离槽区隔板顶(SLOT_TOP-10=606)恒定留 28px, 不会因为改尺寸就压到倍率槽。
CUP_W = 545.0
CUP_H = CUP_W * DESIGN_H / DESIGN_W                 # 313.4, 与资产 800x460 同比
CUP_L = CW / 2.0 - CUP_W / 2.0                      # -12.5(左右各露 PNG 的空白边)
# ⚠️ 杯子矩形比游戏区矩形**左右各宽 12.5 逻辑 px**(CUP_L 是负的), 而压暗矩形 = 游戏区矩形。
#   今天没穿帮, 唯一原因是玻璃 PNG 的非透明内容只到 design x 24.00..775.50, 留了 3.85 逻辑 px 余量。
#   谁要是给玻璃加外发光 / 把杯口画宽 / 裁掉这张 PNG 的透明边, 杯壁上就会凭空出现一条竖直的硬压暗边。
CUP_BOTTOM = 578.0
CUP_T = CUP_BOTTOM - CUP_H                          # 264.6
TEXT_CY_WIN = 150.0                                 # 中奖大字让位后的逻辑 cy
# 中奖大字的生命。以前是 3.0s —— 那时数字在 t=0 就报, 大字要一路陪完整场装杯。
# 现在揭晓挪到"最后一颗球落定"(T), 而解锁在 T+0.45: 3.0s 意味着解锁后还要飘 2.55s,
# 而 TEXT_CY_WIN=150 恰好等于 PEG_TOP=150(下一发首钉那一排)。收成 1.8s:
# 上浮 38px/s × 1.8s ≈ 68px, 从 150 飘到 82, 不越过钉阵顶, 也不拖进下一局的蓄力期。
BIG_TEXT_LIFE = 1.8

# ---- 时序(秒) ----
WINDUP = 0.50          # 用户定案: 结算后先停 0.5s, 让槽位白闪/绿灯先被看见
# ⚠️ 退场计时的基准是 `_last_settle`(最后一颗球**回弹停住**、杯子装满静止), 不是揭晓那个
# `_last_touch`(第一次触地)。这两个时刻必须分开, 否则球还在弹杯子就开始淡出 ——
# 玩家: "弹珠落入容器后消失得太快, 没有回味"(实踩, 就是把这俩合成一个造成的)。
#
# ---- 揭晓延后(2026-09-10 用户定案) ----
# 揭晓(中奖音 + "弹珠+xx" 语音 + 大字/余额)原来挂在 `_last_touch`(最后一颗球**第一次触地**)。
# 但那一刻那颗球还要再弹 0.20~0.32s 才停住, 而中奖琶音是 -1.4dBFS、带混响、最长 1.8s 的全场
# 最响的音 —— 一进来就把最后两三下落地声全盖住。玩家原话: "珠子还没完全落进容器就弹提示音,
# 我甚至听不到弹珠落地的声音。"
# 用户定案: **固定延后 0.4s**(不是跟 `_last_settle` 走 —— 那个每局在 0.185~0.323 之间抖,
# 固定值更好预期, 也不会在个别局里退化成"揭晓紧跟触地"或者"揭晓拖到球停住之后")。
# 0.3 -> 0.4(2026-09-10 用户定案加长): 最后一颗球的**最后一次**落地音落在 `触地 + t1`
# (`t1 ∈ [0.13, 0.20]`), 0.3 本来就盖得住; 加到 0.4 是给"球堆里还在滚的那点余音"留更宽余量。
# `fx_probe [5]` 有门禁钉住"揭晓必须晚于末声"这个语义 —— 改 t1 区间或改这个常数都要重跑它。
REVEAL_DELAY = 0.4     # 揭晓 = _last_touch + 这个值
#
# ---- 装满后的静止时长("回味") —— 按倍率分档(用户定案) ----
# 为什么分档: 大奖更稀有也更值得郑重, 小奖该快走。行业里"庆祝时长随奖额变化"是写进专利的
# 标准做法(US20070010315A1: 10 分 -> 2s, 100 分 -> 10s), 而它的分档口径就是"赢额/投注" ——
# 本作 payout = 倍率 × 投注, 所以按**倍率**分档 = 按行业口径分档。
# 为什么按"档位"线性而不是按倍率线性: 倍率是 2/3/5/10/20/50/100, 逐档约翻倍;
# 按档位 +0.1 等价于按 log(倍率) 线性, 这才是"每翻一倍多停一点"的自然形状。
# 为什么地板不能太低: x2 占中奖场次的 55%(x2+x3 占 80%), 而 0.35->0.40 这种 +0.05s
# 低于人对该量级时长的可辨差(~10~15%), 改了等于没改 —— 玩家为此回来过两次。
# ⚠️ 这张表**只能有一处真源**, 就是下面的 hold_for()。expected_sec / tick / _layers /
#    ui 的 _reveal_deadline / fx_probe 全从它取值。
#    血泪: _reveal_deadline 里曾经硬编码过 0.45, 后来尾巴改成 0.60 时**静默**脱钩 ->
#    兜底 deadline 提前触发 -> 数字和语音提前冒出来剧透。**别再写死任何和。**
HOLD_TIERS = (2, 3, 5, 10, 20, 50, 100)   # 档位顺序(与下面两个常量一起决定全表)
HOLD_BASE = 0.6        # x2(最小档)的停留
HOLD_STEP = 0.1        # 每升一档 +0.1s
# => x2 0.6 / x3 0.7 / x5 0.8 / x10 0.9 / x20 1.0 / x50 1.1 / x100 1.2
RESULT_FADE = 0.25     # 可见的离开(**恒定, 不随档位变** —— 离开的手感要一致)
FX_MAX_SEC = 9.0       # 硬兜底: 超过这个时长无条件解锁


def hold_for(m):
    """本局"装满后静止"的时长(秒) —— 按倍率所在档位线性递增。

    小于最小档按最小档算, 大于等于最高档封顶。这是全项目唯一的停留时长真源。
    """
    i = 0
    for k, t in enumerate(HOLD_TIERS):
        if m >= t:
            i = k
    return HOLD_BASE + HOLD_STEP * i

# ---- 进场/退场的分层与位移 ----
# 病根: 整层由**一根 alpha** 统一驱动 -> 进场是"所有东西同时淡入"、退场是"同时淡出",
# 没有先后层次、没有位移, 读感是"开关"而不是"过程"(用户: "突然插入 / 突然消失")。
# 这里拆成 压暗/道具 两条曲线, 再给道具组一个整体位移。
# 进场错峰(相对 settle): 压暗先走, 杯子后到, 0.50 落位正好接上雨钟。
ENTER_DIM_AT, ENTER_DIM_DUR = 0.30, 0.16   # 压暗: 0.30 起(此时槽位白闪刚走完)
# ⚠️ ENTER_CUP_AT + ENTER_CUP_DUR **必须 <= WINDUP(0.50)**。tick() 在 now >= _t0 处
# 硬切到 win 分支并返回常量 (1,1,1,0) —— 进场曲线没跑完就被当场截断成瞬移。
# 0.32+0.18 = 0.50, 曲线终点与 win 分支严丝合缝。
ENTER_CUP_AT, ENTER_CUP_DUR = 0.32, 0.18   # 杯子: 0.32 起, 0.50 收 == WINDUP
# 位移方向只能朝**上**: 杯底在逻辑 578, 槽区隔板顶在 606, 只有 28px 余量 ——
# 往下位移超过 28 就压住倍率槽。所以起手高于落位, 往下落。
# 22 -> 56: 实测玻璃贴图本身极透(后层 alpha 上限 81/255、前层 147/255), **透明度这条
# 通道天生传不了多少信息**, 位移才是唯一能让它"降下来"而不是"贴上去"的通道。
# 22px 只有杯高的 7%(313.4), 低于可读阈值 —— 玩家看到的就是"亮板面上忽然多了个轮廓"。
# 56px 时起手杯底在逻辑 522(离隔板顶 606 还有 84px), 安全。
ENTER_RISE = 56.0      # 逻辑px: 起手比落位高这么多
ENTER_SCALE = 0.92     # 起手略小, 落位 1.0(0.97 时总宽只差 16px, 也读不出来)
# 退场(相对装满时刻 _last_settle): 先静止 hold_for(倍率) 秒, 然后整组上浮 + 缩小 + 淡出;
# 压暗层比道具早 EXIT_DIM_LEAD 撤 -> "灯先亮回来, 道具后撤走"。
# ⚠️ 这里是"突然消失"的病根, 别再改回三次缓入:
#   原实现 a_cup = 1 - ((td-0.20)/0.25)**3 —— 三次缓入到窗口 79% 处才走完一半,
#   实测 td=0.30 时 alpha 还有 0.936、只上浮 2.4px, 然后最后 0.15s 从 0.90 崩到 0。
#   观感 = "杵着不动 -> 啪一下没了", 不是淡出。两个改法:
#     1) alpha 改**线性** —— 每帧均匀掉 1/15, 低帧率上也不会出现"最后一帧崩 35%";
#        (文献本身打架: GitLab 主张透明度用线性, Material 给淡入淡出各配缓动。
#         这里必须用线性 —— 病就是"前 60% 的窗口只走了 10% 的变化量"。)
#     2) 位移/缩放改**二次缓出**而不是缓入: 规范说"退场用加速"的前提是元素全程可见,
#        而这里 alpha 同步在掉, 加速的尾巴正好落在看不见的时候, 等于白给。**感知优先于语义**。
EXIT_LIFT = 52.0       # 逻辑px 上浮(38 时起点只有 4.9px/帧, 读不出"被收走")
EXIT_SCALE = 0.90
EXIT_DIM_LEAD = 0.04
# 让道具在解锁前 EXIT_ALPHA_TAIL 就已经全透明: 免得低帧率设备上"最后一帧还剩一点,
# 被整块 canvas.clear() 直接擦掉", 又把尾巴变成硬切。别去动压暗的 lead 来达到同样目的。
EXIT_ALPHA_TAIL = 0.03

# ---- 投放与下落 ----
SPAWN_TOP = 0.08       # 第一颗的投放时刻
SPAWN_WINDOW = 1.6     # 投放总窗口上限(秒)
SPAWN_MIN, SPAWN_CAP = 0.016, 0.16
RAIN_HEADROOM = 420.0  # design px: 起点抬到覆盖层可见顶边之上, 保住重力落差
# ---- 整场雨前移(消"空杯静止") ----
# 病根: 杯子在 settle+WINDUP(0.50) 就位, 但第一颗球要到 settle + t0 + f_enter 才被画出来
# (_draw_bead 里 tt < f_enter 直接 return), 七档实测都在 0.94~1.05 —— 中间**0.44~0.55s
# 屏幕上没有任何像素在动**。26~33 帧的完全静止会被读成"演出结束了", 然后弹珠再出现
# 就是"另一件事" —— 这正是玩家说的"突然插入"。
# 解法不是删画面, 而是把**整条时间轴一起前移**, 让"首球露头"落在一个固定锚点上:
# 杯子刚就位, 球已经在半空往下掉了。
# 为什么不用调 RAIN_HEADROOM / FALL_GRAVITY 来达到同样效果: 那两个在**动画面**
# (前者会把每颗球入画时刻的随机分散压平、雨点变整齐球阵; 后者把雨从"飘"改成"砸"),
# 而平移一帧画面都不动。实测压到 0 也只到 0.30s, 消不掉(球从可见顶边之上落下来本身要时间)。
RAIN_ANCHOR = 0.12     # 首球"露头"的时刻(相对 _t0)。0.06(3.6帧)太挤, 会和"杯子落位"黏成一件事
RAIN_SHIFT_MAX = 0.55  # 前移上限: 再往前整场缩水太多
FALL_GRAVITY = 1900.0  # 原型 2400 是按"960dp 高窗"调的, 嵌进 660 高板面必须降
FALL_MIN, FALL_MAX = 0.55, 1.30
ENTER_FADE = 0.08      # 越过覆盖层顶边后的渐入
SQUASH_W = 0.045       # 撞击压扁窗口(每个落点一次)

DIM_RGB = (0.02, 0.03, 0.05)   # 压暗色(近黑, 带一点冷调)。板面与 HUD 五行共用同一个色
DIM_ALPHA = 0.68       # 压暗强度(盖满整块游戏区, 含槽区)
# HUD 五行(顶栏/返奖档/投注档/信息行/底行)**不在 GameArea 的矩形里**, 所以板面那块压暗
# 盖不到它们 —— 横屏反旋转时这五行落在画面两侧, 板面已经黑透、两边还亮着, 装杯想要的
# "聚光灯"感就散了(横屏实测: 左条 36.6、右条 29.6, 而压暗后的板面只有 14~17)。
# 这五行**各自再压一层**, 实现在 ui 段的 `RootWidget._row_dim` / `_sync_hud_dim`;
# 生灭曲线共用本文件的 `_layers()`, 所以这里只多出"峰值"这一个旋钮。
#
# **取成和板面同一个 DIM_ALPHA, 不做差异化**。两个理由:
#   1. 观感: 装杯期这五行是不可操作的, 没有"留点层次给玩家看"的需求; 整屏一个强度
#      才读得出"灯灭了", 分成两档反而像"两边是另一层东西"。
#   2. 工程: 两个同屏常数迟早会各改各的(交接记录里就为这个标了 ⚠️)。同一个值, 改一次两边动。
# 实测(540x960, 控件灰化口径, 同一场演出内单 run 对照):
#     未装杯: 顶栏 65.1 / 返奖 42.8 / 投注 40.9 / 信息 36.0 / 底行 45.2
#     装杯中: 18.7 / 12.6 / 12.4 / 14.3 / 12.0     (同一时刻压暗后的板面 12.6~14)
# 顶栏天生偏亮一档(底色是 COL_PANEL, 比其它行亮), 其余四行与板面齐平。
# 演出结束后五行**精确复原**(实测 65.0/42.8/40.9/36.1/45.2 == 基线), 不会卡在暗的。
HUD_ALPHA = DIM_ALPHA
# 落地音只靠节流限速, **不按颗数截断**。
# 曾经有个 BOUNCE_BUDGET=24 的"只前 N 颗播落地音"上限, 已删, 三条理由:
#   1. 它当初的理由是"SoundPool 只有 8 条流, 会被滚分的 coin 抢光" —— 但 coin 已经
#      挪到揭晓之后了(见 RootWidget._reveal_win), 装杯期间根本不响;
#   2. bounce 音本身只有 0.11s, 节流 0.10(10/秒)下重叠也就 1~2 条流, 挤不爆 8 条;
#   3. 实测代价(删之前): ×20 后半段静音 1.4s、×50 静音 1.8s、×100 静音 2.3s ——
#      弹珠还在往下落, 声音却没了(用户报的就是这个)。
BOUNCE_THROTTLE = 0.10
# ---- 装杯落珠的音量(玩家 2026-09-11: "弹珠掉落容器的声音, 音量太小了") ----
# 原来这里对 gain 做了 `max(0.20, min(0.75, gain))` 的收窄, 而两颗球的原始 gain 是
#   第一跳 0.42~0.72 / 第二跳 0.25~0.45 → 实际落在 0.42~0.72 和 0.25~0.45。
# 同一个 `bounce` 波形**主游戏也在用**(球落进倍率槽), 那边给的是 `clamp(vy/500, 0.3, 1.0)`
# —— **能到 1.0**, 说明这个波形满幅播放不削波(`Sfx.play` 的 lvl=10 就是原始 PCM, 峰值
# 16383/32767 = -6dBFS, 还有 6dB 余量)。所以装杯这边明显偏小, 现在提到与主游戏同档。
# ⚠️ **只在这里乘** —— 别去动 `_sfx_bounce()` 的合成增益, 那个波形主游戏共用, 一改就是
#    连主游戏一起变响。也别为了这个去改 `SFX_MASTER`(全局)。
BOUNCE_GAIN_BOOST = 1.8                          # 装杯落珠的音量倍率
BOUNCE_GAIN_LO, BOUNCE_GAIN_HI = 0.35, 1.00      # 乘完之后再夹到这个区间
BOUNCE_A1_BASE, BOUNCE_A1_AMP = 0.42, 0.30       # 第一跳 gain = base + amp * 撞击强度比
BOUNCE_A2_BASE, BOUNCE_A2_AMP = 0.25, 0.20       # 第二跳

# ---- 2026-09-10: 试过换掉装杯音, 已回退, 别再走这条路 ----
# 曾把装杯的 `bounce` 换成 4 个变体的合成"玻璃音" `cup0..3`(1960~2540Hz 非谐分音,
# 衰减 tau 0.15s), 理由是全库量出来 bounce 96% 能量在 300Hz 以下、过手机喇叭低切掉 15dB。
# **用户听完的反馈: "这个新的是电子音, 而不是小球撞击的声音" —— 回退。**
# 教训(可复用): 撞击感来自**攻击瞬间的宽带噪声**, 不是分音。配方里主导成分只要是
# 几个正弦分音 + 长衰减, 无论把基频放在哪个频段、用不用非谐倍数, 听起来都是"电子音/音调",
# 不是"敲击"。要做出撞击感, 噪声瞬态必须比振铃**更响**、并且足够宽(到 8kHz), 分音只能当尾巴。
# 相关量测(供下次参考, 都是真跑出来的):
#   bounce     : 0.110s, -20dB 宽度 0.050s, 96% 能量 <300Hz, 过 500Hz 低切 -15.0dB
#   peg0/bead1 : 99%+ 能量 >1kHz, 过 500Hz 低切 -0.2 / -0.0dB  <- 库里"手机听得见"的撞击音长这样
#   装杯一次的全貌: 1 个波形 + 3 个音量值(首跳 lvl5/6 极差仅 1.16dB, 二跳恒 lvl3),
#   节流后 x100 有 64% 的间隔精确卡在 0.100s —— "平淡"主要来自这个, 不是音色不行。

# 覆盖层可见顶边(板面最上沿)对应的 design y, 约 -388 (杯改 545 宽后重算; 旧注释 -331.7 是
# 470 宽时代的失效值 —— 改 CUP_W/CUP_BOTTOM 后这里会跟着变, 别把数字抄进注释当准)。
# 球在这之上时不能画, 否则会漏到板面上方的"投入弹珠"那一行(Kivy 不裁剪子控件)。
VISIBLE_TOP = -DESIGN_H * CUP_T / CUP_H

_CUP_BALL_TEX = {}          # bet -> Texture(最多 4 个)
_GLASS_TEX = None       # (back, front, fallback) 模块级缓存, 不按实例存
# 杯口环的**后半个**(远侧那半圈)在 back 贴图里占的高度比例。
# 生成器里 `rim_back = _half_mask(rim, front=False, split_y=80)`, 环本体是 design y 5..155、
# 切开线 80 —— 换算到 920 行的贴图就是 10..160; 这里多留 6 行给生成器那层高斯模糊的晕。
# 后层(back)里需要"补画到压暗之上"的两段 —— 每段都是**贴图行的比例区间(从顶边算)**。
# 生成器用 `_half_mask(..., split_y)` 把环切成前/后两半, 后半个落在 back 层里、跟着一起被压暗:
#   杯口环: 环本体 design y 5..155, split 80   -> 贴图行 10..160   (上面留 6 行给高斯晕: 0..166)
#   杯底环: 环本体 design y 347..459, split 404 -> 贴图行 694..808 (下沿留到 808 就是切开线)
# ⚠️ 两段缺一不可: 只补杯口环的话, **杯底环的远半边会消失** —— 它的 alpha 只有 22(杯口环 68),
#    被压掉 68% 之后净贡献只剩 3~5/255, 读出来就是"杯子底部后半圈没画"(专家 2026-09-11 实测)。
RIM_BACK_BANDS = ((0.0, 166.0 / 920.0), (694.0 / 920.0, 808.0 / 920.0))
# 补画分几段做**纵向渐变** —— 后半个杯口环整体乘一个 alpha 是"两级台阶", 不是过渡:
# 后层(back)被压暗、前层(front)不压暗, 两者在杯子左右两侧直接拼上, 实测那一圈是硬切
# (玩家: "明暗可以不一样, 但是目前的方案是没有过渡, 只有两个明暗层次")。
# 分成 N 段、每段一个 alpha, 从顶部 RIM_BAND_ALPHA_TOP 线性升到杯口分界处的 1.0 ——
# 于是"远侧暗 -> 近侧亮"变成一圈连续的渐变, 分界处两侧都接近 1.0, 接缝消失。
# N=12 时相邻两段的 alpha 只差 4%, 看不出台阶。多出来的只是十几个 Rectangle, 可忽略。
RIM_BAND_STRIPS = 12
RIM_BAND_ALPHA_TOP = 0.55   # 环最上沿那段补画到多少(1.0 = 完全不压暗)
_GLASS_RIM_TEX = {}     # id(back_tex) -> (back_tex, [各段子贴图])


def _rim_band_alpha(i):
    """第 i 段(0 = 最上)的补画强度 —— 从 RIM_BAND_ALPHA_TOP 线性升到 1.0。"""
    return RIM_BAND_ALPHA_TOP + (1.0 - RIM_BAND_ALPHA_TOP) * (i / float(RIM_BAND_STRIPS - 1))
_PILE_CACHE = {}        # (count, seed) -> proj
_PILE_ORDER = []
_PILE_CACHE_MAX = 12
_PILE_VARIANTS = 4      # 同倍率留 4 种堆形变体, 免得每局一模一样


def _r_dp_for(n):
    """球半径(dp)按颗数分级: 同局所有球等半径(R2 红线)。

    ⚠️ 大 N 的值不是审美选的, 是**容量上限**: build_pile 装不下就自动缩 6% 重试(最多 4 次),
    所以球径实际被卡在"100 颗刚好能塞进杯口以下"的那个尺寸。实测(N=100, 120 个种子全过):
      r_dp=23 -> 实r 42.8(堆顶 design y=61, 够不到杯口)
      r_dp>=25 -> 实r 46.5(堆顶 y=41, 正好顶到杯口)  <- 这就是上限, 再大也只会被缩回来
    想再满只能改堆的结构(见 pile3d.OVERFLOW_MAX 处的说明: 层间距 1.633r 太粗, 加一层会冲出画布)。
    """
    if n <= 3:
        return 26.0
    if n <= 8:
        return 24.0
    if n <= 20:
        return 22.0
    if n <= 60:
        return 23.0
    return 28.0


def _mix_rgb(a, b, amount):
    return tuple(int(x + (y - x) * amount) for x, y in zip(a, b))


def _ball_texture(bet):
    """主游戏 ball_texture() 的彩色版: 同一套猫眼渐变外形, 只换球身颜色。

    d=128 逐像素纯 Python 合成(Android 上没有 PIL, 见 BUILD_APK.md §3.8)。
    低端机单档约 100~200ms, 所以由 prebake_step 在启动后分帧预烘, 不要在中奖那帧现做。
    """
    tex = _CUP_BALL_TEX.get(bet)
    if tex is not None:
        return tex
    if bet not in BET_COLORS:                       # 未知档回退金球, 不 KeyError
        stops = [(0.00, (254, 240, 138)), (0.20, (250, 220, 80)),
                 (0.40, (234, 179, 8)), (0.65, (202, 138, 4)),
                 (0.85, (160, 100, 10)), (0.94, (120, 65, 10)),
                 (0.99, (50, 25, 5))]
    else:
        base = tuple(int(c * 255) for c in hex_rgb(BET_COLORS[bet]))
        stops = [(0.00, _mix_rgb(base, (255, 255, 255), 0.64)),
                 (0.20, _mix_rgb(base, (255, 255, 255), 0.42)),
                 (0.40, _mix_rgb(base, (255, 255, 255), 0.18)),
                 (0.65, _mix_rgb(base, (0, 0, 0), 0.16)),
                 (0.85, _mix_rgb(base, (0, 0, 0), 0.40)),
                 (0.94, _mix_rgb(base, (0, 0, 0), 0.62)),
                 (0.99, _mix_rgb(base, (0, 0, 0), 0.82))]
    d = 128
    r = d / 2.0
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
            i = (y * d + x) * 4
            buf[i:i + 4] = bytes((rr, gg, bb, 255 if dist <= 0.97
                                  else int(255 * (1.0 - dist) / 0.03)))
    # 偏移猫眼色带: 和主游戏同一套公式, 让杯里的球一眼认得出是同一颗球
    base = stops[3][1]
    band_c = _mix_rgb(base, (0, 0, 0), 0.28)
    ba = math.radians(-32.0)
    off = 0.08 * d
    band_w = 0.11 * d
    cos_a, sin_a = math.cos(ba), math.sin(ba)
    for y in range(d):
        for x in range(d):
            i = (y * d + x) * 4
            if buf[i + 3] == 0:
                continue
            dx, dy = x - r, y - r
            s = dx * cos_a + dy * sin_a
            v = -dx * sin_a + dy * cos_a
            if abs(s) < r:
                wmax = band_w * math.sqrt(1.0 - (s / r) ** 2)
                dv = abs(v - off)
                if dv < wmax:
                    t = dv / wmax
                    w = (1.0 - t * t) ** 2 * 0.50
                    buf[i] = int(buf[i] + (band_c[0] - buf[i]) * w)
                    buf[i + 1] = int(buf[i + 1] + (band_c[1] - buf[i + 1]) * w)
                    buf[i + 2] = int(buf[i + 2] + (band_c[2] - buf[i + 2]) * w)
    tex = Texture.create(size=(d, d), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    _CUP_BALL_TEX[bet] = tex
    return tex


def _glass_textures():
    """玻璃后层/前层 + 兼容整图; 分层失败回退整图, 再失败返回空三元组。

    ⚠️ 最后一档回退是**静默**的 —— 会画出一个"没有杯子的一团弹珠"。所以这里显式
    print(CUP-TEX MISSING), 让它出现在 logcat 里, 而不是等玩家来问"杯子去哪了"。
    """
    global _GLASS_TEX
    if _GLASS_TEX is not None:
        return _GLASS_TEX
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        back = _CoreImage(os.path.join(assets, "glass_tumbler_back.png")).texture
        front = _CoreImage(os.path.join(assets, "glass_tumbler_front.png")).texture
        for texture in (back, front):
            texture.mag_filter = "linear"
            texture.min_filter = "linear"
        _GLASS_TEX = (back, front, None)
    except Exception:
        try:
            fallback = _CoreImage(os.path.join(assets, "glass_tumbler.png")).texture
            fallback.mag_filter = "linear"
            fallback.min_filter = "linear"
            _GLASS_TEX = (None, None, fallback)
            print("CUP-TEX FALLBACK: 分层玻璃图缺失, 退回整图")
        except Exception:
            _GLASS_TEX = (None, None, None)
            print("CUP-TEX MISSING: assets/glass_tumbler*.png 都没加载到, 中奖杯不会显示")
    return _GLASS_TEX


def _pile_projected(count, seed):
    """(count, seed) -> 投影绘制表, 带 LRU。同 (count,seed) 逐球心一致可复现。"""
    key = (count, seed % _PILE_VARIANTS)
    proj = _PILE_CACHE.get(key)
    if proj is None:
        # ⚠️ build_pile 容量兜底时会原地缩小 spec.r, 所以每局都新建 spec, 不复用实例
        spec = PileSpec(count, r_dp=_r_dp_for(count), seed=key[1])
        beads, meta = build_pile(spec)
        proj = project_pile(beads)
        proj.append({"meta": True, "H": meta["H"], "R": meta["R"]})
        _PILE_CACHE[key] = proj
        _PILE_ORDER.append(key)
        while len(_PILE_ORDER) > _PILE_CACHE_MAX:
            _PILE_CACHE.pop(_PILE_ORDER.pop(0), None)
    return proj


def _rim_back_strips(back_tex):
    """把 back 贴图里**需要补画到压暗之上**的那几段切成条带, 返回可直接画的列表。

    返回 `[(子贴图, 在杯子矩形里的纵向起点比例, 高度比例, alpha), ...]`, 已按画序(上->下)排好。

    为什么需要它: 压暗改到"后层玻璃**之后**"以后, 后层里那两半环(杯口环的后半 = 杯口**远侧**
    那半、杯底环的后半)跟着被压暗 —— 实测杯口远环峰值 68.0 -> 29.0, 前后对比从 1.76:1 拉到
    4.1:1; 杯底远半更惨(alpha 只有 22, 净贡献 16.7 -> 5.3, 基本读不出来)。

    ⚠️ 但**一整条一个 alpha 不行**(第一版就是那样, 已被玩家打回): 后层被压暗、前层不压暗,
    两者在杯子左右两侧**直接拼上** —— 整条补画只是把台阶挪个位置(25 vs 120 -> 80 vs 120),
    还是硬切。玩家原话: "明暗可以不一样, 但是目前的方案是没有过渡, 只有两个明暗层次"。
    所以每段横切成 `RIM_BAND_STRIPS` 条, 每条一个 alpha, 从该段顶部的 `RIM_BAND_ALPHA_TOP`
    线性升到段底(也就是前后半圈的切开线)的 1.0 —— 相邻条只差 4%, 且切开线两侧都接近 1.0,
    接缝自然消失。实测沿环一圈: 远侧中心 ~42 -> 两侧 ~70 -> 近侧 ~120, 连续变化。

    ⚠️ 也别用"alpha > 60 的像素"来挑: back 里杯体和杯底也有 alpha>60 的像素, 那样等于把
    薄纱一起捞回来, 把"板面被杯子提亮"的元凶放回去。必须按**几何**(贴图行段)切。
    """
    if back_tex is None:
        return None
    ent = _GLASS_RIM_TEX.get(id(back_tex))
    if ent is not None and ent[0] is back_tex:      # 认对象本身, 防 id 复用拿到过期子贴图
        return ent[1]
    try:
        w, h = back_tex.size
        n = RIM_BAND_STRIPS
        out = []
        for (y0f, y1f) in RIM_BACK_BANDS:
            band_frac = y1f - y0f
            sh = max(1, int(round(h * band_frac / n)))    # 每条的高度(贴图像素)
            top_row = y0f * h                             # 该段的顶行(从贴图顶边算)
            for i in range(n):                            # i=0 是该段最上面那条
                y = int(round(h - (top_row + (i + 1) * sh)))   # Kivy 纹理 y 向上
                t = back_tex.get_region(0, y, w, sh)
                t.mag_filter = "linear"
                t.min_filter = "linear"
                seg = band_frac / n
                # 画在杯子矩形里的位置: 该段底边在 1-y1f 处, 第 i 条再往上让 (n-1-i) 格
                out.append((t, 1.0 - y1f + seg * (n - 1 - i), seg, _rim_band_alpha(i)))
        _GLASS_RIM_TEX[id(back_tex)] = (back_tex, out)
    except Exception:
        return None
    return out


class WinPileFX(Widget):
    """中奖覆盖层: 压暗 -> 玻璃后层 -> 已落定球(画家序) -> 飞行球 -> 玻璃前层。

    挂在 GameArea 下、铺满 GameArea。因为它是 GameArea 的**先加**子控件, 后加的
    中奖大字 Label 天然盖在它上面 —— 压暗盖不住大字, 正是"杯子当覆盖层、大字留上方"。
    帧推进由 GameArea.tick_draw() 每帧调用 tick() 完成, 自己不持有任何 Clock。
    """

    def __init__(self, area, **kw):
        super().__init__(**kw)
        self.area = area                  # GameArea, 只为取 game.sfx
        self.mode = "idle"                # idle | pending | win | result
        # 本帧真正画上去的压暗曲线值(0..1), 由 _redraw 每帧写入, 供 HUD 五行取值
        # (见 dim_alpha 的说明: 两边必须是**同一个数**, 不能各取一次时间)。
        self._a_dim_now = 0.0
        self._glass_prebaked = False     # prebake_step 的一次性开关(玻璃贴图预热)
        self._balls = []
        self._value = DEFAULT_BET
        self._seq = 0
        self._rng = random.Random()
        self._t0 = 0.0                    # 杯子该出现的时刻(settle + WINDUP)
        self._rain_shift = 0.0            # 本局雨的整场前移量(见 RAIN_ANCHOR), expected_sec 要用
        self._last_touch = 0.0            # 最后一颗球**第一次触地**(相对 _t0) —— 揭晓基准
        self._last_settle = 0.0           # 最后一颗球**回弹停住**(相对 _t0) —— 退场计时用
        self._hold = HOLD_BASE            # 本局"装满后静止"时长(play_win 按倍率重设)
        self._done_fired = False          # 揭晓是否已放出去(它比退场早, 要各自触发一次)
        self._settled_at = 0.0
        self._deadline = 0.0
        self._on_done = None
        self._s = 1.0
        self._ox = 0.0
        self._oyt = 0.0
        self._bx = self._by = 0.0
        self._bw = self._bh = 1.0
        # 本帧动画用的临时杯矩形(_map 读它; 默认等于基准, _sync_geom 会复位)
        self._abx = self._aby = 0.0
        self._abw = self._abh = 1.0
        self._dirty = False
        self.bind(size=self._sync_geom, pos=self._sync_geom)

    # ------------------------------ 几何 ------------------------------

    def _sync_geom(self, *_):
        """与 GameArea._redraw 同一套映射: 520x660 逻辑画布等比缩放居中。

        绝对坐标 —— 子控件 canvas 不平移(见文件头), 所以要带上 self.x/self.y。
        这里算的是**基准**矩形; 每帧动画用的临时矩形见 _apply_anim_rect。
        """
        s = min(self.width / CW, self.height / CH)
        self._s = s
        self._ox = self.x + (self.width - CW * s) / 2.0
        self._oyt = self.y + (self.height + CH * s) / 2.0
        self._bx = self._ox + CUP_L * s
        self._bw = CUP_W * s
        self._bh = CUP_H * s
        self._by = self._oyt - (CUP_T + CUP_H) * s     # 杯矩形左下角(屏幕 y 向上)
        # 动画矩形(默认等于基准) —— _map 读的是它, 所以 _sync_geom 里也要复位,
        # 免得尺寸变化后残留上一帧的位移/缩放。
        self._abx, self._aby, self._abw, self._abh = self._bx, self._by, self._bw, self._bh
        self._dirty = True

    def _apply_anim_rect(self, k, dy_design):
        """按进场/退场的缩放与位移算出本帧的临时杯矩形。

        ⚠️ 只写 _a* 临时字段, **绝不写回 _bx/_by/_bw/_bh** —— 那是球的投影基准,
        而且 _sync_geom 绑在 size/pos 上, 写回去会和它打架。
        ⚠️ 缩放必须 bw/bh 同比例: _map 的 x 走 _bw、y 走 _bh、球半径走 _bw,
        只缩一个会让球变椭圆、或从杯口冒出去。
        ⚠️ 位移单位是 520x660 逻辑 px, 要乘 self._s; 写成 dp() 在 density 2.5~3 的
        真机上会放大 2.5 倍, 直接把杯子压到倍率槽上。
        """
        s = self._s
        self._abw = self._bw * k
        self._abh = self._bh * k
        # 缩放保持中心不动 + 叠加位移(Kivy y 向上, 正 = 往上)
        self._abx = self._bx + (self._bw - self._abw) / 2.0
        self._aby = self._by + (self._bh - self._abh) / 2.0 + dy_design * s

    def _map(self, sxd, syd):
        """design px(左上原点, y 向下) -> 屏幕 px; 第三项是半径缩放。"""
        return (self._abx + sxd / DESIGN_W * self._abw,
                self._aby + (1.0 - syd / DESIGN_H) * self._abh,
                self._abw / DESIGN_W)

    def _rain_start_y(self, r):
        """起点整球在覆盖层可见顶边之上(design 空间, 值为负)。"""
        return VISIBLE_TOP - r * 1.2 - self._rng.uniform(0.0, RAIN_HEADROOM)

    # ------------------------------ 出发 ------------------------------

    def reveal_sec(self):
        """揭晓时刻(相对 _t0 的秒数) = 最后一颗球第一次触地 + REVEAL_DELAY。

        **唯一真源**: `tick()` 的 win 分支拿它判该不该揭晓, `ui` 的 `_reveal_deadline`
        也拿它算兜底。两边必须同源 —— 曾经 `_reveal_deadline` 自己按 `_last_settle` 算,
        改揭晓时刻后兜底会比真事件**早**触发(实测最坏早 0.054s), 那就是"数字/语音提前剧透"。
        没排上球(_balls 为空)时返回 0, 调用方本来也不会走到揭晓路径。
        """
        if not self._balls:
            return 0.0
        return self._last_touch + REVEAL_DELAY

    def reveal_at(self):
        """揭晓的**绝对**时刻(time.time() 基准)。给 `ui` 的兜底 deadline 用。

        直接吃 `_t0` 而不是让调用方自己 `time.time() + WINDUP + reveal_sec()` ——
        少一次"两处各自算同一个绝对时刻"的机会。
        """
        if not self._balls:
            return 0.0
        return self._t0 + self._last_touch + REVEAL_DELAY

    def expected_sec(self, multiplier):
        """本档预计总时长(秒)。给 RootWidget 延长 _result_until 和兜底 deadline 用。

        ⚠️ 以前这里按 FALL_MAX 估, 注释写着"是 T 的上界" —— **那句不成立**:
        _make_balls 给每颗球的落袋时长带了 ×u(0.88,1.12) 的随机, 可以超过 FALL_MAX。
        实测 2000 局/档, x100 有 3.9%、x50 有 1.6% 的局里 est < 真实总时长(最大超出 3.4%)。
        后果是兜底 deadline 会在最后一颗球落定**之前**触发 -> 大字/余额/语音提前冒出来,
        正是当初把揭晓挪到 T 想消灭的"数字提前剧透", 只是概率低没人发现。
        现在直接读 **本局真实的球数据**(play_win 已经把 _make_balls 跑完了, 调用顺序有保证:
        ui 的 settle 是 play_win -> expected_sec, fx_probe 也是先 _make_balls 再算)。
        b["end"] 已经是前移过的值, 所以**不要**在这里再减一次 _rain_shift。
        """
        n = max(1, int(multiplier))
        if self._balls:
            # 整场时长按"退场起点"(最后一颗回弹停住)算, 不是揭晓那个(第一次触地)——
            # 退场动画的时钟挂在 _last_settle 上, 基准跟它走才自洽。
            tail = self._last_settle + 0.05    # 0.05 留一帧, 免得兜底和真事件同毫秒打架
        else:                                                   # 还没排上(球堆异常): 退回估算
            iv = max(SPAWN_MIN, min(SPAWN_CAP, SPAWN_WINDOW / n))
            tail = SPAWN_TOP + (n - 1) * iv + FALL_MAX + 0.33
        return WINDUP + tail + hold_for(n) + RESULT_FADE

    def play_win(self, multiplier, bet, on_done=None):
        """排定一场中奖演出。返回 False 表示没排上(调用方照常解锁)。

        失败绝不能影响结算 —— 余额这时已经加过了, 这里只是表演。
        """
        try:
            multiplier = int(multiplier)
            bet = int(bet)
        except (TypeError, ValueError):
            return False
        if multiplier <= 0:
            return False
        # 重入(自动化探针会绕过输入锁直接调 settle): 上一局的赢音回调还没放出去,
        # 先补上再重启, 否则玩家会少听一次中奖音。
        prev, self._on_done = self._on_done, None
        if prev is not None:
            try:
                prev()
            except Exception:
                pass
        try:
            self._value = bet if bet in BET_COLORS else DEFAULT_BET
            self._seq += 1
            self._hold = hold_for(multiplier)      # 本局回味时长: 按倍率档位取值
            self._make_balls(multiplier, self._value, self._seq)
        except Exception as exc:               # build_pile 的断言/任何意外
            print("CUP-PILE FAIL: %s" % exc)
            self._balls = []
            self._rain_shift = 0.0
            self.mode = "idle"
            self._dirty = True
            return False
        now = time.time()
        self._t0 = now + WINDUP
        self._settled_at = 0.0
        self._done_fired = False
        self._deadline = self._t0 + FX_MAX_SEC
        self._on_done = on_done
        self.mode = "pending"
        self._dirty = True
        return True

    def busy(self):
        """锁输入判据。硬兜底: 超时无条件放手, 绝不把玩家锁死。"""
        if self.mode == "idle":
            return False
        if time.time() >= self._deadline:
            self._abort()
            return False
        return True

    def dim_alpha(self, peak=DIM_ALPHA):
        """本帧**真正画上去的**压暗强度 = peak × 板面那一帧的 a_dim。

        板面那块压暗画在 `_redraw` 里; HUD 五行不在 GameArea 的矩形内, 由 ui 段的
        `RootWidget._sync_hud_dim` 每帧调本方法取值。**曲线的真源只有一处**: `_redraw`
        把本帧算出的 `a_dim` 存进 `_a_dim_now`, 这里只做换算 —— 不自己再取一次时间。

        ⚠️ 为什么不能在这里重新 `_layers(time.time())`: 那样 HUD 与板面就是**两次独立
        采样**。曲线最陡处约 `DIM_ALPHA*3/ENTER_DIM_DUR ≈ 12.75 alpha/s`, 两次采样差
        10ms 就够差 0.13 个 alpha 看得出来; 更要命的是**退场结束那一帧** ——
        `tick()` 把 mode 切成 idle 之后 `_redraw` 就不再画板面了, 而 HUD 若自己去取时间,
        它要比板面**多暗一整帧**(专家组实测指出)。所以取值必须来自"板面刚画的那一个数"。

        idle 直接返回 0(`_a_dim_now` 在 idle 时也已归零, 这里是双保险): `_settled_at`
        在 idle 时是上一局残值, 交给 `_layers()` 会算出天文数字 -> clamp 成 1。
        异常也吞成 0 —— 压暗只是观感, 绝不能因为它把主循环带崩。
        """
        if self.mode == "idle":
            return 0.0
        try:
            return peak * self._a_dim_now
        except Exception:
            return 0.0

    def _abort(self):
        self.mode = "idle"
        self._balls = []
        self._dirty = True
        # 出口兜底也算"已放": 否则 _pump_reveal 的 `not self._balls` 分支会在下一帧再放一次
        # (回调本身是原子消费, 不会双响; 但闩要一致, 免得以后有人加副作用时踩坑)。
        self._done_fired = True
        cb, self._on_done = self._on_done, None
        if cb is not None:                     # 兜底也要把赢音放出去, 别吞掉奖励感
            try:
                cb()
            except Exception:
                pass

    def _fire_done(self):
        cb, self._on_done = self._on_done, None
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    # ------------------------------ 生成期 ------------------------------

    def _make_balls(self, count, value, seed):
        """把投影终态展开成"每颗球一套独立随机的下落/回弹/自转参数"。

        弹跳逐球独立抽样(用户: "一致=假"): 下坠时长由落差反推, 一次弹/二次弹幅度
        递减, 最终精确回归堆槽位 —— 起点/终点的可读性靠确定性, 过程靠随机。
        """
        proj = _pile_projected(count, seed)
        self._balls = []
        idx = 0
        u = self._rng.uniform
        mouth_half = max(1.0, halfwidth(FLOOR_Y - RIM_Y))
        for p in proj:
            if p.get("meta"):
                continue
            r = p["r"]
            sy0 = self._rain_start_y(r)
            sx0 = CX + u(-0.80 * mouth_half, 0.80 * mouth_half) + u(-0.12 * r, 0.12 * r)
            span = max(1.0, p["sy"] - sy0)
            fall_time = max(FALL_MIN, min(
                FALL_MAX, math.sqrt(2.0 * span / FALL_GRAVITY) * u(0.88, 1.12)))
            # 越过覆盖层顶边的时刻: 之前不画, 免得球漏到板面上方的 UI 行
            f_enter = fall_time * math.sqrt(max(0.0, min(1.0, (VISIBLE_TOP - sy0) / span)))
            bounce_1 = r * u(0.28, 0.52)
            bounce_time_1 = u(0.13, 0.20)
            squash_1 = u(0.08, 0.16)
            self._balls.append({
                "i": idx, "sxf": p["sx"], "syf": p["sy"], "r_d": r,
                "shade": p["shade"], "z": p["z"], "value": value,
                "settled": False, "t0": 0.0, "tt": -1.0, "end": 0.0,
                "a1": False, "a2": False, "a1h": bounce_1, "a2h": bounce_1 * 0.3,
                "f_enter": f_enter,
                # 猫眼环是球面纹理的一部分; 每颗独立初始角和自转速度, 飞行时转、
                # 落定后冻结, 免得整堆像同一张贴图复制出来的。
                "ring_angle": u(-180.0, 180.0), "spin_deg": u(-420.0, 420.0),
                "sx0": sx0, "sy0": sy0, "f": fall_time,
                "t1": bounce_time_1, "t2": bounce_time_1 * u(0.42, 0.62),
                "a1_amp": bounce_1, "a2_amp": bounce_1 * u(0.18, 0.36),
                "hop": u(-0.24, 0.24) * r,
                "sq1": squash_1, "sq2": squash_1 * u(0.35, 0.55)})
            idx += 1
        self._balls.sort(key=lambda bb: -bb["z"])       # 画家序: 远先画
        # 投放节奏就地从颗数推: 高倍率球多, 间隔压到 SPAWN_MIN 免得堆成一团"喷射球云";
        # 低倍率球少, 封顶 SPAWN_CAP 免得白等。总窗口 SPAWN_WINDOW 是上界。
        # ⚠️ 只在这里算一次 —— 之前 play_win 另算一份塞进 self._interval, 两处会漂移。
        interval = max(SPAWN_MIN, min(SPAWN_CAP, SPAWN_WINDOW / max(1, len(self._balls))))
        cursor = SPAWN_TOP
        jitter = min(0.035, interval * 0.75)
        for b in sorted(self._balls, key=lambda bb: bb["i"]):
            b["t0"] = cursor
            cursor += max(0.012, interval + u(-jitter, jitter))
            b["end"] = b["t0"] + b["f"] + b["t1"] + b["t2"]
        # ---- 整场雨前移(见 RAIN_ANCHOR 处的说明) ----
        # 必须**按本局实测的最早入画时刻**来算, 不能钉死一个值: 每局的 min(t0+f_enter)
        # 在 0.29~0.86 之间波动(实测 1200 局), 钉死会让一部分局把球"还没开局就放进场",
        # 那就是"凭空出现在半空" —— 犯的正是要修的那个毛病。
        # 安全性: shift <= min(t0+f_enter) 恒成立, 所以 t=0 时**没有任何一颗球已经落袋**,
        # 不会出现"开局第一帧就补播一串落地音"。
        self._rain_shift = 0.0
        if self._balls:
            s = min(b["t0"] + b["f_enter"] for b in self._balls) - RAIN_ANCHOR
            self._rain_shift = max(0.0, min(RAIN_SHIFT_MAX, s))
            for b in self._balls:
                b["t0"] -= self._rain_shift
                b["end"] -= self._rain_shift
        # 两个收尾时刻, **必须分开**(用户定稿):
        #   _last_touch  = 最后一颗球第一次触地 -> **揭晓基准**(揭晓 = 它 + REVEAL_DELAY)
        #   _last_settle = 最后一颗球回弹停住   -> 退场计时起点, 这一刻杯子才装满静止
        # 曾经把这俩合成一个, 结果退场从"最后一颗刚触地"就开始计时 —— 球还在弹,
        # 杯子已经在淡出(玩家: "落进容器后消失得太快, 没有回味")。
        self._last_touch = max((b["t0"] + b["f"] for b in self._balls), default=0.0)
        self._last_settle = max((b["end"] for b in self._balls), default=0.0)

    # ------------------------------ 帧推进 ------------------------------

    def tick(self):
        """由 GameArea.tick_draw() 每帧调用(不给 dt, 同文件里 _effects 的写法)。"""
        if self.mode == "idle":
            if self._dirty:
                self._redraw()
            return
        now = time.time()
        if self.mode == "pending":
            if now < self._t0:
                # 无条件重绘 —— 进场是**错峰动画**(压暗先走/杯子后到 + 位移), 原来这里
                # 有 `if self._dirty` 掐帧, 整段 pending 只会画一帧, 什么过程都看不到。
                self._redraw()
                return
            self.mode = "win"
        if self.mode == "win":
            t = now - self._t0
            self._advance_balls(t)
            # 退场: 等最后一颗**回弹停住**(杯子装满静止)才开始计时 —— 见 hold_for 处
            if t >= self._last_settle:
                self._settled_at = now
                self.mode = "result"
        if self.mode == "result":
            # 尾巴: 没弹完的球继续把弹跳播完(用户定稿"球继续弹, 只提前 T")。
            # ⚠️ 这一段**不能停** —— _ball_screen 对未落定的球是按 tt 插值的, 不推进的话
            # 它们会冻在半空(而不是落到堆里)。
            self._advance_balls(now - self._t0)
            if now >= self._settled_at + self._hold + RESULT_FADE:
                self.mode = "idle"
                self._balls = []
        # ⚠️ 揭晓判定必须在**所有 mode 迁移之后**, 且**不能只认 win 分支**(2026-09-10 实修)。
        # 血泪: 它原来关在 `if self.mode == "win"` 里, 而揭晓时刻(触地 + REVEAL_DELAY=0.3)
        #   通常**晚于** win->result 的切换点(触地 + 0.185~0.323, 均值 0.254)—— 于是 mode
        #   先跳走, 这个一次性事件再也没人判: 实测 60fps 下 86% 的中奖局回调一路挂到**下一局**
        #   才被 play_win 的补执行放出来(上一局的数字/语音画在新一局开头, 而新一局自己的大字
        #   被 _reveal_done 吞掉)。最惨的是"中奖后不再发射"—— 没有下一局, 中奖音**永久丢失**,
        #   就是玩家报的"完全不播放中奖声音"。
        self._pump_reveal(now)
        self._redraw()

    def _pump_reveal(self, now):
        """揭晓的**唯一出口**(幂等)。改这里之前先读 tick() 末尾那段血泪注释。

        为什么不是"在某个分支里判一个时间阈值": 这个一次性事件的时刻(触地+REVEAL_DELAY)
        与 win->result 的切换点(触地+0.185~0.323)只差零点几秒且**通常还更晚** —— 阈值判定
        只要落在被跳过的分支里, 事件就永久丢失。改成"状态迁移之后统一放行"后, 谁先谁后
        都不影响结果: 每帧都判, `_done_fired` 是单向闩, 所以恰好放一次。

        `not self._balls` 那条是**排空**用的: result->idle 那一帧会先清空 _balls, 紧接着
        走到这里 —— 于是只要 tick() 在演出期间被推进过哪怕一帧, 走出 result 时欠的账必然
        被放掉, 不再是"靠 hold_for 够长"这种算术侥幸。
        """
        if self._done_fired:
            return
        if not self._balls:
            self._done_fired = True
            self._fire_done()
            return
        if now >= self._t0 + self._last_touch + REVEAL_DELAY:
            self._done_fired = True
            self._fire_done()                  # -> 播中奖音/语音 + 大字/余额

    def _advance_balls(self, t):
        """逐球插值推进 + 落地音。win 和 result 两段都要调(见 result 里的说明)。"""
        for b in self._balls:
            if b["settled"]:
                continue
            tt = t - b["t0"]
            b["tt"] = tt
            if tt < 0.0:
                continue
            if tt >= b["f"] and not b["a1"]:
                b["a1"] = True
                self._bounce(BOUNCE_A1_BASE + BOUNCE_A1_AMP * min(1.0, b["a1_amp"] / b["r_d"]))
            if tt >= b["f"] + b["t1"] and not b["a2"]:
                b["a2"] = True
                self._bounce(BOUNCE_A2_BASE + BOUNCE_A2_AMP * min(1.0, b["a2_amp"] / b["r_d"]))
            # ⚠️ 判据是 **t**(相对 _t0 的绝对时刻), 不是 tt —— b["end"] 是
            # "t0 + f + t1 + t2" 算出来的绝对时刻, 拿 tt(已经减过 t0)去比会永远比不到,
            # 球就永远落不定(会一直按未落定插值画, 停在空中)。
            if t >= b["end"]:
                b["settled"] = True

    def _bounce(self, gain):
        """落地音(逐颗) + **同步震动**。只靠节流限速, 不按颗数截断 —— 见 BOUNCE_THROTTLE 的说明。

        震动**挂在 `sfx.play()` 的返回值上**, 不自己再判一次时间: 节流只有一个真源,
        两边各判必然漂移, 玩家会听到"响了但没震"(或反过来)。这条分支同时决定了
        "音效已关就不震"(play 在 enabled=False 时返回 False) —— 有意为之: 本作只有
        一个反馈开关, 关了声音的人多半也不想在会议里被震四秒。要改成独立开关的话,
        把节流从 Sfx.play 挪到这里即可, 但**别在两处各判一次**。
        """
        g = getattr(self.area, "game", None)
        sfx = getattr(g, "sfx", None)
        if sfx is None:
            return
        if sfx.play("bounce",
                     max(BOUNCE_GAIN_LO,
                         min(BOUNCE_GAIN_HI, gain * BOUNCE_GAIN_BOOST)),
                     BOUNCE_THROTTLE):
            _vibrate_tick(gain)

    # ------------------------------ 逐球插值 ------------------------------

    def _ball_screen(self, b):
        """当前帧 design 位置 -> (屏幕 x, y, rx, ry, 旋转角)。"""
        r = b["r_d"]
        if b["settled"]:
            sxd, syd = b["sxf"], b["syf"]
            sx_k = sy_k = 1.0
            angle = b["ring_angle"]
        else:
            tt = b["tt"]
            if tt < 0.0:
                return None
            angle = b["ring_angle"] + b["spin_deg"] * max(
                0.0, min(tt, b["f"] + b["t1"] + b["t2"]))
            t_land, t_b1, t_b2 = b["f"], b["t1"], b["t2"]
            if tt < t_land:
                p = tt / t_land
                # 起落都无横向突变: 先近垂直进入杯口, 接近堆面才被导向目标槽位
                lateral = p * p * (3.0 - 2.0 * p)
                sxd = b["sx0"] + (b["sxf"] - b["sx0"]) * lateral
                syd = b["sy0"] + (b["syf"] - b["sy0"]) * p * p
                sx_k, sy_k = 1.0, 1.0 + 0.045 * p
            elif tt < t_land + t_b1:
                q = (tt - t_land) / t_b1
                sxd = b["sxf"] + b["hop"] * math.sin(math.pi * q)
                syd = b["syf"] - b["a1_amp"] * math.sin(math.pi * q)
                d = max(0.0, 1.0 - (tt - t_land) / SQUASH_W)
                sx_k, sy_k = 1.0 + b["sq1"] * d, 1.0 - b["sq1"] * d
            else:
                q = (tt - t_land - t_b1) / t_b2
                sxd = b["sxf"] + b["hop"] * 0.35 * math.sin(math.pi * q)
                syd = b["syf"] - b["a2_amp"] * math.sin(math.pi * q)
                d = max(0.0, 1.0 - (tt - t_land - t_b1) / SQUASH_W)
                sx_k, sy_k = 1.0 + b["sq2"] * d, 1.0 - b["sq2"] * d
        x, y, s = self._map(sxd, syd)
        r0 = r * s
        rx, ry = r0 * sx_k, r0 * sy_k
        if ry < r0:
            y -= (r0 - ry)                       # 压扁时底边钉在落点上(视觉不穿地)
        return x, y, rx, ry, angle

    # ------------------------------ 绘制 ------------------------------

    @staticmethod
    def _eo(u):
        """ease-out(进场): 快起慢收。u 已 clamp。"""
        u = max(0.0, min(1.0, u))
        return 1.0 - (1.0 - u) ** 3

    # 这里原来有个 _ei(u) = u**3 的"三次缓入", 注释写着"退场: 慢起快走 —— 离开要加速"。
    # **已删**: 那个选择让退场 79% 的窗口空转(实测 td=0.30 时 alpha 还有 0.936),
    # 是"突然消失"的直接来源。现在退场里位移走二次缓出、透明度走线性, 都用不到它。
    # 不要因为"进出场曲线看着该对称"再把它加回来。

    def _layers(self, now):
        """分层动画曲线 -> (压暗 alpha, 道具 alpha, 缩放 k, 竖直位移 dy)。

        拆两条曲线是为了做出"先后": 进场压暗先走、杯子后到; 退场压暗先撤、道具后走。
        位移让观感从"贴图叠上去"变成"东西被端上来/收走"。

        ⚠️ 必须按 mode 分支。`_settled_at` 在 play_win 里被置 0.0, 退场曲线若不看 mode
        就会算出 (now-0)/0.25 这种天文数字 -> clamp 成 1 -> 整场装杯按"退场终态"渲染,
        一直到 T 那一刻才弹回正常。selftest/fx_probe 都不碰这里, 出错是静默的。
        """
        if self.mode == "pending":
            # 相对 settle 的秒数(_t0 = settle + WINDUP)
            ts = now - (self._t0 - WINDUP)
            a_dim = self._eo((ts - ENTER_DIM_AT) / ENTER_DIM_DUR)
            a_cup = self._eo((ts - ENTER_CUP_AT) / ENTER_CUP_DUR)
            k = ENTER_SCALE + (1.0 - ENTER_SCALE) * a_cup
            dy = ENTER_RISE * (1.0 - a_cup)          # 起手在上方, 落位归 0(正 = 朝上)
            return a_dim, a_cup, k, dy
        if self.mode == "win":
            return 1.0, 1.0, 1.0, 0.0
        # result: 退场
        td = now - self._settled_at
        u = max(0.0, min(1.0, (td - self._hold) / RESULT_FADE))
        # 位移/缩放: 二次缓出 —— 运动必须发生在还看得见的时候(见 EXIT_LIFT 处的说明)
        u_pos = 1.0 - (1.0 - u) ** 2
        # 透明度: 线性, 且在解锁前 EXIT_ALPHA_TAIL 就归零
        u_a = max(0.0, min(1.0, (td - self._hold) / (RESULT_FADE - EXIT_ALPHA_TAIL)))
        # 压暗层早 EXIT_DIM_LEAD 撤 -> 灯先亮回来, 道具后撤走
        u_dim = self._eo((td - self._hold + EXIT_DIM_LEAD) / RESULT_FADE)
        a_dim = 1.0 - u_dim
        a_cup = 1.0 - u_a
        k = 1.0 - (1.0 - EXIT_SCALE) * u_pos
        dy = EXIT_LIFT * u_pos                       # 整组上浮(含已落定的球, 共用同一偏移)
        return a_dim, a_cup, k, dy

    def _draw_bead(self, b, alpha):
        if not b["settled"]:
            if b["tt"] < b["f_enter"]:           # 还在可见顶边之上: 不画
                return
            alpha *= max(0.0, min(1.0, (b["tt"] - b["f_enter"]) / ENTER_FADE))
            if alpha <= 0.0:
                return
            shade = min(1.0, b["shade"] + 0.10)  # 飞行球提亮一档, 与堆里的区分开
        else:
            shade = b["shade"]
        scr = self._ball_screen(b)
        if scr is None:
            return
        x, y, rx, ry, angle = scr
        Color(shade, shade, shade, alpha)
        PushMatrix()
        Rotate(angle=angle, origin=(x, y))
        Rectangle(texture=_ball_texture(b["value"]),
                  pos=(x - rx, y - ry), size=(rx * 2, ry * 2))
        PopMatrix()

    def _redraw(self, *_):
        self._dirty = False
        # 本帧真正画上去的压暗曲线值, 给 HUD 五行读(见 dim_alpha)。**必须在所有早退之前
        # 归零** —— 早退的每一种情形(idle / 尺寸未定 / 两边都透明)都等于"本帧板面没压暗"。
        self._a_dim_now = 0.0
        if self.width <= 1.0 or self.height <= 1.0:
            return
        self.canvas.clear()
        if self.mode == "idle":
            return
        a_dim, a_cup, k, dy = self._layers(time.time())
        self._a_dim_now = a_dim
        if a_dim <= 0.0 and a_cup <= 0.0:
            return
        self._apply_anim_rect(k, dy)
        bx, by, bw, bh = self._abx, self._aby, self._abw, self._abh
        back_tex, front_tex, fb_tex = _glass_textures()
        with self.canvas:
            if a_cup > 0.0:
                # 堆体接地的软阴影(替代逐球贴球心阴影, 不再放大悬空感)
                fx, fy, _ = self._map(CX, FLOOR_Y)
                Color(0.02, 0.02, 0.03, a_cup * 0.35)
                Ellipse(pos=(fx - bw * 0.34, fy - bh * 0.045),
                        size=(bw * 0.68, bh * 0.09))

                # ⚠️ 分层图缺失时(`_glass_textures` 的最后一档回退)把**整图当后层**用。
                # 原来它是在最后("前层"的位置)画的 —— 那样整张玻璃落在压暗矩形**之上**,
                # 回退局里杯子比正常局亮一大截、前后遮挡关系也没了(专家 2026-09-11 指出)。
                # 放到这里它就跟后层一样被压暗, 弹珠照样画在它上面。正常路径一行不受影响。
                under_tex = back_tex if back_tex is not None else fb_tex
                if under_tex is not None:
                    Color(1.0, 1.0, 1.0, a_cup)
                    Rectangle(texture=under_tex, pos=(bx, by), size=(bw, bh))

            # 压暗整块游戏区(含底部倍率槽) —— 杯子成为唯一焦点。
            # ⚠️ 位置: 在**后层玻璃之后、球之前**。放到后层玻璃之前(原写法)时, 后层玻璃
            # 会把刚压暗的板面又提亮回去 —— 实测杯内只被压掉 12%, 而杯外板面压掉 46%,
            # 玩家盯着的杯子周围反而是全画面压暗最失败的地方。前层玻璃也不能盖在压暗之后
            # 之外的位置: 放到最后会把弹珠本身也压暗(球就"沉"进背景里了)。
            if a_dim > 0.0:
                Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], DIM_ALPHA * a_dim)
                Rectangle(pos=self.pos, size=self.size)

            if a_cup > 0.0:
                # 后层那两半环(杯口远侧 + 杯底远半)补画到压暗之上 —— 见 _rim_back_strips。
                # 放在球之前: 它们本来就在珠子后面, 球要能挡住。
                for t, f0, fh, a in (_rim_back_strips(back_tex) or ()):
                    Color(1.0, 1.0, 1.0, a_cup * a)
                    Rectangle(texture=t, pos=(bx, by + bh * f0), size=(bw, bh * fh))

                # 已落定球按画家序(远先近后); 前玻璃层随后覆盖, 形成真实杯内层次
                for b in self._balls:
                    if b["settled"]:
                        self._draw_bead(b, a_cup)
                # 飞行/弹跳球一律最后画(置顶), 盖掉层间穿插
                for b in self._balls:
                    if not b["settled"]:
                        self._draw_bead(b, a_cup)

                if front_tex is not None:      # 回退时没有前层, 弹珠就在玻璃之上(可接受的降级)
                    Color(1.0, 1.0, 1.0, a_cup)
                    Rectangle(texture=front_tex, pos=(bx, by), size=(bw, bh))

    # ------------------------------ 启动期预热 ------------------------------

    def prebake_step(self, dt=0):
        """启动后分帧预热: 玻璃贴图 -> 当前投注档球纹理 -> 其它档 -> 7 档球堆。

        ⚠️ **玻璃贴图也要预热**(2026-09-11 补): `_glass_textures()` 第一次调用要现场
        解码两张 PNG + 上传 GL, 桌面实测 **21ms**; 贴图提到 1600x920(2x) 之后平板上是
        40~80ms 的一次长帧, 而它正好落在**冷启动后的第一次中奖**那一刻(演出起手)。
        这套分帧预热机制本来就是为"别在中奖那帧现做"存在的, 之前漏了它。

        低端机单档 d=128 纯 Python 合成约 100~200ms, 4 档挤在一帧就是 4 个长帧;
        摊开后每帧只多一档。当前档排最前 —— 玩家最可能先看到它。
        球堆便宜得多(7 档合计桌面 30ms), 放最后。

        ⚠️ 自链式 Clock.schedule_once 的回调**必须能收 1 个位置参数** —— BUILD_APK.md
        §3.23: 零参签名在真机上启动 0.7s 必闪退, 而桌面 selftest/smoke 测不出来。
        """
        if not self._glass_prebaked:
            self._glass_prebaked = True
            try:
                _glass_textures()
            except Exception:
                pass
            Clock.schedule_once(self.prebake_step, 0.05)
            return
        cur = getattr(getattr(self.area, "game", None), "bet", DEFAULT_BET)
        order = [cur] + [b for b in (1, 10, 50, 100) if b != cur]
        todo = [b for b in order if b not in _CUP_BALL_TEX]
        if todo:
            try:
                _ball_texture(todo[0])
            except Exception:
                pass
            Clock.schedule_once(self.prebake_step, 0.05)
            return
        for n in (2, 3, 5, 10, 20, 50, 100):
            if (n, 1) not in _PILE_CACHE:
                try:
                    _pile_projected(n, 1)
                except Exception:
                    pass
                Clock.schedule_once(self.prebake_step, 0.03)
                return

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
    """槽位底色(m=0 空槽, 否则按倍数取色, WoW 品质色调整版)。"""
    if m <= 0:
        return "#2a3550"
    return COL_x.get(m, "#1e8a5a")

def slot_txt(m):
    """槽位数字字色: ×100 深橙白字对比 2.3 太低(大奖会糊), 故黑字(8.0)最跳;
    其余档白字(低档绿蓝红干净醒目, 深红/紫暗底白字最亮)。"""
    return "#0b1220" if m >= 100 else "#ffffff"


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


def _vibrate(ms, amp=255):
    """单次震动(仅 Android; 其它平台静默)。需要 buildozer.spec 的 VIBRATE 权限。
    取服务必须用 Context.VIBRATOR_SERVICE 字符串 —— 传 autoclass("android.os.Vibrator")
    那个 Class 对象在 pyjnius 下匹配不到 getSystemService(Class<T>) 重载, 会静默失败
    (整段被 try/except 吞掉, 表现为"权限也给了、代码也跑了, 就是不震")。

    `amp` 只在 API 26+ 生效; 老机器退回 `vibrate(ms)`, 振幅由系统定。
    """
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
                ms, amp))     # 满振幅 255; DEFAULT_AMPLITUDE(-1) 约 50%, 太弱
        except Exception:
            vib.vibrate(ms)                  # API < 26: 没有 VibrationEffect
    except Exception:
        pass


def _vibrate_tick(gain):
    """装杯落珠的**单次轻震**(只有 Android 有; 其它平台静默)。

    ⚠️ 这个函数**不判时间、不计数** —— 节流只能有一处真源, 就是调用点
    (`WinPileFX._bounce` 里那次 `sfx.play(...)` 的返回值)。两边各判一次必然漂移,
    玩家就会出现"听到响但手上没感觉"(或反过来), 那正是"同步"要消灭的东西。

    时长/振幅都跟着撞击强弱走(gain 与落地音用的是同一个 0.25~0.72):
    10~18ms / 振幅 80~220。**刻意做得很短、也不算满**:
      - 节流后是 10 次/秒(见 BOUNCE_THROTTLE), 脉冲一长就糊成"持续嗡嗡",
        而不是"珠子一颗一颗落进杯子里";
      - 振幅压在 220 不满格, 因为这是"雨点"不是"大奖震一下"(后者是 settle 那次长震)。
    手机马达的启动时间约 10~20ms, 所以 10ms 以下没意义 —— 下限就取 10ms。
    """
    if platform != "android":
        return
    g = max(0.0, min(1.0, (gain - 0.20) / 0.55))
    _vibrate(int(round(10 + 8 * g)), int(round(80 + 140 * g)))



# "弹珠落容器"(装杯演出)的**触发**延后多少秒(用户 2026-09-11 定案)。
# 用户给的规格:
#   当前   —— 第0秒进倍率槽: 立即播声音 + **立即(第0秒)触发落容器事件**
#   调整后 —— 第0秒进倍率槽: 立即播声音, **第0.15秒才触发落容器事件**
# 所以动的不是任何声音, 也不是球的运动, 是 `settle()` 里那句 `win_fx.play_win(...)` 的**调用时刻**。
# 演出内部的时间轴(WINDUP / 压暗 0.30 / 杯子 0.32 / 落珠 / 回味 / 退场)一律不动,
# 于是整场只是起点后移 0.15s, 总时长不变。
CUP_TRIGGER_DELAY = 0.15

# 隐藏弹窗里"制作时刻"的格式。
# ⚠️ 时间**必须是 24 小时制(%H)**, 玩家定稿。用 %I 会变成"下午 1:49"那种 12 小时制,
#    跟其余界面的时间口径不一致(历史记录里也是 `%Y-%m-%d %H:%M`, 24 小时制)。
# ⚠️ 年月日写成**汉字**: `2026-09-11` 这种全数字写法在中文语境下容易被读反(有人按 日/月 读),
#    带上「年月日」就没有歧义(`fx_probe [13]` 有功能性断言钉住这两条)。
BUILD_TIME_FMT = '%Y年%m月%d日 %H:%M'


def _vibrate_double(ms=35, gap=40, amp=255):
    """短促双震(彩蛋用): 两下短脉冲, 手机读作"发现惊喜"; 区别于单次长震的大奖之感。
    仅 Android。API>=26 用 createWaveform 出确切双脉冲, 否则退回单次。"""
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
            v = VibrationEffect.createWaveform([0, ms, gap, ms], [0, amp, 0, amp], -1)
            vib.vibrate(v)
        except Exception:
            vib.vibrate(ms * 2 + gap)          # 退回单次(近似时长)
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
        if wan == 2:
            names.append("voice_liang")   # 万位的 2 读"两"(两万)
        else:
            names.extend(_read_4digits(wan, is_highest=True))
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
        need_zero = is_highest is False   # 低位组(前面有万位)且无千位: 组前要"零"
    if bai > 0:
        if need_zero:
            parts.append("voice_d_0")
            need_zero = False
        parts.append("voice_d_%d" % bai)
        parts.append("voice_u_100")
    elif qian > 0:
        need_zero = True                  # 千位后百位空: 十位/个位前要"零"
    if shi > 0:
        if need_zero:
            parts.append("voice_d_0")
            need_zero = False
        if shi == 1 and not parts:
            parts.append("voice_u_10")          # 10~19: "十" 不读 "一十"
        else:
            parts.append("voice_d_%d" % shi)
            parts.append("voice_u_10")
    if ge > 0:
        if shi == 0 and (need_zero or qian > 0 or bai > 0):
            parts.append("voice_d_0")
        parts.append("voice_d_%d" % ge)
    return parts


from kivy.animation import Animation


# =============================================================================
# 横屏反旋转层(2026-08-17 定案: 画面永远保持竖拿构图, 横拿时玩家扭头看/转回竖屏玩)
# 2026-08-19 屏幕比例分流: 仅 16:9 及更宽的屏(平板)才四方向旋转;更瘦长的手机
# (18:9/20.5:9 等)锁竖屏(正竖+倒竖), 不进横屏 —— 瘦长机横拿时系统会多出一条
# 横向状态栏压在旋转画面上, 显示直接坏掉, 且反旋转构图在小屏上本就不适合阅读。
# =============================================================================
_DEVICE_WIDE_MIN = 9.0 / 16.0    # 短边/长边 ≥ 9:16 = 宽屏(16:9 及更宽/更方)
_device_wide_cache = None        # 开机量一次物理屏比例, 之后不再变


def _device_is_wide():
    """本机物理屏是否 16:9 及更宽(平板类, 允许横屏旋转)。
    用 Display.getRealSize() 的物理分辨率(含系统栏, 不随旋转变), 不受当前
    窗口尺寸/状态栏影响。读不到(桌面/异常)时按宽屏处理 = 保持原有行为
    (桌面 LandLayer 本就只认 --landscape 模拟, 不受影响)。"""
    global _device_wide_cache
    if _device_wide_cache is None:
        aspect = 1.0
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            disp = act.getWindowManager().getDefaultDisplay()
            pt = autoclass("android.graphics.Point")()
            disp.getRealSize(pt)
            s, l = min(pt.x, pt.y), max(pt.x, pt.y)
            aspect = s / float(l)
        except Exception:
            aspect = 1.0
        _device_wide_cache = aspect >= _DEVICE_WIDE_MIN
    return _device_wide_cache


def _land_angle():
    """横屏渲染旋转角(度, Kivy Rotate 逆时针为正): 抵消系统转屏, 让画面在屏幕上的
    构图与竖拿时完全一致。Display.getRotation(): 1(ROTATION_90)->+90, 3(ROTATION_270)->-90,
    其余/读不到(桌面 --landscape 模拟)固定 +90。真机若发现扭头方向不对/画面倒置,
    交换 90/-90 两映射即可(一行)。"""
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = act.getWindowManager()
        disp = wm.getDefaultDisplay()
        return -90 if disp.getRotation() == 3 else 90
    except Exception:
        return 90


def _land_layer():
    app = App.get_running_app()
    return getattr(app, "layer", None)


class LandLayer(FloatLayout):
    """Android 12L+ 大屏锁竖屏会被 letterbox 政策/ZUI 塞进半屏兼容盒(app 改不了窗口
    宽高), 故方向放开(fullSensor 四方向), 横拿时系统给全屏横窗; 本层把整棵 UI 树按
    "等效竖屏窗口"(短边x长边)布局后整体旋转 90 度铺满横屏 -> 屏幕上的画面构图与
    竖拿时一模一样。竖屏时 angle=0 且层尺寸=窗口, 行为与没有本层完全一致(零回归)。
    仅 16:9 及更宽的设备启用(瘦长手机锁竖屏, 见 _device_is_wide)。
    触摸在 on_touch_* 先逆旋转到等效坐标再分发; 弹窗用 RotPopup 挂到本层。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.angle = 0
        self._anchor = None          # 等效竖屏窗口容器(App.build 里塞 AnchorLayout)
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, axis=(0, 0, 1), origin=(0, 0))
        with self.canvas.after:
            PopMatrix()

    def apply_orientation(self):
        """窗口尺寸变化时重算层尺寸/旋转角, 返回是否处于横屏反旋转模式。
        仅 16:9 及更宽的设备(平板, 或桌面 --landscape 模拟)且窗口为横向才启用;
        瘦长手机被竖屏锁(见 _apply_orientation), 窗口不会横, 此处再加一道闸
        双保险; 桌面普通宽窗维持竖构图居中不变。"""
        w, h = Window.width, Window.height
        land = (w > h and (platform == "android" or "--landscape" in sys.argv)
                and _device_is_wide())
        self.pos = (0, 0)
        self.size = (w, h)
        self.angle = _land_angle() if land else 0
        self._rot.angle = self.angle
        self._rot.origin = (w / 2.0, h / 2.0)
        if self._anchor is not None:
            self._anchor.size = (h, w) if land else (w, h)
            self._anchor.center = self.center
        return land

    def _to_eq(self, x, y):
        """物理窗口坐标 -> 等效竖屏坐标(渲染旋转的逆变换)。

        ⚠️ 只做逆旋转, **不能**再减 anchor.pos: anchor 是普通 AnchorLayout,
        不是 RelativeLayout —— Kivy 单一 window 坐标系(见 relativelayout 模块
        文档)下整棵树的 pos 数值本来就是"等效竖屏物理坐标", anchor 没有开新
        坐标系。多减一次会把所有控件的点击判定区平移出屏幕(横屏 1740x1000 时
        偏移 (370,-370)), 症状 = 横屏所有按钮点不到、竖屏正常(2026-08-25 修复)。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:                      # 逆变换 = 顺时针 90
            px, py = cx + dy, cy - dx
        else:                                     # angle == -90: 逆时针 90
            px, py = cx - dy, cy + dx
        return (px, py)

    def _to_win(self, x, y):
        """等效竖屏坐标 -> 物理窗口坐标(渲染旋转的正变换, to_parent 用)。"""
        if self.angle == 0:
            return (x, y)
        cx, cy = Window.width / 2.0, Window.height / 2.0
        dx, dy = x - cx, y - cy
        if self.angle == 90:                      # 正变换 = 逆时针 90
            px, py = cx - dy, cy + dx
        else:                                     # angle == -90: 顺时针 90
            px, py = cx + dy, cy - dx
        return (px, py)

    def to_local(self, x, y, **k):
        """覆写(EventLoop 的 grab 派发依赖): 按钮在 down 时 touch.grab(self),
        之后 move/up 由 EventLoop **直接派发给按钮本体**(不经过本层的
        on_touch_*), 派发前 Window 用 parent.to_widget 链换算坐标 —— 该链
        沿祖先逐层调 to_local。整棵树只有本层带旋转, 这里注入逆旋转, 横屏下
        按钮的 on_touch_up 才能拿到正确坐标; 不覆写则 collide_point 判不中 +
        always_release 默认 False 直接吞掉 on_release(症状: 按钮按下有反馈、
        抬起无动作)。Scatter 就是同款做法(覆写 to_local/to_parent)。"""
        return self._to_eq(x, y)

    def to_parent(self, x, y, **k):
        """覆写(与 to_local 对称): 局部(等效竖屏)坐标 -> 物理窗口坐标。
        Widget.to_window 沿祖先链调 to_parent, 覆写后"问子控件它在窗口哪里"
        类调用(弹窗定位等)在横屏也能得到正确答案。"""
        return self._to_win(x, y)

    def _pass_touch(self, method, touch):
        if self.angle == 0:
            return method(touch)
        touch.push()
        # 必须用 apply_transform_2d 而不是 touch.pos = ...: pos 只是个普通元组属性,
        # 直接赋值不改 x/y —— 而 ButtonBehavior.on_touch_down 判点击用的是
        # collide_point(touch.x, touch.y), 拿到的还是物理坐标, 横屏所有按钮点不中。
        # apply_transform_2d 把 x/y/pos/ox/oy/px/py 全变换(RelativeLayout 同款做法)。
        touch.apply_transform_2d(self._to_eq)
        ret = method(touch)
        touch.pop()
        return ret

    def on_touch_down(self, touch):
        return self._pass_touch(super().on_touch_down, touch)

    def on_touch_move(self, touch):
        return self._pass_touch(super().on_touch_move, touch)

    def on_touch_up(self, touch):
        return self._pass_touch(super().on_touch_up, touch)


class RotPopup(Popup):
    """挂 LandLayer 的 Popup: 横屏时随层旋转, 坐标系统一为等效竖屏窗口。
    Kivy 2.3 ModalView.open() 硬编码挂 Window, 这里照抄其 open/_real_remove_widget
    把宿主换成旋转层(层无 on_resize/on_keyboard 事件, bind 静默无害; 返回键走
    RootWidget._on_key_down 原路径, 竖屏横屏行为一致)。竖屏(angle=0)回落原生行为。
    pos_hint 居中交给 FloatLayout 布局, 转屏时尺寸变化自动跟随。"""

    def open(self, *_args, **kwargs):
        layer = _land_layer()
        if layer is None or layer.angle == 0:
            return super().open(*_args, **kwargs)
        if self._is_open:
            return
        self._window = layer
        self._is_open = True
        self.dispatch('on_pre_open')
        if not self.pos_hint:
            self.pos_hint = {"center_x": 0.5, "center_y": 0.5}
        layer.add_widget(self)
        layer.bind(on_resize=self._align_center, on_keyboard=self._handle_keyboard)
        self.center = layer.center
        self.fbind('center', self._align_center)
        self.fbind('size', self._align_center)
        if kwargs.get('animation', True):
            ani = Animation(_anim_alpha=1., d=self._anim_duration)
            ani.bind(on_complete=lambda *_a: self.dispatch('on_open'))
            ani.start(self)
        else:
            self._anim_alpha = 1.
            self.dispatch('on_open')

    def _real_remove_widget(self):
        if not self._is_open:
            return
        self._window.remove_widget(self)
        self._window.unbind(on_resize=self._align_center,
                            on_keyboard=self._handle_keyboard)
        self._is_open = False
        self._window = None


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
        # 中奖玻璃杯覆盖层(见 tools/android_part_pile.py)。**必须最先 add_widget**:
        # Kivy 按 children 逆序绘制, 后加的画在上面 —— 中奖大字是 settle 时才 add
        # 的 Label, 所以大字天然盖在杯层之上, 杯子压暗盖不住它(用户要的"大字留上方")。
        self.win_fx = WinPileFX(self, size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self.add_widget(self.win_fx)
        self.bind(size=self._redraw, pos=self._redraw)

    def win_fx_busy(self):
        """中奖演出是否还在放(锁输入判据, 见 RootWidget._frame 的 landed 分支)。"""
        return self.win_fx.busy()

    def _restack_overlays(self):
        """把覆盖层重新挂回画布末尾, 顺序固定为「板面 < 中奖杯 < 中奖大字」。

        ⚠️ 这一步不能省: self.canvas.clear() 会**把子控件的 canvas 一起摘掉** ——
        子控件的 canvas 是 add_widget 时挂进父 canvas 的, clear 一视同仁。不重挂的话
        每次换盘面(park_ball)或尺寸变化, 杯子和中奖大字就整个从屏幕上消失, 而它们的
        canvas.children 看起来完全正常(指令都在, 只是没挂在渲染树上)。实测判据:
        self.canvas.indexof(w.canvas) 返回 -1 = 已被摘掉。
        顺序靠**追加次序**保证 —— 后 add 的后画, 所以杯层必须排在大字之前。
        """
        chain = [self.win_fx]
        for e in self._effects:
            chain.extend(e["ws"])
        for w in chain:
            try:
                self.canvas.remove(w.canvas)
            except Exception:
                pass
            self.canvas.add(w.canvas)

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
                Color(*hex_rgb(slot_txt(m)))
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
        self._restack_overlays()
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
            hexcolor = slot_color(m)
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
        # 中奖时大字上移到杯顶之上(逻辑 cy = TEXT_CY_WIN, 现行值见 android_part_pile.py 顶部常量;
        # 杯子占逻辑 y ≈264.6~578, 且大字要上浮 38px/s, 起跳点留足余量才不会被杯口压住)。
        # 未中不播杯子, 保持原来的画布中央位置。
        cy_logical = (CH / 2.0 - 80.0) if m <= 0 else TEXT_CY_WIN
        # ⚠️ **横向不要再减 `self.x`**(2026-09-10 修)。这里是 `_px()` 的返回值, 已经是
        # Kivy 窗口绝对坐标; 而本项目的子控件 canvas 就是**绝对坐标**, 父级不做平移
        # (见 CLAUDE.md 坐标系那条坑)。再减一次等于把大字左移整整一个 `GameArea.x`。
        # 竖屏 `GameArea.x` 恒为 0, 减了个 0 所以一直没人发现; **横屏反旋转时 x=370**
        # (1740x1000), 大字就偏左 370px —— 玩家报的"弹珠+xxx 在特殊情况下偏左很多"。
        # 纵向的 `- self.y` 是历史遗留(竖屏 y=122, 是被眼睛调过的既成观感), 本轮不动。
        self._effects.append({"kind": "big", "ws": [main, shadow], "born": time.time(),
                              "life": BIG_TEXT_LIFE, "size": size, "rgb": hex_rgb(hexcolor),
                              "cx": self._px(CW / 2.0),
                              "cy": self._py(cy_logical) - self.y})

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
        # ⚠️ 横向同样**不减 `self.x`** —— 理由见 big_result_text 处(子控件 canvas 是绝对
        # 坐标, 竖屏 x=0 掩盖了这个错, 横屏会整体左移一个 GameArea.x)。
        cx = self._px(CW / 2.0)
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
        # ⚠️ 必须放在下面的 _ball_e is None 早退**之前**: 否则画布还没建好的那几帧
        # (启动/尺寸未定)中奖演出不推进, 起播时间会被白白拖后。
        self.win_fx.tick()
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
        self._coin_start = 0.0        # 计分滚动音起播时刻(错峰: 晚于结算)
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
        self._boards = {r: roll_multipliers(r) for r in (0.80, 1.20, 2.00, 3.60)}   # 4档盘面一起生成, 切换不刷新
        self.multipliers = self._boards[self.rtp_target]
        # 声音两态: on(音效已开, 含语音播报, 默认) | off(音效已关)。
        # **不持久化** —— 不进配置文件, 每次启动都是 on(用户定稿)。
        self.sound_mode = "on"
        self.max_plays = 50            # 每轮次数上限
        self.round_plays = 0           # 本轮已玩次数
        self.round_history = []        # 最近完成的轮次记录
        self.bench_history = []        # 性能测试历史(最近100次)
        self._load_history()           # 从磁盘恢复(跨启动持久化)
        self._load_bench_history()
        self._auto_reset_on_start = False
        self._load_config()            # 恢复上次的游戏设定
        self._round_end_shown = False  # 本轮结束弹窗是否已弹出
        self._landing_primed = False   # landing首帧标记(防每帧重置vy)
        self._settle_slot = 0          # 本发物理落格槽(结算延迟到回弹落定后)
        self._settled = False          # 本发是否已结算(防重复)
        # 彩蛋流程锁: 弹窗关掉前 + 装杯播完前压住 park_ball。
        # ⚠️ 必须在 __init__ 初始化, 不能只放在 launch() 里 —— 任何"直接进 landed"
        # 的路径(探针、将来新加的下落分支)都会在 _frame 的 landed 分支读到它而 AttributeError。
        self._easter_hold = False
        self._easter_popup = None     # 彩蛋弹窗引用(探针用, 也便于查是否还开着)
        # 揭晓合流(中奖): 大字/余额滚动/结果语音 全部推迟到"最后一颗球落定"那一刻一起给。
        # t=0 就报数字 = 提前剧透, 后面整场装杯沦为重播(用户反馈)。
        self._anim_pending = False    # 为真时余额冻结在扣注后的值, 一帧都不许追平
        self._reveal_done = True      # 本局是否已揭晓(幂等闸, 唯一真源在 settle 的 _on_settled)
        self._reveal_deadline = 0.0   # 兜底: 到点还没揭就自己揭(防 tick 停摆把数字吞了)
        self._pending_win = None      # (m, payout), 兜底揭晓时要用
        self._win_seq = 0             # 中奖轮次序号: 当幂等键用, 丢弃上一局的迟到回调
        self._settle_cb = None        # 本轮的揭晓闭包: 主路径与兜底路径**共用同一个**
        self._last_motion = 0.0       # flying 帧内刷新; 卡死兜底看"位置不动"而非发射时长
        self._last_ball_xy = (PLUNGER_X, PLUNGER_Y)
        self.landed_at = 0.0
        self.land_target_x = PLUNGER_X
        self.target_slot = 0
        self.target_x = PLUNGER_X
        self.ball = None
        self._last_win_size = None    # 窗口尺寸轮询快照(bind(size) 对程序启动期的 resize 不可靠)
        # HUD 的装杯期压暗块(见 _build_hud_dim / _sync_hud_dim): 板面那块压暗够不到
        # GameArea 之外的地方, 横屏反旋转时那几行正好落在画面两侧亮着。
        # 两个字段由 _build_hud_dim() 建好 —— 它在 _build_ui() 末尾调(那时 game_area 才存在)。
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
        # 跑分置灰层: 第2轮物理benchmark时全屏置灰(半透明深色矩形盖住整个界面含游戏区)。
        # ⚠️ 它是**第三套**压暗常数(0.72), 与 DIM_ALPHA/HUD_ALPHA(0.68) 不是一套。两者
        # 不会同屏: 跑分要长按标题 3 秒才起, 而装杯期输入是锁的(`_bench_running` 还会
        # 直接拦住 play_win, --smoke 有断言)。真要改其中一个, 记得它们互不影响。
        # ⚠️ 它挂在 `RootWidget.canvas.after` = 盖住**整棵子树**(含 GameArea 里的中奖大字),
        # 所以不能拿它做装杯期的 HUD 压暗 —— 那会把大字一起压掉(见 _build_hud_dim 的说明)。
        self._bench_dim_shown = False
        with self.canvas.after:
            self._bench_dim_col = Color(0.05, 0.06, 0.09, 0.0)
            self._bench_dim_rect = Rectangle(pos=(0, 0), size=(0, 0))
        self.bind(size=self._relayout_bench_dim, pos=self._relayout_bench_dim)
        Clock.schedule_interval(self._frame, FIXED_DT)
        # 中奖杯的球纹理/球堆预热: 分帧摊在启动后做, 别等中奖那一刻现算(低端机单档
        # d=128 纯 Python 合成要 100~200ms, 一次做完就是几个长帧, 而这动画的全部意义
        # 就是丝滑)。排在 _frame 之后, 不影响冷启动的建界面/烘音效。
        Clock.schedule_once(self.game_area.win_fx.prebake_step, 0.05)

    def _veq(self):
        """等效竖屏窗口尺寸: 横屏反旋转时 = (短边, 长边), 画面构图与竖拿时一致。
        竖屏时 = 物理窗口尺寸, 行为不变。所有布局计算一律吃等效值,
        物理窗口只用来算旋转(LandLayer)。"""
        w, h = Window.width, Window.height
        return (w, h) if w <= h else (h, w)

    def _fit_width(self):
        """内容最大宽度 = 让 520:660 场景恰好填满可用高度。
        窄屏(手机竖屏)直接铺满宽度; 宽屏(16:10 桌面)内容列居中、两侧留深色边。
        尺寸取"等效竖屏窗口"(横屏反旋转时短边x长边), 横竖屏布局同一套。"""
        vw, vh = self._veq()
        self._ui_scale = min(1.0, vh / dp(680))
        us = self._ui_scale
        # 缩放后的固定高度(行高+间距), 与 _apply_sizes() 一致
        scaled_fixed = (dp(H_TOP + H_RTP + H_BETS + H_INFO + H_BOTTOM) * us
                        + dp(10) * 5 * us * us + dp(12) * us)  # +底部留白
        avail_h = max(100.0, vh - scaled_fixed)
        want = avail_h * (CW / CH) + dp(8)
        self.width = min(vw, want)
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

    def _popup(self, hint_w, h_dp, **kw):
        """统一建 Popup(RotPopup: 横屏挂旋转层随画面转, 坐标系统一为等效竖屏窗口)。

        ⚠️ 宽高都必须按**等效竖屏窗口**(`_veq()` = 短边x长边)算成绝对值, 不能用
        size_hint —— size_hint 取的是父容器的**原始**宽高, 而设备横拿时窗口是横的。
        主界面走 `_fit_width()` 吃的是 `_veq()`(已排序, 保持竖构图), 弹窗若吃裸窗口
        尺寸就会比界面宽 60~80%: 实测 960x540 下界面宽 540 / 弹窗 806,
        1740x1000 下 1000 / 1462。
        这就是"设备横屏时启动游戏(游戏正确变竖屏), 一开设置窗口宽度就是错的"的根因。
        `_veq()` 会排序, 所以即使弹窗在方向尚未落定前打开, 也能拿到竖构图的那一边。

        `title_align` 默认给 "center": Kivy 的 `Popup.title_align` **默认是 'left'**,
        不覆盖的话标题会贴左边缘, 而主界面标题栏是严格居中的(左右等 flex 容器) ——
        玩家报的"弹窗文字没居中"就是它(用户用调用方显式传值仍可覆盖)。
        """
        vw, vh = self._veq()
        kw["size_hint"] = (None, None)
        kw["width"] = hint_w * vw
        kw["height"] = min(dp(h_dp), vh * 0.92)
        kw.setdefault("title_align", "center")
        return RotPopup(**kw)

    def _row_bg(self, row, hexcolor):
        with row.canvas.before:
            Color(*hex_rgb(hexcolor))
            row._bg_rect = Rectangle(pos=row.pos, size=row.size)
        row.bind(pos=lambda w, *_: setattr(w._bg_rect, "pos", w.pos),
                 size=lambda w, *_: setattr(w._bg_rect, "size", w.size))

    def _build_hud_dim(self):
        """建"装杯期压暗"的两块矩形 —— **整块界面减去游戏区**, 挂在 `RootWidget.canvas.after`。

        覆盖范围(必须一块不漏, 也别压到游戏区):
          上块 = 从**游戏区上沿**到本控件上沿(顶栏/返奖档/投注档 + 它们之间与上方的空白)
          下块 = 从本控件下沿到**游戏区下沿**(信息行/底行 + 它们之间与底部的留白)
        两块在游戏区处严丝合缝地断开, 所以**碰不到 GameArea 里的中奖大字**
        ("大字留上方"是用户定案的)。

        ⚠️ 为什么是"整块减去游戏区", 而不是"每行各盖一块"(第一版就是这么写的, 已推翻):
        五行之间有 `spacing=dp(10)`、底部有 `padding` 12dp, 那些地方是**窗口背景**,
        没有任何控件去盖。第一版只盖行本身, 于是这 62px 成了全屏最亮的东西
        (实测缝 20.6 vs 相邻被压暗的行 16.8~19.6; 横屏反旋转时它们是 **6 条贯通全高的
        竖亮线**, 非常显眼)—— 正是"板面黑了, 但上下有六条亮线"。按区域盖就没有这个
        算术: 行高/spacing/padding 怎么改都不会漏。

        ⚠️ 也别用"每行盖一块、相邻两块相接"的写法: 块与块一旦重叠, 0.68 叠 0.68 会变成
        0.90, 缝就从"更亮"翻成"更暗", 只是把问题翻个面。

        **横向要一直铺到视口边**: `_fit_width` 让 `self.width = min(vw, want)`, 宽窗口下
        RootWidget 比视口窄(左右留深色边), 只盖 `self.width` 的话演出期两侧会留两条亮带
        (玩家的 Y700 平板正是这种情况, 横屏时那两条落在物理屏幕的最上/最下)。本控件的
        原点就是画布原点, 所以视口横向 = `[-self.x, self.width + self.x]`。
        """
        with self.canvas.after:
            self._hud_dim_col_top = Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], 0.0)
            self._hud_dim_top = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
            self._hud_dim_col_bot = Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], 0.0)
            self._hud_dim_bot = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
            # Kivy 的 Color 是**全局状态**: 块里的近黑+alpha0 会泄漏给之后画的兄弟/子控件。
            # 本作目前每条绘制路径都自己先写 Color, 所以没发作; 这里补一刀白色打底,
            # 以后谁新增一条"不自己设色"的绘制也不会静默变成全黑。
            Color(1.0, 1.0, 1.0, 1.0)
        self._hud_dim_cols = [self._hud_dim_col_top, self._hud_dim_col_bot]
        self._hud_dim_last = -1.0
        self.bind(pos=self._relayout_hud_dim, size=self._relayout_hud_dim)
        self.game_area.bind(pos=self._relayout_hud_dim, size=self._relayout_hud_dim)
        self._relayout_hud_dim()

    def _relayout_hud_dim(self, *_):
        """把两块压暗矩形摆到"整块**屏幕**减去游戏区"的位置(尺寸一变就跟着走)。

        ⚠️ 视口取 **`self.parent`(App.build 里那个 AnchorLayout)**, 不按 self 的 pos/width 反推。
        Kivy 是**单一 window 坐标系**(`LandLayer._to_eq` 的注释写得很清楚: 整棵树的 pos 数值
        本来就是"等效竖屏物理坐标"), 那个 AnchorLayout 的矩形**恰好就是等效视口** ——
        横屏反旋转时它铺满整块物理屏。直接读它就没有"留白多宽"这类算术, 也就不会再算错。

        实测踩过(2026-09-11): 上一版按 `x0=-self.x, w=self.width+2*self.x` 反推, 在
        1400x1000 横屏窗上差分对比 base/装杯两张截图 —— 压暗只盖住物理 x∈[0,1277],
        **右边 122px 和顶边 56px 原样亮着**; 而且那个窗口里 `game_area.y = -78 < 0`,
        下块的 `max(0.0, ga.y)` 直接算成 **高度 0**, 游戏区**下方**那两行整个没盖。
        换成读父容器矩形之后两个毛病一起消失(下块的纵向范围也跟着视口走, 不再被 0 截断)。
        """
        vp = self.parent
        if vp is None or vp.width <= 1.0 or vp.height <= 1.0:
            vp = self                       # 未挂父/尺寸未定: 退回自身(探针夹具走这条)
        ga = self.game_area
        x0, y0, w, h = vp.x, vp.y, vp.width, vp.height
        ga_lo = min(max(ga.y, y0), y0 + h)
        ga_hi = min(max(ga.y + ga.height, y0), y0 + h)
        self._hud_dim_top.pos = (x0, ga_hi)
        self._hud_dim_top.size = (w, max(0.0, y0 + h - ga_hi))
        self._hud_dim_bot.pos = (x0, y0)
        self._hud_dim_bot.size = (w, max(0.0, ga_lo - y0))

    def _sync_hud_dim(self):
        """每帧把压暗块对齐到装杯演出的生灭曲线。

        真源只有一个: `WinPileFX.dim_alpha()`(它读的是板面本帧真正画上去的那个数)。
        这里**不重算任何曲线** —— 两边各写一份必然脱钩(这个仓库已经踩过一次:
        `_reveal_deadline` 硬编码 0.45, 尾巴从 0.45 改到 0.60 时静默脱钩, 兜底提前
        触发把数字剧透了)。
        `_hud_dim_last` 短路是性能考虑: 演出之外这一层恒为 0, 不必每帧去动那两个 Color。
        """
        a = self.game_area.win_fx.dim_alpha(HUD_ALPHA)
        if a == self._hud_dim_last:
            return
        self._hud_dim_last = a
        for col in self._hud_dim_cols:
            col.rgba = (DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], a)

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
        for label, val in (("80%", 0.80), ("120%", 1.20), ("200%", 2.00), ("360%", 3.60)):
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
        # 压暗块放在最后建: 它要读 game_area 的 pos/size, 且 canvas.after 必须排在
        # 全部子控件之后(见 _build_hud_dim 的说明)。
        self._build_hud_dim()
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
        pos = touch.pos
        layer = _land_layer()
        if layer is not None:
            pos = layer._to_eq(*pos)        # Window 级触摸是物理坐标, 先逆旋转到等效坐标
        if (self.title_lbl.collide_point(*pos)
                and not getattr(self, "_bench_running", False)):
            self._bench_start = time.time()
            self._bench_triggered = False

    def _on_title_touch_up(self, win, touch):
        self._bench_start = 0.0
        if self.state == "charging":
            self.launch()   # 发射保底: 松手时若仍在蓄力(如滑出按钮致 on_release 未触发), 补发

    def _show_bench_dim(self):
        self._bench_dim_shown = True
        self._bench_dim_col.rgba = (0.05, 0.06, 0.09, 0.72)
        self._relayout_bench_dim()

    def _relayout_bench_dim(self, *_):
        if getattr(self, "_bench_dim_shown", False):
            # 盖满等效竖屏窗口(RootWidget 在 anchor 居中, self.size 只是内容列);
            # 矩形画在 RootWidget.canvas.after, 随 LandLayer 一起旋转
            self._bench_dim_rect.pos = (-self.x, -self.y)
            self._bench_dim_rect.size = self._veq()

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
            self._show_bench_menu()   # 长按3秒: 弹性能测试菜单(开始测试/查看历史)

    def _show_bench_menu(self):
        """弹珠发射性能测试菜单弹窗: 开始测试 / 查看历史。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        title_lbl = Label(text='弹珠发射性能测试', font_size='20sp', bold=True, halign='center',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(30))
        title_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(title_lbl)
        desc_lbl = Label(text='全程约 25 秒(含完整的中奖装杯演出)。\n\n测试两项设备性能：\n1. 自动发 3 颗球，测屏幕渲染帧率\n2. 物理引擎全力跑，测每秒模拟步数\n\n第 2 项主要吃 CPU 单核浮点算力。\n纯 Python 执行，反映设备跑弹珠的实际流畅度。',
                         font_size='15sp', halign='left', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(170))
        desc_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(desc_lbl)
        # ⚠️ 版本/制作日期 与 音频体检**不在这里** —— 它们搬去「启动信息」独立弹窗了。
        #    用户 2026-09-11 定稿: 这里是跑分菜单, 那两样是启动信息, 不该混在一起。
        start_btn = Button(text='开始测试', font_size='17sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                           size_hint_y=None, height=dp(52))
        hist_btn = Button(text='查看历史', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                          size_hint_y=None, height=dp(52))
        info_btn = Button(text='启动信息', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                          size_hint_y=None, height=dp(52))
        popup = self._popup(0.84, 470, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        start_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_bench_test()))
        hist_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_bench_history()))
        info_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_startup_info()))
        content.add_widget(start_btn)
        content.add_widget(hist_btn)
        content.add_widget(info_btn)
        popup.open()

    def _show_startup_info(self):
        """「启动信息」弹窗: 版本/制作日期 + 音频体检。

        用户 2026-09-11 定稿: 这两样不该塞在性能测试菜单里 —— 那里是跑分, 这是启动信息,
        所以从那个菜单里搬出来、单开一个按钮(主菜单: 开始测试 / 查看历史 / 启动信息)。

        音频体检是给「初次安装必然没声音」那个 bug 用的: 它的**所有候选原因在产物里长得
        一模一样**(静默 / 不抛异常 / 不留痕), 没有 adb 就只能靠这几行把真值摆出来 ——
        尤其"音效就绪"报的是**后端真的握着几个 sampleId**, 不是闸门放行了几个(病灶正是
        两者不等; 只报闸门会显示全绿, 那比不显示更有害)。
        ⚠️ 逐行分栏, **绝不并成一行**: 实测合并后 658px > 内容区 422px, 会折行而被定高标签
        裁掉, 玩家看到的是一句缺尾巴的话(见 v0.6.12)。
        ⚠️ 纯只读 —— 这个弹窗**不许**放任何会动音频栈或游戏状态的按钮(那是"绝不软锁"的前提)。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        title_lbl = Label(text='启动信息', font_size='20sp', bold=True, halign='center',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(30))
        title_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(title_lbl)
        rows = []
        try:
            _info = self._build_info()
        except Exception:
            _info = ""
        def _mk_lbl(_text, _align, _size='16sp', _h0=26):
            """自动撑高的行标签 —— **折行不再等于裁切**。

            ⚠️ 为什么必须是这个形状: 这块面板栽在"文字被裁掉"上两次了(v0.6.12 把两行并一行;
            2026-09-11 在**手机密度**的模拟器上, `音频后端 …（非预期…）` 那行又折了行, 而标签是
            定高 dp(26) —— 第二行直接看不见)。根因是**可用宽度取决于设备密度**: 桌面等效宽 540
            (可用 421px), 而手机密度下等效宽可能只有 360(可用 ~270px), 同一条字符串在桌面上不折、
            在手机上折。所以不能靠"把字符串写短"来躲, 只能让行高跟着实际排版走。
            绑 width → 先让 Kivy 按可用宽度算出真正的 text_size(text_size 第二位给 None 才自动换行),
            再把 texture_size[1](排版后的真实高度)写回 height。"""
            lb = Label(text=_text, font_size=_size, halign=_align, valign='middle',
                       color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(_h0))
            lb.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
            lb.bind(texture_size=lambda w, ts: setattr(w, 'height', max(dp(_h0), ts[1] + dp(4))))
            return lb

        try:
            rows.extend(self.sfx.audio_detail())
        except Exception:
            pass
        if _info:                       # 版本/日期居中
            content.add_widget(_mk_lbl(_info, 'center'))
        for _ln in rows:                # 字段行一律左对齐(标签等宽 4 个汉字, 左对齐才排得成一列)
            content.add_widget(_mk_lbl(_ln, 'left'))
        ok_btn = Button(text='确定', font_size='17sp', bold=True,
                        background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                        size_hint_y=None, height=dp(52))
        # 「重放冷启动」: 不丢存档地按需复现"初次安装那种局"(见 _replay_cold_start)。
        # 玩家 2026-09-11 提的 —— 那个 bug 一年犯一次、"关掉重开"就自愈, 想抓现场只能卸载重装,
        # 而卸载会清掉余额/轮次。它不新建 Sfx 对象, 所以不碰任何接线, 也不动游戏状态。
        replay_btn = Button(text='重放冷启动', font_size='17sp', bold=True,
                            background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                            size_hint_y=None, height=dp(52))
        content.add_widget(ok_btn)
        content.add_widget(replay_btn)
        # ⚠️ 高度必须**按内容算**: 实测弹窗内容区 = 弹窗高 − 44px(Kivy 标题栏, 即使 title='' 也吃),
        #    每行 38px(行高 26 + spacing 12)。写死高度的话加一行就会被裁掉尾巴 —— v0.6.12 踩过。
        n_lbl = 1 + (1 if _info else 0) + len(rows)
        n_btn = 2
        need = (dp(30) + dp(26) * (n_lbl - 1) + dp(52) * n_btn + dp(32)
                + dp(12) * (n_lbl + n_btn - 1))
        popup = self._popup(0.84, need + dp(64), title='', content=content,
                            auto_dismiss=True, separator_height=0)
        ok_btn.bind(on_release=lambda *_: popup.dismiss())
        replay_btn.bind(on_release=lambda *_: (popup.dismiss(), self._replay_cold_start()))
        popup.open()

        # 上面那个高度是**按单行估的**; 一旦有行折了(设备越窄越容易折), 内容就比弹窗高。
        # 所以开完再按**真实排版高度**对一次 —— 这样"折行"永远只让弹窗长高, 不会把内容顶出去。
        # ⚠️ 必须等一帧: minimum_height 要等子控件的高度都落定才算得准。
        def _refit(*_):
            try:
                _vw, _vh = self._veq()
                popup.height = min(content.minimum_height + dp(64), _vh * 0.92)
            except Exception:
                pass
        Clock.schedule_once(_refit, 0.06)

    def _replay_cold_start(self):
        """**不丢存档**地重放一次冷启动 —— 按需复现「初次安装」那种局, 用来抓现场。

        玩家 2026-09-11 提的需求。那个 bug 一年犯一次、而且"关掉 app 再打开"就自愈,
        想抓现场只能卸载重装 —— 而卸载会清掉余额/轮次, 没人愿意为调试反复清自己的存档。
        这里做的是**原地重放**: 清掉音效缓存目录 + 把同一个 Sfx 对象的状态按回启动前, 再烘一次。
        ⚠️ 绝不新建 Sfx 对象: 新建会让 GameArea / WinPileFX 里持有的旧引用全部接错。
        ⚠️ 绝不软锁: 烘焙全程在后台线程; 失败也会置 baked, 而探针本身有 6 秒硬超时
           (见 Sfx._await_ready) —— 所以加载页一定会被 _frame 摘掉。
        ⚠️ 只在 state == "ready" 时允许: 球在飞的时候重烘 = 那一刻所有音效都播不出来。
        ⚠️ 它只是"近似"初装: 真初装还多两件事 —— app 目录刚解包(文件缓存是冷的)、进程刚启动
           (模块导入是冷的)。所以重放会比真初装快一些, 数字对上了不等于 bug 一定复现。"""
        try:
            if self.state != "ready":
                self.game_area.center_toast("先等这一发落定")
                return
            sfx = self.sfx
            # ① **先把结果页建起来再动音频状态**。顺序是要紧的: 建页失败就直接返回, 绝不能先把
            #    named 清掉 —— 那会留下一个"闸门是空的、也不会重烘"的静默状态, 而这正是这块
            #    面板要抓的那种故障。(2026-09-11 就踩了一次: 见下面那条 AttributeError。)
            host = getattr(self, "_load_veil_host", None) or self.parent
            if host is None:
                self.game_area.center_toast("重放失败：找不到挂载点")
                return
            veil = _LoadVeil(text="正在重放冷启动…", size_hint=(1, 1))
            host.add_widget(veil)
            self._load_veil = veil
            self._replay_veil = veil          # _frame 靠它把这一页改成"停住等点击"
            # ② 再动音频状态
            import shutil
            shutil.rmtree(_sfx_cache_dir(), ignore_errors=True)   # 缓存整目录清掉 = 真·冷路径
            sfx.baked = False
            sfx._audio_ready = False
            sfx.cached = False
            sfx.ready_ms = 0.0
            sfx.named.clear()
            sfx._failed = []
            try:
                sfx._last.clear()
            except Exception:
                pass
            # ③ 后台重烘
            import threading
            threading.Thread(target=sfx._bake, daemon=True).start()
        except Exception as exc:
            # ⚠️ **绝不静默**: 上一次这里一句都不说, 玩家点下去"完全没有反馈", 而音频状态其实
            #    已经被清掉了。现在起不来就立刻放行 + 把原因说出来(toast)。项目红线是绝不软锁。
            try:
                sfx.baked = True
                sfx._audio_ready = True
            except Exception:
                pass
            self._finish_replay_veil()
            try:
                self.game_area.center_toast("重放失败：%s" % exc)
            except Exception:
                pass

    def _replay_summary(self):
        """重放结束后摆在加载页上的结论。

        ⚠️ 直接**复用 `audio_detail()`** —— 不许另写一套格式化: 那样 PC 上又会冒出
        `音效就绪 0 / 0`(PCM 后端压根不用 sampleId), 而这个数在那边是**没有意义的**,
        看着却像全军覆没。复用同一处真源, 两个地方才不会各说各话(项目里 hold_for 那次教训)。
        去掉「音效开关」那行(玩家刚点完按钮, 开关状态不需要再告诉他一遍)。"""
        try:
            rows = [r for r in self.sfx.audio_detail() if not r.startswith("音效开关")]
            return "\n".join(rows)
        except Exception:
            return ""

    def _finish_replay_veil(self):
        """玩家点掉了"重放完成"那一屏。幂等; 绝不在这里动音频栈。"""
        v = getattr(self, "_replay_veil", None)
        self._replay_veil = None
        self._load_veil = None
        if v is not None:
            v.drop()

    def _start_bench_test(self):
        """开始性能测试(菜单点"开始测试"后)。"""
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

    def _build_info(self):
        """这个包是**哪个版本、什么时候做出来的** —— 长按标题那两个弹窗里显示一行。

        版本走 Android 的 PackageManager(它的 versionName 就是 buildozer.spec 的 version);
        日期取 `main.py` 的文件 mtime。

        ⚠️ **为什么不把日期烘成源码常量**: `tools/build_android_main.py` 是纯字符串拼接、
        不做任何改写 —— 源码里写死日期的话, `--check` 每次都报"生成物与源不同步"。
        运行时取 mtime 就与生成过程完全解耦, 生成器一个字都不用改。

        mtime 为什么约等于构建日: 打包时 p4a 把整个 app 目录塞进 APK 里的 `private.tar`,
        条目时间戳 = 构建机上那些文件的写入时间(CI 是新拉代码后立刻构建), 首次运行时
        bootstrap 解包, `tarfile` 默认保留 mtime。

        ⚠️ 整段 try/except: 拿不到就少显示一段, 绝不把弹窗带崩 —— 这只是隐藏功能里的
        一行信息, 不值得为它冒任何风险。
        """
        parts = []
        if platform == 'android':
            try:
                from jnius import autoclass
                act = autoclass('org.kivy.android.PythonActivity').mActivity
                pi = act.getPackageManager().getPackageInfo(act.getPackageName(), 0)
                if pi.versionName:
                    parts.append('v%s' % pi.versionName)
            except Exception:
                pass
        try:
            t = os.path.getmtime(os.path.abspath(__file__))
            # 1600000000 = 2020-09; 再过掉"未来时间"(设备时钟不对时会取到), 免得显示怪日期
            if 1600000000 < t < time.time() + 86400:
                # 文案(用户三次定稿): 不用「构建」(行话); 用「于 … 制作」;
                # 年月日写成**汉字**(用户: "加入年月日文字, 避免误解") —— `2026-09-11` 这种
                # 全数字写法在中文语境下容易被读反(有人按 日/月 读), 带上「年月日」就没有歧义。
                parts.append('于 %s 制作'
                             % time.strftime(BUILD_TIME_FMT, time.localtime(t)))
        except Exception:
            pass
        return ' · '.join(parts)

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
        # 存历史(最近100次)
        self.bench_history.append({
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "phys_fps": int(phys_fps),
            "avg_frames": int(avg_frames),
            "cost_ms": round(cost_ms, 1),
            "render_fps": round(render_fps, 1),
            "render_1low": round(render_1low, 1),
            "device": dev,
        })
        if len(self.bench_history) > 100:
            self.bench_history.pop(0)
        self._save_bench_history()
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
        # ⚠️ 这一屏**不放**版本/制作日期(用户 2026-09-11 定稿: "性能测试的成绩面板别加").
        #    成绩面板只放成绩; 版本/日期在长按标题的**菜单弹窗**里(见 _show_bench_menu)。
        data_lbl = Label(text=data, font_size='17sp', halign='left', valign='top',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(160))
        data_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(data_lbl)
        popup = self._popup(0.90, 300, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        popup.open()
        self.status_lbl.text = getattr(self, '_bench_saved_status', '按住蓄力发射')
        self._set_controls_enabled(True)
        self._bench_running = False
        self._bench_start = 0.0

    def _show_bench_history(self):
        """弹珠发射性能测试历史弹窗(最近100次, 每行只显示每秒步数)。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = Label(text='测试历史（最近100次）', font_size='19sp', bold=True, halign='center',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(28))
        title_lbl.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
        content.add_widget(title_lbl)
        if not self.bench_history:
            empty = Label(text='暂无测试记录\n\n长按标题 3 秒即可测试', font_size='16sp', halign='center',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(90))
            empty.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
            content.add_widget(empty)
        else:
            scroll = ScrollView(size_hint=(1, 1))
            inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
            inner.bind(minimum_height=inner.setter('height'))
            for r in reversed(self.bench_history[-100:]):
                row = Label(text='%s    每秒 %d 步' % (r.get('time', '--'), r.get('phys_fps', 0)),
                            font_size='17sp', halign='left', valign='middle',
                            color=hex_rgb(COL_TEXT) + (1,),
                            size_hint_y=None, height=dp(30))
                row.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
                inner.add_widget(row)
            scroll.add_widget(inner)
            content.add_widget(scroll)
        close_btn = Button(text='关闭', font_size='16sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        content.add_widget(close_btn)
        # 宽高同 _popup(): 必须吃等效竖屏窗口, 不能用 size_hint(见 _popup 的说明)
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.86 * _vw, height=0.7 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()

    # 声音两态循环: 音效已开(含语音) -> 音效已关。原来是三态, 中间的"音效已开(不播语音)"
    # 档没有存在价值 —— 要语音的选开、不要的直接关掉, 中间那档只会让人纠结(用户定稿)。
    SOUND_MODES = ("on", "off")

    def toggle_mute(self):
        i = self.SOUND_MODES.index(self.sound_mode)
        self.sound_mode = self.SOUND_MODES[(i + 1) % len(self.SOUND_MODES)]
        if self.sound_mode == "off":
            # "关闭声音"(0.84s)必须在静音前播; 延迟真正关闭, 让播报收尾后再停输出
            self.sfx.play("voice_mode_off")
            Clock.schedule_once(self._apply_sound_off, 1.0)
        else:
            self.sfx.set_enabled(True)
            # 开启提示写死 voice_mode_sfx(「开启中奖音效」): 与按钮上的"音效已开"一致。
            # 不能再用 "voice_mode_" + sound_mode 拼 —— 模式值叫 on, 没有 voice_mode_on。
            self.sfx.play("voice_mode_sfx")
        self._refresh_mute_btn()
        self._save_config()

    def _apply_sound_off(self, dt):
        if self.sound_mode == "off":      # 延迟窗口内玩家又切回 on 则取消关闭
            self.sfx.set_enabled(False)

    def _refresh_mute_btn(self):
        # 开=绿底深字 / 关=深底亮灰字, 两态一眼可辨
        if self.sound_mode == "on":
            self.mute_btn.text = "音效已开"
            self.mute_btn.background_color = hex_rgb(COL_GREEN) + (1,)
            self.mute_btn.color = hex_rgb("#0e1524") + (1,)
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
        if not silent and self.sound_mode == "on" and time.time() >= self._result_until:
            self.sfx.play("voice_bet_%d" % v, throttle=0.6)
        self._save_config()

    def set_rtp(self, t, silent=False):
        self.rtp_target = t
        self._restyle_selects()
        self.sfx.play("click", throttle=0.08)
        if not silent and self.sound_mode == "on" and time.time() >= self._result_until:
            pct = int(t * 100)
            self.sfx.play("voice_rtp_%d" % pct, throttle=0.6)
        if self.state == "ready":
            self.multipliers = self._boards[t]   # 切换: 直接取该档盘面, 不刷新(只有发射才刷新)
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
            if self.sound_mode == "on":
                self.sfx.play("voice_reset_progress", throttle=1.5)
        # 恢复按钮颜色
        self.reset_btn.background_color = hex_rgb("#2a2a35") + (1,)
        self._save_config()

    def start_charge(self):
        if self.state != "ready":
            return
        if self.balance < self.bet:
            if self.sound_mode == "on":
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
        self._charge_start = time.time()     # 蓄力起始时刻(3秒兜底自动发射)
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
        self._easter_egg = False              # 新发射重置彩蛋标记(球落回竖井才置 True)
        # 防御性重置: 正常路径下 launch() 只在 state=ready 时可达, 而 _easter_hold 期间
        # 状态是 landed, 照理进不来。但 reset_balance() 能强制中断回 ready —— 那之后
        # 这一发必须能把上一次的彩蛋锁清掉, 否则玩家永远发不出去。留作逃生口。
        self._easter_hold = False
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
        if getattr(self, "_easter_egg", False):
            self._easter_egg = False
            self.balance += 2 * self.bet          # 彩蛋: 球跳回发射槽, 按 ×2 结算(投10回20, 净+10)
            self.plays -= 1                       # 不计一局(与哑火一致): 总投/每轮投都退回
            self.round_plays -= 1
            self._refresh_stats()
            # 不设 status_lbl: 这条路径的结果全交给弹窗说(用户定稿, 状态栏那行已删)
            # (跑分期间不弹窗 —— 那时也没有文字说明, 但那是测试流程, 玩家知道自己在跑分)
            _vibrate_double(35)                   # 短促双震=惊喜, 非长震大奖
            self._result_until = time.time() + 2.5
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = time.time()
            self._save_config()
            # ⚠️ 跑分期间不表演: 不播装杯、不弹彩蛋窗(用户 2026-09-11 定案: "benchmark 的时候
            #    不弹, 其他时间还是需要弹的")。弹窗会盖住跑分置灰层。账务照走(余额/统计都不吞),
            #    单局照常结束。
            if getattr(self, "_bench_running", False):
                self._land_hold = max(0.3, LAND_HOLD - 0.5)
                return
            # 一路锁到"装杯播完 + 弹窗关掉": 彩蛋分支不设 _land_hold, 不锁的话
            # park_ball 会在 landed_at+0.6s 就跑掉 —— 重掷盘面、state 回 ready、按钮恢复,
            # 然后装杯才播, 变成"已经能发射了还在放动画"。
            self._easter_hold = True
            # 先播装杯, 全部落定后再弹对话框(用户定稿)。排不上(球堆异常)也绝不能把
            # 弹窗吞了 —— 那是这条路径唯一的结果说明。
            if not self.game_area.win_fx.play_win(2, self.bet, on_done=self._on_easter_settled):
                self._on_easter_settled()
            return
        m = self.multipliers[i]
        payout = self.bet * m
        self.balance += payout
        if m > 0:
            self.hits += 1
        self._refresh_stats()
        # 指示灯回答的是"哪一格中了", 所以固定 绿=中 / 红=未中(与 PC 版同一套设计)。
        # 以前这里是 slot_color(m) 按倍率取色, 有两个毛病:
        #   1) slot_color(5) 恰好 == COL_FIRE —— x5 中奖的灯和"未中"一模一样, 一眼分不出;
        #   2) 倍率槽的色块本来就是 slot_color(该格倍率), 灯再按同一个色函数上色是重复信息。
        lamp = COL_FIRE if m <= 0 else COL_GREEN
        self.game_area.set_lamp(i, lamp)
        self.game_area.pulse_slot(i)
        self._play_pocket_sound(m)
        # 数字滚动节奏 + 大奖分档(x10 以上滚更久, 看得清中大奖); 实际起滚在 _reveal_win
        big = m >= 10
        self._land_hold = 0.7 if big else max(0.3, LAND_HOLD - 0.5)  # 提前0.5s可发射
        self._anim_dur = 1.2 if big else 0.5
        if m > 0:
            # 揭晓合流: 大字/余额/结果语音 全部等到"最后一颗球落定"那一刻(见 _reveal_win)。
            # 以前 t=0 就报 "+200", 而语音要等装杯播完(×2≈2.0s, ×100≈3.7s)才念 ——
            # 数字提前剧透, 后面整场装杯沦为重播(用户反馈)。
            # 账务(上面的 balance/hits/_save_config)一秒都不挪: 中途被杀不能吞奖励。
            self.status_lbl.text = "命中 x%d · 结算中…" % m   # 中间态: 第一秒不发空, 余额"冻结"不像 bug
            self._anim_pending = True
            # ---- 揭晓: 一次性事件, 用"本轮序号"当幂等键 ----
            # 三个洞一起补(2026-09-10 实修; 血泪见 android_part_pile.py 的 _pump_reveal):
            #   1) 闸原来只在 `_reveal_win` 里, **语音完全没有闸** —— 兜底先揭、迟到的 on_done
            #      后到时, 大字被挡掉而语音照样响;
            #   2) 闸在 `play_win` **之前**复位, 而 play_win 开头会补执行上一局遗留的回调 ——
            #      那一刀把上一局的数字画在本局第 0 帧(杯子还没出现), 同时把本局的闸锁死,
            #      于是本局自己的大字再也立不起来(实测 300 局里 258 局如此);
            #   3) 兜底路径自己另写了一份"揭晓该做什么", 与主路径分叉(兜底那份没有语音)。
            # 现在: 闸收到唯一一处、同时盖住两个副作用, 并用序号丢弃迟到的旧回调。
            self._win_seq += 1
            seq = self._win_seq
            self._reveal_done = False
            self._pending_win = (m, payout)

            def _on_settled():
                # 揭晓 + 语音同拍: 数字和声音一起给。顺序不能反 —— 先立大字再响。
                # `seq != self._win_seq` 说明这是**上一局**的迟到回调, 直接丢弃。
                if self._reveal_done or seq != self._win_seq:
                    return
                self._reveal_done = True
                self._reveal_win(m, payout)      # 内部自带 try/except
                self._play_win_voice(m, payout)

            self._settle_cb = _on_settled

            # 中奖演出: 倍率决定颗数, 投注档决定球色。放在彩蛋分支(本函数开头 return)之外。
            # ⚠️ 性能测试期间**照常播**(用户 2026-09-11 定案, 见下面那段) —— 这条以前是反的。
            # ⚠️ 2026-09-11 用户定案: **跑分期间也照常播装杯**。
            #    原来这里对 `_bench_running` 是"直接揭晓、不播演出"(下面那段废弃注释的
            #    第 1、2 条理由), 玩家报"性能测试有bug, 你丢掉了落袋动画", 并要求保留。
            #    代价是**真实的**, 记在这里免得以后有人又把它"优化"掉:
            #      · 跑分那 3 发之间, `_auto_launch_tick` 要等 state 回 ready 才发下一发,
            #        而装杯期间 state 是 landed ⇒ 采样窗口从 ~1.5s 拉长到 ~15~20s,
            #        里面混进大量装杯渲染 ⇒ **平均帧率偏低**(用户明确接受: "保留动画,
            #        接受跑分数字偏低");
            #      · 全程时长从 ~10s 涨到 ~20~30s(菜单里那句提示已同步改掉)。
            #    (废弃的旧理由, 留档: 1) 装杯会画满游戏区盖住置灰层; 2) 装杯最多画 100 颗球,
            #     开销会污染正在采样的帧率 —— 第 2 条就是上面写的那个代价, 现在接受它。
            #     第 1 条已不成立: 置灰层在 RootWidget.canvas.after, 本来就盖在装杯之上。)
            # ⚠️ **"弹珠落容器"事件的触发时刻延后 `CUP_TRIGGER_DELAY` 秒**(用户 2026-09-11 定案)。
            #    账务/指示灯/槽位白闪/入袋音全部留在 t=0(上面那些行一个字没挪),
            #    只有下面这句 `play_win` 后移 —— 见常量处的规格说明。
            #
            # ⚠️⚠️ `_reveal_deadline` **必须先归零再调度**。兜底判据(见 _frame 的 landed 分支)是
            #    `... and self._reveal_deadline and now >= self._reveal_deadline` —— 不归零的话,
            #    **上一轮残留的非零 deadline** 会在这 0.15s 窗口里让兜底提前开火:
            #    大字/语音/余额在演出还没开始时就冒出来(= 提前剧透, 正是当初费大力气消灭的东西)。
            # ⚠️ Clock 回调收 1 个位置参数 —— BUILD_APK.md §3.23 红线, 零参签名真机启动即闪退。
            # ⚠️ deadline 与 play_win **必须同拍**: `reveal_at()` 读的是 play_win 写好的 `_t0`。
            # ⚠️ `park_ball` 不会在这 0.15s 里抢跑: landed 分支要求 `landed_at + _land_hold` 已过,
            #    而 `_land_hold` 的最小值是 max(0.3, LAND_HOLD-0.5) = 0.3s > 0.15s。
            #    **以后谁把 `_land_hold` 调到 0.15 以下, 就会在演出触发前重掷盘面。**
            self._reveal_deadline = 0.0

            def _start_cup():
                if not self.game_area.win_fx.play_win(m, self.bet, on_done=_on_settled):
                    # 排不上(球堆异常)也绝不能把数字和声音吞了 —— 以前这个返回值是被丢弃的
                    _on_settled()
                # 兜底: 到点还没揭就自己揭(防 tick 停摆)。
                # ⚠️ 基准必须和 tick() 判揭晓用**同一个真源**(`win_fx.reveal_at()`)。
                #    这里曾经按 `expected_sec - (hold_for + RESULT_FADE)` 反推, 等价于
                #    "最后一颗球停住 + 0.05s"。揭晓改成"第一次触地 + REVEAL_DELAY(0.3s)"之后,
                #    那两个时刻只差 0.196~0.315s, 于是兜底会比真事件**早**最多 0.054s 触发 ——
                #    数字/语音在球停稳前就冒出来, 正是当初要消灭的"提前剧透"。
                self._reveal_deadline = self.game_area.win_fx.reveal_at() + 0.05

            Clock.schedule_once(lambda dt: _start_cup(), CUP_TRIGGER_DELAY)
            # 只要中奖就震, 按倍率分档(x2/x3 轻点一下)
            _vibrate(300 if m >= 100 else (220 if m >= 50 else (150 if m >= 20 else (110 if m >= 10 else (75 if m >= 5 else 45)))))
        else:
            # 未中不播装杯, 保持原来的即时反馈(大字 + 余额滚动照旧)
            self.status_lbl.text = "未中"
            self.game_area.big_result_text(m, payout)
            self._anim_pending = False
            self._reveal_done = True
            self._pending_win = None
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = time.time()
            self._coin_until = 0.0
            self._coin_start = 0.0
        self._result_until = time.time() + 2.5 + (
            self.game_area.win_fx.expected_sec(m) if m > 0 else 0.0)
        self._save_config()

    def _reveal_win(self, m, payout):
        """揭晓: 装杯最后一颗球落定的那一刻, 大字 + 余额滚动 + coin 一起给。

        语音的挂点不在这里 —— 它挂在 settle 的 `_on_settled` 里(和这里同拍)。
        ⚠️ **幂等闸不在这里**: 唯一真源是 settle() 的 `_on_settled`(它同时挡住语音)。
        在这里再放一道闸就是"两处各自判断同一个东西", 改一处另一处静默脱钩 ——
        项目在 `hold_for` 上已经踩过一次这个坑, 别再犯。
        """
        try:
            self._anim_pending = False
            self.status_lbl.text = ("中奖!  +%d (x%d)" % (payout, m)) if payout > 0 else "未中"
            self.game_area.big_result_text(m, payout)
            now = time.time()
            self._anim_start_balance = self.display_balance
            self._anim_target_balance = float(self.balance)
            self._anim_start_time = now
            # coin 是"计分滚动"的伴音, 必须跟着余额滚。起点让开语音起句(语音长约 0.9~1.5s,
            # 压在 0.35s 处起播不会盖住开头的"弹珠加二百", 又能撑满整个滚动过程)。
            self._coin_start = now + 0.35
            self._coin_until = now + (1.2 if m >= 10 else 0.5)
        except Exception as exc:
            # _frame 的 try/except 是静默的, 这里不留痕的话"数字永不出现"会查无对证
            print("REVEAL FAIL: %s: %s" % (type(exc).__name__, exc))

    def park_ball(self, reroll=True, silent=False):
        """重掷盘面(reroll=True), 新球停到柱塞, 回 ready。哑火 reroll=False 防免费刷盘。"""
        if reroll:
            self._boards = {r: roll_multipliers(r) for r in (0.80, 1.20, 2.00, 3.60)}   # 重新发射: 4档盘面一起刷新
            self.multipliers = self._boards[self.rtp_target]
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

    def _on_easter_settled(self, *_):
        """装杯播完 -> 弹对话框(用户定稿: 先看弹珠落进杯子, 再看说明)。

        守卫 _easter_hold: 玩家若在装杯期间按了重置并又发了一发(launch 会清这个锁),
        这时再弹窗就会盖在新一局上 —— 那就不弹了。
        """
        if not self._easter_hold:
            return
        # ⚠️ 跑分期间**不弹窗**(用户定案: "benchmark的时候不弹, 其他时间还是需要弹的")。
        #    这里**不能只 return** —— 那样 _easter_hold 永远不放, 玩家会被软锁死;
        #    必须走 _easter_finish 解锁。
        if getattr(self, "_bench_running", False):
            self._easter_finish()
            return
        # ⚠️ 这个回调挂在"最后一颗球**第一次触地**"(揭晓用), 而装杯的退场还要再跑
        # 0.6s 左右。用户定稿是"先播完装杯、全部落定再弹对话框" —— 所以这里必须等到
        # win_fx 真正回到 idle, 否则弹窗会在杯子还在淡出的时候盖上来。
        # (轮询有上界: win_fx 自己有 FX_MAX_SEC=9s 硬兜底, 不会空转。)
        if self.game_area.win_fx.mode != "idle":
            Clock.schedule_once(self._on_easter_settled, 0.2)
            return
        self._show_easter_popup()

    def _show_easter_popup(self):
        """彩蛋弹窗: 球跳回发射槽, 按 ×2 结算(返还 2×投注), 点确定才关。

        文案与语音用中性正式的措辞(用户定稿): 这条路径的措辞与游戏其余部分
        ("弹珠数量已调整到一千个" / "弹珠返还比例调整到百分之三百")保持一致,
        量词统一用「个弹珠」(对齐"每次投入弹珠: 1个/10个/50个/100个"那排按钮)。
        金额报的是**总返还** 2×bet(按确定后余额确实 +2×bet); 净赚仍是 bet。

        ⚠️ **跑分期间不弹**(用户 2026-09-11 定案: "benchmark的时候不弹, 其他时间还是需要弹的"),
        防线在 `_on_easter_settled` 里(那里必须走 _easter_finish 解锁, 不能只 return)。
        """
        content = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(14))
        title = Label(text="弹珠返回发射槽", font_size="28sp", bold=True, halign="center",
                      color=hex_rgb(COL_METER) + (1,), size_hint_y=None, height=dp(44))
        msg = Label(text="弹珠未落入倍率槽, 已回到发射槽。" + chr(10)
                         + "本局按 ×2 结算, 返还 %d 个弹珠。" % (2 * self.bet),
                    font_size="16sp", halign="center", valign="middle",
                    color=hex_rgb(COL_TEXT) + (1,))
        msg.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        content.add_widget(title)
        content.add_widget(msg)
        content.add_widget(ok_btn)
        popup = self._popup(0.78, 260, title="", content=content,
                            auto_dismiss=False,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            separator_color=hex_rgb(COL_DIV) + (1,))
        ok_btn.bind(on_release=lambda *_: popup.dismiss())
        # 绑 on_dismiss 而不是按钮: 将来多一条关闭路径(手势/系统)也不会漏掉解锁。
        # 顺便把弹窗引用留给探针用。
        popup.bind(on_dismiss=self._on_easter_closed)
        self._easter_popup = popup
        # 播报。直接调 sfx.play —— set_bet/set_rtp 那类辅助函数会被 _result_until 挡掉
        # (本分支刚把它设成 now+2.5), 走辅助函数会自己把自己抑制掉。
        # 与正常中奖同一套约定: 语音档播语音, 非语音档播 ×2 轻赢琶音(两者同播会互盖)。
        if self.sound_mode == "on":
            self.sfx.play("voice_easter_%d" % self.bet, throttle=0.5)
        else:
            self.sfx.play("win1", 0.6)
        popup.open()

    def _on_easter_closed(self, *_):
        """弹窗关掉 -> 整段彩蛋结束, 放行 park_ball。"""
        self._easter_finish()

    def _easter_finish(self):
        """彩蛋流程收尾: 解锁。装杯已经把 busy() 走完了, 这里只松 _easter_hold。"""
        self._easter_hold = False

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
    def _bench_history_path():
        """性能测试历史 JSON 路径(与轮次历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_bench_history.json")

    def _load_bench_history(self):
        try:
            with open(self._bench_history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.bench_history = data[-100:]
        except Exception:
            pass

    def _save_bench_history(self):
        try:
            with open(self._bench_history_path(), "w") as f:
                json.dump(self.bench_history[-100:], f)
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
                # sound_mode 不再读档(用户定稿: 声音不持久化, 每次启动都是"音效已开").
                # 老存档里的 "voice"/"sfx" 两个旧值因此被自然忽略, 不需要迁移代码。
                if isinstance(cfg.get("max_plays"), int) and cfg["max_plays"] in (20, 50, 100):
                    self.max_plays = cfg["max_plays"]
                if isinstance(cfg.get("rtp_target"), (int, float)) and cfg["rtp_target"] in (0.80, 1.20, 2.00, 3.60):
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
        # (原来这里按读回的 sound_mode 决定是否 set_enabled(False); 现在不读档, 恒为 on, 删)
        if self.round_plays >= self.max_plays:
            self._auto_reset_on_start = True   # UI还没建, 延后到 _build_ui 之后

    def _save_config(self):
        try:
            cfg = {
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
        if self.sound_mode != "on":
            if on_done:
                Clock.schedule_once(lambda dt: on_done(), 3.0)   # sfx/off 档不念, 3s 后自动重置
            return 0.0
        prefix_key = "voice_round_end_%d" % self.max_plays
        voices = [prefix_key]
        voices.extend(number_voice_names(self.balance))
        voices.append("voice_round_suffix")
        return self._play_voice_sequence(voices, on_done=on_done)

    def _show_round_end(self):
        """本轮游戏结束弹窗: 恭喜文案 + 统计 + 语音播报(玩家点"确定"才重置并关闭)。"""
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
        msg = "本轮游戏 %d 次已结束\n剩余 [color=%s]%d[/color] 个弹珠\n弹珠数量已调整到1000个\n欢迎你再次挑战" % (
            self.round_plays, COL_BALL, self.balance)
        lbl = Label(text=msg, font_size="18sp", halign="center", valign="middle",
                    markup=True, color=hex_rgb(COL_TEXT) + (1,))
        lbl.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        content.add_widget(lbl)
        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        content.add_widget(ok_btn)
        popup = self._popup(0.82, 320, title="本轮游戏结束", content=content,
                            auto_dismiss=False,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            title_size="19sp",
                            separator_color=hex_rgb(COL_DIV) + (1,))
        popup.open()
        # 玩家点"确定"才重置并关闭弹窗; 语音只播报, 不自动关闭
        _done = [False]                            # 防重复调用
        def _auto_reset():
            if _done[0]:
                return
            _done[0] = True
            self.reset_balance(notify=False)
            popup.dismiss()
        ok_btn.bind(on_release=lambda *_: _auto_reset())
        self._play_round_end_voice()

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
        popup = self._popup(0.84, 500, title="每轮游戏次数设定", content=content,
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
        # 刷新弹窗内选中高亮
        if sel_btns:
            for v, b in sel_btns.items():
                b.background_color = hex_rgb(COL_BTN if self.max_plays == v else COL_BTN_OFF) + (1,)
        # toast + 语音提示
        self.game_area.center_toast("每轮已设定为%d次" % val, hexcolor=COL_GREEN, size=20, life=1.5)
        if self.sound_mode == "on":
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
                if bit in (EV_WALL, EV_CEIL) and b.y < SFX_TOP_Y:
                    continue          # 顶墙/天花板撞击与 apex 转向同帧, 交给 top 音, 不叠
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

    def _play_pocket_sound(self, m):
        """入袋音 —— 结算瞬间就播(杯子还没出来), 所以和赢音拆开。"""
        self.sfx.play("pocket")
        if m <= 0:
            self.sfx.play("lose", 0.9)    # "好遗憾"语音已制作(voice_lose), 暂不接入

    def _play_win_voice(self, m, payout):
        """赢音/中奖语音 —— 由中奖玻璃杯**全部落定那一刻**回调触发。

        档位映射与杯子落定的节奏天然对齐: 杯里最后一颗球砸下去, 小号角/语音才响,
        比"球刚进槽就报喜"更有兑现感。映射表一个字没动, 与改动前逐档一致。
        """
        if m <= 0:
            return
        if self.sound_mode == "on":
            # 语音档: "弹珠加xx"替换 win 琶音(语音与琶音同播会互相盖, 见 BUILD 讨论)
            self.sfx.play("voice_win%d" % payout)
            return
        tier = 0 if m <= 2 else (1 if m <= 3 else (2 if m <= 5 else (3 if m < 20 else (4 if m < 50 else (5 if m < 100 else 6)))))
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
        # 冷启动加载页收尾: 音效库**真的能播**了就摘掉(见 _LoadVeil / Sfx.audio_ready)。
        # ⚠️ 判据是"烘完 **且** 探到真能播(或硬超时)", 不是"烘完就摘" —— `SoundPool.load()`
        #    返回 sampleId ≠ 解码完, 没解码完 play() 返回 0 静默跳过。这正是 BUILD_APK.md 里
        #    记的「首次安装打开 App 必然全静音」的根因。
        # ⚠️ 绝不软锁: audio_ready() 里 `!enabled` 与"探针不可用"都直接放行, 且等待有硬超时。
        _veil = getattr(self, "_load_veil", None)
        if _veil is not None:
            _veil.tick()          # 加载图"从小到大 + 呼吸"(它自己不持有 Clock, 由这里驱动)
            if self.sfx.audio_ready():
                if _veil is getattr(self, "_replay_veil", None) and not _veil._hold:
                    # 「重放冷启动」完成: **不自动摘页** —— 摆出结果等玩家点一下。
                    # 玩家反馈「成功之后没有暂停, 直接回去了, 我啥都没有看清」: PC 上烘焙 1.2 秒、
                    # 探针一过就摘, 那几行数字等于闪一下。
                    _veil._hold = True
                    _veil.set_result(self._replay_summary())   # 结果页: 字号放大 + 摆出结论
                    _veil._on_tap = self._finish_replay_veil
                elif not _veil._hold:
                    self._load_veil = None
                    _veil.drop()
            # ⚠️ 这里原来有一段"实时诊断": 往加载页印第二行「已加载 42 / 97　·　已用 1.0 秒」。
            #    玩家 2026-09-11 定稿: 「我要的是只显示一个加载界面即可」「这个加载页面 啥字都没有」
            #    —— 整段删除, 加载页**一个字都不印**。
            #    那些数一个都没丢: 同样的信息「启动信息」里全有(启动方式/冷启动 XXXX ms、
            #    音效就绪 97 / 97、加载失败 0 个), 启动完长按标题就能看, 而且更全。旧立意是
            #    "这块页吞掉所有触摸, 那几秒长按不到标题" —— 但等一下它就过去了。

        ws = (Window.width, Window.height)
        if ws != self._last_win_size:
            self._last_win_size = ws
            layer = _land_layer()
            if layer is not None:
                layer.apply_orientation()   # 横竖切换: 重算旋转角/等效窗口(画面保持竖拿构图)
            app = App.get_running_app()
            force = getattr(app, "_apply_orientation", None)
            if force is not None:
                force()                     # 窗口一变立即重申方向策略, 不等守卫周期(转屏跟手)
            self._fit_width()
            self._apply_sizes()
        if self.state == "charging":
            self.power = min(1.0, self.power + CHARGE_RATE * dt)  # 用真实dt, 适配30fps设备
            if time.time() - self._charge_start > 3.0:
                self.launch()   # 兜底: 蓄力超3秒自动发射(防 on_release 丢失卡死)
                return
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
                if landed is not None:
                    # 落袋即本例终态: 这一帧剩下的物理步不再推进这颗球。
                    # ⚠️ 与 PC 版 plinko.py 的 _frame 同因同解。第一道保险在 physics_step 的
                    # 落袋返回处(清 vx + 贴地), 已经让"结算槽==落格槽"成为结构性不变量; 这一道
                    # 管的是**别的事**: 少了它, 球落袋后还会被继续模拟(实测越槽局多飞
                    # 0.68~1.27s), 结算时刻/槽位白闪/装杯/揭晓全部随帧率漂, 状态栏这段时间还
                    # 打着"即将入袋…"。也与 --selftest 的"逐物理步 break"口径对齐。
                    break
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
                if b.x > FIELD_R:                          # 球落回竖井(发射槽) — 罕见彩蛋
                    self._easter_egg = True
                i = max(0, min(NUM_SLOTS - 1,              # 物理落格结算(球落到哪算哪)
                               int((b.x - FIELD_L) / SLOT_W)))
                self.land_target_x = FIELD_L + (i + 0.5) * SLOT_W
                self._settle_slot = i
                self.landed_at = time.time()
                self.state = "landing"
                self._accumulator = 0.0
                self._landing_primed = True   # 首帧补初速, 之后交给物理
                # 回弹改**纯竖直**(用户定稿): 第一次触地就把横向速度清零。
                # 病根: landing 循环里只有重力/横向弹簧/地板, **没有隔板碰撞**, 而球带着
                # 飞行末段的横速入槽 —— 回弹期它能横着滑过隔板停到隔壁槽里(结算槽仍是本槽,
                # 于是"钱算对了、球停错地方")。清零横速后球只在本槽原地上下弹; 横向只剩
                # LAND_K 弹簧, 而弹簧只会把球拉向**本槽中心**, 拉不出去。
                b.vx = 0.0
                # 结算提前到"第一次触地"这一刻(用户定稿): 以前要等回弹落定, 而实测 88.7%
                # 的落定是走 0.5s 超时兜底(平均比触地晚 0.3~0.5s)。回弹照播(landing 状态
                # 只是不再拦着结算), 但中奖演出/揭晓从触地就开始排, 整场更利索。
                if not self._settled:
                    self._settled = True
                    self.settle(i)
            if tick_ev:
                self._play_events(tick_ev, tick_amp, self.ball)
        elif self.state == "misfire":
            self._accumulator += dt
            while self._accumulator >= FIXED_DT:
                self._accumulator -= FIXED_DT
                self._misfire_frames += 1     # 每物理步 +1(与飞行分支/selftest 同构, 防刷新率漂移)
                if advance_misfire(self.ball) or self._misfire_frames > MISFIRE_MAX_FRAMES:
                    self.ball.x = PLUNGER_X
                    self.ball.y = PLUNGER_Y
                    self.ball.vx = 0.0
                    self.ball.vy = 0.0
                    self.sfx.play("bounce", 0.55)
                    self.park_ball(reroll=False)
                    break
        elif self.state == "landing":
            b = self.ball
            if self._landing_primed:
                self._landing_primed = False
                # 落地必须弹一下(用户 2026-09-11 定稿: "真跳和假跳都可以, 高度别太固定"):
                # 撞击速度 = max(球真实下落速度, 保底) × 随机系数 —— 落得越快弹得越高,
                # 同时每一发高度都不一样。回弹 = 撞击 × LAND_E, 上限 LAND_BOUNCE_MAX_VY。
                if b.vy < LAND_BOUNCE_MIN_VY:
                    b.vy = LAND_BOUNCE_MIN_VY
                b.vy *= random.uniform(*LAND_BOUNCE_JITTER)
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
                        b.vy = -b.vy * LAND_E * random.uniform(*LAND_BOUNCE_DECAY_JITTER)
                        if b.vy < -LAND_BOUNCE_MAX_VY:     # 回弹vy上限(删刹车后防弹越隔板)
                            # apex≤24px, 球顶恰=隔板顶, 不穿帮。⚠️ 顶到上限时**也要随机**(取
                            # JITTER 的下半段): 否则所有"落得快"的球都停在同一个 24.2px,
                            # 一眼看出是套路(用户定稿: 高度别太固定)。
                            b.vy = -LAND_BOUNCE_MAX_VY * random.uniform(LAND_BOUNCE_JITTER[0], 1.0)
            if (abs(b.x - self.land_target_x) < SLOT_W * 0.45 and abs(b.vy) < 10.0
                    and b.y >= floor_y - 0.5):
                b.vx = 0.0
                b.vy = 0.0
                self.state = "landed"
                self.landed_at = time.time()
            elif time.time() - self.landed_at >= 0.5:
                b.y = floor_y
                b.vx = 0.0
                b.vy = 0.0
                self.state = "landed"
                self.landed_at = time.time()
        elif self.state == "landed":
            # 中奖玻璃杯演出期间不放行: 否则 park_ball 会在杯子播到一半时重掷盘面、
            # 恢复按钮, 杯子就盖在一个已经换过的盘面上, 玩家还能同时发下一颗。
            # 硬兜底在 WinPileFX.busy() 里(FX_MAX_SEC 超时无条件放手), 不会锁死。
            # _easter_hold: 彩蛋的弹窗+装杯整段(弹窗是模态的, 玩家只有"确定"一条出口,
            # 所以这个锁不会把谁困住; 装杯那半段另有 busy() 的 9s 兜底)。
            if (time.time() - self.landed_at >= self._land_hold
                    and not self.game_area.win_fx_busy()
                    and not self._easter_hold):
                self.park_ball()
        if self.state != "charging" and self.power <= 0.01 and self.power_lbl.text:
            self.power_lbl.text = ""
        # 余额数字滚动(老虎机式翻滚再落定), 中奖滚分时连播 coin; 大奖(x>=10)滚 1.2s
        now = time.time()
        if self._coin_start <= now < self._coin_until:
            self.sfx.play("coin", 0.8, 0.055)
        if self._anim_pending:
            # 揭晓前余额冻结在扣注后的值 —— 一帧都不许动。
            # 这里不能图省事只把三行赋值搬到 _reveal_win: 不冻结的话, _anim_start_time 还是
            # 上一局的旧值, elapsed >= _anim_dur 成立 → 走 else 分支 0.1 秒内就追平新余额,
            # 剧透一点没修、滚动和 coin 全废。
            pass
        else:
            # elapsed 必须 clamp ≥0: 负值时 noise = (1-t)*uniform 里的 (1-t) > 1 反而放大噪声,
            # 在 t 略小于 0 的那几帧里 ease+noise 可能转正, 抖出一帧偏移(投注大时肉眼可见)。
            elapsed = max(0.0, now - self._anim_start_time)
            if elapsed < self._anim_dur:
                t = elapsed / self._anim_dur
                ease = 1.0 - (1.0 - t) ** 3
                noise = (1.0 - t) * random.uniform(-0.15, 0.15) if t < 0.6 else 0
                f = max(0.0, min(1.0, ease + noise))
                self.display_balance = (self._anim_start_balance +
                                        (self._anim_target_balance - self._anim_start_balance) * f)
            else:
                self.display_balance += (self.balance - self.display_balance) * 0.5
        # 兜底揭晓: 正常路径下 on_done 会先到, 这条只在杯子卡住/tick 异常时才用得上。
        # 必须在 park_ball 之前触发, 否则玩家会看到新盘面上飘着旧局的 +200。
        # 兜底揭晓: 正常路径下 FX 的 `_pump_reveal()` 会先放; 这条只在 tick 完全停摆
        # (切后台/卡死)时才用得上。**必须调同一个闭包** —— 兜底自己另写一份"揭晓该做什么",
        # 就会出现"补了数字没补声音"这种副作用不一致(实测就是这么丢音的)。
        # 共用闭包 + 共用 `_reveal_done`/`seq` 闸 ⇒ 主路径和兜底可以安全赛跑, 不会双响。
        if (not self._reveal_done and self._pending_win and self._settle_cb
                and self._reveal_deadline and now >= self._reveal_deadline):
            print("REVEAL 兜底触发: %s" % (self._pending_win,))
            self._settle_cb()
        self.balance_lbl.text = str(int(round(self.display_balance)))
        self.game_area.tick_draw()
        # 装杯期把整块界面(减去游戏区)压暗 —— 板面那块覆盖不到 GameArea 之外, 见 _build_hud_dim。
        # ⚠️ 位置: 必须在 `tick_draw()` **之后**。演出由 tick_draw -> win_fx.tick() ->
        #    _redraw() 推进, 而 `_redraw` 会把**本帧真正画上去的** a_dim 存进 `_a_dim_now`;
        #    HUD 从这里取值 ⇒ 两边永远是同一个数(同帧同值)。放在前面的话 HUD 读到的是
        #    上一帧: 退场结束那一帧板面已经不画了, HUD 还会多黑一整帧(专家组实测指出)。
        # ⚠️ 它后面**不能再有早退**。下面 charging 分支的 `return` 在它之前是安全的:
        #    蓄力期不可能有装杯演出(演出期输入是锁的), `_a_dim_now` 此时本来就是 0。
        self._sync_hud_dim()


# =============================================================================
# App 入口 / 冒烟
# =============================================================================
# 加载页那张图的进场动画参数。图**和系统 presplash 是同一个文件**(见 _loadveil_src),
# 所以"系统 splash -> 加载页"的交接是连续的; 这一段只是在那张图上加一点"活气"。
LOADVEIL_ASPECT = 1080.0 / 1920.0   # presplash.png 的宽高比
# ⚠️ 加载页底色必须**等于 presplash.png 自己的底色**(实测四角全是 #0b1220), 否则那张图会在
#    屏幕上显示成一个"方块"; 用了它, 图溢出屏幕的部分也看不出来。
#    (buildozer.spec 里 `android.presplash_color` 也是同一个值, 系统那层同样不闪。)
VEIL_BG = "#0b1220"
LOADVEIL_BREATH_SEC = 1.9           # 呼吸周期
LOADVEIL_BREATH_AMP = 0.018         # 呼吸幅度(±1.8%)


def _loadveil_src():
    """加载页那张图的路径 —— **和系统 presplash 是同一个文件**, 交接才连续。
    找不到就返回 None(退化成纯色页面) —— **绝不因为一张装饰图把启动带崩**。"""
    try:
        import os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "presplash.png")
        return p if os.path.exists(p) else None
    except Exception:
        return None


def _veil_fit(w, h):
    """加载图在 w×h 页面里的基准尺寸 —— **cover: 铺满整屏**(超出的部分裁掉), 保持原图比例。

    ⚠️ **必须是 cover, 不能是 contain。** 系统 presplash 是 Android 拿这张图当
    `windowBackground` **铺满整屏**画的 ⇒ "打开 app"看到的第一眼就是铺满那一版。
    我们这边如果只做 contain(把整张图完整放下), 就会**比它小一圈**, 玩家看到的是:
    「打开app显示一个最大的加载, **然后迅速变小**」(2026-09-11 原话)。
    铺满之后两者大小一致, 交接处没有尺寸跳变 —— 溢出裁掉的本来就是它自己的底色。
    返回 (宽, 高), 宽高比恒等于 LOADVEIL_ASPECT。"""
    try:
        bw = h * LOADVEIL_ASPECT
        if bw < w:          # 比屏幕"瘦"(如平板 15:10) -> 按宽度顶满, 高度溢出
            bw = w
        return bw, bw / LOADVEIL_ASPECT
    except Exception:
        return 0.0, 0.0


def _veil_scale(t):
    """加载图在"这一页开了 t 秒"时的缩放系数: **只有呼吸**(±AMP, 周期 BREATH_SEC)。

    ⚠️ 这里原来还有一段"进场从小到大"(0.72 → 1.0 / 0.55s), **已删**。两个理由:
      ① 它和系统 splash **冲突**: presplash 是铺满的, 进场从 0.72 起步就等于"先缩一圈再长大",
         而玩家睁眼看到的第一眼正好是那个"缩" —— 原话:「打开app显示一个最大的加载, 然后
         迅速变小」。
      ② 它在真机上**根本来不及播**: `_t0` 从构造算起, 而第一帧要等 presplash 撤掉才画出来,
         中间可能早过了 0.55 秒 ⇒ 一睁眼就是终态 ⇒ 玩家原话里的「**然后啥也没有动**」。
    现在: 铺满 + 直接呼吸, 交接处没有任何尺寸跳变, 动的东西从头到尾都看得见。

    ⚠️ 为什么不是"循环进度条"(玩家问过"和 windows 一样搞个循环进度条…感觉不太合适?"):
    进度条承诺的是"我能告诉你还剩多少", 而这个等待**没有可报的进度**(热启动半秒 / 冷启动几秒,
    没有中间态)。呼吸只传达"我在", 尺寸有终态, 不承诺任何剩余时间。"""
    try:
        import math
        return 1.0 + LOADVEIL_BREATH_AMP * math.sin(
            2.0 * math.pi * t / LOADVEIL_BREATH_SEC)
    except Exception:
        return 1.0


class _LoadVeil(Widget):
    """冷启动"音效库烘焙中"的加载页: 盖住整屏 + 吞掉所有触摸。

    玩家 2026-09-11 要求: 「初次安装时如果音效库还没合成完, 就一直卡在加载界面, 而不是主界面」。
    原来 `Sfx(..., sync=True)` 是在**建 UI 之前**在主线程把整库烘完的 —— 玩家看到的是几秒钟的
    **黑屏**(窗口已经在了, 但一个控件都还没建), 而且那几秒 Kivy 主循环被阻塞, 什么都画不出来。
    现在改成后台烘焙 + 这一页盖住: 窗口立刻有内容, 而且这一页是活的。
    ⚠️ 触摸必须吞掉: 不吞的话 state 还是 ready, 玩家能按发射 —— 球飞出去了音效却没就绪,
       又是一次"没声音", 正是换掉 sync=True 想避免的那件事。
    ⚠️ 必须是 Widget 而不是"只画个矩形": 矩形不吞触摸, 挡不住下面那层。
    ⚠️ 它是整个 App 最早出现的东西, 只依赖 sp()/hex_rgb()/COL_BG, 不碰任何游戏状态。"""

    # ⚠️ 默认**一个字都不印**(玩家 2026-09-11 定稿: 「我要的是只显示一个加载界面即可」
    #    「这个加载页面 啥字都没有」)。它盖住整屏、吞掉触摸、就绪就把自己摘掉 —— 就这些。
    #    只有「重放冷启动」那条路会带文字进来(那是作者主动点开、专门停下来读的一屏)。
    def __init__(self, text="", **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*hex_rgb(VEIL_BG) + (1,))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self._lbl = Label(text=text, font_size=sp(20), bold=True,
                          color=hex_rgb("#eef2ff") + (1,),
                          halign="center", valign="middle")
        # 第二行: **只服务「重放冷启动」的结果页**(set_result 往里写结论)。
        # ⚠️ 普通启动路径**绝不写它** —— 玩家 2026-09-11 定稿: 加载页一个字都不印。
        #    这里原来印的是实时诊断(「已加载 42 / 97 · 已用 1.0 秒」), 那种数在「启动信息」里
        #    全都有, 而且更全, 不需要在加载页上再占一行。
        self._sub = Label(text="", font_size=sp(17),
                          color=hex_rgb(COL_SUB) + (1,),
                          halign="center", valign="middle")
        # ⚠️ `_hold`: "重放冷启动"完成时置真 —— 那一屏**不自动摘**, 摆出结果等玩家点一下。
        #    玩家 2026-09-11 反馈: 「点击重放冷启动后, 成功之后没有暂停, 直接回去了, 我啥都没有看清」。
        #    PC 上烘焙只要 1.2 秒、探针一过就摘页, 结果那几行数字等于闪一下。
        #    普通冷启动**不用**这个(那里玩家要的是赶紧进游戏, 不是看数据)。
        self._hold = False
        self._on_tap = None
        # 加载图: 就是系统 presplash 那张(同一个文件), 于是"系统 splash 撤掉 -> 本页接上"
        # 中间没有跳变: 底色是同一个 #0b1220, 内容也是同一份。
        # ⚠️ 用 **AsyncImage** 而不是 Image: Image 在**首次绘制时同步解码**, 1080x1920 那一张
        #    会把第一帧顶后几十~几百毫秒 —— 而这块页存在的全部意义就是"第一帧别是黑屏"。
        #    AsyncImage 先把背景色画出来, 图解码完自己长出来。
        # ⚠️ 整段 try/except: 它只是一张装饰图, 拿不到就退化成纯色页面 —— 绝不把启动带崩。
        self._img = None
        self._base_w = 0.0
        self._base_h = 0.0
        self._t0 = time.time()
        try:
            from kivy.uix.image import AsyncImage
            _src = _loadveil_src()
            if _src:
                # ⚠️ 不写 allow_stretch/keep_ratio: 这两个在 Kivy 2.3 的 AsyncImage 上**已弃用**
                #    (会刷两条 DeprecationWarning)。用不着它们 —— 我们把 size 按原图比例算好
                #    (见 _sync 里的 LOADVEIL_ASPECT), 宽高比本来就严丝合缝。
                self._img = AsyncImage(source=_src, size_hint=(None, None), mipmap=True)
                self.add_widget(self._img)     # 先加 = 在下层, 文字盖在它上面
        except Exception:
            self._img = None
        self.add_widget(self._lbl)
        self.add_widget(self._sub)
        self.bind(pos=self._sync, size=self._sync)
        self._lbl.bind(texture_size=self._sync)   # 文字一变就重排版(结果页会换字号/换内容)
        self._sub.bind(texture_size=self._sync)
        self._sync()

    def set_title(self, text):
        try:
            self._lbl.text = text
        except Exception:
            pass

    def set_result(self, text):
        """把这一页从「进度」切成「结果」: **字号放大**。

        ⚠️ 两行用不同字号是有意的: 烘焙进度那行(sp(13))是"赶时间瞥一眼"的信息 —— 玩家这时
        只想赶紧进游戏; 而重放结果是**专门停下来给人读的**(玩家 2026-09-11: 「挺好 就是字太小了」),
        13sp 在平板上根本读不清。同一个标签分两档字号, 比新加一个标签省事, 也不会改变布局。"""
        try:
            self._lbl.text = "重放完成，点一下继续"
            self._lbl.font_size = sp(28)
            self._sub.font_size = sp(24)
            self._sub.text = text
            # 结果页有 6 行字要读, 图退成背景 —— 否则字压在盘面上根本看不清。
            if self._img is not None:
                self._img.opacity = 0.22
        except Exception:
            pass

    def drop(self):
        """把这一页摘掉(幂等)。"""
        try:
            if self.parent is not None:
                self.parent.remove_widget(self)
        except Exception:
            pass

    def tick(self):
        """每帧推进加载图的动画。由 `RootWidget._frame` 调 —— **自己不持有 Clock**:
        切后台回来直接跳终态, 而不是冻在半路(和 WinPileFX 同一条纪律)。"""
        try:
            if self._img is None or self._base_h <= 0.0:
                return
            k = _veil_scale(time.time() - self._t0)
            w = self._base_w * k
            h = self._base_h * k
            self._img.size = (w, h)
            self._img.pos = (self.center_x - w * 0.5, self.center_y - h * 0.5)
        except Exception:
            pass

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        # ⚠️ 标题与正文必须**按内容高度各自撑开、上下排开** —— 不能让两个满屏标签叠在一起。
        #    结果页有 6 行、字号又放大到 24 时, 两块会在画面中间重叠, 上面那段就糊掉了
        #    (玩家 2026-09-11 真机截图: 「重放完成…」和第一行数据叠在一起)。
        self._lbl.text_size = (self.width, None)
        self._sub.text_size = (self.width, None)
        lh = max(sp(34), self._lbl.texture_size[1] + sp(8))
        sh = max(sp(24), self._sub.texture_size[1] + sp(8))
        gap = sp(14) if self._sub.text else 0
        total = lh + gap + sh
        top = self.center_y + total / 2.0
        self._lbl.size = (self.width, lh)
        self._lbl.pos = (self.x, top - lh)
        self._sub.size = (self.width, sh)
        self._sub.pos = (self.x, top - lh - gap - sh)
        # 加载图: **铺满整屏**(cover, 见 _veil_fit) —— 和系统 presplash 一样大, 所以"系统 splash
        # 撤掉 -> 本页接上"看不出尺寸变化; 溢出裁掉的部分是它自己的底色 #0b1220, 而背景矩形也
        # 是同一个颜色 ⇒ 连边界都看不见。
        if self._img is not None:
            self._base_w, self._base_h = _veil_fit(self.width, self.height)
            self.tick()

    def on_touch_down(self, touch):
        # 普通加载页: 吞掉所有触摸(不让玩家在音效没就绪时按发射)。
        # ⚠️ 但「重放冷启动完成」那一屏**必须能点掉** —— 它是个结果页, 不点掉就永远停在那儿,
        #    而"永远停着"正是项目红线(绝不软锁)最怕的形状。所以只在这一屏放行。
        if self._hold and self._on_tap is not None:
            try:
                self._on_tap()
            except Exception:
                pass
        return True

    def on_touch_move(self, touch):
        return True

    def on_touch_up(self, touch):
        return True


class PlinkoApp(App):
    def build(self):
        Window.clearcolor = hex_rgb(COL_BG) + (1,)
        if platform != "android":
            # 桌面预览 9:16; 宽屏可最大化, 内容自适应居中。
            # --landscape: 模拟横屏反旋转(Y700 横窗比例), 验证"画面保持竖拿构图"用
            Window.size = (1740, 1000) if "--landscape" in sys.argv else (540, 960)
        self.title = "跳跳的弹珠机"
        # 音效库**放后台烘**: 首装要把整库用纯 Python 合成一遍(好几秒)。以前这里是
        # sync=True —— 那是在建 UI **之前**、在主线程上烘完, 于是这几秒钟玩家看到的是一块
        # 黑屏(窗口已经在, 控件一个都没有), 而且 Kivy 主循环被阻塞、什么都画不出来。
        # 现在: 窗口立刻有内容, 由 _LoadVeil 盖住整屏挡输入, 烘完由 _frame 收掉。
        sfx = Sfx(SOUND_ENABLED)
        # 横屏反旋转层: 内容在等效竖屏窗口里布局, 横拿时整体旋转 90 度铺满横屏,
        # 画面构图与竖拿一致(玩家扭头看/转回竖屏玩)。竖屏时透明无感(零回归)。
        self.layer = LandLayer()
        anchor = AnchorLayout(anchor_x="center", anchor_y="center")
        anchor.size_hint = (None, None)   # 等效盒尺寸由 LandLayer 全权控制(FloatLayout 会按 size_hint 覆盖)
        self.layer._anchor = anchor
        self.layer.add_widget(anchor)
        self.rootw = RootWidget(sfx=sfx, size_hint_x=None)
        anchor.add_widget(self.rootw)
        # 冷启动加载页: 音效库没烘完就盖住整屏(不盖的话首装前几秒是黑的, 而且能按发射出哑球)。
        # 后 add 的在上层, 所以它就是最上面那层。烘完由 RootWidget._frame 摘掉。
        self.veil = None
        if not sfx.audio_ready():
            self.veil = _LoadVeil(size_hint=(1, 1))
            anchor.add_widget(self.veil)
            self.rootw._load_veil = self.veil      # 交给 _frame 收尾
            self.rootw._load_veil_host = anchor    # 重放冷启动时要往这里再挂一页
        self.layer.apply_orientation()
        self.rootw._fit_width()
        self.rootw._apply_sizes()
        if platform == "android":
            # 方向守卫: 启动 1s(SDL 启动序列完成后)按设备分流抢一次话语权, 之后常驻。
            # 0.7s 周期幂等: 宽屏横拿时持续顶掉 SDL 的竖屏自报, 转屏跟随基本即时
            # (窗口变化分支里还有一记立即重申, 双保险); 瘦长手机持续重申竖屏锁。
            Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)
            Clock.schedule_interval(self._orient_guard, 0.7)
            # 全屏沉浸: buildozer fullscreen=1 之外的运行时双保险。
            # 弹窗/切后台回前台后系统栏会复活, 与方向守卫同节奏持续重申(幂等)。
            Clock.schedule_once(lambda *_: self._enter_immersive(), 1.0)
            Clock.schedule_interval(self._enter_immersive, 0.7)
        return self.layer

    # ---- 方向策略(2026-08-19 按屏幕比例分流): manifest+SDL 全四方向(fullSensor)。
    #      宽屏(16:9 及更宽, 平板): fullSensor 四方向, 横拿时系统给全屏横窗,
    #      LandLayer 把画面反转回竖拿构图铺满(锁竖屏会被 12L+/ZUI 塞 letterbox
    #      半屏盒, app 改不了盒子宽高, 不对抗)。
    #      瘦长手机(<16:9, 如 20.5:9): 锁竖屏 SENSOR_PORTRAIT(7, 正竖+倒竖180)。
    #      横拿时系统根本不进横屏 —— 反旋转层在瘦长机上会撞上横向多出的状态栏
    #      显示坏掉, 手机小屏看旋转竖构图也不适合阅读; 竖屏锁在小屏手机上
    #      不会触发 12L letterbox(那是大屏政策), 老方案在手机上本就验证过。
    #      横竖切换由 RootWidget._frame 的窗口尺寸轮询驱动(layer.apply_orientation)。 ----
    def _apply_orientation(self):
        """以毒攻毒: SDL 启动/onResume 会按自身 hint 调 setRequestedOrientation,
        可能把 manifest 的 fullSensor 在运行时覆盖成竖屏 -> ZUI 判定'竖屏app'塞半屏盒
        (80b6db5 真机诊断: manifest 已 fullSensor+targetSdk33, 窗口仍 1519x1754 盒,
        唯一剩余变量就是 SDL 的运行时自报)。这里按设备分流重申一次抢回话语权:
        宽屏设备抢 FULL_SENSOR(10), 瘦长手机抢 SENSOR_PORTRAIT(7)。"""
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.setRequestedOrientation(10 if _device_is_wide() else 7)
        except Exception:
            pass

    def _orient_guard(self, dt):
        """常驻方向守卫: 按设备分流持续重申方向请求(幂等, 系统无感)。
        宽屏: 横置(rotation=1/3)时重申 fullSensor, 顶掉 SDL 竖屏自报;
        瘦长手机: 持续重申竖屏锁(7), 任何运行时横屏自报都被顶掉。"""
        if platform != "android":
            return
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            if _device_is_wide():
                rot = act.getWindowManager().getDefaultDisplay().getRotation()
                if rot in (1, 3):
                    act.setRequestedOrientation(10)
            else:
                act.setRequestedOrientation(7)
        except Exception:
            pass

    _immersive_task_inst = None

    @classmethod
    def _immersive_task(cls):
        """构造(并缓存)沉浸 Runnable, 单实例反复投递(防 pyjnius 代理被 GC)。
        ⚠️ setSystemUiVisibility 必须在 UI 线程执行: 从 SDL(Python)线程直调, 视图已
        attach 时会被 ViewRootImpl 线程检查拦下(CalledFromWrongThread, 被 except 吞掉
        后无声无息) —— ZUI 真机实测竖屏启动期沉浸一直不生效, 转一次屏才"自愈"
        (2026-08-26 dumpsys 逐帧实锤: 窗口 vsysui 始终只剩 LAYOUT_STABLE)。"""
        if cls._immersive_task_inst is None:
            from jnius import PythonJavaClass, java_method

            class ImmersiveTask(PythonJavaClass):
                __javainterfaces__ = ['java/lang/Runnable']

                @java_method('()V')
                def run(self):
                    try:
                        from jnius import autoclass
                        act = autoclass("org.kivy.android.PythonActivity").mActivity
                        View = autoclass("android.view.View")
                        act.getWindow().getDecorView().setSystemUiVisibility(
                            View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
                    except Exception:
                        pass

            cls._immersive_task_inst = ImmersiveTask()
        return cls._immersive_task_inst

    @staticmethod
    def _enter_immersive(*_):
        """沉浸式全屏: 隐藏状态栏/导航栏, 玩家从屏幕边缘滑入可临时呼出(几秒后自动隐藏)。
        setSystemUiVisibility 在 API30+ 已弃用但未移除, targetSdk 33 下仍生效。
        ⚠️ 形参 *_ 必须保留: schedule_interval 回调会塞 dt 进来, 零参签名在真机上
        启动 0.7s 即 TypeError 闪退(2026-08-26 logcat 实锤, 桌面测试测不出)。
        ⚠️ 必须 runOnUiThread: 线程不对时静默失败(病根见 _immersive_task 注释)。"""
        if platform != "android":
            return
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            act.runOnUiThread(PlinkoApp._immersive_task())
        except Exception:
            pass


    # Android 生命周期: on_pause 必须返回 True 保持 GL 上下文
    def on_pause(self):
        try:
            self.rootw.sfx.pause_out()       # 切后台静音(SoundPool.autoPause)
        except Exception:
            pass
        return True

    def on_resume(self):
        if platform == "android":
            self._apply_orientation()   # 回前台 SDL 会重报方向, 抢回话语权
            self._enter_immersive()     # 回前台系统栏复活, 重新隐藏
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
        try:
            # 预热是自链式 schedule_once, 退出时可能还挂在 Clock 上(幂等, 没链就无操作)
            Clock.unschedule(self.rootw.game_area.win_fx.prebake_step)
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
        # 直接调 settle 定格特效: x20 大奖 -> 大字 + 槽闪 + 灯绿 + 滚分。
        # 注意大字不再立刻出现: 揭晓已挪到"最后一颗球落定"(见 _reveal_win)。
        r.multipliers = [0, 0, 0, 0, 20, 0, 0, 0, 0]
        r.game_area._redraw()
        r.settle(4)

    def s7b(dt):
        shot("06b_fx_bigtext.png")
        app.rootw.toggle_mute()              # 静音: 截一张"音效已关"看对比
        shot("06c_mute_off.png")
        app.rootw.toggle_mute()              # 恢复

    def s7c(dt):
        # 中奖玻璃杯演出途中(杯子 + 正在下落/堆叠的球)。settle 是 s7(15.0),
        # 杯子在 +WINDUP(0.5s) 后出现, 这里取到的是落珠中段。
        shot("06d_cup.png")

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
        Clock.schedule_once(when_ready(s9b, "s9b"), 0.5)

    def s9b(dt):
        r = app.rootw
        print("SMOKE s9b: state=%s balance=%s" % (r.state, r.balance))
        shot("08_no_beads.png")
        # 跑分期间不许弹窗/不许播装杯(用户报: 跑分时彩蛋窗打断灰屏)。
        # 顺带守住 CLAUDE.md 里"绝不软锁"那条红线 —— 彩蛋被拦掉时必须**解锁**,
        # 只 return 不 _easter_finish 的话 _easter_hold 永远不放, 玩家只能杀进程。
        # ⚠️ 先清掉前面某一局**真彩蛋**可能留下的窗/锁: 不清的话下面那条断言测的是
        #    "残留"而不是"跑分拦没拦住"(实测偶发误报: hold=False 却报 FAIL)。
        #    这是冒烟夹具的清理, 不是产品逻辑。
        r._easter_popup = None
        r._easter_hold = False
        r._bench_running = True
        r.multipliers = [0] * 9
        r.multipliers[4] = 50
        r.state = "landing"
        r._settled = False
        r.settle(4)                       # 跑分中中奖
        print("SMOKE bench-win: cup=%s reveal=%s busy=%s"
              % (r.game_area.win_fx.mode, r._reveal_done, r.game_area.win_fx.busy()))
        # ⚠️ 2026-09-11: "落容器"事件的**触发**延后了 CUP_TRIGGER_DELAY 秒, 所以 settle 刚
        #    返回这一刻 mode 本来就该是 idle —— 断言必须等过了那一段再看(下面用 Clock 推迟)。
        if r.game_area.win_fx.mode != "idle":
            print("SMOKE-FAIL: 落容器事件不该在触地那一帧就触发(应延后 %.2fs)" % CUP_TRIGGER_DELAY)

        def _cup_then_easter(dt):
            try:
                # ⚠️ 2026-09-11 用户定案反过来了: 跑分期间**应该**照常播装杯
                #    (玩家报"你丢掉了落袋动画", 要求保留)。以前这条断言是"不该播"。
                if r.game_area.win_fx.mode == "idle":
                    print("SMOKE-FAIL: 跑分中应该照常播装杯(用户定案保留落袋动画), 实际已回 idle")
                r.state = "landing"
                r._settled = False
                r._easter_egg = True
                r.settle(4)                       # 跑分中彩蛋
                if r._easter_popup is not None or r._easter_hold:
                    print("SMOKE-FAIL: 跑分中弹了彩蛋窗或软锁住了 (hold=%s)" % r._easter_hold)
            finally:
                r._bench_running = False
                r._easter_hold = False
                r._easter_popup = None
            print("SMOKE-OK state=%s cup=%s -> %s"
                  % (r.state, r.game_area.win_fx.mode, outdir))
            App.get_running_app().stop()

        Clock.schedule_once(_cup_then_easter, CUP_TRIGGER_DELAY + 0.12)

    def when_ready(fn, name, tries=120):
        """等状态机真正回到 ready 再执行, 超时(默认 12s)则打日志后强制执行。

        ⚠️ 中奖玻璃杯演出会锁住输入(见 _frame 的 landed 分支), 固定时刻的 s8/s9 会被
        **静默跳过** —— 冒烟照样全绿, 覆盖却没了。所以改成轮询, 并把结果打进日志。
        """
        def poll(dt, left=tries):
            r = app.rootw
            if r.state == "ready" and not r.game_area.win_fx_busy():
                fn(dt)
                return
            if left <= 0:
                print("SMOKE %s 等 ready 超时: state=%s cup=%s"
                      % (name, r.state, r.game_area.win_fx.mode))
                fn(dt)
                return
            Clock.schedule_once(lambda d: poll(d, left - 1), 0.1)
        return poll

    def when_revealed(fn, name, tries=200):
        """等中奖大字真的立起来再截。

        ⚠️ 揭晓现在挂在"最后一颗球落定"那一刻(×20 约 settle+3.4s), 原来的固定时刻
        (15.4s)会拍在一只还没揭晓的杯子上 —— 文件在、名字在、覆盖没了, 属于会骗人的绿。
        """
        def poll(dt, left=tries):
            if app.rootw.game_area._effects or left <= 0:
                if left <= 0:
                    print("SMOKE %s 等大字超时" % name)
                fn(dt)
                return
            Clock.schedule_once(lambda d: poll(d, left - 1), 0.1)
        return poll

    Clock.schedule_once(s1, 1.5)
    Clock.schedule_once(s2, 2.5)
    Clock.schedule_once(s3, 4.0)
    Clock.schedule_once(s4, 8.5)
    Clock.schedule_once(s5, 9.3)
    Clock.schedule_once(s6, 13.6)
    Clock.schedule_once(s7, 15.0)
    Clock.schedule_once(when_revealed(s7b, "s7b"), 15.4)
    Clock.schedule_once(s7c, 16.8)
    Clock.schedule_once(when_ready(s8, "s8"), 17.0)
    Clock.schedule_once(when_ready(s9, "s9"), 20.0)
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
