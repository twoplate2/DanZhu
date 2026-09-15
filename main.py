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

# 必须在导入 Window 之前配置。所有平台都保留 SDL 的 swap interval：Windows 上关闭它不会
# 绕过桌面合成器，实测反而把 60Hz 显示器从稳定 60fps 拖成约 50fps。高刷显示器开启 vsync
# 会自然按其刷新率呈现；Android 则由下面的 Window/显示模式请求优先提升到 165Hz。
from kivy.config import Config
Config.set("graphics", "maxfps", "120")
Config.set("graphics", "vsync", "1")

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
from kivy.graphics import (Color, Rectangle, Line, Ellipse, RoundedRectangle,
                            PushMatrix, PopMatrix, Rotate, Scale,
                            StencilPush, StencilPop, StencilUse, StencilUnUse)
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
# ⚠️ 2026-09-15 加: 本文件**第一处**用到 Kivy 属性类(`GlyphLabel` 的 text/font_size/bold)。
#    必须走 `Property` 而不是普通实例属性 —— `_install_fit` 靠 `w.bind(text=...)` 挂
#    "文字一变就重挑字号"的钩子(见 8488), 普通属性绑不上, 表现是**余额再也不缩字号**。
from kivy.properties import StringProperty, NumericProperty, BooleanProperty
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
# 呈现/界面逻辑按可选档位最高的 185Hz 调度；物理仍固定在 60Hz，累加器决定何时推进物理。
# 低刷屏由 vsync 合并 tick，高刷屏可获得与设定档位一致的画面更新。
FRAME_TICK_HZ = 185
FRAME_TICK_DT = 1.0 / FRAME_TICK_HZ
# 一帧最多补几个物理步(超出的积压**丢掉**, 不往下攒)。见 `_clamp_accum` 的说明。
MAX_STEPS_PER_FRAME = 4
FRAME_MS = 16
SUBSTEPS = 6                 # 子步数(增加: 高速下防穿透)


def _clamp_accum(a):
    """把固定步长累加器的积压**截到单帧上限**。

    ⚠️ 为什么必须截(2026-09-14, 冲 1%Low): 真机实测过"单帧最大 dt **625 毫秒** ——
       一帧跑了 **37 个物理步**"(跑分阶段 1 的原话, 见 android/CLAUDE.md)。帧被拖慢之后,
       累加器会把积压的时间**全塞进那一帧** —— 那一帧于是更慢, 而它补出来的步又产生新的
       耗时 ⇒ **"帧慢 → 补更多物理步 → 更慢"的正反馈放大器**(工程文档里点名过这一条)。
       截断之后最坏一帧只补 MAX_STEPS 步, 长停顿不再自我放大。

    ⚠️ **代价是丢掉时间**: 截断 = 那一帧只推进 MAX_STEPS 步, 而墙钟走了更多 ⇒ 球在那一瞬
       **走得比墙钟慢一点**。这是**刻意的取舍**: 停顿时球慢一瞬, 好过整台机器卡 600 毫秒。
       (反过来"把积压留着下帧再还"是错的 —— 那会让之后每帧都跑满上限, 拖出一长串慢帧。)
    ⚠️ 上限别调小: 60Hz 上一帧正常就是 1 步, 30Hz 是 2 步。取 4 留足余量,
       只在真正卡顿时才生效。
    """
    _lim = MAX_STEPS_PER_FRAME * FIXED_DT
    return _lim if a > _lim else a
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
# 弧面碰撞的实际作用半径 —— 与 `_collide_arc` 里算的 r 必须是**同一个值**
# (y 带粗筛要用它当 reach; 两处不同步就会漏碰, 而且是静默的)。
_ARC_REACH = BALL_R * ARC_VISUAL
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
# 跑分用的固定种子(2026-09-14, 玩家提的"跑分改成放录像")。
# ⚠️ **改这个数 = 换一盘录像**: 所有历史跑分都会和新的不可比。**别随手改。**
BENCH_SEED = 20260914
# 跑分用的**固定盘面**(2026-09-14, 玩家提的"放录像"): 第 N 发把**所有格子都填成同一个倍率**,
# 于是"球落在哪一格"不再影响结果 ⇒ 装杯长度确定 ⇒ **每轮跑分的内容一模一样, 可以直接比**。
# 这五个值是玩家定的: 5 / 10 / 20 / 50 / 100 —— **相邻两档大约都是 2 倍, 分布更均匀**
# (原先的 0/2/5/20/100 里 5->20 是 4 倍, 偏了), 而 100 保证**最重的那一档每轮都被量到**。
# ⚠️ 只影响**跑分**; 正常游戏照常掷盘面(掷盘面的规矩见 `roll_multipliers`)。
# ⚠️ 换这五个值 = 换一盘录像, 历史跑分会不可比 —— **别随手改**。
BENCH_BOARD = (5, 10, 20, 50, 100)
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
COL_DARKRED = "#8f3a2e"        # 暗砖红(安卓隐藏档弹窗的"确定"按钮): 深蓝紫底上够沉, 白字够清
                               # ⚠️ 不复用 COL_FIRE(偏亮偏橙, 且已是"蓄力发射"按钮的颜色),
                               #    也不用 COL_x[10](那是槽位倍率色体系, 语义不同)。
COL_GREEN = "#39d98a"
COL_GRAY = "#5a6a8c"
COL_METER = "#f0b000"
# SOC 高压测试那一系的按钮色(2026-09-15)。⚠️ **不复用 `COL_METER`**: 那个 #f0b000 太亮,
# 白字压在上面读不清; 这两个是**同色相、够深**的一对(行动亮 / 历史暗), 白字都够清,
# 而且与"模拟系"的红明显分开 —— 玩家要的"同 1 类是一个色系、不同类分开"就靠这一对。
COL_SOC = "#c07a10"            # SOC 系: 行动(SOC高压测试)
COL_SOC_DIM = "#7a4e0a"        # SOC 系: 历史(高压测试历史)
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

def _collide_pegs(b, rows):
    rr = BALL_R + PEG_R
    rng = getattr(b, "_rng", None) or random    # 确定性: 预演/真发共享同一 rng
    for _row in rows:
        # ⚠️ y 带粗筛(2026-09-13 性能): 整行的 y 离球超过 rr ⇒ 该行一颗都碰不到 ——
        #    "最近点距离 < rr" 的判据下, dy 已经 >= rr 了。**每次重读 b.y**:
        #    上面某颗钉会改 b.y, 缓存住就会漏碰(那就不是等价, 是静默错)。
        if _row[0][1] < b.y - rr or _row[0][1] > b.y + rr:
            continue
        for px, py in _row:
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
            _ylo = w[1] if w[1] < w[3] else w[3]
            _yhi = w[3] if w[1] < w[3] else w[1]
            if _yhi < b.y - BALL_R or _ylo > b.y + BALL_R:
                continue
            _collide_rect(b, w[0], w[1], w[2], w[3], WALL_E, EV_WALL)
        for s in geo["deflectors"]:
            _ylo = s[1] if s[1] < s[3] else s[3]
            _yhi = s[3] if s[1] < s[3] else s[1]
            if _yhi < b.y - _ARC_REACH or _ylo > b.y + _ARC_REACH:
                continue
            _collide_arc(b, s[0], s[1], s[2], s[3], _ARC_FRAME)  # 缓动带球: 贴轨转向, 静音接触
        _collide_pegs(b, geo["peg_rows"])
        for d in geo["dividers"]:
            _ylo = d[1] if d[1] < d[3] else d[3]
            _yhi = d[3] if d[1] < d[3] else d[1]
            if _yhi < b.y - BALL_R or _ylo > b.y + BALL_R:
                continue
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

SOC_WARMUP_CPU_SEC = 1.5
# ⚠️ **样本时长 / 轮数 / 间隔 —— 2026-09-15 按玩家要求整套调大**
#    (玩家:「跑分测试那个 34 秒太短了, 你改为 geekbench6.1 的标准吧」「主要是担心 pc 和手机不一样」;
#     来回试了几版后自己定稿: **跑 5 次, 每次 3 秒, 每次休息 5 秒**)。
#    · ✅ `SOC_SAMPLE_GAP_SEC = 5.0` **就是 Geekbench 6.1 的口径**: 它把 workload 间隔
#      **从 2 秒加到 5 秒**(6.0 是 2 秒), 官方理由是"**减少温控、降低逐次波动**"。
#      这正是玩家"担心 pc 和手机不一样"想要的 —— 间隔够长, SoC 才凉得下来, 测到的才是
#      **峰值**而不是"半烤机"状态, PC 与手机的对比也才公平(手机热容小得多)。
#      ⚠️ **别把它调小**: 中间试过 3 秒, 那是往反方向走(官方加长间隔就是为了防这个)。
#      ⚠️ 它是**墙钟 sleep**, 不是 CPU 秒 —— 要的就是真实散热时间。
#    · ⚠️ **样本时长与轮数不是 Geekbench 的规范**: 官方**没有公开**每个 workload 跑多久
#      (查过, internals PDF 403)。所以"按 6.1 的标准"能唯一确定的只有上面那个 5 秒间隔。
#    · ⚠️ **轮数取奇数 5**(玩家从 8 改到 7 再改到 5): `_bench_done` 取中位数用的是
#      `sorted(v)[len(v)//2]` —— n=8 时那是 `sorted[4]`, 下面 4 个上面 3 个, 是**上中位数**
#      (报出来偏悲观); 奇数时 `sorted[n//2]` 下面上面一样多, 才是真正居中的那个。
#      ⚠️ 顺带: **5 轮 = 4 个间隔**(不是 5 个) —— 第一轮前不休息, 因为预热刚做完。
#      (同一条 `sorted[n//2]` 也用在高压测试的 `_hp_stats` 上, 那里的窗口数由时长决定。)
#    · 账(桌面实测比值 1.31): 跑 = 预热 1.5 + 5x3.0 = **16.5 CPU 秒 ≈ 22 秒真实时间**;
#      休息 = 4 x 5.0 = **20 秒**; 物理段合计 ≈ **42 秒**; 主测试总计 ≈ **67 秒**(渲染 25 + 42)。
#      整屏置灰层要挂这么久, 已告知玩家。
#    · ⚠️ 轮数改了, 状态栏 `物理跑分 d/N` 的分母会**自动跟着变**(它读的就是本常量)。
SOC_SAMPLE_CPU_SEC = 3.0
SOC_SAMPLE_RUNS = 5
SOC_SAMPLE_GAP_SEC = 5.0
# ⚠️ **高压测试(独立的「高压测试」按钮, 2026-09-15 从主测试里拆出来)**。
#    与波 1 相反 —— **窗口之间一秒都不停**, 专门看衰减。
#    ⚠️ 玩家定案: 「新增高压测试按钮, **原有测试是原有时间**」—— 主测试不再跑高压段,
#       它的时长/参数一个字不动(渲染 25s + 波 1 14.5s)。高压单独一键, 要跑多久跑多久。
#
# ⚠️⚠️ **2026-09-15 改口径: 从"CPU 秒"改成"真实时间(墙钟)秒"**。玩家三条要求:
#    「真的至少测试 300 秒 / 测试结束后就要弹窗 / 面板显示的数别太违和」。
#    · 病根: 面板报的是工作线程跑掉的 **CPU 秒**, 而主线程每帧要抢 GIL 渲染, 工作线程
#      只拿到约 3/4 的核 —— 桌面实测比值 **1.31**(20 秒 CPU 的活要 26.2 秒墙钟,
#      见 `temp/hp_tail_probe.py`) ⇒ 旧口径"压 300 秒"实际要玩家等 **约 393 秒**,
#      而面板早在 300/300 就顶格了 ⇒ **面板在骗人**(玩家原话:「跑了 300/300 秒后,
#      很久都没有弹出窗口」)。
#    · 为什么不去调数字凑: 想让"面板显示 360、到 360 必弹"就得把工作量改成
#      360/1.31 ≈ 275 秒, 但 **1.31 是这台电脑的比值, 不是常数**; 手机若为 1.5,
#      275 秒的活要跑 413 秒 ⇒ 面板到 360/360 时活儿还剩 53 秒 —— **同一个 bug 原样回来**。
#      猜比值只在测过的机器上成立。
#    · 解法: **让循环条件本身就是墙钟**(见 `benchmark_sustained`) —— 分母 = 真实秒数目标,
#      分子 = 秒表, 数学上必然在到点那一帧结束, 换任何设备都对得齐, 一个数都不用猜。
#    · ⚠️ 代价(已告知玩家): ①**工作量随设备浮动**(桌面约干 275 CPU 秒的活) ——
#      "步/秒"是**速率**(分母是各窗口自己消耗的 CPU 秒), 仍然可比; 而且受热时间对所有设备
#      都恰好 360 秒, **对测衰减反而更公平**。②旧历史记录的 `sec`=300 是旧口径(CPU 工作量),
#      新记录 =360 是新口径(真实时长), **新老不直接可比**; 步/秒那一列不受影响。
#    · ⚠️ 两个名字必须分得清(这次 bug 的本质就是"两种秒混用一个名字"):
#      `..._WALL_SEC` = 真实时间, 决定**跑多久**;
#      `..._WINDOW_CPU_SEC` = 采样窗口, 仍是 **CPU 秒**, 决定**每份样本多长**("步/秒"的分母)。
SOC_SUSTAIN_WALL_SEC = 360.0
SOC_SUSTAIN_WINDOW_CPU_SEC = 1.0
# 渲染采样窗口自动发几颗球(原来是 `self._target_launches = 5` 写死在跑分函数里, 提成常量)。
BENCH_TARGET_LAUNCHES = 5


# 「频率采样门」(2026-09-15)。**唯一的写者是 `benchmark_trajectories`**(样本之间的
# `sleep(gap_sec)` 期间关门); 唯一的读者是 `_freq_sampler_start` 起的那个采样线程。
# 默认 True = 一直采 —— 所以 `benchmark_sustained`(波 2 / SOC 高压, **没有间隙**)不受影响。
# ⚠️ 为什么必须是**进程级**而不是参数: 采样线程与 benchmark 是两个线程、两个调用栈,
#    它们之间只有模块级状态这一条路。
_FREQ_GATE = [True]


def benchmark_trajectories(warmup_cpu_sec=SOC_WARMUP_CPU_SEC,
                           sample_cpu_sec=SOC_SAMPLE_CPU_SEC,
                           runs=SOC_SAMPLE_RUNS,
                           gap_sec=SOC_SAMPLE_GAP_SEC,
                           on_sample=None):
    """**波 1 —— 测性能(峰值)**: `warmup_cpu_sec` 秒 CPU 预热 + `runs` 轮 x `sample_cpu_sec` 秒
    样本, 取中位数。

    用工作线程自身的 ``thread_time`` 作时间基准，排除等待 GIL、渲染和系统调度的墙钟空档；
    每轮用实际消耗的 CPU 时间作分母，避免旧版「实际跑过 0.7 秒但固定除以 0.7」的偏差。
    返回 ``(总发数, 总步数, 各轮步/秒, 各轮实际CPU秒)``。

    ⚠️ **样本之间留 `gap_sec` 秒**(2026-09-15 从 2 秒改成 **5 秒** —— **这就是 Geekbench 6.1
       的口径**, 6.0 是 2 秒; 详见 `SOC_SAMPLE_GAP_SEC` 处)。没有间隔就是"背靠背烤机",
       后几轮被自己烤热 —— 那测到的**不是峰值**。想测持续性能走 `benchmark_sustained`(波 2),
       **两件事分开测**。
    ⚠️ **顺序必须是先本函数、再 `benchmark_sustained`** —— 反过来的话波 2 先把机器烤热,
       这里就不再是峰值了。
    ⚠️ 间隔期间**主线程照常渲染**, 采样线程也照常采频率 —— 那正是观察降频的窗口。
    """
    geo = build_geo()
    cpu_clock = getattr(time, "thread_time", None) or time.process_time

    def _run_once(cpu_seconds, seed):
        # 每个样本从同一条确定性输入序列起跑，波动反映 SoC 状态，而不是抽到不同球路。
        rng = random.Random(seed)
        flights = 0
        frames = 0
        t0 = cpu_clock()
        while cpu_clock() - t0 < cpu_seconds:
            power = rng.uniform(MISFIRE_POWER, 1.0)
            b = launch_ball(power, rng=rng)
            for _ in range(4000):
                landed = advance_flight(b, geo)
                frames += 1
                if landed is not None:
                    flights += 1
                    break
        used = max(0.000001, cpu_clock() - t0)
        return flights, frames, used

    # ⚠️⚠️ **门: 采样间隙里频率不算数**(2026-09-15 玩家:「必须用跑分时的平均频率
    #    (空闲的时候可以不计)」)。采样线程是每 0.5 秒无脑采一次的, 而样本之间有
    #    `gap_sec` 秒的 sleep(默认 5 秒 × 4 次 = **20 秒**), 那几段应用基本闲着、
    #    频率是低频 —— 不关门的话"平均频率"会被这 20 秒拖下水, 报出来的是
    #    "跑分 + 休息"的平均, 不是"跑分时"的平均。
    # ⚠️ 进门先开门: 预热那一段也算"在跑"(它同样满载)。
    # ⚠️ `benchmark_sustained`(波 2 / SOC 高压)**没有间隙**, 门对它恒真 —— 默认就是 True,
    #    只有本函数会去翻, 互不干扰。
    _FREQ_GATE[0] = True
    _run_once(warmup_cpu_sec, 98765)   # 升频/Python 热身，不计成绩

    fps_list = []
    cpu_seconds_list = []
    total_flights = 0
    total_frames = 0
    for _i in range(runs):
        if _i > 0 and gap_sec > 0:
            _FREQ_GATE[0] = False      # 关门 → 这几秒的频率不进平均
            try:
                time.sleep(gap_sec)    # ⚠️ 只在样本之间停, 第一轮前不停(预热刚做完)
            finally:
                _FREQ_GATE[0] = True   # ⚠️ finally: 中断了也必须把门开回来
        flights, frames, used = _run_once(sample_cpu_sec, 12345)
        fps_list.append(frames / used)
        cpu_seconds_list.append(used)
        total_flights += flights
        total_frames += frames
        # ⚠️ 回调跑在**工作线程**上: 只能写普通属性, **绝不许碰界面**
        #    (界面只能在主线程的 Clock 回调里动)。进度条由主线程按 0.25 秒轮询这个值。
        if on_sample is not None:
            try:
                on_sample(len(fps_list), runs)
            except Exception:
                pass
    return total_flights, total_frames, fps_list, cpu_seconds_list


def benchmark_sustained(total_wall_sec=SOC_SUSTAIN_WALL_SEC,
                        window_cpu_sec=SOC_SUSTAIN_WINDOW_CPU_SEC):
    """**波 2 —— 测高压(衰减)**: 窗口之间**一秒都不停**, 整整压 `total_wall_sec` 秒**真实时间**。

    与波 1 的区别就一个 —— **没有间隔**。问的问题也不同:
      波 1 问"这台机器**最好**能跑多快"(峰值, 跨次/跨设置可比);
      波 2 问"**一直压着跑会掉多少**"(温控衰减, 以及抖成什么样)。

    返回 ``(总发数, 总步数, 各窗口步/秒, 各窗口实际CPU秒)``。
    ⚠️ 口径与波 1 **逐字相同**(同一条确定性输入序列、`thread_time` 当分母、用实际消耗的
       CPU 秒做除法) —— 否则两波的数没法放在一起比。
    ⚠️⚠️ **外循环按 `time.time()`(真实时间)退出, 不是 CPU 时间**(2026-09-15 改; 理由见
       `SOC_SUSTAIN_WALL_SEC` 顶上那段长注释)。**窗口本身仍是 1 CPU 秒** —— 它决定"步/秒"
       的分母, 不许跟着改。
       副作用: 外循环在**窗口开始前**判条件, 所以最后一个窗口会**多做 ≤ 一个窗口**的活
       ⇒ 总墙钟落在 `[total_wall_sec, total_wall_sec + 约1.4秒]`。
       ⚠️ **别为了"精确到点"去截断最后一个窗口** —— 那样它的分母会变得很小、样本变噪,
          而**末窗口正是衰减曲线的关键读数**(`_hp_stats` 的"末")。
    """
    geo = build_geo()
    cpu_clock = getattr(time, "thread_time", None) or time.process_time

    def _run_once(cpu_seconds, seed):
        rng = random.Random(seed)
        flights = 0
        frames = 0
        t0 = cpu_clock()
        while cpu_clock() - t0 < cpu_seconds:
            power = rng.uniform(MISFIRE_POWER, 1.0)
            b = launch_ball(power, rng=rng)
            for _ in range(4000):
                landed = advance_flight(b, geo)
                frames += 1
                if landed is not None:
                    flights += 1
                    break
        used = max(0.000001, cpu_clock() - t0)
        return flights, frames, used

    fps_list = []
    cpu_seconds_list = []
    total_flights = 0
    total_frames = 0
    # ⚠️ **这里是墙钟**(2026-09-15 改): 与外循环判据同一根尺子。
    #    旧版用 `cpu_clock()` —— 那是"工作线程自己跑了多久", 与玩家等的真实时间差 1.31 倍。
    t_all = time.time()
    while time.time() - t_all < total_wall_sec:
        flights, frames, used = _run_once(window_cpu_sec, 12345)
        fps_list.append(frames / used)
        cpu_seconds_list.append(used)
        total_flights += flights
        total_frames += frames
    return total_flights, total_frames, fps_list, cpu_seconds_list


def _freq_sampler_start():
    """起一个 0.5 秒一次的主频采样线程, 返回 `(频率列表, 停止标志)`。

    用 `_cpufreq_mhz()`(读 sysfs), 非安卓/读不到时**采不到任何值**, 列表保持为空 ——
    调用方据此印「没采到」, **不印假数**。
    """
    _frq = []
    _stop = [False]

    def _samp():
        while not _stop[0]:
            try:
                # ⚠️ **关门期间不采**(见 `_FREQ_GATE`): 样本之间那几秒 sleep 里 CPU 是闲的,
                #    采进来会把"跑分时的平均频率"拖低 —— 玩家要的是"跑分时"的平均。
                if _FREQ_GATE[0]:
                    _v = _cpufreq_mhz()
                    if _v > 0:
                        _frq.append(_v)
            except Exception:
                pass
            time.sleep(0.5)

    try:
        threading.Thread(target=_samp, daemon=True).start()
    except Exception:
        pass
    return _frq, _stop


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
    # ⚠️ **隐藏档(2026-09-11)**: 长按"期望返还比例"5 秒解锁, **不保存**(重开要重新解锁),
    #    入口与 UI 见 `tools/android_part_ui.py`。装杯的颗数 = 盘面倍率, 所以"球多"= "倍率大",
    #    而**倍率大在数学上只能靠抬 RTP 档位**: 360% 档的平均每格倍率被锁死在 4.56
    #    (= RTP×9/E[K]), 怎么改形状都只能是 x2 为主。
    #    10.0 = 1000%。玩家定稿: 「中奖率是 100%」⇒ K_DIST 用 9 格全有奖;
    #    「肯定要有 *50 和 *100」⇒ 六档齐全(x2/x5/x10/x20/x50/x100)。
    #    E[盘和] = 10 × 9 = 90, 9 格 ⇒ **每格平均恰好 10 倍** —— 分布唯一的硬约束。
    #    ⚠️ 含 x50/x100 的代价**全落在 x2 头上**: 均值被锁死在 10, 大档越多, 就得靠越多 x2
    #       把它压回来(实测下面这组解出 x2 权重 ≈ 0.56)。这是"必中 + 均值固定"的必然,
    #       不是配得不好 —— 想让 x100 更常见, 只能让 x2 更常见。
    #    最终落地概率: **x2 56% / x5 17% / x10 12% / x20 8% / x50 5% / x100 3%**
    #    本表写的是**非 x2 部分**的整数权重(和不必为 1, 归一化交给 `_shape_of`);
    #     x2 那一档的权重由 `_solve_p2` 反解出来, 不写死。
    10.0: {5: 17, 10: 12, 20: 8, 50: 5, 100: 3},
    # 2000% / 5000%(2026-09-12): 彩蛋弹窗从「开/不开」改成四选一之后的两档新选择。
    # ⚠️ 三档**全部 9 格有奖**(K_DIST = {9: 1.0}) ⇒ 每格均值被恒等式钉死在档位上:
    #      均匀落 9 格 ⇒ E[赔付] = Σ倍率/9 = rtp ⇒ E[倍率] = rtp
    #    所以 **x2 的占比不是自由参数**, 它由 _solve_p2 反解:
    #      p2 = (E[非x2部分] − rtp) / (E[非x2部分] − 2)
    #    人话: **想要大倍率, 就只能拿更多 x2 去把它拉回来**。1000% 档 x2 占 56% 就是这么来的,
    #    不是配得不好, 是"必中 + 均值锁定"的必然代价。
    # 下面两组的实测落格分布(20000 局 MC, 空军 0):
    #   2000%: x2 43.8 / x5 5.7 / x10 11.2 / x20 18.0 / x50 14.7 / x100 6.7   (x50 约 7 局一次)
    #   5000%: x2 22.7 / x20 15.7 / x50 30.7 / x100 30.9                      (三局里两次大奖)
    # ⚠️ 5000% **故意不放 x5/x10**: 均值锁死在 50, 掺小倍率反而要更多 x2 去平衡
    #    (p2 会涨到 0.4 以上), 大奖反而更少见。用 x20/x50/x100 三档就够。
    20.0: {5: 10, 10: 20, 20: 32, 50: 26, 100: 12},
    50.0: {20: 20, 50: 40, 100: 40},
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
    10.0: {9: 1.0},                            # ⚠️ 临时测试档(不提交): **9 格全有奖 = 必中**。
    #   玩家 2026-09-11 吐槽「5000% 还tmd空军」—— 空军是**这张表**管的, 不是 RTP 管的:
    #   RTP 只管"中了给多少", K_DIST 管"几格有奖"。原来沿用最高档的 {6/7/8}, 9 格里总有
    #   1~3 格是空的(实测空军率 21%), 落在空格的观感就是"白瞎一发"。
    # 2026-09-12: 2000% / 5000% 同样必中 —— 玩家定稿「都没有空军的」。
    20.0: {9: 1.0},
    50.0: {9: 1.0},
}

def _shape_of(rtp):
    """取**归一化后**的形状 —— 表里允许写整数权重(和不必为 1)。

    2026-09-11 加: 隐藏档 50.0 的权重是玩家直接给的(25:25:25:35), 凑成小数既难读、
    又会被顶层 assert 卡在小数点后几位上(凑不出精确的 1.0)。其余各档的表本来就和为 1,
    过一遍这里是恒等变换。"""
    sh = VALUE_SHAPE[rtp]
    tot = float(sum(sh.values()))
    return {v: w / tot for v, w in sh.items()} if tot else sh

def _effective_rtp(p2, rtp):
    """给定 x2 权重 p2, 闭式算出含坏盘重抽后的 RTP(= E[盘面倍率和]/9)。
    坏盘 = (x2 占比 >= PITY_RATIO) 或 (>=2 个高倍率); 坏盘必重抽, 最多 MAX_REROLL 次。
    E[盘和'] = E + (P + P^2 + ... + P^R) * (E - E[坏盘]), P = 坏盘概率, R = MAX_REROLL。"""
    shape, kd = _shape_of(rtp), K_DIST[rtp]
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
for _rtp in (0.80, 1.20, 2.00, 3.60, 10.0, 20.0, 50.0):
    # ⚠️ `VALUE_SHAPE` 允许写**整数权重**(和不必为 1, 归一化由 `_shape_of` 做) —— 所以这里
    #    只校验"权重为正"; `K_DIST` 是概率分布, 仍然要求和 = 1。
    assert all(w > 0 for w in VALUE_SHAPE[_rtp].values()) and \
           abs(sum(K_DIST[_rtp].values()) - 1) < 1e-9, "权重/K 分布不合法: %.2f" % _rtp
    _p2 = _solve_p2(_rtp)
    VALUE_DIST[_rtp] = {2: _p2}
    VALUE_DIST[_rtp].update({v: (1 - _p2) * w for v, w in _shape_of(_rtp).items()})
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
# 并发声道数(可同时叠加的音效数)。
# ⚠️ 2026-09-14 由 8 提到 16, 是冲着**真机实测的那个 53 毫秒/次**去的:
#    真机跑分测出 `SoundPool.play()` 单次最慢 143.6ms、50 次累计 2674ms(平均 53.5ms),
#    而帧间隔才 12.5ms。最常见的成因就是**声道不够 → SoundPool 要抢一条正在响的流**,
#    抢流要停掉再启一条 AudioTrack, 走音频服务。
#    本作最长的音: `win6` **1980ms**、`win5` 1780、`flight` 1500 —— 一条 2 秒的音
#    就占死一条声道 2 秒; 揭晓那一刻"中奖琶音 + 语音(1~3秒) + 落珠 10 次/秒 + coin"
#    很容易顶到 8 条上限。提到 16 只是给 SoundPool 更多余量, **不改任何播放逻辑**,
#    而且顺带**少掐断正在响的音**(文档里记着: 满了的行为不是丢音, 是把还在响的流当场掐断)。
#    ⚠️ 这一条是**根因尝试、不是已证实的修复** —— 判据看跑分面板的"发声·单次最慢":
#       前端值 143.6 毫秒, 若明显下降就是这个方向对了; 若纹丝不动说明慢在别处(音频 HAL 唤醒等)。
#    ⚠️ 反向风险(为什么只敢翻倍、不敢更大): 并发流越多, 音频线程的混音/重采样越重。
#       16 条短音对现代 SoC 是小事, 但 32 条就没把握了 —— 别再往上加。
# ⚠️ 2026-09-14 **改回 8**。当初 8 -> 16 的理由是"声道不够 -> SoundPool 抢流 ->
#    主线程卡几十毫秒", 但那条因果链**在 v0.6.65 就已经断了**: `SoundPool.play()` 现在跑在
#    **发声工作线程**上, 抢流只会让工作线程多阻塞一会儿(队列满了就丢一声), **再也到不了
#    主线程**。留下的只有代价那半边 —— 上面自己写着"并发流越多, 音频线程的混音/重采样越重";
#    而文档还记着满了的行为是"把还在响的流当场掐断", 那本来是 16 想避免的、现在由队列兜着。
#    也就是说: 16 相对 8 **没有任何已证实的收益**, 只有"更多 AudioTrack 同时活着"这一项开销。
#    ⚠️ 这是一条"把无收益的改动退回去"的**低风险**改动, 不是新优化 —— 没有观感变化。
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

_VOICE_FILES_CACHE = [None]      # None = 还没列过; 列到东西了才缓存(见下)


def _voice_files():
    """{语音名: wav 路径}。语音是 edge-tts 预录文件(tools/generate_voice.py 生成),
    不是 bake_bank 的合成品, 不参与 iter_bank 的"顺序即音色"体系。

    ⚠️ 2026-09-14 **加缓存**。原来每次调用都是一次 `os.listdir(voice/)`(**63 项**) ——
    在安卓上走 FUSE, 目录列举不是免费的; 而且老写法还把 `_voice_dir()` 重复算 63 次
    (`os.path.join(_voice_dir(), fn)`)。它挂在**主线程**上(`voice_duration` 由 `Sfx.play`
    和 `_play_voice_sequence` 调用), 而 `_play_voice_sequence` 一轮要查 4~8 段 ——
    就是**同一帧里 4~8 次目录列举**。
    目录内容运行期不会变(语音是打包进来的), 所以缓存是安全的。
    ⚠️ **只缓存非空结果**: 首次调用若目录还没解包出来(listdir 抛异常或空), 不能把空字典
    缓存住 —— 那会让语音**永久静默失效**。拿不到就下次再列。
    """
    got = _VOICE_FILES_CACHE[0]
    if got is not None:
        return got
    out = {}
    try:
        d = _voice_dir()
        for fn in os.listdir(d):
            if fn.endswith(".wav"):
                out[fn[:-4]] = os.path.join(d, fn)
    except Exception:
        pass
    if out:
        _VOICE_FILES_CACHE[0] = out
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
    # ⚠️ **真机实测: `SoundPool.play()` 会阻塞调用线程几十到一百多毫秒**
    #    (Y700 二代: 50 次调用累计 2674ms、单次最慢 143.6ms, 而帧间隔只有 12.5ms
    #     ⇒ 主线程每响一声就被卡几十毫秒, 1%Low 只有 7.8 就是它造成的)。
    #    声明这一条 ⇒ `Sfx` 会起一个工作线程, 主线程只投递不等待(见 `Sfx._drain`)。
    #    只给这一个后端开: winmm 桌面实测单次 0.9ms 不值得动; Kivy-SoundLoader 走 SDL,
    #    从工作线程调它的安全性没有验证过, 不开。
    needs_worker = True

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
# 跑分采样期的"这一帧发生了什么"计数器(2026-09-13 加)。
# ⚠️ 为什么需要它: 真机(Y700 二代)实测那个偶发长停顿, 特征是**周期性(约每 0.5 秒一次)**
#    且**是等出来的不是算出来的**(155ms 的帧只烧了 62.8ms CPU)。而玩家手上唯一有因果力的
#    线索是「关掉音效后 1% low 大幅回升」—— 可 `_vibrate_tick` 挂在 `sfx.play()` 的返回值上,
#    关音效会**连震动一起关掉**, 两条怀疑路径混在一起, 分不出是谁。
#    这里逐帧记下"发了几次声 / 震了几次", 跑分面板就能报出**慢帧里有几帧在发声/震动** ——
#    一刀切开。只在跑分采样期读, 平时只多两次列表自增。
# 2026-09-14 加第三格: **本帧跑了一次启动预热**。
# ⚠️ 为什么必须能看出这一格: 桌面逐帧归因实测 —— 最慢的 11 帧**全部**落在启动后 0.6 秒内
#    (= 预热链), 而那些帧的"本线程自算"只有 0.03~0.10 毫秒(我们自己的代码什么都没干)。
#    真机上一个预热单步是 **100~200 毫秒**(球纹理烘焙; 桌面只要 16.6), 而跑分的采样窗口
#    只有约 25 秒(`_target_launches = 5`), 玩家又是**启动后 3 秒就长按标题**开跑的 ——
#    预热很可能还在跑, 正落在采样窗口里, 把 1%Low 压下去。
#    分不分得出来, 决定了两件完全不同的事:
#      慢帧里有预热 ⇒ 那是**启动期**的账, 玩家实际游玩(预热早跑完)没那么差;
#      慢帧里没预热 ⇒ 那是**稳态**的账, 得继续往别处找。
_FRAME_PROBE = [0, 0, 0]         # [本帧发声次数, 本帧震动次数, 本帧是否跑了预热]

# 本帧**各子步骤**各花了多少秒: {名字: 秒}。由下面几个包装器累加, `_on_flip` 读完清零。
# ⚠️ 为什么必须细分到子步骤: 面板到 v0.6.81 为止只能说出"这一帧是**待机**、`_frame` 自己烧了
#    **9 毫秒**"(真机连续两份面板都这样: "36毫秒(待机·实算27.5·**自算8.1**)" /
#    "34毫秒(待机·实算25.6·**自算9.1**)") —— 但**不知道那 9 毫秒花在哪**, 只能靠桌面一条条
#    排除。细分之后真机一次跑分就能指名道姓。
# ⚠️ 只包**几个**大头(板面动态重画 / 重掷盘面 / 自适应字号 / 装杯重画), 不是每个函数都包 ——
#    包装本身有开销, 包多了反而把被测量的东西改变掉。
_FRAME_BRK = {}                  # 本帧累计(会被 _on_flip 读走并清空)
_BRK_KEYS = ("板面", "重掷", "字号", "装杯")
# 本帧「字号」这一格的**分解计数**: [ `_fit1` 被真正调了几次, `text_px` 冷测量了几回 ]。
# ⚠️ 为什么光有毫秒不够(2026-09-15 加, 起因是 v0.7.28 真机日志):
#    「字号」一帧烧了 **50.7 毫秒**(帧723), 而上一版同一格只有 16.5。同一个名字下
#    藏着两种**完全不同**的病, 光看总数分不出来:
#      · 「叫了 1 次、量了 12 回」= **一次** `_fit1` 走完阶梯 + 二分, 每回都是冷字号
#        ⇒ 该收敛字号集合(那 12 个字号这辈子只用这一次);
#      · 「叫了 20 次、量了 20 回」= 一帧里好几个标签同时换字
#        ⇒ 该错峰/合并, 收敛字号没用。
#    这两个数必须和毫秒**一起**印出来, 否则下一轮还是只能猜。
_FRAME_FIT = [0, 0]
# `_frame` **算完那一刻**的 `perf_counter`(0 = 本帧还没算完 / 已经用掉了)。
# 用途: 在 `flip()` 里算出"自算之后到交画面之间"有多长, 记成子步骤 `尾`。
# 见 `_swap_wrap` 里 `_brk_add("尾", ...)` 处那段说明。
# 本次跑分里**最慢的几次「冷字号测量」**: [(毫秒, 字号, bold, 标签), ...]。
# ⚠️ 为什么要单独留这一份(2026-09-15): 真机日志只肯说「字号 21.7 毫秒」, **不说是哪个字号、
#    哪个标签** —— 而修法完全取决于这个:
#      · 是漏烘了某一档   ⇒ 补预热表;
#      · 是某个标签的基准压根没进表(或它的 bold 在运行期变了) ⇒ 得从标签那边修。
#    我在这件事上已经猜错过两次(先说"冷开只要 1.4ms"、又把桌面探针测到的那个当成了真机的),
#    所以这次不猜了 —— 把 (字号, bold, 标签) 原样印进日志头部。
# 跑分采样期**按阶段采样到的屏幕刷新率**: {(阶段, Hz): 次数}。
# ⚠️⚠️ 为什么必须有这一栏(2026-09-14 玩家报的): 「**发射的时候必然会降低帧率上限**」——
#    如果屏幕上真的降到 60/120Hz, 那么那一段的帧间隔会**自然变长**(60Hz = 16.7 毫秒),
#    于是:
#      ① 那些帧会被判成"卡顿", 而其实**画面是满帧的**, 不是我们的锅;
#      ② 新判据的分母是**中位帧间隔**, 窗口里混进一段低频会把中位往大推 ⇒ 判据线跟着动。
#    现在这份日志只印**采集那一刻**的 Hz(一个点), 答不了"窗口中间变没变"。
#    这一栏按阶段采样, 直接回答"发射/蓄力期到底降没降"。
_BENCH_HZ = {}
# 「卡顿帧 / 慢帧」两档门槛, 都是**相对本机中位帧率**的**帧率**比例(2026-09-14 定稿)。
# ⚠️ **只许在这里写数字**。原先 0.55/0.75 散在四处(收尾统计判 jank/slow 两处 +
#    日志头两颗 ★ 两处), 那就是本工程反复栽的「两处各算一遍必然脱钩」的形状 ——
#    改了判据忘了改打印, 或者反过来, 画面/日志就会给出互相矛盾的数。
# ⚠️ 语义: 帧率 < 中位 x JANK_RATE  => 卡顿帧;   < 中位 x SLOW_RATE => 慢帧。
#    等价于**帧间隔** > 中位间隔 / 该比例(所以下面写的是 `_med / JANK_RATE`)。
#    慢帧那一档**包含**卡顿帧(同一个分母, 宽的必然包含窄的)。
JANK_RATE = 0.55
SLOW_RATE = 0.75
_COLD_FS = []
_COLD_FS_TAG = ["?"]
# 本帧冷字号测量用的**基准字号**(`_fit1` 传进 `fit_font_size` 的那个 `b`)。
# ⚠️ 为什么要它(2026-09-14 真机 v0.7.35): 榜单上那 5 条冷字号的"离最近预热档差"是
#    0.18~3.37 —— **差得不像是四舍五入**, 说明它们压根不在表里。而表 = 各标签基准 x FIT_SCALES,
#    所以它们要么是 `基准 x FIT_FINE`(v0.7.32 为了压到 64 上限以内**故意不烘**的那 7 档),
#    要么是"某个没进表的基准"。**光看一个 fs 分不出这两者** —— 记下基准之后,
#    `fs / 基准` 直接就是那个倍率, 一眼能看出它在 FIT_SCALES 里还是在 FIT_FINE 里。
_COLD_FS_BASE = [0.0]
# 多慢才算「冷」—— 下限设 3 毫秒: 热字号一次量出来是 0.02~1.5 毫秒(桌面实测),
# 3 毫秒已经远超它, 不会把热测量混进来。
COLD_FS_MIN_MS = 3.0
# 预热表里**真正传给 `text_px` 的那些值**(原值, 不四舍五入), [(字号, bold), ...]。
# ⚠️ 为什么要留这一份: v0.7.31 真机逮到 5 个"冷字号", 字号是 `57.2375` 这种带小数尾巴的,
#    而 v0.7.30 已经把二分的任意值换成固定档了 —— **理论上不该再有**。真相只有两种:
#      (a) 预热时 `round(_b * _k, 4)` 过、运行期没 round ⇒ 差一点点 ⇒ 是两个 fontid;
#      (b) 预热用的基准和运行期用的基准根本不是同一个数 ⇒ 差得远。
#    两者修法完全不同, 而日志上只印一个四舍五入到 4 位的字号**分不出来**。
#    所以这里存原值, 冷测量时顺手找**最近的同 bold 预热档**并记下差值。
_FONT_WARM_ALL = []
# 预热**真的调过 `text_px`** 的那些 (字号, bold)。和 `_FONT_WARM_ALL`(表里列了什么)不同 ——
# 这一份是"确实开出过这个 fontid"。冷测量时两相对照就能分清最后一层:
#   表里有 + 量过 + 仍是冷 ⇒ **被 Kivy 的字体缓存挤掉了**(warm 再多也没用, 得减字号总数);
#   表里有 + 没量过   ⇒ 预热那一句被 `text_px` 自己的缓存挡掉了(等于没烘)。
_WARM_DID = set()
# `_frame` **算完那一刻**的 `perf_counter`(0 = 本帧还没算完 / 已经用掉了)。
# 用途: 在 `flip()` 里算出"自算之后到交画面之间"有多长, 记成子步骤 `尾`。
# 见 `_swap_wrap` 里 `_brk_add("尾", ...)` 处那段说明。
_FRAME_END = [0.0]

_FRAME_THR = [0.0]               # 本帧**主线程**烧了多少毫秒 CPU(线程级时钟)
# 上一帧 `Window.flip()`(**真正把画面交给系统**那一步)阻塞了多少毫秒。
# ⚠️ 为什么必须单独有这一格(2026-09-14, 120Hz 与 60Hz 两份真机日志对比之后):
#    实测 Kivy 的**绑定回调 `on_flip` 跑在默认处理器之前**, 而真正做 swap 的是默认处理器
#    (`WindowBase.on_flip` -> `self.flip()` -> `WindowSDL.flip`)。也就是说 `_on_flip` 里的
#    那个时间戳打在**画面还没交出去**的那一刻 ⇒ 记下来的"帧间隔"里虽然**含**着等屏幕的时间,
#    却**分不出来**它占多少。实测证据(桌面 `temp/fliporder.py`): 事件序列恒为 `CB SWAP CB SWAP`。
#    后果: 真机 v0.7.23 那批「主线程只烧 0.8~3.1 毫秒、帧却走 11 毫秒」的慢帧, 到底是
#    **我们交晚了**还是**屏幕/缓冲队列不让我们交**, 整个日志头一个字都答不上。
#    `time.thread_time()` 也救不了: 阻塞在驱动 fence 上**不算 CPU 时间**, 那批帧两个数都小。
# 配对: 本格在 `flip()` 里写、在下一次 `_on_flip` 里读走 —— 正好对应"这段时间里那一次 swap"。
_FRAME_SWAP = [0.0]
_FRAME_CALLS = [0]               # `_frame` 被调了几次(与"采样了多少帧"比, 见面板"节拍"行)
_SINCE_LAUNCH = [0]              # 距上一次 `launch()` 过了多少帧(判 C6: 最差帧是不是紧跟在发射后)
_TEXUPD = [0]                    # 本轮跑分内的文字重排累计次数
_TEXUPD_BY = {}                  # {来源标签: 次数}，只在跑分采样窗口统计
_TEXUPD_ACTIVE = [False]         # 避免启动/成绩弹窗的重排污染游戏采样


def _tag_texupd(label, tag):
    """给会动态变化的 Label 标记来源，供跑分定位文字纹理重建。"""
    try:
        label._texupd_tag = str(tag)
    except Exception:
        pass
    return label


def _set_label_text(label, text):
    """只有文本真的变化才触发 Kivy Label 的属性更新与后续纹理检查。"""
    try:
        if label.text == text:
            return False
        label.text = text
        return True
    except Exception:
        return False


def _hist_stamp(raw):
    """把记录里的时间戳**归一化**成 `2026-09-15 14:07`(年月日 + 时间)。

    玩家 2026-09-15 两次定案: 先要「只有月日 2 个数值」, 看过实际排版之后又改回
    「**日期改为年月日时间**(看起来宽度足够)」—— 连**时间**一起要。所以这里不再是
    "缩短", 而是"归一化": 认得出就按 `%Y-%m-%d %H:%M` 重排, 认不出就原样返回。

    ⚠️ **只改显示** —— 盘上本来存的就是 `%Y-%m-%d %H:%M`, 所以旧记录一条都不用动。
    ⚠️ 宽度账(**实测**, `temp/benchhist_probe.py` 的 F 判据直接量建好的 Label):
       这一串 16 字符约 120px, 整行约 **364px**。360dp 上弹窗内容区只有 **275.2px**
       ⇒ `_fit_uniform` 把字号缩到 **11.48sp**(540dp 上是 **14.00sp**, 不缩)。
       **仍然一行、不折、不裁** —— 但 360dp 上确实比"只有月日"那版小了一档多
       (那版约 272px ⇒ 满 14sp)。这是玩家看过排版之后自己选的取舍。
       ⚠️ 11.48sp 已经压到 Android 正文建议下限(12sp)之下, 再往这一格加字就要重算。
    ⚠️ 认不出来**原样返回**, 绝不吞信息、也不抛。
    """
    _s = str(raw if raw is not None else '')
    try:
        _d = time.strptime(_s[:16], "%Y-%m-%d %H:%M")
        return time.strftime("%Y-%m-%d %H:%M", _d)
    except Exception:
        return _s or '--'


# ---- 「模拟测试历史」那张表的四个字段(2026-09-15) ---------------------------
# ⚠️⚠️ **为什么是"一串空格分隔"而不是固定列宽的表格**: 玩家 2026-09-15 加第四列
#    (CPU平均频率)之后实测(`temp/hist_colwidth_probe.py`)四列在 14sp 下要 **333px**,
#    而弹窗内容区在 360dp 上只有 277.6px(0.92 宽) —— **放不下**。
#    固定列宽时"每一列各自要装下自己最宽的那一串"(时间 58 / 帧 96 / 跑分 95 / 频率 84),
#    余量要付四遍; 改成整行一起排之后**同一行数据只要 272px**, 满 14sp 就放得下。
#    ⇒ 玩家原话:「数据用空格分割, 不强制要求对齐了」。
# ⚠️ **表头与数据行必须共用同一个分隔串** —— 于是把它提成常量, 两边都 `join` 它,
#    从结构上杜绝"表头用一种分隔、数据行用另一种"。
# ⚠️ **字段顺序按玩家 2026-09-15 给的样例**:
#      `08-25 59.8/53.0 4256Mhz 14685/5.8%`
#    = 时间 / 平均·1%Low帧 / **CPU平均频率** / 中位跑分·波动
#    —— 频率在**第三位**(跑分/波动之前), **不是**放在最后。
# ⚠️ 单位写 `Mhz`(玩家两次都这么写), 不写成 `MHz`。
_HIST_COLS = ('时间', '平均/1%Low帧', 'CPU平均频率', '中位跑分 / 波动')
_HIST_SEP = ' '
_HIST_HEAD = _HIST_SEP.join(_HIST_COLS)


# ---- 字体"钉子"(2026-09-15, 对抗性评审 top 3 之一) ---------------------------
# Kivy 的 `_text_sdl2.pyx` 里有两个模块级容器:
#     cdef dict sdl2_cache = {}          # fontid -> _TTFContainer(持有 TTF_Font*)
#     cdef list sdl2_cache_order = []    # 插入序; 超过 64 就 `pop(0)` + `del cache[popid]`
# **命中只读 dict、不碰 order; 淘汰只从 order 队头取。** 于是有一条 Kivy 没打算给、
# 但结构上成立的用法: **把某个 fontid 从 order 里 `remove` 掉且不 append** ⇒ 它永远
# 排不进淘汰队列 ⇒ **dict 里那份永远不会被 `del`, 那个 fontid 再也不会被冷开**。
#
# ⚠️ 为什么需要它: 真机日志「预热时量过=**是** 却仍冷」= 预热真的烘过它, 但被后来的
#    字号挤掉了。`fs=45.1875 [累计1投1中(100]` 一次冷开 **47.0ms**(第 1、4 轮的帧724)。
# ⚠️ **为什么不钉静态名单**(评审 A 的版本): 实测每轮开过 **86~93 个**不同字号,
#    全钉上去按 0.88MB/个 ≈ **+49~104MB RSS**; 而真正"复冷"的只有 **22 个**(桌面普查)。
#    ⇒ 改成**复冷时才钉**: 一个 (字号,bold) 第二次被冷开, 就说明它刚被驱逐过 —— 那一刻
#      把它摘出 order。钉的正是受害的那几个, 数量自限, 且**不需要任何预设名单**。
# ⚠️ 硬上限 `_PIN_MAX`: 每个 fontid 实测约 **0.88MB**(NotoSansSC, psutil 两次复现),
#    24 个 ≈ +21MB —— 这是"按 RSS 预算倒推"的上限, 不是"能钉多少钉多少"。
# ⚠️ **绝不 append**: order 里出现重复项时, 淘汰那句 `del sdl2_cache[popid]` 会 KeyError
#    崩在渲染路径上(评审 B 本机复现过)。所以只准 `remove`, 且钉之前断言 order 无重复。
# ⚠️ **找它只能在启动期做一次**: `gc.get_objects()` 要建 5.4 万个引用的列表, 在采样窗口里
#    调它本身就是一次长帧。
_PIN_ORDER = [None]      # [那个 list]; None = 还没找过
_PIN_DONE = [None]       # set(); None = 还没建(省一个模块级 set)
_PIN_MAX = 24
_PIN_STAT = [0, 0, 0]    # [钉成功, 钉失败, 复冷次数] —— **必须印进日志, 不许静默**
_PIN_WHERE = ["还没找"]   # 定位结果的一句话说明(日志里印)
_PIN_CAND = [0]          # 那次扫描里"形状对得上"的 list **一共有几个**(见 `_PIN_WHERE`)


def _find_sdl2_order():
    """在 CPython 对象图里找出 Kivy 的 `sdl2_cache_order`。找不到返回 None。

    ⚠️ `getattr(_text_sdl2, "sdl2_cache_order")` **拿不到** —— 它是 `.pyx` 里的 `cdef`，
       不进模块 dict(评审两位都核过)。但它是货真价实的 **list 对象**，被 GC 跟踪，
       `gc.get_objects()` 能扫到。
    判据: 元素**全是** `'<字号>|<字体全路径>|<bold>|<italic>|<underline>|<strikethrough>'`
       形状的字符串(6 段、5 个竖线) —— 这个形状在本进程里是**唯一**的。
    ⚠️ **"唯一"是判据的一部分, 所以要把命中数记下来**(`_PIN_CAND`) —— 万一命中 2 个以上,
       这里返回的是**先扫到的那个**, 可能是错的, 而错的后果同样是**静默不生效**。
       记下来, 日志里就能一眼看出"是没找到"还是"找到好几个、可能认错了"。
    ⚠️ 认不出来就**如实返回 None**(日志里印"没找到"), 绝不猜。
    """
    _n = 0
    _first = None
    try:
        import gc as _gc
        for o in _gc.get_objects():
            if type(o) is list and 8 <= len(o) <= 64:
                for x in o:
                    if not (isinstance(x, str) and x.count("|") == 5):
                        break
                else:
                    _n += 1
                    if _first is None:
                        _first = o
    except Exception:
        pass
    _PIN_CAND[0] = _n
    return _first


def _pin_fontid(fid):
    """把一个 fontid 从淘汰队列里摘掉(只 remove, 绝不 append)。返回是否成功。

    ⚠️ 三条硬约束(评审给的, 每条都有实测支撑):
      ① **绝不 append** —— order 里出现重复项 ⇒ 淘汰时 `del cache[popid]` KeyError, 崩在
         渲染路径上;
      ② 钉之前断言 `len(order) == len(set(order))` —— 不成立就整条停用(失败要计数上报);
      ③ 超过 `_PIN_MAX` 就**不再钉**(RSS 预算)。
    """
    if _PIN_ORDER[0] is None:
        return False
    if _PIN_DONE[0] is None:
        _PIN_DONE[0] = set()
    if fid in _PIN_DONE[0] or len(_PIN_DONE[0]) >= _PIN_MAX:
        return False
    _o = _PIN_ORDER[0]
    try:
        if len(_o) != len(set(_o)):        # 约束②
            _PIN_STAT[1] += 1
            return False
        if fid not in _o:
            return False
        _o.remove(fid)                     # 约束①(只删不加)
        _PIN_DONE[0].add(fid)
        _PIN_STAT[0] += 1
        return True
    except Exception:
        _PIN_STAT[1] += 1
        return False


def _bench_menu_desc():
    """跑分菜单里那段说明的正文。

    ⚠️ **抽成独立函数是为了让探针能直接读到"出货那一份"**(`temp/check_desc.py`
       —— 它原来从 `tools/android_part_ui.py` 里正则捞字面量, 而 `tools/` 与
       `android/main.py` **早已分叉**, 量到的是别的版本; 这个工程的惯例见
       `_bench_save_board` 的注释:「抽成独立方法是为了能被探针直接调」)。

    ⚠️ 末句的「连压 N 分钟」**必须走常量**(2026-09-15 改): 它原来是硬编码的
       `连压 5 分钟`, 改 `SOC_SUSTAIN_WALL_SEC` 时**不会跟着变** —— 本轮把高压口径
       从 300 秒改成 360 秒时差点漏掉, 那就会印"连压 5 分钟"而实际跑 6 分钟。
    ⚠️ 开头那个「约 67 秒」是**手写的粗估**, 不是算出来的: 它 = 渲染窗口(约 25 秒,
       由 `_target_launches` 发 5 颗球决定, 不是常量) + 波 1(预热 + `SOC_SAMPLE_RUNS` x
       `SOC_SAMPLE_CPU_SEC` + 间隔)。改了那几个参数要回来改这个数 —— 可用
       `python temp/check_desc.py` 顺手复核排版(它同时会量每行宽度)。
    """
    return ('模拟测试约 67 秒（含落珠动画）。\n测试两项设备性能：\n1. 自动发 3 颗球，测屏幕渲染帧率\n'
            '2. 物理引擎全力跑，测每秒模拟步数\n第 2 项主要吃 CPU 单核浮点算力。\n'
            '物理引擎是纯 Python 写的。\n'
            'SOC高压测试：连压 %d 分钟，看持续性能衰减。'
            % (int(SOC_SUSTAIN_WALL_SEC) // 60))

# ⚠️ 为什么必须单独有"主线程 CPU"这一格(2026-09-14, 玩家质疑"9 毫秒是不是小头"之后补):
#    真机面板上那三帧的账**对不上** —— 40毫秒(待机·实算29.0·自算8.1) 还有约 19 毫秒没人认领;
#    36毫秒(飞行·实算26.2·**自算0.3**) 整帧 23.5 毫秒的超出**几乎全不在 `_frame` 里**。
#    而面板此前能看到的只有 `_frame` 自己。**主线程上还有一大块在 `_frame` 外面**:
#      · **Kivy 自己的渲染**(canvas 遍历 + GL 提交);
#      · **Kivy 用 Clock 延后去做的 `Label.texture_update`**(重测+重光栅化+重建纹理+上传);
#      · 其它 Clock 回调。
#    `time.thread_time()` 是**线程级** CPU 时钟(安卓/linux 纳秒级), 覆盖本线程上跑过的一切,
#    又天然排除发声/震动/守卫那几条工作线程。三个数一摆就能分流:
#      实算(全进程)大 · 主线程小   => 工作线程在忙;
#      主线程大 · `_frame` 小      => **Kivy 渲染 / 文字重排**在吃;
#      `_frame` 大                => 就是我们自己的代码。
# ⚠️ **这几个名字必须定义在这里**: `_on_flip` / `_start_benchmark` / `_bench_collect_diag`
#    引用它们时用的是**裸名**。漏定义的话 `--selftest`/`--smoke` 都测不出来(它们不跑跑分),
#    一到真机跑分就 NameError 崩 —— 这个坑 2026-09-14 已经踩过一次(见 v0.6.83 提交说明)。
_THREAD_TIME = getattr(time, "thread_time", None) or time.process_time


# ---- 文字纹理缓存(按标签自己存, 不做跨标签共享) ------------------------------
# ⚠️ **设计取舍的完整理由见 `_texupd_wrap` 的 docstring** —— 一句话: Kivy 的
#    `CoreLabel.refresh()` 在**尺寸没变**时会 `texture.ask_update(...)` **原地重画进同一张纹理**,
#    所以跨标签共享会让 A 改字时把 B 正在显示的字悄悄改掉。按标签存零风险。
# ⚠️ **必须是 64 而不是 12**(2026-09-14 改): 状态栏现在要存 **10 条固定文案 + 35 条带数字的**
#    (见 `_build_texwarm` 的枚举预热) = 45 项。上限留在 12 的话它会**自己把自己挤掉** ——
#    烘一个丢一个, 命中率反而跌回去, 而且**不报错**。
_TEXTEX_PER_LABEL = 64
_TEXEX_HIT = [0]            # 跑分采样期命中次数(进日志, 用来验证这层到底有没有生效)
_TEXEX_MISS = [0]

# ⚠️⚠️ **2026-09-14: 这一层的实现方式换过一次, 别改回去。**
#
# **不能直接缓存 Kivy 给标签建的那张 `Texture`** —— 桌面用逐像素比对验过
# (`temp/texcache_probe.py`): 计数器证明命中 12 次, 但把命中纹理的 `pixels` 和"该句现烘一张"比,
# **4 句里有 3 句不一致**(尺寸全对 78/39/30/66, 所以只看尺寸永远发现不了)。
# 病根: `CoreLabel._texture_fill(self, texture)` **忽略传进来的 texture 参数**, 方法体只有一句
# `self.render(real=True)` —— 它画进的是**那一刻标签的 `self.texture`**, 不是当初
# `Texture.create(callback=...)` 注册回调的那一张; 而填充推迟到 Texture 第一次 `bind()` 才发生。
# 这段延迟里谁先被 bind, 内容就写进谁 ⇒ 缓存下来的对象张冠李戴。
#
# **现在的做法**: 每个指纹配一个**专用的 `CoreLabel`**, 它的 `text`/`options` 从此**永不改变**
# ⇒ 延迟填充只会发生一次, 而且填的必然是它自己的内容, 不可能被覆盖。
# 未命中时**不让标签自己渲染**, 直接把这个专用 CoreLabel 的纹理交给标签 ——
# 于是标签显示的东西 100% 来自那张稳定的纹理。
# 这条范式工程里本来就有(`slot_text_tex` 5603 的槽位倍率贴图), 只是这次把它推广到动态文字。
_TEXEX_ON = True

# ⚠️ 字段表**必须覆盖影响纹理的一切**, 按 Kivy `Label._font_properties` 抄。
#    漏一项 = 那个属性改了但缓存命中 ⇒ 显示旧内容, 而且**只在命中时犯**, 极难复现。
#    宁可多带几个无害的(`underline`/`strikethrough`), 也不要漏。
_TEXEX_FIELDS = ("text", "font_size", "bold", "color", "text_size", "halign", "valign",
                 "padding", "mipmap", "outline_width", "disabled", "font_name",
                 "underline", "strikethrough", "font_kerning")


def _texex_norm(v):
    """把属性值变成可哈希的。⚠️ 浮点**原样保留精度** —— 字号差 0.01 就是另一张纹理。"""
    if isinstance(v, (list, tuple)):
        return tuple(_texex_norm(x) for x in v)
    return v


def _texex_key(lbl):
    """一张文字纹理的完整指纹。

    ⚠️ `color` 必须在内: Kivy 把颜色烘进纹理(`_trigger_texture_update` 里
       `self._label.options['color'] = ...`), 换个色就是另一张图。
       ⚠️ 这也是 `_set_controls_enabled` 那 3 处 `.color` 赋值(每发球 6 次重建)
       **不用单独修就自动免费**的原因 —— 两种颜色各烘一次, 之后全命中。
    ⚠️ `text_size` 必须在内: `_mk_label` 把 `size` 绑到了它(7454), 布局一变它就变。
    """
    _out = []
    for _n in _TEXEX_FIELDS:
        try:
            _out.append(_texex_norm(getattr(lbl, _n)))
        except Exception:
            _out.append(None)
    return tuple(_out)


# ⚠️⚠️ **全局(跨标签)指纹缓存**(2026-09-15 加)。为什么必须有这一份 ——
#   结算大字/阴影是**每次中奖现新建的 Label**(`big_result_text` 里 `Label(...)`),
#   而 `_texex` 存在标签自己身上 ⇒ **每个新标签的缓存都是空的** ⇒
#   每局中奖都稳定产生 2 次重建(`结算大字` + `结算阴影`), 真机上各 5~10 毫秒,
#   落在同一帧 ⇒ 那一帧必然越过 11.11 毫秒(v0.7.32 真机: 帧2501/3653/5079 都是重建 2)。
#
#  ⚠️ **为什么全局共享在这里是安全的**(这一点必须说清, 因为按标签存本身是有理由的):
#     当初按标签存, 是因为"直接把 Kivy 给标签建的那张纹理交出去"会被**原地重画** ——
#     `CoreLabel.refresh()` 在宽高不变时会 `texture.ask_update(...)` 重画进**同一张纹理**,
#     于是 A 改文字会把 B 正在显示的图改掉。
#     但**现在不是那样**: `_texex_bake` 烘的是一个**专用的 CoreLabel, 它的 text/options
#     从此一个字都不动**(见 `_texex_bake` 的说明) ⇒ 那张纹理**再也不会被重画**,
#     多给几个标签用完全没问题。**前提是"永不改动"这条不破。**
_TEXEX_G = {}
_TEXEX_G_ORDER = []
_TEXEX_G_MAX = 256          # 全局上限: 一张纹理几十 KB, 256 张约十几 MB, 可接受


def _texex_key_of(lbl):
    """按标签当前属性算指纹(抽出来是因为全局缓存那条路也要用)。"""
    return _texex_key(lbl)


def _texex_get_g(key):
    return _TEXEX_G.get(key)


def _texex_put_g(key, cl):
    if key in _TEXEX_G:
        return
    _TEXEX_G[key] = cl
    _TEXEX_G_ORDER.append(key)
    while len(_TEXEX_G_ORDER) > _TEXEX_G_MAX:
        _TEXEX_G.pop(_TEXEX_G_ORDER.pop(0), None)


def _texex_get(lbl, key):
    _d = getattr(lbl, "_texex", None)
    return _d.get(key) if _d else None


def _texex_put(lbl, key, tex):
    _d = getattr(lbl, "_texex", None)
    if _d is None:
        _d = {}
        lbl._texex = _d
        lbl._texex_order = []
    if key in _d:
        return
    _d[key] = tex
    lbl._texex_order.append(key)
    while len(lbl._texex_order) > _TEXTEX_PER_LABEL:
        _d.pop(lbl._texex_order.pop(0), None)


def _texex_bake(lbl):
    """按 `lbl` **当前**的属性烘一个**专用的 `CoreLabel`**, 返回它。

    ⚠️ **这是整套缓存能成立的关键**: 返回的这个 CoreLabel 从此**再也不会被改**
       (`text`/`options` 一个字都不动) ⇒ Kivy 那次延迟光栅化只会发生一次,
       而且填的必然是它自己的内容 —— 不会被"另一个标签改了文字"顺手覆盖掉。
       直接缓存标签自己那张纹理就不行, 原因见 `_TEXEX_ON` 处那段证据。
    ⚠️ `Label` 与 `CoreLabel` 的属性名不完全一样:
       · **`disabled` 会把颜色换成 `disabled_color`**(Kivy 在 `_trigger_texture_update` 里就是这么干的)
         —— 不照做的话, 灰化状态会烘出**亮色**的纹理, 而且是"看着正常但颜色不对"那种错。
       · `outline_color` 同理。
    ⚠️ 属性**逐个 `getattr` 且缺失就跳过**, 不写死一份"以为一定有"的清单 ——
       Kivy 版本一变就会静默少传一个参数, 而少传的表现是"烘出来的字和显示的不一样", 极难查。
    """
    _o = {}
    for _n in ("text", "font_size", "font_name", "bold", "italic", "underline",
               "strikethrough", "font_family", "halign", "valign", "shorten",
               "mipmap", "line_height", "strip", "unicode_errors", "font_hinting",
               "font_kerning", "font_blended", "outline_width", "font_features",
               "font_context", "base_direction", "text_language",
               "limit_render_to_text_bbox", "padding"):
        try:
            _o[_n] = getattr(lbl, _n)
        except Exception:
            pass
    # 尺寸: `Label` 上叫 `text_size`(列表), 对应 `CoreLabel` 的构造参数同名。
    try:
        _ts = list(lbl.text_size)
        _o["text_size"] = _ts
    except Exception:
        pass
    # 颜色: **必须按 disabled 走 Kivy 那套映射**, 否则灰化态烘成亮色。
    try:
        _o["color"] = tuple(lbl.disabled_color if lbl.disabled else lbl.color)
    except Exception:
        pass
    try:
        _oc = lbl.disabled_outline_color if lbl.disabled else lbl.outline_color
        if _oc is not None:
            _o["outline_color"] = tuple(_oc)
    except Exception:
        pass
    _cl = CoreLabel(**_o)
    # ⚠️ **`refresh()` 绝不能漏**(2026-09-14 实修)。漏了它 `_cl.texture` 恒为 `None`,
    #    于是 `_texex_apply` 返回 False ⇒ 永远存不进缓存、每次都退回 Kivy 老路。
    #    症状特别阴: **画面完全正常**(退回去渲染了), 但命中率恒为 0、白拿一个 CoreLabel 的开销。
    #    第一版就是这么写的, 靠探针打印"缓存里有几项: 0"才抓到 —— 光看画面永远看不出来。
    _cl.refresh()
    # ⚠️⚠️ **2026-09-15: 必须在这里就把第二趟(真光栅化 + 纹理上传)做掉, 否则预热等于没烘。**
    #    桌面实测(`temp/texfill_force_probe.py`, 直接数 `LabelBase._texture_fill` 被调几次):
    #      · `CoreLabel.refresh()` 之后 —— 填纹 **+0**。它只量了宽高、挂了个回调;
    #      · 真正的光栅化要等**这张纹理第一次被绑上去画**才发生(Kivy 两趟渲染, 见 `_texfill_wrap`)。
    #    也就是说: **预热只是把那一笔 4~10.7 毫秒的账推迟到"这句话第一次真的出现在屏幕上"
    #    的那一帧**, 一个字都没省。
    #    真机铁证(v0.7.28, TB323FU, `plinko_fps_20260914_193357.txt`): 帧2032 / 帧3741 /
    #    帧5145 三帧 **`文字重建 = 0` 却各付了 3.6 / 3.1 / 4.4 毫秒填纹** —— 那不是新文字,
    #    就是某张预热好的纹理第一次被画出来。
    #    这同时解释了 v0.7.26 那次"延迟渲染"为什么实测是 0:
    #    **填纹跟着纹理走, 不跟着 `texture_update` 走** —— 把它从这一帧挪开, 它还是会在
    #    同一张纹理第一次上屏时冒出来, 只是换了个人付钱。
    #    ⚠️ 触发方式选 `bind()` 不选读 `.pixels`: 两者都能触发(探针实测各 +1), 但读
    #    `.pixels` 要把整张纹理拷进 Python 侧(45KB/项, 预热上百项就是几 MB), `bind()` 只是
    #    把它设为当前 GL 纹理 —— 而 Kivy 每次画 canvas 都会重设自己的 GL 状态, 不留副作用。
    #    ⚠️ 触发**只做一次**: 探针实测再 `bind()` 一次是 +0(回调已被摘掉)。
    try:
        _cl.texture.bind()
    except Exception:
        pass
    return _cl


def _texex_apply(lbl, cl):
    """把专用 CoreLabel 的纹理交给标签显示。**这是标签唯一的出图路径**(见 `texture_update`)。"""
    _t = cl.texture
    if _t is None:
        return False
    lbl.texture = _t
    lbl.texture_size = list(_t.size)
    return True


def _build_texwarm(rw, bet=None):
    """列出"启动期该提前烘好的 (标签, 文案, 颜色)"。

    ⚠️ 为什么值得单列一张表(2026-09-14): `texture_update` 那条缓存把**重复**变免费了
       (真机 4~10.7 毫秒/次), 但每句的**第一次**仍要付全价。而状态栏那十来句在一局里必然
       全都要出现 —— 不预热的话, 第一次发射/第一次落袋那几帧还是要各卡 4~10 毫秒。
       预热 = 把这些"必然出现"的首次挪到启动期(那时玩家还在看加载页)。
    ⚠️ **只列固定文案**。带数字的(`累计%d投%d中`、余额、`中奖! +%d (x%d)`)列不进来,
       它们的值域是无限的 —— 那部分只能靠缓存命中重复值, 见计划文件 1.3 的诚实估算。
    ⚠️ **颜色必须一起列**: Kivy 把颜色烘进纹理, 同一个字符串换个色就是另一张图。
       `_set_controls_enabled` 每发球都会改那三个标签的 `.color`, 那一块就靠这里预先烘好。
    ⚠️ `bet` = **当前投注档**。带数字的"中奖! +N (xM)"里 `payout = bet x m`,
       四个投注档全铺是 28 条(1.4 秒启动), 而**一局里投注档通常不变** ——
       所以只烘当前档那 7 条, 别的档第一次用到时走懒缓存(一次 4~7 毫秒, 之后命中)。
    """
    out = []
    if rw is None:
        return out
    # ---- 状态栏: 10 句固定文案(颜色恒定 COL_SUB, 只有 `_fit1` 可能改字号)----
    _st = getattr(rw, "status_lbl", None)
    if _st is not None:
        try:
            _c = tuple(_st.color)
        except Exception:
            _c = None
        for _t in ("按住蓄力发射", "已重置", "蓄力中", "力度不足,未扣弹珠", "发射!", "未中",
                   "即将入袋…", "弹跳中…", "入场中…", "性能测试中…", str(_st.text)):
            out.append((_st, _t, _c))
        # ---- 带数字的那两句: **取值域有限, 所以能枚举**(2026-09-14) ----
        # 真机 v0.7.26 的 `重建来源` 里 "状态栏 6 次" 全是这两句; 缓存吃不到(每次数字都变),
        # 只能靠**提前烘好所有可能的组合**。
        # ⚠️ 倍率与投注档**从代码里派生, 不许手抄一份** —— 抄的那份迟早和真值脱钩,
        #    而脱钩的表现是"预热白做、还不报错"(静默失败, 本工程最贵的一类坑)。
        #    倍率的真源是 `VALUE_SHAPE` 的键(再并上 x2, 它由 `_solve_p2` 单独给)。
        # ⚠️ 枚举不全**不会出错**: 没预到的组合第一次照旧走 Kivy 老路(4~7 毫秒), 之后命中。
        #    失败模式是"没赚到", 不是"画面坏了"。
        try:
            _mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
            for _m in _mults:
                out.append((_st, "命中 x%d · 结算中" % _m, _c))
            _my_bet = bet if bet in PRESETS else (PRESETS[0] if PRESETS else 1)
            for _m in _mults:
                out.append((_st, "中奖! +%d (x%d)" % (_my_bet * _m, _m), _c))
        except Exception:
            pass
    # ---- 音效按钮: 两句话 x 两种颜色(`_refresh_mute_btn` 里那两个)----
    _mb = getattr(rw, "mute_btn", None)
    if _mb is not None:
        try:
            _on = hex_rgb("#0e1524") + (1,)
            _off = hex_rgb("#c0c8e4") + (1,)
        except Exception:
            _on = _off = None
        for _t, _c in (("音效已开", _on), ("音效已关", _off)):
            out.append((_mb, _t, _c))
    # ---- 两个标题标签: **文字永不变**, 但 `.color` 每发球被改两次 ----
    #      见 `_set_controls_enabled` 8013-8014 / 8020 的两个取值。
    for _n in ("_rtp_title_lbl", "_bet_title_lbl"):
        _lb = getattr(rw, _n, None)
        if _lb is None:
            continue
        try:
            # ⚠️ 只烘**一个色**了(2026-09-14): 这两个标签的 `.color` 从此恒定, 亮/暗改走
            #    画布染色(`_set_lbl_tint`)⇒ 纹理只有一张, 不会再因为变色重建。
            out.append((_lb, str(_lb.text), tuple(_lb.color)))
        except Exception:
            pass
    # ---- 中奖大字/阴影: 每次中奖现建新标签, 只有全局缓存能救它, 而"第一次"仍要付全价 ----
    #      (2026-09-15 加; 收益实测见 `_bigtext_warm_items` 的说明)
    try:
        out.extend(_bigtext_warm_items(rw))
    except Exception:
        pass
    # ⚠️ **进度串不预烘**(2026-09-15 玩家定案): 进度只在**物理段/SOC 高压段**显示,
    #    那时屏幕采样**已经停了** ⇒ 写标签不进成绩, 没必要预烘。
    #    (曾给“渲染窗口的进度”预烘过, 随“渲染窗口不显示进度”一起撤了。)
    # ---- 飘字(`center_toast`): 同样是**每次现建 Label**, 同一个病, 同样只有全局缓存能救 ----
    try:
        out.extend(_toast_warm_items(rw))
    except Exception:
        pass
    return out


def _warm_one(item):
    """把 (标签, 文案, 颜色[, 字号]) 走一遍 —— 走的是 `Label.texture_update`, 于是自动进缓存。

    ⚠️ 走完**必须还原**。还原那一下也会触发一次重建, 但还原回去的正是标签原本那句,
       而它也在预热表里 ⇒ 那一次是**命中**, 不会再付一次全价。
    ⚠️ 这里**只调 `texture_update()`**, 不碰 `_orig`: 命中/未命中由包装器自己决定。
    ⚠️ 第 4 项 `font_size` 只给**中奖大字**那批用(见 `_bigtext_warm_items`)——
       那个标签是每次中奖现建的, 字号由 `fit_font_size` 现算, **必须一起设**,
       否则烘出来的是"模板那个字号"的纹理, 而缓存指纹里含 `font_size` ⇒ **一条都命中不了**。
    """
    lbl, text, color = item[0], item[1], item[2]
    _fs = item[3] if len(item) > 3 else None
    _save_t = lbl.text
    try:
        _save_c = tuple(lbl.color)
    except Exception:
        _save_c = None
    _save_f = None
    if _fs is not None:
        try:
            _save_f = float(lbl.font_size)
        except Exception:
            _save_f = None
    try:
        if color is not None:
            lbl.color = color
        if _fs is not None:
            lbl.font_size = float(_fs)
        lbl.text = text
        lbl.texture_update()
    finally:
        try:
            lbl.text = _save_t
            if _save_c is not None:
                lbl.color = _save_c
            if _save_f is not None:
                lbl.font_size = _save_f
        except Exception:
            pass


# ---- 中奖大字/阴影的**预热模板**(见 `_bigtext_warm_items`) ----------------------
# ⚠️⚠️ **必须打 `_texupd_tag`**, 否则整条预热是空转: `_texupd_wrap` 的入口判据是
#    `self._texupd_tag is not None and self.text`(`main.py` 里那句), 没打标的标签
#    **直接走 Kivy 老路、压根不进缓存** ⇒ 烘了等于没烘, 而且**不报错**。
# ⚠️ tag 本身**不进** `_texex_key` 的字段表, 所以一个模板就够(大字/阴影共用),
#    烘出来的指纹由 `text/font_size/color/...` 决定, 与真标签一致即可。
_BIGTEXT_TMPL = {}


def _warm_tmpl(key, factory):
    """按 `key` 缓存一个**预热模板标签**(已打 `_texupd_tag`, 见那段说明)。

    ⚠️ **每类标签要各自的模板**: `_texex_key` 含 `halign`/`valign`/`bold`/`font_name` 等,
       飘字的 Label 是 `halign="center"`, 拿大字的模板去烘 ⇒ 指纹对不上 ⇒ **一条都命中不了**,
       而且是**静默**的(画面对, 只是没赚到)。所以工厂由调用方给。
    """
    lb = _BIGTEXT_TMPL.get(key)
    if lb is None:
        try:
            lb = factory()
            _tag_texupd(lb, key)
            _BIGTEXT_TMPL[key] = lb
        except Exception:
            lb = None
    return lb


def _bigtext_tmpl():
    return _warm_tmpl("结算大字",
                      lambda: Label(text="", bold=True, size_hint=(None, None)))


def _toast_tmpl():
    # ⚠️ 与 `center_toast` 里的建法**逐字一致**: `bold=True, halign="center", size_hint=(None,None)`
    return _warm_tmpl("飘字",
                      lambda: Label(text="", bold=True, halign="center",
                                    size_hint=(None, None)))


def _toast_warm_items(rw):
    """列出**飘字**(`center_toast`)**能枚举**的那几条, 让它的第一次也是缓存命中。

    ⚠️ 与中奖大字**同一个病**: `center_toast` 里是 `lbl = Label(...)` —— **每次现建新标签**
       ⇒ `_texex`(按标签存)永远是空的, 只有 `_TEXEX_G`(全局)能救; 而"每种文案第一次出现"
       仍要付一次真光栅化(真机 8~11 毫秒), 那一帧必卡。
    ⚠️ **只列固定文案**: `重放失败：%s` 那条带异常文本, **枚举不了**, 不列(它第一次照旧走老路)。
    ⚠️ 文案/颜色/字号**全部从 `center_toast` 的调用点抄下来**(`main.py` 里那 5 处) ——
       这几条不是从代码派生的常量, 是**散在调用点上的字面量**; 改了调用点这里不会自动跟,
       所以**每一条都在下面标了出处行号**, 将来对不上时一眼能查。
    ⚠️ 字号必须和 `center_toast` 里那句 `fit_font_size(text, sp(size), seen, True)` **同口径**
       (`seen = max(80, GameArea.width * 0.94)`), 否则指纹里的 `font_size` 对不上。
    """
    out = []
    _lb = _toast_tmpl()
    if _lb is None or rw is None:
        return out
    try:
        _ga = getattr(rw, "game_area", None)
        _seen = max(80.0, float(getattr(_ga, "width", 0.0) or 540.0) * 0.94)
        _nl = chr(10)          # 有一条文案自带换行, 见下面第三条
        _tasks = [
            ("先等这一发落定", COL_FIRE, 26),
            ("重放失败：找不到挂载点", COL_FIRE, 26),
            ("弹珠数量不足" + _nl + "请重置或降低投入", COL_FIRE, 26),
            ("弹珠数量已调整到1000个", COL_GREEN, 28),
        ]
        for _v in (20, 50, 100):                    # 轮次档位, 与 `_set_max_plays` 的选项一致
            _tasks.append(("每轮已设定为%d次" % _v, COL_GREEN, 20))
        # ⚠⚠ 去重键**必须含文案**: 缓存指纹里有 `text`,
        #    文案不同就是**不同条目**。只按 (字号, 颜色) 去重会把
        #   「先等这一发落定」「重放失败：找不到挂载点」「弹珠数量不足…」
        #   （全是 COL_FIRE + 同一个字号）合并成一条 ⇒ 只烘第一条,
        #   其余**照旧冷开且不报错**。实测 7 条只烘进 3 条。
        _seen_fs = set()
        for _t, _c, _sz in _tasks:
            _fs = fit_font_size(_t, sp(_sz), _seen, True)
            if (_t, _fs, _c) in _seen_fs:
                continue
            _seen_fs.add((_t, _fs, _c))
            # ⚠️ `halign="center"` 是 `center_toast` 里的建法, 模板已经带上; 这里只给字/色/号。
            out.append((_lb, _t, hex_rgb(_c) + (1,), _fs))
    except Exception:
        pass
    return out


def _bigtext_warm_items(rw):
    """列出**中奖大字/阴影**该预烘的那些整串, 让"第一次中奖"也是缓存命中。

    ⚠️ 为什么值得单列(2026-09-15, 真机 6 份日志 + 玩家自己在两个版本上复现):
       中奖大字/阴影是**每次中奖现新建的 Label**, 而 `_texex` 存在标签自己身上 ⇒
       每个新标签的缓存都是空的。`_TEXEX_G`(全局缓存)能救它, 但**每种 (文案, 颜色)
       第一次出现时仍要付一次真光栅化** —— 真机实测那一帧的 `填纹` 是 **8~11 毫秒**,
       而 165Hz 一帧的预算只有 6.06 毫秒 ⇒ 那一帧必卡。
       冷/热两批日志的对比(**同版本 v0.7.44/45、同设备、同配比、中奖演出都是 4 次**):
         冷(第1轮) 1%Low 91.8 / 98.6 · 卡顿帧 8 / 7 · 装杯>8.1ms 105 / 117
         热(后两轮) 1%Low 114.8 / 112.6 / 116.5 / 107.6 · 卡顿帧 2/5/0/4 · 装杯>8.1ms 26/38/98/266
       —— 差 **+18%**。而 `重建来源` 从「余额8·统计4·**大字4·阴影4**」变成「只剩 余额8·统计4」,
       说明热的那几轮**全部命中**。把"第一次"挪到启动期, 就是白拿这一跳。
    ⚠️ 枚举范围**从代码派生**(`VALUE_SHAPE` / `PRESETS` / `slot_color`), 不许手抄一份 ——
       抄的那份迟早和真值脱钩, 而脱钩的表现是"预热白做、还不报错"。
    ⚠️ 枚举不全**不会出错**: 没预到的组合第一次照旧走 Kivy 老路, 之后命中。
       失败模式是"没赚到", 不是"画面坏了"。
    ⚠️ 摊子: 当前投注档 7 个赔付 + 未中 = **8 条文案 x 2 个标签 = 16 次**, 每次约 5 毫秒
       ⇒ **约 80 毫秒**, 分摊在预热链里(玩家那时还在看加载页)。
    ⚠️ **字号必须和 `big_result_text` 用同一句算**(同一个 `avail`、同一个 `base`)——
       指纹里含 `font_size`, 差一点就是一条都命中不了。
    """
    out = []
    _lb = _bigtext_tmpl()
    if _lb is None or rw is None:
        return out
    try:
        _ga = getattr(rw, "game_area", None)
        _avail = max(80.0, float(getattr(_ga, "width", 0.0) or 540.0) * 0.94)
        _bet = getattr(rw, "bet", DEFAULT_BET)
        if _bet not in PRESETS:
            _bet = PRESETS[0] if PRESETS else 1
        _mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
        _tasks = [(0, "未中", COL_FIRE, sp(36))]          # m=0 那条, 与 `big_result_text` 一致
        for _m in _mults:
            _tasks.append((_m, "+%d" % (_bet * _m), slot_color(_m), sp(48)))
        for _m, _txt, _col, _base in _tasks:
            _sz = fit_font_size(_txt, _base, _avail, True)
            _c = hex_rgb(_col) + (1,)
            out.append((_lb, _txt, _c, _sz))               # 大字
            out.append((_lb, _txt, (0, 0, 0, 0.6), _sz))   # 黑影(同字同号, 只差颜色)
    except Exception:
        pass
    return out


def _texupd_wrap():
    """给 `Label.texture_update` 挂计数器 + **一层按标签自己的文字纹理缓存**。

    ⚠️ 一次文字重排 = 重测字形 + 重光栅化 + 重建纹理 + 上传, 真机字形表更贵。它由 Kivy 用
       Clock **延后**执行, 跑在 `_frame` 外面。去掉余额滚动(0.6.79)就是冲它去的,
       这一格是**验证那一步到底有没有生效**的判据。

    ⚠️⚠️ **2026-09-14 加的缓存**(v0.7.25)。起因是真机日志把 1%Low 的账算清了:
       44 个慢帧里有 16 帧的最大一笔是 `填纹`(`CoreLabel._texture_fill`, 第二趟真光栅化)
       **4.1~10.7 毫秒**;把这笔全拿掉 1%Low 从 81.1 → 95.2。
       机制见 `_texfill_wrap` 的说明 —— Kivy 的文字**两趟画**, 第二趟才是真光栅化,
       而它由 Texture 回调在"纹理下次被用到时"触发。
       **命中缓存时直接换纹理 ⇒ 第一趟、第二趟、纹理上传三样全跳过。**

    ⚠️ **为什么缓存按标签自己存, 不做全局共享**(这是本设计最关键的一条):
       Kivy 的 `CoreLabel.refresh()` 里有一支是
       `if texture is None or 宽高变了: 新建 Texture else: texture.ask_update(self._texture_fill)`
       —— **尺寸没变时它会"原地重画进同一张纹理"**。
       所以如果把 A 标签烘出来的纹理交给 B 标签用, 那么 A 下次改成同尺寸的另一段文字时,
       会**把 B 正在显示的那张纹理原地改掉** —— B 的字会悄悄变成 A 的, 而且只在同尺寸时发生。
       按标签存就不存在这张跨标签共享, 一点风险都没有。

    ⚠️ **另一条必须做的事:未命中时先把"马上要被重画的那张"从缓存里摘掉**。
       同理, `_orig` 跑起来可能对 `self.texture` 原地重画 ⇒ 缓存里指向它的那项必须作废,
       否则下一次"命中"会显示成上一段文字。见 `_texex_forget`。

    ⚠️ **只对打了 `_texupd_tag` 的标签生效** —— 普通按钮/弹窗/档位按钮一律走原路,
       把影响面压到最小(实测那些重建基本都在这几个标签上)。
    """
    try:
        from kivy.uix.label import Label as _L
    except Exception:
        return
    _orig = getattr(_L, "texture_update", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    # ⚠️ 这些**必须定义在模块级**(下面 `_texex_*` 用裸名引用), 见 `_TEXUPD` 处的教训。
    def texture_update(self, *a, **k):
        # ⚠️ 2026-09-15: 这里**同时给子步骤计时**。真机 v0.7.20 的数据显示: 每发球稳定产生
        #    4 个慢帧(周期 252/89/21/423 帧, 签名固定 = 板面/重掷/字号/发射), 每帧主线程烧
        #    13 毫秒而"板面/重掷/字号/装杯/发射"五个已计时的加起来只有 **2 毫秒** ——
        #    剩下 11 毫秒**不在我们任何一处已计时的代码里**, 而这几帧**每帧正好 3 次重建**。
        #    把重排接进 `_brk_add` 之后, 下一次跑那 11 毫秒会自己报名字, 不用再推。
        _key = None
        if _TEXEX_ON and getattr(self, "_texupd_tag", None) is not None and self.text:
            # 空文字不进缓存: Kivy 那条路会把 `texture` 置 None、`texture_size` 置 (0,0),
            # 而 `CoreLabel.refresh()` 空文字给的是一张 1x1 的占位图 —— 两者语义不同, 混了会出鬼。
            try:
                _key = _texex_key(self)
                _cl = _texex_get(self, _key)
                if _cl is None:
                    # ⚠️ 本标签没烘过 ⇒ 查**全局**那一份(见 `_TEXEX_G` 的说明):
                    #    结算大字/阴影每次中奖都是新标签, 靠这一条才能命中第二次之后的所有局。
                    _cl = _texex_get_g(_key)
            except Exception:
                _key, _cl = None, None
            if _cl is not None:
                # 命中: 第一趟(`refresh`)、第二趟(`_texture_fill` 真光栅化)、纹理上传**三样全跳过**。
                # 这就是 4~10.7 毫秒的来源(真机 v0.7.24 实测), 命中的意义全在这一句。
                if _TEXUPD_ACTIVE[0]:
                    _TEXEX_HIT[0] += 1
                if _texex_apply(self, _cl):
                    return
                _key = None          # 纹理没了(被回收/上下文丢失) ⇒ 退回老路, 绝不让画面空着
            else:
                # ⚠️ 未命中时**不让标签自己渲染**, 而是烘一个专用的 CoreLabel 并把它的纹理交给标签。
                #    这样标签显示的东西 100% 来自那张稳定的纹理 —— 不存在"标签自己那张纹理
                #    被后来改文字顺手覆盖"的问题(那正是第一版翻车的地方, 见 `_TEXEX_ON` 处的证据)。
                #    代价: 未命中时多一个 CoreLabel 对象; 烘的工时和原来一模一样, **没有回归**。
                try:
                    _cl = _texex_bake(self)
                    if _TEXUPD_ACTIVE[0]:
                        _TEXUPD[0] += 1
                        _TEXUPD_BY[getattr(self, "_texupd_tag", "其他文字")] = \
                            _TEXUPD_BY.get(getattr(self, "_texupd_tag", "其他文字"), 0) + 1
                        _TEXEX_MISS[0] += 1
                    if _texex_apply(self, _cl):
                        _texex_put(self, _key, _cl)
                        _texex_put_g(_key, _cl)      # 两边都存: 下一个新标签才接得上
                        return
                except Exception:
                    _key = None      # 烘不出来就退回 Kivy 老路, 绝不抛
        _t0 = time.perf_counter()
        try:
            if _TEXUPD_ACTIVE[0]:
                _TEXUPD[0] += 1
                _tag = getattr(self, "_texupd_tag", "其他文字")
                _TEXUPD_BY[_tag] = _TEXUPD_BY.get(_tag, 0) + 1
            _orig(self, *a, **k)
        finally:
            # ⚠️ 只在跑分采样期记 —— 平时 `_FRAME_BRK` 没人读, 记了也是白记。
            if _TEXUPD_ACTIVE[0]:
                _brk_add("文字", _t0)
    texture_update._probe_wrapped = True
    texture_update.__name__ = "texture_update"
    _L.texture_update = texture_update


_texupd_wrap()


def _texfill_wrap():
    """把**第二趟**文字渲染(`CoreLabel._texture_fill`)也接进子步骤计时。

    ⚠️ 为什么必须有这一格(2026-09-14, 查了 kivy/core/text/__init__.py 源码才明白):
       Kivy 的文字是**两趟**画的 ——
         `refresh()` 里 `render()`                     <- 第一趟: **只量宽高**(不是真画)
         `Texture.create(callback=...)` 或 `texture.ask_update(...)`
         `_texture_fill()` -> `render(real=True)`      <- 第二趟: **真光栅化**
       第二趟是**纹理下一次被用到时**由 Texture 回调触发的, 也就是**跑在
       `Label.texture_update` 外面**(Kivy 的渲染/上传路径上)。而 `_texupd_wrap` 只包了
       `texture_update` ⇒ **我们一直在量第一趟, 真正贵的那一趟没人认领。**
       真机证据(165Hz TB323FU): 全窗口**只有 13 帧**低于 90fps, 它们 **13/13 主线程都在真算**
       (没有一帧是"在等")、13/13 都伴随文字重建; 而那些帧 `主线程 17.1 毫秒` 而 `_frame` 自算
       只有 `4.3 毫秒` —— **中间 11 毫秒不在 `_frame` 里, 也不在 texture_update 里**。
       (⚠️ 只有在 `_frame` 与 flip **同频**时才敢相减: 165Hz 下 Clock 间隔 1/165 ≈ flip 周期,
       一次 flip 里 `_frame` 只跑一次; 120Hz 下它跑两次, 那边相减不成立。)
    ⚠️ 包装函数**必须保住 `__name__`**: Kivy 有按方法名找的地方, 改名会静默失联。
    ⚠️ 和其它埋点一样只在采样期计时 —— 这一趟跑的频率比 `texture_update` 还高。
    """
    try:
        from kivy.core.text import LabelBase as _LB
    except Exception:
        return
    _orig = getattr(_LB, "_texture_fill", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def _texture_fill(self, texture, *a, **k):
        if not _TEXUPD_ACTIVE[0]:
            return _orig(self, texture, *a, **k)
        _t0 = time.perf_counter()
        try:
            return _orig(self, texture, *a, **k)
        finally:
            _brk_add("填纹", _t0)

    _texture_fill._probe_wrapped = True
    try:
        _texture_fill.__name__ = "_texture_fill"
        _LB._texture_fill = _texture_fill
    except Exception:
        pass


_texfill_wrap()


def _swap_wrap():
    """把 `Window.flip()`(真正 swap 那一步)包起来计时, 写进 `_FRAME_SWAP`。

    ⚠️ **包的是 `type(Window).flip`, 不是 `Window.flip`** —— 后者是绑定方法, 改不动类。
       真机上 `type(Window)` 是 `WindowSDL`, 它自己的 `flip` 先调 `self._win.flip()`(SDL 的
       `SDL_GL_SwapWindow`, 就是会阻塞等缓冲 / 等 vsync 的那一句), 再 `super().flip()`。
       所以这一包正好罩住"等屏幕"那一段, **不含渲染**(渲染在 `on_draw` 里, 更早)。
    ⚠️ **只在跑分采样期真的计时**(`_TEXUPD_ACTIVE` 是现成的"正在采样"闸门)—— 平时每帧两次
       `perf_counter` 虽只有百纳秒级, 但这个工程的规矩是"别让埋点改变被测量的东西"。
    ⚠️ 必须 try/except 兜住: `self._win` 在无窗口 / 自测路径上可能是 `None`。埋点绝不能让主循环抛。
    ⚠️ **别改成包 `Window.flip` 这个名字**: Kivy 的 `Window` 是单例, `Window.flip` 拿到的是
       绑定方法, 赋值上去只会给实例加一个属性, 而真正被调的是 `WindowBase.on_flip` 里的
       `self.flip()` —— 那个走的是类, 撞不到实例属性上。桌面上实测确认过包装生效(见 temp/fliporder.py)。
    """
    _cls = type(Window)
    _orig = getattr(_cls, "flip", None)
    if _orig is None or getattr(_orig, "_probe_wrapped", False):
        return

    def flip(self, *a, **k):
        if not _TEXUPD_ACTIVE[0]:
            return _orig(self, *a, **k)
        # ⚠️ **本帧的「尾巴」**: 从 `_frame` 算完到真正交画面之间那一段(渲染 + 其余 Clock 回调)。
        #    2026-09-15 加。起因是三份真机日志里都有一个同形状的怪帧(待机 -> 蓄力那一拍,
        #    25~49 毫秒, 主线程 15~47 毫秒, 而 `_frame` 自算只有 0.03~0.25 毫秒、子步骤栏是空的):
        #      v0.7.28 帧16 28.73 · v0.7.29 帧1184 48.74 · v0.7.30 帧1237 25.26
        #    「主线程 − 自算」能算出"不在 `_frame` 里", 但分不出是渲染还是别的回调。
        #    这一格就是那把刀: 它记的是**墙钟**(含等 GPU/驱动), 而 `主线程` 是 CPU 时间 ——
        #    两者一起读就能分流: 尾大而 CPU 小 = 卡在渲染/驱动; 两个都大 = 有回调在烧 CPU。
        #    ⚠️ 必须在 `_orig` **之前**记 —— `_on_flip` 是在 `_orig` 里面被派发的,
        #       它读完 `_FRAME_BRK` 就清空; 记晚了这一帧就白记。
        if _FRAME_END[0] > 0.0:
            # 2026-09-15: 老实现是 `_brk_add("尾", _FRAME_END[0])` 一格记完整段墙钟残差。
            # 现在按上面的 `_clock_wrap()` 切两刀(第三段 flip 自身在 `_FRAME_SWAP` 里):
            #   `尾·回调` = `_frame` 跑完 → Clock 本轮跑完;  `尾·空档` = Clock 跑完 → 进 flip。
            # 两段之和 == 老那一格的总和, 语义不丢; 拿不到 Clock 标记就原样退回。
            _ce = _CLOCK_END[0]
            if _ce > _FRAME_END[0]:
                _FRAME_BRK["尾·回调"] = (_FRAME_BRK.get("尾·回调", 0.0)
                                        + (_ce - _FRAME_END[0]))
                _FRAME_BRK["尾·空档"] = (_FRAME_BRK.get("尾·空档", 0.0)
                                        + (time.perf_counter() - _ce))
            else:
                _brk_add("尾", _FRAME_END[0])
            _FRAME_END[0] = 0.0
        _t0 = time.perf_counter()
        try:
            return _orig(self, *a, **k)
        finally:
            _FRAME_SWAP[0] = (time.perf_counter() - _t0) * 1000.0

    flip._probe_wrapped = True
    try:
        _cls.flip = flip
    except Exception:
        pass


def _clock_wrap():
    """给 `Clock.tick` 的**结束**盖一个时间戳 —— 「尾」三分段的中间那一刀。

    为什么需要它(2026-09-15, 对抗评审第 2 批): 老的「尾」= `_frame` 跑完 → 进 `flip` 之间的
    **整段墙钟残差**, 是一个 lump。而对抗评审里两位专家对"参考带帧多出来的 2~3ms 去哪了"
    吵了一整轮 —— 就是因为这段里没有细分:
      · `尾·回调` = `_frame` 跑完 → Kivy Clock 本轮其余事件跑完(还有别的 `schedule_interval`)
      · `尾·空档` = Clock 跑完 → 真正进 `flip`(事件循环/驱动的空当)
      · 再加已有的 `_FRAME_SWAP`(flip 自身耗时) ⇒ 一共三段, 不再是一个 lump。
    ⚠️ 必须在 `_orig` **之后**记(`finally`), 而且只在采样期记(`_TEXUPD_ACTIVE`)。
    ⚠️ 拿不到这一刀时, `flip` 那边会**退回原来那一格「尾」** —— 绝不因为少一个标记就把账丢了。
    ⚠️ 报「尾·回调 / 尾·空档」两个键, 而 `_bench_frames` 是按"取最大的两个子步骤"记的,
       所以旧日志里的「尾」在读数上自然被这两格取代。
    """
    try:
        from kivy.clock import Clock as _KC
        _ccls = type(_KC)
        _corig = getattr(_ccls, "tick", None)
        if _corig is None or getattr(_corig, "_probe_wrapped", False):
            return

        def tick(self, *a, **k):
            try:
                return _corig(self, *a, **k)
            finally:
                if _TEXUPD_ACTIVE[0]:
                    _CLOCK_END[0] = time.perf_counter()

        tick._probe_wrapped = True
        try:
            _ccls.tick = tick
        except Exception:
            pass
    except Exception:
        pass


_clock_wrap()
_swap_wrap()


# ---- CPU 频率(跑分期的调频状态) ----------------------------------------------
# ⚠️ 为什么必须有这一格(2026-09-14, 玩家在跑分时用第三方工具看到的):
#    **跑分前期整个采样窗口里 CPU 只跑 1.1GHz, 到后期纯 CPU 的物理跑分才升到 4.5GHz。**
#    这不是小数字 —— 同一个 SoC 上 1.1G 对 4.5G 是**4 倍**, 而 `主线程ms` 量的是
#    `time.thread_time()` = **真实的 CPU 秒数**(不是指令数), 主频越低、同一个函数量出来
#    就越"贵"。它能一口气解释掉三件本来互不相干的事:
#      · 慢帧的**绝对**时长在 60/120/165 三档下几乎不变(11.5~12ms)—— 固定工作量在低主频下
#        就是个固定的墙钟数, 与刷新周期无关;
#      · TB323FU 那台 165Hz 有两段 0.8 秒掉到 136~152fps, 而同 SoC 的 120Hz 机全程纹丝不动;
#      · 两台同 SoC 的机器每帧 CPU 能差 20%。
#    ⚠️ **采样必须放工作线程**, 不能塞进 `_on_flip`: 读 sysfs 每次几十微秒, 而这一格恰恰是
#       用来解释 `主线程ms` 的 —— 埋点自己抬高被解释的那个数就自相矛盾了。
#    ⚠️ 频率拿不到(桌面 / 被 SELinux 挡住)时一律吞掉当"读不到", 日志里那一段**不印**,
#       而不是印一行 0 假装量到了。
_CPUFRQ = {"freqs": [], "cap": 0.0, "stop": True, "thr": None}


def _cpufreq_mhz():
    """当前 CPU 频率(MHz): 取各核**最大值**(调度会把跑得最多的核拉到最高)。

    ⚠️ 逐核试到连续两核读不到就停 —— 真机核数不一(4/8), 没必要每次开 8 个文件。
    """
    best = 0
    miss = 0
    for i in range(8):
        try:
            with open("/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq" % i) as fh:
                v = int(fh.read().strip() or 0)
            if v > best:
                best = v
            miss = 0
        except Exception:
            miss += 1
            if miss >= 2:
                break
    return best / 1000.0


def _cpufreq_cap_mhz():
    """这台机器的 CPU 最高频(MHz)。读不到返回 0。"""
    for _p in ("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq",
               "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_max_freq"):
        try:
            with open(_p) as fh:
                v = int(fh.read().strip() or 0)
            if v > 0:
                return v / 1000.0
        except Exception:
            pass
    return 0.0


def _cpufreq_worker():
    """后台每 0.5 秒采一次主频, 直到 `stop`。开销 ≈ 每 0.5 秒两次小文件读。"""
    while not _CPUFRQ["stop"]:
        _v = _cpufreq_mhz()
        if _v > 0:
            _CPUFRQ["freqs"].append(_v)
        time.sleep(0.5)


def _cpufreq_start():
    _CPUFRQ["freqs"] = []
    _CPUFRQ["cap"] = _cpufreq_cap_mhz()
    _CPUFRQ["stop"] = False
    try:
        _CPUFRQ["thr"] = threading.Thread(target=_cpufreq_worker, daemon=True)
        _CPUFRQ["thr"].start()
    except Exception:
        _CPUFRQ["thr"] = None
        _CPUFRQ["stop"] = True
    # 起步立刻采一次: 窗口只有 25 秒、0.5 秒一次, 早采一点才看得出"前期就低"。
    _v = _cpufreq_mhz()
    if _v > 0:
        _CPUFRQ["freqs"].append(_v)


def _cpufreq_stop():
    _CPUFRQ["stop"] = True
    _CPUFRQ["thr"] = None


# ---- 高刷新率呈现策略 -----------------------------------------------------
# `maxfps` 只是 Kivy Clock 的调度上限，不能要求 Android 切换显示模式；反过来，仅请求
# Android 的高刷模式也无法解除 Kivy 自己的 60fps 睡眠。因此两层必须同时设置。
# 物理仍用 FIXED_DT=1/60：这是模拟精度，不是呈现上限；高刷新率下也由累加器决定推进。
FPS_CAP_OPTIONS = (60, 90, 120, 144, 165, 185)
FPS_CAP_DEFAULT = 120
FPS_CAP_MAX = max(FPS_CAP_OPTIONS)
FPS_CAP_FALLBACK = FPS_CAP_DEFAULT
FPS_CAP_PC = 120
_FPS_USER_CAP = [FPS_CAP_DEFAULT]
_FPS_INFO = [0.0, 0.0, 0.0]  # [当前屏幕Hz, 实际目标上限, Android请求的模式Hz]


def _fps_user_cap():
    """当前用户选择的上限；异常值永远回到 120，避免坏存档把循环设成 0。"""
    try:
        cap = int(_FPS_USER_CAP[0])
        return cap if cap in FPS_CAP_OPTIONS else FPS_CAP_DEFAULT
    except Exception:
        return FPS_CAP_DEFAULT


def _android_system_fps_cap(activity):
    """读取 Android 用户/系统设置的峰值刷新率上限；读不到返回 0 表示未知。"""
    try:
        from jnius import autoclass
        settings = autoclass("android.provider.Settings$System")
        key = settings.PEAK_REFRESH_RATE
        cap = float(settings.getFloat(activity.getContentResolver(), key, 0.0))
        return cap if cap > 1.0 else 0.0
    except Exception:
        return 0.0


def _screen_hz():
    """当前屏幕刷新率(Hz)。拿不到返回 None。

    ⚠️ `getRefreshRate()` 给的是**当前模式**的刷新率, 会随智能刷新率/外接屏变 ——
       所以切回前台时会再算一次(见 `_apply_fps_cap` 的调用点)。
    """
    if platform != "android":
        return None
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        disp = act.getWindowManager().getDefaultDisplay()
        try:
            return float(disp.getMode().getRefreshRate())     # API 23+
        except Exception:
            return float(disp.getRefreshRate())
    except Exception:
        return None


def _request_android_high_hz():
    """按“屏幕支持、Android 系统上限、用户档位”三者最小值请求显示模式。

    `Window.setFrameRate(target)`（API 30+）告诉系统此窗口的期望节拍；
    `preferredDisplayModeId`（API 23+）则兼容旧系统，并在自适应刷新率机器上给出明确的
    高刷模式偏好。两者都是系统可拒绝的请求：省电模式、温控或用户强制 60Hz 时不能绕过系统。
    """
    if platform != "android":
        return (0.0, float(min(FPS_CAP_PC, _fps_user_cap())))
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        window = activity.getWindow()
        display = window.getWindowManager().getDefaultDisplay()
        current = display.getMode()
        cw, ch = int(current.getPhysicalWidth()), int(current.getPhysicalHeight())
        modes = list(display.getSupportedModes())
        same_size = [m for m in modes
                     if int(m.getPhysicalWidth()) == cw and int(m.getPhysicalHeight()) == ch]
        candidates = same_size or modes

        def hz(mode):
            return float(mode.getRefreshRate())

        screen_cap = max((hz(m) for m in candidates), default=0.0)
        system_cap = _android_system_fps_cap(activity)
        # 读不到系统设置时不能凭空把它当 60Hz；让系统最终拒绝/降档，并由“屏幕 Hz”回读真值。
        if system_cap <= 0.0:
            system_cap = screen_cap
        target = min(screen_cap, system_cap, float(_fps_user_cap()))
        at_or_below = [m for m in candidates if hz(m) <= target + 0.5]
        # 优先不超过三者共同上限的最高同分辨率模式；没有精确档位时宁可保守降档，
        # 不绕过 Android 系统的峰值刷新率设定。
        chosen = (max(at_or_below, key=hz) if at_or_below else
                  min(candidates, key=hz) if candidates else None)
        mode_id = int(chosen.getModeId()) if chosen is not None else 0
        mode_hz = hz(chosen) if chosen is not None else 0.0
        target = min(target, mode_hz) if mode_hz > 0.0 else target
        sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)

        from android.runnable import run_on_ui_thread

        @run_on_ui_thread
        def apply_request(win, requested_mode_id, requested_hz, api_level):
            # Window API 从 Android 11 起可用；即使没有列出 mode，也保留高刷窗口请求。
            if api_level >= 30:
                try:
                    win.setFrameRate(float(requested_hz))
                except Exception:
                    pass
            if requested_mode_id:
                try:
                    attrs = win.getAttributes()
                    attrs.preferredDisplayModeId = requested_mode_id
                    win.setAttributes(attrs)
                except Exception:
                    pass

        apply_request(window, mode_id, mode_hz, sdk)
        return (mode_hz, target)
    except Exception:
        return (0.0, float(_fps_user_cap()))


def _refresh_screen_hz(*_):
    """显示模式请求异步生效后，刷新诊断面板中的实际 Hz。"""
    hz = _screen_hz()
    if hz:
        _FPS_INFO[0] = float(hz)


def _apply_fps_cap():
    """重申实际帧率上限 = min(屏幕支持, Android系统上限, 用户设定)。"""
    hz = _screen_hz()               # 只用于面板显示
    requested_hz, cap = _request_android_high_hz()
    try:
        cap = float(cap)
    except Exception:
        cap = float(FPS_CAP_FALLBACK)
    _FPS_INFO[0] = float(hz or 0.0)
    _FPS_INFO[1] = cap
    _FPS_INFO[2] = requested_hz
    try:
        Config.set("graphics", "maxfps", str(int(cap)))
        # 窗口创建前已设过；这里保留运行期状态供诊断，并防止配置被其他代码改回去。
        Config.set("graphics", "vsync", "1")
    except Exception:
        pass
    try:
        from kivy.clock import Clock
        Clock._max_fps = cap          # 真正生效的那个(见本段顶部说明)
    except Exception:
        pass
    if platform == "android":
        try:
            Clock.schedule_once(_refresh_screen_hz, 0.6)
        except Exception:
            pass
    return cap


def _cpu_split():
    """读 `/proc/self/stat` 的 utime(14)/stime(15), 返回 (用户态秒, 内核态秒) 或 None。

    ⚠️ **只适合整窗口统计, 不能逐帧**: 安卓/Linux 的 USER_HZ 是 100 ⇒ 一个 tick = 10 毫秒,
       而一帧只有 12 毫秒 —— 逐帧读等于全是量化台阶。整窗口的占比足够把候选池切两半:
         内核态占比高 ⇒ JNI/Binder/logd socket 写/文件写回 这一族;
         用户态占比高 ⇒ 纯 Python 的 CPU 竞争(GIL)这一族。
    ⚠️ 解析必须**从最后一个 ')' 之后切**: 进程名那段带括号且可能含空格。
    """
    try:
        with open("/proc/self/stat", "r") as f:
            raw = f.read()
        rest = raw[raw.rfind(")") + 2:].split()
        hz = 100.0
        try:
            import os as _os
            hz = float(_os.sysconf("SC_CLK_TCK")) or 100.0
        except Exception:
            pass
        return (int(rest[11]) / hz, int(rest[12]) / hz)     # 14-3=11, 15-3=12
    except Exception:
        return None


def _brk_add(tag, t0):
    """记一笔子步骤耗时(秒)。**只在跑分采样期有用**, 平时只是一次字典读改写。"""
    d = _FRAME_BRK.get(tag)
    _FRAME_BRK[tag] = (d or 0.0) + (time.perf_counter() - t0)


def _brk_wrap(cls, attr, tag):
    """给一个方法包一层计时。⚠️ 包装函数的 `__name__` 必须**与原方法同名** ——
    Kivy 的 WeakMethod 存的是 `__name__`, 名字对不上会在下次调度时 AttributeError
    (本工程的探针踩过一次)。"""
    orig = getattr(cls, attr, None)
    if orig is None:
        return
    def f(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            _brk_add(tag, t0)
    f.__name__ = attr
    setattr(cls, attr, f)
# 发声耗时统计: [累计次数, 累计秒, 单次最慢秒, 最慢那一次的音效名]。
# ⚠️ 它是**全程**的、不是逐帧的 —— 因为发声已经搬到工作线程上了(见 Sfx._drain),
#    后端耗时不再属于某一帧。真机实测这个数大得离谱: 15 秒窗口里 **50 次调用共 2674 毫秒**
#    (平均每次 53.5ms, 单次最慢 143.6ms, 后端 SoundPool), 而帧间隔才 12.5ms ⇒
#    **主线程每响一声就被卡几十毫秒**。记下最慢那一次的名字, 万一元凶是某个特定音效。
_SND_STAT = [0.0, 0.0, "", 0.0]    # [累计秒, 单次最慢秒, 最慢的名字, 超过阈值的次数]
# 发声"慢调用"的判据(毫秒)。20ms 已经远超一帧的余量(帧间隔 12.5ms), 落在这儿就一定有问题。
# 它的用途是**区分两种成因**: "每一次都慢"(说明是这条路的固定开销/HAL 唤醒)
# vs "少数几次很慢"(说明是争议资源 —— 声道抢用/锁竞争)。
SND_SLOW_MS = 20.0
# "慢帧"的判定门槛(毫秒)。只此一处 —— 判据与面板文案都读它, 免得两处各写一个数漂掉。
BENCH_SLOW_MS = 90.0

_VARIANT_FAMILIES = ("peg", "wall", "div", "top")

def _throttle_key(name):
    """节流键: 同一族的随机变体**共用一个闸门**(peg0..5 / wall0..1 / div0..1 / top0..1)。

    ⚠️ 不能按变体名各自计时 —— 那等于把聚合速率乘以变体数。实测 `peg` 是 6 倍:
       `impact()` 里写的是 `throttle=0.08`(意图"机关枪连珠只响第一声"), 但 idx 是
       按撞击强度 + randint 选出来的, 6 个变体各有一个计时器轮流放行 ⇒ 上限 75 次/秒。
       每一次通过闸门的播放都是一次 `SoundPool.play` 的 JNI 往返, 在骁龙 870 这类
       机器上足以把帧时间顶过 16.67ms 的预算 —— 表现就是"弹珠在飞的时候一卡一卡"。
       (2026-09-13 玩家实测: 关掉音效后 1% low 大幅回升, 是这个问题存在的直接证据。)
    """
    for _p in _VARIANT_FAMILIES:
        if name.startswith(_p) and name[len(_p):].isdigit():
            return _p
    return name

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
        # 发声工作线程: 把后端调用搬出主线程。见 `_drain` 的说明。
        self._q = None
        self._qthread = None
        if getattr(self.out, "needs_worker", False):
            try:
                import queue as _queue
                self._q = _queue.Queue(maxsize=64)
                self._qthread = threading.Thread(target=self._drain, daemon=True)
                self._qthread.start()
            except Exception:
                self._q = None
        if sync:
            self._bake()
        else:
            self._thread = threading.Thread(target=self._bake, daemon=True)
            self._thread.start()

    def _backend_call(self, item):
        """真正那一次后端调用 + 计时(同步路径与工作线程共用, 保证两边统计口径一致)。"""
        _t0 = time.perf_counter()
        try:
            if item[0] == "pcm":
                self.out.play_pcm(item[1])
            else:
                self.out.play_named(item[2], item[1])     # (增益, 名字) —— 名字放最后
        finally:
            _dt = time.perf_counter() - _t0
            _SND_STAT[0] += _dt
            if _dt * 1000.0 > SND_SLOW_MS:
                _SND_STAT[3] += 1
            if _dt > _SND_STAT[1]:
                _SND_STAT[1] = _dt
                # ⚠️ 取**最后一项**当名字 —— 两个分支的载荷不同(pcm 是字节, named 是名字+增益),
                #    所以名字一律放最后。写成 `item[1] if pcm else item[1]` 会把整段 PCM
                #    字节当名字塞进面板(踩过一次, 面板上刷出几 KB 的二进制)。
                _SND_STAT[2] = str(item[-1])[:24]

    def _drain(self):
        """发声工作线程(只在后端声明 `needs_worker` 时才起)。

        ⚠️ **为什么必须把发声搬出主线程**(2026-09-14, 真机实测定案):
        真机跑分面板测出 `SoundPool.play()` **单次最慢 143.6 毫秒、50 次累计 2674 毫秒**
        (平均每次 53.5ms) —— 而帧间隔才 12.5ms。也就是说**主线程每响一声就被卡几十毫秒**,
        整局 1%Low 只有 7.8 就是它造成的(慢帧 14 帧, 14 帧都在发声; 关掉音效 1%Low 立刻回升)。
        SoundPool 为什么慢是设备/HAL 的事, 这里不赌它变快 —— **只赌"主线程不必等它"**。
        ⚠️ 线程安全: SoundPool 本身是线程安全的; 而且本工程**早就在后台线程里调它的 JNI**
        (烘焙线程 `_bake_named` 调 `self._sp.load`), 这条路已经跑了很久。
        ⚠️ 队列满就**丢这一声**(和 winmm 声道全忙时的处理一致) —— 绝不阻塞主线程,
        那正是这个线程存在的理由。
        """
        q = self._q
        if q is None:
            return
        while True:
            try:
                item = q.get()
            except Exception:
                return
            if item is None:
                return
            try:
                self._backend_call(item)
            except Exception:
                pass

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
    SFX_READY_POLL = 0.15               # 探测间隔(秒) —— 它决定**摘页的发现延迟**: 音频是异步
    #   解码的, 探针只负责轮询"好了没有"; 若它在探针刚过去 1ms 时就绪, 玩家要白等满一个间隔。
    #   ⚠️ v0.6.44 试过压到 0.05(理论收益: 最坏 150ms -> 50ms、平均 75ms -> 25ms), v0.6.45 **回退**。
    #      回退理由: 作者没有实测到收益, 求稳 —— **没有实测证据的"理论优化"不值得留在出货里**。
    #      要再试的话先做真机对拍: 同一个包看「启动信息」里的"音效等待"有没有变短, 变短了才谈收益。
    #   ⚠️ 也别反过来往上调"省点 CPU": 省下的 CPU 换不来任何东西, 而多等的那几十毫秒是
    #      **每一次冷启动**都要付的。

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
            # ⚠️ 这里原来有一行「音效开关　已开/已关/无后端」(v0.6.5 就有, 一直排在最前)。
            #    **玩家 2026-09-11 定稿删除**: 「去掉音效开关： xx 这个行 没有意义」。
            #    理由是它属于这块面板自己那条成文规则里的反面 —— **有唯一预期值的, 只在偏离时
            #    才有信息**; 而"音效已开"是常态, 每次打开都印一行不变的"已开"就是纯噪音
            #    (同「后端重建」只在非 0 时出现)。
            #    剩下的信息没有丢: 真出问题时「音频后端」那行会变 ——
            #    静默降级会印出 `Kivy-SoundLoader` 并附「安卓上应为 SoundPool」, 彻底没后端时
            #    会印 `音频后端　静音`。
            # ⚠️ 唯一的代价: **玩家自己点了静音** 时, 面板看起来是全正常的(后端名照旧) ——
            #    那条路只有玩家自己知道。真要补, 正确形状是"偏离才印"而不是恢复常显。
            # ⚠️ 玩家 2026-09-11 定稿: **PC 上也用「冷启动」这个格式** —— 原话「我在pc上也需要
            #    知道版本号 也需要知道那几个时间 别tmd自作主张」。
            #    PCM 后端(_bake_pcm)不写磁盘缓存 ⇒ `cached` 恒为 False ⇒ PC 上永远显示「冷启动」。
            #    那是**事实**, 不是标签错。(原先为了回避"冷启动在 PC 上恒真"而印成「合成耗时 …」,
            #    玩家要的是两边同一个口径, 所以统一。)
            mode_row = "启动方式　%s启动　%.0f ms" % ("热" if self.cached else "冷", self.bake_ms)
            n_rc = getattr(out, "rebuild_count", 0)

            # ⚠️ PCM 后端(PC 的 winmm / Kivy-SoundLoader): 下面三项对它**结构上就不适用**
            #    (音效就绪 / 音效等待 / 加载失败 全都建立在"按名字加载、有 sampleId"之上)。
            #    玩家 2026-09-11 反馈「这几个不适用听起来有点奇怪」—— 三行"不适用"既是纯噪音,
            #    又把真正有内容的两行淹掉了。所以**不适用的行直接不出现**, 而不是印成"不适用"。
            if not named_mode:
                # ⚠️ 玩家 2026-09-11 定稿: PC 上也要看到**那几个时间**。
                #    「音效等待」在 PC 上是**真的 0 ms**, 不是"测不到": winmm 后端没有 probe_all
                #    (`_await_ready` 见到就立刻放行), 根本不存在"等解码"这件事。
                #    PC 的耗时全在上一行的「冷启动 XXXX ms」里。
                rows = ["音频后端　%s" % bname, mode_row, "音效等待　0 ms"]
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
            # ⚠️ 「语音就绪」**紧跟在音效就绪后面**(玩家 2026-09-11 定稿: 「把加载失败 x个
            #    语音xx/xx 改为 语音就绪：xx/yy, 放在音效就绪的后面」)。
            #    它原来是最后一行的「加载失败　N 个　·　语音 XX / YY」: 那一行把两件事挤在一起,
            #    而"加载失败几个"这个数在**语音就绪**里其实已经体现(分母就是满编数)。
            #    和「音效就绪」成对摆开, "音效响、语音不响"这种半死状态一眼可见 —— 单看
            #    「音效就绪 97 / 97」它是隐形的(那个数含语音)。
            _n_voice = sum(1 for _n in self.named if _n.startswith("voice_"))
            _n_voice_all = max(0, self._expected - self._n_bank)
            rows = ["音效就绪　%s" % ready,
                    "语音就绪：%d / %d" % (_n_voice, _n_voice_all),
                    "音频后端　%s" % bname]
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

            # ⚠️ 这里原来是最后一行「加载失败　%d 个　·　语音 %d / %d」—— 已按玩家 2026-09-11
            #    的要求拆掉: 语音那半挪到「音效就绪」后面成了「语音就绪：XX / YY」(见上面),
            #    "加载失败几个"那半不再单列(它已经体现在语音就绪的分母上)。
            #    如果将来"某个音效没加载上"要单独看, `self._failed` 仍然躺在内存里, 随时能印。

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
            _tk = _throttle_key(name)      # 按族计时, 见 _throttle_key 处
            if now - self._last.get(_tk, 0.0) < throttle:
                return False
            self._last[_tk] = now
        if pcm_mode:
            key = (name, lvl)
            data = self._scaled.get(key)
            if data is None:
                data = pcm if lvl >= 10 else _scale_pcm(pcm, lvl / 10.0)
                self._scaled[key] = data
            _FRAME_PROBE[0] += 1
            if self._q is not None:
                self._enqueue(("pcm", data, name))
            else:
                self._backend_call(("pcm", data, name))
            return True
        # ⚠️ 必须把这个返回值存下来再返回: 它和上面那几个"设计内静默"的 return False 长得一模一样,
        #    但语义完全不同 —— 这一条是"过了 enabled/互斥/已加载/增益/节流五道闸门之后,
        #    后端仍然说没播成", 也就是 README 里那个"首次安装必然没声音"的**正面计数**。
        #    节流那一支在它上面提前 return, 根本走不到这里, 所以天然被排除, 不用额外判断。
        _FRAME_PROBE[0] += 1
        self.n_attempt += 1
        if self._q is not None:
            # ⚠️ 投递就返回 True —— 与同步路径"后端受理了"同义。返回值还牵着**震动**
            #    (`_vibrate_tick` 挂在它上面), 所以这里绝不能因为"异步还不知道成败"就返回 False,
            #    那会让装杯落珠的震动整段消失。
            self._enqueue(("named", lvl / 10.0, name))
            return True
        _t0 = time.perf_counter()
        ok = self.out.play_named(name, lvl / 10.0)
        _dt = time.perf_counter() - _t0
        _SND_STAT[0] += _dt
        if _dt * 1000.0 > SND_SLOW_MS:
            _SND_STAT[3] += 1
        if _dt > _SND_STAT[1]:
            _SND_STAT[1] = _dt
            _SND_STAT[2] = name
        if not ok:
            self.n_missed += 1
        return ok

    def _enqueue(self, item):
        """投递给发声工作线程。**队列满就丢这一声** —— 绝不阻塞主线程(见 `_drain`)。"""
        try:
            self._q.put_nowait(item)
        except Exception:
            pass

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
                 jitter=0.10, polish=30, over_f=1.0, wall_f=1.0, rot_deg=0.0,
                 sites=None, scatter=None, quota=None):
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
        # 整堆**绕杯轴(竖直轴)的旋转角**(度)。这是唯一"零容量"的堆形自由度, 理由三条:
        #   1. 旋转是刚性变换 ⇒ 任意两球心距不变 ⇒ `_assert_pile` 的重叠断言天然满足;
        #   2. 俯视截面是圆(a==b, 见 _enumerate) ⇒ 格点纳入判据 (x/a)^2+(z/b)^2<=0.94 不变;
        #   3. 墙体约束 |x| <= halfwidth(h)-r 会被旋转改变(ρ 不变但 |cos| 变了), 实测仍有
        #      >= 11 design px 余量(最紧的是 x100 @ 15 度), 7 档在 0/15/30/45/60/90 度全过。
        # 为什么需要它: 现役"换种子"机制唯一的差别是 `jit = 0.03*2r` 的抖动, 实测 4 个变体
        # 之间逐球位移只有 1.29 design px, 而球径是 100.5 —— 玩家原话"每次下落都是固定的
        # 位置"说的就是这个。实测本参数带来的逐球屏幕位移: 30 度时最差档(x5) 26.6px、
        # x100 档 64.7px, 是现役的 20 倍。
        # ⚠️ 它改的是**相位不是形状**: h 谱 / 离轴 ρ 谱 / 最近邻 3D 距离谱全都逐位不变,
        #    所以它答的是"落点固定", **不答**"堆形呆板"。后者不在本参数的能力范围内。
        self.rot_deg = float(rot_deg)
        # 层间注册位序列: 0=A 1=B 2=C(见 _enumerate 的 ox/oz)。None = 老行为(奇偶交替 ABAB)。
        # 这是**唯一改形状不改密度**的自由度: 每层仍是密排三角格、层距仍是 1.633r(球照样坐
        # 在三个下层球之间的谷上), 变的只是"这层整体落在 A 位还是 B/C 位"。相邻两层不能同
        # 位, 所以 4 层共有 3·2³ = 24 个合法序列 —— 实测**其中 12 个装得下 100 颗**(另 12 个
        # 会触发缩球兜底), 而且 12 个在"去掉绕轴旋转的形状指纹"下**两两不同**。
        # 对照: 绕杯轴旋转(rot_deg)在同一个指纹下距离恒为 0 —— 它只改相位不改形状。
        self.sites = tuple(sites) if sites else None
        # 单层小档(x2/x3, 占中奖场次的 ~81%)的**自由摆放**变体号。0/None = 老行为(格点由内而外)。
        # 为什么要它: 那两档只有 2~3 颗球、地板可用半径 231, 而格点把它们钉在离轴 80 附近
        # —— 只用了 35% 的空间。换成自由摆放后, 4 种摆法(紧凑/贴壁/一字/随机)之间球心
        # 相差可达 ±230, 是格点选点顺序那点差异的量级之上。
        # ⚠️ 只对 count <= _SCATTER_MAX 生效(那几档实测都是单层, 没有跨层落谷问题)。
        self.scatter = None if scatter is None else int(scatter)
        # 每层的**颗数配额**(纯大档用, 见 _enumerate 里读它的地方)。None = 不限制(每层填满)。
        # 为什么要它: 大档(x50/x100)的层间注册只改"整层横移", 而它们是把杯子**填满**的堆,
        # 横移一下轮廓几乎不动(实测两两 Hausdorff 中位只有 0.47~0.55 球径, 小档是 0.59~1.05)。
        # 配额改的是"哪一层多、哪一层少" ⇒ **直接改轮廓**, 这正是缺的那一味。
        # ⚠️ 配额只是"上限", 被卡住的球会**流到下一层**(不会丢), 所以总颗数仍然 = count。
        #    但也因此**必须验证装得下** —— 流上去的球可能顶到高度上限, 触发缩球兜底。
        #    (实测 x100 的 24 个合法层间序列里只有 12 个能装下 100 颗, 配额再一变就更挑。)
        self.quota = tuple(quota) if quota else None

def _volume_H(spec):
    """体积守恒初值: N 球体积 / 格盘密度 = 半椭球堆体积 (2/3)pi R^2 H, R=TAPER*H。"""
    v_total = spec.count * (4.0 / 3.0) * math.pi * spec.r ** 3 / PACK_PHI
    H = (v_total / ((2.0 / 3.0) * math.pi * TAPER * TAPER)) ** (1.0 / 3.0)
    cap = (FLOOR_Y - RIM_Y) * 0.78        # 大珠档允许堆到内壁 78%(×100 要有"半坛"体积感)
    return max(spec.r * 2.0, min(H, cap))

_SCATTER_MAX = 10       # 自由摆放生效的档位上限。
# 为什么是 10: x2/x3/x5/x10 这四个档加起来占中奖场次约 90%, 而它们在地板层的
# 可用半径有 245 —— 把 10 颗球平铺开需要的半径只有 sqrt(10*r^2/0.9) ≈ 167, 绰绰有余。
# 原来这四档都挤在离轴 60~88 那一小圈里(格点由内而外取的), 自由摆放一上来就能甩到 ±245。
# x20 不在此列: 20 颗球平铺需要半径 ~237, 已经顶到 245 的边, 而且"贴壁一圈"那种摆法
# 圆周长只够放 15 颗(2*pi*a/(2r) = 15.3), 放 20 颗必然重叠 —— 它继续走格点 + 层间注册。

def _scatter_floor(spec, a):
    """单层小档的自由摆放: 返回 [(x, z), ...] 或 None(表示"走老格点")。

    spec.scatter 的语义:
      0            -> None, 老行为(格点由内而外, 紧凑居中, 球只用到离轴 ~80)
      其余按 3 取模: 1=贴壁一圈 / 2=偏心一侧 / 0=随机散开
    ⚠️ 三种摆法**都带上 raw 自己的相位/朝向/种子** —— 否则 1/4/7/10 会给出完全相同的
       结果, 变体数就白加了。加了之后 12 个变体的摆法两两不同。
    ⚠️ 返回值必须**逐颗**满足 |(x,z)| <= a 且两两球心距 >= 2r, 否则 _assert_pile 抛。
    """
    n, raw, r = spec.count, spec.scatter, spec.r
    if raw == 0 or n < 1:
        return None
    if n == 1:
        return [(0.0, 0.0)]
    mode = (raw - 1) % 3 + 1
    # ⚠️ 这里踩过两次"变体重合"的坑, 结论记全:
    #   · "偏心"对 n=2 是**伪自由度** —— 2 颗球挤在一侧 = 两个挨着的球, 转一下还是
    #     "两个挨着的球", 于是和"紧凑"重合(双向 Hausdorff 只有 7% 球径)。
    #     n=2 真正只有一个自由度: **分开多远**。所以下面改成"把间距从最紧铺到最松"。
    #   · 相位/朝向必须走黄金角, 不能用固定步长 —— 0.7 弧度是 3 颗球间隔(120 度)的
    #     整数倍, 于是 n=3 的变体 7 和 10 完全重合。
    # 方案: 前一半变体铺一圈(半径 ρ 从"间距恰好 2r"到"贴壁"单调拉开), 后一半随机散开。
    _NSTEP = 6
    if raw <= _NSTEP:
        rho_min = r / math.sin(math.pi / n) if n > 1 else 0.0
        rho = rho_min + (float(raw - 1) / max(1, _NSTEP - 1)) * (a - rho_min)
        rho = max(0.0, min(rho, a))
        ph = math.radians((raw * 137.508) % 360.0)
        return [(rho * math.cos(2.0 * math.pi * i / n + ph),
                 rho * math.sin(2.0 * math.pi * i / n + ph)) for i in range(n)]
    rg = random.Random(raw * 7919 + spec.seed * 131 + n)    # 随机散开, 每 raw 一种
    pts = []
    for _ in range(n):
        for _try in range(400):
            t = rg.uniform(0.0, 2.0 * math.pi)
            rr = a * math.sqrt(rg.uniform(0.05, 1.0))
            x, z = rr * math.cos(t), rr * math.sin(t)
            if all((x - p[0]) ** 2 + (z - p[1]) ** 2 >= (2.0 * r) ** 2 for p in pts):
                pts.append((x, z))
                break
    return pts if len(pts) == n else None

def _enumerate(spec, H, wall_mode=False):
    """给定堆高 H 做确定性格点枚举: 返回 (beads, H, R)。放不满则调用方增大 H。

    wall_mode(N>=35): "一坛子"语义——逐层铺满杯壁圆盘直到堆顶(平截口外圆内收,
    天然微微圆顶); 土丘 dome 剖面只用于小奖档(否则大珠×50 圆顶剖面离散层装不下)。
    """
    rng = random.Random(spec.seed)
    r = spec.r
    _rot = math.radians(spec.rot_deg)
    _rc, _rs = math.cos(_rot), math.sin(_rot)
    # ---- 格子间距系数 k_p = 球心间距 / (2r): 1.0 = 球紧挨着 ----
    # ⚠️ 原来壁模式用 1.12(是"给抛光留的松量", 不是物理值) —— 代价是**每层只填 89% 的位置**,
    #    于是 x100 装不下 100 颗 ⇒ 缩球兜底把球从 r=52 一路缩到 46。玩家 2026-09-11 报的
    #    「珠子不够大 / 100 个不够满」根子就在这。改成 **1.02**(每层多装 13%)之后实测:
    #      · 球 r=50.2(**+9%**, 一次都不用缩, x2/x5/x10/x50/x100 统一同径)
    #      · 顶层不再"中间塌一个坑"(满度 60% -> 92% —— 这才是"不够满"的真实来源)
    #      · 堆顶 design y 44 -> 16(仍**在玻璃轮廓内**, 没有冒出杯口)
    #      · 生成反而更快(19.6ms -> 13.1ms)
    #    ⚠️ **不能再往下压到 1.00**: 跨层三维距离 = sqrt((1.1547·k_p·r)² + (1.633r)²),
    #       k_p=1.02 时是 2.0138r(只剩 0.7px 余量), 1.00 时正好 2.000r —— 抖动一上来
    #       `_assert_pile` 就报 "unresolved overlap"(实测 k_p=1.00 直接抛)。1.02 实测 8 个种子全过。
    # ⚠️ 土丘档(count<35) 从 1.0 改成 **1.25**(玩家新诉求「球少的时候 球和球有一定距离」)——
    #    原来小球是**死贴着**的。散开后像"几颗球散落在杯底"; 代价是每层占地方, 小档要缩一点球
    #    (实测 x20 落在 r=44.4, 那就是它这一类堆法的容量上限)。
    k_p = 1.02 if wall_mode else 1.25
    # ⚠️ wall 模式的抖动系数**上限是 0.09, 不能到 0.10**: 实测 0.10(±10px) 会让 x100
    #    触发缩球兜底(r 50.23 -> 47.22), 而缩球破坏玩家定稿的"球一样大"。0.09(±9px) 在
    #    32 个种子上全部安全。抖动是从可用半径里扣的(a = wall - r - jit), 所以这个系数
    #    同时是"逼真度"和"容量"的交换 —— 别再往上加。
    # 它只改**位置**不改球径: 每颗球在 (x,z) 上随机偏移, r 一个字没动。
    # ⚠️ 但别指望靠它做跨局差异: 实测把系数从 0.03 翻三倍到 0.09, **变体之间的**逐球位移
    #    只从 1.35px 涨到 3.31px(球径的 1.3% -> 3.3%)。因为抖动是每颗球独立的小扰动,
    #    两个独立随机序列之差的期望只有 0.67*jit。跨局可辨靠 PileSpec.rot_deg, 不是这里。
    #    (小档走 dome, 用的是 spec.jitter = 0.10, 本来就比这里松。)
    jit = (0.09 if wall_mode else spec.jitter) * 2.0 * r
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
        # 单层小档(x2/x3): 走自由摆放, 就地返回
        # ⚠️ 半径要用**杯壁**允许的(floor_radius - r - jit), 不能用上面那个 a —— dome 模式下
        #    a 取的是 min(杯壁, 土丘半径), 而土丘半径才是瓶颈(x2 只有 124, 减完剩 64);
        #    但 _polish 只保证"在杯壁内", 实测球本来就会跑到 ρ=88(超出 64)。
        #    用土丘半径会把自由摆放白白锁死在中心 —— 那正是要解决的问题本身。
        if (not wall_mode) and spec.scatter is not None and spec.count <= _SCATTER_MAX:
            _sp = _scatter_floor(spec, floor_radius() - r - jit)
            if _sp is not None:
                return ([{"x": _x, "z": _z, "h": h, "layer": k, "r": r}
                         for _x, _z in _sp], H, R)
        b = a
        # 层间注册位(0=A 1=B 2=C): 默认奇偶交替(A/B) = 老行为; 给了 sites 就按序列走
        _st = spec.sites[k] if (spec.sites and k < len(spec.sites)) else (1 if k % 2 else 0)
        ox = 0.0 if _st == 0 else r * k_p
        oz = 0.0 if _st == 0 else (0.577 * r * k_p if _st == 1 else -0.577 * r * k_p)
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
        _nl = 0                                  # 本层已填几颗(用于配额)
        for x, z in pts:
            if len(beads) >= spec.count:
                break
            # 层颗数配额(大档用, 见 PileSpec.quota): 本层填够就收手, 剩下的球流到上一层。
            # 只限制上限、不丢球 —— 被卡住的球在下一层的循环里继续填, 总颗数仍 = count。
            if spec.quota and _nl >= spec.quota[k]:
                break
            _nl += 1
            jx = rng.uniform(-1.0, 1.0) * jit
            jz = rng.uniform(-1.0, 1.0) * jit
            bx, bz = x + jx, z + jz
            if spec.rot_deg:                     # 绕杯轴旋转(见 PileSpec.rot_deg): 刚性, 不改距离
                bx, bz = bx * _rc - bz * _rs, bx * _rs + bz * _rc
            beads.append({"x": bx, "z": bz, "h": h, "layer": k, "r": r})
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

# -*- coding: utf-8 -*-
"""球堆坐标表 —— **离线烘焙产物, 勿手改**。

由 tools/bake_pile_data.py 生成。每项是 "x,z,h;x,z,h;...", 单位 design px x 10(定点),
球心坐标(不是投影结果 —— 投影仍走 project_pile, 这样以后改 K2/CX/FLOOR_Y 不用重烘)。

⚠️ 改球堆的任何参数(_PILE_SITES/_PILE_QUOTA/_PILE_ROT_STEP/_r_dp_for/
   pile3d 的 k_p/jit/_SCATTER_MAX...) 之后**必须重跑烘焙**, 否则表和行为脱钩。
   fx_probe 有一条"表 vs 现算逐位对拍"的门禁守着这件事。

⚠️ 运行期**必须保留程序化生成的回退**: 表损坏不能表现为"那一局没球"。
"""
_PILE_BAKED_SCALE = 10

_PILE_BAKED_R = {
    2: 50.233,
    3: 50.233,
    5: 50.233,
    10: 50.233,
    20: 44.385,
    50: 50.233,
    100: 50.233,
}

_PILE_BAKED = {
    # ---- x2 ----
    (2, 0): '697,233,502;-16,-955,502',
    (2, 1): '-370,339,502;370,-339,502',
    (2, 2): '75,-858,502;-75,858,502',
    (2, 3): '742,968,502;-742,-968,502',
    (2, 4): '-1555,-275,502;1555,275,502',
    (2, 5): '1635,-1040,502;-1635,1040,502',
    (2, 6): '-596,2218,502;596,-2218,502',
    (2, 7): '123,561,502;-760,-1504,502',
    (2, 8): '-55,-803,502;490,786,502',
    (2, 9): '449,444,502;1731,-593,502',
    (2, 10): '999,-163,502;108,-829,502',
    (2, 11): '-488,928,502;2041,-702,502',

    # ---- x3 ----
    (3, 0): '69,-310,502;-644,677,502;630,706,502',
    (3, 1): '-428,392,502;-125,-566,502;553,174,502',
    (3, 2): '81,-920,502;756,530,502;-837,390,502',
    (3, 3): '771,1005,502;-1256,165,502;485,-1170,502',
    (3, 4): '-1586,-281,502;1036,-1233,502;550,1514,502',
    (3, 5): '1649,-1049,502;84,1952,502;-1732,-903,502',
    (3, 6): '-596,2218,502;-1623,-1626,502;2219,-593,502',
    (3, 7): '906,1504,502;-1689,758,502;182,-565,502',
    (3, 8): '636,-1793,502;-633,832,502;671,408,502',
    (3, 9): '-587,608,502;-671,-1439,502;-774,-380,502',
    (3, 10): '-1540,1071,502;-979,-1100,502;1249,-631,502',
    (3, 11): '103,1599,502;-1409,0,502;-699,-1177,502',

    # ---- x5 ----
    (5, 0): '163,-496,502;-644,677,502;630,706,502;1313,-402,502;-99,21,1323',
    (5, 1): '-630,577,502;-744,-421,502;170,-837,502;849,-97,502;354,778,502',
    (5, 2): '100,-1139,502;1114,-257,502;588,980,502;-750,863,502;-1052,-447,502',
    (5, 3): '871,1136,502;-811,1180,502;-1373,-407,502;-37,-1431,502;1350,-477,502',
    (5, 4): '-1694,-300,502;-238,-1704,502;1546,-753,502;1194,1238,502;-808,1518,502',
    (5, 5): '1695,-1078,502;1549,1279,502;-737,1868,502;-2005,-124,502;-502,-1945,502',
    (5, 6): '-596,2218,502;-2294,118,502;-821,-2145,502;1786,-1444,502;1926,1253,502',
    (5, 7): '-1779,-1087,502;310,-1182,502;-1214,1496,502;-261,-2014,502;-1144,203,502',
    (5, 8): '613,-18,502;-1167,-1469,502;399,1250,502;-706,1044,502;1921,1246,502',
    (5, 9): '1285,155,502;195,1142,502;-379,-1475,502;-1279,772,502;394,-470,502',
    (5, 10): '-904,1123,502;1613,-1124,502;-1144,-683,502;1330,1437,502;-579,-1902,502',
    (5, 11): '1216,-1256,502;-1910,278,502;310,1078,502;-653,178,502;-656,-1680,502',

    # ---- x10 ----
    (10, 0): '-559,233,502;613,133,502;2,-925,502;57,1230,502;1251,-889,502;81,364,1323;-672,-674,1323;652,-775,1323;1338,459,1323;690,262,2143',
    (10, 1): '-1199,1098,502;-1615,184,502;-1415,-801,502;-674,-1479,502;324,-1593,502;1199,-1098,502;1615,-184,502;1415,801,502;674,1479,502;-324,1593,502',
    (10, 2): '154,-1753,502;1155,-1328,502;1715,-395,502;1620,688,502;906,1509,502;-154,1753,502;-1155,1328,502;-1715,395,502;-1620,-688,502;-906,-1509,502',
    (10, 3): '1152,1503,502;49,1894,502;-1074,1561,502;-1786,632,502;-1816,-539,502;-1152,-1503,502;-49,-1894,502;1074,-1561,502;1786,-632,502;1816,539,502',
    (10, 4): '-1998,-353,502;-1408,-1460,502;-281,-2009,502;953,-1791,502;1824,-888,502;1998,353,502;1408,1460,502;281,2009,502;-953,1791,502;-1824,888,502',
    (10, 5): '1825,-1161,502;2159,134,502;1668,1377,502;540,2094,502;-794,2012,502;-1825,1161,502;-2159,-134,502;-1668,-1377,502;-540,-2094,502;794,-2012,502',
    (10, 6): '-596,2218,502;-1786,1444,502;-2294,118,502;-1926,-1253,502;-821,-2145,502;596,-2218,502;1786,-1444,502;2294,-118,502;1926,1253,502;821,2145,502',
    (10, 7): '-709,-2167,502;-1175,1826,502;-454,1095,502;1005,-45,502;-445,-792,502;1742,-1347,502;-1405,290,502;-2036,-991,502;783,1585,502;894,-1928,502',
    (10, 8): '438,-273,502;2197,-511,502;846,2087,502;-2128,-639,502;-711,-1545,502;609,955,502;-1232,573,502;1798,857,502;-271,1506,502;-1505,1597,502',
    (10, 9): '1668,608,502;-378,-1098,502;-1735,-1253,502;583,1258,502;575,-1785,502;-1074,1022,502;-547,2129,502;-2089,241,502;-915,-126,502;1518,-985,502',
    (10, 10): '-1380,-908,502;1991,1103,502;1814,-827,502;-950,1605,502;-358,-2197,502;890,1163,502;428,-428,502;1066,-1710,502;-635,216,502;-1542,667,502',
    (10, 11): '43,1580,502;-1376,211,502;930,-347,502;-993,2067,502;2001,756,502;137,-1773,502;873,932,502;-218,-591,502;1302,-1721,502;1943,-491,502',

    # ---- x20 ----
    (20, 0): '61,206,444;-566,-842,444;557,-817,444;-1059,125,444;1105,175,444;-482,1122,444;516,1167,444;21,-1806,444;-497,-68,1169;610,-89,1169;-34,841,1169;71,-1089,1169;-1115,730,1169;1098,820,1169;-1039,-1040,1169;1106,-1056,1169;-27,-273,1893;-546,555,1893;594,623,1893;1167,-290,1893',
    (20, 1): '427,358,444;109,-584,444;-667,96,444;1088,-415,444;-328,1170,444;-1010,-1014,444;1432,828,444;634,1532,444;885,-1569,444;-1548,880,444;-98,-1875,444;-1835,-245,444;59,1,1169;-66,-1066,1169;-1001,-292,1169;-699,817,1169;1184,380,1169;344,1109,1169;-376,-321,1893;753,38,1893',
    (20, 2): '-478,-152,444;300,672,444;453,-647,444;-607,1030,444;-491,-1204,444;1377,106,444;-1547,435,444;-1463,-733,444;348,1621,444;582,-1610,444;1248,1306,444;1500,-976,444;-7,50,1169;810,613,1169;879,-362,1169;0,-1055,1169;-1035,463,1169;-955,-664,1169;267,-604,1893;-722,-105,1893',
    (20, 3): '235,-285,444;-783,-127,444;-84,764,444;-394,-1205,444;943,601,444;-1174,1046,444;846,-1272,444;1546,-455,444;-1431,-970,444;600,1643,444;-1739,-27,444;-454,1804,444;-385,-450,1169;369,526,1169;717,-458,1169;-840,658,1169;1381,336,1169;-446,330,1893;141,-723,1893;720,69,1893',
    (20, 4): '-28,486,444;673,-390,444;-357,-600,444;945,601,444;-1183,176,444;249,-1460,444;340,1595,444;-734,1257,444;1675,-247,444;-1617,-719,444;1381,-1242,444;-867,-1728,444;460,76,1169;-565,-76,1169;-172,990,1169;176,-953,1169;-1345,662,1169;-916,-1218,1169;-560,-588,1893;-168,478,1893',
    (20, 5): '-297,-509,444;-136,655,444;785,1,444;-1268,230,444;717,-1085,444;797,1120,444;-1286,-982,444;-338,-1581,444;-1246,1303,444;1669,-405,444;-155,1735,444;1720,538,444;-24,-75,1169;-66,1163,1169;844,510,1169;1000,-657,1169;-1042,-531,1169;-64,-1148,1169;552,-68,1893;-357,-624,1893',
    (20, 6): '465,212,444;-201,-644,444;-478,344,444;709,-964,444;272,1171,444;-1433,-427,444;1619,-106,444;1310,953,444;-27,-1787,444;-870,1546,444;-1150,-1402,444;-1668,764,444;53,-93,1169;-711,-697,1169;-981,246,1169;-240,1137,1169;1112,-183,1169;838,753,1169;-367,450,1893;745,249,1893',
    (20, 7): '-558,314,444;576,292,444;76,-686,444;122,1275,444;-974,-618,444;1250,-563,444;-1101,1234,444;-1636,296,444;1254,1199,444;-363,-1702,444;1680,282,444;705,-1688,444;-82,125,1169;1058,118,1169;565,-840,1169;-621,-990,1169;-677,946,1169;-1173,76,1169;-140,-589,1893;-682,391,1893',
    (20, 8): '63,-325,444;-788,357,444;226,696,444;-777,-850,444;1182,23,444;-542,1452,444;-98,-1508,444;963,-1233,444;-1794,-188,444;1359,1084,444;-1533,902,444;478,1802,444;-629,-130,1169;506,267,1169;264,-796,1169;-354,976,1169;1430,-426,1169;-198,357,1893;-281,-616,1893;614,-234,1893',
    (20, 9): '182,426,444;346,-782,444;-616,-328,444;1172,117,444;-918,758,444;-488,-1421,444;1033,1142,444;23,1616,444;1425,-1033,444;-1752,122,444;442,-1795,444;-1625,-1052,444;549,-311,1169;-506,274,1169;405,952,1169;-348,-928,1169;-646,1192,1169;-1379,-602,1169;-778,-160,1893;222,473,1893',
    (20, 10): '-489,-234,444;58,627,444;681,-442,444;-949,751,444;-83,-1244,444;1360,685,444;-1587,-231,444;-1096,-1227,444;-314,1714,444;1147,-1335,444;875,1598,444;1791,-315,444;-1,-78,1169;526,834,1169;929,6,1169;419,-1140,1169;-1186,87,1169;-717,-881,1169;322,-476,1893;-663,-327,1893',
    (20, 11): '-135,40,444;626,-699,444;942,281,444;-504,-1064,444;136,1144,444;-1224,-273,444;-978,812,444;1769,-519,444;113,-409,1169;497,535,1169;-646,86,1169;1221,-400,1169;-1069,-885,1169;-451,1217,1169;677,-1386,1169;1379,734,1169;-368,-587,1893;-15,503,1893;785,-131,1893;-1023,433,1893',

    # ---- x50 ----
    (50, 0): '26,96,502;-524,-823,502;522,-819,502;-1031,127,502;1045,103,502;-523,1020,502;545,1002,502;-4,-1683,502;-1551,-740,502;1590,-741,502;-1561,1029,502;1598,1008,502;-1037,-1734,502;1057,-1679,502;60,1959,502;2095,128,502;-1036,1922,502;1113,1888,502;-496,349,1323;533,377,1323;0,-567,1323;15,1237,1323;-1017,-516,1323;1039,-558,1323;-1039,1284,1323;1069,1250,1323;-1616,290,1323;1564,361,1323;-533,-1447,1323;526,-1446,1323;-495,2130,1323;602,2164,1323;-2114,-600,1323;2152,-598,1323;-1570,-1504,1323;1596,-1465,1323;-2103,1234,1323;2156,1173,1323;32,-2323,1323;-1556,2181,1323;1627,2156,1323;2652,288,1323;-988,-2407,1323;1053,-2353,1323;-494,-264,2143;546,-254,2143;12,630,2143;29,-1146,2143;-1030,586,2143;1053,635,2143',
    (50, 1): '154,130,502;-84,-867,502;-879,-178,502;913,-573,502;-602,839,502;-1099,-1227,502;1146,413,502;397,1164,502;746,-1627,502;-1626,543,502;-199,-1966,502;-1931,-414,502;1977,-279,502;-282,1910,502;1707,-1295,502;-1268,1592,502;1541,1455,502;300,-432,1323;-511,268,1323;503,593,1323;-773,-747,1323;1329,-169,1323;-298,1331,1323;41,-1438,1323;-1503,-23,1323;1136,-1198,1323;-1288,1013,1323;1495,877,1323;787,1594,1323;-1016,-1854,1323;-1783,-1204,1323;2079,-849,1323;-1064,2135,1323;2262,227,1323;-115,2465,1323;770,-2206,1323;-2370,493,1323;1700,2013,1323;-211,-2546,1323;-2591,-552,1323;-2149,1556,1323;2446,1215,1323;881,2594,1323;37,83,2143;1014,318,2143;237,1109,2143;796,-729,2143;-220,-920,2143;-948,-193,2143;-751,795,2143',
    (50, 2): '-144,-74,502;654,622,502;768,-503,502;-285,1075,502;-112,-1079,502;1568,207,502;-1188,485,502;-1027,-609,502;532,1660,502;800,-1507,502;1503,1342,502;1682,-920,502;-1270,1490,502;-979,-1620,502;-465,2263,502;-92,-2092,502;-1960,-234,502;2473,632,502;2525,-374,502;106,449,1323;195,-587,1323;-726,-114,1323;1025,14,1323;-857,963,1323;-690,-1118,1323;977,1101,1323;1108,-1012,1323;-46,1604,1323;229,-1609,1323;-1741,305,1323;-1606,-702,1323;1901,706,1323;1945,-398,1323;-977,1991,1323;-643,-2274,1323;-1820,1306,1323;-1544,-1752,1323;976,2139,1323;1313,-1996,1323;-2563,-272,1323;1918,1747,1323;2243,-1404,1323;470,-2609,1323;-2649,735,1323;-2430,-1277,1323;-466,535,2143;-385,-582,2143;447,-19,2143;-1303,-74,2143;398,1124,2143',
    (50, 3): '407,-290,502;-612,-158,502;46,648,502;-260,-1104,502;1052,520,502;-963,795,502;772,-1232,502;1462,-398,502;-1301,-980,502;621,1493,502;-1653,-13,502;-373,1637,502;216,-2107,502;2052,424,502;-1003,-1945,502;1619,1373,502;1808,-1397,502;-2120,967,502;-1371,1748,502;-115,-542,1323;525,278,1323;-482,407,1323;957,-682,1323;-1125,-427,1323;105,1225,1323;274,-1530,1323;1525,182,1323;-740,-1428,1323;1105,1098,1323;-1485,542,1323;-889,1370,1323;1282,-1640,1323;2027,-859,1323;-1786,-1298,1323;729,2063,1323;-2127,-353,1323;-266,2207,1323;-297,-2379,1323;2283,958,1323;-1896,1502,1323;704,-2462,1323;2560,-8,1323;1884,1889,1323;-2537,564,1323;-1264,2318,1323;2,26,2143;353,-955,2143;1001,-104,2143;-640,-789,2143;-1025,144,2143',
    (50, 4): '-87,519,502;553,-285,502;-447,-448,502;922,679,502;-1120,332,502;213,-1240,502;288,1521,502;-753,1270,502;1636,-176,502;-1488,-652,502;1284,-1131,502;-778,-1484,502;1324,1696,502;-1834,1091,502;1957,869,502;-2155,139,502;-432,2222,502;870,-2076,502;-179,-2320,502;460,328,1323;-559,183,1323;99,-645,1323;-180,1180,1323;1116,-433,1323;-900,-810,1323;847,1366,1323;-1262,992,1323;1483,534,1323;-1593,-4,1323;770,-1401,1323;-217,-1630,1323;177,2138,1323;-980,1999,1323;2216,-163,1323;-1951,-1001,1323;1863,-1151,1323;-1290,-1757,1323;1900,1501,1323;-2295,740,1323;396,-2426,1323;1232,2359,1323;-1958,1717,1323;-2600,-234,1323;551,-245,2143;-444,-385,2143;-76,609,2143;212,-1234,2143;930,792,2143;-1078,442,2143;1207,-1006,2143',
    (50, 5): '-53,-151,502;-58,866,502;869,330,502;-1043,382,502;825,-748,502;893,1346,502;-1033,-625,502;-184,-1162,502;-1093,1460,502;1757,-250,502;-6,1870,502;1784,766,502;-1935,-182,502;608,-1779,502;-1925,896,502;1793,-1391,502;-1067,-1731,502;931,2373,502;1777,1832,502;-411,407,1323;527,-139,1323;-421,-598,1323;521,901,1323;-1326,-125,1323;420,-1164,1323;-397,1439,1323;1448,294,1323;-1280,911,1323;1403,-710,1323;-1262,-1158,1323;-423,-1710,1323;572,1918,1323;1472,1339,1323;-2355,507,1323;1309,-1710,1323;-2423,-496,1323;538,-2355,1323;-1149,2106,1323;2414,-71,1323;-1457,-2161,1323;-170,2649,1323;2425,1019,1323;2320,-1122,1323;-2214,-1478,1323;-402,-2715,1323;-705,-92,2143;205,-622,2143;199,419,2143;-666,-1124,2143;-682,933,2143',
    (50, 6): '135,43,502;-566,-721,502;-833,334,502;434,-923,502;-100,1035,502;-1584,-419,502;1171,-141,502;910,829,502;-271,-1703,502;-1075,1429,502;-1259,-1449,502;-1834,606,502;1489,-1254,502;750,2010,502;834,-2168,502;-324,2096,502;1982,542,502;-2234,-1210,502;-23,-516,1323;-270,474,1323;695,192,1323;-1045,-206,1323;1005,-820,1323;459,1193,1323;-702,-1285,1323;-1296,822,1323;296,-1578,1323;-499,1495,1323;1751,-121,1323;1489,849,1323;-1699,-988,1323;-2092,33,1323;1279,-1795,1323;230,2267,1323;2043,-1083,1323;1303,1836,1323;-576,-2361,1323;-1720,1732,1323;2513,775,1323;-1691,-1992,1323;-2476,961,1323;-973,2404,1323;2730,-349,1323;542,-389,2143;294,610,2143;-468,-143,2143;1263,311,2143;-133,-1172,2143;-716,845,2143;1566,-671,2143',
    (50, 7): '-142,108,502;870,77,502;404,-818,502;348,1204,502;-650,-769,502;1409,-847,502;-770,998,502;-1209,65,502;1419,1087,502;-165,-1748,502;1875,71,502;916,-1766,502;-264,2354,502;-1688,-854,502;851,2084,502;-1171,-1763,502;-1840,983,502;392,404,1323;-85,-508,1323;-685,312,1323;947,-498,1323;-229,1206,1323;-1191,-556,1323;1500,513,1323;470,-1395,1323;906,1356,1323;-611,-1377,1323;-1308,1214,1323;-1738,304,1323;2074,-530,1323;1571,-1404,1323;274,2137,1323;-1667,-1463,1323;-762,2058,1323;-2219,-622,1323;2081,1351,1323;150,-2432,1323;-2385,1182,1323;2583,414,1323;1148,-2315,1323;-961,-2342,1323;-1765,2116,1323;-2741,238,1323;-141,633,2143;-611,-263,2143;432,-230,2143;-1202,575,2143;945,707,2143;-39,-1117,2143;-745,1470,2143',
    (50, 8): '175,-469,502;-661,198,502;317,536,502;-816,-828,502;1125,-101,502;-463,1194,502;-47,-1494,502;974,-1135,502;-1645,-151,502;1272,970,502;-1447,834,502;481,1676,502;-1034,-1828,502;1939,-741,502;-1842,-1145,502;2139,257,502;774,-2145,502;-1259,1824,502;-403,-421,1323;549,-26,1323;-258,615,1323;394,-1118,1323;-1237,261,1323;692,979,1323;-622,-1420,1323;1360,-702,1323;-1393,-777,1323;1560,283,1323;-1040,1248,1323;-98,1646,1323;195,-2111,1323;1224,-1780,1323;-2223,-206,1323;1768,1269,1323;-2025,884,1323;1003,1934,1323;-1611,-1893,1323;2365,-348,1323;-887,2268,1323;-737,-2567,1323;2209,-1428,1323;2563,654,1323;-1837,1871,1323;-29,22,2143;-181,-1043,2143;780,-685,2143;-1007,-343,2143;-832,696,2143;117,1057,2143;985,357,2143',
    (50, 9): '232,480,502;357,-581,502;-589,-136,502;1192,88,502;-763,854,502;-475,-1170,502;1037,1081,502;80,1530,502;1328,-930,502;-1623,201,502;458,-1638,502;-1513,-801,502;2019,692,502;-891,1866,502;2126,-320,502;-1734,1261,502;905,2103,502;-352,-2302,502;-1310,-1795,502;614,44,1323;-347,450,1323;-223,-587,1323;452,1085,1323;753,-1004,1323;-1168,-159,1323;1440,663,1323;-494,1443,1323;1549,-370,1323;-1342,833,1323;-121,-1630,1323;-1055,-1157,1323;1277,1658,1323;322,2104,1323;1720,-1360,1323;-2203,192,1323;916,-1996,1323;-2093,-806,1323;2400,255,1323;-1471,1866,1323;-928,-2232,1323;2248,1401,1323;-624,2482,1323;-2314,1268,1323;67,-2703,1323;-1890,-1791,1323;345,-469,2143;-591,-77,2143;240,545,2143;-429,-1129,2143;1200,110,2143',
    (50, 10): '-171,-89,502;431,760,502;890,-241,502;-631,804,502;219,-1025,502;1436,716,502;-1205,-35,502;-807,-990,502;-120,1734,502;1308,-1280,502;993,1646,502;1931,-235,502;-1633,894,502;-383,-2020,502;-1215,1928,502;758,-2196,502;-1853,-899,502;2004,1563,502;2476,609,502;-105,537,1323;328,-383,1323;-697,-331,1323;926,440,1323;-1158,562,1323;-303,-1278,1323;488,1361,1323;1412,-493,1323;-601,1411,1323;729,-1305,1323;-1742,-256,1323;-1351,-1189,1323;1490,1294,1323;1928,374,1323;-1602,1496,1323;179,-2159,1323;-2168,666,1323;-956,-2113,1323;200,2455,1323;1928,-1355,1323;-2400,-1094,1323;1206,2263,1323;2584,-562,1323;1337,-2167,1323;-2751,-153,1323;-615,261,2143;-178,-666,2143;394,208,2143;-1211,-600,2143;-38,1117,2143;872,-709,2143',
    (50, 11): '-93,29,502;647,-728,502;921,275,502;-389,-958,502;233,1025,502;-1095,-241,502;-831,763,502;1664,-539,502;257,-1728,502;1218,1270,502;-1409,-1242,502;-465,1765,502;1332,-1527,502;1915,495,502;-1868,606,502;540,2036,502;-2150,-448,502;-1467,1587,502;-496,-388,1323;-167,605,1323;536,-159,1323;-1213,326,1323;247,-1148,1323;821,846,1323;-1504,-653,1323;-861,1342,1323;-777,-1390,1323;157,1600,1323;1257,-952,1323;1565,33,1323;-2199,130,1323;-1887,1186,1323;-35,-2230,1323;1232,1850,1323;954,-1967,1323;1886,1074,1323;-1794,-1676,1323;-563,2337,1323;2252,-777,1323;-2469,-932,1323;-1556,2183,1323;406,2601,1323;1965,-1741,1323;2581,318,1323;-48,-12,2143;701,-787,2143;1033,265,2143;-318,-1018,2143;-1065,-275,2143;-733,731,2143',

    # ---- x100 ----
    (100, 0): '29,94,502;-522,-836,502;518,-819,502;-1029,116,502;1051,97,502;-514,995,502;546,992,502;-7,-1699,502;-1566,-743,502;1566,-765,502;-1570,1024,502;1598,1009,502;-1043,-1728,502;1040,-1677,502;50,1969,502;2096,124,502;-1061,1920,502;1112,1888,502;-495,342,1323;540,368,1323;-6,-572,1323;16,1229,1323;-1022,-542,1323;1029,-545,1323;-1035,1249,1323;1067,1247,1323;-1582,292,1323;1567,362,1323;-533,-1452,1323;510,-1434,1323;-513,2109,1323;598,2159,1323;-2130,-610,1323;2131,-632,1323;-1565,-1475,1323;1583,-1475,1323;-2109,1236,1323;2150,1189,1323;17,-2332,1323;-1588,2162,1323;1626,2156,1323;2652,288,1323;-999,-2417,1323;1026,-2380,1323;-505,-278,2143;525,-257,2143;16,615,2143;1,-1156,2143;-1026,581,2143;1055,634,2143;-1033,-1159,2143;1027,-1172,2143;-1592,-323,2143;1562,-318,2143;-505,1483,2143;533,1492,2143;-535,-2065,2143;521,-2044,2143;-2091,567,2143;2090,612,2143;-1569,1475,2143;1567,1541,2143;-2121,-1225,2143;2118,-1250,2143;5,2371,2143;-1546,-2063,2143;1531,-2095,2143;2600,-291,2143;-1045,2410,2143;-4,13,2963;-518,-893,2963;505,-853,2963;-1069,-21,2963;-487,902,2963;534,889,2963;1042,17,2963;-34,-1773,2963;25,1777,2963;-1553,-901,2963;1550,-920,2963;-1540,866,2963;1563,914,2963;-1040,-1779,2963;1040,-1787,2963;-1038,1742,2963;1038,1777,2963;-2097,-16,2963;2046,16,2963;-539,-2701,2963;486,-2635,2963;-496,2690,2963;545,2653,2963;-2064,-1803,2963;2045,-1826,2963;-2077,1754,2963;2100,1769,2963;-2594,-889,2963;2571,-888,2963;-2578,883,2963;2623,840,2963',
    (100, 1): '125,100,502;-100,-887,502;-877,-214,502;875,-617,502;-628,794,502;-1109,-1216,502;1109,381,502;373,1114,502;739,-1643,502;-1620,511,502;-214,-1986,502;-1945,-439,502;1945,-318,502;-360,1828,502;1684,-1298,502;-1324,1544,502;1526,1406,502;286,-456,1323;-506,229,1323;493,546,1323;-733,-775,1323;1272,-196,1323;-278,1254,1323;30,-1462,1323;-1505,-62,1323;1094,-1185,1323;-1265,968,1323;1510,799,1323;731,1569,1323;-1035,-1854,1323;-1777,-1177,1323;2087,-882,1323;-1036,2047,1323;2307,134,1323;-73,2331,1323;781,-2226,1323;-2344,491,1323;1739,1945,1323;-250,-2564,1323;-2596,-552,1323;-2132,1574,1323;2471,1140,1323;896,2599,1323;51,71,2143;1025,327,2143;274,1080,2143;822,-673,2143;-173,-922,2143;-955,-239,2143;-722,766,2143;1250,1316,2143;-1203,-1273,2143;1824,-365,2143;-521,1779,2143;563,-1689,2143;-1763,457,2143;2073,664,2143;444,2070,2143;-475,-2003,2143;-2010,-629,2143;1593,-1479,2143;-1506,1495,2143;2286,1690,2143;1457,2452,2143;-1409,-2385,2143;-2164,-1623,2143;2826,-126,2143;-303,2863,2143;318,-2683,2143;-2755,82,2143;2606,-1140,2143;-1327,2548,2143;1302,-2484,2143;-2507,1132,2143;589,-143,2963;-164,609,2963;-395,-387,2963;811,864,2963;369,-1128,2963;-1205,284,2963;1580,160,2963;36,1609,2963;1375,-847,2963;-923,1310,2963;-618,-1441,2963;-1436,-740,2963;1814,1182,2963;1017,1847,2963;1126,-1823,2963;-1886,1023,2963;134,-2107,2963;-2197,-80,2963;2387,-504,2963;-759,2308,2963;-1597,-1745,2963;2624,484,2963;255,2618,2963;-1725,2033,2963;-811,-2508,2963;-2380,-1084,2963',
    (100, 2): '-114,-51,502;691,634,502;803,-492,502;-207,1102,502;-38,-1062,502;1591,190,502;-1136,523,502;-985,-598,502;626,1691,502;885,-1494,502;1550,1295,502;1741,-949,502;-1251,1521,502;-928,-1601,502;-427,2322,502;-33,-2089,502;-1945,-225,502;2463,670,502;2531,-329,502;131,500,1323;224,-546,1323;-693,-77,1323;1042,38,1323;-771,972,1323;-617,-1096,1323;996,1127,1323;1165,-986,1323;51,1621,1323;322,-1631,1323;-1669,299,1323;-1557,-700,1323;1901,690,1323;1971,-352,1323;-891,1974,1323;-572,-2303,1323;-1786,1297,1323;-1494,-1730,1323;967,2160,1323;1362,-1977,1323;-2525,-240,1323;1932,1732,1323;2243,-1404,1323;521,-2627,1323;-2642,770,1323;-2394,-1278,1323;-443,486,2143;-354,-546,2143;468,32,2143;-1270,-120,2143;418,1146,2143;586,-999,2143;-1352,915,2143;-1193,-1150,2143;-521,1527,2143;-253,-1548,2143;1331,608,2143;1404,-414,2143;-2245,347,2143;-2136,-729,2143;386,2185,2143;782,-2011,2143;1327,1705,2143;1583,-1404,2143;-1471,1948,2143;-1139,-2189,2143;2424,166,2143;-2361,1364,2143;-2073,-1760,2143;-55,-2566,2143;2227,1187,2143;2441,-845,2143;-110,13,2963;-1020,440,2963;-931,-590,2963;-157,1076,2963;752,613,2963;827,-420,2963;6,-999,2963;-1848,-160,2963;1845,128,2963;-1097,1462,2963;-829,-1599,2963;751,1633,2963;986,-1458,2963;-1930,875,2963;-1772,-1180,2963;1647,1166,2963;1833,-876,2963;-211,2105,2963;140,-2000,2963;-2821,283,2963;-2716,-716,2963;2676,801,2963;2750,-354,2963;-2047,1883,2963;-1717,-2231,2963;1596,2220,2963;1902,-1888,2963;-1143,2479,2963;-633,-2650,2963',
    (100, 3): '414,-300,502;-594,-175,502;48,641,502;-244,-1117,502;1050,507,502;-959,785,502;781,-1236,502;1465,-422,502;-1302,-979,502;626,1500,502;-1634,-31,502;-371,1631,502;209,-2100,502;2050,412,502;-1003,-1944,502;1625,1376,502;1794,-1391,502;-2124,952,502;-1369,1745,502;-107,-554,1323;521,270,1323;-477,392,1323;946,-680,1323;-1115,-430,1323;121,1217,1323;278,-1524,1323;1522,155,1323;-730,-1432,1323;1126,1082,1323;-1478,527,1323;-883,1360,1323;1275,-1649,1323;2022,-858,1323;-1785,-1300,1323;736,2070,1323;-2117,-352,1323;-260,2200,1323;-303,-2374,1323;2285,943,1323;-1885,1480,1323;728,-2493,1323;2593,-19,1323;1886,1893,1323;-2548,556,1323;-1259,2314,1323;-1,15,2143;393,-955,2143;999,-103,2143;-620,-825,2143;-1000,142,2143;-402,967,2143;597,844,2143;1411,-1040,2143;-1403,1102,2143;-238,-1788,2143;1705,705,2143;-1631,-696,2143;228,1788,2143;759,-1913,2143;2075,-280,2143;-2003,280,2143;-772,1929,2143;-1273,-1637,2143;1265,1645,2143;1763,-2001,2143;2440,-1259,2143;-2411,1209,2143;-1786,2051,2143;157,-2731,2143;2716,547,2143;-2666,-537,2143;-192,2777,2143;-846,-2630,2143;2335,1520,2143;-546,-244,2963;76,590,2963;486,-383,2963;-928,722,2963;-142,-1189,2963;1095,470,2963;-1541,-74,2963;-294,1536,2963;-1148,-1065,2963;711,1465,2963;851,-1340,2963;1508,-453,2963;-1933,860,2963;-1322,1676,2963;-778,-2000,2963;1720,1285,2963;232,-2156,2963;2175,331,2963;-2270,-971,2963;292,2447,2963;1852,-1420,2963;-2537,54,2963;-734,2550,2963;1345,2245,2963;1254,-2301,2963;2543,-688,2963',
    (100, 4): '-90,519,502;556,-277,502;-447,-435,502;913,679,502;-1111,335,502;212,-1233,502;282,1515,502;-752,1274,502;1633,-177,502;-1486,-641,502;1277,-1126,502;-771,-1475,502;1305,1684,502;-1812,1088,502;1967,875,502;-2166,147,502;-429,2225,502;872,-2075,502;-174,-2320,502;455,323,1323;-554,173,1323;98,-632,1323;-182,1167,1323;1114,-435,1323;-921,-770,1323;835,1343,1323;-1245,968,1323;1480,560,1323;-1599,23,1323;766,-1402,1323;-212,-1629,1323;173,2164,1323;-966,1989,1323;2213,-163,1323;-1939,-1004,1323;1855,-1171,1323;-1277,-1760,1323;1899,1507,1323;-2300,765,1323;419,-2437,1323;1232,2359,1323;-1956,1728,1323;-2607,-228,1323;547,-250,2143;-447,-397,2143;-112,591,2143;211,-1201,2143;900,767,2143;-1099,407,2143;1219,-1005,2143;-807,-1339,2143;1526,-18,2143;-1459,-540,2143;241,1588,2143;-802,1394,2143;893,-1975,2143;-110,-2200,2143;1969,931,2143;-2156,203,2143;1237,1762,2143;-1788,1173,2143;2280,-776,2143;-1831,-1588,2143;-460,2353,2143;1911,-1754,2143;-1115,-2316,2143;-2479,-794,2143;646,2600,2143;-1516,2175,2143;-9,9,2963;327,977,2963;-660,788,2963;985,193,2963;649,-821,2963;-354,-969,2963;-994,-188,2963;-334,1738,2963;301,-1774,2963;1365,1144,2963;-1659,565,2963;1627,-590,2963;-1396,-1201,2963;662,2000,2963;-1395,1607,2963;1315,-1577,2963;-693,-1919,2963;2003,352,2963;-2030,-419,2963;71,2813,2963;-1059,2606,2963;925,-2561,2963;-87,-2780,2963;1691,2123,2963;-2395,1468,2963;2331,-1354,2963;-1770,-2166,2963;2389,1331,2963;-2662,497,2963;2707,-384,2963;-2429,-1372,2963',
    (100, 5): '-31,-98,502;-52,923,502;909,362,502;-994,472,502;889,-702,502;887,1371,502;-1025,-571,502;-178,-1111,502;-1076,1561,502;1823,-181,502;26,1929,502;1848,823,502;-1931,-137,502;601,-1745,502;-1886,933,502;1848,-1377,502;-1067,-1729,502;945,2371,502;1785,1825,502;-384,449,1323;548,-92,1323;-376,-565,1323;527,922,1323;-1262,-42,1323;475,-1103,1323;-356,1496,1323;1488,368,1323;-1231,1004,1323;1464,-636,1323;-1214,-1119,1323;-362,-1661,1323;605,1926,1323;1462,1379,1323;-2390,645,1323;1332,-1640,1323;-2373,-513,1323;593,-2325,1323;-1148,2137,1323;2418,-81,1323;-1432,-2180,1323;-173,2652,1323;2402,1026,1323;2357,-1096,1323;-2178,-1507,1323;-467,-2675,1323;-660,-59,2143;233,-580,2143;238,424,2143;-636,-1084,2143;-625,981,2143;1169,-123,2143;-1567,-536,2143;216,-1620,2143;-1500,493,2143;1087,-1119,2143;248,1480,2143;1195,883,2143;-1600,-1550,2143;-751,-2090,2143;-1473,1637,2143;2076,-554,2143;-545,2097,2143;2163,448,2143;-2410,65,2143;1156,-2191,2143;1261,1995,2143;-2552,-1064,2143;112,-2648,2143;2085,-1654,2143;-58,-73,2963;-987,-537,2963;-42,-1090,2963;-921,483,2963;10,954,2963;870,408,2963;831,-596,2963;-992,-1542,2963;999,1477,2963;-1826,15,2963;821,-1634,2963;-868,1615,2963;1782,-46,2963;-1873,-1028,2963;-97,-2108,2963;94,2080,2963;1881,955,2963;-1744,1123,2963;1788,-1110,2963;-1859,-2069,2963;-1011,-2608,2963;978,2605,2963;1927,1958,2963;-2717,-450,2963;780,-2670,2963;-752,2639,2963;2740,389,2963;-2633,655,2963;1730,-2113,2963;-1792,2134,2963;2662,-613,2963',
    (100, 6): '94,33,502;-604,-737,502;-885,315,502;380,-938,502;-173,1023,502;-1607,-406,502;1128,-149,502;871,858,502;-337,-1743,502;-1160,1436,502;-1305,-1471,502;-1875,603,502;1435,-1284,502;714,2065,502;791,-2228,502;-395,2087,502;1965,563,502;-2266,-1176,502;-62,-531,1323;-321,448,1323;654,184,1323;-1051,-238,1323;950,-833,1323;386,1175,1323;-752,-1298,1323;-1320,771,1323;219,-1579,1323;-580,1451,1323;1708,-143,1323;1449,828,1323;-1741,-970,1323;-2101,30,1323;1196,-1813,1323;164,2241,1323;1988,-1108,1323;1232,1808,1323;-595,-2370,1323;-1701,1706,1323;2507,771,1323;-1663,-2005,1323;-2476,962,1323;-986,2418,1323;2689,-358,1323;504,-405,2143;240,591,2143;-483,-133,2143;1223,297,2143;-196,-1136,2143;-744,844,2143;1510,-687,2143;956,1296,2143;773,-1408,2143;-34,1645,2143;-1174,-873,2143;-1531,137,2143;2238,92,2143;1977,1065,2143;-53,-2150,2143;-1136,1858,2143;-1022,-1880,2143;-1851,1115,2143;1755,-1662,2143;750,2316,2143;-2261,-577,2143;2549,-956,2143;1758,2051,2143;-423,2571,2143;-2047,-1570,2143;-2614,398,2143;72,36,2963;1065,-260,2963;803,732,2963;360,-975,2963;-615,-735,2963;-962,291,2963;-188,1010,2963;1777,469,2963;-1698,-438,2963;1330,-1238,2963;538,1739,2963;-460,-1737,2963;-1302,1302,2963;2061,-506,2963;1507,1475,2963;-1470,-1511,2963;-2007,556,2963;529,-1977,2963;-545,1962,2963;2814,160,2963;2506,1303,2963;-2444,-1147,2963;-2700,-176,2963;2307,-1483,2963;1282,2546,2963;-1231,-2525,2963;-2286,1548,2963;1601,-2310,2963;264,2708,2963;-241,-2718,2963;-1578,2313,2963',
    (100, 7): '-104,87,502;901,67,502;438,-830,502;376,1221,502;-570,-803,502;1442,-843,502;-706,927,502;-1161,32,502;1458,1067,502;-99,-1812,502;1905,63,502;932,-1777,502;-234,2337,502;-1636,-860,502;877,2094,502;-1103,-1813,502;-1816,1002,502;413,376,1323;-48,-517,1323;-644,298,1323;966,-510,1323;-193,1196,1323;-1114,-603,1323;1502,490,1323;486,-1407,1323;940,1327,1323;-518,-1412,1323;-1234,1162,1323;-1691,267,1323;2062,-495,1323;1580,-1406,1323;297,2105,1323;-1588,-1496,1323;-742,2058,1323;-2164,-619,1323;2069,1335,1323;146,-2432,1323;-2364,1191,1323;2585,430,1323;1146,-2316,1323;-930,-2379,1323;-1746,2082,1323;-2701,244,1323;-111,623,2143;-574,-277,2143;459,-223,2143;-1148,585,2143;951,677,2143;-12,-1111,2143;-694,1481,2143;-1613,-307,2143;373,1530,2143;-1056,-1194,2143;1528,-207,2143;1064,-1125,2143;-1731,1458,2143;-2191,560,2143;1468,1559,2143;-393,-2161,2143;2054,670,2143;602,-2023,2143;-136,2493,2143;-2083,-1194,2143;2086,-1074,2143;-1212,2398,2143;-2669,-335,2143;-1449,-2119,2143;2680,-142,2143;1650,-2005,2143;-55,45,2963;-615,907,2963;-1101,-34,2963;430,931,2963;994,69,2963;528,-828,2963;-554,-904,2963;-1644,885,2963;1535,-787,2963;-134,1819,2963;-1569,-924,2963;1492,943,2963;87,-1747,2963;-1186,1787,2963;-2138,-61,2963;2026,91,2963;1124,-1760,2963;932,1803,2963;-914,-1882,2963;-2205,1791,2963;-2692,852,2963;2620,-720,2963;2178,-1659,2963;-691,2661,2963;-2650,-1070,2963;2538,989,2963;618,-2628,2963;381,2763,2963;-1984,-1895,2963;1975,1839,2963;-380,-2745,2963',
    (100, 8): '177,-467,502;-632,172,502;327,539,502;-799,-834,502;1129,-118,502;-448,1194,502;-11,-1519,502;977,-1135,502;-1644,-153,502;1285,973,502;-1424,827,502;510,1671,502;-1003,-1823,502;1942,-755,502;-1834,-1139,502;2125,266,502;786,-2149,502;-1260,1829,502;-400,-413,1323;556,-29,1323;-249,607,1323;397,-1108,1323;-1209,237,1323;706,978,1323;-582,-1424,1323;1365,-704,1323;-1376,-783,1323;1546,285,1323;-1025,1248,1323;-69,1633,1323;206,-2131,1323;1229,-1775,1323;-2222,-196,1323;1784,1269,1323;-2001,882,1323;1026,1934,1323;-1577,-1906,1323;2378,-372,1323;-867,2255,1323;-737,-2567,1323;2191,-1451,1323;2568,641,1323;-1837,1888,1323;-21,26,2143;-173,-992,2143;786,-678,2143;-979,-349,2143;-825,672,2143;130,1046,2143;973,374,2143;608,-1698,2143;-646,1687,2143;-1155,-1339,2143;1788,-306,2143;-1784,308,2143;1177,1357,2143;-357,-1994,2143;1613,-1320,2143;-1597,1344,2143;357,2027,2143;-1952,-709,2143;1950,701,2143;367,-2698,2143;1410,-2391,2143;-1437,2363,2143;-402,2721,2143;-1309,-2421,2143;2572,-1013,2143;-2576,959,2143;1342,2420,2143;-2139,-1760,2143;2760,64,2143;-2820,-28,2143;2145,1729,2143;-594,111,2963;364,469,2963;227,-526,2963;-446,1111,2963;-742,-882,2963;1185,-187,2963;-1399,758,2963;551,1480,2963;-1581,-245,2963;1373,800,2963;47,-1529,2963;1040,-1229,2963;-1218,1783,2963;-272,2156,2963;-1728,-1244,2963;2146,153,2963;-925,-1873,2963;2018,-843,2963;-2352,424,2963;1515,1829,2963;809,-2241,2963;-2173,1419,2963;671,2515,2963;2313,1154,2963;-202,-2584,2963;1764,-1926,2963',
    (100, 9): '229,485,502;361,-557,502;-594,-126,502;1190,99,502;-748,866,502;-477,-1140,502;1029,1093,502;83,1533,502;1316,-926,502;-1614,206,502;465,-1640,502;-1500,-795,502;2010,689,502;-895,1875,502;2130,-324,502;-1723,1248,502;901,2117,502;-351,-2301,502;-1302,-1791,502;612,50,1323;-349,445,1323;-219,-568,1323;449,1084,1323;738,-997,1323;-1172,-171,1323;1430,675,1323;-492,1458,1323;1551,-355,1323;-1327,825,1323;-112,-1591,1323;-1057,-1169,1323;1294,1691,1323;318,2114,1323;1721,-1345,1323;-2193,226,1323;930,-1987,1323;-2080,-800,1323;2398,258,1323;-1475,1865,1323;-927,-2234,1323;2252,1378,1323;-616,2509,1323;-2302,1295,1323;67,-2703,1323;-1881,-1821,1323;353,-469,2143;-588,-83,2143;222,550,2143;-459,-1095,2143;1199,143,2143;-716,923,2143;467,-1508,2143;-1401,-703,2143;1305,-880,2143;-1550,290,2143;1025,1178,2143;96,1578,2143;-326,-2130,2143;-1262,-1712,2143;2158,-306,2143;-1660,1300,2143;2005,752,2143;-840,1970,2143;1500,-1881,2143;-2400,-316,2143;928,2238,2143;632,-2499,2143;-2232,-1360,2143;-2512,754,2143;1863,1807,2143;-12,20,2963;794,645,2963;-142,1031,2963;926,-369,2963;156,-1014,2963;-822,-614,2963;-960,397,2963;667,1697,2963;-655,-1641,2963;1777,203,2963;-1074,1413,2963;1082,-1451,2963;-1800,-237,2963;1643,1208,2963;-268,2066,2963;262,-2051,2963;-1648,-1230,2963;1881,-815,2963;-1903,762,2963;1497,2351,2963;516,2695,2963;-523,-2691,2963;-1449,-2302,2963;2582,814,2963;-1225,2406,2963;1210,-2450,2963;-2654,-838,2963;2724,-181,2963;-2053,1826,2963;2127,-1790,2963',
    (100, 10): '-121,-72,502;459,792,502;930,-202,502;-547,845,502;320,-1000,502;1460,721,502;-1147,-1,502;-731,-974,502;-42,1800,502;1399,-1294,502;1014,1659,502;1952,-230,502;-1582,905,502;-319,-2052,502;-1175,1996,502;822,-2226,502;-1848,-916,502;2029,1548,502;2472,636,502;-59,533,1323;369,-382,1323;-652,-307,1323;949,437,1323;-1079,614,1323;-210,-1227,1323;505,1375,1323;1434,-489,1323;-498,1443,1323;822,-1291,1323;-1701,-178,1323;-1297,-1096,1323;1510,1322,1323;1956,421,1323;-1523,1532,1323;254,-2118,1323;-2135,729,1323;-896,-2017,1323;199,2455,1323;1976,-1349,1323;-2406,-1077,1323;1201,2278,1323;2595,-558,1323;1399,-2172,1323;-2707,-119,1323;-581,282,2143;-155,-631,2143;421,207,2143;-1188,-527,2143;-15,1122,2143;895,-719,2143;-1614,393,2143;-744,-1451,2143;-1033,1222,2143;294,-1530,2143;1026,1009,2143;1488,92,2143;-2192,-485,2143;-1761,-1441,2143;-353,2082,2143;1440,-1593,2143;651,2036,2143;2033,-768,2143;-2066,1329,2143;-233,-2432,2143;2211,1016,2143;-2657,477,2143;-1312,-2421,2143;827,-2391,2143;1774,1945,2143;2620,47,2143;-95,-54,2963;-1111,48,2963;-685,-868,2963;-545,888,2963;482,812,2963;960,-143,2963;365,-954,2963;-1695,-808,2963;1633,775,2963;-1554,969,2963;-234,-1769,2963;122,1750,2963;1510,-1018,2963;-2117,104,2963;-1255,-1723,2963;1124,1672,2963;2060,-160,2963;-879,1837,2963;893,-1813,2963;-2732,-696,2963;-2315,-1611,2963;2251,1614,2963;2659,647,2963;-2581,1061,2963;-767,-2657,2963;738,2609,2963;2575,-1116,2963;-1881,1919,2963;307,-2647,2963;-313,2674,2963',
    (100, 11): '-84,33,502;646,-729,502;924,270,502;-385,-958,502;233,1019,502;-1086,-239,502;-816,769,502;1643,-522,502;260,-1728,502;1220,1283,502;-1396,-1243,502;-462,1762,502;1322,-1516,502;1915,502,502;-1859,606,502;524,2026,502;-2139,-456,502;-1470,1579,502;-485,-387,1323;-174,606,1323;535,-160,1323;-1195,331,1323;245,-1148,1323;841,844,1323;-1475,-669,1323;-867,1347,1323;-757,-1403,1323;139,1592,1323;1242,-942,1323;1551,50,1323;-2179,122,1323;-1896,1185,1323;-39,-2225,1323;1226,1863,1323;948,-1960,1323;1881,1081,1323;-1786,-1672,1323;-537,2337,1323;2243,-761,1323;-2473,-931,1323;-1560,2178,1323;433,2599,1323;1965,-1741,1323;2580,320,1323;-27,-11,2143;688,-770,2143;1002,237,2143;-320,-1017,2143;-1043,-229,2143;-740,729,2143;276,977,2143;1697,-511,2143;-1732,550,2143;390,-1800,2143;1280,1251,2143;-1309,-1224,2143;-422,1739,2143;1362,-1516,2143;1985,494,2143;-2019,-468,2143;-1421,1518,2143;-647,-2008,2143;574,1976,2143;2431,-1310,2143;2685,-332,2143;-2747,237,2143;-2460,1319,2143;1128,-2511,2143;2388,1474,2143;-2339,-1495,2143;-1089,2515,2143;76,-2793,2143;1677,2228,2143;-1656,-2238,2143;119,-572,2963;420,407,2963;-584,151,2963;1110,-372,2963;-903,-802,2963;-291,1118,2963;783,-1342,2963;1394,666,2963;-209,-1626,2963;685,1400,2963;-1581,-12,2963;-1282,953,2963;1792,-1109,2963;2090,-77,2963;-1227,-1798,2963;21,2199,2963;-1881,-1035,2963;-969,1931,2963;481,-2373,2963;1725,1647,2963;-2276,750,2963;1514,-2076,2963;2404,894,2963;1012,2363,2963;-2611,-343,2963;-1969,1707,2963',

}

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
#   3. 绝不软锁 —— 但"出口"现在分两种, 别混:
#        · pending / win(进场 + 落珠): 玩家没有话语权 ⇒ `busy()` 有 FX_MAX_SEC 硬兜底,
#          超时无条件放手(`_abort`, 画面当场清掉)。
#        · result(杯子装满后): 出口是**玩家的点击**(`request_close`, 挂在 Window 级触摸
#          观察者上, 每一下必到)。这一段**故意没有任何时间兜底** —— 玩家 2026-09-12 定稿
#          "不点就一直在", 任何定时退场都是产品违约(也会让切后台回来那一帧把杯子擦掉)。
#          谁要给它加超时, 先回去问玩家。
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

# 中奖/未中大字淡出时的 **alpha 量化档数**(性能, 2026-09-13)。
# ⚠️ 为什么必须量化: `color` 是 Kivy `Label._font_properties` 之一, 会被**烘进字形纹理**
#    (实测 Label 画布里那条 Color 恒为 (1,1,1,1), 颜色不在那儿) ⇒ 赋一次值就重测字形 +
#    重光栅化 + 重建纹理 + 上传。而淡出段 alpha 每帧都在变 ⇒ 不量化就等于**每帧重画一遍大字**。
#    量化到 20 档: 0.81s 的淡出分成每 40ms 一档、每档 5% alpha —— 配着同一时刻的上浮与缩放,
#    肉眼分辨不出台阶; 代价从"每帧一次"降到"一次演出 20 次"。
#    调小它 → 台阶可见; 调大它 → 白花性能(超过 ~24 档对 0.8s 的淡出已无意义)。
BIG_TEXT_ALPHA_STEPS = 20

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
HOLD_BASE = 0.6        # 装满后的**最短停留**: 这之前点击无效, 过了才受理(见 request_close)
# ⚠️ 2026-09-12 起**不再按倍率分档**(原来是 x2 0.6 -> x100 1.2, "大奖多看一会儿")。
#    玩家定稿: 装满后由**玩家点击**才退场, 这个 0.6s 只当"最短停留"用。
#    `HOLD_TIERS` / `HOLD_STEP` 已删 —— 别因为"想给大奖多一点"再加回来, 那会和点击逻辑打架。
RESULT_FADE = 0.25     # 可见的离开(**恒定, 不随档位变** —— 离开的手感要一致)
# 硬兜底: 超过这个时长无条件解锁。⚠️ **只覆盖 pending / win**(球还在进场/下落那两段,
# 玩家没有话语权, 卡住必须放手)。**result 段(装满静止)不设任何时间兜底** —— 玩家
# 2026-09-12 定稿"不点就一直在", 定时退场会在切后台回来那一帧把杯子当场擦掉。
# 见 `busy()`。最长非交互段 ≈ WINDUP + 最后一颗落定 ≈ 5~6s, 9s 绰绰有余。
FX_MAX_SEC = 9.0

def hold_for(m):
    """装满后的**最短停留**(秒) —— 全项目唯一真源。

    ⚠️ 参数保留但**不再使用**: 2026-09-12 起统一 0.6s, 不再按倍率分档(见 HOLD_BASE 处)。
       签名不改是因为 5 个消费者 + 6 条探针判据都写的是 `hold_for(n)`, 改名会牵一大片。
    """
    return HOLD_BASE

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
# 前移上限 0.55 -> 0.80。发起因由: 发牌改成随机拓扑序之后, **第一颗球不再是最远那颗**
# —— 它现在是随机挑的一颗"没有被谁压着"的球, 也就是底层球, 而底层球的下落路径最长
# (从可见顶边之上一直落到杯底), 入画必然更晚。实测 min(t0+f_enter) 从约 0.67 涨到
# 0.73~0.80(x2/x3 两档), 0.55 的上限就被封顶了 -> 首球要等 0.25~0.29s 才露头, 比设计的
# 0.12 晚一倍多。放开上限让"首球 0.12 露头"这条设计意图重新成立。
# ⚠️ 安全性不靠这个常数: shift = min(RAIN_SHIFT_MAX, min(t0+f_enter) - RAIN_ANCHOR),
#    恒有 shift < min(t0+f_enter) -> t=0 时没有任何一颗球已经入画(凭空出现在半空)。
# ⚠️ 影响面只有原本就封顶的档: n>=5 的 s 都不到 0.55, 加不加一个字都不变; x2/x3 整场
#    提前约 0.13s(_last_settle 1.21 -> 1.08), 尾巴的揭晓/退场跟着一起提前, 无副作用。
RAIN_SHIFT_MAX = 0.80
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
# 启动预热要"碰一次"的字号: 大字 sp(36)/sp(48) + 飘字 sp(26)/sp(30)。
# ⚠️ **必须懒算**: `sp()` 读当时的窗口密度, 而模块导入时窗口还没建 —— 在这里直接
#    写 `sp(36)` 会拿到错误的密度(真机上就是"预热了一堆没人用的字号")。
# ⚠️ **一次只碰一个**(见 prebake_step 的字体预热那段): 4 个挤一帧 = 一帧 100ms+。
_FONT_WARM_SIZES = None
_FONT_WARM_HUD = ()      # [(字号, bold)] —— HUD 基准 x 阶梯, 见 prebake_step
# GC 冻结计数(gc.freeze() 之后为永久代里的对象数; 0 = 还没冻结)。只给跑分面板显示用 ——
# 让玩家/后来的人一眼看出"这版的 GC 冻结到底跑没跑", 而不是靠猜(本仓库栽过静默失效)。
# [冻结的对象数, 冻结**前**强制全量回收一次要多久(毫秒), 冻结**后**同一个动作要多久]
# 后两格是 v0.6.75 加的 —— 桌面实测**量不出**冻结的收益(桌面 30 秒只有 0.6ms 的 GC, 全是
# 零头), 所以只能让 App 在**真机上**自己量: 启动时冻结前后各强制做一次 gen-2 全量回收,
# 把两个数打进跑分面板。判据一眼可见: "全量回收 26.8 -> 0.0 毫秒"。
_GC_FROZEN = [0, 0.0, 0.0]
# 启动预热链是否**全部**跑完(球纹理/字形/球堆/GC 冻结)。
# ⚠️ 为什么跑分要等它: 采样窗口约 25 秒(`_target_launches = 5`), 而玩家是启动后 3 秒
#    就长按标题开跑的 —— 真机上一个预热单步要 100~200 毫秒(桌面只要 16.6), 常常还没跑完。
#    混进采样里会把 1%Low 压下去, 而且量到的是**启动期**的数, 不是玩家平时玩的数。
#    等它跑完再采样, 才是"稳态"的成绩。(桌面实测预热链 11 步、约 2.2 秒; 真机更久。)
_PREBAKE_DONE = [False]
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
_PILE_ORDER = []        # 插入序 —— 见 _pile_projected: 这是 **FIFO 淘汰序, 不是 LRU**
# ⚠️ 上限 12 装不下 7 档 × 4 变体 = 28 个 key。2000 局(真实中奖构成)实测冷建率:
#      cap=12 -> 20.3% / 24.1% / 25.4%(80档 / 360档 / 混档)
#      cap=32 ->  3.3% /  1.2% /  2.2%
#    代价为什么大: x100 冷建桌面 18.7ms(安卓按 3~8 倍估 60~150ms), 而 `_pile_projected` 是在
#    play_win 里**同步**执行的 —— 正好卡在 settle 那一帧, 压住 WINDUP 本该给玩家看的
#    槽位白闪/绿灯。实测 14~26% 的中奖局会撞上。
# ⚠️ **它和预烘是两件事, 别只改一处**: 上限修"复发"(玩久了被 FIFO 挤掉), 预烘修"最坏那一帧"
#    (x100 的首次冷建)。2×2 网格实跑(cap × 预烘范围)证实只改一个各修一半:
#      cap12 + 只烘 (n,1) = 19.6%   |  cap32 + 只烘 (n,1) = 0.5~3.3%
#      预烘全 28 key(cap 不限) = 0% |  cap32 + 全预烘     = 0%
_PILE_CACHE_MAX = 128
# ⚠️ 4 -> 12。玩家的反馈: "感觉还是几个枚举, 不够随机" —— 4 个变体意味着 `_seq % 4`
#    玩 4 局就循环一遍, 而且"层间注册 / 摆法 / 旋转"三个维度被绑死在同一个号码上,
#    等于把组合数从 (12 序列 x 12 摆法) 压成了 4。现在拆开: 每个变体号独立派生三者。
#    内存代价: 7 档 x 12 = 84 个 key, x100 单表 40KB ⇒ 约 1MB(x100 占 646KB), 可接受。
#    预烘代价: 84 个 key 分帧烘(每次 0.03s 间隔), 启动后台跑完约 4 秒 —— 不阻塞界面。
_PILE_VARIANTS = 12
# 层间注册序列(A=0 B=1 C=2): 4 层共 3*2^3 = 24 个合法序列(相邻两层不能同位),
# 实测其中 12 个装得下 100 颗(另 12 个会触发缩球兜底)。
# 下面是和 _PILE_QUOTA 配好的一对 —— 每组都是"序列 + 配额"的联合搜索产物, 别单独换其中一个。
_PILE_SITES = ((2, 1, 2, 0), (0, 1, 0, 2), (0, 1, 2, 0), (0, 2, 0, 1), (0, 2, 1, 0),
               (0, 1, 2, 0), (0, 1, 2, 0), (0, 1, 2, 0), (0, 2, 0, 1), (0, 2, 1, 0),
               (0, 1, 2, 0), (2, 1, 0, 2))
# 每层的**颗数配额**(大档用): 上限, 填够就收手, 剩下的球流到上一层, 总颗数不变。
# 为什么需要它: 大档(x50/x100)是把杯子**填满**的堆, 光靠层间横移轮廓几乎不动 ——
#   实测 12 组"只换序列"的两两 Hausdorff 中位只有 0.47~0.55 球径(小档是 0.59~1.05)。
#   配额改的是"哪层多、哪层少" ⇒ 层接缝的位置和每层的明暗台阶全变, 那是屏幕上真看得见的。
# 这 12 组是在 12 序列 x 各层 ±4 的 84 个合法组合里, 按"层结构指纹两两最远"贪心挑的
#   (两两最小距离 2.32, 自然堆与自身是 0); 每一组都实测 x100 与 x50 都能装下、不缩球。
_PILE_QUOTA = ((18, 26, 25, 31), (17, 26, 31, 26), (19, 26, 26, 29), (19, 26, 29, 26),
               (19, 24, 26, 31), (19, 26, 24, 31), (18, 25, 26, 31), (17, 26, 26, 31),
               (18, 25, 31, 26), (19, 26, 25, 30), (19, 25, 26, 30), (18, 26, 30, 26))
# 旋转角: 黄金角步进(137.5 度)而不是均分 —— 相邻两个变体的角度差永远是"最不重复"的,
# 不会出现"变体 0 和变体 6 差 180 度所以看起来是一对"这种巧合。
# ⚠️ 它**单独用是无效的**(只改相位不改形状, 见下), 现在是配合层间注册/摆法一起用。
_PILE_ROT_STEP = 137.508

def _r_dp_for(n):
    """球半径(dp)。**所有档同一个值** —— 玩家 2026-09-11 定稿: 「球一样大」。

    ⚠️ 原来这里是**按颗数分级**的(26/24/22/23/28), 那套值不是审美选的, 是"容量上限"的产物:
    `build_pile` 装不下就自动缩 6% 重试, 所以每档的球径实际被卡在"该档刚好能装进杯口以下"的
    那个尺寸上 —— 结果就是**中得少的档球反而大**(x2 是 48.4, x100 只有 46.0), 玩家看着别扭。
    真正该修的不是这张表, 是 `pile3d._enumerate` 里那个"每层只填 89% 位置"的松量(已改, 见
    该处注释)。松量修掉之后容量上来了, 就**不需要再分级**: 27dp 实测让
    x2/x5/x10/x50/x100 全部落在同一个 r=50.2, 一次缩球都不触发。

    ⚠️ 唯一例外是 **x20**: 它走的是"土丘"堆法(count<35), 那条路的容量比"一坛子"小,
    装不下 50.2 的球, 兜底缩到 r=44.4 —— 这是它的上限, 不是没调好。要它跟别的档一样大,
    只能改堆法阈值(把 20 也划进"一坛子"), 那是另一件事。
    """
    return 27.0

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

_GLASS_OVER = [None, False]     # [over 纹理, 是否试过]


def _glass_over():
    """远侧环独立层 `glass_tumbler_over.png` —— **它是这条接缝的正式修法**。

    生成器 `generate_glass_tumbler.py` 179..190 的注释: 后半环画在 `over`(压暗之后),
    颜色统一成 HIGHLIGHT, al**只在 alpha 上从顶部 62 渐变到分界处 80 —— 与前半环的 80
    严丝合缝** ⇒ 前后半环在切缝处**接得上**。
    ⚠️ 2026-09-14「适配当前基线」把这一层摘掉了(连 fx_probe 那两条断言一起), 于是
       设计稿里的硬切又露出来 —— 玩家报的「还是有接缝」就是它。
    ⚠️ 必须**原样画**(带贴图自带的 alpha), 不许再乘一个整体 alpha: 它是一道**更白的
       镜面高光**而不是「更亮的 back」, 按 α=1 整幅画会 −9% 反向过冲。
    """
    if _GLASS_OVER[1]:
        return _GLASS_OVER[0]
    _GLASS_OVER[1] = True
    try:
        _a = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        _t = _CoreImage(os.path.join(_a, "glass_tumbler_over.png")).texture
        _t.mag_filter = "linear"
        _t.min_filter = "linear"
        _GLASS_OVER[0] = _t
    except Exception:
        _GLASS_OVER[0] = None
    return _GLASS_OVER[0]


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

def _beads_from_baked(count, v):
    """查离线烘的球堆坐标表(tools/android_part_piledata.py)。命中返回 beads, 否则 None。

    ⚠️ 这只是**快路**, 不是唯一路径 —— 表缺项/损坏时必须安静地返回 None 让调用方回退到
    程序化生成。表坏了的表现如果是"那一局没有球", 那就是线上事故(演出空转、9 秒兜底)。
    ⚠️ 表里存的是**球心坐标**, 不是投影结果 —— 投影仍走 project_pile, 这样以后改
    K2/CX/FLOOR_Y 不用重烘。
    """
    s = _PILE_BAKED.get((count, v))
    r = _PILE_BAKED_R.get(count)
    if not s or r is None:
        return None
    sc = float(_PILE_BAKED_SCALE)
    out = []
    for item in s.split(";"):
        try:
            x, z, h = item.split(",")
            out.append({"x": int(x) / sc, "z": int(z) / sc, "h": int(h) / sc,
                        "layer": 0, "r": r})
        except (ValueError, TypeError):
            return None                       # 任何一项解不开 -> 整表当坏, 走回退
    return out if len(out) == count else None

def _support_map(proj, r):
    """算"谁必须先落"的偏序: 对每颗球 j 找出所有 h 更低、且水平距 < 2r 的球 i。

    为什么这是"必须先落"的充要近似: 球 j 从画面上方落到自己的位置 h_j, 途中会经过
    **所有比 h_j 高的高度** —— 包括 i 所在的 h_i(h_i < h_j)。只要两者的**水平**距离
    小于一个球直径, j 的下降轨迹就会切进 i 的球体。所以 i 必须先落定, j 才有路。

    ⚠️ 判据必须是**水平**距离, 不是 3D 距离。用 3D 距离(≤2r+ε)会漏掉"隔一层"的球
    —— 层距 1.633r 让隔层球的 3D 距离 ≥ 1.633r·... 实测那样会漏掉约 2/3 的阻挡关系,
    按那个图跑出来的顺序比不改还差(专家实测 x100 一局 PEN 708, 是"什么都不做"的 34 倍)。

    ⚠️ 还有一个坑: 球堆有 ±9px 抖动, 所以**不能**用"接触"/"距离≈2r"当判据 ——
    抖开之后会有球找不到任何支撑(实测 x100 v0 有 22 颗"无支撑", 而底层只有 18 颗)。
    用 2r 的**水平**阈值天然免疫这个问题。

    返回 {proj 下标: [前驱 proj 下标, ...]}, 只含非 meta 项。
    """
    ms = [k for k, p in enumerate(proj) if not p.get("meta")]
    xs = [proj[k]["sx"] - CX for k in ms]
    zs = [proj[k]["z"] for k in ms]
    # h 从投影反推(project_pile 的 sy = FLOOR_Y - h - K2*z), 不依赖 beads 的 layer 字段:
    # 离线烘焙表的 "layer" 恒为 0(见 _beads_from_baked), 按它分层会静默失效。
    hs = [FLOOR_Y - proj[k]["sy"] - K2 * proj[k]["z"] for k in ms]
    thr2 = (2.0 * r) ** 2
    out = {}
    for j in range(len(ms)):
        xj, zj, hj = xs[j], zs[j], hs[j]
        pre = []
        for i in range(len(ms)):
            if hs[i] >= hj:
                continue
            dx = xs[i] - xj
            dz = zs[i] - zj
            if dx * dx + dz * dz < thr2:
                pre.append(ms[i])
        out[ms[j]] = pre
    return out

def _topo_deal(proj, rng):
    """随机拓扑序: 每次从"支撑已全部发牌"的球里**均匀随机**挑一颗, 返回 proj 下标排列。

    为什么不是"按层发牌": 层的先后只是这个偏序的一个**保守特例**(把整层当一块),
    代价是屏幕上出现"一层一块"的施工缝 —— 把"永远先内后外"换成"永远先下后上",
    在玩家眼里还是同一个固定套路。拓扑序允许"这层角上先落、中间后落", 才是真随机。

    复杂度 O(n+e); 偏序图由 _support_map 算好挂在 proj 上(缓存), 这里只跑一次 Kahn。
    """
    ms = [k for k, p in enumerate(proj) if not p.get("meta")]
    n = len(ms)
    if n == 0:
        return []
    pos = {m: t for t, m in enumerate(ms)}
    indeg = [0] * n
    succ = [[] for _ in range(n)]
    for m in ms:
        t = pos[m]
        for i in (proj[m].get("sup") or ()):   # 前驱(更低的球)必须先发
            ti = pos.get(i)
            if ti is None:
                continue
            succ[ti].append(t)
            indeg[t] += 1
    ready = [t for t in range(n) if indeg[t] == 0]
    out = []
    while ready:
        k = rng.randrange(len(ready))          # 均匀随机挑一颗 -> 随机性全在这里
        t = ready[k]
        ready[k] = ready[-1]                   # 交换删除, O(1)
        ready.pop()
        out.append(ms[t])
        for u in succ[t]:
            indeg[u] -= 1
            if indeg[u] == 0:
                ready.append(u)
    if len(out) != n:
        # 有环(只可能来自坏数据) -> 安静退回画家序, 绝不锁死演出(见本模块"绝不软锁"那条)
        return ms
    return out

def _pile_projected(count, seed):
    """(count, seed) -> 投影绘制表, 带缓存。同 (count,seed) 逐球心一致可复现。

    ⚠️ 淘汰策略是 **FIFO(插入序), 不是 LRU** —— 命中分支只 `get` 就返回, 从不把 key 挪到
    `_PILE_ORDER` 末尾。所以"单档工作集只有 4 个变体, 12 格够用"这个推理是错的:
    一局的落格倍率取值是 {2,3,5,10,20}(80% 档实测, 5000 局), 每个倍率 4 个变体
    ⇒ **单档工作集最多 20 个 key**, 12 格在单档内就不够。
    (不必为此改成真 LRU: cap=128 之后 FIFO 的冷建率已降到极低, 提上限就够了。)

    坐标来源: **优先查离线烘的表**(tools/android_part_piledata.py, 由 bake_pile_data.py
    生成), 查不到再现场跑 build_pile。两条路径产出的球心坐标由 fx_probe 的
    "表 vs 现算逐位对拍"门禁钉住, 不许漂移。
    """
    key = (count, seed % _PILE_VARIANTS)
    proj = _PILE_CACHE.get(key)
    if proj is None:
        beads = _beads_from_baked(count, key[1])
        if beads is not None:
            proj = project_pile(beads)
        else:
            # ---- 回退路径: 现场生成(表缺项/损坏时走这里, 一行都不要删) ----
            # ⚠️ build_pile 容量兜底时会原地缩小 spec.r, 所以每局都新建 spec, 不复用实例
            _v = key[1] % _PILE_VARIANTS
            spec = PileSpec(count, r_dp=_r_dp_for(count), seed=key[1],
                            rot_deg=(_v * _PILE_ROT_STEP) % 360.0,
                            sites=_PILE_SITES[_v % len(_PILE_SITES)],
                            scatter=_v,
                            quota=_PILE_QUOTA[_v % len(_PILE_QUOTA)])
            beads, meta = build_pile(spec)
            proj = project_pile(beads)
            proj.append({"meta": True, "H": meta["H"], "R": meta["R"]})
        # 支撑偏序图只跟几何有关 -> 挂缓存上算一次; 运行期发牌只跑一遍 Kahn(O(n+e))。
        # 放在缓存构建里而不是 _make_balls 里: 那是 settle 那一帧, 而那一帧有别的活要干
        # (槽位白闪/绿灯), 不能塞 10^4 次距离计算进去。
        if proj:
            _r0 = proj[0].get("r")
            if _r0:
                for _k, _pre in _support_map(proj, _r0).items():
                    proj[_k]["sup"] = _pre
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
        self._font_prebaked = 0          # prebake_step 的进度(已烘过几个字号, 见 _FONT_WARM_SIZES)
        self._vib_prebaked = False       # prebake_step 的一次性开关(震动线程+系统服务代理)
        self._balls = []
        # ---- 持久指令表(2026-09-14): 见 `_redraw` / `_tbl_build` 顶上那几段说明 ----
        # `_tbl_ok` = 表还能用; 另外三项是**重建判据**(球列表身份 / 三张贴图 / 控件尺寸)。
        # ⚠️ 球列表必须比**对象身份**: 新一局长度可能一样(8 颗换 8 颗), 只比长度会拿上一局的球继续画。
        self._tbl_ok = False
        self._tbl_balls = None
        self._tbl_tex = None
        self._tbl_wh = None
        self._fx_pre = []
        self._fx_post = []
        self._bd = []
        self._value = DEFAULT_BET
        self._seq = 0
        self._rng = random.Random()
        self._t0 = 0.0                    # 杯子该出现的时刻(settle + WINDUP)
        self._rain_shift = 0.0            # 本局雨的整场前移量(见 RAIN_ANCHOR), expected_sec 要用
        self._last_touch = 0.0            # 最后一颗球**第一次触地**(相对 _t0) —— 揭晓基准
        self._last_settle = 0.0           # 最后一颗球**回弹停住**(相对 _t0) —— 退场计时用
        self._done_fired = False          # 揭晓是否已放出去(它比退场早, 要各自触发一次)
        self._settled_at = 0.0
        self._deadline = 0.0
        # 退场由**玩家点击**触发(2026-09-12 定稿): `_closing_at` = 收到关闭请求的墙钟时刻,
        # 0 = 还没有(此时画面停在"装满静止")。`_auto_close` = 这一局不等点击、到点自己走
        # —— **只给跑分用**(跑分是自动连续发射的, 等人点击会把整轮跑分卡死)。
        self._closing_at = 0.0
        self._auto_close = False
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
        self._result_busy = True          # 非 result 态一律照常重绘(见 tick 里那段说明)
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
        """本档预计总时长(秒)。给 RootWidget 延长 _result_until(语音抑制窗口)用。

        ⚠️ 它算的是"**最短**上屏时长" = 进场 + 落定 + HOLD_BASE + RESULT_FADE, 不再是
           整场时长 —— 2026-09-12 起退场由**玩家点击**触发(result 段可以无限长)。
           唯一消费者是 `_result_until`, 作用是"别让 set_bet/set_rtp 的 UI 语音插嘴";
           而那段里按钮本来就是 disabled + 输入锁定, 所以窗口长短不影响正确性。
        ⚠️ 另注(既有缺陷, 不是本次引入): `settle()` 里调 expected_sec 的时刻**早于**
           play_win(后者被 CUP_TRIGGER_DELAY=0.15s 延后调度) ⇒ 线上实际走的是下面那个
           **估算**分支, 不是"本局真实球数据"。要修是把 _result_until 的赋值挪进
           _start_cup, 属于另一个改动 —— 别在这上面加"必须读真实球"的新假设。
        """
        # ⚠️ 以前这里按 FALL_MAX 估, 注释写着"是 T 的上界" —— **那句不成立**:
        # _make_balls 给每颗球的落袋时长带了 x u(0.88,1.12) 的随机, 可以超过 FALL_MAX。
        # 实测 2000 局/档, x100 有 3.9%、x50 有 1.6% 的局里 est < 真实总时长(最大超出 3.4%)。
        # 后果是兜底 deadline 会在最后一颗球落定**之前**触发 -> 大字/余额/语音提前冒出来,
        # 正是当初把揭晓挪到 T 想消灭的"数字提前剧透", 只是概率低没人发现。
        # b["end"] 已经是前移过的值, 所以**不要**在这里再减一次 _rain_shift。
        n = max(1, int(multiplier))
        if self._balls:
            # 整场时长按"退场起点"(最后一颗回弹停住)算, 不是揭晓那个(第一次触地)——
            # 退场动画的时钟挂在 _last_settle 上, 基准跟它走才自洽。
            tail = self._last_settle + 0.05    # 0.05 留一帧, 免得兜底和真事件同毫秒打架
        else:                                                   # 还没排上(球堆异常): 退回估算
            iv = max(SPAWN_MIN, min(SPAWN_CAP, SPAWN_WINDOW / n))
            tail = SPAWN_TOP + (n - 1) * iv + FALL_MAX + 0.33
        return WINDUP + tail + hold_for(n) + RESULT_FADE

    def play_win(self, multiplier, bet, on_done=None, auto_close=False):
        """排定一场中奖演出。返回 False 表示没排上(调用方照常解锁)。

        失败绝不能影响结算 —— 余额这时已经加过了, 这里只是表演。

        `auto_close`: 装满后**不等玩家点击**、到点自己退场。**只给跑分用** —— 跑分是
        自动连续发射的, 等人点击会把整轮跑分卡死(玩家 2026-09-12 定稿)。
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
            # ⚠️ 跑分: **装杯内部的下落时序也要确定**(2026-09-14, 玩家提的)。
            #    实测: 球路钉死之后 `飞行 959/958 · 落袋 58/58` 已经逐帧吻合, 但
            #    `装杯 571/545` 还差 26 帧 —— 那正是下面 `_make_balls` 里
            #    "每颗球一套独立随机的下落/回弹参数"(用户原话: "一致=假")。
            #    种子按**倍率**派生: 跑分那 5 发的倍率是钉死的 ⇒ 每次跑分每一发的杯子完全一样。
            #    ⚠️ `auto_close` 就是"这是跑分"的标志(界面在跑分时传 True);
            #       **正常游戏照旧随机** —— 那是观感, 不能钉。
            if auto_close:
                try:
                    self._rng = random.Random(BENCH_SEED + 2000 + int(multiplier))
                except Exception:
                    pass
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
        self._closing_at = 0.0            # 新一局: 还没收到关闭请求(画面会停在装满等点击)
        self._auto_close = bool(auto_close)
        self._done_fired = False
        self._deadline = self._t0 + FX_MAX_SEC
        self._on_done = on_done
        self.mode = "pending"
        self._dirty = True
        return True

    def request_close(self):
        """玩家点了一下屏幕, 请求把装杯画面收走。返回 True = 这一下被消费掉了。

        调用方(RootWidget 的 Window 级触摸观察者)拿到 True 就该**早退**, 别再走
        "标题/期望返还比例的长按计时" —— 玩家点的是"我看够了", 不是 HUD 上的长按热区。

        ⚠️ 只有"装满静止**且**过了最短停留"才受理。在那之前点击必须无效, 否则刚装好的
           画面会被手快的玩家点掉(玩家 2026-09-12 定稿)。球还在往下落(mode != "result")
           时同样不受理 —— 那正是"没有下落完成时点击无效"这条。
        ⚠️ 基准用 `_settled_at`(**墙钟**, tick 里写进去的)而**不是** `_last_settle`
           (相对 `_t0` 的秒数) —— 只有前者能和 `time.time()` 比。
        """
        if self.mode != "result" or self._closing_at:
            return False
        if time.time() < self._settled_at + HOLD_BASE:
            return False
        self._closing_at = time.time()
        return True

    def busy(self):
        """锁输入判据。

        ⚠️ 硬兜底**只覆盖 pending / win**(球还在进场/下落那两段) —— 那时玩家没有话语权,
           状态机卡住必须放手, 否则是真软锁。`FX_MAX_SEC=9s` 对那两段绰绰有余
           (最长非交互段 ≈ WINDUP + 最后一颗落定 ≈ 5~6s)。

        ⚠️ **result 段(装满静止)故意不设时间兜底**: 玩家 2026-09-12 定稿"不点就一直
           在", 任何定时退场都是产品违约 —— 而且它会在玩家切后台一分钟后回来时, 把杯子
           和整屏压暗**当场擦掉**。出口只有 `request_close()`, 它挂在 Window 级触摸观察者
           上(每一下触摸都会到, 比任何算术都可靠)。
        """
        if self.mode == "idle":
            return False
        if self.mode in ("pending", "win") and time.time() >= self._deadline:
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
        self._closing_at = 0.0             # 清干净: 别留半局状态给下一局
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
        # 发牌顺序 = 随机拓扑序(见 _topo_deal): 每颗球发出时, 它正下方的球都已发出。
        # "i" 决定 t0(见下面按 i 递增排的那段), 所以这里是**顺序的唯一真源**;
        # project_pile 的画家序(远先近后)与 _balls 的 z 序(绘制用)都不受影响。
        # ⚠️ 原来这里是 `for p in proj`, 而 proj 是按 z 降序排的 -> 每局、每个变体
        #    都是"从杯子最里侧扫到最外侧", 玩家报的"总是先内后外"就是它。
        for _k in _topo_deal(proj, self._rng):
            p = proj[_k]
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
        self._result_busy = True          # 非 result 态一律照常重绘(下面 result 分支会覆盖)
        if self.mode == "result":
            # 尾巴: 没弹完的球继续把弹跳播完(用户定稿"球继续弹, 只提前 T")。
            # ⚠️ 这一段**不能停** —— _ball_screen 对未落定的球是按 tt 插值的, 不推进的话
            # 它们会冻在半空(而不是落到堆里)。
            _was = sum(1 for b in self._balls if not b["settled"])
            self._advance_balls(now - self._t0)
            _unst = sum(1 for b in self._balls if not b["settled"])
            # "这一帧画面会变吗": 还有球在动 ⇒ 会; **有球刚停稳** ⇒ 也会
            #   (停稳那一帧它从 tt 插值位置跳到终值, 少画一次就会留在旧位置)。
            self._result_busy = (_unst > 0) or (_unst != _was)
            # 退场**不再自动** —— 等玩家点击(见 request_close)。**跑分期间例外**: 跑分是
            # 自动连续发射的, 等人点击会把整轮跑分卡死(玩家 2026-09-12 定稿), 那一路仍到点自己走。
            if (not self._closing_at and self._auto_close
                    and now >= self._settled_at + HOLD_BASE):
                self._closing_at = now
            if self._closing_at and now >= self._closing_at + RESULT_FADE:
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
        # ⚠️ **装满静止、还没开始退场时, 画布一个像素都不会变, 就别重建它**
        #    (2026-09-14 中风险优化, 由真机数据推动):
        #    `_redraw` 在 x100 时要重建 **684 条指令**(桌面实测 0.79ms/次 ⇒ 真机约 2.8ms,
        #    占一帧 22%), 而"等玩家点击"那段可能持续好几秒 —— 那几秒里每帧都在白烧。
        #    条件里带上 `_dirty` 是为了**只多画不少画**: 任何真心需要重画的状态变化都会置它。
        # ⚠️⚠️ **`_closing_at` 必须单独判**(踩过一次, 是探针抓出来的):
        #    退场那 0.25s 每帧都在淡出 + 上浮, **必须逐帧重绘**。我第一版想当然地以为
        #    `_result_busy` 已经涵盖了它 —— 并没有, 那个旗子**只看球有没有在动**。
        #    少判这一下的后果实测: 点击后 0.5 秒里只重绘了 **1 次**(应为约 30 次),
        #    整个淡出动画被掐成"啪一下消失"。
        if (self.mode == "result" and not self._dirty and not self._result_busy
                and not self._closing_at):
            return
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
        # result: 退场 —— 从**玩家点击那一刻**起算(见 request_close; 跑分走 _auto_close)。
        # ⚠️ 点击之前一律返回"装满静止态", **dy 必须是 0** —— 否则整组会停在"上浮过的"
        #    位置上, 那是退场终态的姿势、不是装满的姿势。
        # ⚠️ 原来这里从 `_settled_at + self._hold` 起算; 改成点击触发之后那个基准不存在了,
        #    别把它加回来(加了就会在等待期里按"已退场"渲染)。
        if not self._closing_at:
            return 1.0, 1.0, 1.0, 0.0
        td = now - self._closing_at
        u = max(0.0, min(1.0, td / RESULT_FADE))
        # 位移/缩放: 二次缓出 —— 运动必须发生在还看得见的时候(见 EXIT_LIFT 处的说明)
        u_pos = 1.0 - (1.0 - u) ** 2
        # 透明度: 线性, 且在解锁前 EXIT_ALPHA_TAIL 就归零
        u_a = max(0.0, min(1.0, td / (RESULT_FADE - EXIT_ALPHA_TAIL)))
        # 压暗层早 EXIT_DIM_LEAD 撤 -> 灯先亮回来, 道具后撤走
        u_dim = self._eo((td + EXIT_DIM_LEAD) / RESULT_FADE)
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

    def _tbl_key_ok(self, back_tex, front_tex, fb_tex):
        """持久指令表还能不能用。见 `_redraw` 顶上那段说明。

        ⚠️ `self._balls` 用**对象身份**判, 不用长度 —— 新一局 `play_win` 换掉整个列表时
           长度完全可能一样(8 颗换 8 颗), 只比长度会拿上一局的球继续画(位置全错)。
        ⚠️ 尺寸也要进判据: 窗口变了 `_abx/_aby/_abw/_abh` 跟着变, 而杯口环那些子纹理
           是按建表时的尺寸切/摆的。
        """
        return (self._tbl_ok
                and self._tbl_balls is self._balls
                and self._tbl_tex == (back_tex, front_tex, fb_tex)
                and self._tbl_wh == (self.width, self.height))

    def _tbl_build(self, back_tex, front_tex, fb_tex):
        """**一次性**建出「固定层 + 球层 + 前层玻璃」的持久指令表。

        ⚠️⚠️ **顺序一个字不许动**(红线 2, 实测出来的):
            接地阴影 < 后层玻璃 < 压暗 < 杯口环补画(48 条) < 弹珠 < 前层玻璃。
            压暗放到后层玻璃之前 ⇒ 杯内只压 12% 而杯外压 46%(杯子周围反而最亮)。
        ⚠️⚠️ **球层必须按 `self._balls` 的原始顺序单趟建**(红线 1, 血泪账):
            改成"落定球 + 飞行球两趟"之后实测 x100 一局 4750 帧里 25.5% 的重叠帧层次错乱、
            落定那一帧约 46 颗球单帧掉一半像素。那个写法当年之所以没出事, 唯一原因是
            "发牌序恰好 = z 降序"这个巧合。**别拆组, 别排序。**
        ⚠️ 不可见的球**不删指令**, 只把 `size` 置 (0,0) —— 指令条数在本局内必须恒定,
           否则每帧增删反而更贵, 还会打乱画家序。
        """
        self.canvas.clear()
        self._fx_pre = []          # 球**之前**的固定层: (kind, Color, Shape, f0, fh, a)
        self._fx_post = []         # 球**之后**的固定层(前层玻璃)
        self._bd = []              # 球层: 每球 (Color, Rotate, Rectangle, ball)
        with self.canvas:
            # ① 堆体接地的软阴影(替代逐球贴球心阴影, 不再放大悬空感)
            self._fx_pre.append(("shadow", Color(0.02, 0.02, 0.03, 0.0),
                                 Ellipse(pos=(0, 0), size=(0, 0)), None, None, None))
            # ② 后层玻璃。⚠️ 分层图缺失时把**整图当后层**用(见原注释: 放到"前层"的位置
            #    会让整张玻璃落在压暗**之上**, 回退局里杯子比正常局亮一大截)。
            _under = back_tex if back_tex is not None else fb_tex
            self._fx_pre.append(("under", Color(1.0, 1.0, 1.0, 0.0),
                                 Rectangle(texture=_under, pos=(0, 0), size=(0, 0)),
                                 None, None, None))
            # ③ 压暗整块游戏区(含底部倍率槽) —— 杯子成为唯一焦点。
            self._fx_pre.append(("dim", Color(DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], 0.0),
                                 Rectangle(pos=(0, 0), size=(0, 0)), None, None, None))
            # ③b **远侧环独立层 `over`**(2026-09-14 接回): 它是生成器设计里**专门修
            #      "前后半环在切缝处接不上"** 的那一层 —— 见 `_glass_over` 处那段证据。
            #     ⚠️ 位置: **压暗之后、补画之前**(它就画在压暗之上, 与生成器的意图一致)。
            #     ⚠️ **原样画**, 不带任何额外 alpha。
            _ov = _glass_over()
            if _ov is not None:
                self._fx_pre.append(("oseam", Color(1.0, 1.0, 1.0, 0.0),
                                     Rectangle(texture=_ov, pos=(0, 0), size=(0, 0)),
                                     None, None, None))
            # ④ 杯口环补画(后半个杯口环 + 杯底环后半, 各 12 段) —— 见 `_rim_back_strips`。
            # ★ **有 `over` 时, 杯口那一段交给它**(2026-09-14 实测定案):
            #   同一次演出只切一个开关, 接缝列逐行 ——
            #     只补画:  88 → **49** → 101        (暗口)
            #     over叠加: 138 → 112 → 101          (亮过头, 峰值 139)
            #     **over 取代杯口补画: 99 → 110 → 111 → 100 → 102 → 100**  ← 既无暗口也不过亮
            #   ⇒ `over` 是生成器设计里专修这条接缝的那一层(见 `_glass_over` 的证据),
            #     补画是后来不知道有它时做的替代品; 两个一起画 = 同一圈环画两遍。
            # ⚠️ **只让 over 接管杯口, 杯底那段仍走补画**: `over` 的杯底环段 alpha 只有 46
            #    (back 同段 71), 整条换过去杯底远半圈会暗掉约 11%。
            # `_rim_back_strips` 返回两段(杯口在前、杯底在后), 各 `RIM_BAND_STRIPS` 条。
            _rst = _rim_back_strips(back_tex) or ()
            if _ov is not None and len(_rst) >= 2 * RIM_BAND_STRIPS:
                _rst = _rst[RIM_BAND_STRIPS:]          # 丢掉杯口那段, 只留杯底
            for _t, _f0, _fh, _a in _rst:
                self._fx_pre.append(("rim", Color(1.0, 1.0, 1.0, 0.0),
                                     Rectangle(texture=_t, pos=(0, 0), size=(0, 0)),
                                     _f0, _fh, _a))
            # ⑤ 球层: **原始顺序单趟**(红线 1)
            # ⚠️⚠️ **每颗球必须自带 PushMatrix/PopMatrix**(红线 4, 2026-09-14 画面事故实修)。
            #    Kivy 的 `Rotate` 是**上下文指令** —— 它作用于**其后所有**指令, 直到被 Pop 掉。
            #    `_draw_bead`(老实现)一直是 `Color/PushMatrix/Rotate/Rectangle/PopMatrix`
            #    五条一组, 而持久指令表的第一版**把这一对漏了** ⇒ 第 2 颗球的旋转叠在第 1 颗
            #    上, 第 3 颗再叠…… 20 颗球就是 20 个旋转的复合。
            #    最惨的不是球本身: **球层后面紧跟着前层玻璃**, 那 20 个未 Pop 的旋转全叠到了
            #    杯子的前层矩形上 ⇒ 整块玻璃被拧飞。玩家原话:「**落杯动画一塌糊涂**」。
            #    ⚠️ 这一条**当时没有门禁覆盖**: 第一版验收探针把两版指令流 dump 出来逐条比,
            #       却报"逐条一致" —— 因为那个探针的 `SRC = sys.argv[1]` **赋了值却从没被用过**,
            #       它永远 `import main`, 等于把同一份文件跟自己比了两遍(假绿灯)。
            #       现在由 `fx_probe [21]` 钉死: Push/Pop 必须**配对**且**包住**每一条 Rotate。
            for _b in self._balls:
                _c = Color(1.0, 1.0, 1.0, 0.0)
                PushMatrix()
                _ro = Rotate(angle=0.0, origin=(0, 0))
                _r = Rectangle(texture=_ball_texture(_b["value"]), pos=(0, 0), size=(0, 0))
                PopMatrix()
                self._bd.append((_c, _ro, _r, _b))
            # ⑥ 前层玻璃(必须在球**之后**)
            self._fx_post.append(("front", Color(1.0, 1.0, 1.0, 0.0),
                                  Rectangle(texture=front_tex, pos=(0, 0), size=(0, 0)),
                                  None, None, None))
        self._tbl_ok = True
        self._tbl_balls = self._balls
        self._tbl_tex = (back_tex, front_tex, fb_tex)
        self._tbl_wh = (self.width, self.height)

    def _tbl_apply_fixed(self, a_cup, a_dim, bx, by, bw, bh):
        """每帧把固定层的**属性**写一遍(不重建指令)。"""
        for _k, _c, _r, _f0, _fh, _a in self._fx_pre:
            if _k == "shadow":
                if a_cup > 0.0:
                    _fx, _fy, _ = self._map(CX, FLOOR_Y)
                    _c.rgba = (0.02, 0.02, 0.03, a_cup * 0.35)
                    _r.pos = (_fx - bw * 0.34, _fy - bh * 0.045)
                    _r.size = (bw * 0.68, bh * 0.09)
                else:
                    _r.size = (0.0, 0.0)
            elif _k == "under":
                if a_cup > 0.0 and _r.texture is not None:
                    _c.rgba = (1.0, 1.0, 1.0, a_cup)
                    _r.pos = (bx, by)
                    _r.size = (bw, bh)
                else:
                    _r.size = (0.0, 0.0)
            elif _k == "oseam":
                # 远侧环独立层: 与 under/front 同款 —— 整幅铺开, alpha 跟着 a_cup 淡入。
                if a_cup > 0.0 and _r.texture is not None:
                    _c.rgba = (1.0, 1.0, 1.0, a_cup)
                    _r.pos = (bx, by)
                    _r.size = (bw, bh)
                else:
                    _r.size = (0.0, 0.0)
            elif _k == "dim":
                if a_dim > 0.0:
                    _c.rgba = (DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], DIM_ALPHA * a_dim)
                    _r.pos = self.pos
                    _r.size = self.size
                else:
                    _r.size = (0.0, 0.0)
            else:                                   # "rim"
                if a_cup > 0.0:
                    _c.rgba = (1.0, 1.0, 1.0, a_cup * _a)
                    _r.pos = (bx, by + bh * _f0)
                    _r.size = (bw, bh * _fh)
                else:
                    _r.size = (0.0, 0.0)
        for _k, _c, _r, _f0, _fh, _a in self._fx_post:
            if a_cup > 0.0 and _r.texture is not None:
                _c.rgba = (1.0, 1.0, 1.0, a_cup)
                _r.pos = (bx, by)
                _r.size = (bw, bh)
            else:
                _r.size = (0.0, 0.0)

    def _tbl_apply_balls(self, a_cup):
        """每帧把球层的属性写一遍。逻辑与 `_draw_bead` 逐字一致, 只是**写进既有指令**。"""
        for _c, _ro, _r, _b in self._bd:
            _al = a_cup
            if not _b["settled"]:
                if _b["tt"] < _b["f_enter"]:        # 还在可见顶边之上: 不画
                    _r.size = (0.0, 0.0)
                    continue
                _al *= max(0.0, min(1.0, (_b["tt"] - _b["f_enter"]) / ENTER_FADE))
                if _al <= 0.0:
                    _r.size = (0.0, 0.0)
                    continue
                _sh = min(1.0, _b["shade"] + 0.10)  # 飞行球提亮一档, 与堆里的区分开
            else:
                _sh = _b["shade"]
            _scr = self._ball_screen(_b)
            if _scr is None:
                _r.size = (0.0, 0.0)
                continue
            _x, _y, _rx, _ry, _ang = _scr
            _c.rgba = (_sh, _sh, _sh, _al)
            _ro.angle = _ang
            _ro.origin = (_x, _y)
            _r.pos = (_x - _rx, _y - _ry)
            _r.size = (_rx * 2, _ry * 2)

    def _tbl_drop(self):
        """作废持久表(并把画布清干净)。

        ⚠️⚠️ **红线 3**: 每次 mode 迁移都必须显式作废 —— 少了它, 退出装杯后
           杯子/压暗会**永久留在画布上**, 而 `canvas.clear()` 是现在唯一能把它们
           擦掉的机制(原来每帧都 clear, 所以这个问题从来没暴露过)。
        """
        if self._tbl_ok:
            self.canvas.clear()
        self._tbl_ok = False
        self._tbl_balls = None
        self._fx_pre = []
        self._fx_post = []
        self._bd = []

    def _redraw(self, *_):
        """装杯演出的每帧绘制。**持久指令表版**(2026-09-14)。

        ⚠️⚠️ 为什么改: 原来是 `canvas.clear()` + **整块重建** —— 固定 56 条(接地阴影 2 +
           后层玻璃 2 + 压暗 2 + 杯口环补画 48 + 前层玻璃 2)每帧都重来一遍, 球层还有
           `5 x N` 条(x100 时整块 684 条)。真机 `板面` 306 帧累计 639 毫秒, 而低90 里
           有三帧(帧5005 / 帧4926 / 帧4934, 板面 2.3~3.4 毫秒)**只超线 0.03~0.23 毫秒**
           —— 把固定层变成"建一次、之后只改 rgba/pos/size"就够清掉它们。
        桌面基线(v0.7.35 拆分实测): 杯层 max 0.71 / 中位 0.32; 球层 max 1.64 / 中位 0.36。
        ⚠️ 三条红线写在 `_tbl_build` 与 `_tbl_drop` 的说明里, 一条都不能碰。
        """
        self._dirty = False
        # 本帧真正画上去的压暗曲线值, 给 HUD 五行读(见 dim_alpha)。**必须在所有早退之前
        # 归零** —— 早退的每一种情形(idle / 尺寸未定 / 两边都透明)都等于"本帧板面没压暗"。
        self._a_dim_now = 0.0
        if self.width <= 1.0 or self.height <= 1.0:
            return
        if self.mode == "idle":
            self._tbl_drop()
            return
        a_dim, a_cup, k, dy = self._layers(time.time())
        self._a_dim_now = a_dim
        if a_dim <= 0.0 and a_cup <= 0.0:
            # 两边都透明 = 本帧不画(进场最前面那几帧 / 退场最后那几帧)。**必须作废**:
            # 留着旧表的话, 下一帧 alpha 回来时画的是上一段曲线的残留。
            self._tbl_drop()
            return
        self._apply_anim_rect(k, dy)
        bx, by, bw, bh = self._abx, self._aby, self._abw, self._abh
        back_tex, front_tex, fb_tex = _glass_textures()
        _t = time.perf_counter()
        if not self._tbl_key_ok(back_tex, front_tex, fb_tex):
            self._tbl_build(back_tex, front_tex, fb_tex)
        self._tbl_apply_fixed(a_cup, a_dim, bx, by, bw, bh)
        _brk_add("杯层", _t)
        _t = time.perf_counter()
        self._tbl_apply_balls(a_cup)
        _brk_add("球层", _t)

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
            try:
                # 远侧环独立层一起烘(1600x920 的 PNG, 现场解码是一记长帧)。
                _glass_over()
            except Exception:
                pass
            Clock.schedule_once(self.prebake_step, 0.05)
            return
        # ---- 字体预热(2026-09-13 补) --------------------------------------------------
        # ⚠️ 为什么必须做: `fit_font_size()` 走 `text_px()` 新建 CoreLabel 再光栅化, 而
        #    **每碰上一个新字号都要重新打开一次 TTF 字形表** —— 实测桌面 **26ms**,
        #    且**换文本不重付、换字号才重付**(同一个字号上量第二串文本只要 0.05ms)。
        #    界面其它文字是 13~22sp, 启动布局时就顺手烘热了; 而**大字用的 sp(36)/sp(48)、
        #    飘字用的 sp(26)/sp(30) 在第一次落袋之前从来没人用过** ⇒ 冷启动后的**每一次
        #    落袋**都要现开一次字形表。
        #    实测归因(桌面, 逐帧计时): 最慢帧 28.8ms 里 `big_result_text` 占 **26.7ms**,
        #    而 `big_result_text` 里 `fit_font_size` 占 26.0ms —— 尖峰就是这一处。
        #    对得上玩家报的"1% low 6fps"(167ms/帧, 真机上字形表更贵)。
        #    ⚠️ 这一条**正是本方法存在的理由**(见上面玻璃贴图那段): "别在中奖那帧现做"。
        #    ⚠️ 预热的是**字号**不是文本 —— 所以只要把这几个字号各碰一次就够, 之后不管
        #    出 "+2" 还是 "+500000" 都是 0.05ms。`_FIT_PX` 清空也冲不掉它: 那 26ms 的开销
        #    记在 Kivy 自己的按字号字体缓存里, 不在 `_FIT_PX` 里。
        if not self._vib_prebaked:
            self._vib_prebaked = True
            try:
                _vib_warm()
            except Exception:
                pass
            try:
                # 守卫工作线程 + 沉浸 Runnable 一起焐热(理由同震动: 别在采样窗口里现建线程)。
                # ⚠️ Runnable **必须在主线程上建**(它要注册 Java 代理)。
                _guard_warm()
            except Exception:
                pass
            Clock.schedule_once(self.prebake_step, 0.05)
            return
        _FRAME_PROBE[2] += 1      # 本帧跑了预热(供跑分面板把"启动慢"与"玩起来卡"分开)
        # ---- 文字纹理预热(2026-09-14 加, 见 `_build_texwarm` 处说明)----
        # ⚠️ 排在字号预热**之前**: 这一条才是真正消掉 `填纹`(真机 4~10.7 毫秒/次)的那一步,
        #    字号那一条只烘第一趟(量宽高), 两趟里贵的那一趟它从来没碰过。
        # ⚠️ 一帧只走一个组合 —— 和字号预热同一个理由(挤在一帧就是自己造一记长帧)。
        if not getattr(self, "_texwarm_done", False):
            if getattr(self, "_texwarm_list", None) is None:
                self._texwarm_list = _build_texwarm(getattr(self.area, "game", None),
                                                  getattr(getattr(self.area, "game", None), "bet", None))
            _tw_i = getattr(self, "_texwarm_i", 0)
            if _tw_i < len(self._texwarm_list):
                self._texwarm_i = _tw_i + 1
                try:
                    _warm_one(self._texwarm_list[_tw_i])
                except Exception:
                    pass
                Clock.schedule_once(self.prebake_step, 0.05)
                return
            self._texwarm_done = True
        if _FONT_WARM_SIZES is None:
            # 首次走到这里才按**当时的窗口密度**算(模块导入时窗口还没建, 那时 sp() 是错的)。
            # ⚠️ **必须用 `_qfs` 落同一个网格**(2026-09-14 修一个我自己引入的回归):
            #    `fit_font_size` 现在探的第一个档是 `_qfs(base*1.0)`, 而这里原来烘的是**裸的**
            #    `sp(48)` —— 两个值差最多 0.25px, 而 Kivy 的字体缓存**按精确字号索引** ⇒
            #    预热等于白烘, **每次创建中奖大字都要现开一次字形表**(桌面 26 毫秒, 真机更贵)。
            #    真机证据(0.6.81 均衡模式那份): "65毫秒(落袋·实算56.3·**自算48.8**)" /
            #    "60毫秒(装杯·实算48.7·**自算40.7**)" —— `_frame` 自己一帧烧 41~49 毫秒,
            #    而那两帧正是**创建大字**的时刻。性能模式下同一份面板是 10.8 毫秒(核频高、字形表便宜)。
            # ⚠️ **必须把整个阶梯都烘掉, 不能只烘第一档**(2026-09-14)。
            #    原来只烘 `sp(36)/sp(48)/sp(26)/sp(30)` 四个裸值, 而 `fit_font_size` 是**阶梯**:
            #    第一档放不下就试 0.94 / 0.88 / 0.82 / 0.76 / 0.70, 再不行还有六轮二分 ——
            #    **每一档都是一个新的精确字号, 每一次都要现开一次字形表**(桌面 26 毫秒, 真机更贵)。
            #    而中奖大字的文字是 `+N`, 位数一多(隐藏档 +500000 / x100 的 +10000)第一档就放不下,
            #    必然往下走。而 `big_result_text` 是**在 `_frame` 里面**跑的
            #    (tick_draw -> win_fx.tick -> _pump_reveal -> _reveal_win) ⇒ 那几档冷开**直接记在
            #    `_frame` 头上**。
            #    真机证据(0.6.81 均衡模式): "65毫秒(落袋·**自算48.8**)" / "60毫秒(装杯·**自算40.7**)"
            #    —— `_frame` 自己一帧烧 41~49 毫秒, 而性能模式同一份面板只有 10.8 毫秒
            #    (核频高、字形表便宜)。那两帧正是**创建大字**的时刻。
            #    代价: 预热从 4 步变 24 步(一步 0.05s, 多 1 秒启动期), 而跑分已经会等预热跑完。
            # 把**整个阶梯**都烘掉, 不能只烘第一档: 中奖大字的文字是 `+N`, 位数一多第一档就
            # 放不下, 必然往 0.94/0.88/... 走, 而每一档都是一个新的精确字号、每次都要现开
            # 一次字形表(桌面 26 毫秒, 真机更贵); 而 `big_result_text` 是在 `_frame` **里面**
            # 跑的(tick_draw -> win_fx.tick -> _pump_reveal -> _reveal_win) => 那几档冷开
            # 直接记在 `_frame` 头上。真机证据(0.6.81 均衡模式): "65毫秒(落袋·自算48.8)" /
            # "60毫秒(装杯·自算40.7)"。代价: 预热从 4 步变 22 步(多约 1 秒启动期)。
            # ⚠️ 这里**不落 0.5px 网格** —— 落网格会和布局烘出来的字号对不上, 反而全变冷开
            #    (0.6.80 那个回退的教训)。
            # ⚠️⚠️ **2026-09-14: HUD 的那几个基准字号也要烘**(真机证据见 `_build_texwarm` 上方)。
            #     原来只烘 `sp(36)/sp(48)/sp(26)/sp(30)` —— 那是**大字和飘字**的基准。
            #     而 HUD 的基准是 `_apply_sizes` 写进 `_fit_base` 的那批 `sp(13/14/15/16/18/19) x fs`,
            #     **一个都没烘**。`_fit1` 给 HUD 标签挑字号时走的是同一条阶梯 ⇒ 挑中没烘过的档
            #     就是一次**冷开 TTF 字形表**。
            #     **真机实测: `字号`(`_fit1`)一次 16.5 毫秒** —— 把帧634 从 6.05ms 推到 22.55ms,
            #     单笔就造出一个低于 90fps 的帧。而桌面同一件事只要 0.46 毫秒, **桌面测不出来**。
            #     (`fit_font_size` 一次最多调 12 次 `text_px`: 6 档阶梯 + 二分 6 次 ⇒ 12 次冷开
            #      x 约 1.4ms = 16.5ms, 对得上。)
            # ⚠️ **基准值必须从标签自己的 `_fit_base` 读, 不许手抄 `sp(N)`**:
            #     `_apply_sizes` 写的是 `sp(N) * _font_scale * _ui_scale`, 而 `sp()` 在真机上是
            #     **非整数** —— 手抄的 `sp(N)` 只要差 0.01 就是另一个 fontid, 预热全白做且不报错。
            #     (`_qfs` 那次翻车就是这个形状: 量化后的值和预热的值对不上。)
            # ⚠️ `bold` 也要从标签读 —— fontid 里含 bold, 猜错等于没烘。
            _bases = []
            try:
                _rw = getattr(self.area, "game", None)
                for _n in ("title_lbl", "status_lbl", "mute_btn", "round_btn",
                           "_rtp_title_lbl", "_bet_title_lbl", "_bead_lbl",
                           "balance_lbl", "stats_lbl", "power_lbl",
                           "reset_btn", "fire_btn"):
                    _lb = getattr(_rw, _n, None)
                    _b = getattr(_lb, "_fit_base", None)
                    if _b and float(_b) > 0:
                        _bases.append((float(_b), bool(getattr(_lb, "bold", False))))
            except Exception:
                pass
            _seen = set()
            _hud = []
            # ⚠️⚠️ **2026-09-15 更正: 这里只能烘 `FIT_SCALES`, 不能连 `FIT_FINE` 一起烘。**
            #    上一版我按"全 app 字号 = 基准 x 13 个倍率, 那就全烘上"去做, 结果是
            #    **低90 从 8 帧退回 10 帧**(v0.7.30 -> v0.7.31, 配比可比)。
            #    桌面实测(`temp/font_cache_probe.py` 那套): **Kivy 的 SDL2 字体缓存上限是 64** ——
            #    开过 64 个不同字号之后, **最早那个会被挤掉**(回访时重新变冷, 30 毫秒)。
            #    而 12 个 HUD 基准 + 4 个大字基准 x 13 档 = **155 项**, 早超上限 ⇒
            #    预热**自己把自己挤掉**, 表里明明有、也真的量过(`_WARM_DID` 证实),
            #    运行期一量还是冷(真机日志: `fs=57.2375 [余额] 最近预热档差 0` 就是这么来的)。
            #    ⇒ 只烘 6 档基础阶梯: 12 x 6 + 4 x 6 = 72, 去重后 ≈48, **在上限以内**。
            #    细分档(`FIT_FINE`)只在"连 0.7 倍都放不下"时才会走到, 属于少数;
            #    它们第一次用会冷一次, 之后就被 LRU 留在缓存里了 —— 这比"全烘、结果全被挤掉"好。
            _all_k = FIT_SCALES
            for _b, _bd in _bases:
                for _k in _all_k:
                    # ⚠️⚠️ **绝不能 `round`**(2026-09-15 真机实证)。运行期算的是
                    #    `_fit_base * _k` 的**原值**, 而这里原来 round 到 4 位 ⇒ 两个值差
                    #    1e-7~5e-5 —— 而 Kivy 的字体缓存按**全精度**索引 ⇒ **两个 fontid**,
                    #    预热等于白烘。真机冷字号榜里那两条就是它:
                    #      `fs=57.23749923706055 最近预热档 57.2375 差 0.000000763`
                    #      `fs=53.80324928283691 最近预热档 53.8032 差 0.000049283`
                    #    差这么一点点, 却各要 20.5 / 16.6 毫秒。
                    #    **预热必须和运行期算同一个数** —— 这是这一整套东西唯一的前提。
                    _v = _b * _k
                    if (_v, _bd) in _seen:
                        continue
                    _seen.add((_v, _bd))
                    _hud.append((_v, _bd))
            # ⚠️⚠️ **2026-09-15 撤回: 这里曾经补烘 `sp(36)` 的 `FIT_FINE` 那 11 项, 已删。**
            #    (v0.7.42 加的。当时真机冷字号榜四条基准全是 57.2375, 就顺手把 sp(36) 的
            #     整条细分阶梯都 ho 上了 —— **方向想对了, 做法违反上面那条硬规矩**。)
            #    撤它的三条理由:
            #    ① **和上面那段直接矛盾**: 6185-6195 明写"只能烘 FIT_SCALES, 不能连
            #       FIT_FINE 一起烘"(全烘 155 项把前面的全挤掉, 低90 从 8 帧退回 10 帧),
            #       而这一段干的正是那件事。
            #    ② **它在真机上没起作用** —— 这一点是实测, 但**机理未定, 别照猜**。
            #       v0.7.42 真机日志的三条 FIT_FINE 冷字号(`fs=37.7767 / 26.3292 / 24.0397`)
            #       全印着 **`预热时量过=否`**, 且它们的"最近预热档差"是 **0.18 / 1.08 / 3.37**
            #       —— 差这么大 ⇒ **表里根本没有那个字号**(有就是差 0, 那几档是精确的
            #       `base x k`)。也就是说: **占了名额, 一项都没兑现。**
            #       ⚠️ 我当时给的解释是"预热链没排到尾部那几项", **桌面探针把这个解释打掉了**:
            #          `temp/warm_table_probe.py` 在 63 项时读到 `_font_prebaked = 63/63`、
            #          `_WARM_DID` 63 个 —— **全跑完了**。真机上到底为什么没有, **尚未定论**
            #          (待查方向: 设备上这里算出的 `sp(36)` 与标签 `_fit_base` 是不是同一个
            #          浮点值)。**在有直接证据之前, 谁也不许照某个猜测改这里。**
            #    ③ **它挤掉的是游玩路径上的字号**。日志自报"超了! 会互相挤, 必须减字号
            #       总数"(判据 10382-10385), 而被挤掉的那条正是
            #       `fs=45.1875 [累计1投1中(100] 预热时量过=是 却仍冷` —— 落在帧724 上,
            #       **15.9 毫秒, 占该帧 22.5 毫秒的 71%**。这一条是**真·被挤出**的
            #       (`=是` 的语义就是"预热真的量过它")。
            #    ⇒ 删完**桌面实测回到 52 项**(`_FONT_WARM_HUD` 48 + `_FONT_WARM_SIZES` 4),
            #      与 6251 行注释里写的口径对上。
            #      阴性对照(把这段塞回去重跑探针): ① 立刻变回 63 并判失败 —— 判据有分辨力。
            #      ⚠️ **设备上会落到几项, 取决于哪几档和别的基准撞号, 没验证过** ——
            #         下一份真机日志里"字号预热表: N 项"那行会直接给出答案(判据 10382-10385)。
            #    ⚠️ 真要在"窄可用宽 + 长文案"上省那一次冷开, 正确的位置是
            #      `_fit_font_size_slow` 那条链本身(别让长文案一路滑到 FINE 档),
            #      **不是往预热表里加项** —— 这张表的上限是硬的, 加多少就挤掉多少。
            globals()["_FONT_WARM_HUD"] = tuple(_hud)
            # ⚠️⚠️ **大字/飘字那批(sp(36/48/26/30))故意不烘** —— 见上面那段: 加上它们就是
            #    48 + 24 = 72 项, 仍然超 64 的上限, 又会开始互相挤。取舍:
            #      · HUD 那批(状态栏/余额/统计/按钮)每局用几十次 ⇒ **必须热**;
            #      · 大字/飘字一局只用几次, 且 `fit_font_size` 第一档多半就放得下
            #        ⇒ 首次冷一次(真机约 20~30 毫秒, 落在第一局装杯那一帧), 之后 LRU 会留住。
            #    ⚠️ 别再"顺手都烘上": v0.7.31 就是这么干的, 结果 155 项把前面全挤掉,
            #       低90 从 8 帧退回 10 帧 —— 预热**不是越多越好**, 上限是硬的。
            # ⚠️⚠️ **不能一个都不烘**(2026-09-15 真机打脸): 上一版删成空的, 结果大字那个
            #    基准(`sp(48)`, 真机上 = **144.6**)第一次用要冷开 **49.2 毫秒**, 而它是在
            #    `_frame -> tick_draw -> _pump_reveal` 里造的 ⇒ 记在「板面」头上, 表现为
            #    "某一帧板面突然 50 毫秒"(v0.7.32 的 `帧1103 60.32ms[板面50.4]` 就是它)。
            #    折中: **只烘第一档**(k=1.0) —— 大字一局只用几次、第一档多半就放得下;
            #    4 个基准 x 1 档 = 4 项, 总数 48+4 = 52, 仍在 64 以内。
            #    ⚠️ 这 4 个是**手抄的 sp(36/48/26/30)** —— 大字是每次中奖现建的, 没有常驻
            #       标签可以读 `_fit_base`。真机实测 sp(48)=144.6, 抄对了; 哪天改了大字基准,
            #       日志里的「最近预热档差」会立刻暴露出来。
            globals()["_FONT_WARM_SIZES"] = tuple(
                _b * FIT_SCALES[0] for _b in (sp(36), sp(48), sp(26), sp(30)))
            # 原值清单(见 `_FONT_WARM_ALL` 处说明): HUD 那批**预热时真的传的是 round 过的值**,
            # 所以这里必须存 round 后的 —— 要比较的是"预热真正开出去的那个字号",
            # 不是"我们以为它会开的值"。大字号那批传的是原值, 照存。
            # ⚠️ 只收**真的进了预热链**的那些 —— 上面把大字那批砍掉之后, 这里也必须跟着砍,
            #    否则日志里的「最近预热档」会指着一个**从来没烘过**的值, 比我什么都不知道还糟
            #    (那正是 2026-09-15 实踩到的: `fs=48.0 [状态栏] 最近预热档48.0 差0` 但
            #     `预热时量过=否` —— 两条自相矛盾, 因为表里列着它、预热链却没碰它)。
            del _FONT_WARM_ALL[:]
            for _v, _bd in _hud:
                _FONT_WARM_ALL.append((float(_v), bool(_bd)))
            for _v in globals()["_FONT_WARM_SIZES"]:
                _FONT_WARM_ALL.append((float(_v), True))
        if self._font_prebaked < len(_FONT_WARM_HUD) + len(_FONT_WARM_SIZES):
            # ⚠️ **一帧只碰一个字号**(2026-09-14 改)。原来是 4 个字号挤在**同一帧**里跑,
            #    而"碰一个新字号"= 重新打开一次 TTF 字形表 = 桌面 26ms ⇒ 那一帧至少 **100ms**,
            #    真机上更贵。这是本方法自己引入的长帧(它要消灭的是"落袋那帧现开字形表",
            #    结果先在启动期造了一记更长的) —— 分帧摊开, 每帧只付一次。
            # ⚠️ HUD 那批先走(它们是这次真机抓到的 16.5 毫秒的来源), 各自带**自己的 bold**;
            #    走完再接大字/飘字那批(那批恒 bold=True, 与 `big_result_text` 一致)。
            _hud_tbl = _FONT_WARM_HUD
            if self._font_prebaked < len(_hud_tbl):
                _fs, _bd = _hud_tbl[self._font_prebaked]
            else:
                _fs = _FONT_WARM_SIZES[self._font_prebaked - len(_hud_tbl)]
                _bd = True
            self._font_prebaked += 1
            try:
                # ⚠️ **`force=True` 不能省**: 走普通路径的话, 只要 `("未中", fs, bd)` 命中
                #    `_FIT_PX`, 这一句就什么都不做 —— 而启动期 `_FIT_PX` 里很可能已经有它
                #    (布局阶段量过)。实测: 不加 force 时, 冷字号榜上仍有一条 18.0 档 82.9ms。
                _c0 = _FRAME_FIT[1]
                text_px("未中", _fs, _bd, force=True)
                # ⚠️ **只在真的量了才记 `_WARM_DID`**。原来是无条件 add ⇒ 日志那栏
                #    「预热时量过=是」在"其实没烘"时也照样显示, **自证失效**(比没有更糟)。
                #    判据用 `_FRAME_FIT[1]`(冷测量计数) —— 它 +1 就证明确实建了 CoreLabel。
                if _FRAME_FIT[1] > _c0:
                    _WARM_DID.add((float(_fs), bool(_bd)))
            except Exception:
                pass
            # ⚠️ **这一步用 0.02 秒, 不是其它步骤的 0.05**(2026-09-14)。
            #    0.05 的依据是"碰一个新字号 = 重开一次 TTF 字形表 = 26 毫秒" ——
            #    而**真机实测一次只要约 1.4 毫秒**(`字号` 16.5ms / 最多 12 次 `text_px`)。
            #    26 那个数在今天的代码与环境上不成立, 于是 0.05 让 72 步白白占掉 3.6 秒。
            #    0.02 秒在 60fps 启动期约 1.2 帧, 仍守住"一帧只碰一个字号"的初衷。
            #    ⚠️ **只改这一支**, 别动其它步骤的 0.05 —— 球纹理在真机上单步 100~200ms,
            #    那几步挤一帧就是自己造一记长帧。
            # ⚠️⚠️ **`schedule_once` 那一句绝不能被注释挤掉**(2026-09-14 我实踩过):
            #    替换这一段时把 `Clock.schedule_once(self.prebake_step, 0.02)` 整个吃掉了,
            #    于是自链式预热**走不下去**、`_PREBAKE_DONE` 永不置真 ——
            #    表现是"启动预热 5.8 秒变 90 秒"(探针测出来的), 而**画面上什么都看不出来**。
            Clock.schedule_once(self.prebake_step, 0.02)
            return
        # ---- 字形图集预热(见 `_GLYPH_CHARS` 那一大块) ----
        # ⚠️ 一次一档, 与上面字号预热同一条理由: 一帧烘 11 个字形 = 一记自己造的长帧。
        # ⚠️ 放在**字号预热之后、球纹理之前** —— 球纹理单步真机 100~200 毫秒, 别和它挤一帧。
        if not getattr(self, "_glyph_done", False):
            try:
                if _glyph_warm_step(self.area):
                    Clock.schedule_once(self.prebake_step, 0.02)   # ⚠️ 这一句不能少(见上面那条教训)
                    return
            except Exception:
                pass
            self._glyph_done = True
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
        # ⚠️ 必须铺满**全部 _PILE_VARIANTS 个变体**, 不能只烘 seed=1:
        #    玩期的 key 是 (倍率, self._seq % 4), `_seq` 每中奖 +1 ⇒ 只烘 (n,1) 的话,
        #    同一档的第 2/3/4 次中奖全是冷建(x100 桌面 18.7ms / 安卓估 60~150ms),
        #    而它卡在 settle 那一帧 —— 正好压住本该给玩家看的槽位白闪/绿灯。
        #    仍然**分帧**烘: 每次只建一个, 0.03s 后接着来, 不阻塞启动。
        # ⚠️ 球堆这一步**一次填完, 不再分帧** —— 坐标现在是查离线烘的表
        #    (tools/android_part_piledata.py), 84 个 key 全部就绪实测 1.9ms
        #    (改造前是 381ms 纯计算 + 84 x 0.03s 的调度间隔 ≈ 2.5 秒后台)。
        #    仍然**分帧**的是上面两块: 玻璃贴图与球纹理合成(那个是真的慢)。
        #    表若缺项会自动回退到现算, 那时单次 22ms(x100), 也不会把启动卡住。
        for n in (2, 3, 5, 10, 20, 50, 100):
            for sd in range(_PILE_VARIANTS):
                if (n, sd) not in _PILE_CACHE:
                    try:
                        _pile_projected(n, sd)
                    except Exception:
                        pass
        # ---- GC 冻结(2026-09-14 加): 预热全部跑完之后, 把**已经稳定下来的对象图**
        #      永久移出 GC 的扫描集。这是针对 1%Low 的一记, 不是针对平均帧。
        #
        # 病根(真机实测, Y700 五代): 面板"内存回收 39.0 毫秒（最坏一次 26.8）" ——
        #   单次 26.8ms 对一次 gen-0 回收来说大得离谱, 那是 **gen-2 全量回收**在扫整个
        #   对象图。而 1%Low 看的就是最坏那一帧: 12.5 + 26.8 ≈ 39~47ms, 正好对上同一份
        #   面板里的"最慢三帧 47(装杯·实算37.8)"。**尾部的最大那一根就是它。**
        #
        # 为什么本工程原先排除 GC 调优**不适用**于这里: `CLAUDE.md` 里那条排除的原话是
        #   "gc.freeze()/禁用换来的只是把一次 7.6ms 拆成更频繁的小停顿, **平均开销不变**"。
        #   那是对**平均帧**的算法 —— 而 1%Low 是"最慢那 1% 的均值", 只看最坏的那几帧,
        #   平均开销变不变与它无关。同一条注释自己也写着"它进不了平均值, 只能进 1% low"。
        #   ⇒ **目标换成 1%Low 之后, 这条排除作废。**
        #
        # 实测(桌面探针 gc_freeze_probe.py, 应用建完等预热跑完后):
        #   GC 跟踪的对象数  54145  ->  0（全部移入永久代, 永不再扫）
        #   强制 gen-2 全量回收 中位 2.9ms  ->  0.0ms
        # ⚠️ **必须在预热全部跑完之后调**: 冻结的是"此刻活着的所有对象", 早调会把
        #    还没建好的缓存漏在外面(仍然被跟踪, 白白多一次 gen-2 的扫描量)。
        # ⚠️ **冻结 = 这些对象永不被 GC 回收**。只要它们真是长期活着的(控件树/贴图/缓存),
        #    这就是纯赚; 会**被淘汰**的缓存要留意 —— 本工程里 `_PILE_CACHE` 上限 128(现用
        #    28)、`_CUP_BALL_TEX` 4 个且从不淘汰、`_FIT_PX` 超 512 会 clear(里面是 float
        #    和元组, 漏掉也无所谓), 都在可忽略的量级。
        # ⚠️ 每局新造的东西(球堆的 100 颗球、画布指令、`_balls` 列表)都是**冻结之后**建的,
        #    照常被跟踪、照常回收 —— 冻结不会让它们泄漏。
        # ⚠️ 冻结之后 gen-0/gen-1 也变便宜了(要扫的年轻对象没变, 但老的不用再被反复提升)。
        #    阈值**不动**(= CPython 默认 700/10/10) —— 没有实测证据就别改它。
        # ⚠️⚠️ **必须在 `gc.freeze()` 之前定位**(2026-09-15 实测踩到)。
        #    `gc.freeze()` 把当时活着的所有对象移进"永久代", 而 **`gc.get_objects()`
        #    不返回永久代里的东西** —— 实测冻结之后 `len(gc.get_objects())` 只剩 **11 个**
        #    (整进程 5.4 万个都被冻住了) ⇒ `sdl2_cache_order` **根本扫不到**, 钉子静默失效。
        #    挪到 freeze 之前就正常了(那时预热表 52 项已烘完, 队列里有货、判据认得出)。
        #    ⚠️ 这一条是评审 A 明确警告过的("必须在主线程、且在 `gc.freeze()` **之前**做"),
        #       我第一版放到了 freeze 之后 —— **桌面探针当场测出来**, 没等真机。
        try:
            _PIN_ORDER[0] = _find_sdl2_order()
            _PIN_WHERE[0] = ("找到了(队列长 %d, 形状命中的 list 共 **%d** 个)"
                             % (len(_PIN_ORDER[0]), _PIN_CAND[0])
                             if _PIN_ORDER[0] is not None
                             else "**没找到**(形状命中的 list 共 %d 个; 钉子不生效)"
                                  % _PIN_CAND[0])
            if _PIN_ORDER[0] is not None:
                _PIN_DONE[0] = set()
        except Exception as _e:
            _PIN_ORDER[0] = None
            _PIN_WHERE[0] = "**定位抛异常** %r" % (_e,)
        try:
            import gc as _gc
            _gc.collect()                 # 先把垃圾收干净, 免得把垃圾也一起冻结
            # 冻结**前**强制一次 gen-2 全量回收并计时 —— 这就是"真机上一次全量回收要
            # 多久"的直接读数(面板那栏"内存回收 最坏一次 26.8"就是它造成的)。
            # 代价是启动期多一记停顿, 发生在预热链里、玩家看不见的地方。
            _t0 = time.perf_counter()
            _gc.collect(2)
            _t1 = time.perf_counter()
            _gc.freeze()
            _t2 = time.perf_counter()
            _gc.collect(2)                # 冻结后再来一次: 这次扫不到东西
            _t3 = time.perf_counter()
            _GC_FROZEN[0] = _gc.get_freeze_count()
            _GC_FROZEN[1] = (_t1 - _t0) * 1000.0
            _GC_FROZEN[2] = (_t3 - _t2) * 1000.0
        except Exception:
            pass
        # 预热链到此结束 —— 跑分要等这个标(见 _PREBAKE_DONE 处的说明)。
        _PREBAKE_DONE[0] = True

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

# 槽倍率文字的**贴图缓存**: (倍率, 字号) -> Texture。
# ⚠️ 为什么必须缓存(2026-09-14): `_redraw` 每次重掷盘面都会把整块画布重建一遍, 而里面
#    **每个非空槽都要新建一个 `CoreLabel` 并 `refresh()`** —— 那是**一次完整的文字光栅化**。
#    一次重掷最多 9 个槽 ⇒ 9 次光栅化, 而它落在**球落定后那一帧**(待机)。
#    真机面板连出两版都指向这一帧: "最慢三帧 ... 36毫秒(待机·**自算8.1**)" /
#    "34毫秒(待机·**自算9.1**)"。桌面实测 `park_ball` 中位 1.9 毫秒, 其中画布重建 1.2 毫秒。
#    而倍率的取值只有 2/3/5/10/20/50/100 这么几种 ⇒ 贴图建一次就够, 之后每次重掷都白建。
# ⚠️ 缓存键**必须带字号** `fs`: 它跟着 `s`(画布缩放)走, 转屏/改窗口时字号会变, 那时要重建。
# ⚠️ 与工程里其它贴图缓存同一套路(`_CUP_BALL_TEX` / `_RAMP_TEX` / `_GLASS_TEX`) —— 持有
#    引用本身就是"别被回收"的保证。
_SLOT_TXT_TEX = {}


def slot_text_tex(m, fs):
    """倍率文字的贴图(带缓存)。见 `_SLOT_TXT_TEX` 处的说明。"""
    k = (int(m), int(fs))
    t = _SLOT_TXT_TEX.get(k)
    if t is None:
        try:
            cl = CoreLabel(text="x%d" % m, font_size=fs, font_name="Roboto", bold=True)
            cl.refresh()
            t = cl.texture
        except Exception:
            return None
        if len(_SLOT_TXT_TEX) > 64:
            _SLOT_TXT_TEX.clear()
        _SLOT_TXT_TEX[k] = t
    return t


def slot_txt(m):
    """槽位数字字色: ×100 深橙白字对比 2.3 太低(大奖会糊), 故黑字(8.0)最跳;
    其余档白字(低档绿蓝红干净醒目, 深红/紫暗底白字最亮)。"""
    return "#0b1220" if m >= 100 else "#ffffff"

# ---------------- 单行自适应字号(把"太长就折行"从根上掐掉) ----------------
# 病根: Kivy 的 Label 只有**两种**行为 —— 设了 `text_size` 就折行, 没设就溢出
# (它不裁剪, 直接画到隔壁控件身上)。没有"缩到放得下"这一档, 而本作 HUD 上几乎每个
# 位置都是**定宽 + 定高**的(顶栏 44dp / 信息行 26dp / 按钮 56x36), 于是:
#   · 可用宽度取决于**设备逻辑宽度**(1200px 的机器落在 400~457dp, 桌面开发窗是 540)
#     ⇒ 同一条字符串在桌面上不折、在手机上折;
#   · `sp()` 还跟着系统"字体大小"(config.fontScale, 0.85~1.3)一起放大, 而盒子是 dp
#     写死的 ⇒ 玩家把系统字体调大, 满屏折行。
# 这里补上 Android 那套 autoSizeTextType: 量一下, 挑一个**放得下的最大字号**;
# 实在放不下就给最小档(0.7 倍), 而不是折行。
# ⚠️ 只在**单行**标签上用。多行正文(弹窗说明/历史列表)不能缩 —— 那会把整段字一起缩小。
# ⚠️ 量宽度走 CoreLabel + 缓存: 只花在**文字变化**时(状态栏那几个字符串一局才变几次),
#    不是逐帧。逐帧量会让手机掉帧。
FIT_SCALES = (1.0, 0.94, 0.88, 0.82, 0.76, 0.70)
# ⚠️⚠️ **2026-09-15: 6 档全试完之后的第二段, 原来是"在 [0.42, 0.70] 之间二分 6 次"。
#    改成这组**固定细分档** —— 这是本轮最大的一处修改, 起因是真机日志。**
#
#    病根(桌面探针 `temp/fit_warm_match_probe.py`, 跑一轮真跑分后逐个比对预热表):
#      预热表里的 72 个字号**运行期全都用到了**(一个没白烘); 而运行期真正冷量到的 83 个
#      字号里有 **11 个不在表里**, 它们全部长这样:
#        7.2800 / 7.7350 / 7.9625 / 8.0194 / 8.0762 / 8.1900 / 8.6450 / 8.8725 / 8.9862 / 9.0431
#      —— 一眼就是**二分产出的任意值**。二分每次都落在一个没人见过的字号上, 而
#      **"开一个没开过的字号"在真机上是一次 40 毫秒级的字体表打开**(同一份 v0.7.29 日志:
#      `帧31 54.6ms[字号48.6(2次/1测)]` / `帧716 49.1ms[字号42.8(1次/1测)]` ——
#      **只量了 1 回就烧掉 42 毫秒**, 所以那笔钱不在"量了几回", 在"量的那个字号是冷的")。
#      ⇒ 那 11 个字号**预先烘不了**(它们由宽度决定, 逐字现算), 但**枚举得了**:
#        把二分换成固定档, 全 app 的字号集合就塌缩成 "每个基准 x 这 13 个固定倍率",
#        预热表能一次全盖上。
#
#    ⚠️ 视觉代价: 只有"连 0.70 倍都放不下"的文字才走到这一段, 而返回的档与二分的最优解
#       最多差 0.04 x 基准(基准 17px 时约 0.7px, 字号越大差得越多 —— 大字基准 48 也只有 1.9px)。
#       回归测试: `--selftest` 里那几条"超宽文字"的门禁全绿; 截图见本次 changelog。
#    ⚠️ **别再加回二分**: 二分在这个问题上是**结构性错误** —— 它保证产出"这辈子只用一次"
#       的字号, 而在这个引擎里每个新字号都是一次冷字体表打开。
FIT_FINE = (0.66, 0.62, 0.58, 0.54, 0.50, 0.46, 0.42)
FIT_HARD_FLOOR = FIT_FINE[-1]   # 硬下限; **只防"小到看不见", 不参与塞不塞得下的判断**
                                # (取 0.5 时实测 1.5 倍字体 + 5 个档位按钮下 "5000%" 还差 5px)
_FIT_PX = {}

# ---- 字体缓存账本(2026-09-15): 记下**每次真的开了一个 fontid** ------------------
# ⚠️ 为什么需要: 真机冷字号榜里出现「**预热时量过=是** 却仍冷」—— 语义是"预热真的开过它,
#    但现在又冷了" ⇒ **被挤掉了**。Kivy/SDL2 的字体缓存只有 64 项、按 LRU 淘汰,
#    所以只要"到现在为止开过的**不同**字号数 > 64", 淘汰就是**必然**的, 不需要再猜。
#    再看一眼"这次冷开**之前**最近开过哪些字号", 就知道是被**谁**挤的
#    (猜想: 弹窗那批 12sp/15sp/16sp/17sp/20sp)。
_FS_OPEN = []            # [(序号, 字号, bold, 调用方/文案前 10 字)] —— 按开的先后
_FS_OPEN_SET = set()     # {(字号, bold)} 去重后 = 开过的**不同** fontid 数
_FS_OPEN_MAX = 300       # 只留最近的, 别让它无限长

# ---- 账本静音 + 两个"普查"计数器(2026-09-15, 对抗性评审第 0 批) ----------------
# ⚠️ **为什么要静音**: 玩家要导出日志, 就得打开「帧率曲线」弹窗并按「保存日志」——
#    而那个弹窗**自己的标题**走 `_fit_line` → 一次走满 13 档阶梯 → **一口气开 12~13 个
#    fontid**。证据: 账本尾部那 12 个连降数 = 基准 57.2375 × 阶梯(相邻比值逐位吻合到
#    小数点后四位), 而 `57.2375/19 = 45.1875/15 = 39.16/13 = 3.0125` 正是密度。
#    ⇒ **"测量动作污染被测对象"**: 保存日志这个动作, 把日志里印的那个计数撑大 12。
#    ⇒ 面板构建期间(含紧随其后的**布局收敛**, 那是异步的、跨好几帧)暂停记账。
# ⚠️ **不许静默**: 静音期间被跳过的次数**照记并必须印进日志**。本仓库栽过太多次
#    "没印"被读成"没发生"。
_FS_MUTE_UNTIL = [0.0]   # 静音到这个墙钟时刻; 0.0 = 不静音
_FS_MUTED_N = [0]        # 静音期间**被跳过**的冷开次数(必须印出来)
_FS_MUTE_SEC = 1.5       # 窗口长度: 弹窗构建 + 布局收敛(异步, 要跨好几帧才定稿)
# `fontid -> [冷开次数, 最后一次的调用方]`。**这是把 P 从"采样"变成"普查"的那个计数器**:
# 原来只有 `_COLD_FS` 留最慢 5 条 —— 它本身就是采样, 所以"到底该钉几个 fontid"根本定不了。
_FS_COLD_CNT = {}
# 「本帧冷开了几次」→「这种帧出现了多少次」。**回答"一帧到底会不会开十几个"**:
# `(N次/M测)` 只在「字号」挤进该帧最大两个子步骤时才印, 21059 帧里只有 2 帧印过 ——
# 那是采样不是普查, 正是对抗评审里两位专家分歧的那个点。
_FIT_HIST = {}
# 「尾」的**两段拆分**用(2026-09-15, 第 2 批): `_frame` 跑完 → Kivy Clock 跑完 → 进 flip。
# 老实现只有一个「尾」= 整段墙钟残差, 分不出里面是回调还是空档 —— 而那正是参考带帧
# 那 2~3ms 的去处之争(对抗评审 S2)。
_CLOCK_END = [0.0]

def text_px(text, fs, bold=False, base=None, ctx=None, force=False):
    """一段文字在字号 fs 下的**单行宽度**(px)。结果缓存。"""
    if not text:
        return 0.0
    key = (text, round(fs, 2), bool(bold))
    # ⚠️ `force=True` **跳过缓存读** —— 只给启动预热用。
    #    为什么必须有它(2026-09-14 实测): 预热那句 `text_px("未中", fs, bd)` 一旦命中
    #    本函数的缓存, 就**不建 CoreLabel、不开字体**, 预热白做且**不报错**。
    #    日志「读法」早就记着这条现象(「=否 ⇒ 预热那一句被 text_px 自己的缓存挡掉了」),
    #    但因为没人能从画面上看出来, 一直没修。**预热要的就是"真的开一次字体"**,
    #    所以它必须能绕过自己的缓存。
    got = None if force else _FIT_PX.get(key)
    if got is None:
        # ⚠️ **这一格就是"冷测量"的定义**(2026-09-15): 缓存没命中 ⇒ 现建一个 CoreLabel
        #    在**这个精确字号**上量一次。真机上一次这样的测量要 1~4 毫秒(超宽文字走二分时
        #    一帧能叠到十几回, 见 `_FRAME_FIT` 处的说明)。它和「重建了几次」是两笔账 ——
        #    `text_px` 从不产生纹理, 只量宽度。
        _FRAME_FIT[1] += 1
        # ⚠️ **记账之前**先问一句"这个字号以前开过吗" —— 用来判"复冷"(见 `_pin_fontid`)。
        #    必须取记账**前**的状态: 记账之后它当然就在集合里了。
        _was_open = (round(float(fs), 4), bool(bold)) in _FS_OPEN_SET
        # ⚠️ 这一句就是"开了一个 fontid"的那一刻 —— 记账在这儿, 不在别处(见 `_FS_OPEN`)。
        # ⚠️ **静音窗口内不记账**(见 `_FS_MUTE_UNTIL`): 报告 UI 自己的标签不算被测对象。
        #    跳过的那几次**单独计数**, 并且必须印进日志 —— 不许静默。
        if time.time() < _FS_MUTE_UNTIL[0]:
            _FS_MUTED_N[0] += 1
        else:
            try:
                _FS_OPEN.append((len(_FS_OPEN), float(fs), bool(bold),
                                 ctx or _COLD_FS_TAG[0] or "?"))
                _FS_OPEN_SET.add((round(float(fs), 4), bool(bold)))
                if len(_FS_OPEN) > _FS_OPEN_MAX:
                    del _FS_OPEN[0]
                # 普查: 每个 fontid 一共冷开了几次(定"该钉几个"的唯一依据)。
                _ck = (round(float(fs), 4), bool(bold))
                _ce = _FS_COLD_CNT.get(_ck)
                if _ce is None:
                    _FS_COLD_CNT[_ck] = [1, ctx or _COLD_FS_TAG[0] or "?"]
                else:
                    _ce[0] += 1
            except Exception:
                pass
        _t_cold = time.perf_counter()
        try:
            _cl = CoreLabel(text=text, font_size=fs, bold=bold, text_size=(None, None))
            _cl.refresh()
            got = _cl.texture.size[0]
            # ⚠️ **复冷 ⇒ 立刻钉住**(见 `_pin_fontid`): 同一个字号**第二次**被冷开, 说明它
            #    刚被淘汰队列挤出去过。此刻 `refresh()` 刚把它重新插回 order 尾部, 正是
            #    摘掉它的最佳时机 —— 摘掉之后 dict 里那份再也不会被 `del`, 它不再复冷。
            #    `_get_font_id()` 是 Kivy 自己拼的那个 6 段键 —— **必须用它**, 手拼会错
            #    (字体路径是运行期解析出来的, 手工拼不出来)。
            if _was_open:
                _PIN_STAT[2] += 1
                try:
                    _pin_fontid(_cl._get_font_id())
                except Exception:
                    pass
        except Exception:
            got = len(text) * fs * 0.55          # 量不出来按汉字宽粗估, 绝不抛
        # ⚠️ 冷测量的**单价**要单独记下来(见 `_COLD_FS` 处说明): 只留最慢的 5 次,
        #    连**是哪个标签、哪个字号、粗不粗**一起 —— 光有一个毫秒数没法决定怎么修。
        _cold_ms = (time.perf_counter() - _t_cold) * 1000.0
        if _cold_ms >= COLD_FS_MIN_MS:
            # 找最近的**同 bold** 预热档(见 `_FONT_WARM_ALL`): 差值小 = 四舍五入对不上,
            # 差值大 = 基准压根不是一个数。两种病的修法完全不同。
            _nb, _nd = 0.0, -1.0
            for _wv, _wb in _FONT_WARM_ALL:
                if _wb != bool(bold):
                    continue
                _d = abs(_wv - float(fs))
                if _nd < 0.0 or _d < _nd:
                    _nd, _nb = _d, _wv
            # ⚠️ `base` / `ctx` 由调用方传**真值**; 拿不到才退回那两个模块级残留值
            #    (它们只在 `_fit1` 里写, 直接调 `fit_font_size` 的路径上是假数)。
            _base = float(base) if base else float(_COLD_FS_BASE[0])
            _ctx = ctx or _COLD_FS_TAG[0]
            # 第 9 位 = 当时的账本下标, 给日志回溯"这次冷开**之前**最近开过哪些字号"用
            _COLD_FS.append((_cold_ms, float(fs), bool(bold), _ctx, _nb, _nd,
                             (float(fs), bool(bold)) in _WARM_DID, _base,
                             len(_FS_OPEN) - 1))
            _COLD_FS.sort(key=lambda _x: -_x[0])
            del _COLD_FS[5:]
        if len(_FIT_PX) > 512:                   # 余额那类数字会一直变, 别让缓存无限长
            _FIT_PX.clear()
        _FIT_PX[key] = got
    return got

# `fit_font_size` 的**结果缓存**。键 = (文字, 基准字号, 可用宽, 粗体) —— 它是纯函数。
# ⚠️ 为什么值得缓存(2026-09-14, 桌面实测): `_fit1` 是 `_frame` 里**最大的单块**开销 ——
#    45 秒累计 **1.06 秒**(每秒 24 毫秒), 是板面重画 `tick_draw`(0.26 秒)的 4 倍。
#    而 `_install_fit` 把 `_fit1` 绑在 **`text` 和 `width` 两条路**上: **宽度变化那条
#    文字根本没变**, 却在用同一份输入把整个阶梯(最多 6 次 `text_px`, 每次光栅化一段文字)
#    从头再算一遍。实测 `_fit1` 每帧被调 ~4.5 次, 而一帧里真正变了文字的只有余额那一格。
# ⚠️ 缓存**不设超大**: 余额那类数字会一直变, 上限到了整体清空(与 `_FIT_PX` 同策),
#    免得无限长。
_FIT_SIZE_PX = {}

# 字号**量化网格**(px)。走过的字号一律先落到这个网格上再测量/返回。
# ⚠️ 为什么必须量化(2026-09-14): "碰一个新字号"在 Kivy 里 = **重新打开一次 TTF 字形表**
#    —— 桌面实测 **26 毫秒**(见 prebake_step 里那段字体预热的说明)。而 `fit_font_size`
#    的**二分**支会返回 `10.65` 这种任意值, `_fit1` 又把它写进 `font_size` ⇒
#    **每挑出一个新字号就付一次 26 毫秒**, 而且那个字号这辈子只用这一次。
#    实测(桌面): `_fit1` 每次调用要 ~13 毫秒 —— 它就是 `_frame` 里最大的单块开销。
#    量化到 0.5px 之后, 全 app 的字号集合塌缩成一个小集合: 量化误差**最大 0.25px**,
#    肉眼分辨不出(比 1sp 在 2.5 倍密度下还小一个量级), 而字形表只需开那么几次。
# ⚠️ 网格别调粗: 1px 网格在 360dp + 小字号(10~12px)上会有约 8% 的字号跳变, 就开始看得出了。
_FIT_GRID = 0.5


def _qfs(x):
    """把一个字号落到量化网格上。

    ⚠️ **2026-09-14: 已停止使用(保留函数只为别处引用不报错)。**
    为什么回退: 真机上 `sp(N)` 是 `N x 密度` 的**非整数**, 落网格后挪了最多 0.25px ——
    而 Kivy 的字体缓存**按精确字号索引**, 于是和启动期预热/布局烘出来的字号**对不上**,
    每次挑字号都变成一次**冷开字形表**(桌面 26 毫秒, 真机更贵)。
    后果是主线程变忙 -> 物理 benchmark 那条线程拿到的 GIL 变少 -> **纯 CPU 吞吐掉约 6%**
    (玩家实测各 10 次: 0.6.79 是 32000+ 步/秒, 0.6.81 只有 30000+)。
    ⚠️ 桌面当时测出来是**变快**(`_fit1` 降 45%) —— 因为桌面上 `sp(48)` 正好 = 48.0,
    本来就落在网格上, 量化是空操作。**桌面测不到这个副作用**, 这正是它骗过我的地方。
    """
    return round(x / _FIT_GRID) * _FIT_GRID


def fit_font_size(text, base_fs, avail_w, bold=False):
    """挑一个"单行塞得进 avail_w"的最大字号档;**返回绝对字号(px)**。

    塞不下就给最小档(FIT_SCALES[-1] = 0.7 倍)—— 宁可小一点, 也不折行/不溢出。
    """
    if not text or avail_w <= 1.0 or base_fs <= 0:
        return base_fs
    _key = (text, round(base_fs, 2), round(avail_w, 1), bool(bold))
    _got = _FIT_SIZE_PX.get(_key)
    if _got is not None:
        return _got
    _res = _fit_font_size_slow(text, base_fs, avail_w, bold)
    if len(_FIT_SIZE_PX) > 256:
        _FIT_SIZE_PX.clear()
    _FIT_SIZE_PX[_key] = _res
    return _res


def _fit_font_size_slow(text, base_fs, avail_w, bold=False):
    """真正干活的那一半(原来 `fit_font_size` 的全部内容)。**别直接调它**, 走带缓存的入口。

    ⚠️ 两段都是**固定阶梯**: `FIT_SCALES`(1.0~0.70) 然后 `FIT_FINE`(0.66~0.42)。
    为什么第二段**绝不能改回二分** —— 见 `FIT_FINE` 上面那一段(真机 42 毫秒/次的证据)。
    """
    # ⚠️ 把**基准**与**文本**一起传下去 —— 冷字号榜要靠它们指名道姓。
    #    这里不许退回模块级残留值: 本函数是**直接调用入口**(big_result_text 等不走 `_fit1`),
    #    残留值会让日志把 A 的基准记在 B 头上(v0.7.38 那两栏的第一版就是这样)。
    _ctx = (text or "")[:10]
    for _k in FIT_SCALES + FIT_FINE:
        _fs = base_fs * _k
        if text_px(text, _fs, bold, base=base_fs, ctx=_ctx) <= avail_w:
            return _fs
    # 连硬下限都放不下 —— 给地板档, 而不是折行/盖邻居。
    # ⚠️ 原来这里还有一段"按比例估一次"的注释: 实测字宽**不随字号线性变**(同一串在 14.95
    #    和 14.05 下量出来都是 139px, 字形步进被取整), 所以估出来的 10.65 量出来仍是
    #    102px > 可用 101。**估计这条路是死的, 别再试。**
    return base_fs * FIT_HARD_FLOOR

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

# 震动: 工作线程 + 系统服务代理缓存 + 单次计时(2026-09-14)。
# ⚠️ 和发声同一个理由, 而且它还多一处浪费:
#   ① `_vibrate` 原本在**主线程**上调 `Vibrator.vibrate` —— 那是一次**双程 Binder**
#      (到 system_server 再回来)。真机实测慢帧 14 帧里 2 帧有它(发声是 14/14),
#      量级比不上发声, 但形状一样: 主线程不该等 IPC。
#   ② 原本**每次震动都要现取一次系统服务** —— `activity.getSystemService(VIBRATOR_SERVICE)`
#      **本身就是一次 Binder 往返**。取到的 Vibrator 是系统服务的代理, 长期有效,
#      缓存它等于每次震动白省一次 IPC。
_VIB_Q = None                    # 震动工作队列(None = 还没建)
_VIB_LOCK = threading.Lock()
_VIB_PROXY = [None]              # [缓存的 Vibrator 代理]
_VIB_STAT = [0.0, 0.0, ""]       # [累计秒, 单次最慢秒, 最慢那次的描述]

# 方向守卫 / 沉浸重申的耗时统计:
#   [0]主线程投递累计秒 [1]主线程单次最慢秒 [2]发起次数
#   [3]工作线程累计秒   [4]工作线程单次最慢秒 [5]工作线程失败次数
#   [6]UI线程累计秒     [7]UI线程次数        [8]UI线程单次最慢秒
# ⚠️ [6]~[8] 量的是 `setSystemUiVisibility` 本身 —— 它在 **Java UI 线程**上跑, 不在我们的
#    线程里, 所以既不出现在 `_frame` 的剖析中, 也不在"主线程实算"里。桌面剖过: 我们的
#    Python 代码(整个 `_frame`)只占帧时间的 **0.9%**, 而"每帧实算"里那一大块是 Kivy 的
#    on_draw + 安卓的 Java 线程。要动安卓特有的周期性开销, 只剩这一条能量。
# ⚠️ **必须定义在模块级**(不是某个类的类属性) —— 用它的人 (`_guard_worker` /
# `_guard_post` / `_start_benchmark` / `_bench_collect_diag`) 写的都是**裸名**,
# 裸名找的是模块全局; 写成类属性会 NameError(本文件踩过一次, 探针逮住的)。
# 为什么单独立一个计数器: 这两条链**每 0.7 秒**各跑一次, 而且**跑分期间照跑** ——
# 它们是全 app 唯一的常驻周期性主线程 JNI, 而 1%Low 只看最差的 1%(约十几帧),
# 每 0.7 秒来一记正好能把那一档占满。搬出主线程之后, 判据变成:
# **主线程那一档要接近 0, 而工作线程那一档接手**(两边都看得见, 才知道是真搬走了还是没跑)。
# ⚠️ 这个 app 里 JNI/Binder 已实测过是**灾难级的慢**(`SoundPool.play()` 单次 143.6ms、
#    平均 53.5ms) —— 所以"每 0.7 秒一次 system_server 往返"完全够格当 1%Low 的天花板。
# ⚠️ 桌面量不到(`platform != "android"` 直接 return), 只能靠真机跑分面板读那一行。
_JNI_STAT = [0.0, 0.0, 0, 0.0, 0.0, 0, 0.0, 0, 0.0]

# 上一帧 `_frame` **自己**在**本线程**上花了多少毫秒(由 `_frame_timed` 写)。
# ⚠️ 为什么必须单独有这一格: 面板那栏"每帧实算"读的是 `time.process_time()`, 那是
#    **整个进程**的 CPU —— 含发声/震动两条工作线程、以及安卓那一堆 Java 线程。
#    于是"慢帧里实算 37.8 毫秒"既可能是**主线程真在忙**, 也可能只是别的线程在忙。
#    这一格与"实算"一起看才分得开:
#        实算大 · **自算也大**  ⇒ 是我们自己的代码(该去优化它);
#        实算大 · **自算很小**  ⇒ 主线程在等(GC / 框架 / 别的线程), 优化我们的代码没用。
#    桌面已经用 cProfile 剖过一遍(整个 `_frame` 只占帧时间 0.9%), 但真机没有剖析器,
#    只能靠这一格。
_FRAME_SELF = [0.0]


# ============================================================================
# 预烘字形图集: **白色字形 + 画布染色**, 把 Kivy 那句文字光栅化整条掐掉
# ============================================================================
# 病根(v0.7.42 真机日志逐帧重算, `video/plinko_fps_20260914_235002.txt`):
#   · **所有慢于 12.7 毫秒的帧, 全部带文字重建**; 无文字重建的帧最大只有 10.48 毫秒;
#   · 最慢 4 帧(20.5 / 21.8 / 22.5 / 22.8)主线程 17.0~17.6 毫秒, 而 `_frame` 自算只 3.5~4.1
#     ⇒ 中间约 13 毫秒全在文字上, 最大一笔就是 `填纹`(= `CoreLabel._texture_fill`,
#     Kivy 文字**两趟渲染的第二趟**, 9.2~10.9 毫秒);
#   · 反事实重算: 把这笔拿掉, 1%Low 从 98.3 → 109~112。
# ⇒ 别再去别处找 1%Low 了, **尾巴就是这一件事**。
#
# 为什么"逐字贴图"能成立(全部实测, 不是推的):
#   · 项目字体 `fonts/NotoSansSC-Medium.otf` 上, `0123456789+` 这 11 个字的 **advance 逐字相同**
#     (48 号时全是 27)、**纹理高全部相同**;
#   · `sum(逐字宽) == 整串宽` 在 8 个字号 x 8 个串上**精确相等**(差 0.000)。
#     含空格/斜杠的串**不**成立 ⇒ 所以有下面 `_GLYPH_CHARS` 这条**硬边界**。
#   · `.pixels` 的 RGB 恒为 255、A 是覆盖率(**不预乘**)⇒ **白字图集 x 画布色 == 现在烘色**,
#     逐通道相等(同 `_tint_from` 那条已钉过的结论)。
#
# ⚠️⚠️ **Kivy 把颜色烘进字形纹理**(见 `_texex_key` 的注释), 所以现在"彩色大字"和"黑影子"
#    是**两张图、两次光栅化**、而且落在同一帧。这里烘成白色之后, **两者共用同一批贴图**,
#    只差画布上那一条 `Color` —— 那一半是白拿的。
# ⚠️ **不进 `_TEXEX_G`**: 那条路缓存的是"整段文字一张纹理", 而这里的目标恰恰是
#    "一张整段纹理都不产生"。两条路各自独立, 互不干扰。
# ⚠️ 图集**一个 fontid 都不多占**: 实测这些标签只落到 2 个字号(`sp(48)`=48.0 与 `sp(19)`=19.0),
#    两个都**本来就在字号预热表里**(见 `_FONT_WARM_SIZES` / `_bases`)。
#    这一点是硬约束 —— 刚因为往预热表多塞 11 项把游玩路径的字号挤出缓存(v0.7.42 帧724 那
#    15.9 毫秒), **别再往 Kivy 的 64 个 fontid 上添东西**。
_GLYPH_CHARS = "0123456789+"
_GLYPH_ON = False                # 总开关(阴性对照 + 线上紧急关, 与 `_TEXEX_ON` 同策)
# ⚠️⚠️ 2026-09-15 **暂时关着**: 见 `GlyphLabel` 处那段"未解释的 1 像素"说明。
#    打开它之前必须先把那个差解释掉 —— 本工程的规矩是"每一个差异像素都要能解释"。
_GLYPH_FONT = "Roboto"           # 就是项目注册的那个中文字体(见文件头部 LabelBase.register)
_GLYPH_ATLAS = {}                # {(fs, bold, font): rec}
_GLYPH_ORDER = []
_GLYPH_MAX_KEYS = 8              # 只 2 档, 给足余量; 上限是"别再撑爆字形表"的兜底
_GLYPH_MAX_CHARS = 12            # 最长串 `+10000`(6 字符, = 投注 100 x x100, 玩家 2026-09-15 确认)
_GLYPH_HIT = [0]
_GLYPH_MISS = [0]                # 退化次数 —— **收益归零是静默的, 所以必须记账**


def _glyph_key(fs, bold):
    # ⚠️ **不 round**(见 6199 那段真机实证): 运行期算的是 `base * k` 的原值,
    #    round 过就是**另一个 fontid**, 预热/图集全白做。
    return (float(fs), bool(bold), _GLYPH_FONT)


def _glyph_bake(fs, bold):
    """烘一档(11 个字形)。任何一步不对**返回 None** ⇒ 这一档永不启用, 那三个标签退回普通 Label。

    ⚠️ 三道门禁都是**真的会失败**的, 不是"看起来对":
      ① 每个字形 `bind()` 之后**必须**数得出 alpha>0 的像素 —— 这就是 `_texex_bake` 那条
         "`refresh()` 只量宽高, `bind()` 才触发第二趟真光栅化"的账。漏了它的症状是
         **图集全空、画面没字, 而且不报错**。
      ② 11 个字的高度必须**完全一致** —— 否则"同一条基线"这条假设不成立, 拼出来会歪。
      ③ 逐字宽相加 == `text_px` 整串量(**`force=True`**) —— 把"已实测的等式"变成运行期门禁。
         `force` 不能省: `_FIT_PX` 的 key 含 text, 命中的话这一句什么都不做(见 6284 那条教训)。
    """
    try:
        rec = {"fs": float(fs), "bold": bool(bold), "adv": {}, "tex": {}, "keep": [],
               "h": 0, "ok": False}
        # ⚠️⚠️ **必须把 11 个字当一整串烘一次, 再按 advance 切** ——
        #    **绝不可以一个字一个 CoreLabel 地烘**。2026-09-15 逐像素对照实测:
        #    单独烘出来的字形, 竖直落点比"在整串里"的**高半个像素** —— 剖面上一眼看得出:
        #    笔画中段两边逐行完全相同(454/454), 只有**顶边那一行**不同(Label 267 / Glyph 317),
        #    整块字差 704 个像素。这是 SDL 的次像素定位, 不是我们算错了坐标
        #    (平移 ±2 行扫过一遍, 0 偏移最优 ⇒ 不是位移, 是渲染本身不同)。
        #    一整串烘 + 按 advance 切就没有这一层: 同一个 pass、同一支笔、同样的整数步进。
        _cl = CoreLabel(text=_GLYPH_CHARS, font_size=float(fs), bold=bool(bold),
                        font_name=_GLYPH_FONT, text_size=(None, None))
        _cl.refresh()
        _t = _cl.texture
        if _t is None:
            return None
        _t.bind()                                       # ⚠️ 门禁①的前提: 第二趟必须在这里付掉
        _W, _H = _t.size
        if _H <= 0:
            return None
        _adv = [int(text_px(c, float(fs), bool(bold), force=True)) for c in _GLYPH_CHARS]
        # 门禁③: 逐字宽相加 == 整串纹理宽。这一条不过就整档作废(宁可退回 Label, 不许拼歪)。
        if sum(_adv) != _W:
            return None
        # 门禁①: 每个字都真的画出了东西(空字形 = 这档废掉, 别静默拼出一串空白)
        _px = _t.pixels
        _x = 0
        for _c, _w in zip(_GLYPH_CHARS, _adv):
            if _w <= 0:
                return None
            _n = 0
            for _xx in range(_x, min(_x + _w, _W)):
                for _yy in range(_H):
                    if _px[(_yy * _W + _xx) * 4 + 3] > 8:
                        _n += 1
            if _n == 0:
                return None                             # ① 空字形 ⇒ 整档作废
            _st = _t.get_region(_x, 0, _w, _H)
            _st.mag_filter = "linear"
            _st.min_filter = "linear"
            rec["adv"][_c] = _w
            rec["tex"][_c] = _st
            _x += _w
        rec["h"] = _H
        rec["keep"].append(_cl)                         # ⚠️ 强引用: 子纹理靠父纹理活着(会被 GC 掉)
        # 门禁②: 逐字宽相加 == `text_px` 整串量(拿几个真串验, 不只验字符集本身)
        for s in ("0", "8", "+2", "+10000", "273300", "1234567890"):
            if sum(rec["adv"][c] for c in s) != text_px(s, float(fs), bool(bold), force=True):
                return None
        rec["ok"] = True
        return rec
    except Exception:
        return None


def _glyph_put(fs, bold, rec):
    k = _glyph_key(fs, bold)
    if k in _GLYPH_ATLAS:
        return
    _GLYPH_ATLAS[k] = rec
    _GLYPH_ORDER.append(k)
    while len(_GLYPH_ORDER) > _GLYPH_MAX_KEYS:
        _GLYPH_ATLAS.pop(_GLYPH_ORDER.pop(0), None)


def _glyph_rec(fs, bold):
    if not _GLYPH_ON or not fs:
        return None
    _r = _GLYPH_ATLAS.get(_glyph_key(fs, bold))
    return _r if (_r and _r.get("ok")) else None


def _glyph_quads(rec, text):
    """-> `([(子纹理, x偏移, 宽)...], 总宽)`; 出现字符集外的字符**立刻返回 None**。

    ⚠️ **这条 `None` 是硬边界, 不许绕过**: `未中` 这两个汉字、任何含空格或斜杠的串
       (实测那种串"逐字和"与"整串宽"差 1~8px)**必须**退回普通 `Label`。
       静默拼出来 = 字距错、宽度错, 而画面"看着差不多"。
    """
    out, x = [], 0
    for ch in text:
        t = rec["tex"].get(ch)
        if t is None:
            return None
        w = rec["adv"][ch]
        out.append((t, x, w))
        x += w
    return out, x


def _glyph_keys_reachable(rw):
    """枚举**运行期真的会用到**的档 —— 不许手抄阶梯, 一律让 `fit_font_size` 自己算。

    ⚠️ 为什么值得这么绕(与 `_build_texwarm` 同一个理由): 每开一个新字号在真机上是一次
       字形表的打开, 而 Kivy 的字形缓存只有 64 项(见 6188 那次实测)。实测这里只产出
       **2 档**(sp(48) 与 sp(19), 且都已在预热表里)。
    ⚠️ `未中`(sp(36))**故意不烘** —— 它不在 `_GLYPH_CHARS` 里, 必退 Label。
    """
    out = []

    def _add(fs, bd):
        k = (float(fs), bool(bd))
        if k[0] > 0 and k not in out:
            out.append(k)

    try:      # 结算大字: 文案 = "+N", N = 投注档 x 倍率(与 `_build_texwarm` 同一个值域)
        mults = sorted({2} | {int(_k) for _d in VALUE_SHAPE.values() for _k in _d})
        bets = list(PRESETS) + [int(getattr(rw, "bet", DEFAULT_BET) or DEFAULT_BET)]
        _aw = max(80.0, float(getattr(rw, "width", 540) or 540) * 0.94)   # 与 `big_result_text` 同一句
        for _b in bets:
            for _m in mults:
                _add(fit_font_size("+%d" % (_b * _m), sp(48), _aw, True), True)
    except Exception:
        pass
    try:      # 余额: 数字等宽 ⇒ 位数就决定档, 用全 8 代表
        _lb = getattr(rw, "balance_lbl", None)
        _base = float(getattr(_lb, "_fit_base", 0.0) or 0.0)   # 与 6178 读 `_fit_base` 同一个理由
        if _base > 0:
            _av = max(1.0, float(_lb.width))
            for _n in ("8", "88", "888", "8888", "88888", "888888", "8888888"):
                _add(fit_font_size(_n, _base, _av, True), True)
    except Exception:
        pass
    return out[:_GLYPH_MAX_KEYS]


def _glyph_warm_step(area):
    """`prebake_step` 里一次烘一档(自链式, 别一帧烘完 —— 一帧 11 次字形表 = 一记长帧)。"""
    if not _GLYPH_ON:
        return False
    _rw = getattr(area, "game", None)
    if getattr(_rw, "_glyph_keys", None) is None:
        _rw._glyph_keys = _glyph_keys_reachable(_rw)
        _rw._glyph_i = 0
    _keys = _rw._glyph_keys
    _i = int(getattr(_rw, "_glyph_i", 0))
    if _i < len(_keys):
        _rw._glyph_i = _i + 1
        _fs, _bd = _keys[_i]
        _r = _glyph_bake(_fs, _bd)
        if _r is not None:
            _glyph_put(_fs, _bd, _r)
        return True
    return False


def _fx_fade_set(w, col, alpha):
    """特效淡出。⚠️ 比 `_fade_set` 多乘一个**基色 alpha**。

    为什么必须有这一条: `_fade_set` 只写 alpha、保留 rgb, 老路上没问题 —— 因为那时
    **颜色(含阴影的 0.6)是烘在纹理里的**, 画布那条 Color 只管透明度。
    换成白字图集之后**基色搬到了画布上**, 于是 `_fade_set(col, 0.5)` 会把阴影的
    `0.6` **覆盖成 0.5** ⇒ 阴影在淡出段比基线**更黑**, 撤场那一下尤其明显。
    普通 `Label` 没有 `_alpha0` ⇒ 默认 1.0 ⇒ 与改动前**完全一致**。
    """
    return _fade_set(col, alpha * float(getattr(w, "_glyph_alpha0", 1.0) or 1.0))


def _vib_get():
    """取(并缓存)Vibrator 系统服务代理。

    ⚠️ 必须用 `Context.VIBRATOR_SERVICE` **字符串** —— 传 `autoclass("android.os.Vibrator")`
    那个 Class 对象在 pyjnius 下匹配不到 `getSystemService(Class<T>)` 重载, 会静默失败
    (整段被 try/except 吞掉, 表现为"权限也给了、代码也跑了, 就是不震")。
    ⚠️ 代理失效(Binder 断了)时 `vibrate` 会抛 —— 那时把缓存清掉, 下次重新取。
    """
    v = _VIB_PROXY[0]
    if v is not None:
        return v
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        _VIB_PROXY[0] = v = activity.getSystemService(Context.VIBRATOR_SERVICE)
    except Exception:
        v = None
    return v


def _vibrate_now(ms, amp):
    """真正那一次震动调用(只在工作线程上跑)。带单次计时, 供跑分面板归因。"""
    _t0 = time.perf_counter()
    try:
        vib = _vib_get()
        if vib is None:
            return
        try:
            from jnius import autoclass
            VibrationEffect = autoclass("android.os.VibrationEffect")
            vib.vibrate(VibrationEffect.createOneShot(
                ms, amp))     # 满振幅 255; DEFAULT_AMPLITUDE(-1) 约 50%, 太弱
        except Exception:
            vib.vibrate(ms)                  # API < 26: 没有 VibrationEffect
    except Exception:
        _VIB_PROXY[0] = None                 # 代理失效: 清缓存, 下次重取
    finally:
        _dt = time.perf_counter() - _t0
        _VIB_STAT[0] += _dt
        if _dt > _VIB_STAT[1]:
            _VIB_STAT[1] = _dt
            _VIB_STAT[2] = "%dms" % ms


def _vib_worker():
    """震动工作线程。队列满就**丢这一次**(与发声同策) —— 绝不阻塞主线程。"""
    while True:
        try:
            item = _VIB_Q.get()
        except Exception:
            return
        if item is None:
            return
        # 两种形状: ("d", ms, gap, amp) = 双震; (ms, amp) = 单次(老形状保留, 防漏改)。
        if len(item) == 4 and item[0] == "d":
            _vibrate_double_now(item[1], item[2], item[3])
        else:
            _vibrate_now(item[0], item[1])


def _vib_warm():
    """启动期把震动这条路**焐热**: 建工作线程 + 预取 Vibrator 代理。

    ⚠️ **为什么必须预热**(2026-09-14 玩家报"v0.6.66 比 v0.6.65 差"之后补的):
    工作线程原本是**第一次震动时才建**的, 而第一次震动正好落在**第一次发射**那一刻 ——
    也就是跑分采样窗口**里面**。于是那一帧要现付: 建线程 + 首次 JNI 调用的
    `AttachCurrentThread`(安卓上可能几十毫秒, 还要 JVM 锁) + 首次 `getSystemService`。
    1%Low 只统计最慢的 1%(约 12 帧), **一帧 200ms 就能把那一档的均值明显拉下去**。
    这和本工程其它预热(玻璃贴图/球纹理/字形表)是同一条规矩: **别在采样/中奖那帧现做**。
    """
    if platform != "android":
        return
    global _VIB_Q
    if _VIB_Q is None:
        with _VIB_LOCK:
            if _VIB_Q is None:
                try:
                    import queue as _queue
                    _VIB_Q = _queue.Queue(maxsize=32)
                    threading.Thread(target=_vib_worker, daemon=True).start()
                except Exception:
                    _VIB_Q = False
    _vib_get()                            # 顺手把系统服务代理也取回来


def _vibrate(ms, amp=255):
    """单次震动(仅 Android; 其它平台静默)。需要 buildozer.spec 的 VIBRATE 权限。

    ⚠️ **投递即返回, 不等 IPC** —— 见上面那段说明。`amp` 只在 API 26+ 生效;
    老机器退回 `vibrate(ms)`, 振幅由系统定。
    """
    if platform != "android":
        return
    global _VIB_Q
    _FRAME_PROBE[1] += 1                     # 逐帧计数记在**发起**这一刻(工作线程不再属于某一帧)
    if _VIB_Q is None:
        with _VIB_LOCK:
            if _VIB_Q is None:
                try:
                    import queue as _queue
                    _VIB_Q = _queue.Queue(maxsize=32)
                    threading.Thread(target=_vib_worker, daemon=True).start()
                except Exception:
                    _VIB_Q = False       # 建不起来: 退回同步调用, 绝不静默失震
    if _VIB_Q is False:
        _vibrate_now(ms, amp)
        return
    try:
        _VIB_Q.put_nowait((ms, amp))
    except Exception:
        pass

# ============ 方向守卫 / 沉浸重申: 搬出主线程(2026-09-14) ============
# 病根与判据见下面 `_guard_post`。这里先给结论: 这两条链原来**每 0.7 秒各在主线程跑一次**,
# 而且是全 app 唯一的**常驻周期性主线程 JNI**。JNI/Binder 在本工程已实测过是灾难级的慢
# (`SoundPool.play()` 单次 143.6ms), 所以它们是"每帧实算只有 4.7ms、却有一批不分阶段的
# 慢帧"的头号嫌疑。改法与发声/震动同策, 而且是本工程**已经验证过两次**的那套:
# 主线程只投递, 工作线程去付 IPC 的钱; 建不起队列就退回同步(= 今天的行为), 绝不静默失效。
_GUARD_Q = None                  # 守卫工作队列(None = 还没建, False = 建不起来)
_GUARD_LOCK = threading.Lock()


def _guard_orient_now():
    """方向守卫的**真身**(只在工作线程上跑)。逻辑与原来一字不差。"""
    from jnius import autoclass
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    if _device_is_wide():
        rot = act.getWindowManager().getDefaultDisplay().getRotation()
        if rot in (1, 3):
            act.setRequestedOrientation(10)
    else:
        act.setRequestedOrientation(7)


def _guard_immersive_now():
    """沉浸重申的**真身**(只在工作线程上跑)。

    ⚠️ 真正的 View 操作本来就已经在 UI 线程上(`runOnUiThread` 投递), 这里搬走的只是
    **`runOnUiThread` 这一次 JNI 调用**。
    ⚠️ `_immersive_task()` 是类级缓存 + 启动期预热过(见 prebake_step), 所以这里只是一次
    属性读 —— **绝不在工作线程上现造 PythonJavaClass**(那一步注册 Java 代理, 留在主线程)。
    """
    from jnius import autoclass
    act = autoclass("org.kivy.android.PythonActivity").mActivity
    act.runOnUiThread(PlinkoApp._immersive_task())


def _guard_worker():
    """守卫工作线程。队列满就**丢这一次** —— 两条链都是幂等的, 丢一次毫无影响。"""
    while True:
        try:
            item = _GUARD_Q.get()
        except Exception:
            return
        if item is None:
            return
        _t0 = time.perf_counter()
        try:
            if item == "orient":
                _guard_orient_now()
            else:
                _guard_immersive_now()
        except Exception:
            _JNI_STAT[5] += 1                 # 失败次数要看得见(原来被 except 静默吞掉)
        _dt = time.perf_counter() - _t0
        _JNI_STAT[3] += _dt
        if _dt > _JNI_STAT[4]:
            _JNI_STAT[4] = _dt


def _guard_warm():
    """启动期焐热: 建工作线程 + 预热沉浸 Runnable。

    ⚠️ **预热 Runnable 必须在主线程做**(它要注册 Java 代理)。顺手也把
    `org.kivy.android.PythonActivity` 的 autoclass 查表热一次 —— 工作线程第一次用
    不必现付 JNI 的 AttachCurrentThread。
    """
    if platform != "android":
        return
    global _GUARD_Q
    if _GUARD_Q is None:
        with _GUARD_LOCK:
            if _GUARD_Q is None:
                try:
                    import queue as _queue
                    _GUARD_Q = _queue.Queue(maxsize=16)
                    threading.Thread(target=_guard_worker, daemon=True).start()
                except Exception:
                    _GUARD_Q = False
    try:
        from jnius import autoclass
        autoclass("org.kivy.android.PythonActivity")
        PlinkoApp._immersive_task()           # 主线程上把代理建好
    except Exception:
        pass


def _guard_post(tag):
    """把一次守卫投给工作线程。返回 True = 已投递(主线程没有付 IPC 的钱)。"""
    global _GUARD_Q
    _JNI_STAT[2] += 1
    if _GUARD_Q is None:
        with _GUARD_LOCK:
            if _GUARD_Q is None:
                try:
                    import queue as _queue
                    _GUARD_Q = _queue.Queue(maxsize=16)
                    threading.Thread(target=_guard_worker, daemon=True).start()
                except Exception:
                    _GUARD_Q = False
    if _GUARD_Q is False:
        return False                          # 建不起来: 调用方退回同步
    try:
        _GUARD_Q.put_nowait(tag)
        return True
    except Exception:
        return False                          # 队列满: 丢这一次(幂等), 不回主线程补


# ---- 设定落盘: 搬出主线程(2026-09-14) ----
# 病根: `_save_config()` 是 open + json.dump + close, **每球一次**(settle 里调), 跑在主线程。
# 安卓上这是一次**阻塞的文件写**(走 FUSE), 而它落在"落袋"那一帧 —— 同一帧还要做结算、
# 排揭晓、槽位白闪。这一类"事件路径上的阻塞调用"本工程已经栽过两次
# (`SoundPool.play()` 143.6ms / 震动 Binder), 修法也都是同一套: 主线程只投递。
# ⚠️ **只保留最新一份**(后写覆盖先写): 配置是"当前状态"不是流水账, 中间态没有保留价值,
#    所以不需要队列、不需要去重, 一个格子 + 一个 Event 就够。
# ⚠️ 落盘走 **临时文件 + `os.replace`** 原子替换 —— 写一半被杀不会留下半个 JSON。
#    老写法 `open(path, "w")` 是先截断再写, 那种时刻被杀就是文件损坏(下次启动读不出来,
#    白名单一挡 = 进度清零)。这算顺带修的一个真 bug, 不只是性能。
# ⚠️ 切后台(`on_pause`)会 flush 一次, 保证切走时一定落了盘。
_CFG_EVT = None
_CFG_PENDING = [None]        # (cfg, path) 或 None
_CFG_OK = [None]             # None=还没建, True=工作线程可用, False=建不起来
_CFG_STAT = [0.0, 0.0, 0]    # [累计秒, 单次最慢秒, 次数]


def _cfg_worker():
    while True:
        try:
            _CFG_EVT.wait()
        except Exception:
            return
        _CFG_EVT.clear()
        item = _CFG_PENDING[0]
        _CFG_PENDING[0] = None
        if item is None:
            continue
        cfg, path = item
        _t0 = time.perf_counter()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f)
            os.replace(tmp, path)
        except Exception:
            pass
        _d = time.perf_counter() - _t0
        _CFG_STAT[0] += _d
        _CFG_STAT[2] += 1
        if _d > _CFG_STAT[1]:
            _CFG_STAT[1] = _d


def _cfg_warm():
    global _CFG_EVT
    if _CFG_OK[0] is not None:
        return
    try:
        _CFG_EVT = threading.Event()
        threading.Thread(target=_cfg_worker, daemon=True).start()
        _CFG_OK[0] = True
    except Exception:
        _CFG_OK[0] = False


def _cfg_post(cfg, path):
    """把一份设定交给工作线程落盘。返回 True = 已投递(主线程不付那次文件写)。"""
    _cfg_warm()
    if _CFG_OK[0] is not True:
        return False
    _CFG_PENDING[0] = (cfg, path)
    try:
        _CFG_EVT.set()
        return True
    except Exception:
        return False


def _cfg_flush(timeout=1.0):
    """等挂着的设定落完盘(切后台/退出前)。**绝不长等** —— 超时就放弃。"""
    if _CFG_OK[0] is not True:
        return
    _t0 = time.perf_counter()
    while _CFG_PENDING[0] is not None and time.perf_counter() - _t0 < timeout:
        time.sleep(0.005)


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

def _vibrate_double_now(ms=35, gap=40, amp=255):
    """短促双震的**真身**(只在工作线程上跑)。

    ⚠️ 2026-09-14 从 `_vibrate_double` 里抽出来 —— 原来它是**同步 JNI**: 在调用线程上直接
       `autoclass` + `activity.getSystemService(VIBRATOR_SERVICE)`(**这本身就是一次 Binder
       往返**) + `vibrate`。而它唯一的调用点是**彩蛋路径的 `settle` 那一帧**。
       同一条规矩本工程已经执行过三次(发声 665 / 震动 666 / 守卫 671 / 落盘 677):
       **事件路径上的阻塞调用一律挪到工作线程**。
    ⚠️ 顺带复用 `_vib_get()` 的缓存代理 —— 原来每次都要现取一次系统服务。
    """
    t0 = time.perf_counter()
    try:
        vib = _vib_get()
        if vib is None:
            return
        try:
            from jnius import autoclass
            VibrationEffect = autoclass("android.os.VibrationEffect")
            v = VibrationEffect.createWaveform([0, ms, gap, ms], [0, amp, 0, amp], -1)
            vib.vibrate(v)
        except Exception:
            vib.vibrate(ms * 2 + gap)          # 退回单次(近似时长)
    except Exception:
        _VIB_PROXY[0] = None
    finally:
        _dt = time.perf_counter() - t0
        _VIB_STAT[0] += _dt
        if _dt > _VIB_STAT[1]:
            _VIB_STAT[1] = _dt
            _VIB_STAT[2] = "双震%dms" % ms


def _vibrate_double(ms=35, gap=40, amp=255):
    """短促双震(彩蛋用): 两下短脉冲, 手机读作"发现惊喜"; 区别于单次长震的大奖之感。
    仅 Android; **投递即返回**(见 `_vibrate_double_now` 处说明)。
    ⚠️ 队列建不起来就退回同步调用 —— 与改之前逐字相同的行为, 绝不静默失震。
    """
    if platform != "android":
        return
    global _VIB_Q
    if _VIB_Q is None:
        _vib_warm()
    if _VIB_Q is False:
        _vibrate_double_now(ms, gap, amp)
        return
    try:
        _VIB_Q.put_nowait(("d", ms, gap, amp))
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

    def _reassert_high_refresh(self, *_):
        """弹窗切换后重申高刷新率：自适应刷新率设备会把静态 Modal 降回 60Hz。"""
        if platform != "android":
            return
        try:
            _apply_fps_cap()
        except Exception:
            return
        # Popup 的淡入/淡出会在下一小段时间才真正提交到 Surface；稳定后再请求一次，
        # 防止系统在布局切换时覆盖 Window 的 frame-rate / display-mode 偏好。
        try:
            Clock.schedule_once(lambda *_: _apply_fps_cap(), 0.35)
        except Exception:
            pass

    def _arm_high_refresh(self):
        self._reassert_high_refresh()
        if not getattr(self, "_high_refresh_bound", False):
            self._high_refresh_bound = True
            self.bind(on_dismiss=self._reassert_high_refresh)

    def open(self, *_args, **kwargs):
        self._arm_high_refresh()
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


# 阶段条: 每一帧"游戏在干什么"的取色与图例顺序。
# ⚠️ 名字必须与 `RootWidget._bench_tag()` 的返回值**逐字一致** —— 那张表是唯一的真源,
#    这里只是给它配颜色。改 `_bench_tag` 的文案就要回来改这里(对不上会退化成灰色)。
# 取色按语义挑(浅底 0.97 上的中饱和色): 蓝=主玩法, 金=中奖演出, 绿=落袋, 砖红=玩家在按,
# 灰=哑火, 浅灰蓝="什么都没发生"(待机)。
STAGE_ORDER = ("飞行", "装杯", "落袋", "蓄力", "哑火", "待机")
STAGE_COLORS = {
    "飞行": "#3563d1",
    "装杯": "#f0b000",
    "落袋": "#39d98a",
    "蓄力": "#e0533b",
    "哑火": "#5a6a8c",
    "待机": "#b9c3d6",
}

class FpsCurve(Widget):
    """跑分结束后的逐帧提交曲线；横轴每一点都是一个原始 on_flip 帧。

    曲线下方另有一条**阶段条**: 每一帧"游戏当时在干什么", 与曲线**同一根时间轴对齐** ——
    这样"哪几帧在掉"和"那几帧在演什么"是上下对着看的, 不用再去对表格。
    """

    def __init__(self, gaps_ms, cap_fps=120.0, tags=None, **kw):
        super().__init__(**kw)
        self._gaps = [max(0.01, float(x)) for x in (gaps_ms or [])]
        # 每一帧的场景标签(飞行/装杯/…), 与 `_gaps` **同序等长** —— 两个都来自同一批
        # `on_flip` 采样(见 `_on_flip` 往 `_bench_frames` 里存的那对值)。
        # ⚠️ 长度对不上就当没有: 曲线照画, 只是不画阶段条 —— 绝不让诊断把曲线本身搞没。
        _tg = list(tags or [])
        self._tags = _tg if len(_tg) == len(self._gaps) else []
        self._cap = max(1.0, float(cap_fps or 120.0))
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def _legend_rows(self, pw):
        """图例按可用宽度折行(360dp 上六个阶段一行放不下)。

        只列**这一轮真出现过的**阶段 —— 没哑火就少一项, 图例自然更短。
        返回 [[(阶段名, 行内x偏移), ...], ...]。
        """
        _names = [n for n in STAGE_ORDER if n in set(self._tags)]
        if not _names:
            return []
        _sw, _pad_name, _pad_item = dp(6.0), dp(6.0), dp(7.0)
        _rows, _cur, _cur_w = [], [], 0.0
        for _n in _names:
            _w = _sw + dp(3.0) + len(_n) * dp(10.0) + _pad_name + _pad_item
            if _cur and _cur_w + _w > pw:
                _rows.append(_cur)
                _cur, _cur_w = [], 0.0
            _cur.append((_n, _cur_w))
            _cur_w += _w
        if _cur:
            _rows.append(_cur)
        return _rows

    @staticmethod
    def _label(canvas, text, x, y, anchor="left"):
        lb = CoreLabel(text=text, font_size=sp(10), color=(0.37, 0.40, 0.45, 1))
        lb.refresh()
        tw, th = lb.texture.size
        if anchor == "right":
            x -= tw
        elif anchor == "center":
            x -= tw / 2.0
        with canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=lb.texture, pos=(x, y), size=(tw, th))

    def _draw(self, *_):
        if self.width < 40 or self.height < 40:
            return
        pad_l, pad_r, pad_t = dp(32), dp(8), dp(18)
        pw = max(1.0, self.width - pad_l - pad_r)
        # ⚠️ 底部留白**按内容算**: 阶段条下面是图例, 而图例在 360dp 上会折成两行 ——
        #    写死 pad_b 的话, 折行的第二行会盖到时间轴上(或掉出控件外面)。
        _strip = dp(7.0)
        _rows = self._legend_rows(pw)
        pad_b = dp(20) + dp(4) + _strip + ((dp(4) + dp(13) * len(_rows)) if _rows else 0.0)
        x0, y0 = self.x + pad_l, self.y + pad_b
        ph = max(1.0, self.height - pad_b - pad_t)
        # 固定 0~120（或设备请求的更高档）坐标，跨轮次的曲线才可直接比较。
        lo, hi = 0.0, max(120.0, self._cap)

        self.canvas.clear()
        with self.canvas:
            Color(0.97, 0.975, 0.985, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(7)])
            for value in range(0, int(hi) + 1, 30):
                y = y0 + value / hi * ph
                Color(0.72, 0.74, 0.77, 0.85)
                Line(points=[x0, y, x0 + pw, y], width=1)
            Color(0.50, 0.52, 0.56, 0.85)
            Line(points=[x0, y0, x0 + pw, y0], width=1)
            Line(points=[x0, y0, x0, y0 + ph], width=1)

            if self._gaps:
                total_ms = sum(self._gaps)
                elapsed = 0.0
                raw_points = []
                # 曲线本身不降采样：每一个原始帧间隔都有一个点，x 按真实累计时间定位。
                # 例如 25 秒、120fps 的一轮大约就是 3000 个原始点。
                for gap in self._gaps:
                    value = max(lo, min(hi, 1000.0 / gap))
                    x = x0 + elapsed / max(0.01, total_ms) * pw
                    y = y0 + value / hi * ph
                    raw_points.extend([x, y])
                    elapsed += gap
                # 浅底上使用深灰蓝：逐帧尖峰足够清楚，又不抢走坐标与统计信息。
                Color(0.24, 0.29, 0.35, 1)
                Line(points=raw_points, width=1.15, joint="round")

                # ---- 阶段条: 每一帧"游戏在演什么", 与曲线同一根时间轴 ----
                # ⚠️ 按**连续同标签合并成段**再画, 不是一帧一个矩形: 34.5 秒 5600 帧铺在
                #    300~700px 上是 8~19 帧/像素, 逐帧画会画出一大堆亚像素矩形(又慢又糊)。
                #    合并之后通常只剩几十段。合并用的是**真实累计时长**, 与曲线的 x 同一算法 ——
                #    所以曲线往下掉的尖峰, 正下方那一段就是它当时在演什么。
                _runs, _t_run, _start, _acc = [], None, 0.0, 0.0
                for _i, _gap in enumerate(self._gaps):
                    _tg = self._tags[_i]
                    if _tg != _t_run:
                        if _t_run is not None:
                            _runs.append((_t_run, _start, _acc - _start))
                        _t_run, _start = _tg, _acc
                    _acc += _gap
                if _t_run is not None:
                    _runs.append((_t_run, _start, _acc - _start))
                _sy = y0 - dp(4.0) - _strip
                for _tg, _st, _dur in _runs:
                    _rx1 = x0 + _st / max(0.01, total_ms) * pw
                    _rx2 = x0 + (_st + _dur) / max(0.01, total_ms) * pw
                    Color(*hex_rgb(STAGE_COLORS.get(_tg, COL_GRAY)) + (1,))
                    Rectangle(pos=(_rx1, _sy), size=(max(0.6, _rx2 - _rx1), _strip))

        self._label(self.canvas, "%.0f" % hi, x0 - dp(5), y0 + ph - dp(5), "right")
        for value in range(0, int(hi), 30):
            self._label(self.canvas, "%.0f" % value, x0 - dp(5),
                        y0 + value / hi * ph - dp(5), "right")
        duration = sum(self._gaps) / 1000.0
        self._label(self.canvas, "0s", x0, self.y + dp(2))
        self._label(self.canvas, "%.1fs" % duration, x0 + pw, self.y + dp(2), "right")

        # ---- 阶段条的图例(只列这一轮真出现过的阶段) ----
        # ⚠️ 画在 `with self.canvas` 块**之后**: 这样它在最上层, 且与上面那批坐标标签
        #    同一种写法(都是往 `self.canvas` 追加指令)。
        if _rows and self._tags:
            for _ri, _row in enumerate(_rows):
                _ly = y0 - dp(4.0) - _strip - dp(3.0) - dp(13.0) * (_ri + 1)
                for _n, _ox in _row:
                    with self.canvas:
                        Color(*hex_rgb(STAGE_COLORS.get(_n, COL_GRAY)) + (1,))
                        Rectangle(pos=(x0 + _ox, _ly + dp(2.0)),
                                  size=(dp(6.0), dp(6.0)))
                    self._label(self.canvas, _n, x0 + _ox + dp(9.0), _ly - dp(1.0))

class GlyphLabel(Widget):
    """把"Kivy 文字光栅化"换成"预烘字形图集 + 画布染色"。**零 `texture_update`、零填纹。**

    为什么是一个新控件而不是给 `Label` 换纹理: `Label` 的纹理由 Kivy 的 `texture_update`
    管着, 而那条路正是要消灭的东西(第二趟光栅化 4~10.7 毫秒, 由 Texture 回调在"纹理下次
    被用到时"触发, 跑在 `_frame` 外面)。这里**根本不产生文字纹理** —— 每个字是一条
    `Rectangle` 贴预先烘好的白色字形贴图。

    ⚠️ 它不是 `Label` 子类, 是故意的:
       · 不碰 `style.kv` 里那条 `Rectangle(texture=self.texture)`(那个在 `texture=None` 时
         会画一块**实心色块**);
       · 不碰全局那个 `Label.texture_update` 补丁(见 `_texupd_wrap`)。
    ⚠️ `_glyph_alpha0` = 这个控件基色的 alpha。阴影基色是 `(0,0,0,0.6)` ⇒ 淡出必须乘 0.6,
       见 `_fx_fade_set`。**这是老路上不存在的一步**(老路把 0.6 烘在纹理里)。

    ==========================================================================
    ⚠️⚠️ **2026-09-15: 这条路做完了但暂时关着(`_GLYPH_ON = False`, 见那里的说明)。
       卡在一个我**没能解释**的 1 像素差上 —— 打开之前必须先把它解释掉。**
    ==========================================================================
    已经**证明**的部分(都是实测, 不是推断):
      · 图集纹理与"整串渲染"**逐像素完全相同** —— 按字符对字符比过 `+2000 / 273300 /
        1234567890 / +10000 / 0`(每个字、每个串, alpha 差>3 的像素都是 **0**);
        `Label` 与 `CoreLabel` 的纹理也**完全相同**(0 差、墨迹行一致)。
        `${temp}/glyph_tex_cmp.py` 与 `temp/label_vs_corelabel.py` 是那两个探针。
      · 摆位坐标与 `Label` **完全相同**: `GlyphLabel` 的 `rect0.pos=(332,685)`,
        `Label` 的 kv 矩形 `pos=(72,685)`, 同一个 y、同一个高度 70、同一个宽 135。
      · `mag_filter` 换 `linear` / `nearest` **结果一模一样**(704 个差异像素, 一个不差)。
      · **同位置**分两次截图比对(排除跨位置疑点)结果也是 704; 单字 `"0"`(单块四边形)
        也是同一形状的差(181 个像素)。
      · `_GLYPH_ON = False` 时(本控件退化成普通 `Label` 子控件) 同位置比对是 **0 个差异像素**
        ⇒ **接线本身干净**, 差 100% 在图集这条路上。
    **没解释的部分**: 屏幕上 GlyphLabel 那一块的字, 顶边 AA **多出恰好一行**
      (墨迹 36 行 -> 37 行, 底边行号不变 ⇒ 不是整体位移, 像被拉了 1 像素)。
      纹理相同 + 矩形相同 + 过滤无关 ⇒ 按推理不该有差, 但它稳定复现
      (两次不同位置、两次运行都是 704)。
    **下一步该查的方向**(还没做): `TextureRegion` 的 `tex_coords` 在**全幅 region** 上
      是否真的恒等; 以及 Kivy 的 `Rectangle` 用 region 时有没有半像素 inset。
      `temp/glyph_same_pos.py` 是那个"同位置分两次截图"的探针, 复现只要一条命令。
    """

    text = StringProperty("")
    font_size = NumericProperty(0.0)
    bold = BooleanProperty(True)
    fit_box = BooleanProperty(False)      # True: 像 HUD 那样排在"控件矩形"里(余额用), 见 `_glyph_place`

    def __init__(self, color=(1, 1, 1, 1), **kw):
        self._rgba0 = tuple(color)
        self._glyph_alpha0 = float(self._rgba0[3])
        self._quads = None
        self._text_w = 0
        self._fb = None                   # 退化用的普通 Label(见 `_degrade`)
        self._degraded = False
        self.padding = [0, 0, 0, 0]       # `_fit1` 会读它
        super().__init__(**kw)
        with self.canvas:
            self._gcol = Color(rgba=self._rgba0)      # ⚠️ 唯一一条 Color ⇒ `_lbl_canvas_color` 必找到它
            self._grects = [Rectangle(size=(0.0, 0.0)) for _ in range(_GLYPH_MAX_CHARS)]
        self.bind(text=self._glyph_sync, font_size=self._glyph_sync, bold=self._glyph_sync,
                  pos=self._glyph_place, size=self._glyph_place)
        self._glyph_sync()

    def _degrade(self):
        """图集没这一档(或出现字符集外的字)时, 挂一个**普通 Label 当孩子** —— 行为与旧版一致。

        ⚠️ 只挂一次, 且**不撤**回到图集: 一个标签一局里要么一直走图集, 要么一直走老路。
           中途来回换会让"这一帧到底画了哪个"变成不确定的东西, 逐像素比对就没法做了。
        """
        if self._degraded:
            return
        self._degraded = True
        _GLYPH_MISS[0] += 1
        try:
            self._fb = Label(text=self.text, font_size=self.font_size, bold=self.bold,
                             color=self._rgba0,
                             halign=("left" if self.fit_box else "center"),
                             valign="middle", size_hint=(None, None))
            self._fb.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            if self.fit_box:
                self._fb.size = self.size
                self._fb.pos = (0.0, 0.0)
                self.bind(size=lambda *a: setattr(self._fb, "size", self.size))
            else:
                self._fb.center = self.center
                self.bind(center=lambda *a: setattr(self._fb, "center", self.center))
            self.add_widget(self._fb)
        except Exception:
            pass

    def _glyph_sync(self, *_a):
        # ⚠️ **只在这里**还原画布颜色。淡出会改它的 alpha(`_fx_fade_set`), 放到 `_glyph_place`
        #    里重置会让大字**永不淡出**(而那是每帧都在摆位的路径)。
        self._usecol = self._rgba0
        try:
            self._gcol.rgba = self._rgba0
        except Exception:
            pass
        if self._fb is not None:
            self._fb.text = self.text
            self._fb.font_size = self.font_size
            return
        _rec = _glyph_rec(self.font_size, self.bold)
        _q = _glyph_quads(_rec, self.text) if _rec is not None else None
        if _q is None:
            self._quads = None
            for _r in self._grects:
                _r.size = (0.0, 0.0)
            self._degrade()
            return
        self._quads, self._text_w = _q
        _GLYPH_HIT[0] += 1
        self._glyph_place()

    def _glyph_place(self, *_a):
        """摆位。⚠️ **绝不碰 `_gcol`** —— 中奖大字每帧都在改 center(上浮), 在这里重置颜色 = 永不淡出。

        坐标照抄 `style.kv` 那条 `Rectangle` 的算法(`pos = int(center - texture_size/2)`),
        **用 `int()` 截断, 不是 `math.floor`**(负数方向两者不同)。

        ⚠️⚠️ **必须从 `x/y/width/height` 现算中心, 不能读 `self.center`**(2026-09-15 实踩,
        桌面探针抓出来的, 症状是"字画在屏幕左下角、x 对但 y 不动"):
           `Widget.center` 是 `ReferenceListProperty(center_x, center_y)`, 而 `center_x`/`center_y`
           是 **`cache=True` 的 `AliasProperty`**。给 `w.center = (a, b)` 赋值时它会**先设
           `center_x` 再设 `center_y`**, 而设 `center_x` 那一下就会派发 `pos` ⇒ 我们的回调
           **在 `center_y` 还没设之前**跑了一次; 等 `center_y` 真设好时, 别名缓存还没失效,
           回调里读到的仍是**旧值** ⇒ 只有 x 跟着动, y 永远停在初始化那一次的值。
           读主属性就没有这层缓存。`int(center - size/2)` 与 `int(x + width/2 - size/2)`
           在整数 `size` 下完全等价(`Widget` 的 x/y/width/height 恒为整数或 .0)。
        """
        if not self._quads:
            return
        _rec = _GLYPH_ATLAS.get(_glyph_key(self.font_size, self.bold))
        if _rec is None:
            return
        _th = _rec["h"]
        _cx = self.x + self.width / 2.0
        _cy = self.y + self.height / 2.0
        if self.fit_box:
            # 排进"控件矩形": 横向 `halign='left'` ⇒ x 从左边起; 纵向 `valign='middle'`.
            _x0 = int(_cx - self.width / 2.0) + int(self.padding[0])
            _y0 = int(_cy - self.height / 2.0) + int((self.height - _th) / 2.0)
        else:
            # 居中: 老路上的可见矩形就是 `texture_size`(= 逐字宽之和 x 字高)。
            _x0 = int(_cx - self._text_w / 2.0)
            _y0 = int(_cy - _th / 2.0)
        for _i, _r in enumerate(self._grects):
            if _i < len(self._quads):
                _t, _dx, _w = self._quads[_i]
                _r.texture = _t
                _r.size = (_w, _th)
                _r.pos = (_x0 + _dx, _y0)
            else:
                _r.size = (0.0, 0.0)


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
        self._slot_txt_cols = []      # 9 个槽的"倍率文字颜色"指令(供 _update_slots 原地改)
        self._slot_txt_rects = []     # 9 个槽的"倍率文字"矩形(空的 size=(0,0))
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
        self._bench_badge = None      # 跑分中的固定提示牌（不参与飘字动画）
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
        if self._bench_badge is not None:
            chain.append(self._bench_badge)
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

    def _update_slots(self):
        """**只更新倍率槽**的颜色与文字, 不清画布、不重建那 ~345 条指令。

        什么时候能这么干: 盘面**几何没变**、只有倍率变了 —— 也就是"球落定后重掷盘面"。
        真机实测(0.6.90 面板): 整块 `_redraw()` 里的 `重掷` 一段是 **9.2 / 7.6 毫秒**,
        而它落在球落定那一帧上, 是最慢三帧里的头号子步骤。
        ⚠️ 结构对不上(首帧 / 刚改过尺寸 / 槽数变了)就**退回完整 `_redraw()`** —— 绝不半更新。
        """
        g = self.game
        try:
            ok = (len(self._slot_cols) == NUM_SLOTS
                  and len(self._slot_txt_cols) == NUM_SLOTS
                  and len(self._slot_txt_rects) == NUM_SLOTS)
        except Exception:
            ok = False
        if not ok:
            self._redraw()
            return
        fs = max(12, int(20 * self._s))
        cy = (SLOT_TOP + FLOOR) / 2.0
        for i in range(NUM_SLOTS):
            m = g.multipliers[i]
            try:
                self._slot_cols[i].rgb = hex_rgb(slot_color(m))
                self._slot_txt_cols[i].rgb = hex_rgb(slot_txt(m))
                _r = self._slot_txt_rects[i]
                if m > 0:
                    _tex = slot_text_tex(m, fs)
                    if _tex is not None:
                        _r.texture = _tex
                        _r.size = _tex.size
                        _r.pos = (self._px(FIELD_L + (i + 0.5) * SLOT_W) - _tex.width / 2.0,
                                  self._py(cy) - _tex.height / 2.0)
                else:
                    _r.size = (0, 0)          # 空槽藏起来(与 `_redraw` 的 size 0 一致)
            except Exception:
                pass
        # 与 `_redraw` 同义: 重掷后槽位白闪作废(它在 `tick_draw` 里读 `_pulse`)
        self._pulse = None
        # ⚠️ **与 `_redraw` 同义的第二件事: 换盘面熄灭全部投中指示灯**。这里曾经漏掉 ——
        #    玩家报「进入倍率槽的时候, 有一个红点和绿点代表有没有命中, 这个点应该在结算之后
        #    就消失, 实际并没有消失」。病根: 灯本来就只在 `_redraw()` 重建 `_lamp_cols` 时
        #    被顺手重置成 `COL_LAMP_OFF`, 而 `park_ball(reroll=True)` 从整块 `_redraw()` 换成
        #    增量 `_update_slots()` 之后, 这层遮盖就没了 ⇒ 灯**每局点亮一格、从不熄灭**,
        #    几局下来槽上攒出一片红绿点。`else`(哑火)那条路上的 `lamps_off()` 救不了它。
        #    ⚠️ 与上面那三张表不同: `_lamp_cols` 的条数由 `_redraw` 建, 这里只按**现有条数**
        #       熄灭(与 `lamps_off()` 同一套写法), 所以不参与上面那个结构守卫, 也不会半更新。
        self.lamps_off()

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
            # 槽倍率文字(走 `slot_text_tex` 的缓存; 逻辑 20px 跟盘面缩放, 手机上≈11sp)
            # ⚠️ **9 个槽全都建**(空的用 size=(0,0) 藏起来, 视觉完全等价) —— 因为倍率的
            #    "哪几个槽有奖"每次重掷都会变(`roll_multipliers` 会重新 `random.sample` 位置),
            #    只给非空槽建的话, 条数会变, 就没法在原地更新了(见 `_update_slots`)。
            self._slot_txt_cols = []
            self._slot_txt_rects = []
            fs = max(12, int(20 * s))
            cx = FIELD_L + 0.5 * SLOT_W
            cy = (SLOT_TOP + FLOOR) / 2.0
            for i in range(NUM_SLOTS):
                m = g.multipliers[i]
                self._slot_txt_cols.append(Color(*hex_rgb(slot_txt(m))))
                if m > 0:
                    tex = slot_text_tex(m, fs)
                    _r = Rectangle(texture=tex,
                                   pos=(self._px(FIELD_L + (i + 0.5) * SLOT_W) - tex.width / 2.0,
                                        self._py(cy) - tex.height / 2.0),
                                   size=tex.size)
                else:
                    _r = Rectangle(texture=None, pos=(0, 0), size=(0, 0))
                self._slot_txt_rects.append(_r)
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
        self._place_bench_badge()
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
        # 同上: 手绘文字, 按可见宽缩字号(隐藏档 5000% 时 "+500000" 会比屏幕还宽)
        size = fit_font_size(text, size, max(80.0, float(self.width) * 0.94), True)
        # ---- 白字图集路线: 彩色大字与黑影子**共用同一批字形贴图** ----
        # ⚠️ 老路上 Kivy 把颜色烘进纹理, 所以 main/shadow 是**两张图、两次光栅化**, 而且
        #    就落在同一帧(真机实测那一帧的 `填纹` 9.2~10.9 毫秒)。图集烘成白色之后,
        #    两者只差画布上那一条 `Color`。
        # ⚠️ 走不了(未中 / 没烘到这一档)就**原样退回 Label**, 一个字都不改 ——
        #    `_glyph_quads` 对 `未中` 返回 None 是硬边界, 见那里的说明。
        _grec = _glyph_rec(size, True)
        if _grec is not None and _glyph_quads(_grec, text) is not None:
            main = GlyphLabel(text=text, font_size=size, bold=True,
                              color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
            shadow = GlyphLabel(text=text, font_size=size, bold=True,
                                color=(0, 0, 0, 0.6), size_hint=(None, None))
        else:
            main = Label(text=text, font_size=size, bold=True,
                         color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
            main.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            shadow = Label(text=text, font_size=size, bold=True,
                           color=(0, 0, 0, 0.6), size_hint=(None, None))
            shadow.bind(size=lambda w, _: setattr(w, "text_size", w.size))
            _tag_texupd(main, "结算大字")
            _tag_texupd(shadow, "结算阴影")
        # ⚠️ **缩放入场走 GPU `Scale`, 绝不再逐帧写 `font_size`**(2026-09-13 性能优化)。
        #    原来 tick_draw 里写 `main.font_size = fs`(fs 每帧都变) —— font_size 在 Kivy 里
        #    属于 `_font_properties`, 每次赋值都会触发 `_trigger_texture` ⇒ **重新测量字形
        #    宽度 + 重新光栅化整段文字 + 重建纹理 + 每帧上传新纹理**。桌面实测: 一次中奖/
        #    未中大字(1.8s)要重测 ~110 次 × 2 个 Label(main + shadow), 10 秒内 277 次,
        #    占**全部文字重建的 65%**。`Scale` 只是乘进 modelview 矩阵, 纹理一次生成、
        #    GPU 免费放大缩小。观感完全一致(1.0→1.2→1.0 的弹入曲线一个字都没改)。
        #    放大 1.2 倍用线性插值, 对粗体大字只是极轻微发虚 —— 比"缩小"安全(缩小没 mipmap
        #    会闪), 所以基准纹理仍然按 `size` 光栅化, 不做"按峰值预放大"。
        for _lb in (main, shadow):
            with _lb.canvas.before:
                PushMatrix()
                _lb._pop_sc = Scale(origin=_lb.center, x=1.0, y=1.0)
            # ⚠️⚠️ **PopMatrix 绝不能省**(2026-09-13 实修, 玩家抓到的)。
            #    Kivy 的 `Scale`/`Rotate` 是**持久变换**: 它一进画布指令流就管到"下一次被改"
            #    为止, 不会自动弹栈。而本项目的子控件画布是**绝对(窗口)坐标、父级不做平移**
            #    (见 CLAUDE.md 坐标系那条坑) ⇒ 挂在 GameArea 子控件 `canvas.before` 上的
            #    这个 Scale 会**泄漏给它之后画的每一个控件** —— 也就是 HUD 五行。
            #    实测症状: 大字活着的那 1.8s 里, 底部"信息行 + 底行"被按 `sc` 缩放位移
            #    (sc 峰值 1.2 时往下推 ~80px), **整块推出窗口, 看起来就是凭空消失**;
            #    大字一撤, Scale 跟着指令一起没了, 那两行又回来 ——
            #    玩家录像里"飞行结束后下面的文字和按钮不见了"就是它。
            #    夹住之后: 缩放只作用于这个 Label 自己的绘制(Label 没有子控件, 所以
            #    `canvas.after` 正好落在它画完的那一刻)。
            with _lb.canvas.after:
                PopMatrix()
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
        # 把两个 Label 的"画布 Color 指令"一起缓存进 effect(见 `_lbl_canvas_color`)。
        _cc = [_lbl_canvas_color(main), _lbl_canvas_color(shadow)]
        self._effects.append({"kind": "big", "ws": [main, shadow], "cols": _cc,
                              "born": time.time(),
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
        # ⚠️ 这是**手绘**文字(不在任何布局里, 自己 `size = texture_size`), 屏幕装不下
        #    就左右被切 —— 实测 360dp + 1.3 倍系统字体: "弹珠数量已调整到1000个" 量出
        #    408px 而屏只有 360px, 左右各切掉 24px。所以这里也要按**可见宽**定字号。
        _seen = max(80.0, float(self.width) * 0.94)
        _fs = fit_font_size(text, sp(size), _seen, True)
        lbl = Label(text=text, font_size=_fs, bold=True, halign="center",
                    color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
        _tag_texupd(lbl, "飘字")
        lbl.texture_update()                      # 立刻出纹理, 尺寸跟文字(center 才摆得准)
        lbl.size = lbl.texture_size
        # ⚠️ 横向同样**不减 `self.x`** —— 理由见 big_result_text 处(子控件 canvas 是绝对
        # 坐标, 竖屏 x=0 掩盖了这个错, 横屏会整体左移一个 GameArea.x)。
        cx = self._px(CW / 2.0)
        cy = self._py(CH / 2.0 - 40) - self.y
        lbl.center = (cx, cy)
        self.add_widget(lbl)
        self._effects.append({"kind": "toast", "ws": [lbl],
                              "cols": [_lbl_canvas_color(lbl)], "born": time.time(),
                              "life": life, "rgb": hex_rgb(hexcolor),
                              "cx": cx, "cy": cy})

    def _place_bench_badge(self):
        badge = self._bench_badge
        if badge is not None:
            badge.center = (self._px(CW / 2.0), self._py(CH / 2.0) - self.y)

    def show_bench_badge(self, text):
        """跑分状态固定在盘面中心：大号红字，不置灰、不加遮挡底板。"""
        badge = self._bench_badge
        display = text.replace("\n", "　·　")
        if badge is None:
            badge = Label(text=display, font_size=sp(20), bold=True, halign="center",
                          valign="middle", color=hex_rgb(COL_FIRE) + (1,),
                          size_hint=(None, None))
            _tag_texupd(badge, "跑分提示")
            badge.texture_update()
            badge.size = badge.texture_size
            self._bench_badge = badge
            self.add_widget(badge)
        elif badge.text != display:
            badge.text = display
            badge.texture_update()
            badge.size = badge.texture_size
        self._place_bench_badge()
        self._restack_overlays()

    def hide_bench_badge(self):
        badge = self._bench_badge
        self._bench_badge = None
        if badge is not None:
            try:
                self.remove_widget(badge)
            except Exception:
                pass

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
        # ⚠️ `板面` 这块的**内部**再细分(2026-09-15 加)。起因: 真机 7 份日志里
        #    `板面` 单帧最大 **7.4 毫秒**, 而它平时只有 1~3 毫秒 —— 那一次尖峰把那一帧
        #    推到 14 毫秒, 成了卡顿帧。桌面复现不出来(桌面整块最大只 0.987 毫秒, 设备慢约
        #    37 倍, 而且这个差主要在 GL 上: 同样条数的画布指令手机驱动贵得多)。
        #    **关键线索**: `_redraw` 早就有独立标签「装杯」(`_brk_wrap`), 而 7 份日志里
        #    「装杯」**一次都没当过最大子步骤** ⇒ 尖峰**不在 `_redraw` 里**, 在 `tick_draw`
        #    的其余部分。这两格就是为了把它指名道姓, 下一轮真机日志就能看到
        #    `板面7.4 / 板·钉闪6.9` 这种形式。
        _t_ball = time.perf_counter()
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
        _brk_add("板·球", _t_ball)
        _t_peg = time.perf_counter()
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
        _brk_add("板·钉闪", _t_peg)
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
                # ⚠️ **必须和"中奖大字"用同一套 alpha 量化**(2026-09-14 修, 玩家一眼看出来的)。
                #    `color` 是 Kivy `Label._font_properties` 之一:**赋一次值就重测字形 +
                #    重光栅化整段文字 + 重建纹理 + 上传**。原来这里**每帧无条件写**, 于是飘字
                #    活着的那几秒里, 每帧都要把整段字重新栅格化一遍。
                #    性能测试不再创建自己的「测试设备性能中」飘字，避免用测试提示污染成绩；
                #    这里保留的只是真实游戏事件的提示（余额不足、数量调整等）。
                #    量化到 BIG_TEXT_ALPHA_STEPS 档: 0.35 x 3s = 1.05 秒的淡出分成 20 档,
                #    每档 5% alpha —— 配着上浮 20px/s 肉眼分辨不出台阶。
                # ⚠️ **走画布那条 Color**(零重建) —— 拿不到才退回写 `label.color` + 量化。
                #    能拿到时**每帧都写**: 反正是 uniform 写入, 淡出更顺, 不必再量化。
                if _fade_set((e.get("cols") or [None])[0], alpha):
                    pass
                else:
                    _q = int(alpha * BIG_TEXT_ALPHA_STEPS + 0.5) / float(BIG_TEXT_ALPHA_STEPS)
                    if _q != e.get("_qa"):
                        e["_qa"] = _q
                        w.color = e["rgb"] + (_q,)
                w.center = (e["cx"], e["cy"] + 20 * (now - e["born"]))
            else:
                if p < 0.5:
                    sc = 1.0 + (p / 0.5) * 0.2       # 前50%生命(1.5s): 1.0→1.2 弹入
                else:
                    sc = 1.2 - ((p - 0.5) / 0.5) * 0.2  # 后50%: 1.2→1.0 慢收
                alpha = max(0.0, 1.0 - max(0.0, p - 0.55) / 0.45)
                rise = 38 * (now - e["born"])
                main, shadow = e["ws"]
                # ⚠️ 只在**量化后的 alpha 真的变了**时才写 `color` —— 见 BIG_TEXT_ALPHA_STEPS
                #    处的说明。原来是无条件每帧写, 而 alpha 在淡出段每帧都变 ⇒ 每帧把整段
                #    大字重新光栅化一遍(桌面实测 `未中` 12 秒内重建 530 次, 占全部文字重建
                #    的 65%)。alpha 恒定那 55% 的生命里现在一次都不写。
                # ⚠️ **走画布那条 Color**(零重建, 见 `_lbl_canvas_color`)。
                #    原来这里每次改 alpha 都写 `main.color`/`shadow.color` —— 那是**两次**
                #    完整的文字重光栅化, 而 20 档量化 x 2 = 每次中奖 40 次。
                #    两个 Label 的贴图颜色在**创建时**就已经烘好了(main 是倍率色、shadow 是
                #    半透明黑), 所以这里只需要给它们乘一个 alpha。
                _cols = e.get("cols") or [None, None]
                # ⚠️ 走 `_fx_fade_set` 而**不是** `_fade_set`: 图集把阴影的基色 alpha(0.6)
                #    搬到了画布上, `_fade_set` 只写 alpha、会把 0.6 覆盖掉 ⇒ 阴影在淡出段
                #    比基线更黑。普通 `Label` 没有 `_glyph_alpha0`, 那一支与改动前完全一致。
                _ok = (_fx_fade_set(main, _cols[0], alpha) and _fx_fade_set(shadow, _cols[1], alpha))
                if not _ok:
                    # 兜底: 拿不到画布 Color 就退回老路(写 label.color + 量化), 绝不静默不淡出
                    _q = int(alpha * BIG_TEXT_ALPHA_STEPS + 0.5) / float(BIG_TEXT_ALPHA_STEPS)
                    if _q != e.get("_qa"):
                        e["_qa"] = _q
                        main.color = e["rgb"] + (_q,)
                        shadow.color = (0, 0, 0, _q * 0.6)
                main.center = (e["cx"], e["cy"] + rise)
                shadow.center = (e["cx"] + 2, e["cy"] + rise - 2)
                # 缩放 = 纯 GPU 变换(见 `_big_text` 里 `_pop_sc` 处的说明)。
                # ⚠️ `origin` 必须是**写完 center 之后**的当前中心 —— 大字一边缩放一边上浮,
                #    锚点不跟就会看到"绕着一个飘走的点放大"。shadow 多偏 (2,-2), 各自锚自己。
                for _lb in (main, shadow):
                    _ps = _lb._pop_sc
                    _ps.origin = _lb.center
                    _ps.x = _ps.y = sc

def _lbl_canvas_color(lbl):
    """取 `Label` 画布里那条 `Color` 指令 —— 用来做**不重建纹理**的淡出。

    ⚠️ 为什么需要它(2026-09-14, 玩家问"获奖后的 +xxx 也很消耗性能吧"): Kivy 把颜色**烘进
       字形纹理**(画布里那条 `Color` 实测恒为 (1,1,1,1)), 所以写 `label.color` 就等于
       **重测字形 + 重光栅化整段文字 + 重建纹理 + 上传** —— 桌面实测写一次就触发一次
       `texture_update`。而淡出期 alpha 一直在变 ⇒ 中奖大字的 20 档量化 x 2 个 Label
       = **每次中奖 40 次重建**; 飘字更糟, 原来是无条件每帧写。
    改写成那条 `Color` 的 rgba ⇒ 给**已烘好的纹理乘一个 alpha**, 只是一次 uniform 写入,
       **零重建**(实测 texture_update 计数为 0, 且截图确认像素真的变淡了)。
    ⚠️ 拿不到就返回 None, 调用方**退回老路**(写 `label.color` + 量化), 绝不静默不淡出。
    """
    try:
        from kivy.graphics import Color as _KC
        for ch in lbl.canvas.children:
            if isinstance(ch, _KC):
                return ch
    except Exception:
        pass
    return None


def _fade_set(col, alpha):
    """把那条画布 Color 的 alpha 设掉(颜色分量保持原样)。成功返回 True。"""
    if col is None:
        return False
    try:
        r, g, b, _a = col.rgba
        col.rgba = (r, g, b, alpha)
        return True
    except Exception:
        return False


def _tint_from(baked_hex, target_hex, alpha=1.0):
    """算出"画布染色"的系数: 把烘成 `baked_hex` 的纹理, 乘多少能得到 `target_hex`。

    ⚠️ **必须逐通道算, 不能拍一个 0.5 这种数**。Kivy 的着色是 **RGB 相乘**:
       最终 = 纹理RGB x 画布Color ⇒ 想要 target, 系数只能是 target/baked(逐通道)。
       三个通道的比例不一样(比如 COL_GRAY/COL_TEXT 是 0.39/0.45/0.56), 用同一个数乘会偏色。
    ⚠️ 这个公式**实测钉过**(2026-09-14, 直接烘 `CoreLabel` 读 `texture.pixels` 最亮像素):
         color=GRAY@0.6  ⇒ rgba (90, 106, 140, 153)   ← RGB 仍是 GRAY, 只有 A = 0.6 x 255
         color=TEXT@1.0  ⇒ rgba (232, 238, 252, 255)
       ⇒ **Kivy 不预乘 RGB**, 所以"烘 TEXT + 乘 GRAY/TEXT + alpha 0.6"**精确等于**老做法。
    """
    _b = hex_rgb(baked_hex)
    _t = hex_rgb(target_hex)
    return (_t[0] / max(1e-6, _b[0]), _t[1] / max(1e-6, _b[1]),
            _t[2] / max(1e-6, _b[2]), alpha)


# `_set_controls_enabled` 用的染色系数。⚠️ **必须排在 `_tint_from` 之后** ——
# 它是模块级调用, 放前面会 NameError(第一版就踩了)。
_TINT_BRIGHT = (1.0, 1.0, 1.0, 1.0)
_TINT_DIM = {"sub": _tint_from(COL_TEXT, COL_GRAY, 0.6),
             "text": _tint_from(COL_TEXT, COL_GRAY, 0.6)}


def _set_lbl_tint(lbl, rgba):
    """给标签**染色**而不重建纹理。成功返回 True; 拿不到画布那条 Color 就返回 False。

    ⚠️ 为什么不用 `lbl.color = ...`(2026-09-14): Kivy 把颜色**烘进字形纹理**, 写一次
       `label.color` 就是一次"重测字形 + 重光栅化 + 重建纹理 + 上传"(真机实测 4~7 毫秒)。
       而 `_set_controls_enabled` 每发球要改 3 个标签的颜色、而且**正好落在"回 ready"那一拍**。
       改画布那条 `Color` 的 rgba 只是一次 uniform 写入, **零重建**。这条范式工程里早就有
       (`_lbl_canvas_color` / `_fade_set`, 中奖大字的淡出就是它)。
    ⚠️ 拿不到就返回 False, 调用方**必须退回写 `lbl.color`** —— 绝不静默不染色
       (那会变成"状态卡住时看不出按钮是暗的")。
    """
    _c = _lbl_canvas_color(lbl)
    if _c is None:
        return False
    try:
        _c.rgba = tuple(rgba)
        return True
    except Exception:
        return False


def _app_version():
    """本包版本号(如 "v0.6.30"); 拿不到返回 ""。

    ⚠️ 两个来源, **都不是在这里另抄一份常数**:
      - 安卓: PackageManager 的 versionName(就是 buildozer.spec 的 version);
      - 桌面: 直接读 main.py 旁边的 `buildozer.spec` —— 出货打包用的就是同一个文件。
    (玩家 2026-09-11: 「我在pc上也需要知道版本号」。)"""
    try:
        if platform == 'android':
            from jnius import autoclass
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            pi = act.getPackageManager().getPackageInfo(act.getPackageName(), 0)
            if pi.versionName:
                return 'v%s' % pi.versionName
    except Exception:
        pass
    try:
        import os
        import re as _re
        _spec = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buildozer.spec")
        with open(_spec, "r", encoding="utf-8", errors="ignore") as _f:
            for _line in _f:
                _m = _re.match(r"\s*version\s*=\s*(\S+)", _line)
                if _m:
                    return "v%s" % _m.group(1)
    except Exception:
        pass
    return ""

def _startup_title():
    """「启动信息」那个弹窗的**标题**。玩家 2026-09-11 定稿:
    「启动信息调整  从启动信息改为 跳跳的弹珠机v0.x.x」, 随后补一句「**加一个空格**」
    ⇒ 现在是「跳跳的弹珠机 v0.x.x」(游戏名与版本号之间留一个空格)。

    ⚠️ 版本号**全工程只在这里出现一次** —— 玩家同时要求「去掉其他地方的版本号」, 原先正文里
    那行 `v0.6.30 · 于 … 制作` 的版本前缀已经删掉, 只留制作时刻(见 `_build_info`)。
    拿不到版本时退化成纯游戏名, 不留一个孤零零的 "v"。"""
    try:
        v = _app_version()
    except Exception:
        v = ""
    return ("跳跳的弹珠机 %s" % v) if v else "跳跳的弹珠机"

class RootWidget(BoxLayout):
    """游戏状态机 + 全部控件。逻辑与 tkinter 版 PlinkoApp 一一对应。"""

    def __init__(self, sfx=None, **kw):
        super().__init__(orientation="vertical", spacing=dp(10), **kw)
        self.sfx = sfx if sfx is not None else Sfx(SOUND_ENABLED)
        self.geo = build_geo()
        self._controls_enabled = True     # 输入锁(见 _set_controls_enabled / on_touch_down)
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
        self._boards = {r: roll_multipliers(r) for r in self._all_rtp()}   # 各档盘面一起生成, 切换不刷新
        self.multipliers = self._boards[self.rtp_target]
        # 声音两态: on(音效已开, 含语音播报, 默认) | off(音效已关)。
        # **不持久化** —— 不进配置文件, 每次启动都是 on(用户定稿)。
        self.sound_mode = "on"
        # 用户档位会与屏幕/系统允许的刷新率共同决定实际目标上限。
        self.fps_cap_setting = FPS_CAP_DEFAULT
        self.max_plays = 50            # 每轮次数上限
        self.round_plays = 0           # 本轮已玩次数
        self.round_history = []        # 最近完成的轮次记录
        self.bench_history = []        # 性能测试历史(最近100次)
        # SOC 高压测试的独立历史(玩家 2026-09-15: 「高压测试也专门搞个 log 记录」)。
        # ⚠️ **与性能测试分开存**: 两者的量纲不同(一个是峰值、一个是衰减),
        #    挤同一张表只会互相污染(一半格子是 0/—)。
        self.hp_history = []
        self._load_history()           # 从磁盘恢复(跨启动持久化)
        self._load_bench_history()
        self._load_hp_history()
        self._auto_reset_on_start = False
        self._load_config()            # 恢复上次的游戏设定
        _FPS_USER_CAP[0] = self.fps_cap_setting
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
        Clock.schedule_interval(self._frame_timed, FRAME_TICK_DT)
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

    def _fit1(self, w, base=None, inset=0.0):
        """把控件 w 的字号调到"当前文字**恰好一行**放得下"的最大档, 返回实际字号。

        `base` = 这一档布局的**基准字号**(由 `_apply_sizes` 按 `sp(N)*_font_scale*_ui_scale`
        算出来传进来); 不传就用上次记下的 / 当前字号 —— 于是文字一变(状态栏、余额、
        统计)也会自动重挑, 不需要每个赋值点都记得调。

        ⚠️ 重入闸是必须的: 这个函数会写 `font_size`, 而 `font_size` 变 → `texture_size` 变,
        绑了 texture_size 的地方(弹窗里那些自动撑高的行)会再回调回来。没有闸就是死循环。
        ⚠️ 只改字号、**不改宽度** —— 宽度归 `_apply_sizes` / BoxLayout 管, 两边都改就会打架。
        """
        if getattr(w, "_fit_busy", False):
            return float(w.font_size)
        # 记一笔"真的叫了一次"(闸门之上早退的不算 —— 那不是活, 是防重入)。
        _FRAME_FIT[0] += 1
        # 给"冷字号"归因用(见 `_COLD_FS`): 谁在挑字号。
        _COLD_FS_TAG[0] = getattr(w, "_texupd_tag", None) or type(w).__name__
        # 记下这次用的基准(见 `_COLD_FS_BASE`): 冷字号的比值靠它才算得出来。
        try:
            _COLD_FS_BASE[0] = float(getattr(w, "_fit_base", 0.0) or 0.0)
        except Exception:
            _COLD_FS_BASE[0] = 0.0
        if base is not None:
            w._fit_base = float(base)
        # inset 记在控件上: 挂在 width 上的自动重挑(`_install_fit`)也要用同一个内缩量,
        # 否则"布局重排"和"文字变化"两条路会算出**两个**字号, 同一个按钮一会儿大一会儿小。
        if inset:
            w._fit_inset = float(inset)
        else:
            inset = float(getattr(w, "_fit_inset", 0.0))
        b = getattr(w, "_fit_base", None)
        if b is None:
            b = float(w.font_size)
            w._fit_base = b
        w._fit_busy = True
        try:
            pad = getattr(w, "padding", [0, 0, 0, 0])
            avail = max(1.0, w.width - pad[0] - pad[2] - inset)
            fs = fit_font_size(w.text or "", b, avail,
                               bool(getattr(w, "bold", False)))
            if abs(float(w.font_size) - fs) > 0.01:
                w.font_size = fs
        finally:
            w._fit_busy = False
        return float(w.font_size)

    def _fit_uniform(self, rows, base):
        """一组**同格式**的行共用一个字号: 按最宽那条算档, 全体照用。

        ⚠️ 逐行各自 `_fit1` 会得出**好几个**字号 —— 实测跑分历史 12 行里出现了
        34.0 / 31.96 / 30.0 三种(位数多的那几行缩了、位数少的没缩)。同一个列表里字号参差,
        玩家一眼就发现「同一个 log 为什么不一样」。同格式的列表**必须**统一。
        `base` = 这一档的基准字号; 宽度落定后(以及转屏时)自动重算。
        """
        rows = [w for w in rows if w is not None]
        if not rows:
            return rows

        def _go(*_a):
            avail = min(float(getattr(w, "width", 0.0) or 0.0) for w in rows)
            if avail <= 1.0:
                return
            # ⚠️ 这里原来手抄了一遍阶梯, 也就是老的"给个地板听天由命" —— 大字体下
            #    (实测 360dp + 1.5 倍)12 行历史列表全都 CLIP。改走 `fit_font_size`
            #    (它带二分, 保证挑到真的塞得下的那一档)。
            # ⚠️⚠️ **度量用的 bold 取"组里只要有一个粗体就按粗体量"**(2026-09-15 改)。
            #    原来用的是 `_longest` **自己**的 bold —— 而 `_longest` 是按"谁最宽"选出来的,
            #    同一组里换一段文字就可能换人 ⇒ 同一组会**今天用粗体量、明天用细体量**。
            #    后果不是画面, 是**预热烘不到**: 预热表按"每个标签自己的 bold"烘字号, 而这里
            #    用的是一个"临时决定"的 bold ⇒ 真机上每次撞上没烘过的那个组合, 就是一次
            #    **21.7 / 16.7 毫秒的冷字体表打开**(v0.7.30 真机: 帧34 与 帧724, 两帧都因此
            #    越过 11.11 毫秒那条线)。桌面探针实测: 运行期冷量到的字号里"不在预热表"的
            #    从 11 个降到 1 个之后, **剩的那一个就是这条路径造出来的**。
            #    改成"任意一个粗体就按粗体量"之后, 这一组只会用到 bold=True 那套字号 ——
            #    而它一定在预热表里(粗体标签自己烘过)。**零预热成本**。
            #    ⚠️ 顺带修掉一个既有的小隐患: 原来若最宽的那行是细体、组里另有粗体行,
            #       就会**按细体定字号** ⇒ 粗体那行可能溢出。粗体更宽, 按它量是保守且正确的。
            _bd = any(bool(getattr(w, "bold", False)) for w in rows)
            _COLD_FS_TAG[0] = "同类行 x%d" % len(rows)
            _longest = max(rows, key=lambda w: text_px(w.text or "", base, _bd))
            fs = fit_font_size(_longest.text or "", base, avail, _bd)
            for w in rows:
                if abs(float(w.font_size) - fs) > 0.01:
                    w.font_size = fs

        for w in rows:
            w._fit_base = float(base)
            w.bind(width=lambda *_a, _g=_go: _g())
        _go()
        return rows

    def _fit_buttons_uniform(self, btns, base, inset=0.0):
        """一排按钮**共用一个字号**: 按最宽那个标签定档, 全体照用。

        ⚠️ 逐个小 `_fit1` 会让长标签缩得更多 —— 实测 360dp 解锁隐藏档后, 返还率那一排
        出现 **24 / 19 / 16px 三种墨迹高**("80%"最大、"5000%"最小), 玩家一眼就看出来不齐。
        这与列表行是同一个道理(见 `_fit_uniform` 的说明), 只是按钮还要扣掉左右内缩 `inset`。
        """
        btns = [b for b in btns if b is not None]
        if not btns:
            return
        avail = min(max(1.0, float(b.width) - inset) for b in btns)
        # ⚠️ 度量 bold 与 `_fit_uniform` **同一条规矩**: 组里只要有一个粗体就按粗体量。
        #    理由见 `_fit_uniform` 里那段(按"最宽那个自己的 bold"量会让预热表烘不到,
        #    真机实测一次冷字号 21.7 毫秒)。
        _bd = any(bool(getattr(b, "bold", False)) for b in btns)
        _COLD_FS_TAG[0] = "同类钮 x%d" % len(btns)
        longest = max(btns, key=lambda b: text_px(b.text or "", base, _bd))
        fs = fit_font_size(longest.text or "", base, avail, _bd)
        for b in btns:
            b._fit_base = float(base)
            b._fit_inset = float(inset)
            if abs(float(b.font_size) - fs) > 0.01:
                b.font_size = fs

    def _install_fit(self, *ws):
        """给单行控件挂上"文字一变就重挑字号"的钩子(宽度归布局管, 也一起绑)。

        挂在 `text` 上而不是在 20 个赋值点各调一次 —— 那样迟早漏掉一个, 而漏掉的表现
        就是"某个状态又折行了", 只有截图才看得见。"""
        for w in ws:
            if w is None or getattr(w, "_fit_installed", False):
                continue
            w._fit_installed = True
            w._fit_base = float(w.font_size)

            def _go(_w, *_a):
                self._fit1(_w)
            w.bind(text=_go, width=_go)
        return ws

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

    def _fit_line(self, lb, base_sp=None):
        """弹窗/正文里的**单行**标签: 自动挑字号保证一行(装不下就缩, 绝不折行)。

        与 `_mk_label` 那条路分开写: 那条绑的是 `size→text_size`(两维都给), 这里必须只给
        宽度、高度留 None, 否则 Kivy 会把文字排版进一个**固定高度**里 —— 折行后的第二行
        就画不出来了(定高裁切, v0.6.12/v0.6.20 栽过两次)。"""
        if base_sp is not None:
            lb.font_size = sp(base_sp)
        lb._fit_base = float(lb.font_size)
        lb.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        self._install_fit(lb)
        return lb

    def _auto_h(self, lb, h0=0.0, extra=0.0):
        """多行正文标签: 高度跟着**真实排版**走 —— 折行只会让弹窗长高, 不会把字裁掉。

        `h0` 是单行时的最小高度。绑 `width→text_size`(不是 size: 见 BUILD_APK §3.19,
        绑 size 会死循环), 再把 `texture_size[1]`(排版后的真实高度)写回 height。"""
        lb.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))

        def _sync(*_a):
            # `_auto_cap` 由 `_popup_fit_content` 在"内容装不下"时写进来(见那里的说明):
            # 有它就把高度**压到它以下**(代价是少显示几行), 没有就按真实排版撑开。
            _cap = float(getattr(lb, "_auto_cap", 0.0) or 0.0)
            _h = max(h0, lb.texture_size[1] + extra)
            lb.height = min(_h, _cap) if _cap > 0 else _h
        lb._auto_sync = _sync
        lb._auto_min_h = h0
        lb._auto_base = float(lb.font_size)      # 装不下时按这个基准缩字号(见 _popup_fit_content)
        lb.bind(texture_size=_sync)
        _sync()
        return lb

    def _popup_fit_content(self, popup, content, tries=2):
        """开完之后把弹窗高度改成**内容真实需要的高度**(只长不缩到 92% 视口以内)。

        ⚠️ 不能靠写死的 `h_dp` 估: 同一个弹窗在 360dp 机器上、在系统字体 1.3 倍下, 排版
        高度差 40%~100%(实测跑分说明 170 → 420), 估小了内容就被顶出弹窗外面。
        ⚠️ 非内容区(标题栏 16~44 + 分隔条 4 + 外壳内边距 24)不写死常数, 而是**量**出来:
        `popup.height - content.height` 就是它, 与 Kivy 版本的 kv 结构无关。
        ⚠️ 必须等一帧: `content.minimum_height` 要等子控件宽度落定(自动撑高的那些标签
        是先知道宽度、再算出高度的)。"""
        def _refit(*_):
            try:
                _vw, _vh = self._veq()
                # ⚠️ 非内容区**绝不能**用 `popup.height - content.height` 反推。那个差值默认
                #    "content 的 height 已经跟上 popup 的新高度", 而在**同一个 tick 里**它还是
                #    上一轮布局的旧值 —— 实测: 第一次调用把 popup 从 280 改成 250 之后, 第二次
                #    调用读到 content.h 仍是 236(没变), 于是 chrome 被算成 14 而不是 44:
                #      · 彩蛋弹窗 -> 高度 220px(110dp), 内容要 206px, 分隔线**压在标题字上**;
                #      · 跑分菜单 -> 高度 518px(259dp), 内容只要 450px, **顶上留一大块空白**。
                #    改成**按结构直接算**: GridLayout 的内边距 + 除 container 之外的孩子高度
                #    (空标题行 16 + 分隔条 4)。它只取决于 kv 结构, 与"布局跑到第几帧"无关,
                #    所以调一次和调十次结果相同(幂等), 也就不会把高度越算越偏。
                chrome = getattr(popup, "_fit_chrome", None)
                if chrome is None:
                    _cont = content.parent
                    _grid = _cont.parent if _cont is not None else None
                    if _grid is not None and hasattr(_grid, "padding"):
                        _pad = _grid.padding
                        chrome = (_pad[1] + _pad[3]
                                  + sum(c.height for c in _grid.children if c is not _cont))
                    else:
                        chrome = max(0.0, popup.height - content.height)   # 兜底
                    popup._fit_chrome = chrome
                # ⚠️ 上限从 0.92 提到 0.96: 0.92 是**我拍的余量**, 不是硬约束 —— 真正的要求
                #    只是"弹窗要放得上屏幕"。定 0.92 的代价实测: 360dp + 1.3 倍系统字体下
                #    跑分菜单的内容高出 11px(半个行高), 于是被压掉一行 —— 而它明明放得下。
                #    缩字号也救不了: 说明是 8 行, 缩 2.6% 还是 8 行, 高度**一像素都不变**
                #    (行高按行算, 不按字号连续变)。所以正确做法是别把它压到屏幕装得下的
                #    范围之内去 —— 剩下那 4% 的留白不值得用一行字去换。
                _cap = _vh * 0.96
                if content.minimum_height + chrome > _cap:
                    # ⚠️ 装不下的时候**不能就这么封顶**: Kivy 不裁剪控件, 多出来的那截会被
                    #    竖排 BoxLayout 摆到**弹窗外面**(实测 320x640 + 大字体时弹窗标题整行
                    #    飘到屏幕外, 「启动信息」的版本行高出弹窗顶 218px) —— 玩家看到的是
                    #    "字飘在游戏画面上"。旧版这里是定高裁掉尾巴(至少还在面板里)。
                    #    做法: 把**"自动撑高"的那几个标签按比例压低**, 让总高正好塞进上限。
                    #    宁可少显示两行(裁在面板里), 也不飘出去。
                    _autos = [c for c in content.children if hasattr(c, "_auto_sync")]
                    _tot = sum(float(c.height) for c in _autos)
                    _over = (content.minimum_height + chrome) - _cap
                    if _autos and _tot > 0:
                        # 先**缩字号**(字全都在, 只是小一点) —— 这比"裁掉几行"对玩家友好。
                        # 字号变了纹理要下一帧才重排, 所以这一帧先按比例把高度压住当保险;
                        # 下一帧 `_refit` 再跑时 `minimum_height` 已经变小, 若够用就不再压。
                        _k = max(0.55, min(1.0, (_tot - _over) / _tot))
                        for _a in _autos:
                            _b = float(getattr(_a, "_auto_base", 0.0) or 0.0)
                            if _b > 0 and _k < 0.999:
                                _a.font_size = max(6.0, _b * _k)
                        _room = max(0.0, _tot * _k)
                        for _a in _autos:
                            _a._auto_cap = max(
                                float(getattr(_a, "_auto_min_h", 0.0)),
                                _room * (float(_a.height) / _tot))
                            _a._auto_sync()
                else:
                    # ⚠️ 钳位**只设不清**是个陷阱: 第一帧压过之后, 即使下一帧字号已经缩小、
                    #    内容真的够了, 旧钳位还留着继续裁(实测 1.3 倍字体下跑分菜单的说明
                    #    被裁掉 11px 一直不恢复)。够用就释放。
                    for _c in content.children:
                        if getattr(_c, "_auto_cap", 0.0):
                            _c._auto_cap = 0.0
                            _sync = getattr(_c, "_auto_sync", None)
                            if _sync is not None:
                                _sync()
                popup.height = min(content.minimum_height + chrome, _cap)
            except Exception:
                pass
        for _i in range(max(1, tries)):
            Clock.schedule_once(_refit, 0.0 if _i == 0 else 0.06)

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
        self._hud_dim_geo = ((x0, ga_hi, w, max(0.0, y0 + h - ga_hi)),
                             (x0, y0, w, max(0.0, ga_lo - y0)))
        # ⚠️ **构造期必须无条件写完整几何**: `fx_probe [11]` 就是在"刚建完、还没跑过一帧"
        #    这个状态下读这四块矩形的 pos/size 来断言"恰好盖满整屏减游戏区"的。
        #    跑起来之后才交给 `_paint_hud_dim` 按当前 alpha 决定"铺满"还是"收到 0"。
        self._paint_hud_dim(max(0.0, self._hud_dim_last),
                            force_geo=(self._hud_dim_last < 0))

    def _paint_hud_dim(self, a, force_geo=False):
        """按当前压暗强度摆这两块矩形。

        ⚠️ **a <= 0 时把矩形收到 0 尺寸, 而不是只把 alpha 写成 0**(2026-09-13 中风险优化)。
        alpha=0 的矩形**仍然要走一遍混合填充** —— 它盖的是"整块屏幕减去游戏区",
        竖屏下约 35% 的屏面积。真机 2560x1600 = 400 万像素, 每帧白烧约 140 万像素的混合;
        按 80fps 算是 ~115 Mpix/s 的无用填充(Adreno 730 的混合填充约 1~2 Gpix/s,
        即 6~11% 的填充预算)。**它是这两块矩形唯一的存在意义就是"演出期压暗",
        演出之外一个像素都不该画。**
        桌面测不出收益(填充率太快), 所以这条是"去掉确凿的白工", 不是"实测更快的优化"。
        """
        for col, geo, rect in ((self._hud_dim_cols[0], self._hud_dim_geo[0],
                                self._hud_dim_top),
                               (self._hud_dim_cols[1], self._hud_dim_geo[1],
                                self._hud_dim_bot)):
            col.rgba = (DIM_RGB[0], DIM_RGB[1], DIM_RGB[2], a)
            rect.pos = (geo[0], geo[1])
            rect.size = (geo[2], geo[3]) if (force_geo or a > 0.0) else (0.0, 0.0)

    def _sync_hud_dim(self):
        """每帧把压暗块对齐到装杯演出的生灭曲线。

        真源只有一个: `WinPileFX.dim_alpha()`(它读的是板面本帧真正画上去的那个数)。
        这里**不重算任何曲线** —— 两边各写一份必然脱钩(这个仓库已经踩过一次:
        `_reveal_deadline` 硬编码 0.45, 尾巴从 0.45 改到 0.60 时静默脱钩, 兜底提前
        触发把数字剧透了)。
        `_hud_dim_last` 短路是性能考虑: 演出之外这一层恒为 0, 不必每帧去动那两个 Color。
        """
        # 跑分时信息栏承担进度提示，不能再被中奖落珠的 HUD 压暗层盖灰。
        # 其它控件本身仍由 disabled 样式表明不可操作，不需要整块额外蒙黑。
        if getattr(self, "_bench_running", False):
            if self._hud_dim_last != 0.0:
                self._hud_dim_last = 0.0
                self._paint_hud_dim(0.0)
            return
        a = self.game_area.win_fx.dim_alpha(HUD_ALPHA)
        if a == self._hud_dim_last:
            return
        self._hud_dim_last = a
        self._paint_hud_dim(a)

    def _build_ui(self):
        # 顶栏: [左边两个按钮(定宽)] [标题(弹性·居中)] [状态(弹性·右对齐)]
        # ⚠️ 原来是 [left_box 弹性][36dp spacer][标题 定宽112][right_box 弹性] ——
        #    两个弹性块按 Kivy 的规矩**平分**剩余宽, 而 left_box 的内容(静音 64 + 每轮 72
        #    + 间距)要 142dp: 实测 400dp 机器只分到 107(内容右溢 35dp)、360dp 只分到 87
        #    (右溢 55dp), 溢出的按钮去盖标题 —— BUILD_APK §3.5 记过同一个坑。
        #    现在左边**显式定宽**(宽度在 `_apply_sizes` 里按按钮实际宽度算), 标题与状态
        #    平分剩下的, 两者都挂单行自适应字号 ⇒ 谁也不折行、谁也不盖谁。
        #    顺带: 标题本来也不居中(那个 36dp spacer 只能把它往右推, 实测偏右 20dp)。
        top = BoxLayout(size_hint_y=None, height=dp(H_TOP),
                        padding=[dp(10), dp(4), dp(10), dp(4)], spacing=dp(6))
        self._row_top = top
        self._row_bg(top, COL_PANEL)
        # ⚠️ 顶栏必须是 [左 flex(1)] [标题 定宽] [右 flex(1)] —— **左右等宽**, 标题才落在
        #    屏幕正中(玩家: 「跳跳的弹珠机应该居中」)。原来左边定宽 126、右边 flex, 两边不等,
        #    于是窗口越宽标题越偏左: 实测 400dp 偏 2.5px(看不出来), 540dp 的桌面窗**偏 38.5px**。
        #    代价: 左边两个按钮的宽得由这份份额反算(见 `_apply_row_budget`), 上限仍是设计值。
        left_box = BoxLayout(spacing=dp(6))
        self._top_left = left_box
        self.mute_btn = self._mk_button("", lambda _b: self.toggle_mute())
        self.mute_btn.size_hint_x = None
        self.mute_btn.width = dp(58)
        self.mute_btn.font_size = "13sp"
        # ⚠️ 打标才进文字纹理缓存(2026-09-14)。这个按钮的文字只有两句、颜色只有两种,
        #    预热之后每次开关音效都是**命中**, 一次纹理都不用重建。
        _tag_texupd(self.mute_btn, "音效按钮")
        self._refresh_mute_btn()
        left_box.add_widget(self.mute_btn)
        self.round_btn = self._mk_button("每轮%d次" % self.max_plays,
            lambda _b: self._show_round_settings(), bg=COL_GREEN)
        self.round_btn.size_hint_x = None
        self.round_btn.width = dp(62)
        self.round_btn.font_size = "13sp"
        _tag_texupd(self.round_btn, "轮次按钮")
        self.round_btn.color = (0, 0, 0, 1)          # 黑字配绿底
        left_box.add_widget(self.round_btn)
        top.add_widget(left_box)
        self.title_lbl = self._mk_label("跳跳的弹珠机", "18sp", COL_TEXT, "center", True,
                                        size_hint_x=None, width=dp(120))
        top.add_widget(self.title_lbl)
        # 状态要装在一个**弹性容器**里(与左边等宽), 这样"右对齐"是相对那一半而言,
        # 而标题正好落在两半中间。
        right_box = BoxLayout()
        self._top_right = right_box
        self.status_lbl = self._mk_label("按住蓄力发射", "13sp", COL_SUB, "right", False)
        _tag_texupd(self.status_lbl, "状态栏")
        right_box.add_widget(self.status_lbl)
        top.add_widget(right_box)
        # 文字一变就重挑字号(状态栏在整局里会换成十来种文案, 逐个赋值点去调必漏)
        self._install_fit(self.title_lbl, self.status_lbl, self.mute_btn, self.round_btn)
        self.add_widget(top)
        # ---- 设定区(左对齐, 不撑满) ----
        # 返还率行: 返还率 + 三档(固定宽)
        # ⚠️ 左右空白都是**宽度预算的一部分**: 原来 padding=[24,4] 是"左右各 24", 右边那
        #    24dp 纯粹白扔(内容左对齐, 右边本来就是空的)。收到 14/10 之后, 360dp 机器上
        #    每个档位按钮从 45.3dp 变成 52.3dp —— 这才是玩家看得见的差别。
        rtp = BoxLayout(size_hint_y=None, height=dp(H_RTP),
                        padding=[dp(14), dp(4), dp(10), dp(4)], spacing=dp(5))
        self._row_rtp = rtp
        self._rtp_title_lbl = self._mk_label("期望返还比例：", "14sp", COL_TEXT, "left", False,
                                      size_hint_x=None, width=dp(115))
        # ⚠️ 打标才进缓存。这两个标签**文字永不变**, 变的是 `.color` ——
        #    而 Kivy 把颜色烘进纹理, 于是每发球禁用/启用各改一次 = 每发球 6 次重建。
        #    打标 + 预热两种颜色之后, 那一块**一次都不用重建**。
        _tag_texupd(self._rtp_title_lbl, "返还率标题")
        # 染色基色: 纹理烘成 COL_TEXT(最亮), 亮/暗两态靠画布乘系数 —— 见 `_tint_from`。
        self._rtp_title_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self._rtp_title_lbl._tint_key = "sub"
        rtp.add_widget(self._rtp_title_lbl)
        # ⚠️ **这个"右侧留空"的 Widget 必须先加**(在按钮之前加进 `rtp`) —— `_add_rtp_button`
        #    靠它定位: 把新按钮插到它**左边**(Kivy 的 `children[0]` 在最右, 所以"更大 index = 更左")。
        #    先建按钮的话它找不到 spacer, 会退到 idx=1, 于是按钮全跑到标题**左边** ——
        #    实测踩过: 界面变成「80% 120% 200% 360%   期望返还比例：」(截图发现, 单测测不出)。
        self._rtp_spacer = Widget()
        rtp.add_widget(self._rtp_spacer)
        self.rtp_btns = {}
        self._rtp_row = rtp
        # ⚠️ 这里原来是 `self._rtp_unlocked = False`(隐藏档解锁状态) —— **已整个删除**。
        #    它只置真、从不置假 ⇒ "长按成功解锁过一次"之后入口就永久失效; 玩家 2026-09-12
        #    定稿改成"每次长按都弹"。删掉之后,**"有没有隐藏档"的唯一真源是 `rtp_btns` 的键**
        #    —— 冷启动重建界面时它是空的, 天然表达"未解锁", 与"隐藏档不持久化"自洽。
        #    留一个只写不读的标志 = 第二个状态源, 迟早和那排按钮漂移(见 _close_rtp_hidden)。
        self._rtp_popup = None         # 隐藏档弹窗的引用: 防重入闸门 + 探针查"还开着吗"
        self._rtp_hold_start = 0.0
        self._rtp_hold_fired = False
        for label, val in self.RTP_TIERS:   # 常驻档; 隐藏档靠长按解锁, 见 _unlock_rtp
            self._add_rtp_button(label, val)
        self.add_widget(rtp)
        # 投入行: 投入弹珠单位 + 1/10/50/100(固定宽)
        bets = BoxLayout(size_hint_y=None, height=dp(H_BETS),
                         padding=[dp(14), dp(4), dp(10), dp(4)], spacing=dp(5))
        self._row_bets = bets
        self._bet_title_lbl = self._mk_label("每次投入弹珠：", "14sp", COL_TEXT, "left", False,
                                       size_hint_x=None, width=dp(115))
        # ⚠️ 打标才进缓存。这两个标签**文字永不变**, 变的是 `.color` ——
        #    而 Kivy 把颜色烘进纹理, 于是每发球禁用/启用各改一次 = 每发球 6 次重建。
        #    打标 + 预热两种颜色之后, 那一块**一次都不用重建**。
        _tag_texupd(self._bet_title_lbl, "投注标题")
        # 染色基色: 纹理烘成 COL_TEXT(最亮), 亮/暗两态靠画布乘系数 —— 见 `_tint_from`。
        self._bet_title_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self._bet_title_lbl._tint_key = "sub"
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
        # 与上面两行同一条左竖线(14dp)。原来这里写 dp(24) 并注"对齐重置按钮(6+12+6=24)",
        # 但底行的重置按钮其实起于 6+12=18 —— 那个 24 从来没对齐过任何东西。
        info.add_widget(Widget(size_hint_x=None, width=dp(14)))
        self._bead_lbl = self._mk_label("弹珠：", "15sp", COL_TEXT, "left", True,
                                       size_hint_x=None, width=dp(48))
        info.add_widget(self._bead_lbl)
        self.balance_lbl = self._mk_label(str(self.balance), "19sp", COL_BALL,
                                          "left", True, size_hint_x=None, width=dp(80))
        _tag_texupd(self.balance_lbl, "余额")
        info.add_widget(self.balance_lbl)
        self.stats_lbl = self._mk_label("", "15sp", COL_TEXT, "center", True,
                                        size_hint_x=0.70)
        _tag_texupd(self.stats_lbl, "统计")
        # 染色基色: 纹理烘成 COL_TEXT(最亮), 亮/暗两态靠画布乘系数 —— 见 `_tint_from`。
        self.stats_lbl._tint_base_rgba = hex_rgb(COL_TEXT) + (1,)
        self.stats_lbl._tint_key = "text"
        info.add_widget(self.stats_lbl)
        # 余额/统计/“弹珠：”都是单行; 余额涨到 8 位数以上时**缩字号**而不是折行
        self._install_fit(self._bead_lbl, self.balance_lbl, self.stats_lbl)
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
        self._install_fit(self.power_lbl)
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
        """锁/解锁 HUD 输入。

        ⚠️ **绝不再逐个按钮改 `btn.disabled`**(2026-09-15 修, 这是 1% Low 的头号来源)。
        病根在 Kivy `uix/label.py` 的 `_trigger_texture_update`:
        ```
            elif name == 'disabled':
                self._label.options['color'] = self.disabled_color if value else self.color
        ```
        **Kivy 把文字颜色烘进纹理** ⇒ 改一次 `disabled` 就要重排一次文字纹理,
        而 `Button` 本身就是 `Label` ⇒ **12 个按钮 = 12 次重排**。更致命的是 Kivy 用
        Clock(`create_trigger(..., -1)`)把这些重排**攒到同一帧**一起做 —— 于是那一帧
        12~21 毫秒(占该帧 95%+), 正是 1% Low 的主要来源。

        桌面二分实测(12 个按钮):
            `disabled=True`            → **12 次重建**
            `disabled_color` 对齐成 `color` 后再 `disabled=True` → **12 次**(Kivy 无条件重排, 没用)
            `background_color`         → **0 次**(变灰走它本来就免费)
        真机对应: 每一发两次爆发 —— **发射时禁用 16 次 / 回 ready 时启用 15 次**,
        间隔正好一个蓄力时长(0.1s), 每次卡 12~21 毫秒。

        现在的做法: 变灰照旧走 `background_color`(免费), 输入锁改由
        `RootWidget.on_touch_down` 在**触摸层**统一吞掉(见那里的注释)。
        ⚠️ 变灰必须保留 —— 万一状态卡住, 玩家**看得见**按钮是暗的, 比"看起来正常却点不动"强得多。
        """
        self._controls_enabled = bool(enabled)
        if enabled:
            self.fire_btn.background_color = hex_rgb(COL_FIRE) + (1,)
            self.reset_btn.background_color = hex_rgb("#2a2a35") + (1,)
            self._restyle_selects()
            self._refresh_mute_btn()
            self.round_btn.background_color = hex_rgb(COL_GREEN) + (1,)
            # ⚠️ **不写 `.color`** —— 写它就是一次字形纹理重排(4~7 毫秒), 而这一拍正是"回 ready"。
            #    染色走画布那条 Color; 拿不到才退回写 `.color`(绝不静默不染色)。
            for _lbl in (self._rtp_title_lbl, self._bet_title_lbl, self.stats_lbl):
                if not _set_lbl_tint(_lbl, _TINT_BRIGHT):
                    _lbl.color = _lbl._tint_base_rgba
        else:
            off = hex_rgb(COL_BTN_OFF) + (1,)
            self.fire_btn.background_color = off
            self.reset_btn.background_color = hex_rgb("#1a1a22") + (1,)
            for btn in list(self.bet_btns.values()) + list(self.rtp_btns.values()):
                btn.background_color = off
            self.round_btn.background_color = off
            self.mute_btn.background_color = off
            # 同上一支: 变暗走染色, 不重建纹理。系数 = 目标色 / 烘的那个色(逐通道)。
            for _lbl in (self._rtp_title_lbl, self._bet_title_lbl, self.stats_lbl):
                if not _set_lbl_tint(_lbl, _TINT_DIM[_lbl._tint_key]):
                    _lbl.color = hex_rgb(COL_GRAY) + (0.6,)

    def on_touch_down(self, touch):
        """锁输入期间把 HUD 的触摸统一吞掉(见 `_set_controls_enabled` 的说明)。

        ⚠️ **只挡 `on_touch_down`, 不挡 `on_touch_up`** —— 松手那条路走 Kivy 的 touch grab
        (发射键在**按下**时就 grab 了), `fire_btn.on_release -> launch()` 必须照常送达,
        否则球永远发不出去。而锁只发生在 `launch()` **之后**(`start_charge` 不加锁),
        那时手早就松开了, 所以两者不会打架。
        ⚠️ 弹窗(Popup)是 Window 的直接子控件、**不挂在 RootWidget 下**, 所以锁输入期间
        弹窗上的按钮照常可点(彩蛋弹窗、轮次结束、设置弹窗都靠这条)。
        """
        if not getattr(self, "_controls_enabled", True):
            return True
        return super().on_touch_down(touch)

    def _show_bench_status(self, text):
        """把跑分状态放在底部操作区正上方，复用统计栏而不遮住盘面。"""
        self._bench_status_active = True
        _set_label_text(self.stats_lbl, text.replace("\n", "　·　"))
        self.stats_lbl.color = hex_rgb(COL_FIRE) + (1,)
        fs = self._font_scale * self._ui_scale
        self.stats_lbl.font_size = sp(16) * fs
        self.stats_lbl._fit_base = self.stats_lbl.font_size
        self._fit1(self.stats_lbl)

    def _set_game_status(self, text):
        """统一更新游戏状态文案，跑分也保持与正常游玩相同的 UI 负载。"""
        return _set_label_text(self.status_lbl, text)

    def _hide_bench_status(self):
        self._bench_status_active = False
        fs = self._font_scale * self._ui_scale
        self.stats_lbl.font_size = sp(15) * fs
        self.stats_lbl._fit_base = self.stats_lbl.font_size
        self._refresh_stats()

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
        # 装杯装满后等玩家点击才退场(玩家 2026-09-12 定稿)。这一下**吞掉** —— 早退, 不触发
        # 下面"标题 / 期望返还比例"的长按计时: 玩家点的是"我看够了", 不是 HUD 上的长按热区。
        # 受理条件(装满静止 + 过了最短停留)全在 request_close 里, 这里只管"点到了就试一下"。
        # ⚠️ try/except 是必须的: Window 的事件派发**不捕获异常**, 这里漏出去会冒到事件循环。
        # ⚠️ 用裸 `return`(不是 `return True`): 实测观察者返回值只是 OR 聚合、**不短路**,
        #    没有任何证据 `return True` 能吞掉后面的 widget 树 —— 没必要冒那个险。
        try:
            if self.game_area.win_fx.request_close():
                return
        except Exception:
            pass
        if (self.title_lbl.collide_point(*pos)
                and not getattr(self, "_bench_running", False)):
            self._bench_start = time.time()
            self._bench_triggered = False
        # 长按"期望返还比例"3 秒 -> 四选一档位弹窗(判据与上面那套同构: 按下记时刻、
        # 每帧查时长、抬手清零; 不在这儿做任何计时器, 免得和跑分那套抢状态)。
        # ⚠️ 这里原来还有 `and not self._rtp_unlocked` —— 它让一局只能解锁一次。玩家
        #    2026-09-12 定稿改成"每次长按都弹", 那个标志已整个删除。去掉守卫后"一次按压
        #    只弹一个、松手再按再弹"由 `_rtp_hold_fired`(每次按下清零)自然保证。
        if self._rtp_title_lbl.collide_point(*pos):
            self._rtp_hold_start = time.time()
            self._rtp_hold_fired = False

    def _on_title_touch_up(self, win, touch):
        self._bench_start = 0.0
        self._rtp_hold_start = 0.0        # 抬手即取消长按(还没到 RTP_UNLOCK_HOLD 就不算)
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

    def _check_title_hold(self):
        t = getattr(self, "_bench_start", 0)
        if t > 0 and not self._bench_triggered and time.time() - t >= 3.0:
            self._bench_triggered = True
            self._show_bench_menu()   # 长按3秒: 弹性能测试菜单(开始测试/查看历史)
        t2 = self._rtp_hold_start
        if t2 > 0 and not self._rtp_hold_fired and time.time() - t2 >= self.RTP_UNLOCK_HOLD:
            self._rtp_hold_fired = True
            self._ask_unlock_rtp()    # 长按 RTP_UNLOCK_HOLD 秒: 四选一档位弹窗

    # ---------------------------------------------------------------- 进度显示
    # ⚠️⚠️ **渲染窗口那 25 秒一个字都不显示**(玩家 2026-09-15 定案): 那段正在测 1%Low,
    #   在里面写标签就是往被测帧上加活儿。**只有后面全力跑 SOC 时才显示**:
    #     · 物理段    -> `物理跑分 d/3`
    #     · SOC 高压段 -> `SOC高压测试 d/300秒`
    #   那两个阶段 `_finish_render_sample` 已经跑过(屏幕采样停了) ⇒ 写标签不进成绩。
    def _prog_text(self):
        """当前该显示的进度文案; 没有测试在跑就返回 None。"""
        if getattr(self, "_hp_running", False):
            # ⚠️ **2026-09-15 改**: 进度现在是**真秒表**, 不再是工作线程报的 CPU 秒数。
            #    旧版(直到 v0.7.52)报的是 CPU 秒, 而活儿也按 CPU 秒跑 ⇒ 面板顶到 300/300 时
            #    活儿还剩两成没干完 —— 玩家报的"跑完了却很久不弹窗"就是这么来的。
            #    ⚠️ **别把这两条注释看反了**: 旧版那条"不能用墙钟当分子"的结论, 前提是
            #       **循环条件用 CPU 秒** —— 那时面板是墙钟、循环是 CPU 秒, 两根尺子必然对不齐。
            #       现在**循环条件本身就是墙钟**(`benchmark_sustained` 的 `total_wall_sec`),
            #       分子分母同源 ⇒ 墙钟才是**唯一正确**的取值。
            #       关键不在"用不用墙钟", 在"**和循环条件是不是同一根尺子**"。
            _w0 = getattr(self, "_hp_wall0", 0.0)
            _el = (time.time() - _w0) if _w0 else 0.0
            _cap = int(SOC_SUSTAIN_WALL_SEC)
            if _el >= _cap:
                # 过点(玩家 2026-09-15:「如果时间超过 6 分钟, 如果还没有弹出窗口, text 上写
                #   『成绩核算中：x秒』, 这个 x 代表进度」)。
                # ⚠️ 这是**保险丝**, 不是常态: 桌面实测"核算 + 建弹窗"只要 **51 毫秒**
                #    (写历史 JSON 1ms + 拼摘要 2ms + 建弹窗 50ms, 见 `temp/hp_tail_probe.py`),
                #    而本轮询每 0.25 秒才刷一次 ⇒ **多数情况下这段文案一帧都不会被画出来**。
                #    真会看到它的场合是"核算真慢了"或"主线程被卡住" —— 那时玩家看到的是一个
                #    **在跳动的计数**, 而不是不动的 360/360(后者读起来就是死机)。
                #    判据只认墙钟一条, **不需要额外的标志位**, 也就没有跨线程状态要同步。
                return "成绩核算中：%d秒" % max(1, int(_el - _cap) + 1)
            return "SOC高压测试 %d/%d秒" % (int(_el), _cap)
        if getattr(self, "_bench_running", False):
            # ⚠️⚠️ **渲染窗口那 25 秒一个字都不显示**(玩家 2026-09-15:「那 25 秒测试帧率的不
            #    显示任何进度消息, 后面全力测试 SOC 的时候才显示」)。
            #    不写标签 ⇒ 不会在测 1%Low 的窗口里造出一次文字重建,
            #    也就**不需要为它预烘**(原设计预烘了 `性能测试 d/5` 那 6 条, 已撤)。
            if not getattr(self, "_phys_started", False):
                return None
            return "物理跑分 %d/%d" % (int(getattr(self, "_phys_done", 0) or 0),
                                      SOC_SAMPLE_RUNS)
        return None

    def _prog_tick(self, dt=0):
        """主线程轮询进度。**只在文案真变了才写标签** —— 每写一次都是一次文字重排。"""
        try:
            _t = self._prog_text()
        except Exception:
            _t = None
        if not _t or _t == getattr(self, "_prog_last", None):
            return
        self._prog_last = _t
        try:
            _set_label_text(self.status_lbl, _t)
        except Exception:
            pass

    def _prog_start(self):
        self._prog_last = None
        try:
            self._prog_ev = Clock.schedule_interval(self._prog_tick, 0.25)
        except Exception:
            self._prog_ev = None

    def _prog_stop(self):
        try:
            if getattr(self, "_prog_ev", None) is not None:
                self._prog_ev.cancel()
        except Exception:
            pass
        self._prog_ev = None

    # ---------------------------------------------------------------- 高压测试
    # ⚠️ **与"性能测试"分开的独立入口**(玩家 2026-09-15:「新增高压测试按钮, 原有测试是原有时间」)。
    #   两者问的问题不同, 时长也差一个量级:
    #     · 性能测试 = 短、**带间隔**、测**峰值**(可比);  约 34 秒
    #     · 高压测试 = 长、**一秒不停**、测**衰减**;      300 秒(5 分钟)
    #   ⚠️ 两者互斥: 一个在跑的时候另一个不许进。
    def _start_hp_test(self):
        if getattr(self, "_hp_running", False) or getattr(self, "_bench_running", False):
            return
        self._hp_running = True
        # ⚠️ 状态栏要先存一份再改, 跑完还回去 —— 与 `_bench_saved_status` 同一个写法。
        self._hp_saved_status = self.status_lbl.text
        # ⚠️ **墙钟起点先归零, 由工作线程真正开跑那一刻再盖章**(见 `_run_hp_test`)。
        #    为什么不在这儿直接 `time.time()`: 中间还隔着 `_wait_idle_then_hp` 等球落地那一段,
        #    盖在这儿会把等待也算成测试时间。归零的语义是"还没开始", `_prog_text` 据此显示 0。
        #    (旧版这里是个 `_hp_t0` 字段 —— 自 v0.7.52 起**没有任何读取方**, 本轮已删。)
        self._hp_wall0 = 0.0
        self._prog_start()
        _set_label_text(self.status_lbl, "SOC高压测试 0/%d秒" % int(SOC_SUSTAIN_WALL_SEC))
        self._set_controls_enabled(False)
        self._wait_idle_then_hp()

    def _wait_idle_then_hp(self, dt=0):
        """等球落地(主线程空闲)再起 —— 与 `_wait_idle_then_bench` 同一个理由: 别抢 CPU。"""
        if self.state == "ready":
            threading.Thread(target=self._run_hp_test, daemon=True).start()
        else:
            Clock.schedule_once(self._wait_idle_then_hp, 0.5)

    def _run_hp_test(self):
        # ⚠️ 工作线程: 只写属性, **不碰界面**(界面只能在 Clock 回调里动)。
        _frq, _stop = _freq_sampler_start()
        # ⚠️ **测试的墙钟起点在这儿盖**, 不在 `_start_hp_test`: 中间隔着"等球落地"那一段,
        #    盖在前面会把等待也算成测试时间。紧挨着 `benchmark_sustained` 之前盖,
        #    量的就正好是测试本身。
        self._hp_wall0 = time.time()
        _fl, _fr, _fps, _cpu = benchmark_sustained()
        _stop[0] = True
        _frq.sort()
        self._hp_fps = list(_fps or [])
        self._hp_cpu = list(_cpu or [])
        self._hp_freq = {"p50": _frq[len(_frq) // 2] if _frq else 0,
                         "min": _frq[0] if _frq else 0,
                         "max": _frq[-1] if _frq else 0, "n": len(_frq),
                         # 平均频率 —— 历史里那一列要的就是它(玩家:「SOC 平均频率」)。
                         "mean": int(sum(_frq) / len(_frq)) if _frq else 0}
        Clock.schedule_once(lambda dt: self._hp_done(), 0)

    def _hp_stats(self):
        """-> (首, 末, 最低, 中位, 降幅%); 没数据返回 None。"""
        v = [x for x in (getattr(self, "_hp_fps", None) or []) if x > 0]
        if not v:
            return None
        sv = sorted(v)
        _d = 100.0 * (v[0] - v[-1]) / v[0] if v[0] > 0 else 0.0
        return v[0], v[-1], sv[0], sv[len(sv) // 2], _d

    def _hp_freq_line(self):
        """结果弹窗里那行 CPU 频率。

        ⚠️ **口径取「平均」, 不取「中位」**(玩家 2026-09-15:「压力测试的 cpu 频率 … 这个地方
           应该取平均值吧」)。理由: 频率采样是**双峰**的 —— 应用大部分时间在等 vsync, 调频器
           把核压在最低档(真机 1133MHz), 满载窗口才冲到睿频(4608)。**中位数必然落在其中一个
           峰上**, 报出来要么像"全程低频"要么像"全程满血", 两个都不代表这段测试; 而**平均值**
           对应的是平均功耗/发热 —— 正是这个高压测试想回答的问题。
        ⚠️ **改之前这里印中位、而历史详情印平均 —— 同一个测试两处口径不一样**, 玩家就是这么
           发现的(他是在结果弹窗上看到的)。现在两处都是平均; 中位照旧印在**详情**的
           「频率分布」那一行, 也照旧存在记录里(`freq_p50`), 没有删。
        ⚠️ 文案长度刻意与本函数改前**一样**(「中位」→「平均」, 都是两字), 不动弹窗排版。
        """
        _f = getattr(self, "_hp_freq", None) or {}
        if _f.get("mean"):
            return ("平均 %dMHz（最低 %d / 最高 %d，%d 个采样）"
                    % (_f["mean"], _f["min"], _f["max"], _f["n"]))
        return "没采到（非安卓 / 读不到 sysfs）"

    def _hp_summary_text(self):
        """弹窗里那几行(短)。没数据返回空串 —— **不印假数**。"""
        st = self._hp_stats()
        if st is None:
            return ""
        _f, _l, _lo, _mid, _d = st
        _dev = self._device_info()
        try:
            _ver = str(_app_version() or "")
        except Exception:
            _ver = ""
        _dv = (_dev + " / " + _ver) if _ver and _ver not in _dev else _dev
        v = [x for x in self._hp_fps if x > 0]
        _step = max(1, len(v) // 12)
        _curve = " / ".join("%d" % v[_i] for _i in range(0, len(v), _step))
        return (_dv + chr(10)
                + "高压 %.0f 秒（背靠背不停）" % SOC_SUSTAIN_WALL_SEC + chr(10)
                + "首 %d → 末 %d 步/秒（降 %.0f%%）" % (_f, _l, _d) + chr(10)
                + "最低 %d · 中位 %d 步/秒" % (_lo, _mid) + chr(10) + chr(10)
                + "每段采样：" + _curve + chr(10)
                + "CPU 频率：" + self._hp_freq_line())

    def _hp_done(self):
        """高压测试结束: 弹结果弹窗。"""
        self._prog_stop()
        self._set_controls_enabled(True)
        _set_label_text(self.status_lbl,
                        getattr(self, "_hp_saved_status", "按住蓄力发射"))
        self._hp_running = False
        # ⚠️ **落一条历史**(玩家 2026-09-15: 「高压测试也专门搞个 log 记录」)。
        #    存的东西要够"详细成绩 + SOC 平均频率"看 —— 逐窗曲线也存下
        #    (详情弹窗要画它)。存不成也不能影响结果弹窗, 所以整段 try 包着。
        try:
            _st2 = self._hp_stats()
            if _st2 is not None:
                _f2, _l2, _lo2, _mid2, _d2 = _st2
                _fr2 = getattr(self, "_hp_freq", None) or {}
                _w2 = sorted(x for x in (getattr(self, "_hp_fps", None) or []) if x > 0)
                self.hp_history.append({
                    "time": time.strftime("%Y-%m-%d %H:%M"),
                    "median": int(_mid2),
                    "spread": (round(100.0 * (_w2[-1] - _w2[0]) / _mid2, 1)
                               if (_w2 and _mid2 > 0) else None),
                    "first": int(_f2), "last": int(_l2), "min": int(_lo2),
                    "decay": round(_d2, 1),
                    "freq_mean": int(_fr2.get("mean", 0) or 0),
                    "freq_p50": int(_fr2.get("p50", 0) or 0),
                    "freq_min": int(_fr2.get("min", 0) or 0),
                    "freq_max": int(_fr2.get("max", 0) or 0),
                    "freq_n": int(_fr2.get("n", 0) or 0),
                    "sec": int(SOC_SUSTAIN_WALL_SEC),
                    "version": _app_version(),
                    "device": self._device_info(),
                    "windows": [int(x) for x in self._hp_fps],
                })
                if len(self.hp_history) > 100:
                    self.hp_history.pop(0)
                self._save_hp_history()
        except Exception:
            pass
        try:
            _txt = self._hp_summary_text()
        except Exception:
            _txt = ""
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        title_lbl = self._fit_line(Label(text="SOC高压测试", bold=True, halign="center",
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        body = Label(text=_txt or "没有采到数据", font_size="15sp", halign="left",
                     valign="top", color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None)
        self._auto_h(body, dp(160), dp(6))
        content.add_widget(body)
        # ⚠️ **没有「保存日志」按钮**(玩家 2026-09-15:「删掉高压测试的写入文件功能」)。
        #    历史记录照旧落盘(JSON), 只是不再导出 txt。
        close_btn = Button(text="关闭", font_size="16sp", bold=True,
                           background_normal="", background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        content.add_widget(close_btn)
        popup = self._popup(0.90, 460, title="", content=content,
                            auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)
    def _show_bench_menu(self):
        """弹珠发射模拟测试菜单：三行两列，测试、历史、信息与帧率设定各自成对。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        _ver = _app_version()
        _title = ('弹珠发射模拟测试 ' + _ver) if _ver else '弹珠发射模拟测试'
        title_lbl = self._fit_line(Label(text=_title, bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title_lbl)
        # ⚠️ 文案在 `_bench_menu_desc()` 里(抽出去是为了让 `temp/check_desc.py` 能量到
        #    **出货这一份**; 它以前从 `tools/` 捞字面量, 而两边早已分叉)。
        # ⚠️ 末句的「连压 N 分钟」走常量, 别再写成硬编码字面量 —— 本轮改高压口径时差点漏掉。
        desc_lbl = Label(text=_bench_menu_desc(),
                         font_size='15sp', halign='left', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(170))
        # 说明是**多行正文** —— 只能用"高度跟着排版走"(缩字号会把整段一起缩小)。
        # 它原来定高 dp(170), 而排版后要 198px(400dp) / 242px(360dp) ⇒ 折出来的行被裁掉,
        # 看到的是一段缺尾巴的说明。现在折行只让弹窗长高。
        # ⚠️ 这里原先写着"最宽那句要 327px / 可用 246~307px" —— **两个都是过期数字**:
        #    327px 量的是**改文案之前**那版(那时这句还带着空格); 246~307 是弹窗加宽(0.84→0.88)
        #    之前的可用宽。**现在实测**: 6 行正文, 最宽一句是「全程约 25 秒(含完整的中奖装杯演出)。」
        #    = **259px**, 可用宽 360dp 上 261px / 400dp 上 296px。**只差 2px** —— 这也是它必须靠
        #    "高度跟着排版走"兜底的原因。量法: `python temp/check_desc.py`(从源码读字面量逐行量)。
        # ⚠️ 末段那两行是**手写 \n 断的**, 别删: 「第 2 项主要吃 CPU 单核浮点算力。」232px
        #    + 「物理引擎是纯 Python 写的。」192px = 424px > 可用 261px, 交给 Kivy 自动折的话
        #    断点会落在"算力。"后面那个**空格**上 —— 那正是当年"纯 Python 执行"被断成两行的老坑。
        self._auto_h(desc_lbl, dp(120), dp(8))
        content.add_widget(desc_lbl)
        # ⚠️ 版本/制作日期 与 音频体检**不在这里** —— 它们搬去「启动信息」独立弹窗了。
        #    用户 2026-09-11 定稿: 这里是跑分菜单, 那两样是启动信息, 不该混在一起。
        # ⚠️ **按系配色 + 按系排序**(2026-09-15 玩家:「这几个按钮的颜色你看看怎么改下,
        #    分几个系？同 1 类的是一个色系？」) —— **3 系, 同类同色系, 行动亮 / 历史暗**:
        #      模拟(性能测试): 红系   `COL_FIRE` / `COL_DARKRED`
        #      SOC 高压:        琥珀系 `COL_SOC` / `COL_SOC_DIM`
        #      信息:           中性蓝 `COL_BTN`(它不是测试, 不占两个测试系的颜色)
        #    ⚠️ **顺序也按系**: 模拟的两个在前 2 名(玩家 2026-09-15:「把开始测试和查看历史
        #       按钮放在一起, 都是在前 2 名」), SOC 的两个跟在后面。
        # ⚠️⚠️ **一行一个颜色 —— 右边的按钮用左边那个的颜色**(玩家 2026-09-15:
        #    「这几个按钮的颜色好乱啊。要不这样，右边的按钮用左边按钮的颜色？」「这样就3种颜色了」)。
        #    改之前是 6 个按钮 6 种颜色(行动亮 / 历史暗两两配对) ⇒ 玩家读成"颜色好乱"。
        #    现在**同一行同色**:  模拟系`COL_FIRE` / SOC 系`COL_SOC` / 信息系`COL_BTN`。
        #    ⚠️ 代价是**同一行里分不出"行动"和"历史"** —— 这是玩家明确选的取舍, 别自作主张改回去。
        #    ⚠️ `COL_DARKRED` / `COL_SOC_DIM` / `COL_BTN_OFF` 在本文件别处仍在用(其它弹窗的
        #       取消/返回/关闭), **不要因为它们在这里不用了就删掉**。
        start_btn = Button(text='开始模拟测试', font_size='17sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                           size_hint_y=None, height=dp(52))
        hist_btn = Button(text='查看模拟历史', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                          size_hint_y=None, height=dp(52))
        hp_btn = Button(text='SOC高压测试', font_size='17sp', bold=True,
                        background_normal='', background_color=hex_rgb(COL_SOC) + (1,),
                        size_hint_y=None, height=dp(52))
        hph_btn = Button(text='高压测试历史', font_size='17sp', bold=True,
                         background_normal='', background_color=hex_rgb(COL_SOC) + (1,),
                         size_hint_y=None, height=dp(52))
        info_btn = Button(text='启动信息', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                          size_hint_y=None, height=dp(52))
        cap_btn = Button(text='帧率上限设定', font_size='17sp', bold=True,
                         background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                         size_hint_y=None, height=dp(52))
        # 0.84 -> 0.88: 加宽之后 400dp 机器上说明的每一句都**刚好一行**(实测最长那句
        # 「全程约 25 秒(含完整的中奖装杯演出)。」259px < 可用 296px), 折行整个消失。
        # ⚠️ 最后那句「纯Python执行，反映设备跑弹珠的实际流畅度。」(291px) 在 360dp 上
        #    怎么都放不进一行(可用只有 261px), 自动折行会把末尾两个字**孤零零甩到下一行**。
        #    所以那一句的断行**写死**(见上面的字面量): 两段分别 93px / 210px, 360 和 400
        #    都一次放得下 —— 排版是我们定的, 不该让 Kivy 在运行时碰运气。
        #    (顺带: 原来「纯 Python」两边有空格, 而**空格就是 Kivy 的断行点**, 于是断成
        #     「纯 Python」+「执行, …」两行 —— 玩家报的"这个纯python执行的换行也很奇怪"就是它。)
        # ⚠️ 断点位置也是量出来的(不能随手断在逗号后): 断在逗号后 -> 第一行只用 111px/可用 261px,
        #    行末孤零零一个逗号 + 右边一大片空洞, 玩家一眼就问"为什么逗号和后面的字不在同一行"。
        #    现在断在「设备」后: 171 / 150 两段, 既都塞得下, 又不把断点落在「的」上
        #    (断在「弹珠」后能到 216/105 更饱满, 但下一行会以「的」开头 —— 中文避头尾不许)。
        popup = self._popup(0.88, 520, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        start_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_bench_test()))
        hist_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_bench_history()))
        info_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_startup_info()))
        hp_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_hp_test()))
        hph_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_hp_history()))
        cap_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_fps_cap_settings()))
        for left, right in ((start_btn, hist_btn), (hp_btn, hph_btn), (info_btn, cap_btn)):
            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
            row.add_widget(left)
            row.add_widget(right)
            content.add_widget(row)
        popup.open()
        self._popup_fit_content(popup, content)

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
        # 玩家 2026-09-11 定稿: 标题从「启动信息」改成 **跳跳的弹珠机v0.x.x**(见 _startup_title)。
        # 版本号全工程只在这里出现一次, 正文那行只剩制作时刻。
        title_lbl = self._fit_line(Label(text=_startup_title(), bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title_lbl)
        rows = []
        _ver = _app_version()
        if _ver:
            rows.append("游戏版本　%s" % _ver)
        try:
            _info = self._build_info()
        except Exception:
            _info = ""
        def _mk_lbl(_text, _align, _size='15sp', _h0=26):
            # ⚠️ 默认字号 16 -> 15: 与另外两个列表弹窗(跑分历史/每轮次数)统一到 15sp。
            #    这三个弹窗长得几乎一样, 却用了 17/16/15 三种字号 —— 玩家一眼就看出参差。
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
        # ⚠️ 顺序 = 屏幕上的上下顺序(纵向 BoxLayout)。玩家 2026-09-11: 「把确定按钮放在重放冷启动
        #    下面」⇒ **重放冷启动在上、确定在下**。别按"添加顺序像主次"去调, 它就是几何顺序。
        content.add_widget(replay_btn)
        content.add_widget(ok_btn)
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
        # ⚠️ 这里原来自己手抄了一份 `content.minimum_height + dp(64)`, 而结构里的非内容区
        #    实测只有 44dp(外壳内边距 24 + 空标题行 16 + 分隔条 4) —— 多出来的 20dp 因为
        #    纵向 BoxLayout 有富余时贴着底部堆, **全落在正文上方**(实测比内容高 20dp)。
        #    改走统一的 `_popup_fit_content`(按结构算 chrome, 且带"装不下"的兜底)。
        self._popup_fit_content(popup, content)

    def _show_replay_detail(self):
        """「重放冷启动」完成后点屏幕: 把**详细统计**摆出来(带确认按钮的独立窗口)。

        ⚠️ 玩家 2026-09-11 定稿: 「重放冷启动界面不应该显示各种文字 …… **完成之后点击屏幕
        任意位置, 弹出的是详细统计信息**, 有个新窗口专门展示(带确认按钮的这种)」——
        所以那些数从加载页搬到了这里, 加载页上只剩「跳跳的弹珠机」+ 底部一行「测试已经完成」。
        ⚠️ 内容仍然复用 `_replay_summary()`(`audio_detail()` 那一个真源), 不另写一套格式化。
        ⚠️ 整段 try/except + 给默认高度: 弹窗起不来也绝不能让玩家卡在加载页(项目红线)。"""
        try:
            content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
            head = self._fit_line(Label(text="重放冷启动 · 详细统计", bold=True,
                                        color=hex_rgb(COL_TEXT) + (1,),
                                        halign="center", valign="middle",
                                        size_hint_y=None, height=dp(34)), 19)
            content.add_widget(head)
            body = Label(text=self._replay_summary(), font_size="16sp",
                         color=hex_rgb(COL_SUB) + (1,),
                         halign="left", valign="top", size_hint_y=None, height=dp(26))
            # ⚠️ 行高按**真实排版**撑开: 手机上可用宽度更窄, 同一串字会折行 ——
            #    写死高度就会把折出来的第二行裁掉(这块面板栽在"文字被裁掉"上两次了)。
            body.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            body.bind(texture_size=lambda w, ts: setattr(w, "height", max(dp(26), ts[1] + dp(4))))
            content.add_widget(body)
            ok_btn = Button(text='确定', font_size='17sp', bold=True,
                            background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                            size_hint_y=None, height=dp(52))
            content.add_widget(ok_btn)
            popup = self._popup(0.86, 420, title='', content=content,
                                auto_dismiss=True, separator_height=0)
            ok_btn.bind(on_release=lambda *_: popup.dismiss())
            popup.open()
            self._popup_fit_content(popup, content)

            # 开完再按真实排版高度对一次(同「启动信息」那块面板的做法: 先给个估高, 再校正)。
            def _refit(*_):
                try:
                    _vw, _vh = self._veq()
                    popup.height = min(content.minimum_height + dp(64), _vh * 0.92)
                except Exception:
                    pass
            Clock.schedule_once(_refit, 0.06)
        except Exception:
            pass

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
        去掉「音效开关」那行(玩家刚点完按钮, 开关状态不需要再告诉他一遍)。
        ⚠️ 后来(2026-09-11)玩家把「音效开关」从 `audio_detail()` 里**整段删掉**了, 所以这里
        那道 `if not r.startswith("音效开关")` 过滤已成**死代码**, 一并删除。"""
        try:
            rows = []
            ver = _app_version()
            if ver:
                rows.append("游戏版本　%s" % ver)
            rows.extend(self.sfx.audio_detail())
            return "\n".join(rows)
        except Exception:
            return ""

    def _finish_replay_veil(self):
        """玩家点掉了「重放冷启动」那一屏: **摘页 + 弹出详细统计**。幂等; 绝不在这里动音频栈。

        玩家 2026-09-11: 「完成之后点击屏幕任意位置, 弹出的是**详细统计信息**, 有个新窗口专门
        展示(带确认按钮的这种)」—— 所以摘页和弹窗是一件事, 顺序是先摘页(别让弹窗盖在加载页上,
        那样关掉弹窗会露出一个已经没用的加载页)。"""
        v = getattr(self, "_replay_veil", None)
        self._replay_veil = None
        self._load_veil = None
        if v is not None:
            v.drop()
        self._show_replay_detail()

    def _start_bench_test(self):
        """开始性能测试(菜单点"开始测试"后)。"""
        self._bench_running = True
        # ⚠️⚠️ **把随机钉死 —— 跑分必须是"放录像", 不是"再抽一次"(2026-09-14, 玩家提的)。**
        #   病根: 每轮球的落格是随机的 ⇒ 中奖次数不同 ⇒ **装杯时长不同** ⇒ 内容配比每轮都不一样。
        #   实测连续三轮的装杯占比是 **25.2% / 30.6% / 38.2%**, 而 1%Low 是 92.4 / 92.2 / 88.4 ——
        #   差的 3.9 **全来自内容配比**, 不是代码变差。**追了三轮噪声。**
        #   随机源(查实的): 发球时 `arc_dy = random.uniform(...)` 用全局 `random`;
        #   撞钉扰动走 `rng = getattr(b, "_rng", None) or random` —— 正常发射 `_rng` 是 None
        #   ⇒ 也走全局。另外 `WinPileFX._rng` 是**无种子的独立实例**, 它决定装杯那几颗球的
        #   下落时长 ⇒ 直接影响装杯多久。**两处都要钉。**
        #   ⚠️ `getstate/setstate` 而不是"跑完再 seed()" —— 后者会把正常游戏的球路也弄得每局一样。
        try:
            self._bench_rng_state = random.getstate()
            random.seed(BENCH_SEED)
            _wf = getattr(self.game_area, "win_fx", None)
            self._bench_pile_rng = getattr(_wf, "_rng", None)
            if _wf is not None:
                _wf._rng = random.Random(BENCH_SEED + 1)
        except Exception:
            self._bench_rng_state = None
            self._bench_pile_rng = None
        self._bench_saved_status = self.status_lbl.text
        self._phys_started = False
        self._phys_done = 0
        self._prog_start()
        # ⚠️⚠️ **盘面也要存**(2026-09-15 玩家报的 bug, 与 `_bench_saved_status` 同一个理由)。
        #    跑分期间 `_auto_launch_tick` 每一发都把 9 个槽**全钉成 `BENCH_BOARD[i]` 那一个值**
        #    (第 5 发是 `100`), 而且**写回了缓存** `self._boards[self.rtp_target]`。
        #    跑分结束时只还了随机数、没还盘面 ⇒ **跑分一完, 下面 9 个倍率槽全是 `x100`**
        #    (玩家原话:「都是*100 这个明显不合理」), 一直挂到下一次发射才自愈。
        self._bench_save_board()
        ver = _app_version()
        _set_label_text(self.status_lbl, ("模拟测试中 " + ver) if ver else "模拟测试中…")
        self._set_controls_enabled(False)
        # 渲染跑分不额外叠加中央红字或飘字：被测画面只保留正常游戏 HUD，
        # 这样 1% Low 与玩家实际发射时看到的负载完全一致。
        self.game_area.hide_bench_badge()
        # ⚠️ **等启动预热跑完再采样**(2026-09-14)。采样窗口只有 7~12 秒
        #    (`_target_launches = 5`), 而玩家是启动后 3 秒就长按标题开跑的 —— 真机上一个
        #    预热单步要 100~200 毫秒(球纹理烘焙; 桌面只要 16.6), 常常还没跑完。
        #    混进采样里会把 1%Low 压下去, 而且量到的是**启动期**的数, 不是玩家平时玩的数。
        #    桌面逐帧归因实测: 最慢的 11 帧**全部**落在启动 0.6 秒内(= 预热链), 而那些帧
        #    我们自己的代码只花了 0.03~0.10 毫秒 —— 完全不该算进"玩起来卡不卡"。
        #    ⚠️ 有上限(最多等 20 秒): 预热万一卡住也不能把跑分永远挂在这儿 —— 本模块
        #    唯一的红线是"绝不软锁"。
        self._bench_wait_bake = 0.0
        self._await_prebake()

    def _await_prebake(self, dt=0):
        """预热没跑完就先等着(最多 20 秒), 跑完再开采样。见 `_start_bench_test` 处说明。"""
        if _PREBAKE_DONE[0] or self._bench_wait_bake >= 20.0:
            self._start_benchmark()
            return
        self._bench_wait_bake += 0.25
        Clock.schedule_once(self._await_prebake, 0.25)

    def _start_benchmark(self):
        """阶段1: 真实屏幕采样(on_flip, 自动发球5发), 发满后停止采样，再测阶段2物理吞吐。"""
        self.game_area.hide_bench_badge()
        self._flip_times = []
        # ---- 诊断(2026-09-13 加): 光有"平均帧率/1%Low"没法定位卡在哪 —— 见 _bench_tag ----
        self._bench_frames = []          # [(帧间隔ms, 场景标签)]
        self._bench_gc = {}              # gen -> [次数, 总秒, 最坏秒]
        self._bench_gc_t0 = 0.0
        self._bench_cpu0 = time.process_time()
        self._bench_cpu_prev = self._bench_cpu0
        self._bench_thr_prev = _THREAD_TIME()
        _FRAME_CALLS[0] = 0
        _TEXUPD[0] = 0
        _TEXUPD_BY.clear()
        _TEXUPD_ACTIVE[0] = True
        # ⚠️ 缓存命中/未命中计数**必须跟着归零**: 它是"这一轮缓存有没有生效"的读数,
        #    带着上一轮的残值就等于在骗自己。
        _TEXEX_HIT[0] = 0
        _TEXEX_MISS[0] = 0
        # ⚠️ 必须在这里归零: 上一轮跑分残留的 swap 值会被当成"第一帧等屏幕的时间"记进新日志的
        #    第一行(那种"凭空冒出来的 40 毫秒"没人解释得了)。
        _FRAME_SWAP[0] = 0.0
        _FRAME_BRK.clear()
        # 屏幕刷新率采样(见 `_BENCH_HZ` 处说明): 清零 + 起一个 0.5 秒的 tick。
        # ⚠️ tick 自己会在采样结束时返回 False 摘掉自己, 不用另找地方 unschedule。
        _BENCH_HZ.clear()
        try:
            Clock.schedule_interval(self._bench_hz_tick, 0.5)
        except Exception:
            pass
        # 同上, 「字号」的分解计数必须跟着归零 —— 不归零的话采样窗口第一帧会背着
        # "上次采样结束以来"的全部累计, 印出一个没人解释得了的"叫了 300 次"。
        _FRAME_FIT[0] = 0
        _FRAME_FIT[1] = 0
        # 冷字号榜也要清 —— 不清的话第一份日志里会混着上一轮的残值(那是"没发生的事")。
        del _COLD_FS[:]
        # ⚠️ **同理, 而且这三个以前一直没清**(2026-09-14 修, 对抗性评审的专家指出):
        #    发声/震动计数(`Sfx.play` 里 `_FRAME_PROBE[0] += 1`、`_vibrate_tick` 里 `[1] += 1`)
        #    和预热位 `[2]` 都是**裸模块级计数, 采样期之外照常累加**, 而 `_on_flip` 只在
        #    `if prev is not None:` 里复位它们 ⇒ **日志第一行背的是"上次采样结束以来"的全部累计**。
        #    真机铁证: v0.7.23 的 120Hz 日志第一行是 `0.74,待机,4,0.03,0.60,6,0,...`,
        #    一帧只有 0.74 毫秒却背着 `发声6`(全窗口次大才 3)、`字号1010.0`(全 3069 行里唯一
        #    ≥100ms 的, 次大 4.9ms)—— 那两个数物理上装不进 0.74 毫秒。`_FRAME_BRK` 已在
        #    v0.7.23 之后单独修掉, 这三个是同一次漏下的。
        #    ⚠️ **别图省事把 `_on_flip` 里那三行挪出 `if prev is not None`** —— 那里必须
        #    **每帧清**(它清的是"这一帧记了多少"), 而这里要的是**开跑前清一次**。两处都要。
        _FRAME_PROBE[0] = 0
        _FRAME_PROBE[1] = 0
        _FRAME_PROBE[2] = 0
        # CPU 调频状态(工作线程里采, 不占主线程) —— 见 `_CPUFRQ` 处的说明: 玩家的第三方工具
        # 在跑分期看到"前期只有 1.1GHz、后期才 4.5GHz", 而 `主线程ms` 是**真实 CPU 秒**,
        # 主频差 4 倍会让同一个函数量出来差 4 倍。不采这一格, 上面所有 CPU 数字都缺前提。
        _cpufreq_start()
        # 逐帧文字纹理重建计数(与 `_bench_frames` 同序等长的平行表, 见 `_on_flip` 末尾)
        self._bench_tex = []
        self._bench_tex_prev = _TEXUPD[0]
        self._bench_cpusplit0 = _cpu_split()
        _SND_STAT[0] = 0.0
        _SND_STAT[1] = 0.0
        _SND_STAT[2] = ""
        _SND_STAT[3] = 0.0
        _VIB_STAT[0] = 0.0
        _VIB_STAT[1] = 0.0
        _VIB_STAT[2] = ""
        _JNI_STAT[0] = 0.0
        _JNI_STAT[1] = 0.0
        _JNI_STAT[2] = 0
        _JNI_STAT[3] = 0.0
        _JNI_STAT[4] = 0.0
        _JNI_STAT[5] = 0
        _JNI_STAT[6] = 0.0
        _JNI_STAT[7] = 0
        _JNI_STAT[8] = 0.0
        _CFG_STAT[0] = 0.0
        _CFG_STAT[1] = 0.0
        _CFG_STAT[2] = 0
        self._bench_wall0 = time.time()
        import gc
        try:
            gc.callbacks.append(self._bench_gc_cb)
        except Exception:
            pass
        Window.bind(on_flip=self._on_flip)
        self._launch_count = 0
        self._target_launches = BENCH_TARGET_LAUNCHES
        # 只在跑分期间轮询；0.1 秒把每局结束到下一发的空档从最多 0.5 秒缩到最多 0.1 秒。
        # 回调只读状态，发射后立即离开 ready，不会重复触发或改变游戏物理。
        self._auto_evt = Clock.schedule_interval(self._auto_launch_tick, 0.1)

    def _bench_gc_cb(self, phase, info):
        """量每一次 GC 的耗时。安卓上 GC 停顿直接表现为掉帧, 而本工程从来没调过 gc。"""
        try:
            if phase == "start":
                self._bench_gc_t0 = time.perf_counter()
                return
            d = time.perf_counter() - self._bench_gc_t0
            e = self._bench_gc.setdefault(info.get("generation", -1), [0, 0.0, 0.0])
            e[0] += 1
            e[1] += d
            if d > e[2]:
                e[2] = d
        except Exception:
            pass

    def _bench_tag(self):
        """这一帧"屏幕上在演什么"。跑分现在会把装杯演出一起采样(25s 窗口被拉到 ~15s),
        所以只看总平均分不清"飞行卡"还是"装杯卡" —— 分场景统计才能回答玩家问的那句
        「球在飞的时候一卡一卡的」。"""
        fx = getattr(self.game_area, "win_fx", None)
        md = getattr(fx, "mode", "idle") if fx is not None else "idle"
        if md in ("pending", "win", "result"):
            return "装杯"
        st = getattr(self, "state", "ready")
        if st == "flying":
            return "飞行"
        if st == "landing":
            return "落袋"
        if st == "misfire":
            return "哑火"
        if st == "charging":
            return "蓄力"
        return "待机"

    def _on_flip(self, win):
        # ⚠️ **量帧间隔必须用单调钟, 不能用 `time.time()`**(2026-09-14 修)。
        #    `time.time()` 是 **CLOCK_REALTIME** —— 会被 NTP 校时、用户改时间、时区/夏令时
        #    调整**跳变**。而这里记下来的差值, 就是面板上「平均帧率 / 1%Low / 10%Low / p99 /
        #    p90」的**全部输入**。一次 +30ms 的校时跳变会被原样记成"一帧 30 毫秒", 直接落进
        #    1%Low 那一档(那档只有 6~10 帧)。
        #    `perf_counter` 单调、高精度, 正是量间隔该用的钟。
        #    ⚠️ 别顺手把**别处**的 `time.time()` 也换掉 —— 动画时间轴要的就是墙钟绝对值
        #    (切后台回来"直接跳终态"依赖它), 那个语义是对的。
        now = time.perf_counter()
        cpu = time.process_time()
        # ⚠️ **这一格必须真的写**(2026-09-14 修一个真 bug): `_FRAME_THR[0]` 原来在出货文件里
        #    只有"读进帧记录"和"面板打印", **没有任何一处赋值** ⇒ 面板那格"主线程"永远是
        #    **硬编码 0.0**, 而格式串照常打印"主线程0.0"、还会因为别的数 >=1.0 打开长格式分支,
        #    字符串看起来完全健康。最坏的一种: 一个专抓静默归因的面板, 用一个常量冒充测量值。
        #    (对抗性评审压测段的 1 号专家独立查出来的。)
        _tt = _THREAD_TIME()
        _FRAME_THR[0] = (_tt - getattr(self, "_bench_thr_prev", _tt)) * 1000.0
        self._bench_thr_prev = _tt
        prev = self._flip_times[-1] if self._flip_times else None
        pcpu = self._bench_cpu_prev
        self._bench_cpu_prev = cpu
        self._flip_times.append(now)
        if prev is not None:
            # ⚠️ 第三个字段是**这一帧真的烧了多少 CPU**(process_time 差)。
            #    真机(Y700 二代)实测最慢帧有 130~156ms —— 光知道"当时在飞行"不够,
            #    必须能分清它是**算出来的**(实算接近帧间隔 ⇒ 处理器瓶颈) 还是
            #    **等出来的**(实算很小 ⇒ GC/IO/显卡/驱动在阻塞)。这是分流的那一刀。
            #    Linux/安卓 上 process_time 是 clock_gettime(CLOCK_PROCESS_CPUTIME_ID),
            #    纳秒级; Windows 上精度只有 15.6ms, 所以桌面看不出分辨力, 真机才有效。
            self._bench_frames.append(((now - prev) * 1000.0, self._bench_tag(),
                                       (cpu - pcpu) * 1000.0,
                                       _FRAME_PROBE[0], _FRAME_PROBE[1],
                                       _FRAME_SELF[0], _FRAME_PROBE[2],
                                       _FRAME_THR[0], _SINCE_LAUNCH[0],
                                       tuple(sorted(
                                           ((v * 1000.0, k) for k, v in _FRAME_BRK.items()
                                            if v > 0.0002), reverse=True)[:4]),
                                       # [10] = **上一次** `Window.flip()` 阻塞了多少毫秒。
                                       # ⚠️ 是"上一次"不是"这一次": 绑定回调 `on_flip` 跑在默认
                                       #    处理器(真正 swap)之**前**(桌面实测序列恒为 CB SWAP),
                                       #    所以读到的必然是上一帧那一笔。而这正好与这一行记的
                                       #    帧间隔配对 —— 那段间隔里含的就是那一次 swap。
                                       # ⚠️ **只能追加在末尾**: 前面 [0]~[9] 的下标被
                                       #    `_bench_collect_diag` 按号取, 插在中间会重演 v0.6.82
                                       #    "把 [7]/[8] 写反、整块诊断静默返回空串"那一次。
                                       _FRAME_SWAP[0],
                                       # [11] = 本帧「字号」的分解: (`_fit1` 调用次数,
                                       # `text_px` 冷测量次数)。见 `_FRAME_FIT` 处说明 ——
                                       # 光有 `字号50.7` 这一个数, 分不出"一次走完阶梯+二分"
                                       # 还是"一帧里十个标签同时换字", 而那两件事的修法完全相反。
                                       (_FRAME_FIT[0], _FRAME_FIT[1])))
            # ⚠️ 本帧冷开次数的**普查**(2026-09-15, 对抗评审第 0 批): `(N次/M测)` 只在
            #    「字号」挤进该帧最大两个子步骤时才印 —— 21059 帧里只印过 2 帧, 是采样。
            #    这一行让"一帧到底会不会开十几个"变成普查。**必须在下面清零之前记。**
            _FIT_HIST[_FRAME_FIT[1]] = _FIT_HIST.get(_FRAME_FIT[1], 0) + 1
            _FRAME_BRK.clear()
            _FRAME_FIT[0] = 0
            _FRAME_FIT[1] = 0
            _FRAME_PROBE[0] = 0
            _FRAME_PROBE[1] = 0
            _FRAME_PROBE[2] = 0
            _FRAME_SWAP[0] = 0.0
            # 这一帧发生了几次**文字纹理重建** —— 与 `_bench_frames` **同序等长的平行表**。
            # ⚠️ 用平行表, 不往 `_bench_frames` 的元组里加字段: 那个元组的**下标被
            #    `_bench_collect_diag` 到处按号取**(v0.6.82 就因为把 [7]/[8] 写反, 让整块诊断
            #    静默返回空串)。平行表不加新下标, 零风险。
            # ⚠️ `_TEXUPD[0]` 数的是**真的 `Label.texture_update`**(由 Kivy 用 Clock 延后执行,
            #    跑在 `_frame` 外面) —— 所以它落在"那一笔账被还上"的那一帧, 而不是"改 text"
            #    的那一帧。这正是要比对的东西: 慢帧当帧有没有在还文字的账。
            self._bench_tex.append(_TEXUPD[0] - self._bench_tex_prev)
            self._bench_tex_prev = _TEXUPD[0]

    def _auto_launch_tick(self, dt):
        # ⚠️⚠️ **2026-09-15: 这里逐句埋点。** 真机 v0.7.31 抓到一个 55.92 毫秒的帧:
        #     帧1201 [自动发51.4] —— **整个 `_auto_launch_tick` 51.4 毫秒, 而它调的两个
        #     子函数(`槽面` = `_update_slots` / `起蓄` = `start_charge`)**都没进前二
        #     (没进前二 = 各自 ≤0.2 毫秒)。也就是说那 51 毫秒花在这个方法**自己身上**,
        #     不在它调的东西里。这个方法剩下能烧时间的只有下面这几句, 所以逐句量开:
        #     桌面探针当初是 `自动发30.3 / 槽面30.2`(那时钱全在 `_update_slots`), 真机不是
        #     —— 两边不一样, 不能照搬结论。
        # ⚠️ `_brk_add` 只在跑分采样期有意义(平时 `_FRAME_BRK` 没人读), 但这几句本身
        #    就是一次 `perf_counter` + 一次字典累加, 比它包住的赋值贵不了多少, 不另加开关。
        _ta = time.perf_counter()
        if self._launch_count >= self._target_launches:
            self._finish_render_sample(0)
            return
        if self.state == "ready":
            # ⚠️ **跑分: 把这一发的盘面钉死**(见 `BENCH_BOARD` 处说明)。
            #    必须放在 `start_charge()`(也就是发射)**之前** —— 结算读的是 `self.multipliers[i]`,
            #    盘面得在球飞出去之前就定下来。
            #    ⚠️ 只改**值**、不改结构 ⇒ 用 `_update_slots()` 增量更新就够
            #    (它结构对不上时会自己退回完整 `_redraw()`, 不会半更新)。
            _tb = time.perf_counter()
            try:
                _bi = self._launch_count
                self._bench_ball_i = _bi      # 给 `launch()` 派生碰撞随机流用
                if 0 <= _bi < len(BENCH_BOARD):
                    self.multipliers = [BENCH_BOARD[_bi]] * NUM_SLOTS
                    self._boards[self.rtp_target] = self.multipliers
                    self.game_area._update_slots()
            except Exception:
                pass
            _brk_add("发·盘面", _tb)
            _tc = time.perf_counter()
            self.start_charge()
            _brk_add("发·起蓄", _tc)
            self._launch_count += 1
            _td = time.perf_counter()
            Clock.schedule_once(lambda _: (setattr(self, "power", 0.8), self.launch()), 0.1)
            _brk_add("发·排程", _td)
        _brk_add("发·整段", _ta)

    def _finish_render_sample(self, dt):
        """停止屏幕采样, 统计真实 FPS/掉帧, 等球落地后启动物理 benchmark。"""
        if getattr(self, "_auto_evt", None):
            self._auto_evt.cancel()
            self._auto_evt = None
        Window.unbind(on_flip=self._on_flip)
        flips = self._flip_times or []
        self._render_lows = {}
        self._render_pct = {}
        if len(flips) >= 2:
            gaps = [flips[i + 1] - flips[i] for i in range(len(flips) - 1)]
            self._render_gaps_ms = [gap * 1000.0 for gap in gaps]
            s = sorted(gaps)
            # 真平均 = 总帧数 / 总耗时；旧版误把中位数标成“平均”，外部工具无法对照。
            self._render_fps = len(gaps) / sum(gaps) if sum(gaps) > 0 else 0.0
            self._render_median_fps = 1.0 / s[len(s) // 2] if s[len(s) // 2] > 0 else 0.0
            # 低帧率(玩家 2026-09-13 要的 1%/10%): "最慢 N% 的帧"**平均下来**是多少 FPS。
            # ⚠️ 它和下面的 p99/p90 **不是一回事**: 这里是"最慢那批的均值", 那里是"分位上那一帧"。
            #    偶发几个巨大尖峰时, 均值会被拉得比阈值狠得多 —— 两个一起看才分得清
            #    "偶发几次长停顿"和"整体都慢"。
            for _pct in (1, 10):
                n = max(1, int(len(s) * _pct / 100.0))
                self._render_lows[_pct] = 1.0 / (sum(s[-n:]) / n) if sum(s[-n:]) > 0 else 0.0
            self._render_1low = self._render_lows[1]
            # p99/p90 帧率(玩家 2026-09-13 要的): **99% / 90% 的帧都比它快**。
            # 取"慢侧分位上那一帧"的帧率(不是均值) —— 它是**门槛**, 不是平均。
            # ⚠️ `gaps` 是 `time.time()` 的差值 = **秒**, 所以这里除的是 1.0 不是 1000.0
            #    (写成 1000.0 会虚高一千倍 —— 面板上会看到 "p99帧率: 66438" 这种鬼数)。
            self._render_pct = {}
            for _q in (99, 90):
                _sec = s[min(len(s) - 1, int(len(s) * _q / 100.0))]
                self._render_pct[_q] = (1.0 / _sec) if _sec > 0 else 0.0
        else:
            self._render_fps = 0.0
            self._render_median_fps = 0.0
            self._render_gaps_ms = []
            self._render_1low = 0.0
        _cpufreq_stop()
        self._bench_diag = self._bench_collect_diag()
        _TEXUPD_ACTIVE[0] = False
        self._wait_idle_then_bench()

    def _bench_hz_tick(self, _dt=0.0):
        """采样期每 0.5 秒记一次「当时在演什么 + 屏幕刷新率」。见 `_BENCH_HZ` 处说明。

        ⚠️ `_screen_hz()` 在安卓上走 JNI, **绝不能每帧调** —— 0.5 秒一次可以忽略。
        ⚠️ 采样期一结束就**自己停**(返回 False 让 Clock 摘掉它); 不然跑完分还在后台
           每 0.5 秒戳一次 JNI。
        """
        if not _TEXUPD_ACTIVE[0]:
            return False
        try:
            _hz = round(float(_screen_hz() or 0.0), 1)
            if _hz > 0.0:
                _k = (self._bench_tag(), _hz)
                _BENCH_HZ[_k] = _BENCH_HZ.get(_k, 0) + 1
        except Exception:
            pass
        return True

    def _bench_collect_diag(self):
        """把采样到的原始帧信息整理成可读诊断。**只读采样结果, 不改任何行为。**"""
        import gc
        try:
            gc.callbacks.remove(self._bench_gc_cb)
        except Exception:
            pass
        out = {}
        fr = list(getattr(self, "_bench_frames", []) or [])
        if not fr:
            return out
        gaps = sorted(x[0] for x in fr)
        nn = len(gaps)
        pk = lambda q: gaps[min(nn - 1, int(nn * q))]
        out["n"] = nn
        # 采样窗口的**时长**(毫秒) = 帧间隔之和。给成绩面板印「采样窗口：X 秒 · N 帧」用。
        # ⚠️ 口径必须与 `_bench_frame_log()` 的日志头一致(那边也是 `sum(gaps)`), 否则
        #    面板与日志会各印一个窗口, 读者对不上账 —— 本工程栽过好几次"两处各算一遍"。
        # ⚠️ **不要**用 `self._flip_times[-1] - self._flip_times[0]`: 那个含首帧之前的一段,
        #    会比这里多一帧, 两边差一个帧间隔。
        out["win_ms"] = sum(float(x[0]) for x in fr)
        out["p50"] = pk(0.50)
        out["p99"] = pk(0.99)
        out["max"] = gaps[-1]
        # 与成绩页 1% Low 使用同一批最慢帧，专门保存成可读的归因摘要。
        # 这样结果页不必再堆满与优化无关的累计计数。
        _low1_n = max(1, int(nn * 0.01))
        _low1 = sorted(fr, key=lambda x: x[0], reverse=True)[:_low1_n]
        out["low1_n"] = _low1_n
        out["low1_ms"] = sum(x[0] for x in _low1) / _low1_n
        _low1_groups = {}
        for _x in _low1:
            _low1_groups[_x[1]] = _low1_groups.get(_x[1], 0) + 1
        out["low1_groups"] = sorted(_low1_groups.items(), key=lambda x: -x[1])
        # 16.7ms 是 60 FPS 的帧预算；120Hz 的平均值高并不代表没有越过这条线。
        out["over60_n"] = sum(1 for x in fr if x[0] > (1000.0 / 60.0))
        # ---- **卡顿帧** = 帧率低于「本机中位帧率」一半的帧(帧间隔 > 中位 x 2) ----
        # ⚠️ **必须用相对本机中位的判据, 不能写死绝对帧率**: 165Hz 与 60Hz 的机器上同一个
        #    绝对门槛含义完全不同(交接文档 `jiaojie.md` 已把这条定死; 原来硬编码 1000/90,
        #    结果 1%Low 贴到 89.7 时同一份数据反而报"变差")。
        # ⚠️ 玩家 2026-09-14 定稿的两档: **<50% 要尽量消除; 50%~70% 只作参考**。
        #    所以这里只收 <50% 的那一档进 `jank_*`, 50%~70% 留在日志里当趋势看, **不上面板**。
        _med = float(out.get("p50") or 0.0)
        _jank = [x for x in fr if _med > 0.0 and x[0] > _med / JANK_RATE]
        out["jank_n"] = len(_jank)
        # **慢帧** = 帧率低于「中位帧率 75%」—— 比卡顿帧宽一档, **包含**卡顿帧。
        # ⚠️ 玩家 2026-09-14 定稿: 成绩面板上**两档都要印**(原来只印了卡顿帧) ——
        #    只看最窄那档会漏掉"虽然没到卡顿、但已经明显掉帧"的那一批, 而那批才是趋势。
        # ⚠️ **先取出这一个 list, 再拿它同时算"有几帧"和"分布"** —— 见文件末尾那段
        #    说明: 写成两趟就是"两处各算一遍", 迟早印出「慢帧 168 帧」而分布只有 9。
        _slow75 = [x for x in fr if _med > 0.0 and x[0] > _med / SLOW_RATE]
        out["slow_n"] = len(_slow75)
        _sg = {}
        for _x in _slow75:
            _sg[_x[1]] = _sg.get(_x[1], 0) + 1
        out["slow_groups"] = sorted(_sg.items(), key=lambda x: -x[1])
        _jg = {}
        for _x in _jank:
            _jg[_x[1]] = _jg.get(_x[1], 0) + 1
        out["jank_groups"] = sorted(_jg.items(), key=lambda x: -x[1])
        # 最慢的 3 帧 + 当时在演什么 + **那一帧真烧了多少 CPU**。
        # 后两个数一起看才分流: 实算 ≈ 帧间隔 ⇒ 算出来的(处理器瓶颈);
        # 实算很小 ⇒ 等出来的(GC/IO/显卡/驱动阻塞) —— 真机上这是唯一能分清的地方。
        # 带上它在 `_bench_frames` 里的**下标** —— 三个最慢帧是不是**同一段忙碌里的连续帧**,
        # 看下标就知道(现在看不出来)。
        by_worst = [x for _i, x in sorted(enumerate(fr), key=lambda p: -p[1][0])[:3]]
        # 每一帧带上"自算"(_frame 在本线程上的耗时, 第 6 个字段)。
        # [帧间隔, 场景, 全进程实算, 本线程自算, 这一帧是不是启动预热]
        # [帧间隔, 场景, 全进程实算, 自算, 是否预热, 本帧最大的两笔子步骤]
        # [帧间隔, 场景, 全进程实算, 主线程, 自算, 是否预热, 本帧最大的两笔子步骤]
        # [帧间隔, 场景, 全进程实算, 主线程(x[7]), 自算(x[5]), 是否预热(x[6]), 子步骤(x[8])]
        _idx_of = {}
        for _i, _x in enumerate(fr):
            _idx_of[id(_x)] = _i
        out["worst"] = [[x[0], x[1], x[2], (x[7] if len(x) > 7 else 0.0),
                         (x[5] if len(x) > 5 else 0.0),
                         (x[6] if len(x) > 6 else 0),
                         (x[9] if len(x) > 9 else ()),      # breaks(子步骤) —— 加字段时别忘同步
                         _idx_of.get(id(x), -1)] for x in by_worst]
        # 全程自算的中位数 —— 与"实算"并排看: 两者都小 ⇒ 主线程既没算也没被我们的代码占,
        # 帧时间就是框架/出图的开销(优化我们的代码没用)。
        _self_sorted = sorted((x[5] if len(x) > 5 else 0.0) for x in fr)
        out["self_p50"] = _self_sorted[len(_self_sorted) // 2] if _self_sorted else 0.0
        out["self_max"] = _self_sorted[-1] if _self_sorted else 0.0
        # 主线程 CPU(线程级时钟) —— 它与"自算"的**差额就是 `_frame` 外面那一大块**
        # (Kivy 渲染 / 延迟的文字重排 / 其它 Clock 回调)。见 _FRAME_THR 处的说明。
        # ⚠️ 索引别记错: 帧记录尾部依次是 `..., _FRAME_SELF, _FRAME_PROBE[2], _FRAME_THR, breaks`
        #    ⇒ **主线程在 [7]、子步骤在 [8]**。v0.6.82 把这两个写反了, 于是 `thr_p50` 拿到的是
        #    **元组**, 面板那行 `%.1f` 直接 TypeError, 而它外面那个 except 把整块诊断**静默**
        #    返回成了空串 —— 一个专抓静默的面板自己静默了(2026-09-14 实测踩到)。
        _thr_sorted = sorted((x[7] if len(x) > 7 else 0.0) for x in fr)
        out["thr_p50"] = _thr_sorted[len(_thr_sorted) // 2] if _thr_sorted else 0.0
        out["thr_max"] = _thr_sorted[-1] if _thr_sorted else 0.0
        out["texupd"] = _TEXUPD[0]
        # 文字纹理缓存: 命中 = 两趟全跳过(省掉 4~10.7ms 的 `填纹`)。见 `_texupd_wrap`。
        # ⚠️ 这两个数**必须印进日志** —— 否则没法判断这层到底有没有生效(命中率 0 时
        #    必须能一眼看出来, 而不是"看着像好了"却什么都没省)。
        out["texex_hit"] = _TEXEX_HIT[0]
        out["texex_miss"] = _TEXEX_MISS[0]
        # ⚠️ 只留前 4 名 —— 但**必须把"其余还有多少"一并记下来**(2026-09-14 修)。
        #    原来只有 `[:4]`, 于是真机 v0.7.23 的日志里"全程文字重建 76 次"与"重建来源
        #    四项相加 68"差了 8 次(约 10%), 而**日志里没有任何地方提示这里截断了** ——
        #    看日志的人只会以为那 8 次凭空消失。加一个"其余合计"就能自洽。
        _by_all = sorted(_TEXUPD_BY.items(), key=lambda it: -it[1])
        out["texupd_by"] = _by_all[:4]
        out["texupd_rest"] = sum(int(c) for _, c in _by_all[4:])
        out["texupd_tags"] = len(_by_all)
        # ---- 等屏幕(swap 阻塞)的分布 ----
        # ⚠️ 2026-09-14 加。为什么要它: 真机同一天两份日志(屏幕 120Hz / 60Hz)显示"慢帧"在两种
        #    刷新率下**都**落在 1.35~1.45 个刷新周期上, 比例几乎一样 ⇒ 只降帧率救不了。
        #    而那批慢帧里有一大半**主线程根本没烧 CPU**(<30% 帧长) —— 光看 CPU 分不出它们是
        #    "我们交晚了"还是"屏幕不让交"。swap 阻塞就是那把刀。
        _sw = sorted(float(x[10]) if len(x) > 10 else 0.0 for x in fr)
        out["swap_p50"] = _sw[len(_sw) // 2] if _sw else 0.0
        out["swap_max"] = _sw[-1] if _sw else 0.0
        out["swap_sum"] = sum(_sw)
        # ---- 节拍真值: 屏幕多少 Hz / 请求的模式 / Kivy 实际生效的上限 / vsync ----
        # ⚠️ 这三样 `_FPS_INFO` 里一直有, 但**从没进过日志文件** —— 而它们是"这份日志能不能
        #    和上一份比"的唯一前提。实测教训: 120Hz 那轮平均 120.3fps、60Hz 那轮 60.1fps,
        #    同一份代码同一个版本, 1%Low 一个是 83.3 一个是 42.2, 差别全部来自屏幕档位。
        #    **没有这一行, 跨版本比较(75.1 -> 78.2 -> 83.3 那种)根本不知道是不是同一把尺子。**
        try:
            out["screen_hz"] = float(_FPS_INFO[0] or 0.0)
            out["req_hz"] = float(_FPS_INFO[2] or 0.0)
        except Exception:
            out["screen_hz"], out["req_hz"] = 0.0, 0.0
        try:
            out["clock_maxfps"] = float(getattr(Clock, "_max_fps", 0.0) or 0.0)
        except Exception:
            out["clock_maxfps"] = 0.0
        # ---- CPU 调频状态(见 `_CPUFRQ` 处说明) ----
        # ⚠️ 单位是 MHz; 采不到就留空列表, 打印端据此**整段不印**(不印 0 冒充量到)。
        try:
            _fr_ = list(_CPUFRQ.get("freqs") or [])
            out["cpufreq_n"] = len(_fr_)
            out["cpufreq_cap"] = float(_CPUFRQ.get("cap", 0.0) or 0.0)
            out["cpufreq_p50"] = _fr_[len(_fr_) // 2] if _fr_ else 0.0
            # 平均 —— 玩家 2026-09-15:「cpu频率不能用中位数」「所有"代表值"都改平均」。
            # ⚠️ 这一段是**渲染窗口**采的, 应用大部分时间在等 vsync ⇒ 均值天生偏低,
            #    打印端**必须注明它是渲染窗口**(否则会被读成"跑分时只有这么点")。
            out["cpufreq_mean"] = (sum(_fr_) / len(_fr_)) if _fr_ else 0.0
            out["cpufreq_min"] = min(_fr_) if _fr_ else 0.0
            out["cpufreq_max"] = max(_fr_) if _fr_ else 0.0
            # 低于"上限 50%"的采样占比 —— 直接回答"是不是全程在低频跑"。
            _cap = out["cpufreq_cap"]
            out["cpufreq_low_pct"] = (100.0 * sum(1 for _v in _fr_ if _cap and _v < 0.5 * _cap)
                                      / len(_fr_)) if (_fr_ and _cap) else -1.0
        except Exception:
            out["cpufreq_n"] = 0
        # 节拍事实: Kivy 的限速旋钮实际是什么值, 以及"逻辑更新几次 vs 呈现了几帧"。
        try:
            from kivy.config import Config as _Cfg
            out["maxfps"] = str(_Cfg.get("graphics", "maxfps"))
            out["vsync"] = str(_Cfg.get("graphics", "vsync")) or "(空=不改)"
        except Exception:
            out["maxfps"], out["vsync"] = "?", "?"
        out["frame_calls"] = _FRAME_CALLS[0]
        try:
            from kivy.config import Config as _Cfg2
            out["multisamples"] = str(_Cfg2.get("graphics", "multisamples"))
        except Exception:
            out["multisamples"] = "?"
        # ---- C6 判据: 最差 20 帧距上一次发射各过了几帧 ----
        try:
            _w20 = sorted(fr, key=lambda x: -x[0])[:20]
            _pos = [(x[8] if len(x) > 8 else -1) for x in _w20]   # [8] = 距上次发射的帧数
            _pos = [p for p in _pos if p is not None]
            _pos.sort()
            out["w20_pos"] = _pos
            out["w20_near"] = sum(1 for p in _pos if p <= 20)   # 发射后 20 帧内(约 0.33 秒)
        except Exception:
            out["w20_pos"] = []
            out["w20_near"] = -1
        _cs1 = _cpu_split()
        _cs0 = getattr(self, "_bench_cpusplit0", None)
        if _cs1 and _cs0:
            out["utime_ms"] = (_cs1[0] - _cs0[0]) * 1000.0
            out["stime_ms"] = (_cs1[1] - _cs0[1]) * 1000.0
        else:
            out["utime_ms"] = out["stime_ms"] = -1.0
        # 慢帧的"节拍"与"这一帧在发声/震动吗" —— 定位偶发长停顿的两把刀:
        #   · 节拍规则 ⇒ 时钟驱动(某个 0.5s 定时器); 不规则 ⇒ 事件驱动。
        #   · 慢帧里绝大多数在发声/震动 ⇒ 就是那条路(玩家"关音效就变好"的因果线索)。
        # ⚠️ 门槛按**中位帧的相对比例**定, 不用任何绝对值(2026-09-14 玩家定稿)。
        #    来由: 原来两处各写一套绝对值(`BENCH_SLOW_MS=90` 与 `1000/90`), 而本机最慢帧
        #    只有 40ms ⇒ `_slow` **恒为空集**, "慢帧的节拍/在发声/在震动"几行永远不打印;
        #    换台低刷设备又会反过来把普通帧判成慢帧。
        #    实证(真机日志 v0.7.32 → v0.7.34): 1%Low 从 77.9 改善到 89.7, 而绝对 90fps
        #    口径反而报"恶化"(9→12 帧) —— 因为 1%Low 已贴着 90 门槛, 计数在门槛附近抖动。
        #    同一份数据按"中位帧 50%"算是 7→6 帧, 与 1%Low **同向**。
        #    定稿: 慢帧 = 中位帧率的 50% ⇒ 帧时间 >= 2 × 中位帧时间。
        # ⚠️⚠️ **这两个键必须叫 `slow2_*`, 不能叫 `slow_n`/`slow_ms`**(2026-09-14 实修)。
        #    上面(收尾统计那段)已经把 `out["slow_n"]` 定义成「中位帧率 <75%」的帧数 ——
        #    那是**成绩面板**读的数; 而这里这一批是**归因分析集**(≥2 倍中位帧时间, 即 <50%),
        #    是给"长停顿的节拍/在不在发声/在不在震动"用的。
        #    原来这里写的是 `out["slow_n"] = len(_slow)`, **把面板那个数原地覆盖成了 5**,
        #    面板再套一层 `max(slow_n, jank_n)` 兜底 ⇒ 印出来**恒等于卡顿帧**。
        #    真机 v0.7.35 实测: <75% 真值 **168 帧**, 被覆盖成 5, 面板印的却是 9(= 卡顿帧)。
        #    玩家原话:「慢帧<75% 的实际帧率数量也是错的吧, 他目前和卡顿帧一直相同」。
        _slow_ms = 2.0 * out["p50"]
        out["slow2_ms"] = _slow_ms
        _slow = [x for x in fr if x[0] >= _slow_ms]
        out["slow2_n"] = len(_slow)
        out["slow_beat"] = (sum(g for g, *_r in fr) / len(_slow)) if _slow else 0.0
        out["slow_snd"] = sum(1 for x in _slow if x[3] > 0)
        out["slow_vib"] = sum(1 for x in _slow if x[4] > 0)
        # 全程总数 —— 用来**自证计数器在工作**: 面板上看到"全程发声 N 次"就知道探针没坏,
        # 否则"慢帧里 0 帧在发声"既可能是真的、也可能是计数器根本没跑(这个仓库栽过这种静默)。
        out["snd_n"] = sum(x[3] for x in fr)
        out["vib_n"] = sum(x[4] for x in fr)
        # 慢帧里有多少帧是**启动预热**在跑(第 7 个字段)。与"在发声/震动"同一把刀。
        out["slow_bake"] = sum(1 for x in _slow if (x[6] if len(x) > 6 else 0) > 0)
        # 发声耗时: 单次最慢 + 全程累计。**这是"那一声到底卡了多久"的直接证据** ——
        # 真机实测慢帧是纯等(119ms 只烧 10.4ms CPU), 所以要看的就是这个数:
        # 单次调用能到几十毫秒 ⇒ 卡的就是它。
        out["snd_worst"] = _SND_STAT[1] * 1000.0        # 全程单次最慢(毫秒)
        out["snd_sum"] = _SND_STAT[0] * 1000.0          # 全程累计(毫秒)
        out["snd_worst_name"] = _SND_STAT[2]
        out["snd_slow_n"] = int(_SND_STAT[3])
        out["vib_worst"] = _VIB_STAT[1] * 1000.0
        out["vib_sum"] = _VIB_STAT[0] * 1000.0
        out["vib_worst_name"] = _VIB_STAT[2]
        # 方向守卫 / 沉浸重申: 每 0.7 秒各一次, **跑分期间照跑**(2026-09-14 已搬出主线程)。
        # 它是"周期性停顿"里唯一的常驻项, 而 1%Low 只看最差的那十几帧 —— 每 0.7 秒来一记
        # 正好能把那一档占满。搬走之后要看的是**两档的对比**:
        #   主线程档 ≈ 0 且 工作线程档 接手  ⇒ 真搬走了(慢帧该跟着消失);
        #   主线程档仍然大                ⇒ 没投出去(队列建不起来 / 满了), 等于没改;
        #   失败次数 > 0                  ⇒ 守卫在真机上抛异常了(原来被 except 静默吞掉)。
        out["jni_n"] = _JNI_STAT[2]
        out["jni_main_worst"] = _JNI_STAT[1] * 1000.0
        out["jni_main_sum"] = _JNI_STAT[0] * 1000.0
        out["jni_bg_sum"] = _JNI_STAT[3] * 1000.0
        out["jni_bg_worst"] = _JNI_STAT[4] * 1000.0
        out["jni_err"] = _JNI_STAT[5]
        # UI 线程那档(沉浸重申本身)。**这是本面板唯一能看到"不在我们线程上"的开销的地方。**
        out["ui_sum"] = _JNI_STAT[6] * 1000.0
        out["ui_n"] = _JNI_STAT[7]
        out["ui_worst"] = _JNI_STAT[8] * 1000.0
        # 音频后端名 —— 这个仓库栽过一次: SoundPool 构造失败会**静默降级**到 Kivy-SoundLoader,
        # 而后者走 SDL_mixer, 阻塞行为完全不同。不知道后端就分不清是哪一个在卡。
        try:
            out["backend"] = str(getattr(self.sfx.out, "name", "?"))
        except Exception:
            out["backend"] = "?" 
        # 分场景: 飞行 vs 装杯(跑分把装杯也采样进去了, 不分就分不清是谁在拖)
        grp = {}
        for _x in fr:
            grp.setdefault(_x[1], []).append(_x[0])
        out["groups"] = {}
        for t, xs in grp.items():
            xs.sort()
            out["groups"][t] = (len(xs), xs[len(xs) // 2])
        # 每帧真正花在计算上的时间(全进程 CPU / 帧数)。面板的"瓶颈"判断读它:
        # 它离"帧间隔"越近, 越是处理器顶不住; 差得远就是大头在等画面。
        _cpu_tot = time.process_time() - getattr(self, "_bench_cpu0", 0.0)
        out["cpu_per_frame"] = _cpu_tot * 1000.0 / max(1, out["n"])
        # GC 停顿: 次数 / 总时长 / 最坏一次
        gcs = getattr(self, "_bench_gc", {}) or {}
        out["gc_n"] = sum(v[0] for v in gcs.values())
        out["gc_total"] = sum(v[1] for v in gcs.values())
        out["gc_worst"] = max((v[2] for v in gcs.values()), default=0.0)
        # 最坏那一次是**哪一代**的回收 —— gen-2(全量)在扫整个对象图, 量级与 gen-0 完全不同。
        # 这一栏是"GC 冻结有没有生效"的判据: 冻结之后 gen-2 该变成扫不到东西。
        out["gc_worst_gen"] = (max(gcs, key=lambda g: gcs[g][2]) if gcs else -1)
        out["gc_frozen"] = _GC_FROZEN[0]
        out["gc_frz_before"] = _GC_FROZEN[1]
        out["gc_frz_after"] = _GC_FROZEN[2]
        out["cfg_sum"] = _CFG_STAT[0] * 1000.0
        out["cfg_worst"] = _CFG_STAT[1] * 1000.0
        out["cfg_n"] = _CFG_STAT[2]
        return out

    def _wait_idle_then_bench(self, dt=0):
        """等球落地(主线程空闲)再启动物理 benchmark, 避免抢 CPU 干扰结果。"""
        if self.state == "ready":
            threading.Thread(target=self._run_benchmark, daemon=True).start()
        else:
            Clock.schedule_once(self._wait_idle_then_bench, 0.5)

    def _run_benchmark(self):
        """**两波**: 波 1 测性能(峰值, 带间隔) -> 波 2 测高压(衰减, 一秒不停)。

        ⚠️⚠️ **顺序不能反**: 波 2 会把这台机器烤热, 反过来的话波 1 就不再是"峰值"了 ——
           那是白测。玩家 2026-09-15 定案: 「我也想顺便看看高压下的性能, 所以应该是两波」。
        ⚠️ **两波各自采一遍 CPU 频率**(`_freq_sampler_start`), 分开存。
           为什么必须单独采: 日志里那行「CPU 频率(采样期)」采的是**渲染窗口**那二十几秒,
           与物理跑分**不是同一段时间** —— 拿它解释跑分的差异是**张冠李戴**。
           起因: 玩家实测**同一台设备**, 显示上限设成 60 与 120, 跑分差**接近 2 倍**。
           而这两个 benchmark 按构造与刷新率无关(工作线程 + `time.thread_time` 当分母 +
           先等主线程空闲) ⇒ 那 2 倍只能来自"同样的代码、不同的核心频率"(DVFS)。
           分成两波之后, "波 2 掉了 40%"能直接对上"频率掉了多少", 一眼分清是不是温控。
        ⚠️ 采样线程每 0.5 秒只读两次小文件, 对跑分的 GIL 干扰可忽略(原来是零采样)。
        """
        # ---- 波 1: 测性能(峰值) ----
        _f1, _s1 = _freq_sampler_start()
        self._phys_started = True

        def _on_sample(_i, _n):
            self._phys_done = _i

        flights, frames, fps_list, cpu_secs = benchmark_trajectories(on_sample=_on_sample)
        _s1[0] = True
        _f1.sort()
        self._phys_freq_p50 = _f1[len(_f1) // 2] if _f1 else 0
        self._phys_freq_n = len(_f1)
        self._phys_freq_min = _f1[0] if _f1 else 0
        self._phys_freq_max = _f1[-1] if _f1 else 0
        # ⚠️ **平均频率** —— 玩家 2026-09-15:「cpu频率不能用中位数」「必须用**跑分时**的平均
        #    频率(空闲的时候可以不计)」。所以: ①这一格是**均值不是中位**; ②采到的样本已经
        #    由 `_FREQ_GATE` 把**样本之间的 sleep 段**滤掉了(那几秒 CPU 闲, 会把均值拖低)。
        # ⚠️ `_phys_freq_p50/min/max/n` **一个都不删** —— JSON 里留着, 只是不再当"代表值"显示。
        self._phys_freq_mean = int(sum(_f1) / len(_f1)) if _f1 else 0
        # ⚠️ **主测试不再跑高压段**(2026-09-15 玩家定案: 高压拆成独立按钮)。
        #    这里必须**主动清空**, 否则上一次高压测试的 `_sust_fps` 会残留在内存里,
        #    面板和日志就会把**旧的高压结果**当成这一次的印出来 —— 那是"印假数", 比没有更糟。
        self._sust_fps = None
        self._sust_freq = None
        Clock.schedule_once(lambda dt: self._bench_done(flights, frames, fps_list, cpu_secs), 0)

    def _bench_save_board(self):
        """跑分开始前把盘面存一份(与"还"配对的另一半, 见 `_bench_restore_board`)。

        ⚠️ 存的是**每个列表的副本**(`list(v)`) —— `self.multipliers` 与 `self._boards[rtp]`
           是**同一个 list 对象**(见 `set_rtp`), 只存引用的话原地改动会连带污染存下来的那份。
        ⚠️ 抽成独立方法是为了**能被探针直接调** —— 跑分那整条链太长, 端到端测不起来
           (第一版探针就是因为存盘藏在 `_start_bench_test` 里, 只能验到"没存就没得还")。
        """
        try:
            self._bench_saved_boards = {r: list(v) for r, v in self._boards.items()}
        except Exception:
            self._bench_saved_boards = None

    def _bench_restore_board(self):
        """把跑分钉死的盘面还回去(存盘在 `_start_bench_test`)。

        ⚠️ 为什么必须还(2026-09-15 玩家报的 bug): `_auto_launch_tick` 每一发都把 9 个槽
           **全钉成 `BENCH_BOARD[i]` 那一个值**, 第 5 发是 `100`, 而且它**写回了缓存**
           (`self._boards[self.rtp_target] = ...`)。跑分结束时只还了随机数、没还盘面
           ⇒ **跑分一完, 下面 9 个倍率槽全是 `x100`**(玩家原话:「都是*100 这个明显不合理」),
           一直挂到下一次发射(`park_ball` 会重掷)才自愈 —— 中间那段时间看着就是坏的。
        ⚠️ 抽成独立方法是为了**能被探针直接调**(跑分那整条链太长, 端到端测不起来)。
        返回是否真的还了(没存盘 = 跑分没起来过 = 不用还)。
        """
        _sb = getattr(self, "_bench_saved_boards", None)
        if not _sb:
            return False
        self._bench_saved_boards = None
        self._boards = _sb
        _m = self._boards.get(self.rtp_target)
        if _m:
            self.multipliers = _m
        try:
            self.game_area._update_slots()      # 只刷 9 个槽, 不整块重画(与 `park_ball` 同一个写法)
        except Exception:
            pass
        return True

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
        """这个包是**什么时候做出来的** —— 长按标题那两个弹窗里显示一行。

        ⚠️ **版本号不在这儿**: 玩家 2026-09-11 定稿把它挪进了弹窗标题(见 `_startup_title`),
        同时要求「去掉其他地方的版本号」—— 这里只剩制作时刻。

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

    def _bench_done(self, flights, frames, fps_list, cpu_secs=None):
        self.game_area.hide_bench_badge()
        self._hide_bench_dim()   # 兼容旧路径：当前跑分不再置灰
        _phys_sorted = sorted(fps_list)
        phys_fps = _phys_sorted[len(_phys_sorted) // 2]   # 物理吞吐中位数
        phys_min = _phys_sorted[0] if _phys_sorted else 0.0
        phys_max = _phys_sorted[-1] if _phys_sorted else 0.0
        phys_spread = 100.0 * (phys_max - phys_min) / phys_fps if phys_fps > 0 else 0.0
        phys_runs = len(fps_list)
        # ---- 波 2(高压)的统计(见 `benchmark_sustained`) ----
        # ⚠️ 口径: 首窗 vs **末窗**(不是 vs 最低)—— 问的是"一直压着跑**最后**掉到哪",
        #    最低值单独印一格。两波各自的频率也分开存(见 `_run_benchmark`)。
        _sv = [x for x in (getattr(self, "_sust_fps", None) or []) if x > 0]
        if _sv:
            _s_first, _s_last, _s_min = _sv[0], _sv[-1], min(_sv)
            _s_decay = 100.0 * (_s_first - _s_last) / _s_first if _s_first > 0 else 0.0
        else:
            _s_first = _s_last = _s_min = _s_decay = 0.0
        avg_frames = frames / max(1, flights)
        cost_ms = avg_frames / phys_fps * 1000.0 if phys_fps > 0 else 0.0  # 每发纯物理耗时
        render_fps = getattr(self, "_render_fps", 0.0)
        render_median = getattr(self, "_render_median_fps", 0.0)
        render_1low = getattr(self, "_render_1low", 0.0)
        dev = self._device_info()
        # 跑分**那一段**的 CPU 频率(见 `_run_benchmark` 的说明: 与日志里那行"渲染采样期"
        # 的频率不是同一段时间, 不可混用)。
        self._bench_phys_freq = {
            "p50": int(getattr(self, "_phys_freq_p50", 0) or 0),
            "min": int(getattr(self, "_phys_freq_min", 0) or 0),
            "max": int(getattr(self, "_phys_freq_max", 0) or 0),
            "n": int(getattr(self, "_phys_freq_n", 0) or 0),
            # 平均 —— 玩家 2026-09-15 定案「cpu频率不能用中位数」; 上面那个 p50 留着做分布。
            "mean": int(getattr(self, "_phys_freq_mean", 0) or 0),
        }
        self._bench_phys_now = int(phys_fps)   # 给 `_bench_frame_log` 印那一行用
        # 存历史(最近100次)
        self.bench_history.append({
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "phys_fps": int(phys_fps),
            "avg_frames": int(avg_frames),
            "cost_ms": round(cost_ms, 1),
            "render_fps": round(render_fps, 1),
            "render_median": round(render_median, 1),
            "render_1low": round(render_1low, 1),
            "phys_runs": phys_runs,
            "phys_min": int(phys_min),
            "phys_max": int(phys_max),
            "phys_spread": round(phys_spread, 1),
            "phys_cpu_seconds": round(sum(cpu_secs or []), 3),
            # ⚠️ 跑分**那一段**自己的 CPU 频率(见 `_run_benchmark`)。留着它才能事后回答
            #    "两次跑分差这么多, 是不是频率不同"。
            # ⚠️ `phys_freq_mean` 是**代表值**(玩家 2026-09-15:「cpu频率不能用中位数」),
            #    而且是**跑分时**的平均(采样间隙已被 `_FREQ_GATE` 滤掉);
            #    `phys_freq_p50` 一并留着 —— 它只是**频率分布的一项**, 不再当代表值印。
            "phys_freq_mean": int(getattr(self, "_phys_freq_mean", 0) or 0),
            "phys_freq_p50": int(getattr(self, "_phys_freq_p50", 0) or 0),
            # 波 2(高压) —— 面板只印一行, JSON 里把逐窗值和它那段的频率全留着。
            "sust_sec": int(SOC_SUSTAIN_WALL_SEC),
            "sust_first": int(_s_first), "sust_last": int(_s_last),
            "sust_min": int(_s_min), "sust_decay_pct": round(_s_decay, 1),
            "sust_freq_p50": int(getattr(self, "_sust_freq_p50", 0) or 0),
            "sust_fps_windows": [int(x) for x in _sv],
            "version": _app_version(),
            "device": dev,
        })
        if len(self.bench_history) > 100:
            self.bench_history.pop(0)
        self._save_bench_history()
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='性能测试', bold=True,
                                         halign='center', color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        _lows = getattr(self, "_render_lows", {}) or {}
        _pct = getattr(self, "_render_pct", {}) or {}
        if _lows:
            # 排版(玩家 2026-09-13 定稿): 平均帧率**独占一行**, 其余四个两两一行。
            _low_txt = ('平均帧率： %.1f    中位帧率：%.1f\n'
                        '1%%Low：%.1f    10%%Low：%.1f\n'
                        'p99帧率：%.1f    p90帧率：%.1f') % (
                            render_fps, render_median, _lows.get(1, 0.0), _lows.get(10, 0.0),
                            _pct.get(99, 0.0), _pct.get(90, 0.0))
        else:
            _low_txt = '平均帧率： %.1f　中位帧率：%.1f\n1%%Low帧率：%.1f' % (
                render_fps, render_median, render_1low)
        # ⚠️ 这一屏**不放**版本/制作日期(用户 2026-09-11 定稿: "性能测试的成绩面板别加").
        # ⚠️ **2026-09-14 玩家改主意了**: 跑完分要能一眼看出"这是哪个版本的包跑出来的"
        #    (他手上有 PC 与两代 Y700 好几份成绩互相对照, 没版本号根本对不上号)。
        #    按他给的方案加在 **"安卓版本后面"**(不是标题后面), 和机器名排在一起 ——
        #    读成绩的人先看"哪台机器", 紧接着就是"哪个包"。
        #    成绩面板只放成绩; 版本/日期在长按标题的**菜单弹窗**里(见 _show_bench_menu)。
        # 排版(玩家 2026-09-13: "窗口高度增加一些 / 布局稍微美化下"):
        #   成绩块(亮色大字) → 细分隔线 → 诊断块(次级色、小一号)。
        #   两块都走 `_auto_h`(按真实排版撑高), 所以折行只会让弹窗长高, 不会把字裁掉 ——
        #   上一版是**一整块** label, 诊断四行挤进去之后底部被裁(玩家截图里 "分场景"
        #   那行折行、最后一行露出半个)。
        # 设备行 + 版本号(玩家 2026-09-14): "TB323FU / Android 16  v0.6.81"
        # ⚠️ 版本号拿不到时**不留空尾巴**(`_app_version()` 可能返回空串)。
        _ver = ""
        try:
            _ver = str(_app_version() or "")
        except Exception:
            _ver = ""
        # ⚠️ 玩家 2026-09-14: 「安卓版本和这个游戏版本也可以加一个符号 /」——
        #    原来是两个空格, 印出来是 `NCO-AL00 / Android 15  v0.7.37`, 版本号看着像
        #    系统版本的一部分。改成 `/` 分隔, 三段并列: `机器 / 安卓 / 游戏版本`。
        _dev_ver = (dev + " / " + _ver) if _ver and _ver not in dev else dev
        # ⚠️ 高压那行**没有数据就整行不印**(不印假数) —— 与窗口/门槛那两处的规矩一致。
        _s_txt = (('高压 %d 秒：首 %d → 末 %d 步/秒（降 %.0f%%）· 最低 %d\n'
                   % (int(SOC_SUSTAIN_WALL_SEC), int(_s_first), int(_s_last),
                      int(_s_decay), int(_s_min)))
                  if _sv else '')
        score = ('%s\n'
                 '运算速度：%d 轮中位每秒 %d 步模拟\n'
                 '稳定性：%d～%d 步/秒 · 波动 %.1f%%\n'
                 '%s'
                 '每次发射：需 %.0f 步模拟(用时 %.1f 毫秒)\n'
                 '%s') % (_dev_ver, phys_runs, int(phys_fps), int(phys_min), int(phys_max),
                            phys_spread, _s_txt, avg_frames, cost_ms, _low_txt)
        score_lbl = Label(text=score, font_size='17sp', halign='left', valign='top',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(130))
        self._auto_h(score_lbl, dp(130), dp(6))
        content.add_widget(score_lbl)

        # ⚠️⚠️ 2026-09-15(玩家定稿): **两个按钮都挪到面板底部, 左边「帧率曲线」右边「关闭」**,
        #    并且**这个面板只能靠「关闭」关掉**(`auto_dismiss=False`) —— 点面板外面不再关它。
        #    原来「帧率曲线」夹在成绩块和诊断块中间(整条通栏), 而**根本没有关闭按钮**,
        #    关闭全靠点外面 —— 玩家点空白处想滚动/误触就把成绩面板关掉了。
        # ⚠️ 横向 `BoxLayout` 里**先 add 的在左边**(Kivy 按 `reversed(children)` 摆位)。
        _btnrow = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10),
                            orientation='horizontal')
        curve_btn = Button(text='帧率曲线', font_size='16sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
        curve_btn.bind(on_release=lambda *_: self._show_fps_curve())
        close_btn = Button(text='关闭', font_size='16sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_BTN_OFF) + (1,))
        _btnrow.add_widget(curve_btn)
        _btnrow.add_widget(close_btn)

        _sep = Widget(size_hint_y=None, height=dp(1))
        with _sep.canvas.before:                      # 同 `_row_bg` 的写法
            Color(*hex_rgb(COL_DIV))
            _sep._line = Rectangle(pos=_sep.pos, size=_sep.size)
        _sep.bind(pos=lambda w, *_: setattr(w._line, "pos", w.pos),
                  size=lambda w, *_: setattr(w._line, "size", w.size))
        content.add_widget(_sep)

        diag_lbl = Label(text=self._bench_low_summary_text(), font_size='15sp',
                         halign='left', valign='top',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
        self._auto_h(diag_lbl, dp(0), dp(0))
        content.add_widget(diag_lbl)
        # ⚠️ **必须最后 add** —— 竖向 `BoxLayout` 按 add 的先后从上往下排, 先加的那批在上。
        #    第一版把这一行加在 `score_lbl` 后面(那是「帧率曲线」原来的位置), 结果两个按钮
        #    卡在成绩块和诊断块**中间**, 不叫"在界面底部"(截图为证)。
        content.add_widget(_btnrow)

        popup = self._popup(0.90, 460, title='', content=content,
                            auto_dismiss=False, separator_height=0)
        # ⚠️ 绑定必须在 `popup` 建出来之后(和隐藏档弹窗同一个写法)。
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)
        self._prog_stop()
        _set_label_text(self.status_lbl, getattr(self, '_bench_saved_status', '按住蓄力发射'))
        self._set_controls_enabled(True)
        # ⚠️ **盘面必须和随机数一起还**(2026-09-15 玩家报的 bug, 见 `_bench_restore_board`)。
        #    放在 `_bench_running = False` **之前** —— 之后 `park_ball` 就会重掷盘面,
        #    这里要还的是"跑分之前那份", 不是"新掷的一份"。
        self._bench_restore_board()
        self._bench_running = False
        self._bench_start = 0.0
        # 把随机**原样还回去**(见 `_start_bench_test`): 不还的话正常游戏的球路会被钉死,
        # 每局一模一样 —— 那是比"跑分不可比"严重得多的事故。
        try:
            if getattr(self, "_bench_rng_state", None) is not None:
                random.setstate(self._bench_rng_state)
                self._bench_rng_state = None
            _wf = getattr(self.game_area, "win_fx", None)
            if _wf is not None and getattr(self, "_bench_pile_rng", None) is not None:
                _wf._rng = self._bench_pile_rng
            self._bench_pile_rng = None
        except Exception:
            pass

    def _bench_frame_log(self):
        """把这一轮的**逐帧原始采样**拼成可复制的文本(帧率曲线弹窗的"复制"按钮用)。

        形状: 头部(机器/版本/关键数 + **各阶段统计表**) → 之后每行一帧 `帧间隔毫秒,阶段`。
        ⚠️ 头部刻意放最前面、且自带**各阶段慢帧率** —— 那是面板上原来印不出来、
        而定位瓶颈最需要的那个数(要除以各阶段自己的总帧数, 不能拿"最慢 1% 的总帧数"当分母)。
        这样**即使粘贴被截断**, 我要的结论仍然在开头, 不会因为尾巴丢了就白跑一趟。
        ⚠️ 阶段名取 `_bench_tag()` 的原值(中文), 不做缩写 —— 缩写表本身又是一份要维护的清单。
        """
        gaps = list(getattr(self, "_render_gaps_ms", []) or [])
        if not gaps:
            return ""
        tags = [x[1] for x in (getattr(self, "_bench_frames", []) or []) if len(x) > 1]
        if len(tags) != len(gaps):
            tags = ["?"] * len(gaps)          # 对不上就照发原始帧间隔, 不猜阶段
        tex = list(getattr(self, "_bench_tex", []) or [])
    
        if len(tex) != len(gaps):
            tex = [0] * len(gaps)
        _n1 = max(1, int(len(gaps) * 0.01))   # 最慢 1% 的帧数(只给 `_order[:3]` 取最慢三帧用)
        _order = sorted(range(len(gaps)), key=lambda _i: -gaps[_i])
        # 慢帧门槛 = 中位帧时间的 2 倍(= 中位帧率的 50%), 与 `_bench_diag` 的 `_slow_ms` **同口径**。
        # ⚠️ 这里原来写的是"最慢 1% 的分位临界值", 与 `_bench_diag` 的 `_slow_ms` 是**两套算法**,
        #    同一份日志里两处"慢帧"能差好几倍, 而面板与各阶段表读的又不是同一个 ⇒ 本次统一
        #    (2026-09-14 玩家定稿: 慢帧=中位帧 50%, 重卡=中位帧 70%, 一律不写死绝对值)。
        _p50 = sorted(gaps)[len(gaps) // 2]
        _thr = 2.0 * _p50
        # ⚠️ "慢帧"的判据**必须与下面各阶段表用同一条**: 都是 `>= 门槛`。
        #    写成 `set(_order[:_n1])` 的话, 正好卡在门槛上的并列帧会让两边算出**不同的集合**
        #    (实测 100 帧里 20 帧同值: 一边 1 帧、一边 20 帧), 于是"慢帧当帧有没有文字重建"
        #    和各阶段慢帧率互相打架。
        _slow_idx = set(_i for _i in range(len(gaps)) if gaps[_i] >= _thr)
        d = getattr(self, "_bench_diag", None) or {}
        _lines = ["# 跳跳的弹珠机 逐帧帧率日志"]
        try:
            _lines.append("# %s  %s" % (self._device_info(), str(_app_version() or "")))
        except Exception:
            pass
        try:
            _lines.append("# 窗口 %.2fs / %d 帧 / 平均 %.1f / 中位 %.1f / 1%%Low %.1f"
                          % (sum(gaps) / 1000.0, len(gaps),
                             float(getattr(self, "_render_fps", 0.0)),
                             float(getattr(self, "_render_median_fps", 0.0)),
                             float(getattr(self, "_render_1low", 0.0))))
            # ⚠️⚠️ **两套口径必须各自带名印出来**(2026-09-15, 对抗评审抓到的).
            #    这一行是【口径 A】: 帧间隔 ≥ 中位 × 2(等价于"中位帧率的 50%")。
            #    它与下面【口径 B】(中位帧率的 55%)**是两个不同的量**, 却**都叫 ★** ——
            #    实测第 2 轮 A=3 帧而 B=8 帧, 差 167%; 而**第 1 轮两者都给 1**,
            #    所以只看一轮永远发现不了"它们不是一个东西"。简报里就是这么混起来的。
            _lines.append("# ★口径A 长停顿「帧间隔 ≥ 中位×2」= 门槛 %.2f 毫秒(中位帧 %.2f 毫秒);"
                          " 命中 **%d** 帧   ← 这是「长停顿」口径"
                          % (_thr, _p50, len(_slow_idx)))
            # ⚠️ **物理跑分 + 它那一段自己的 CPU 频率**(2026-09-15 加)。
            #    起因: 玩家实测同一台设备在 60Hz 与 120Hz 下跑分差**接近 2 倍**, 而跑分本该
            #    只反映 CPU。跑分以前**只在面板和 JSON 里**, 日志里没有 ⇒ 没法把它和频率放在
            #    一起对。现在两行相邻印, 一眼就能看出"是不是频率不同造成的"。
            #    ⚠️ 这行里的频率是**跑分那 8.5 秒 CPU 时间**内的, 与下面「CPU 频率(采样期)」
            #       (渲染窗口那二十几秒)**不是同一段时间**, 别混用。
            _pf = getattr(self, "_bench_phys_freq", None)
            _pm = int(getattr(self, "_bench_phys_now", 0) or 0)
            if _pm > 0:
                # ⚠️ **代表值用平均, 不用中位**(玩家 2026-09-15:「cpu频率不能用中位数」)。
                #    而且这个平均是**跑分时**的 —— 样本之间的 sleep 段已被 `_FREQ_GATE` 滤掉。
                _pfm = int((_pf or {}).get("mean", 0) or 0) or int((_pf or {}).get("p50", 0) or 0)
                if _pf and _pfm:
                    _lines.append("# 物理跑分(中位 %d 步/秒) · **跑分那段的 CPU 平均频率**: %dMHz "
                                  "(最低 %d / 最高 %d, %d 个采样; 采样间隙已剔除)"
                                  % (_pm, _pfm, _pf["min"], _pf["max"], _pf["n"]))
                else:
                    _lines.append("# 物理跑分(中位 %d 步/秒) · 跑分那段的 CPU 平均频率: **没采到**"
                                  "(非安卓 / 读不到 sysfs)" % _pm)
            # ⚠️ **波 2(高压)那两行** —— 与波 1 相邻印,
            #    两波的频率**必须分开看**(它们是不同的两段时间)。
            _sv2 = [x for x in (getattr(self, "_sust_fps", None) or []) if x > 0]
            if _sv2:
                _d2 = 100.0 * (_sv2[0] - _sv2[-1]) / _sv2[0] if _sv2[0] > 0 else 0.0
                _sf2 = getattr(self, "_sust_freq", None) or {}
                if _sf2.get("p50"):
                    _f2t = ("高压那段的 CPU 频率: 中位 %dMHz "
                            "(最低 %d / 最高 %d, %d 个采样)"
                            % (_sf2["p50"], _sf2["min"], _sf2["max"], _sf2["n"]))
                else:
                    _f2t = "高压那段的 CPU 频率: **没采到**"
                _lines.append("# 高压 %d 秒(背靠背不停): 首 %d → 末 %d 步/秒"
                              "(降 %.0f%%) · 最低 %d"
                              % (int(SOC_SUSTAIN_WALL_SEC), int(_sv2[0]), int(_sv2[-1]),
                                 _d2, int(min(_sv2))))
                _lines.append("#   逐窗: " + ", ".join("%d" % x for x in _sv2))
                _lines.append("#   " + _f2t)
        except Exception:
            pass
        # ---- ★ 这一轮到底能不能和上一轮比(2026-09-14 加) ----
        # ⚠️ 加这一段的理由: 连续三轮的「低于 90fps 的帧数」是 10 / 10 / 9, 而
        #    **1%Low 是 92.4 / 92.2 / 88.4** —— 差的那 3.9 全来自"这轮抽到了大杯局"
        #    (装杯占比 25.2% → 30.6% → 38.2%), 不是代码变差。
        #    **装杯占比不印出来, 每一轮都不可比** —— 追噪声追三轮了。
        # ⚠️ 判据用「低于 90fps 的帧数」而不是 1%Low: 后者被配比带得晃。
        # ⚠️ **这一段不包在 `try` 里**(它自己在下面逐项兜底) —— 包了的话一旦出错就被
        #    静默吞掉, 而玩家看到的是"日志里少了这两行", 不报错也不提示。
        # 判据 = **中位帧率的 50%**(帧间隔 = 中位 / 0.5 = 中位的 2 倍)。与 70% 同源, 只是更严。
        # ⚠️ 玩家 2026-09-14 定的两档口径: **<50% 要尽量消除; 50%~70% 只作参考**。
        #    所以下面印**两行** —— 一行硬判据、一行参考带, 别把参考带的帧混进硬指标里。
        # ⚠️ 写法沿用既有约定:「中位帧×70%」= **中位帧率的 70%**, 阈值 = `p50 / 0.7`。
        _p50b = sorted(gaps)[len(gaps) // 2]
        _th90 = _p50b / JANK_RATE
        _below90 = [_i for _i in range(len(gaps)) if gaps[_i] > _th90]
        _th_ref = _p50b / SLOW_RATE
        _refband = [_i for _i in range(len(gaps)) if _th_ref < gaps[_i] <= _th90]
        # ⚠️ **就地取, 不引用上面的 `tex`/`tags`** —— 那两个列表在本函数里定义得**很晚**
        #    (在"各阶段统计表"那一段之后), 而这一段的插入点在头部 ⇒ 直接引用会 NameError。
        _tex_all = list(getattr(self, "_bench_tex", []) or [])
        _fr_all = list(getattr(self, "_bench_frames", []) or [])
        _tags_all = [x[1] for x in _fr_all if len(x) > 1]
        if len(_tags_all) != len(gaps):
            _tags_all = ["?"] * len(gaps)
        _b90_tex = sum(1 for _i in _below90 if _i < len(_tex_all) and _tex_all[_i] > 0)
        _b90_face = 0
        _b90_fit = 0
        for _i in _below90:
            try:
                _b = _fr_all[_i][9] if (0 <= _i < len(_fr_all) and len(_fr_all[_i]) > 9) else ()
                if any(_k == "板面" for _v, _k in (_b or ())):
                    _b90_face += 1
            except Exception:
                pass
            # 「带字号」的判据是**分解计数**不是子步骤名: v0.7.28 真机那两帧(15.5 / 50.7 毫秒)
            # 的 `_fit1` 都跑在**别的回调**里, 最大子步骤那一栏未必写着"字号" —— 但
            # `_FRAME_FIT[0] > 0` 一定为真。用计数判, 不会漏。
            try:
                _r = _fr_all[_i]
                if 0 <= _i < len(_fr_all) and len(_r) > 11 and _r[11][0] > 0:
                    _b90_fit += 1
            except Exception:
                pass
        # ⚠️ `1%%Low` 的双百分号**不能省**: 这一行是 `%` 格式化的, 写成 `1%Low` 会被当成
        #    格式符(`%L`), 报的是 "not enough arguments for format string" —— 报错信息
        #    指向 `%d` 的个数, 而**真正的原因在后面那个 `%`**。
        _lines.append("# ★口径B 低于「中位帧率 55%%」(%.1f fps = 帧间隔 ≥ %.3f 毫秒 = 中位×%.3f)"
                      " 的帧数: %d  ·  其中带文字重建 %d / 带板面 %d / 带字号 %d"
                      "   ← **跨版本比较用这一条**(口径A 是另一回事, 见上)"
                      % (1000.0 / _th90, _th90, (_th90 / _p50) if _p50 > 0 else 0.0,
                         len(_below90), _b90_tex, _b90_face, _b90_fit))
        # 参考带单独一行(玩家定稿: 50%~70% 只作参考, 不进硬指标)。
        _lines.append("# ★ 参考带「中位帧率 55%%~75%%」(%.1f~%.1f fps): %d 帧  ·  中位帧 %.2f 毫秒"
                      "   ← 这**不是**指标, 只用来看趋势(跟着上面的硬指标一起降才对)"
                      % (1000.0 / _th90, 1000.0 / _th_ref, len(_refband), _p50b))
        # ★ 成绩面板「慢帧（<75%）」那一档的**自证行**。
        # ⚠️ 为什么必须单独印: 面板读的是 `_bench_diag["slow_n"]`, 而这个数在收尾统计里
        #    算一遍、又有可能被后面的段落覆盖一次(v0.7.39 就踩了, 印出来恒等于卡顿帧)。
        #    印成 `N = 低于55% + 参考带` 之后, **下次读日志当场就能验** —— 对不上就是又脱钩了。
        _lines.append("# ★ 慢帧「中位帧率 <75%%」(%.1f fps): %d 帧 = 低于55%% %d + 参考带 %d"
                      "   ← 成绩面板那一档读的就是这个数(面板把门槛印成帧/秒)"
                      % (1000.0 / _th_ref, len(_below90) + len(_refband),
                         len(_below90), len(_refband)))
        # ★ **面板窗口 vs 日志窗口** 的自证行(2026-09-14 加)。
        # ⚠️ 为什么必须有: 面板读 `_bench_diag` 的 `n` / `win_ms`, 而日志头这两个数是
        #    从 `gaps` 现算的 —— **两边各算一遍**, 正是本工程反复栽的脱钩形状
        #    (v0.7.40 那个 `slow_n` 被覆盖的 bug 就是同一类)。印在同一行上,
        #    下次读日志当场就能验; 对不上就说明又脱钩了。
        _dn = int(d.get("n", -1) or -1)
        _dw = float(d.get("win_ms", -1.0) or -1.0)
        _lw = sum(gaps)
        _lines.append("# ★ 面板窗口 vs 日志窗口: %d 帧 / %.2fs  vs  %d 帧 / %.2fs  ==> %s"
                      % (_dn, _dw / 1000.0, len(gaps), _lw / 1000.0,
                         "一致" if (_dn == len(gaps) and abs(_dw - _lw) < 0.5)
                         else "**不一致, 面板与日志脱钩了**"))
        _cnt = {}
        for _t in _tags_all:
            _cnt[_t] = _cnt.get(_t, 0) + 1
        _lines.append("# ★ 内容配比(判断这轮和上一轮能不能比): "
                      + " · ".join("%s %.1f%%" % (_k, 100.0 * _v / max(1, len(_tags_all)))
                                   for _k, _v in sorted(_cnt.items(), key=lambda kv: -kv[1])))
        # ---- 最慢的几次「冷字号测量」----
        # ⚠️ 这一段的理由(2026-09-15): 逐帧那行只印得出「字号21.7(1次/1测)」——
        #    知道"一次冷测量烧了 21.7 毫秒", 但**不知道是哪个字号、哪个标签**。
        #    修法完全取决于这个(补预热表? 还是从标签那边修?), 我在这件事上已经猜错两次,
        #    所以把 (毫秒, 字号, bold, 标签) 原样印出来, 让下一份日志直接给答案。
        # ⚠️ 同样**不包在 try 里**: 包了出错就被静默吞掉, 玩家只看到"少了一行"。
        if _COLD_FS:
            # ⚠️ 字号印**原值 6 位**、并带上"离最近的同 bold 预热档差多少":
            #    差值 ~0 ⇒ 只是四舍五入对不上(预热 round 过); 差值大 ⇒ 基准不是同一个数。
            #    只印 4 位小数的话这两种情况长得一模一样 —— 那正是我上一轮读不出来的原因。
            _lines.append("# 最慢的冷字号测量(>%.0f 毫秒才算, 含「离最近预热档的差」): %s"
                          % (COLD_FS_MIN_MS,
                             " · ".join("%.1fms fs=%r bold=%d [%s] 基准%.4f 倍率%.4f "
                                        "最近预热档%r 差%.4f 预热时量过=%s"
                                        % (_c[0], _c[1], int(_c[2]), _c[3],
                                           (_c[7] if len(_c) > 7 else 0.0),
                                           ((_c[1] / _c[7]) if len(_c) > 7 and _c[7] > 0 else 0.0),
                                           _c[4], _c[5], "是" if _c[6] else "**否**")
                                        for _c in _COLD_FS)))
        else:
            _lines.append("# 冷字号测量: **一次都没有**(全部命中了预热表)")
        # ⚠️ 预热项数必须**明着印出来**: Kivy 的 SDL2 字体缓存上限是 **64**(桌面实测),
        #    超了就是"预热自己把自己挤掉"—— 表里有、也量过, 运行期还是冷, 而且**不报错**。
        # ⚠️ `or ()` 不能省: 这两个表的初始值是 `None`(预热链还没跑到就算不出来),
        #    直接 `len()` 会 TypeError —— 而外层 `_copy_bench_log` 会把异常吞成**空串**,
        #    玩家看到的是"没有可复制的数据"。门禁的夹具(不跑预热直接出日志)当场就把它逮住了。
        # ---- 采样期屏幕刷新率**变过没有**(见 `_BENCH_HZ` 处说明) ----
        # ⚠️ 这一段存在的理由: 玩家报「**发射的时候必然会降低帧率上限**」。
        #    屏幕上真降频的话, 那一段的帧间隔会**自然变长** —— 既可能被误判成"卡顿",
        #    又会把"中位帧间隔"(新判据的分母)往大推。**先把事实印出来再谈归因。**
        if _BENCH_HZ:
            _hz_vals = sorted({_k[1] for _k in _BENCH_HZ})
            _by = {}
            for (_st, _h), _c in _BENCH_HZ.items():
                _by.setdefault(_h, {})
                _by[_h][_st] = _by[_h].get(_st, 0) + _c
            if len(_hz_vals) <= 1:
                _lines.append("# 屏幕刷新率(采样期, 每 0.5 秒记一次): 恒为 %.1f Hz —— "
                              "**没变过**, 长帧与降频无关" % _hz_vals[0])
            else:
                _lines.append("# ⚠️ 屏幕刷新率(采样期)**变过**: %s"
                              % " · ".join(
                                  "%.1fHz{%s}" % (_h, " ".join(
                                      "%s %d" % (_st, _c) for _st, _c in
                                      sorted(_by[_h].items(), key=lambda kv: -kv[1])))
                                  for _h in _hz_vals))
                _lines.append("#   ← 降频那一段的帧间隔会**自然变长**(60Hz=16.7ms / 120Hz=8.3ms): "
                              "既可能被误判成卡顿, 也会把「中位帧间隔」(判据的分母)往大推")
        else:
            # ⚠️ 采不到也要**明说**。本工程反复踩过"没印"被读成"没发生" —— 屏幕上没有这一行,
            #    读数的人分不清是"刷新率没变"还是"这一栏根本没在跑"。
            _lines.append("# 屏幕刷新率(采样期): **采不到**(桌面预览 / 无权限 / `_screen_hz()` 返回 0)")
        _wc = len(_FONT_WARM_HUD or ()) + len(_FONT_WARM_SIZES or ())
        # ---- 字体缓存账本(见 `_FS_OPEN`): 回答"冷字号到底是被谁挤掉的" ----
        # ⚠️ 判据不靠猜: **一次冷测量 = 真的开了一个 fontid**。
        #    不同字号数 > 64 ⇒ 淘汰是铁的 —— "预热时量过=是 却仍冷" 就是这么来的。
        #    再看"这次冷开之前最近开过哪些", 就知道是谁挤的(实锤: 「帧率曲线」弹窗的标题)。
        try:
            _nopen = len(_FS_OPEN_SET)
            # ⚠️⚠️ **判据看的是"实际开过几个", 不是"我烘了几项"**(2026-09-15, 对抗评审)。
            #    原来这里拿 `_wc`(=预热表项数 52) 去比 64 ⇒ 恒印「有余量」, 而**紧跟着的下一行**
            #    印的是"开过 86 个 ⇒ 必然发生过淘汰" —— **同一份日志里两行结论相反**, 而且
            #    乐观的那一行在读者的视线更前面。病灶不是"没有警报", 是判据量错了对象:
            #    占住 64 个槽位的是**实际开过的 fontid 数**, 预热表只是其中一部分。
            # ⚠️ **判据那行必须排在预热项数那一行之前** —— 先看到的要是真正会溢出缓存的那个数。
            _lines.append("# 字号缓存判据: 实际开过 **%d** 个不同字号"
                          " (Kivy 字体缓存上限约 64)  %s"
                          % (_nopen,
                             "**超了 —— 必然发生淘汰, 必须减总数**" if _nopen > 60
                             else ("贴着上限, 别再往上加" if _nopen >= 56 else "有余量")))
            _lines.append("# 字号预热表: %d 项 —— ⚠️ **这一行只说明「我烘了几项」, 不是判据**;"
                          " 挤爆缓存的是上面「实际开过」那个数" % _wc)
            if _FS_MUTED_N[0]:
                # ⚠️ 静音了多少次**必须印** —— 否则"账本变小了"会被读成"真的少开了"。
                _lines.append("#   （其中**报告 UI 期间静音跳过 %d 次** —— 打开「帧率曲线」弹窗"
                              "本身会开 12~13 个 fontid, 那是测量动作污染被测对象, 已剔除）"
                              % _FS_MUTED_N[0])
            _lines.append("# 字体缓存账本: 到现在一共开过 **%d** 个不同字号 (上限约 64) %s"
                          % (_nopen,
                             "**必然发生过 LRU 淘汰**" if _nopen > 60
                             else "还没撑爆"))
            # ---- 两个**普查**计数器(2026-09-15 加) ----
            # 「每个 fontid 冷开了几次」—— 定"该钉几个"的唯一依据(原来只有 `_COLD_FS` 的
            # 最慢 5 条, 那是采样)。按次数降序, 只印前 12 个。
            if _FS_COLD_CNT:
                _top = sorted(_FS_COLD_CNT.items(), key=lambda kv: -kv[1][0])[:12]
                _lines.append("# 冷开次数普查: 共 %d 个不同字号被冷开过, 总计 %d 次 ·"
                              " **复冷过的 %d 个**"
                              % (len(_FS_COLD_CNT),
                                 sum(v[0] for v in _FS_COLD_CNT.values()),
                                 sum(1 for v in _FS_COLD_CNT.values() if v[0] > 1)))
                _lines.append("#   前 12 名(字号/bold → 次数[最后调用方]): "
                              + " · ".join("%.2f%s x%d[%s]"
                                           % (k[0], "B" if k[1] else "", v[0], str(v[1])[:8])
                                           for k, v in _top))
            else:
                _lines.append("# 冷开次数普查: **一次冷开都没有**(全部命中预热表)")
            # ---- 字体"钉子"(见 `_pin_fontid`) ----------------------------------
            # ⚠️ **必须印** —— 钉子是"静默失效"的重灾区: 定位失败 / 断言不通过 / 超上限,
            #    三种情况下它都只是**不生效**, 而日志一片安静, 看起来和"钉住了所以没问题"
            #    一模一样。这里把"定没定到、钉了几个、失败几次、复冷几次"全摊开。
            _ps = _PIN_STAT
            _lines.append("# 字体钉子: 队列定位=%s · 钉住 **%d** 个(上限 %d) · "
                          "失败 %d 次 · 触发(复冷) %d 次"
                          % (_PIN_WHERE[0], _ps[0], _PIN_MAX, _ps[1], _ps[2]))
            if _PIN_ORDER[0] is None:
                _lines.append("#   ⚠️ **钉子没生效** —— 没在对象图里认出 Kivy 的淘汰队列"
                              "(`sdl2_cache_order`)。冷字号会照旧被驱逐, 这是**已知的失效**,"
                              " 不是「没问题」。")
            elif _PIN_CAND[0] > 1:
                _lines.append("#   ⚠️ 形状命中的 list 有 **%d 个** —— 认的是先扫到的那个, **可能认错**。"
                              " 若同时看到「钉住 0 个」, 那就是认错了(不是「本轮没有复冷」)。"
                              % _PIN_CAND[0])
            elif _ps[0] == 0 and _ps[2] == 0:
                _lines.append("#   本轮**一次复冷都没有** ⇒ 没有可钉的对象(这是好事, 不是失效)。")
            # 「本帧冷开几次」的分布 —— 回答"一帧到底会不会开十几个"。
            # ⚠️ 这是 `(N次/M测)` 的**普查版**: 那一栏只在「字号」挤进该帧最大两个子步骤时
            #    才印(21059 帧里只印过 2 帧), 拿它当普查是采样当普查 —— 对抗评审抓到过。
            if _FIT_HIST:
                _tot = sum(_FIT_HIST.values())
                _lines.append("# 帧内冷开分布: " + " · ".join(
                    "%d次 %d帧(%.2f%%)" % (_k, _v, 100.0 * _v / max(1, _tot))
                    for _k, _v in sorted(_FIT_HIST.items())))
            if _FS_OPEN:
                _lines.append("#   最近开过的 12 个(字号[调用方]): "
                              + " · ".join("%.2f[%s]" % (_x[1], str(_x[3])[:10])
                                             for _x in _FS_OPEN[-12:]))
            for _c in _COLD_FS[:2]:
                try:
                    _li = int(_c[8])
                    _before = [x for x in _FS_OPEN if x[0] < _li][-8:]
                    _n_at = len(set((round(x[1], 4), x[2]) for x in _FS_OPEN[:_li + 1]))
                    _lines.append("#   冷字号 fs=%.4f[%s]: 发生时已开过 %d 个不同字号"
                                  % (_c[1], str(_c[3])[:10], _n_at))
                    _lines.append("#     它之前最近开的 8 个: "
                                  + (" · ".join("%.2f[%s]" % (_x[1], str(_x[3])[:10])
                                                   for _x in _before) or "无"))
                except Exception:
                    pass
        except Exception:
            pass
        # ⚠️ 顺带把这两句读法写进日志 —— 下一份日志不用再回来翻源码就知道怎么读。
        _lines.append("#   读法2: 「倍率」若落在 FIT_SCALES(1.0/0.94/0.88/0.82/0.76/0.70) "
                      "之外, 那就是 v0.7.32 为了压到 64 上限以内**故意不烘**的 FIT_FINE 档 —— "
                      "**这是「要么超上限自己挤自己、要么多付一次冷开」的硬取舍, 不是漏烘。**")
        # ⚠️ 2026-09-14 修: 「基准 / 倍率」两栏以前只在 `_fit1` 路径上有值, **直接调
        #    `fit_font_size` 的路径记的是上一次 `_fit1` 的残留** ⇒ 假数(740 日志里四条
        #    FINE 冷字号都写「基准57.2375」, 而用 sp(36) 的只有"未中大字"、它走不到 FINE)。
        #    现在由 `_fit_font_size_slow` 现传真值, 并加了**文本前 10 个字**。
        #    **看这一行时以文本为准。**
        _lines.append("#   读法: 「预热时量过=是」而仍然是冷 ⇒ **被 Kivy 的字体缓存挤掉了**"
                      "(warm 再多也没用, 要减字号总数); 「=否」⇒ 预热那一句被 `text_px` "
                      "自己的缓存挡掉了, 等于没烘。最近预热档的**差**若为 0 就排除"
                      "「四舍五入/基准不同」这两种解释。")

        # ⚠️ **节拍真值必须印在最前面**(2026-09-14 加)。理由见 `_bench_collect_diag` 里
        #    "节拍真值"那段: 屏幕档位一变, 同一份代码的 1%Low 能从 83.3 掉到 42.2。
        #    这一行是"这份日志能不能和上一份比"的唯一前提 —— 放在头部第一眼就能看到。
        try:
            _lines.append("# 节拍: 屏幕 %.1fHz · 请求高刷模式 %.1fHz · Kivy上限 maxfps=%s"
                          " (Clock._max_fps=%.1f) · vsync=%s · multisamples=%s"
                          % (float(d.get("screen_hz", 0.0)), float(d.get("req_hz", 0.0)),
                             str(d.get("maxfps", "?")), float(d.get("clock_maxfps", 0.0)),
                             str(d.get("vsync", "?")), str(d.get("multisamples", "?"))))
        except Exception:
            pass
        # ---- 等屏幕(swap 阻塞) ----
        # ⚠️ 这一块**必须排在 `_fr` 定义之后**(它在下面"最慢三帧的子步骤"那段才被取出来)。
        #    第一版写在头部"节拍"那行后面, 直接 NameError —— 而 `_bench_frame_log` 的调用方
        #    `_copy_bench_log` 把异常吞成空串, 玩家看到的是"没有可复制的数据"。已挪到下面。
        # ⚠️ **慢帧 × 文字纹理重建的交叉表** —— 这一行是"文字重排到底是不是元凶"的直接读数。
        #    `Label.texture_update` 由 Kivy 用 Clock **延后**执行(重测字形+重光栅化+重建纹理+
        #    上传), 跑在 `_frame` 外面; 所以这笔账落在"被还上"的那一帧, 而不是"改 text"的那一帧。
        #    两边比例一摆就见分晓: 慢帧里占一大半、其余帧里几乎为零 ⇒ 就是它。
        _s_n = len(_slow_idx)
        _o_n = len(gaps) - _s_n
        _s_t = sum(1 for _i in _slow_idx if tex[_i] > 0)
        _o_t = sum(1 for _i in range(len(gaps)) if _i not in _slow_idx and tex[_i] > 0)
        # ⚠️ 分母必须是**慢帧集合的真实大小**(`_s_n`), 不是 `_n1` —— 门槛上并列时
        #    `>= 门槛` 选出来的帧会比 `_n1` 多(夹具实测 20 vs 1, 印出 `20/1 (2000%)`)。
        #    真机上毫秒值是浮点、几乎不会并列, 两者相等; 但判据必须自洽。
        _lines.append("# 慢帧当帧发生文字重建: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                      % (_s_t, _s_n, 100.0 * _s_t / max(1, _s_n),
                         _o_t, _o_n, 100.0 * _o_t / max(1, _o_n)))
        _lines.append("# 全程文字重建 %d 次(其中 %d 次落在慢帧上)"
                      % (sum(tex), sum(tex[_i] for _i in _slow_idx)))
        # ⚠️ **文字纹理缓存**的命中率。这一行是"缓存到底有没有生效"的唯一判据:
        #    命中 = 第一趟(量宽高)、第二趟(`填纹` 真光栅化)和纹理上传**三样全跳过**。
        #    ⚠️ 命中率若是 0, 说明指纹每次都不一样(最常见的原因是 `text_size` 在变,
        #       而它被 `_mk_label` 绑在 `size` 上) —— 那就要回去看 `_texex_key` 的字段表,
        #       别以为"加了缓存"就万事大吉。
        try:
            _th = int(d.get("texex_hit", 0) or 0)
            _tm = int(d.get("texex_miss", 0) or 0)
            if not _TEXEX_ON:
                # ⚠️ 必须明说"本版关着" —— 否则那行会印成"命中率 0%", 看着像缓存失效,
                #    而实际是根本没开。**一个会骗人的面板比没有面板更坏。**
                _lines.append("# 文字纹理缓存: **本版关闭**(见 `_TEXEX_ON` 处的证据: 逐像素比对"
                              "验出命中内容张冠李戴, 已停用)")
            else:
                _lines.append("# 文字纹理缓存: 命中 %d 次 · 未命中 %d 次(命中率 %.0f%%)"
                              "  ← 命中 = 两趟渲染 + 纹理上传全部跳过"
                              % (_th, _tm, 100.0 * _th / max(1, _th + _tm)))
        except Exception:
            pass
        try:
            _by = d.get("texupd_by") or []
            if _by:
                # ⚠️ 采集端只留前 4 名, 所以这里**必须把"其余"和"合计"一起印出来** ——
                #    否则读者拿这行去对上面的"全程文字重建 N 次"会永远差一截, 而不知道该信谁。
                #    真机 v0.7.23 就是这么差了 8 次(76 vs 68)且**没有任何提示**。
                _rest = int(d.get("texupd_rest", 0) or 0)
                _bits = ["%s %d" % (n, c) for n, c in _by]
                if _rest > 0:
                    _bits.append("其余%d类合计 %d" % (max(1, int(d.get("texupd_tags", 0) or 0)
                                                          - len(_by)), _rest))
                _lines.append("# 重建来源: " + " · ".join(_bits)
                              + "  [合计 %d]" % (sum(int(c) for _, c in _by) + _rest))
            # 发声 / 震动单次最慢 —— 覆盖另一条假设("关掉音效 1%low 就回升", 而关音效会
            # 连震动一起关掉, 两条分不开)。这两组埋点一直在跑, 只是很多年没显示了。
            _lines.append("# 发声 %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒"
                          % (int(d.get("snd_n", 0)), float(d.get("snd_worst", 0.0)),
                             float(d.get("snd_sum", 0.0))))
            _lines.append("# 震动 %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒"
                          % (int(d.get("vib_n", 0)), float(d.get("vib_worst", 0.0)),
                             float(d.get("vib_sum", 0.0))))
        except Exception:
            pass
        _lines.append("# 各阶段: 帧数 / 占总帧 / 中位ms / p99ms / 长停顿数 / 该阶段长停顿率")
        for _s in STAGE_ORDER:
            _v = [gaps[_i] for _i in range(len(gaps)) if tags[_i] == _s]
            if not _v:
                continue
            _vs = sorted(_v)
            _slow = sum(1 for _x in _v if _x >= _thr)
            _lines.append("#   %s %d / %.1f%% / %.2f / %.2f / %d / %.2f%%"
                          % (_s, len(_v), 100.0 * len(_v) / len(gaps),
                             _vs[len(_vs) // 2],
                             _vs[min(len(_vs) - 1, int(len(_vs) * 0.99))],
                             _slow, 100.0 * _slow / len(_v)))
        # 最慢三帧的 `_frame` **子步骤** —— 用来回答"这 20 毫秒里我们自己的代码占多少"。
        # ⚠️ 只统计 `_frame` **内部**被 `_brk_wrap` 包过的那几处(板面/重掷/字号/装杯/发射)。
        #    这一格很小而整帧很大 ⇒ 钱花在 `_frame` 外面(Kivy 延后的文字重排 / 渲染 / 其它
        #    Clock 回调), 不是我们的代码 —— 这是分流的那一刀。
        _fr = list(getattr(self, "_bench_frames", []) or [])
        # ---- 等屏幕(swap 阻塞) ----
        # ⚠️ 这一格回答的是"主线程没烧 CPU 的那些慢帧, 到底在等谁"。见 `_FRAME_SWAP` 处的说明:
        #    Kivy 的绑定回调 `on_flip` 跑在真正 swap **之前**(桌面实测序列恒为 `CB SWAP`),
        #    所以帧间隔里那一段"等屏幕"以前只混在总数里, 从没被单独量过。
        #    `time.thread_time()` 也看不见它 —— 阻塞在驱动 fence 上**不算 CPU 时间**。
        # ⚠️ 必须排在 `_fr` 之后: 第一版写在头部"节拍"那行旁边, 直接 NameError, 而调用方
        #    `_copy_bench_log` 会把异常吞成空串 ⇒ 玩家看到的是"没有可复制的数据"。
        _sw = [float(_r[10]) if len(_r) > 10 else 0.0 for _r in _fr] if _fr else []
        if len(_sw) == len(gaps) and any(_sw):
            _sw_s = sorted(_sw)
            _s_sw = sorted(_sw[_i] for _i in _slow_idx)
            _o_sw = sorted(_sw[_i] for _i in range(len(gaps)) if _i not in _slow_idx)
            _lines.append("# 等屏幕(swap 阻塞): 中位 %.2f 毫秒 · 最慢 %.2f 毫秒 · 累计 %.0f 毫秒"
                          " (占窗口 %.1f%%)"
                          % (_sw_s[len(_sw_s) // 2], _sw_s[-1], sum(_sw),
                             100.0 * sum(_sw) / max(1.0, sum(gaps))))
            # ⚠️ 判据**两个方向都要写**, 不能只往"在等屏幕"引 —— 桌面实测就撞到过反例:
            #    桌面 vsync 下大部分帧 swap 只要 0.3 毫秒, 但**排到队的那一帧要等 15.9 毫秒**;
            #    那一帧"等屏幕高"是因为它**本来就来晚了、撞上了队列**, 不是被屏幕拖慢的。
            #    所以这一格必须和上面那句「慢帧 CPU 账」**一起读**:
            #      慢帧 swap 高 **且** 主线程 CPU 也高  ⇒ ② 我们自己交晚了, 该去砍 CPU;
            #      慢帧 swap 高 **但** 主线程 CPU 很低  ⇒ ① 在等屏幕, 优化代码没用。
            _lines.append("#   慢帧当帧 等屏幕 中位 %.2f 毫秒  ·  其余帧 中位 %.2f 毫秒"
                          "  ← 必须配合上面的「慢帧 CPU 账」读: CPU 也高=我们交晚了;"
                          " CPU 低=真在等屏幕"
                          % (_s_sw[len(_s_sw) // 2] if _s_sw else 0.0,
                             _o_sw[len(_o_sw) // 2] if _o_sw else 0.0))
        if len(_fr) == len(gaps):
            _bits = []
            for _i in _order[:3]:
                # ⚠️ 必须兜底: `[9]` 是 `_on_flip` 存的 `((毫秒, 标签), ...)`, 但**形状意外时
                #    绝不能让整份日志消失**(外层 `_copy_bench_log` 会把异常吞成空串, 玩家只看到
                #    "没有可复制的数据")。实测: 只要 `[9]` 是一个扁平的 `(1.3, "发射")`,
                #    `for v, k in ...` 就 TypeError。
                _b = _fr[_i][9] if len(_fr[_i]) > 9 else ()
                # 「字号」那一格**必须带上分解**, 否则只看到一个 50.7 毫秒的数字,
                # 分不出"一次走完阶梯+二分(每回冷字号)"还是"一帧里十个标签同时换字" ——
                # 那两件事的修法完全相反(见 `_FRAME_FIT`)。见 [11] 的注释。
                _fn = _fr[_i][11] if len(_fr[_i]) > 11 else (0, 0)
                try:
                    _parts = []
                    for _v, _k in (_b or ()):
                        if _k == "字号":
                            _parts.append("字号%.1f(%d次/%d测)" % (_v, _fn[0], _fn[1]))
                        else:
                            _parts.append("%s%.1f" % (_k, _v))
                    _s = " / ".join(_parts) or "无"
                except Exception:
                    _s = "无"
                _bits.append("帧%d %.1fms[%s]" % (_i, gaps[_i], _s))
            _lines.append("# 最慢三帧的子步骤(仅 _frame 内部): " + " · ".join(_bits))
        # ---- 逐帧 CPU 账: 把"慢"分流成 我们的代码 / Kivy / 在等 ----
        # 这三样**每一帧本来就在采**(`_on_flip` 往 `_bench_frames` 里存的第 6/8/10 个字段),
        # 只是以前没印。真机 v0.7.18 的日志里有一批 11~12ms 的慢帧**文字重建为 0**
        # (中位帧 8.29 的 1.35 倍 = 多干了约 3ms 的活), 靠"有没有重建"解释不了它们,
        # 必须靠这三个数分流: 自算大=我们的代码; 主线程大而自算小=Kivy 渲染/延后重排;
        # 两个都小=在等(GC/显卡/驱动)。⚠️ 桌面 `thread_time` 精度只有 15.6ms, 这台机器上
        # 主线程那一列基本是台阶 —— 分流**只能在真机上看**。
        _self_ms, _thr_ms, _top1, _snd_n, _vib_n = [], [], [], [], []
        for _i in range(len(gaps)):
            _rec = _fr[_i] if _i < len(_fr) else ()
            _self_ms.append(float(_rec[5]) if len(_rec) > 5 else 0.0)
            _thr_ms.append(float(_rec[7]) if len(_rec) > 7 else 0.0)
            # 发声/震动是**逐帧**计的(`_on_flip` 的第 4/5 个字段), 而且发声那个计数在
            # **节流闸门之后** —— 数的是"真的播了", 不是"想播"。所以它能直接回答
            # 玩家问的那句「卡的那一下是不是正在响/正在震」。
            _snd_n.append(int(_rec[3]) if len(_rec) > 3 else 0)
            _vib_n.append(int(_rec[4]) if len(_rec) > 4 else 0)
            _t1 = ""
            try:
                _b = _rec[9] if len(_rec) > 9 else ()
                if _b:
                    _v, _k = max((v, k) for v, k in _b)
                    _t1 = "%s%.1f" % (_k, _v)
            except Exception:
                _t1 = ""
            _top1.append(_t1)
        # 发声/震动与慢帧的相关性 —— 与上面"文字重建"那一条同一个形状, 便于横向比。
        # ⚠️ 判据要**两边都印**(慢帧 vs 其余帧): 只印"慢帧里 40% 在发声"读不出结论,
        #    因为发声本来就密(4 次/秒), 得看它是不是**超配**。
        _s_n = len(_slow_idx)
        _o_n2 = len(gaps) - _s_n
        for _nm, _arr in (("发声", _snd_n), ("震动", _vib_n)):
            _s_hit = sum(1 for _i in _slow_idx if _arr[_i] > 0)
            _o_hit = sum(1 for _i in range(len(gaps))
                         if _i not in _slow_idx and _arr[_i] > 0)
            _lines.append("# 慢帧当帧在%s: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                          % (_nm, _s_hit, _s_n, 100.0 * _s_hit / max(1, _s_n),
                             _o_hit, _o_n2, 100.0 * _o_hit / max(1, _o_n2)))
            # ⚠️ **滞后窗口**那一版也必须印(玩家 2026-09-15 提的假设需要它):
            #    发声/震动的计数记在**投递那一刻**, 真正的 JNI 调用发生在那之后 —— 而 JNI 会在
            #    JVM 里分配对象, JVM 的 GC 是**停全世界**的(把主线程一起按住)。
            #    若卡是这么来的, 它会出现在发声的**下一两帧**, 而不是当帧 —— 只看当帧必然漏掉。
            _lag = 3
            _s_lag = sum(1 for _i in _slow_idx
                         if any(_arr[_j] > 0 for _j in range(max(0, _i - _lag), _i)))
            _o_lag = sum(1 for _i in range(len(gaps))
                         if _i not in _slow_idx and _arr[_i] == 0
                         and any(_arr[_j] > 0 for _j in range(max(0, _i - _lag), _i)))
            _o_lag_n = sum(1 for _i in range(len(gaps)) if _i not in _slow_idx and _arr[_i] == 0)
            _lines.append("# 慢帧**前%d帧内**有过%s: %d/%d (%.0f%%)  ·  其余帧: %d/%d (%.0f%%)"
                          % (_lag, _nm, _s_lag, _s_n, 100.0 * _s_lag / max(1, _s_n),
                             _o_lag, _o_lag_n, 100.0 * _o_lag / max(1, _o_lag_n)))
        # ⚠️ 必须带 `_slow_idx` 非空判断: 门槛改成"中位帧×2"之后, 帧时间分布集中时
        #    **可能一帧都不命中** —— 旧门槛"最慢1%"天然保证至少命中 1 帧(_n1 >= 1),
        #    这个隐含保障被改掉了 ⇒ `_sg` 为空 ⇒ 下面 `_sg[len(_sg)//2]` IndexError。
        #    (2026-09-14 改门槛时, `fx_probe` 的 CPU 账夹具当场把它抓红。)
        if _slow_idx and (any(_self_ms) or any(_thr_ms)):
            _sg = sorted(gaps[_i] for _i in _slow_idx)
            _ss = sorted(_self_ms[_i] for _i in _slow_idx)
            _st = sorted(_thr_ms[_i] for _i in _slow_idx)
            _gm = _sg[len(_sg) // 2]
            _sm = _ss[len(_ss) // 2]
            _tm = _st[len(_st) // 2]
            _lines.append("# 慢帧 CPU 账(中位): 帧间隔 %.2f / `_frame` 自算 %.2f / 主线程 %.2f 毫秒"
                          % (_gm, _sm, _tm))
            # ⚠️ 判据写死在这里, 免得看日志的人各读各的: 自算>=1ms 就是"我们自己的代码在吃"
            #    (1ms 在 8~12ms 的帧上是 8~12%, 已远超"顺带做一点"的量级)。
            # ⚠️ 判据按**主线程占帧长的比例**分, 不是"自算是否 >=1ms"。
            #    第一版用的就是后者, 结果把 17 个"主线程只跑了 1.9 毫秒、帧却走了 11 毫秒"的帧
            #    也算进了"`_frame` 外面", 读起来像"钱花在 Kivy 上" —— 而那些帧**主线程
            #    大部分时间根本没在跑**(在等节拍/合成器/调度)。两者要修的东西完全不同。
            _n_wait = sum(1 for _i in _slow_idx if _thr_ms[_i] < 0.30 * gaps[_i])
            _n_half = sum(1 for _i in _slow_idx
                          if 0.30 * gaps[_i] <= _thr_ms[_i] < 0.70 * gaps[_i])
            _n_busy = len(_slow_idx) - _n_wait - _n_half
            _lines.append("#   主线程没在跑(<30%%帧长) %d 帧 = 帧在等, 不是算出来的 · 半跑半等 %d 帧"
                          " · 一直在算(>=70%%) %d 帧" % (_n_wait, _n_half, _n_busy))
            _all_ratio = sorted(_thr_ms[_i] / max(0.01, gaps[_i]) for _i in range(len(gaps)))
            _br_ = _all_ratio[len(_all_ratio) // 2] if _all_ratio else 0.0
            _lines.append("#   常态帧主线程只占帧长 %.0f%%(拿它当尺子): 慢帧明显低于这个 = 在等;"
                          " 明显高于 = 真在算" % (100.0 * _br_))
        # ---- CPU 调频状态 ----
        # ⚠️ 这一行是上面**所有** CPU 数字的前提。玩家在跑分时用第三方工具看到: 采样窗口前期
        #    CPU 只跑 1.1GHz, 到后期纯 CPU 的物理跑分才升到 4.5GHz。而"主线程ms"量的是
        #    `time.thread_time()` = **真实 CPU 秒数**, 主频差 4 倍 ⇒ 同一个函数量出来差 4 倍。
        #    所以读数之前必须先看这一行: 它决定了这一轮的数字是"1.1G 下的"还是"4.5G 下的"。
        try:
            _fn = int(d.get("cpufreq_n", 0) or 0)
            if _fn > 0:
                _fm = float(d.get("cpufreq_min", 0.0) or 0.0)
                _fp = float(d.get("cpufreq_p50", 0.0) or 0.0)
                _fx = float(d.get("cpufreq_max", 0.0) or 0.0)
                _fc = float(d.get("cpufreq_cap", 0.0) or 0.0)
                _lp = float(d.get("cpufreq_low_pct", -1.0))
                # ⚠️ **代表值用平均**(玩家 2026-09-15:「cpu频率不能用中位数」)。中位仍印在括号里
                #    —— 它是**分布的一项**, 与最低/最高并列, 不再冒充代表值。
                # ⚠️ **平均取不到就印「没采到」, 绝不拿中位数顶**(那正是"印假数")。
                _fmn = float(d.get("cpufreq_mean", 0.0) or 0.0)
                _fm_txt = ("**平均 %.0fMHz**" % _fmn) if _fmn > 0 else "平均 **没采到**"
                _lines.append("# CPU 频率(**渲染窗口**那一段, 不是跑分段): %s · "
                              "最低 %.0f · 最高 %.0f · 上限 %.0fMHz · (中位 %.0f)%s"
                              % (_fm_txt, _fm, _fx, _fc, _fp,
                                 ("  ·  **低于上限一半的采样占 %.0f%%**" % _lp) if _lp >= 0 else ""))
                _lines.append("#   ⚠️ 这一段天生偏低: 渲染窗口里应用大部分时间在**等 vsync**,"
                              " 调频器据此判它很闲。要看「跑分时跑到多少」请看上面那行"
                              "「跑分那段的 CPU 平均频率」。")
                # ⚠️ 判据: 中位远低于上限 ⇒ 采样期全程低频, 那 `主线程ms` 是**被主频放大过的**,
                #    不能拿去和其他跑分比, 也不能当作"应用真的这么重"; 反过来, 砍掉同样的工作量
                #    在低频下**省下的墙钟更多** —— 所以低频并不是"优化没用", 是"优化更值"。
                _lines.append("#   读法: 中位远低于上限 = 采样期一直低频跑(调频器按负载升频, 而本应用"
                              "大部分时间在等 vsync ⇒ 它看着很闲)。此时 `主线程ms` 被主频放大,"
                              " 同一份代码在不同跑分里会差好几倍。")
        except Exception:
            pass
        # ---- 已采未印的那几格(2026-09-14 补) ----
        # ⚠️ 为什么补: 真机 TB323FU(165Hz, 与 120Hz 那台**同 SoC**)的日志里有两段 0.8 秒的低谷
        #    (滚动 fps 掉到 136~152); 而低谷里飞行帧的**主线程 CPU 占比与全局一模一样(34%)**,
        #    只是**绝对 CPU 涨了 20%**(2.05→2.49ms)、帧长同步涨 20%。也就是说那段**不是被外面
        #    挡住, 是我们自己每帧多干了 20% 的活**。可"多干的是什么"这份日志一个字都没有 ——
        #    而 GC 次数/耗时、进程态 utime/stime、后端是谁、这几个数 `_bench_collect_diag`
        #    **早就采好了, 只是 `_bench_frame_log` 从来没引用过它们**。
        #    纯打印, 零新埋点、零行为改变。
        try:
            _gc_n = int(d.get("gc_n", 0) or 0)
            _u = float(d.get("utime_ms", -1.0))
            _s = float(d.get("stime_ms", -1.0))
            _cpf = float(d.get("cpu_per_frame", 0.0) or 0.0)
            if _u >= 0.0 and _s >= 0.0:
                _lines.append("# 进程态: 用户态 %.0f 毫秒 · 内核态 %.0f 毫秒(内核占 %.0f%%)"
                              "  ·  每帧全进程 CPU 平均 %.2f 毫秒"
                              % (_u, _s, 100.0 * _s / max(1.0, _u + _s), _cpf))
                # ⚠️ 读法: 内核占比高 ⇒ JNI/Binder/socket 写/文件写回这一族;
                #    用户态占绝对多数 ⇒ 纯 Python 的 CPU 竞争(GIL)。见 `_cpu_split` 处的说明。
            _lines.append("# GC: %d 次 · 累计 %.1f 毫秒 · 单次最慢 %.1f 毫秒(gen%s)  ·  后端 %s"
                          % (_gc_n, float(d.get("gc_total", 0.0) or 0.0) * 1000.0,
                             float(d.get("gc_worst", 0.0) or 0.0) * 1000.0,
                             str(d.get("gc_worst_gen", -1)), str(d.get("backend", "?"))))
            # ⚠️ **内存冻结必须落进日志**(2026-09-15 加)。这一行原来**只在成绩面板上显示**
            #    (`main.py` 里那行 `内存冻结：已冻结 N 个常驻对象 …`), txt 里没有 ⇒ **离线看日志
            #    时无从判断**。实测代价: 四轮 v0.7.50 日志里有一轮撞到 **21.2 毫秒的 gen-2**
            #    (占那一轮最慢帧的全部), 而账面上它与另外三轮看不出任何差别 —— 于是"冻结到底
            #    生效没有"这个问题**查不动**。面板那行的原注释本来就写着"冻结生效与否**必须
            #    显示**", 但只显在面板上等于离线分析时没有。
            # ⚠️ **`gc_frozen == 0` 也要印**(印成"没冻结"), 不能 `if frozen:` 就跳过 ——
            #    那正是本仓库栽过的"静默": 冻结链没跑到/抛了异常时, 日志一片安静, 看起来
            #    和"冻结成功所以没问题"一模一样。
            _fz = int(d.get("gc_frozen", 0) or 0)
            if _fz:
                _lines.append("# 内存冻结: 已冻结 %d 个常驻对象 · 强制全量回收 冻结前 %.1f → 冻结后 %.1f 毫秒"
                              % (_fz, float(d.get("gc_frz_before", 0.0) or 0.0),
                                 float(d.get("gc_frz_after", 0.0) or 0.0)))
            else:
                _lines.append("# 内存冻结: **没冻结**(gc_frozen=0) —— 冻结链没跑到或抛了异常,"
                              " 常驻对象仍被 gen-2 全量回收反复扫")
            # JNI: 主线程那一笔才是"卡我们"的; 后台那一笔只说明工作线程在忙。
            _jn = int(d.get("jni_n", 0) or 0)
            if _jn or float(d.get("ui_n", 0) or 0):
                _lines.append("# JNI: 发声 %d 次 · 主线程累计 %.1f / 最慢 %.1f 毫秒"
                              "  ·  后台累计 %.1f / 最慢 %.1f 毫秒"
                              "  ·  界面调用 %d 次 / 最慢 %.1f 毫秒  ·  失败 %d 次"
                              % (_jn, float(d.get("jni_main_sum", 0.0) or 0.0),
                                 float(d.get("jni_main_worst", 0.0) or 0.0),
                                 float(d.get("jni_bg_sum", 0.0) or 0.0),
                                 float(d.get("jni_bg_worst", 0.0) or 0.0),
                                 int(d.get("ui_n", 0) or 0),
                                 float(d.get("ui_worst", 0.0) or 0.0),
                                 int(d.get("jni_err", 0) or 0)))
            _w20 = d.get("w20_pos") or []
            if _w20:
                _lines.append("# 最差20帧距上次发射的帧数(<=20 帧 = 发射后 0.33 秒内): %d 个"
                              "  ·  全部位次 %s"
                              % (int(d.get("w20_near", -1)), str([int(x) for x in _w20])[:110]))
            _cfg_n = int(d.get("cfg_n", 0) or 0)
            if _cfg_n:
                _lines.append("# 配置写盘: %d 次 · 累计 %.1f / 最慢 %.1f 毫秒"
                              % (_cfg_n, float(d.get("cfg_sum", 0.0) or 0.0),
                                 float(d.get("cfg_worst", 0.0) or 0.0)))
        except Exception:
            pass
        # ⚠️ 新列**只能加在末尾**: 前面几列的位置被 `_bench_frames` 的下标和外部脚本按号取,
        #    插在中间会让旧解析器静默错位(比报错更难发现)。
        _adv = [float(_r[10]) if len(_r) > 10 else 0.0 for _r in _fr] if _fr else []
        if len(_adv) != len(gaps):
            _adv = [0.0] * len(gaps)
        _lines.append("# 每行: 帧间隔毫秒,阶段,文字重建,_frame自算ms,主线程ms,发声,震动,最大子步骤,"
                      "等屏幕ms")
        _lines.extend("%.2f,%s,%d,%.2f,%.2f,%d,%d,%s,%.2f" % (g, t, x, s, m, sn, vb, b, w)
                      for g, t, x, s, m, sn, vb, b, w
                      in zip(gaps, tags, tex, _self_ms, _thr_ms, _snd_n, _vib_n, _top1, _adv))
        return "\n".join(_lines) + "\n"

    def _copy_bench_log(self, btn=None):
        """把逐帧日志复制到剪贴板。失败**必须说出来** —— 静默失败等于让玩家白等一次出包。"""
        txt = ""
        try:
            txt = self._bench_frame_log()
        except Exception:
            txt = ""
        if not txt:
            if btn is not None:
                btn.text = '没有可复制的数据'
            return False
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(txt)
            _ok = True
        except Exception:
            _ok = False
        # ⚠️ `return False` 必须在 `btn is not None` **外面** —— 否则无参调用(保存那条路
        #    就是这么调的)会一路落到 `return True`: 玩家被告知"已复制到剪贴板",
        #    而剪贴板其实是空的。对抗性审查实测出来的(审查前这里是 `if not _ok and btn is not None`)。
        if not _ok:
            if btn is not None:
                btn.text = '复制失败(剪贴板不可用)'
            return False
        if btn is not None:
            _n = txt.count("\n") + 1
            btn.text = '已复制 %d 行' % _n
            Clock.schedule_once(lambda _d: setattr(btn, 'text', '复制逐帧日志'), 2.0)
        return True

    def _bench_save_log(self):
        """把逐帧日志**存成 txt 文件**(玩家 2026-09-15: 「复制改为下载 txt, 这样就不缺少东西」)。

        ⚠️ 为什么不能只存 `user_data_dir`: 那是**应用内部目录**(`/data/data/<pkg>/files`),
        文件管理器看不见 —— 存那儿等于没存。剪贴板那条路在真机实测**会被截断**
        (3778 帧的日志只贴出 425 行, 安卓剪贴板走 Binder 有大小上限), 所以必须落盘。

        逐级降级, **每一级都把结果说出来**(绝不静默失败):
          ① 安卓 10+(API 29+) → MediaStore 写进**公共 Download 目录**, 不需要任何权限;
          ② `getExternalFilesDir` → `/sdcard/Android/data/<pkg>/files/`, 老系统也能写、不用权限;
          ③ `user_data_dir`(最后手段, 要 adb 才能取);
          ④ 全失败 → 退回**复制到剪贴板**, 并在提示里写明"剪贴板可能被截断"。
        返回 (成功?, 给玩家看的说明)。
        """
        try:
            txt = self._bench_frame_log()
        except Exception:
            txt = ""
        if not txt:
            return False, "没有可保存的数据"
        try:
            name = "plinko_fps_%s.txt" % time.strftime("%Y%m%d_%H%M%S")
        except Exception:
            name = "plinko_fps.txt"

        if platform != "android":
            # 桌面: 写到一个明确的地方(这样桌面也能验证"文件真的写出来了、内容完整")
            try:
                d = os.path.join(tempfile.gettempdir(), "plinko_fps")
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, name)
                with open(p, "wb") as f:
                    f.write(txt.encode("utf-8"))
                return True, "已保存: " + p
            except Exception as e:
                return False, "保存失败: %r" % (e,)

        # ---- ① MediaStore → 公共 Download(API 29+) ----
        try:
            from jnius import autoclass
            _sdk = int(autoclass("android.os.Build$VERSION").SDK_INT)
        except Exception:
            _sdk = 0
        if _sdk >= 29:
            _uri1 = None
            try:
                from jnius import autoclass
                act = autoclass("org.kivy.android.PythonActivity").mActivity
                resolver = act.getContentResolver()
                cv = autoclass("android.content.ContentValues")()
                cv.put("_display_name", name)
                cv.put("mime_type", "text/plain")
                _uri1 = resolver.insert(
                    autoclass("android.provider.MediaStore$Downloads").EXTERNAL_CONTENT_URI, cv)
                if _uri1 is not None:
                    os_ = resolver.openOutputStream(_uri1)
                    # ⚠️ 必须走 `java.lang.String.getBytes("UTF-8")` 拿到**真正的 byte[]** ——
                    #    直接把 Python `bytes` 交给 `OutputStream.write` 时, pyjnius 可能挑中
                    #    `write(int)` 重载, 于是只写进去一个字节(静默截断成一个字符)。
                    os_.write(autoclass("java.lang.String")(txt).getBytes("UTF-8"))
                    os_.flush()
                    os_.close()
                    return True, "已保存到 Download/" + name
                _err1 = "insert 返回空"
            except Exception as e:
                _err1 = repr(e)
            # ⚠️ **insert 一返回, 那一行就已经落库、文件当场对玩家可见**(没设 is_pending)。
            #    此后任何一步抛(openOutputStream 返回 None / 写到一半 / close 的 flush 失败)
            #    都会走到这里 —— 不清掉的话, 公共 Download 里留下一个**0 字节或半截的同名 txt**,
            #    而这个功能的全部承诺就是"去 Download 拿", 玩家会抓到那个坏文件发出去。
            #    (对抗性审查 M1 提的; API 29+ 删自己插的行不需要权限。)
            #    ⚠️ 别把 `os_.close()` 挪进 `finally` —— 那样"没写全"会被吞成"已保存",
            #       正好制造这个功能要消灭的静默截断。
            if _uri1 is not None:
                try:
                    resolver.delete(_uri1, None, None)
                except Exception:
                    pass
        else:
            _err1 = "API %d < 29" % _sdk

        # ---- ② 外部私有目录 / ③ 内部目录 ----
        _tries = []
        try:
            from jnius import autoclass
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            _d = act.getExternalFilesDir(None)
            if _d is not None:
                _tries.append(_d.getAbsolutePath())
        except Exception:
            pass
        try:
            _tries.append(App.get_running_app().user_data_dir)
        except Exception:
            pass
        # ⚠️ 这一行**必须在 try 里** —— `tempfile.gettempdir()` 在候选目录都不存在时会抛,
        #    裸着写会让整个函数冲出异常, ①②③ 白跑、连 ④ 都到不了(审查 M4)。
        try:
            _tries.append(tempfile.gettempdir())
        except Exception:
            pass
        for _base in _tries:
            try:
                if not os.path.isdir(_base):
                    os.makedirs(_base, exist_ok=True)
                p = os.path.join(_base, name)
                with open(p, "wb") as f:
                    f.write(txt.encode("utf-8"))
                # ⚠️ 这一级**必须把 ① 为什么没成一起说出来**(审查 M2): 否则玩家看到
                #    "需用文件管理器/adb 取"会先去文件管理器白找一轮 —— 而这个目录在
                #    Android 11+ 上**系统文件管理器根本进不去**, 只有 adb 能取。
                #    把原因写上, 他一眼就知道该换电脑插线, 而不是以为是自己没找对地方。
                return True, ("已保存: %s（Android 11+ 的文件管理器看不到这个目录, "
                              "要用电脑 adb pull 取）· 直接存 Download 失败原因: %s"
                              % (p, _err1))
            except Exception:
                continue

        # ---- ④ 全失败: 退回剪贴板(复用已测过的那个方法), 并**明说可能被截断** ----
        try:
            if self._copy_bench_log():
                return True, "没法落盘(%s); 已复制到剪贴板 —— 安卓剪贴板可能截断" % _err1
        except Exception:
            pass
        return False, "保存失败(落盘与剪贴板都不可用): %s" % _err1

    def _show_fps_curve(self):
        """展示本轮提交帧率趋势；曲线与 1% Low 共用同一份 on_flip 原始采样。"""
        gaps = list(getattr(self, "_render_gaps_ms", []) or [])
        # ⚠️⚠️ **本面板自己的标签不计入字体账本**(2026-09-15, 对抗评审第 0 批)。
        #    病根: 下面那个标题走 `_fit_line` → `_fit_font_size_slow` **一次走满 13 档阶梯**
        #    ⇒ 一口气开 12~13 个 fontid。而玩家**必须**打开这个面板才能导出日志
        #    ⇒ **保存日志这个动作, 把日志里印的那个计数撑大 12** —— 测量动作污染被测对象。
        #    铁证: 账本尾部那 12 个连降数 = 基准 57.2375 × 阶梯(相邻比值逐位吻合到小数点
        #    后四位), 而 `57.2375/19 = 45.1875/15 = 39.16/13 = 3.0125` 正是本机密度。
        #    ⚠️ 窗口是**时间**不是"函数返回": 布局收敛是异步的, 跨好几帧才定稿。
        #    ⚠️ 跳过几次**照记**(`_FS_MUTED_N`)并印进日志 —— 不许静默。
        _FS_MUTE_UNTIL[0] = time.time() + _FS_MUTE_SEC
        # 每一帧"当时在演什么", 与 `gaps` **同序等长** —— 两者来自同一批 `on_flip` 采样:
        # `_on_flip` 一直在往 `_bench_frames` 里存 `(帧间隔, 场景标签)`, 曲线原来只取了前半截。
        # 接到曲线上之后, "哪几帧在掉" 和 "那几帧在演什么" 就是上下对齐看的, 不用再对表格。
        # ⚠️ 取不到(长度对不上/老记录)时 `FpsCurve` 会自己退化成不画阶段条, 曲线照旧。
        tags = [x[1] for x in (getattr(self, "_bench_frames", []) or []) if len(x) > 1]
        content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(8))
        title = self._fit_line(Label(text='帧率曲线', bold=True, halign='center',
                                     color=hex_rgb(COL_TEXT) + (1,),
                                     size_hint_y=None, height=dp(26)), 19)
        content.add_widget(title)
        cap = float(_FPS_INFO[1] or _fps_user_cap())
        curve = FpsCurve(gaps, cap_fps=cap, tags=tags,
                         size_hint_y=None, height=dp(276))
        content.add_widget(curve)
        avg = float(getattr(self, "_render_fps", 0.0))
        med = float(getattr(self, "_render_median_fps", 0.0))
        low = float(getattr(self, "_render_1low", 0.0))
        note = Label(text='逐帧 FPS　平均 %.1f　中位 %.1f　1%%Low %.1f'
                          % (avg, med, low),
                     font_size='13sp', halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(22))
        note.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        content.add_widget(note)
        # 两个按钮一行: 保存日志(txt) + 返回。
        # ⚠️ 保存是**唯一**能把"逐帧数据"**完整**带出这台设备的出口 —— 曲线只能看, 带不走;
        #    而剪贴板在真机上**会被截断**(实测 3778 帧的日志只贴出 425 行)。
        _btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        save = Button(text='保存日志(txt)', font_size='16sp', bold=True,
                      background_normal='', background_color=hex_rgb(COL_BTN) + (1,))
        close = Button(text='返回', font_size='16sp', bold=True,
                       background_normal='', background_color=hex_rgb(COL_BTN_OFF) + (1,))
        _btns.add_widget(save)
        _btns.add_widget(close)
        content.add_widget(_btns)
        # 保存结果**单独一行常驻显示** —— 按钮上的字两秒就变回去了, 而"存到哪了"是要照着去找的。
        # ⚠️ 高度**必须走 `_auto_h`**: ② 那条提示里带完整路径 + 失败原因, 12sp 下要折
        #    2~4 行, 写死 `height=dp(30)` 会把尾巴裁掉 —— 而尾巴里正是"① 为什么没成"。
        #    (对抗性审查 A2 提的, 当时标的是"未验证"; 随后我把文案写得更长, 所以必须一起改。)
        _note = Label(text='', font_size='12sp', halign='center', valign='middle',
                      color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(30))
        _note.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
        self._auto_h(_note, dp(30), dp(4))
        content.add_widget(_note)
        popup = self._popup(0.92, 390, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _do_save(*_):
            try:
                _ok, _msg = self._bench_save_log()
            except Exception as _e:
                _ok, _msg = False, "保存失败: %r" % (_e,)
            _set_label_text(_note, _msg)
            save.text = '已保存' if _ok else '保存失败'
            Clock.schedule_once(lambda _d: setattr(save, 'text', '保存日志(txt)'), 2.5)

        save.bind(on_release=_do_save)
        close.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _bench_diag_text(self):
        """性能测试成绩面板的**诊断追加行**(2026-09-13 加)。

        为什么要加: 原来的成绩只有"平均帧率 / 1%Low", 而玩家报的是"球在飞行的时候
        一卡一卡的" —— 光看两个数**分不清卡在哪儿**: 是在飞行, 还是在装杯演出?
        是处理器顶不住, 还是画面在拖?
        ⚠️ 措辞一律用白话(玩家 2026-09-13: "我有点看不太懂 比如墙钟"): 不用"p50/p99/
          墙钟/GC"这些行话, 直接说"一半的帧""内存回收停顿""在等画面 / 算不过来"。
        任何一项取不到都返回空串, 面板与旧版逐字相同 —— 绝不让诊断把成绩挤没。"""
        d = getattr(self, "_bench_diag", None)
        if not d:
            return ''
        try:
            parts = []
            # "实算"= 那一帧真的烧掉的 CPU。它接近帧间隔就是**算出来的**(处理器瓶颈);
            # 很小就是**等出来的**(GC/IO/显卡/驱动)。真机上这两个数一对一比就见分晓。
            # "实算"只在 >=1 毫秒时才带出来: 安卓是 Linux, process_time 有纳秒精度, 那几个数
            # 是真有用的; 而 Windows 只有 15.6ms 精度, 桌面跑分里恒为 0.0, 纯噪声。
            # ⚠️ 两个分支的**占位符个数不一样**(带实算 3 个 / 不带 2 个), 所以必须分别格式化 ——
            #    写成 `('A' if c>=1 else 'B') % (g,t,c)` 会在 B 分支抛 TypeError, 而外层那个
            #    except 会把它**静默吞掉**(面板诊断整块消失, 不报错)。踩过一次了。
            # ⚠️ `_FRAME_SELF`(自算) **只包了 `_frame`** —— 启动预热、方向守卫、进度提示
            #    这些都跑在别的 Clock 回调里, 在它外面。所以"自算很小"**不等于**"我们没干活":
            #    桌面实测那几帧 实算 62.5 而自算 0.1, 正是预热(球纹理烘焙)干的。
            #    所以**预热帧必须直接标出来**, 否则会被误读成"主线程在等"。
            def _frame_cell(_g, _t, _c, _h, _s, _b, _k=(), _i=-1):
                # ⚠️ 占位符个数不同的分支**必须分开格式化** —— 写成
                #    `('A' if c else 'B') % (g,t,c)` 会在一个分支抛 TypeError, 而外层
                #    那个 except 会把它静默吞掉(面板诊断整块消失)。踩过一次了。
                # **实算 / 主线程 / 自算** 三个数并排 —— 玩家 2026-09-14 质疑"9 毫秒是不是小头",
                # 那三帧的账确实对不上(36毫秒那帧自算只有 0.3, 23.5 毫秒的超出不在 `_frame` 里),
                # 而"主线程"才是能看见 Kivy 渲染那一块的那一格。
                if _c >= 1.0 or _s >= 1.0:
                    _x = '%.0f毫秒(%s·实算%.1f·主线程%.1f·自算%.1f)' % (_g, _t, _c, _h, _s)
                else:
                    _x = '%.0f毫秒(%s)' % (_g, _t)
                if _b:
                    _x += '·预热'
                # 本帧最大的两笔子步骤 —— **这是"那 9 毫秒到底花在哪"的直接答案**。
                # ⚠️ 标签之间用 '/' 分隔而不是空格: 面板那一行本来就长, 空格会让它读不清哪里断开。
                for _ms, _nm in (_k or ()):
                    _x += '|%s%.1f' % (_nm, _ms)
                if _i >= 0:
                    _x += '@%d' % _i          # 在采样序列里的下标: 相邻=同一段忙碌
                return _x
            _w = []
            for _it in d["worst"]:
                _w.append(_frame_cell(_it[0], _it[1], _it[2],
                                      (_it[3] if len(_it) > 3 else 0.0),
                                      (_it[4] if len(_it) > 4 else 0.0),
                                      (_it[5] if len(_it) > 5 else 0),
                                      (_it[6] if len(_it) > 6 else ()),
                                      (_it[7] if len(_it) > 7 else -1)))
            w = '  '.join(_w)
            parts.append('最慢三帧： ' + w)
            _ord = ("飞行", "装杯", "落袋", "蓄力", "哑火", "待机")
            gs = d["groups"]
            # 带上帧数 —— 只印中位数的话, "待机 8 帧"的中位和"飞行 300 帧"的中位不可比,
            # 而三个最慢帧里两个是待机(压测段专家指出的)。
            seg = ['%s %.1f(%d帧)' % (t, gs[t][1], gs[t][0]) for t in _ord if t in gs]
            if seg:
                parts.append('各阶段每帧： ' + ' · '.join(seg) + ' 毫秒')
            # 慢帧的节拍 + 当时在不在发声/震动。这一行是给"偶发长停顿"定位用的 ——
            # 规则节拍指向时钟驱动, 不规则指向事件驱动; 而慢帧里发声/震动的占比
            # 直接回答玩家那句"关掉音效就好了"到底是声音还是震动造成的。
            # 发声/震动计数**独立一行、永远显示**(玩家 2026-09-13: "发声多少次 没有看到啊")。
            # 它有两个用途: ① 真机上把"偶发长停顿"归因到声音还是震动(玩家唯一有因果力的线索
            # 是"关掉音效 1% low 就回升", 而那会**连震动一起关掉**, 混在一起分不开);
            # ② **自证探针在工作** —— 看到"全程发声 N 次"就知道计数器真跑了, 否则
            # "慢帧里 0 帧在发声"既可能是真的、也可能是计数器根本没跑(这仓库栽过这种静默)。
            # 发声/震动**各占一行** —— 这两个数现在是定位卡顿的主判据, 挤一行读不清。
            # 后端名并到发声那行(它只跟发声有关)。
            _wn = d.get("snd_worst_name", "")
            parts.append('发声： %d 次 · 单次最慢 %.1f 毫秒%s · 累计 %.0f 毫秒'
                         ' · 超%.0f毫秒 %d 次（后端 %s）'
                         % (d.get("snd_n", 0), d.get("snd_worst", 0.0),
                            ('（%s）' % _wn) if _wn else '',
                            d.get("snd_sum", 0.0), SND_SLOW_MS,
                            d.get("snd_slow_n", 0), d.get("backend", "?")))
            _vn = d.get("vib_worst_name", "")
            parts.append('震动： %d 次 · 单次最慢 %.1f 毫秒%s · 累计 %.0f 毫秒'
                         % (d.get("vib_n", 0), d.get("vib_worst", 0.0),
                            ('（%s）' % _vn) if _vn else '',
                            d.get("vib_sum", 0.0)))
            # 方向守卫 / 沉浸重申那一行 —— 只在这两条链**真的跑过**时出(桌面是 0 次)。
            # 2026-09-14 起它们**已搬到工作线程**, 所以这里报的是两档对比:
            # 主线程那档该接近 0(证明真搬走了), 工作线程那档接手。
            if d.get("jni_n"):
                parts.append('方向守卫： %d 次（每0.7秒）· 主线程单次最慢 %.2f 毫秒 · 累计 %.1f 毫秒'
                             '　工作线程累计 %.0f 毫秒（最慢 %.1f）· 失败 %d 次'
                             % (d.get("jni_n", 0), d.get("jni_main_worst", 0.0),
                                d.get("jni_main_sum", 0.0), d.get("jni_bg_sum", 0.0),
                                d.get("jni_bg_worst", 0.0), d.get("jni_err", 0)))
            # 系统栏那一次重申的**真实代价** —— 跑在 Java UI 线程上, 只有这里看得见。
            # 判据: 单次 ≥5 毫秒 ⇒ 每 0.7 秒重申一次是在拿 UI 线程换一个系统本来就会
            # 自动隐藏的东西(IMMERSIVE_STICKY 自己会收回), 那就该把周期拉长。
            if d.get("ui_n"):
                parts.append('系统栏重申： %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒（UI线程，不在主线程实算里）'
                             % (d.get("ui_n", 0), d.get("ui_worst", 0.0),
                                d.get("ui_sum", 0.0)))
            # 慢帧那一行只在真有慢帧时出(它回答的是"停顿长什么样", 没停顿就没什么可说的)。
            if d.get("slow2_n"):
                # ⚠️ "几帧在预热"必须单独报 —— 那几帧是**启动期**的账, 与稳态卡顿不是一回事
                #    (桌面实测: 最慢的 11 帧全在启动 0.6 秒内, 而那些帧我们自己的代码只花了
                #    0.03~0.10 毫秒)。混在一起看会把"启动慢"误读成"玩起来卡"。
                # ⚠️ 这里叫「长停顿」不叫「慢帧」: 「慢帧」已经被成绩面板占成
                #    「中位帧率 <75%」(那一档有 168 帧), 而这一行说的是「≥2 倍中位帧时间」
                #    (<50%, 只有 5 帧)。**同一个词两个含义**正是本工程栽过的那类静默脱钩
                #    (实测同一份日志里两个"慢帧"差 33 倍)。代码里这个现象一直叫"长停顿"
                #    (见 159/2009/9570/9774/10819 行), 这里跟着叫它。
                parts.append('长停顿： %d 帧≥%.0f毫秒（平均每 %.2f 秒一次）· 其中 %d 帧在发声'
                             ' / %d 帧在震动 / %d 帧在启动预热'
                             % (d["slow2_n"], d.get("slow2_ms", 25.0), d["slow_beat"] / 1000.0,
                                d.get("slow_snd", 0), d.get("slow_vib", 0),
                                d.get("slow_bake", 0)))
            # 瓶颈判断: 每帧真正花在计算上的时间 vs 帧间隔。差得远 = 大头在等画面
            #   (GPU 出图 / 垂直同步); 快追平 = 处理器就是瓶颈。
            _cpu = float(d.get("cpu_per_frame", 0.0))
            _p50 = float(d["p50"])
            # ⚠️ **Windows 上这一整行不成立, 必须说清楚**(2026-09-14 补)。
            #    本文件上面自己写着"Windows 的 `process_time` 精度只有 15.6ms, 桌面跑分里
            #    恒为 0.0, 纯噪声" —— 可"瓶颈"判断仍然拿它去除帧间隔, 于是 PC 上会打出
            #    「处理器算不过来 · 每帧实算 18.4 / 帧间隔 16.5」这种**自相矛盾**的结论
            #    (玩家 2026-09-14 实测: 同一份面板里"最慢三帧 20毫秒(待机·实算31.2)" ——
            #     20 毫秒的帧不可能烧 31 毫秒, 那就是量化台阶本身)。
            #    这正是本仓库警告过的那个形状: **一个专抓静默归因的面板, 自己在做静默归因**。
            _win = (sys.platform == "win32")
            if _win:
                _v = "本机测不准"
            elif _p50 > 0 and _cpu > 0:
                _r = _cpu / _p50
                # ⚠️ 第三档**故意不说"画面是瓶颈"**(玩家 2026-09-13 问"是 gpu 还是?"):
                #    这一项只量"本进程烧了多少 CPU"。"算得少、等得多"既可能是等显卡出图/
                #    等垂直同步, 也可能是**阻塞在系统调用上**(Binder/JNI/文件IO) —— 本项
                #    **分不出这两者**。写死"画面"会把归因带偏(这个仓库栽过: 一个专抓静默的
                #    面板自己却在做静默归因)。要分清得靠"慢帧里在发声/震动吗"那一行。
                _v = ("处理器算不过来" if _r >= 0.8
                      else ("处理器比较吃紧" if _r >= 0.4 else "大头在等"))
            else:
                _v = "—"
            # GC 那一栏后面挂上"最坏那次是哪一代" —— gen-2 全量回收与 gen-0 差一个量级,
            # 不写清是哪一代, 读者没法判断这是"轻微抖动"还是"扫了整个对象图"。
            _gn = {0: "轻度", 1: "中度", 2: "全量"}.get(d.get("gc_worst_gen", -1), "—")
            # 三个数并排才能分流"谁在吃时间"(见 _FRAME_THR 处的说明):
            #   实算(全进程)大 · 主线程小 => 工作线程在忙;
            #   主线程大 · _frame 小      => **Kivy 渲染 / 文字重排**在吃;
            #   _frame 大                => 就是我们自己的代码。
            _win_s = max(0.001, d.get("p50", 12.5) * max(1, d.get("n", 0)) / 1000.0)
            # 节拍那一行 —— 直接回答"帧循环是不是自由跑": 比值 < 1 说明**呈现比逻辑更新更频繁**
            # (有一批帧在白白占用呈现机会)。也顺手把 Kivy 的限速旋钮值打出来。
            _fc = int(d.get("frame_calls", 0))
            _hz, _cap, _mode_hz = _FPS_INFO[0], _FPS_INFO[1], _FPS_INFO[2]
            _platform_rate = (' · Android模式请求 %s' %
                              (('%.0fHz' % _mode_hz) if _mode_hz else
                               ('%.0fHz（模式未知）' % _fps_user_cap()))
                              if platform == "android" else ' · 桌面vsync跟随屏幕刷新率')
            parts.append('节拍： 屏幕 %s · 帧率上限 %s · vsync=%s%s'
                         ' · `_frame` %d 次 / 采样 %d 帧（比值 %.2f）'
                         % (('%.0fHz' % _hz) if _hz else '没读到',
                            ('%.0f' % _cap) if _cap else '不设(0)', d.get("vsync", "?"), _platform_rate, _fc,
                            d.get("n", 0), _fc / float(max(1, d.get("n", 1)))))
            # 内核态占比 —— 把这一个数当**分流器**: 高则查 JNI/Binder/文件写, 低则查 GIL。
            # C6: 最差 20 帧是不是**紧跟在一次发射之后**? 是 ⇒ "每发才做一次的活"有罪;
            # 铺开 ⇒ 无罪, 该往别处找。(这一条是全池子里判"站得住"的那条。)
            _p = d.get("w20_pos") or []
            if _p:
                parts.append('最差20帧： 距上次发射 %d~%d 帧（中位 %d）· 其中 %d 帧在发射后 20 帧内'
                             '　（铺开=与发射无关）' % (_p[0], _p[-1], _p[len(_p) // 2],
                                                    d.get("w20_near", -1)))
            parts.append('渲染设置： multisamples=%s（Kivy 默认 2 = 2x MSAA 全屏，每帧固定带宽成本）'
                         % d.get("multisamples", "?"))
            _u, _k = float(d.get("utime_ms", -1)), float(d.get("stime_ms", -1))
            if _u >= 0 and (_u + _k) > 0:
                parts.append('CPU 构成： 用户态 %.0f 毫秒 · 内核态 %.0f 毫秒（内核占 %.0f%%）'
                             % (_u, _k, 100.0 * _k / (_u + _k)))
            parts.append('瓶颈： %s · 每帧实算 %.1f / 主线程 %.1f（其中 _frame %.1f，最坏 %.1f）'
                         '/ 帧间隔 %.1f 毫秒'
                         ' · 内存回收 %.1f 毫秒（最坏一次 %.1f·%s）· 文字重排 %.1f 次/秒'
                         % (_v, _cpu, d.get("thr_p50", 0.0), d.get("self_p50", 0.0),
                            d.get("self_max", 0.0), _p50, d["gc_total"] * 1000.0,
                            d["gc_worst"] * 1000.0, _gn, d.get("texupd", 0) / _win_s))
            _tex_by = d.get("texupd_by") or []
            if _tex_by:
                parts.append('文字重排来源：' + ' · '.join('%s %d次' % (name, count)
                                                       for name, count in _tex_by))
            if d.get("cfg_n"):
                parts.append('　存档落盘： %d 次（工作线程）· 单次最慢 %.1f 毫秒'
                             % (d["cfg_n"], d.get("cfg_worst", 0.0)))
            # 冻结生效与否**必须显示** —— 否则"GC 没再拖后腿"既可能是真冻结了, 也可能是
            # 根本没跑到(这个仓库专门栽过这种静默)。
            if d.get("gc_frozen"):
                # 冻结**前后各强制做一次全量回收**的实测读数 —— 这是"冻结到底买到了什么"
                # 在真机上的直接答案, 不用靠推断(桌面量不出来: 30 秒总共才 0.6 毫秒的 GC)。
                parts.append('内存冻结： 已冻结 %d 个常驻对象 · 全量回收 %.1f -> %.1f 毫秒'
                             % (d.get("gc_frozen", 0), d.get("gc_frz_before", 0.0),
                                d.get("gc_frz_after", 0.0)))
            if _win:
                parts.append('⚠️ 每帧实算这一项在 Windows 上测不准(系统计时粒度 15.6 毫秒), '
                             '「瓶颈」不判 —— 要看它请用安卓机的成绩')
            return '\n'.join(parts)
        except Exception:
            return ''

    def _bench_low_summary_text(self):
        """成绩页默认只显示能指导 1% Low 优化的简短证据。"""
        d = getattr(self, "_bench_diag", None) or {}
        if not d:
            return ''
        try:
            parts = []
            # ⚠️ 这里原先还有三行 `_n / _ms / _stages = d.get("low1_*")` —— 是上一版
            #    「1% Low 定位」那一段留下来的**死代码**: 取值后再没人用, 而 `_n` 还在
            #    下面被重新定义了一次(影子变量)。2026-09-14 由 `fx_probe` 新增的那条
            #    「不许退回 low1_groups」断言抓出来 —— 门禁逮到了我自己留的垃圾。
            # ⚠️⚠️ **2026-09-15 玩家点检这一段**(「这个信息是不是没有用了? 如果真没有用就删掉,
            #    如果有用就保留有用的」)。这是**成绩面板**, 而玩家 2026-09-11 已经定过一条
            #    原则: **成绩面板只放成绩**(版本/日期都因此挪去了菜单弹窗)。逐行判下来:
            #      · `1% Low 定位：最慢 N 帧平均 X 毫秒` —— **删**。那是在解释 1%Low 是什么,
            #        而玩家明说过「大家都知道什么是平均帧和 1%low帧就不用你教学了」。
            #      · `文字纹理：N 次（余额 8 · 统计 4 · 结算大字 4）` —— **删**。那是**纯埋点**
            #        (给"文字重建"这条优化线用的), 玩家看了没有任何用处; 数据在保存的 txt 里
            #        一条不少(`_bench_frame_log`)。
            #      · `最慢帧：… · 主线程 9.8 / 游戏逻辑 8.5 毫秒` —— **删掉"主线程/游戏逻辑"**,
            #        那是开发者术语; 「最慢一帧是多少**帧/秒**、发生在哪个阶段」这一半留着,
            #        玩家看得懂(2026-09-15 又从"毫秒"改成了"帧/秒", 见下面 `_worst_line`)。
            #      · `各阶段出慢帧比例：…` 与 `低于 60 FPS 的帧：N / M` —— **留**。前者回答
            #        "卡在哪一段", 后者就是判据本身。
            _grp = d.get("groups") or {}
            # ⚠️ 2026-09-14 玩家定稿: 这一段的口径统一到**卡顿帧**(帧率 < 中位帧率 50%),
            #    不再是"最慢 1%" —— 否则上面写"卡顿帧共 N 帧"、下面写"各阶段比例"用的却是
            #    另一批帧, 两行对不上, 读数的人会以为哪里算错了。
            # ⚠️ 卡顿帧为 0 时**不要退回 `low1_groups`** —— 那会印出"分布"却写着"共 0 帧",
            #    两行自相矛盾(而且那批帧根本不是卡顿帧)。0 就明说 0。
            def _dist_line_of(groups, n_key, label):
                """把 {阶段: 帧数} 排成一行分布文案; 没有该档时返回空串。

                ⚠️ 卡顿帧与慢帧**共用这一个函数** —— 两处各写一份格式化迟早会漂成
                   两种排版(本工程在"口径"上栽过太多次)。
                ⚠️ `x/y` 的分母是**该阶段的帧数**("占这一阶段多少"), 与两档那行的分母
                   (**窗口总帧数**)不是一回事, 两处不可互比。
                ⚠️ **只印 `x/y`, 不印百分比**(玩家 2026-09-15:「去掉百分比 只有 x/y 这样的格式」)。
                   但**排序仍按比率**(`_rw` 里的 `r[0]`) —— 分数大小一样时, 比率才是
                   "哪个阶段更容易卡"的正确次序; 按绝对帧数排会把长阶段顶到前面。
                ⚠️ 该档为 0 帧时**整行不印**(见调用处的说明), 别印一个空分布。
                """
                _st = (groups or []) if d.get(n_key) else []
                _rw = []
                for _name, _cnt in _st[:3]:
                    _info = _grp.get(_name)
                    _tot = int(_info[0]) if isinstance(_info, (tuple, list)) and _info else 0
                    _rw.append(((100.0 * _cnt / _tot) if _tot > 0 else -1.0,
                                _name, int(_cnt), _tot))
                _rw.sort(key=lambda r: -r[0])
                _rt = [('%s %d/%d' % (r[1], r[2], r[3])) if r[3] > 0
                       else ('%s %d帧' % (r[1], r[2])) for r in _rw]
                return (label + '：' + ' · '.join(_rt)) if _rt else ''

            _dist_line = _dist_line_of(d.get("jank_groups"), "jank_n", '卡顿帧分布')
            # 慢帧分布(玩家 2026-09-14:「额外新增一个慢帧分布」)。
            # ⚠️ 判据是**中位帧率的 75%**(`SLOW_RATE`), 与 `slow_n` **同一个集合** ——
            #    这一档**包含**卡顿帧, 所以它的分布天然比上一行"大一圈"(每一格都 ≥)。
            _slow_dist_line = _dist_line_of(d.get("slow_groups"), "slow_n", '慢帧分布')
            _worst = (d.get("worst") or [None])[0]
            if _worst:
                _gap, _stage = float(_worst[0]), _worst[1]
                # ⚠️ 分隔符统一用**全角冒号** —— 上一版这里写的是半角 ": ", 三行里两行全角
                #    一行半角, 截图上一眼看出来不齐(玩家说过"排版废话很多", 别再送把柄)。
                # ⚠️ **2026-09-15 玩家要求改成帧率**(原为「最慢一帧：48 毫秒（装杯）」)。
                #    单位跟本面板其余几行走**中文「帧/秒」, 不用 `fps`** —— 上面两档门槛印的
                #    就是「低于 91 帧/秒」, 同一块面板混两种单位, 正是上一版被打回过的那种
                #    "排版送把柄"(见下面那条全角冒号的注释)。
                #    `_gap` 是帧间隔毫秒, 所以帧率 = `1000 / _gap` —— 和门槛那两行**同一个换算**。
                _worst_line = ('最慢一帧：%.1f 帧/秒（%s）' % (1000.0 / _gap, _stage)
                               if _gap > 0 else '')
            else:
                _worst_line = ''
            # ⚠️ 原来这一行是「低于 60 FPS 的帧: N / M」—— **绝对帧率**的门槛在高刷机上没有
            #    意义(165Hz 的机器上"低于 60fps"是地板级要求, 而且它的分子分母都是全窗口,
            #    跟上面"卡顿帧"那批根本不是同一批帧)。玩家 2026-09-14 定稿改成:
            #    **卡顿帧(帧率 < 中位帧率的 50%), 一共有 X 帧** —— 门槛跟着本机中位走。
            # ⚠️ **两档都要印**(玩家 2026-09-14 定稿, 名字也是他定的: 「改名字即可」):
            #    `卡顿帧（<55%）` 与 `慢帧（<75%）`, 阈值都是**相对本机中位帧率**。
            #    ⚠️ 慢帧**包含**卡顿帧(同一个分子集的两档), 所以它的数一定 ≥ 卡顿帧 ——
            #       别拿两者相减去算中间带。
            # ⚠️ **不变量: 慢帧(75%) 一定 ⊇ 卡顿帧(55%)** —— 两个阈值同一个分母, 宽的必然
            #    包含窄的。老 diag 字典/异常兜底里可能没有 `slow_n`, 直接印 0 就会出现
            #    "卡顿帧 6 / 慢帧 0" 这种**自相矛盾**的两行(探针夹具上当场撞到过)。
            # ⚠️⚠️ 但这个 `max` 是**兜底, 不是定义**(2026-09-14 实修): 它曾经把
            #    「`slow_n` 被覆盖成 5」这件事**盖住了** —— `max(5, 9) = 9`, 于是面板印的
            #    慢帧**恒等于卡顿帧**, 看起来"合理"却完全失真。真正的数来自 `_bench_collect_diag`
            #    里那句 `<75%` 的统计(真机 v0.7.35 = 168 帧), 对应的自证行是日志头的
            #    `# ★ 慢帧「中位帧率 <75%」`。**改这里之前先去看那一行对不对得上。**
            _jn = int(d.get("jank_n", 0))
            _sn = max(int(d.get("slow_n", 0)), _jn)
            # ---- 采样窗口 + 两档(玩家 2026-09-14 定稿的排版) --------------------------
            # 玩家原话:「**让我知道帧率计算的表演是多少秒, 多少帧**」, 随后两次点检措辞:
            #   · 「<55% 让人看得不明不白的」—— 而且那一行里**两个 `%` 含义还不一样**
            #     (门槛 vs 比例), 读者分不出哪个是哪个 ⇒ 门槛改成**算出来的帧/秒**。
            #   · 「**应该是 <xx帧, 这个xx是计算来的**」 ⇒ xx 不许写死, 必须由中位派生。
            #   · 「**你应该先写要求, 再写数量, 目前顺序不对**」 ⇒ 门槛进名字后的括号,
            #     数量跟在冒号后。别写成「卡顿帧：1 帧（…）· 低于 91 帧/秒」那种顺序。
            # ⚠️ **三个数必须同源**: 中位帧率、两个门槛**全从 `d["p50"]` 派生**, 而
            #    `_bench_collect_diag` 判 `jank_n`/`slow_n` 用的也是同一个 p50 ⇒
            #    "印出来的门槛"与"数出来的帧数"严格一致。**不要**改用日志头那个
            #    `_render_median_fps`(它是从 `_flip_times` 另算的) —— 两处各算必然脱钩。
            # ⚠️ 窗口时长取 `win_ms`(帧间隔之和), 与日志头 `sum(gaps)` **同一口径**。
            # ⚠️ 拿不到窗口就**整行不印、门槛与比例也不印**: 「0.0 秒 · 0 帧」「0.00%」
            #    是**假数**, 比没有更糟(本工程栽过好几次「面板印假数」)。帧数照常印。
            _n = int(d.get("n", 0) or 0)
            _win_ms = float(d.get("win_ms", 0.0) or 0.0)
            _p50 = float(d.get("p50", 0.0) or 0.0)
            if _n > 0 and _win_ms > 0.0 and _p50 > 0.0:
                parts.append('采样窗口：%.1f 秒 · %d 帧 · 中位 %.0f 帧/秒'
                             % (_win_ms / 1000.0, _n, 1000.0 / _p50))
                parts.append('卡顿帧（低于 %.0f 帧/秒）：%d 帧（%.2f%%）'
                             % (1000.0 * JANK_RATE / _p50, _jn, 100.0 * _jn / _n))
                parts.append('慢帧（低于 %.0f 帧/秒）：%d 帧（%.2f%%）'
                             % (1000.0 * SLOW_RATE / _p50, _sn, 100.0 * _sn / _n))
            else:
                parts.append('卡顿帧（<55%%）：共 %d 帧' % _jn)
                parts.append('慢帧（<75%%）：共 %d 帧' % _sn)
            if _dist_line:
                parts.append(_dist_line)
            if _slow_dist_line:
                parts.append(_slow_dist_line)
            if _worst_line:
                parts.append(_worst_line)
            return '\n'.join(parts)
        except Exception:
            return ''

    def _show_hp_history(self):
        """SOC 高压测试历史: **4 列**(时间 / 中位数 / 波动 / 详情)。

        玩家 2026-09-15: 「高压测试也专门搞个log记录。有4列，时间、中位数、波动、按钮。
        点击按钮可以看到详细成绩和SOC平均频率。」
        ⚠️ 与「测试历史」是**两张表**: 那一张是性能测试(峰值/帧率),
           这一张是 SOC 高压(衰减/频率)。挤一张只会互相污染。
        """
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='SOC高压测试历史', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        if not self.hp_history:
            content.add_widget(Widget(size_hint_y=1))
            empty = Label(text='暂无 SOC 高压测试记录\n\n性能测试菜单里选「SOC高压测试」\n连压 %d 秒即可产生一条' % int(SOC_SUSTAIN_WALL_SEC),
                          font_size='16sp', halign='center',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
            empty.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
            content.add_widget(empty)
            content.add_widget(Widget(size_hint_y=1))
        else:
            time_w, mid_w, spr_w = dp(112), dp(76), dp(66)
            columns = BoxLayout(size_hint_y=None, height=dp(22))
            for text, width in (('时间', time_w), ('中位数', mid_w),
                                ('波动', spr_w), ('详情', None)):
                head = Label(text=text, halign='center', valign='middle',
                             color=hex_rgb(COL_SUB) + (1,),
                             size_hint_x=None if width else 1)
                if width:
                    head.width = width
                self._fit_line(head, 14)
                self._fit1(head)
                columns.add_widget(head)
            content.add_widget(columns)
            # ⚠⚠ **必须给显式高度**(2026-09-15 实测): `ScrollView` 不是带
            #    `minimum_height` 的 Layout ⇒ `_popup_fit_content` 算内容最小高度时
            #    读到 0, 弹窗只按"标题+表头+脚注+按钮"收 ⇒ **滚动区被压成 0,
            #    一行数据都显示不出来**(截图为证: 表头在、下面空的)。
            #    按行数算死高度就不依赖 Kivy 的布局时序了。
            _vn = max(1, min(len(self.hp_history), 8))
            scroll = ScrollView(size_hint_y=None, height=dp(28 * _vn + 10))
            inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
            inner.bind(minimum_height=inner.setter('height'))
            t_rows, m_rows, s_rows = [], [], []
            for r in reversed(self.hp_history[-100:]):
                stamp = str(r.get('time', '--'))
                mid = r.get('median')
                spr = r.get('spread')
                mid_text = '%d' % int(mid) if mid is not None else '—'
                spr_text = '%.1f%%' % float(spr) if spr is not None else '—'
                row = BoxLayout(size_hint_y=None, height=dp(28))
                labs = []
                for text, width in ((stamp, time_w), (mid_text, mid_w), (spr_text, spr_w)):
                    lbl = Label(text=text, font_size='14sp', halign='center',
                                valign='middle', color=hex_rgb(COL_TEXT) + (1,),
                                size_hint_x=None if width else 1)
                    if width:
                        lbl.width = width
                    lbl.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
                    row.add_widget(lbl)
                    labs.append(lbl)
                # ⚠️ 第四列是**按钮**(唯一一个), 不进 `_fit_uniform` —— 它不是文字行。
                btn = Button(text='详情', font_size='14sp', bold=True,
                             background_normal='',
                             background_color=hex_rgb(COL_BTN) + (1,))
                btn.bind(on_release=lambda _b, rr=r: self._show_hp_detail(rr))
                row.add_widget(btn)
                t_rows.append(labs[0])
                m_rows.append(labs[1])
                s_rows.append(labs[2])
                inner.add_widget(row)
            self._fit_uniform(t_rows, sp(14))
            self._fit_uniform(m_rows, sp(14))
            self._fit_uniform(s_rows, sp(14))
            scroll.add_widget(inner)
            content.add_widget(scroll)
            foot = Label(
                text=('  中位数：高压那段各个 1 秒窗口速度的中位数\n'
                      '  波动：(最大-最小)/中位数，越大越不稳'),
                font_size='12sp', halign='left', valign='top',
                color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
            self._auto_h(foot, dp(44))
            content.add_widget(foot)
        close_btn = Button(text='关闭', font_size='16sp', bold=True,
                           background_normal='',
                           background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        content.add_widget(close_btn)
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.86 * _vw, height=0.7 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)

    def _show_hp_detail(self, r):
        """某一条 SOC 高压记录的**详细成绩 + SOC 平均频率**。"""
        _n = chr(10)
        _w = [x for x in (r.get('windows') or []) if x > 0]
        _step = max(1, len(_w) // 12) if _w else 1
        _curve = ' / '.join('%d' % _w[i] for i in range(0, len(_w), _step)) if _w else '—'
        _fm = int(r.get('freq_mean', 0) or 0)
        _fp = int(r.get('freq_p50', 0) or 0)
        _fn = int(r.get('freq_n', 0) or 0)
        if _fm > 0:
            _ft = ('%d MHz' % _fm)
            _fd = ('中位 %d / 最低 %d / 最高 %d（%d 个采样）'
                   % (_fp, int(r.get('freq_min', 0) or 0), int(r.get('freq_max', 0) or 0), _fn))
        else:
            _ft, _fd = '没采到', '非安卓 / 读不到 sysfs'
        _spr = r.get('spread')
        _txt = (_n + str(r.get('device', '?')) + '  ' + str(r.get('version', '')) + _n
                + str(r.get('time', '--')) + '   高压 %d 秒（背靠背不停）'
                % int(r.get('sec', 0) or 0) + _n + _n
                + '中位 %d 步/秒' % int(r.get('median', 0) or 0)
                + ('（波动 %.1f%%）' % float(_spr) if _spr is not None else '') + _n
                + '首 %d → 末 %d 步/秒（降 %.1f%%）' 
                % (int(r.get('first', 0) or 0), int(r.get('last', 0) or 0),
                   float(r.get('decay', 0) or 0)) + _n
                + '最低 %d 步/秒' % int(r.get('min', 0) or 0) + _n + _n
                + 'SOC 平均频率：' + _ft + _n
                + '频率分布：' + _fd + _n + _n
                + '每段采样：' + _curve)
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='SOC高压测试详情', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        body = Label(text=_txt, font_size='14sp', halign='left', valign='top',
                     color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None)
        self._auto_h(body, dp(220), dp(6))
        content.add_widget(body)
        close_btn = Button(text='关闭', font_size='16sp', bold=True,
                           background_normal='',
                           background_color=hex_rgb(COL_BTN_OFF) + (1,),
                           size_hint_y=None, height=dp(46))
        content.add_widget(close_btn)
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.88 * _vw, height=0.62 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        popup.open()
        self._popup_fit_content(popup, content)
    def _show_bench_history(self):
        """性能测试历史：每次完整测试严格一行，保留时间、帧率与 SoC 波动。

        ⚠️ 第三列表头是「**中位跑分** / 波动」(2026-09-14 从「步数」改的): 那一格是
           `phys_fps`, 来源是 `_bench_done` 里的 `_phys_sorted[len // 2]` —— **中位数, 不是均值**。
           表头写「步数」会让人以为是总步数或均值(玩家就这么问过)。
           ⚠️ 「波动」= `100 x (max - min) / 中位跑分`, 分子是**极值**、分母是**中位数** ——
              两次口径混用, 而且只看两个样本点 ⇒ **跑分次数少时一个异常值就能把它带飞**。
        """
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        # 这是“数据表”而不是一段左对齐正文：三列标题与数值均居中，扫视同一
        # 行时能更快对应；时间列仍固定足够宽，完整年份不会被压缩。
        # ⚠️⚠️ **2026-09-15 玩家定稿: 标题金色、备注灰色 —— 与 2026-09-14 那版正好对调。**
        #    玩家原话:「这里应该是标题用金色 备注用灰色？ 2个颜色换下」。
        #    对调后整块面板的层次是: **标题金(主) > 数据近白 > 表头/备注灰(次)** ——
        #    表头本来就是 `COL_SUB`, 备注改用同一档次色, 视觉上归成"说明文字"一类。
        #    ⚠️ 这是**有意反转** 11389-11394 那条旧决定(那里写"解释文字换成金色"), 不是漂移,
        #       别照着旧注释改回去。金色 `COL_BALL` 仍是本作「主数字」的颜色, 只是现在
        #       挂在标题上, 不再挂在备注上。
        title_lbl = self._fit_line(Label(text='测试历史（渲染 / SoC）', bold=True,
                                         halign='center', color=hex_rgb(COL_BALL) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
        content.add_widget(title_lbl)
        if not self.bench_history:
            # ⚠️⚠️ **2026-09-15: 弹窗高度固定不变, 空态靠两根"弹簧"竖向居中。**
            #    我第一版改错了方向 —— 把空态的弹窗**收小**了(还发了一版)。玩家当场指出:
            #    「高度不变, 因为以后要 tmd 放数据啊」—— 对。这个面板以后是要装数据的,
            #    高度必须**始终一样**, 否则"有没有记录"会让面板忽大忽小、读数时跳来跳去;
            #    真要有问题也只是"空的时候字堆在底下", 而那该用**居中去解决, 不是改高度**。
            #    做法: 上下各加一根 `size_hint_y=1` 的弹簧, 把这几行夹在标题和「关闭」中间居中。
            #    ⚠️ 弹簧只加在**空态**: 有记录时那块是 `size_hint=(1, 1)` 的 ScrollView,
            #       它自己就会把剩余空间吃掉, 再加弹簧反而挤掉列表。
            content.add_widget(Widget(size_hint_y=1))          # 上弹簧
            empty = Label(text='暂无测试记录\n\n长按标题 3 秒即可测试', font_size='16sp', halign='center',
                          color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(90))
            empty.bind(size=lambda w, _: setattr(w, 'text_size', w.size))
            content.add_widget(empty)
            content.add_widget(Widget(size_hint_y=1))          # 下弹簧
        else:
            # ⚠️ `fps_w` 82 -> 94(2026-09-15, 玩家截图: 「平均/1%Low帧」折成了两行)。
            #    实测: 那一串在 12sp 下**正好要 82.0 px**, 而这一格就是 `dp(82)` = 82.0 px
            #    —— **零余量**。原来是「平均/1%Low」(70px) 有 12px 余量, v0.7.28 按要求加上
            #    「帧」之后把余量吃光了, 任何一点取整/字体缩放都会把它顶成两行。
            #    94 恢复成原来那 12px 余量。第三列拿的是剩余宽度, 实测它的表头只要 83px, 够。
            # ⚠️⚠️ **不做固定列宽了**(玩家 2026-09-15:「数据用空格分割, 不强制要求对齐了」)。
            #    实测(`temp/hist_colwidth_probe.py`): 四列按各自最宽的那串算要 **333px**,
            #    而 360dp 的内容区(0.92 宽)只有 299.2px ⇒ 硬排就得把字号缩到 ~11.7sp。
            #    改成**整行一串空格分隔**之后, 同一行只要 **272px** —— **满 14sp 就放得下**。
            #    省下来的正是"每列各自的余量付四遍"。
            #    ⚠️ 代价是**上下不对齐**(比例字体, 列宽随内容浮动) —— 这是玩家明确接受的。
            head = Label(text=_HIST_HEAD, halign='center', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,),
                         size_hint_y=None, height=dp(22))
            head.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
            # ⚠️ **必须走 `_fit_line`(自动缩字号)** —— 表头比数据行长得多(315px vs 272px),
            #    窄屏上得靠它缩。⚠️ 绑的必须是 `(w.width, None)` **不是** `w.size`:
            #    两维都给 ⇒ 宽度不够就**折行**, 而这一排只有 dp(22) 高, 第二行直接被顶出格子。
            self._fit_line(head, 14)
            self._fit1(head)
            content.add_widget(head)
            scroll = ScrollView(size_hint=(1, 1))
            inner = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(2))
            inner.bind(minimum_height=inner.setter('height'))
            rows = []
            for r in reversed(self.bench_history[-100:]):
                # "2026-09-11 19:22    每秒 10971 步" 要 266px, 360dp 机器上只有 253px ⇒
                # 原来折成两行而格子只有 30px 高, 第二行直接被裁掉(玩家看到半行字)。
                # ⚠️ 字号/行高与另外两个列表弹窗**对齐**(见 _fit_uniform 上方那段说明):
                #    这里原来是 17sp/30dp —— 全 app 最大的正文, 比主界面正文(14~15)还大一档,
                #    而它是个要塞很多行的滚动列表。统一到 15sp(Body 档) + 26dp 行高:
                #    同一个滚动框里能多放约两行(玩家: 「这个设计的目的是放更多内容的」)。
                render, low = r.get('render_fps'), r.get('render_1low')
                stamp = _hist_stamp(r.get('time'))
                if render is None or low is None:
                    fps_text = '—'
                else:
                    fps_text = '%.1f/%.1f' % (float(render), float(low))
                # ⚠️ **CPU 平均频率**(玩家 2026-09-15 要的那一列, 排在**第三位**)。
                #    · 取的是**跑分时**的平均(采样间隙已被 `_FREQ_GATE` 滤掉), **不是中位数**
                #      —— 玩家原话「cpu频率不能用中位数」。
                #    · **旧记录**里从来没存过平均(那之前只存了 `phys_freq_p50`) ⇒ 显示
                #      「无数据」。**不拿中位数回落** —— 玩家明说不能用中位数, 拿别的数顶就是印假数。
                #    · ⚠️ 玩家 2026-09-15:「如果无/读不到频率 改为 **无数据** 而不是一个 `-`」
                #      —— 破折号会被读成"这一格坏了", 「无数据」才是"没采到"的正常态。
                #    · 单位写 `Mhz`(玩家两次都这么写), 不写成 `MHz`。
                _fmean = r.get('phys_freq_mean')
                freq_text = ('%dMhz' % int(_fmean)) if _fmean else '无数据'
                spread = r.get('phys_spread')
                if spread is None:
                    soc_text = '%d/—' % r.get('phys_fps', 0)
                else:
                    soc_text = '%d/%.1f%%' % (
                        r.get('phys_fps', 0), float(spread))
                # ⚠️ 整行**一串空格分隔**, 不做列对齐(玩家 2026-09-15:「数据用空格分割,
                #    不强制要求对齐了」)。字段顺序就是 `_HIST_COLS` 那个顺序 ——
                #    表头与数据行 `join` 的是**同一个** `_HIST_SEP`。
                row = Label(text=_HIST_SEP.join((stamp, fps_text, freq_text, soc_text)),
                            halign='center', valign='middle',
                            color=hex_rgb(COL_TEXT) + (1,),
                            size_hint_y=None, height=dp(26))
                # ⚠️ 绑 `(w.width, None)` 而**不是** `w.size`: 两维都给 ⇒ 宽度不够就折行,
                #    这是本工程 2026-09-15 踩过的那个坑(表头就是这么折的)。
                row.bind(size=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                rows.append(row)
                inner.add_widget(row)
            # ⚠️ 必须 `sp(17)` 而不是 `17.0` —— 这个形参是**绝对字号(px)**, 不是 sp 档位。
            #    传裸 17.0 在 density=2 的机器上就只有一半大(实测被探针的数字逮住:
            #    同一批行 17.0 而别的 17sp 行是 34.0)。
            # ⚠️ **只一条** —— 现在整行是**一个 Label**, 所以一组就是全表。
            #    这正是 `_fit_uniform` 存在的理由(整表同一个字号, 不许参差)。
            self._fit_uniform(rows, sp(14))
            scroll.add_widget(inner)
            content.add_widget(scroll)
            # 底部口径说明(2026-09-14, 玩家要的)。⚠️ **两个「中位」不是同一个东西**:
            #   左列那个中位是**渲染帧率**(每秒画了多少帧),
            #   右列那个中位是**物理吞吐**(每秒模拟多少步) —— 两列各自取自己的中位数。
            # ⚠️⚠️ 这一段 2026-09-15 重写了(玩家: 「大家都知道什么是平均帧和1%low帧就不用你教学了」
            #    +「难点是中位跑分和波动是什么」)。三件事:
            #  ① **平均/1%Low帧 不解释** —— 常识, 占了小半屏还折行。只留这个面板**特有**的两条。
            #  ② **手工折行 + 全角空格做悬挂缩进**。不让它自动折 —— 自动折出来的续行没有缩进,
            #     三条会糊成一片(玩家截图就是这个)。手折每条约 2 行、宽度与设备无关。
            #  ③ 高度原来写死 `height=dp(88)`, 而折行后真实要 ~6 行 ⇒ **最后一行被「关闭」按钮
            #     压掉一半**(玩家截图实证)。改成 `_auto_h`: 高度跟真实排版走(本工程所有多行正文
            #     的标准做法, 这里是漏掉的一个)。`**中位数**` 那种星号是 markdown 残留, Kivy 不认,
            #     会在屏幕上原样显示 —— 强调一律用「」。
            # ⚠️⚠️ 2026-09-14 玩家定稿两处:
            #   ① **去掉开头的「口径：」三个字** —— 那两行本身就是注释, 前面再挂个标签是废话。
            #   ② ~~解释文字换成金色 `COL_BALL`~~ —— **2026-09-15 玩家又改回去了**:
            #      「标题用金色 备注用灰色, 2个颜色换下」⇒ 备注回到 `COL_SUB`, 金色挪给标题
            #      (见 `title_lbl` 上面那段)。**别照着被划掉的这条改回来。**
            #      留下来的道理是"两块颜色要分开": 备注仍然用次级色、标题用主数字色, 只是
            #      谁是谁对调了。
            foot = Label(
                text=('  中位跑分：物理引擎每秒模拟步数的中位数\n'
                      '  波动：(最大跑分-最小跑分)/中位数跑分'),
                font_size='12sp', halign='left', valign='top',
                color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
            self._auto_h(foot, dp(44))
            content.add_widget(foot)
        # ⚠️ 「清空历史」只在**有记录**时才放出来 —— 空列表上摆一个"清空"是没意义的热区,
        #    而且它离「关闭」只有 dp(8), 误触代价是**不可逆**的。
        if self.bench_history:
            _acts = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
            clear_btn = Button(text='清空历史', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_DARKRED) + (1,))
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,))
            _acts.add_widget(clear_btn)
            _acts.add_widget(close_btn)
            content.add_widget(_acts)
        else:
            close_btn = Button(text='关闭', font_size='16sp', bold=True,
                               background_normal='',
                               background_color=hex_rgb(COL_BTN_OFF) + (1,),
                               size_hint_y=None, height=dp(46))
            content.add_widget(close_btn)
        # 宽高同 _popup(): 必须吃等效竖屏窗口, 不能用 size_hint(见 _popup 的说明)
        # ⚠️ 宽 **0.86 -> 0.92**(2026-09-15 加第四列时改): 见上面列宽那段实测 ——
        #    360dp 上四列要多 55px, 加宽这一档拿回 21.6px, 其余靠缩字号。
        _vw, _vh = self._veq()
        popup = RotPopup(title='', content=content, size_hint=(None, None),
                         width=0.92 * _vw, height=0.7 * _vh,
                         auto_dismiss=True, separator_height=0)
        close_btn.bind(on_release=popup.dismiss)
        if self.bench_history:
            # ⚠️ 清空之后**当场把面板重开一次** —— 玩家要立刻看到空态, 而不是盯着
            #    一份已经删掉的旧表格。(旧面板先 dismiss, 否则会叠两层。)
            def _ask_clear(*_):
                popup.dismiss()
                self._clear_bench_history()
            clear_btn.bind(on_release=_ask_clear)
        popup.open()

    def _clear_bench_history(self):
        """清空「模拟测试历史」——**不可逆, 所以必须先过一道确认**。

        ⚠️ 玩家 2026-09-15 定案要二次确认(见 `_show_fps_cap_settings` 的 `取消/确定` 同款写法)。
        ⚠️ **只清这一张表**(`plinko_bench_history.json`)。SOC 高压历史是**另一张**
           (`plinko_hp_history.json`) —— 两张表分开是玩家 2026-09-15 定过的案, 别一起清。
        ⚠️ 确认框里**必须写清条数**, 让玩家知道要删掉多少东西(「不可恢复」这四个字不能省)。
        """
        _n = len(self.bench_history)
        if _n <= 0:
            return
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(10))
        ttl = self._fit_line(Label(text='清空模拟测试历史', bold=True, halign='center',
                                   color=hex_rgb(COL_TEXT) + (1,),
                                   size_hint_y=None, height=dp(28)), 19)
        content.add_widget(ttl)
        # ⚠️⚠️ **正文里绝不能出现 markdown 星号** —— Kivy 的 Label 不认 markdown,
        #    `**3**` 会在屏幕上**原样显示成 `**3**`**(`fx_probe` 有一条专门钉这个)。
        msg = Label(text='将删除全部 %d 条模拟测试历史，\n不可恢复。\n\n'
                         '（SOC 高压测试历史不受影响）' % _n,
                    font_size='15sp', halign='center', valign='middle',
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None)
        self._auto_h(msg, dp(90), dp(6))
        content.add_widget(msg)
        acts = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                        background_color=hex_rgb(COL_BTN_OFF) + (1,))
        ok = Button(text='确定清空', font_size='16sp', bold=True, background_normal='',
                    background_color=hex_rgb(COL_DARKRED) + (1,))
        acts.add_widget(cancel)
        acts.add_widget(ok)
        content.add_widget(acts)
        popup = self._popup(0.86, 260, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _confirm(*_):
            self.bench_history = []
            self._save_bench_history()          # 盘上也要清, 否则重启又回来了
            popup.dismiss()
            _set_label_text(self.status_lbl, '模拟测试历史已清空')
            self._show_bench_history()          # 当场重开 → 看到空态

        cancel.bind(on_release=popup.dismiss)
        ok.bind(on_release=_confirm)
        popup.open()
        self._popup_fit_content(popup, content)

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
        if getattr(self, "_bench_status_active", False):
            return
        rate = 100.0 * self.hits / self.plays if self.plays > 0 else 0
        _set_label_text(self.stats_lbl, "累计%d投%d中(%.0f%%)" % (
            self.plays, self.hits, rate))

    def set_bet(self, v, silent=False):
        self.bet = v
        self._restyle_selects()
        self._refresh_stats()
        self.sfx.play("click", throttle=0.08)
        if not silent and self.sound_mode == "on" and time.time() >= self._result_until:
            self.sfx.play("voice_bet_%d" % v, throttle=0.6)
        self._save_config()

    # ---- 期望返还比例: 常驻档 + 需要长按解锁的隐藏档 ----
    RTP_TIERS = (("80%", 0.80), ("120%", 1.20), ("200%", 2.00), ("360%", 3.60))
    # ⚠️ **隐藏档**(玩家 2026-09-11 定稿的入口): 长按"期望返还比例"标签 -> 弹窗选一个 ->
    #    **不保存**。长按时长见 `RTP_UNLOCK_HOLD`(2026-09-12: 5s -> 3s)。
    #    所以它只是内存里的一个开关: ① 存档那侧的档位白名单**不含** 50.0(重开时那份存档被忽略);
    #    ② `_boards` 里那份盘面也无所谓(选不中就用不到)。
    # 彩蛋档位(长按"期望返还比例"解锁, **不保存**)。弹窗让玩家从四选一里挑(或"关闭隐藏")。
    # ⚠️ 三档**全部"必中"**(plinko.py 的 K_DIST = {9: 1.0}), 所以每格均值被恒等式钉死在
    #    档位上 —— 实际落格分布见 plinko.py 的 VALUE_SHAPE 注释。
    RTP_HIDDEN = (("1000%", 10.0), ("2000%", 20.0), ("5000%", 50.0))
    RTP_UNLOCK_HOLD = 3.0      # 长按多久触发隐藏档弹窗(5.0 -> 3.0, 玩家 2026-09-12 定稿)

    def _reflow_row_budget(self):
        '''档位按钮增删之后重跑一次宽度预算(包在 try 里: 探针夹具只搭了半套控件)。'''
        try:
            self._apply_row_budget(self._ui_scale, self._font_scale * self._ui_scale)
        except Exception:
            pass

    def _add_rtp_button(self, label, val):
        """往返还率那一行插一个按钮 —— 插在"右侧留空"的 Widget 之前(视觉上接在最右那个后面)。"""
        b = self._mk_button(label, lambda _b, t=val: self.set_rtp(t))
        b.size_hint_x = None
        b.width = dp(56)
        b.size_hint_y = 1.0
        # ⚠️ `_fit_base` 必须在这里就给对: `_apply_sizes` 只给**当时已存在**的按钮写过它,
        #    而隐藏档是**运行期**(长按解锁)新建的。不给的话 `_fit1` 会退回
        #    `float(b.font_size)` —— 那是裸的 16.0(不含 _font_scale*_ui_scale), 而且会被
        #    **缓存**成基准: 实测 320dp 上解锁后隐藏档按钮 `_fit_base=16.0`/font=11.20,
        #    而同排四个常驻档是 12.709/11.95 —— 就它一个跟别人参差, 一直到下次窗口尺寸
        #    变化才被纠正。(对抗性复核挖出来的。)
        b._fit_base = (sp(16) * float(getattr(self, "_font_scale", 1.0) or 1.0)
                       * float(getattr(self, "_ui_scale", 1.0) or 1.0))
        self.rtp_btns[val] = b
        try:
            idx = self._rtp_row.children.index(self._rtp_spacer) + 1
        except (ValueError, AttributeError):
            idx = 1
        self._rtp_row.add_widget(b, index=idx)
        self._reflow_row_budget()
        return b

    def _rtp_is_hidden(self, t):
        """t 是不是隐藏档 —— 判据走 RTP_HIDDEN, 不手抄数字(见 _all_rtp 的血泪注释)。"""
        return any(abs(t - v) < 1e-6 for _lab, v in self.RTP_HIDDEN)

    def _remove_rtp_button(self, val):
        """把一个档位的按钮从返还率那排摘掉 —— `_add_rtp_button` 的逆操作。

        ⚠️ 两处必须**一起**改: ① `rtp_btns` 字典 ② `_rtp_row` 的 children。
           只删字典 -> 按钮还画在屏幕上、点下去照样切档(玩家看到"关了还在");
           只删 children -> `_restyle_selects`/`_set_controls_enabled` 还在遍历它(白干不报错),
           而且下次 `_unlock_rtp` 走 `val in self.rtp_btns` 直接 return, 按钮永远加不回来。
        ⚠️ `_rtp_spacer` 绝不碰 —— 它是 `_add_rtp_button` 的定位锚点。
        `pop(val, None)` 让"本来就没这个按钮"(冷启动直接点「关闭隐藏」)天然是空操作。
        """
        b = self.rtp_btns.pop(val, None)
        if b is not None:
            self._rtp_row.remove_widget(b)
        self._reflow_row_budget()

    def _close_rtp_hidden(self, silent=False):
        """关掉隐藏档: 摘掉**全部**隐藏档按钮, 返还比例回到最高常驻档。

        ⚠️ `was_hidden` 必须在**动任何东西之前**判掉 —— 先切 360% 的话再判就恒假,
           那个信息丢了, 症状是"选了关闭却什么都没发生"。
        ⚠️ 本来就是常驻档 -> **什么都不做**(不跳 360%)。玩家 2026-09-12 点名的细节。
        ⚠️ 摘的是**全部**隐藏档按钮(不只是当前那个), 收完那排就只剩 80/120/200/360。
        ⚠️ 切回的是 `max(RTP_TIERS)`, **不写死 3.60**(同一份清单原则, 见 _all_rtp)。
        ⚠️ `_boards` 一个字都不动: 摘的是"按钮"不是"盘面", 删键 = 复刻 v0.6.47 的 KeyError 闪退。
        """
        was_hidden = self._rtp_is_hidden(self.rtp_target)
        was_tier = self.rtp_target          # 语音要念"被关掉的是哪一档", 必须在 set_rtp 之前存
        for _lab, v in self.RTP_HIDDEN:     # 不手抄, 加一档自动跟着摘
            self._remove_rtp_button(v)
        if not was_hidden:
            return
        self.set_rtp(max(v for _lab, v in self.RTP_TIERS), silent=True)
        # ⚠️ `silent=True` 是必须的: 切回 360% 那句 `voice_rtp_360` 与新语音同族, 两条都走
        #    普通路径的话 `Sfx.play` 的互斥会按"上一句真实时长"静默挤掉一条 —— 靠调用顺序
        #    兜底太脆(谁调换两行就回归), 声明式地只留一个新语音出口。click 在语音分支之前,
        #    不受影响, 按钮反馈不丢。
        if not silent and self.sound_mode == "on":
            self.sfx.play("voice_rtp_hide_%d" % int(was_tier * 100), throttle=0.6)

    def _unlock_rtp(self, val):
        """应用玩家选中的档位(幂等): 先摘掉**别的**隐藏档按钮(只留当前一个), 再切过去。

        ⚠️ 这里原来写的是 `self.RTP_HIDDEN[0][1]`(写死第一档) —— 弹窗改成多选一之后
        必须收参数, 否则选 2000% 也会跳到 1000%。
        ⚠️ 原来还在这里置 `_rtp_unlocked = True` —— 那个标志已随"每次都弹"整个删除。
        ⚠️ "只留当前一个"是玩家 2026-09-12 定的: 开过 1000% 又改选 2000% 时, 那排上
        不该同时挂着两个隐藏档按钮。
        """
        for _lab, v in self.RTP_HIDDEN:
            if v != val:
                self._remove_rtp_button(v)
        if val not in self.rtp_btns:
            for label, v in self.RTP_HIDDEN:
                if v == val:
                    self._add_rtp_button(label, v)
        self.set_rtp(val)

    def _ask_unlock_rtp(self):
        """长按"期望返还比例"3 秒: 四选一 + 确认(玩家 2026-09-12 定稿)。

        与旧版的四点差异:
          ① **每次都弹** —— 旧版靠 `_rtp_unlocked` 守卫, 一局只弹一次;
          ② 点选项只**选中**(高亮), 点「确定」才生效 —— 旧版点了立即生效、没有回头路;
          ③ 「关闭隐藏」能把隐藏档按钮**摘掉** —— 旧版只有"加", 没有"减";
          ④ 默认选中**当前档位**(当前是常驻档 -> 选中「关闭隐藏」)。

        档位文字**从 RTP_HIDDEN 取**, 不写死 —— 这里写死过一次(5000%), 隐藏档改定稿时
        漏改, 弹窗一直显示上一版的数字(玩家 2026-09-11 截图报的就是这个)。
        """
        if self._rtp_popup is not None:
            # 防重入: `_on_title_touch_down` 是 **Window 级触摸观察者**, 模态弹窗拦不住它
            # (它只看坐标) —— 弹窗开着时再长按会叠出第二个。闸门开在"建弹窗"这一端。
            return
        content = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(8))
        tip = Label(text='（重启游戏后隐藏返还率失效，需重新激活）',
                    font_size='14sp', halign='center', valign='middle',
                    color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(28))
        # 这行 22 个汉字要 320px, 而 360dp 机器上只有 312px(系统字体放大更宽) ⇒
        # 原来"定高 28 + text_size=w.size"会把折出来的第二行**裁掉**。折行就长高。
        self._auto_h(tip, dp(28), dp(4))

        # 四个选项**横向**一排(玩家定稿)。第一项是"不要隐藏档", 文字固定; 后三项**从
        # RTP_HIDDEN 生成**, 加一档就自动多一个按钮。
        opts = [(None, '关闭隐藏')] + [(v, lab) for lab, v in self.RTP_HIDDEN]
        sel = [self.rtp_target if self._rtp_is_hidden(self.rtp_target) else None]
        btns = {}

        def _restyle():
            for k, b in btns.items():
                b.background_color = hex_rgb(COL_BTN if k == sel[0] else COL_BTN_OFF) + (1,)

        def _pick(k):
            def _go(*_):
                sel[0] = k
                _restyle()
            return _go

        row = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(4))
        for key, text in opts:
            b = Button(text=text, font_size='18sp', bold=True, background_normal='',
                       background_down='')
            # Kivy 的 Button **不换行也不缩**: 装不下就**盖到隔壁按钮上**。"关闭隐藏"
            # 4 个汉字在 360dp 机器上只有 75.2dp(18sp 要 72dp, 余量仅 3.2dp; 系统字体
            # 1.15 倍就超) ⇒ 接进单行自适应, 只有真的装不下时才缩。
            b._fit_base = float(b.font_size)
            self._install_fit(b, )
            b.bind(on_release=_pick(key))
            btns[key] = b
            row.add_widget(b)
        content.add_widget(row)
        # 说明行放在**选项和「确定」之间**(玩家 2026-09-12 定稿): 先选, 选完往下看到
        # 说明, 再看到确定 —— 顺序跟玩家的动作顺序一致。放最上面时它挤在标题下面,
        # 容易被当成"副标题"跳过。
        content.add_widget(tip)
        _restyle()                                   # 建完立刻上默认高亮

        ok_btn = Button(text='确定', font_size='18sp', bold=True, background_normal='',
                        background_down='', background_color=hex_rgb(COL_DARKRED) + (1,),
                        size_hint_y=None, height=dp(52))
        content.add_widget(ok_btn)
        # 尺寸 0.98 / h_dp=265 是**算出来再用截图量过的**(不是拍的)。字号从 16sp 提到 18sp
        # 之后, 四个汉字("关闭隐藏")要 4x18 = 72dp, 而按钮宽 = `hint_w*vw - 24(外壳内边距,
        # 见 kivy/data/style.kv 的 GridLayout(padding:'12dp')) - 2*pad - 3*spacing` 再 /4
        # —— 360dp 机器上 0.92/10/4 只给 68.8dp, **放大字号就会溢出**(Kivy 的 Button 没有
        # text_size, 不会自动换行, 超了直接盖到隔壁按钮上)。现在 0.98/8/4 给 75.2dp。
        # 改文案/加档位/改字号都要重算这个。
        popup = self._popup(0.98, 265, title='隐藏返还率', content=content,
                            auto_dismiss=True,          # 同"每轮游戏次数设定": 点外面关掉且不生效
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            title_size='19sp',
                            separator_color=hex_rgb(COL_DIV) + (1,))

        def _confirm(*_):
            popup.dismiss()
            if sel[0] is None:
                self._close_rtp_hidden()
            else:
                self._unlock_rtp(sel[0])

        ok_btn.bind(on_release=_confirm)
        # 绑 on_dismiss(而不是绑按钮)与 _show_easter_popup 同一套路: 点外部/系统关掉也能
        # 放行, 顺带把引用留给探针查"还开着吗" —— 它也是防重入闸门的唯一解锁点。
        popup.bind(on_dismiss=lambda *_: setattr(self, '_rtp_popup', None))
        self._rtp_popup = popup
        popup.open()
        self._popup_fit_content(popup, content)

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
        self._set_game_status("已重置")
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
        self._set_game_status("蓄力中")

    def launch(self):
        if self.state != "charging":
            return
        _SINCE_LAUNCH[0] = 0              # 发射那一刻归零, 后面每帧 +1(判 C6)
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
            self._set_game_status("力度不足,未扣弹珠")
            return
        frozen_power = self.power  # 在清零前保存, 用于音量/震动分级
        self.balance -= self.bet
        # 发射: 弧面垂直抖动 ±6px(每发随机), 纯物理飞行(无预演/无渲染修正)。
        # ⚠️⚠️ **跑分: 连碰撞随机流也换成按球号派生的确定流**(2026-09-14, 玩家提的)。
        #    `physics_step` 里撞钉/撞墙/隔板的扰动都写成 `rng = getattr(b, "_rng", None) or random`
        #    —— 正常发射 `_rng` 是 None ⇒ **走全局 `random`**。而全局流的**抽取顺序**受
        #    "球什么时候落袋"(真实时间)影响 ⇒ 光在场次开始时 seed 一次**不够**, 后面的数会错位
        #    (实测: 连跑两轮, 帧数与装杯帧数都不一样)。
        #    给它一条**自己的流**, 抽取顺序就与时间无关 ⇒ **球路逐帧可复现**。
        #    ⚠️ 种子按**球号**派生, 不是每发同一个 —— 否则 5 发会走出一模一样的轨迹、
        #    落在同一个格子里, 那就只测了一种球路, 反而不如"随机的 5 发"。
        #    ⚠️ 只影响跑分: 正常发射照旧 `_rng=None`(走全局流, 每球天然不同)。
        _brng = None
        if getattr(self, "_bench_running", False):
            _brng = random.Random(BENCH_SEED + 1000 + int(getattr(self, "_bench_ball_i", 0)))
            arc_dy = _brng.uniform(-6.0, 6.0)
        else:
            arc_dy = random.uniform(-6.0, 6.0)
        self.geo["deflectors"] = [(x1, y1 + arc_dy, x2, y2 + arc_dy)
                                  for (x1, y1, x2, y2) in self._base_deflectors]
        self.ball = launch_ball(frozen_power, rng=_brng)
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
        self._set_game_status("发射!")

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
        # ⚠️ **统计不在这一帧写**(2026-09-14, v0.7.26)。这一帧是"落袋那一拍" ——
        #    灯/槽闪/音效/余额/揭晓全挤在这里, 而写文字 = 重排一次字形纹理(真机 4~7 毫秒)。
        #    真机日志里 10 个低于 90fps 的帧有 7 个落在这批上。
        #    挪后 0.20 秒: 这段时间 HUD 正被装杯压暗、玩家也还在看落袋, 数字晚 0.2 秒无感;
        #    而 `_land_hold` 最小 0.3 秒(见下面那行注释), 所以**挪不到下一发去**。
        #    ⚠️ 别改成 0(下一帧) —— 下一帧仍在落袋窗口里, 白挪。
        #    ⚠️ 也别挪进 `_reveal_win`: 那是揭晓合流的地方, 统计跟它没关系,
        #    混进去会重演"揭晓时序"那类事故(这个模块历史上丢过中奖音、提前剧透过数字)。
        Clock.schedule_once(lambda _dt: self._refresh_stats(), 0.20)
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
            # 精简(玩家: 「把那几个换行的文字精简下」): 去掉尾省略号 —— "结算中"本身已含进行义,
            # 128px 收到 112px, 于是 360dp 的右侧份额(110px)里也塞得下, 不必再靠缩字号。
            self._set_game_status("命中 x%d · 结算中" % m)   # 中间态: 第一秒不发空, 余额"冻结"不像 bug
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
            #      · 跑分那 5 发之间, `_auto_launch_tick` 要等 state 回 ready 才发下一发,
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
                # auto_close: **跑分期间**不等玩家点击(跑分是自动连续发射的, 等人点击会把
                # 整轮跑分卡死); 正常玩则停在装满状态等点击(玩家 2026-09-12 定稿)。
                if not self.game_area.win_fx.play_win(m, self.bet, on_done=_on_settled,
                                                      auto_close=getattr(self, "_bench_running", False)):
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
            self._set_game_status("未中")
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
            # 那个双空格是笔误, 收成单空格(119 -> 116px)
            self._set_game_status(("中奖! +%d (x%d)" % (payout, m)) if payout > 0 else "未中")
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

    def _regular_rtp(self):
        """常驻档(可持久化的那批) —— 从 RTP_TIERS 派生, **不手抄**。

        用途只有一个: `_load_config` 的读档白名单。派生而非手写 ⇒ 隐藏档**结构上**
        不可能被读回来, 这就是"隐藏档不保存"的实现点(保存侧照常写盘, 读侧丢弃)。
        """
        return tuple(v for _lab, v in self.RTP_TIERS)

    def _all_rtp(self):
        """所有档位(常驻 + 彩蛋) —— **唯一真源**, 别在各处再手写一份元组。

        ⚠️ 这里是真的踩过坑(2026-09-12 线上闪退): `_boards` 的初始化 和 `park_ball` 的
        重刷**各写了一份手抄档位列表**, 加彩蛋档时只改到其中一处 —— 另一处漏掉之后,
        切到 2000%/5000% 再发射一次, `self._boards[self.rtp_target]` 就抛 KeyError 闪退。
        1000% 当时没事, 纯粹因为它早就在那行手抄列表里。凡"同一份清单出现在两处"必出事。
        """
        return (tuple(v for _l, v in self.RTP_TIERS)
                + tuple(v for _l, v in self.RTP_HIDDEN))

    def park_ball(self, reroll=True, silent=False):
        """重掷盘面(reroll=True), 新球停到柱塞, 回 ready。哑火 reroll=False 防免费刷盘。"""
        # ⚠️ 跑分期间**不重掷**(2026-09-14): 盘面由 `_auto_launch_tick` 按 `BENCH_BOARD` 钉死,
        #    这里再掷一次会把刚钉好的覆盖掉(而且每发白掷一遍、画面还会闪一下)。
        if reroll and not getattr(self, "_bench_running", False):
            self._boards = {r: roll_multipliers(r) for r in self._all_rtp()}   # 各档盘面一起刷新
            self.multipliers = self._boards[self.rtp_target]
            # ⚠️ **只更新倍率槽**, 不整块重画(2026-09-14): 重掷时几何一点没变 ——
            #    钉子/隔板/墙/槽底/力条全在原位, 变的只有 9 个槽的颜色与文字。
            #    真机实测: 整块 `_redraw()` 的 `重掷` 一段是 **9.2 / 7.6 毫秒**, 而它落在
            #    "球落定 -> ready"那一帧上, 是最慢三帧里的头号子步骤。
            #    `_update_slots()` 结构对不上时会自己退回完整 `_redraw()`, 不半更新。
            self.game_area._update_slots()
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
        # 出口是**玩家点击**(win_fx 的手动收尾, 见 request_close); 轮询在 FX 未 idle 期间
        # 持续, **没有算术上界** —— 每次只做一次 id 比较 + 一次 schedule_once, 开销可忽略。
        # 玩家一直不点, 弹窗就和他一起等。
        # ⚠️ 别改成"到点自己弹": 那会让弹窗盖在**还立着的杯子**上。
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
        content = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        # 三个孩子**全部定高**(size_hint_y=None) ⇒ `content.minimum_height` 就是精确值,
        # 弹窗高度由 `_popup_fit_content` 按它反算。原来正文是**弹性**孩子: 400dp 机器上
        # 它只分到 248px 可用宽, 而最长那句要 253px ⇒ 折成 4 行(96px)塞在 56px 的格子里,
        # 末行直接压在「确定」上 —— 玩家报的就是这个。
        title = self._fit_line(Label(text="弹珠返回发射槽", bold=True, halign="center",
                                     color=hex_rgb(COL_METER) + (1,),
                                     size_hint_y=None, height=dp(44)), 28)
        # 标点与全 app 一致: 夹在中文之间的逗号用全角「，」(实测 249->256px, 仍放得下)
        msg = Label(text="弹珠未落入倍率槽，已回到发射槽。" + chr(10)
                         + "本局按 ×2 结算，返还 %d 个弹珠。" % (2 * self.bet),
                    font_size="16sp", halign="center", valign="middle",
                    color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(48))
        self._auto_h(msg, dp(24), dp(6))
        ok_btn = Button(text="确定", font_size="16sp", bold=True,
                        background_normal="", background_down="",
                        background_color=hex_rgb(COL_BTN) + (1,),
                        color=(1, 1, 1, 1), size_hint_y=None, height=dp(48))
        content.add_widget(title)
        content.add_widget(msg)
        content.add_widget(ok_btn)
        # 宽度 0.78 -> 0.90, 内边距 20 -> 16: 正文那两句各 16 个汉字, 16sp 下要 253px。
        # 老参数在 400dp 机器上只剩 248px、在 360dp 上只剩 216px, **必然折行**; 现在 360dp
        # 上也有 268px(余量 15)。
        # ⚠️ `separator_height=0` 不能漏: 这个弹窗的标题在**内容里**, Popup 自带的标题栏
        #    是空的, 但不关分隔条的话那条线还画着 —— 于是它飘在标题上方, 而另外三个同样
        #    "无标题栏"的弹窗(跑分菜单/跑分历史/启动信息)都显式关了。六个弹窗该长一个样。
        popup = self._popup(0.90, 280, title="", content=content,
                            auto_dismiss=False,
                            title_color=hex_rgb(COL_TEXT) + (1,),
                            separator_color=hex_rgb(COL_DIV) + (1,),
                            separator_height=0)
        ok_btn.bind(on_release=lambda *_: popup.dismiss())
        # 绑 on_dismiss 而不是按钮: 将来多一条关闭路径(手势/系统)也不会漏掉解锁。
        # 顺便把弹窗引用留给探针用。
        popup.bind(on_dismiss=self._on_easter_closed)
        self._easter_popup = popup
        self._popup_fit_content(popup, content)
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

    # ---- SOC 高压测试的独立历史(玩家 2026-09-15: 「高压测试也专门搞个 log 记录」) ----
    # ⚠️ 与性能测试历史**同一套存取路子**(同目录、同 JSON 套路), 只是另一个文件。
    #    分开存是因为量纲不同(峰值 vs 衰减), 挤一张表只会互相污染。
    def _hp_history_path(self):
        """SOC 高压测试历史 JSON 路径(与性能测试历史同目录)。"""
        if platform == "android":
            try:
                base = App.get_running_app().user_data_dir
            except Exception:
                base = tempfile.gettempdir()
        else:
            base = tempfile.gettempdir()
        return os.path.join(base, "plinko_hp_history.json")

    def _load_hp_history(self):
        try:
            with open(self._hp_history_path(), "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.hp_history = data[-100:]
        except Exception:
            pass

    def _save_hp_history(self):
        try:
            with open(self._hp_history_path(), "w") as f:
                json.dump(self.hp_history[-100:], f)
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
                # ⚠️ 白名单走 `_regular_rtp()`(从 RTP_TIERS 派生), **不手抄** —— 这里原本是
                #    硬编码的常驻档白名单, 正是 v0.6.47 闪退事故的同款形状
                #    (同一份档位清单出现在两处, 改档位表时漏改一处就静默出错)。
                #    派生之后"隐藏档读不回来"是**结构性**的, 不靠谁记得改这一行。
                if (isinstance(cfg.get("rtp_target"), (int, float))
                        and cfg["rtp_target"] in self._regular_rtp()):
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
                if (isinstance(cfg.get("fps_cap_setting"), int)
                        and cfg["fps_cap_setting"] in FPS_CAP_OPTIONS):
                    self.fps_cap_setting = cfg["fps_cap_setting"]
        except Exception:
            pass
        # (原来这里按读回的 sound_mode 决定是否 set_enabled(False); 现在不读档, 恒为 on, 删)
        if self.round_plays >= self.max_plays:
            self._auto_reset_on_start = True   # UI还没建, 延后到 _build_ui 之后

    def _save_config(self):
        """存设定。**默认走工作线程**(见 `_cfg_post` 处说明), 建不起线程就同步写。"""
        try:
            cfg = {
                "max_plays": self.max_plays,
                "rtp_target": self.rtp_target,
                "bet": self.bet,
                "balance": self.balance,
                "round_plays": self.round_plays,
                "plays": self.plays,
                "hits": self.hits,
                "fps_cap_setting": int(self.fps_cap_setting),
            }
            path = self._config_path()          # 路径在主线程算好(App 不能从工作线程问)
            if _cfg_post(cfg, path):
                return
            with open(path, "w") as f:          # 兜底: 与改之前逐字相同
                json.dump(cfg, f)
        except Exception:
            pass

    def _set_fps_cap_setting(self, cap):
        """写入用户帧率档位，并立即按三重上限重算实际目标。"""
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            return False
        if cap not in FPS_CAP_OPTIONS:
            return False
        self.fps_cap_setting = cap
        _FPS_USER_CAP[0] = cap
        try:
            _apply_fps_cap()
        except Exception:
            pass
        self._save_config()
        return True

    def _show_fps_cap_settings(self):
        """帧率上限设定：拖动滑条选档，确认后持久化并立即重申 Android 高刷请求。"""
        from kivy.uix.slider import Slider

        values = FPS_CAP_OPTIONS
        current = self.fps_cap_setting if self.fps_cap_setting in values else FPS_CAP_DEFAULT
        picked = [current]
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        title = self._fit_line(Label(text='帧率上限设定', bold=True, halign='center',
                                     color=hex_rgb(COL_TEXT) + (1,),
                                     size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title)
        value_lbl = Label(text='当前设定：%d Hz' % current, font_size='20sp', bold=True,
                          halign='center', valign='middle', color=hex_rgb(COL_FIRE) + (1,),
                          size_hint_y=None, height=dp(34))
        value_lbl.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
        content.add_widget(value_lbl)
        slider = Slider(min=0, max=len(values) - 1, step=1,
                        value=values.index(current), size_hint_y=None, height=dp(42))
        content.add_widget(slider)
        ticks = BoxLayout(size_hint_y=None, height=dp(20))
        for hz in values:
            tick = Label(text=str(hz), font_size='12sp', halign='center', valign='middle',
                         color=hex_rgb(COL_SUB) + (1,))
            tick.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
            ticks.add_widget(tick)
        content.add_widget(ticks)
        hint = Label(text='实际帧率上限 = min（屏幕支持，安卓系统设定的上限，当前设定的上限）',
                     font_size='13sp', halign='center', valign='middle',
                     color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(42))
        hint.bind(size=lambda w, *_: setattr(w, 'text_size', w.size))
        content.add_widget(hint)
        actions = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        cancel = Button(text='取消', font_size='16sp', bold=True, background_normal='',
                        background_color=hex_rgb(COL_BTN_OFF) + (1,))
        confirm = Button(text='确定', font_size='16sp', bold=True, background_normal='',
                         background_color=hex_rgb(COL_BTN) + (1,))
        actions.add_widget(cancel)
        actions.add_widget(confirm)
        content.add_widget(actions)
        popup = self._popup(0.88, 310, title='', content=content,
                            auto_dismiss=True, separator_height=0)

        def _pick(_slider, value):
            picked[0] = values[int(round(value))]
            value_lbl.text = '当前设定：%d Hz' % picked[0]

        def _confirm(*_):
            if self._set_fps_cap_setting(picked[0]):
                self.game_area.center_toast('帧率上限已设为 %d Hz' % picked[0],
                                            hexcolor=COL_GREEN, size=20, life=1.3)
            popup.dismiss()

        slider.bind(value=_pick)
        cancel.bind(on_release=popup.dismiss)
        confirm.bind(on_release=_confirm)
        popup.open()
        self._popup_fit_content(popup, content)

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
                    markup=True, color=hex_rgb(COL_TEXT) + (1,),
                    size_hint_y=None, height=dp(96))
        self._auto_h(lbl, dp(72), dp(8))
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
        self._popup_fit_content(popup, content)
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
        lbl = self._fit_line(Label(text="每轮游戏次数：", halign="left", valign="middle",
                                   color=hex_rgb(COL_SUB) + (1,),
                                   size_hint_y=None, height=dp(28)), 16)
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
        hist_lbl = self._fit_line(Label(text="最近完成的轮次：", halign="left", valign="middle",
                                        color=hex_rgb(COL_SUB) + (1,),
                                        size_hint_y=None, height=dp(28)), 15)
        content.add_widget(hist_lbl)
        # 一行一条, 每条自己**单行自适应**。原来是"一个大 Label 用 \n 拼", 而
        # "最近第1轮  每轮50次  剩 17405 个弹珠" 要 255px、360dp 机器上只有 250px ⇒
        # 每条都折成两行(白占一倍高度, 看着像坏掉了)。顺带把"最近"两字去掉(表头已经说了)。
        inner = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        inner.bind(minimum_height=inner.setter("height"))
        _rows = []
        if self.round_history:
            for i, r in enumerate(reversed(self.round_history[-100:])):
                _rows.append(Label(
                    text="第%d轮  每轮%d次  剩 %d 个弹珠" % (i + 1, r["plays"], r["balance"]),
                    font_size="15sp", halign="left", valign="middle",
                    color=hex_rgb(COL_TEXT) + (0.7,), size_hint_y=None, height=dp(26)))
        else:
            _rows.append(Label(
                text="暂无完成的轮次记录", font_size="15sp", halign="left", valign="middle",
                color=hex_rgb(COL_TEXT) + (0.7,), size_hint_y=None, height=dp(26)))
        for _r in _rows:
            _r.bind(width=lambda w, *_: setattr(w, "text_size", (w.width, None)))
            inner.add_widget(_r)
        self._fit_uniform(_rows, sp(15))     # 绝对字号, 必须过 sp()(见 _show_bench_history)
        scroll = ScrollView(size_hint=(1, 1), bar_width=dp(6))
        scroll.add_widget(inner)
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
        self._row_rtp.padding    = [dp(14), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_bets.padding   = [dp(14), dp(4) * uv, dp(10), dp(4) * uv]
        self._row_bottom.padding = [dp(6), dp(4) * uv, dp(12), dp(4) * uv]
        self.padding = [0, 0, 0, dp(12)]  # 底部留白

        # ---- 基准字号: 与原来逐条一致(sp(N) * fs), 紧接着全部交给 `_fit1` 定档 ----
        # `_fit1` 只会在**装不下**时往下调(最低 0.7 倍), 装得下就原样保持 —— 所以宽屏/
        # 正常字体下与改动前逐像素相同, 只有"会折行/会溢出"的那些才会变。
        for _w, _base in ((self.title_lbl, sp(18) * fs),
                          (self.status_lbl, sp(13) * fs),
                          (self.mute_btn, sp(13) * fs),
                          (self.round_btn, sp(13) * fs),
                          (self._rtp_title_lbl, sp(14) * fs),
                          (self._bet_title_lbl, sp(14) * fs),
                          (self._bead_lbl, sp(15) * fs),
                          (self.balance_lbl, sp(19) * fs),
                          (self.stats_lbl, sp(15) * fs),
                          (self.power_lbl, sp(14) * fs),
                          (self.reset_btn, sp(16) * fs),
                          (self.fire_btn, sp(16) * fs)):
            _w.font_size = _base
            _w._fit_base = _base
        for b in list(self.rtp_btns.values()) + list(self.bet_btns.values()):
            b.font_size = sp(16) * fs
            b._fit_base = b.font_size

        self._apply_row_budget(us, fs)

    def _apply_row_budget(self, us, fs):
        '''重算"顶栏 / 两条档位行 / 信息行 / 底行"的**宽度预算**, 再定一遍字号。

        ⚠️ 单独成方法是为了让**档位按钮增删之后能再跑一次** —— 长按解锁隐藏档会在返还率
           那行**多插一个按钮**, 关闭隐藏又摘掉。插/摘都会改这一行需要的总宽, 而原来
           `_add_rtp_button` 把新按钮宽**写死** `dp(56)`、常驻档按钮的宽却是这里反算出来的
           (360dp 机器上 ≈45dp): 一解锁, 那一行从"恰好铺满"变成**右溢 61dp**, 最右那个
           隐藏档按钮只剩一小半在屏内 —— 玩家报的"隐藏返还率几乎看不到"就是**被推出屏幕**,
           不是"看不清"。所以增删之后必须重跑一遍(见 `_reflow_row_budget`)。
        '''
        # 顶栏宽度预算: 标题**按自己的字量**定宽(不折行), 左右两块各拿 (行内宽 - 标题 - 2间距)/2
        # —— 两边**强制等宽** ⇒ 标题居中。左边两个按钮的宽由这份份额反算(上限是设计值 58/62),
        # 于是窄屏上收的是按钮、不是标题的字号。
        _tp = self._row_top.padding                    # Kivy 已展开成 [l, t, r, b]
        _top_inner = (self._row_top.width or self.width) - _tp[0] - _tp[2]
        _gap = self._row_top.spacing
        _title_w = text_px(self.title_lbl.text, sp(18) * fs, True) + dp(6)
        self.title_lbl.width = _title_w
        _share = max(dp(56) * us, (_top_inner - _title_w - _gap * 2) / 2.0)
        self.mute_btn.width    = dp(58) * us
        self.round_btn.width   = dp(62) * us
        self._top_left.spacing = _gap
        _need = self.mute_btn.width + _gap + self.round_btn.width
        if _need > _share:                             # 窄屏: 两个按钮等比收, 保住标题字号
            _k = max(0.62, (_share - _gap) / max(1.0, _need - _gap))
            self.mute_btn.width  = dp(58) * us * _k
            self.round_btn.width = dp(62) * us * _k

        # 「返还比例」「投入」两行: 标签宽**按它自己的字量**(原写死 115dp, 实测字只要 98),
        # 档位按钮宽**由行宽反算**。原来按钮一律 dp(56): 整行最小要 364dp, 而 360dp 机器上
        # 只有 312 —— 最右那个按钮(360% / 100个)被推出屏幕 47dp。现在整行恰好铺满。
        # 全角"："的字身自带宽空白(墨迹只占左边一小半), 所以宽度量到字宽就够了, 再垫 4dp
        # 就已经"贴上去"了。原来写死 dp(115) 而字只要 98 —— 那 17dp 白占, 又正好把
        # 最右那个档位按钮挤出屏幕。
        _lbl_w = max(text_px(self._rtp_title_lbl.text, sp(14) * fs),
                     text_px(self._bet_title_lbl.text, sp(14) * fs)) + dp(4)
        self._rtp_title_lbl.width = _lbl_w
        self._bet_title_lbl.width = _lbl_w
        for _rw in (self._row_rtp, self._row_bets):
            # ⚠️ 预算吃的是**行的真实宽度**, 而 `_apply_sizes` 是在"窗口尺寸变了"那一帧跑的
            #    —— 那一刻 `_row.width` 还是**上一次布局**的值(实测 360dp 机器上会按 540 算,
            #    按钮宽 66 而不是 52, 最右那个"100个"被推出屏幕)。绑到行宽上就自洽了:
            #    布局一落定就重算一次, 与"窗口变化"这个触发时机彻底解耦。
            # ⚠️⚠️ **只能绑一次**(`_budget_bound` 守着)。Kivy 的 `bind` 只追加、不去重,
            #    而这条回调会调回 `_apply_row_budget`, 于是每跑一次就再挂一条新的 ——
            #    实测 observer 数 21 -> 85 -> 341 -> 1365(**每变一次宽就 ×4**),
            #    一次宽度变化要跑几千遍(单遍 0.046ms) => 转屏 / 分屏拖拽 / 折叠屏开合时
            #    整帧卡 1~4 秒, 而且**只增不减、越玩越糟**。这是对抗性复核挖出来的。
            if not getattr(_rw, "_budget_bound", False):
                _rw._budget_bound = True
                _rw.bind(width=lambda *_a: self._reflow_row_budget())
        for _row, _btns in ((self._row_rtp, self.rtp_btns),
                            (self._row_bets, self.bet_btns)):
            _n = len(_btns)
            if not _n:
                continue
            _pad = _row.padding                     # Kivy 已展开成 [l, t, r, b]
            _avail = (max(_row.width, self.width) if self.width else _row.width)                 - _pad[0] - _pad[2]
            _gap = _row.spacing
            # 两行的孩子都是 n+2 个(标签 + 一个吃余量的空白 + N 个档位按钮)
            # ⇒ 间距有 n+1 段; 按钮把余量吃干净, 那个空白弹簧自然收到 0。
            _bw = (_avail - _lbl_w - _gap * (_n + 1)) / float(_n)
            # ⚠️ **上下都要夹**: 下界防窄屏把按钮压没, 上界防宽屏把它撑爆。
            #    联想 Y700 的等效竖屏内容列有 792dp(手机的近两倍), 只按下界的话实测
            #    返还率按钮 127dp/个、投入按钮 160dp/个(设计值 56) —— 一排巨无霸。
            #    夹到 dp(72) 之后: 400dp 上仍是算出来的 62.3(不变)、457dp 上 76.5 -> 72,
            #    平板上的富余交回给"吃余量的空白弹簧", 正好回到原本的左对齐构图。
            _bw = min(max(dp(34) * us, _bw), dp(72) * us)
            for b in _btns.values():
                b.width = _bw
            # 一排按钮**共用一个字号**(见 `_fit_buttons_uniform` 的说明)
            self._fit_buttons_uniform(list(_btns.values()), sp(16) * fs,
                                      inset=dp(6) * us)

        # 信息行: "弹珠："按字量, 余额/统计靠 `_fit1`(余额 8 位数以上时会缩, 而不是折行)
        self._bead_lbl.width = text_px(self._bead_lbl.text, sp(15) * fs) + dp(4)

        # 底行: 定宽件按原尺寸, 只把按钮文字也接进自适应(系统大字体下不溢出)
        self.reset_btn.width = dp(96)  * us
        self.fire_btn.width  = dp(110) * us
        # 力度标签**按它自己的字量**给宽 —— 它现在是空的(全工程只有一处赋值, 赋的还是空串),
        # 而它白占 dp(100): 底行定宽件 + 间距 + 内边距 = 366dp, 340dp 以下的机器上
        # "蓄力发射"会被推出屏幕(实测 320dp 右溢 28dp、300dp 右溢 39dp)。
        # 空串给 0 宽, 以后真往里写力度也就自动有位置了。
        self.power_lbl.width = (text_px(self.power_lbl.text, sp(14) * fs) + dp(6)
                                if self.power_lbl.text else 0.0)

        # ---- 定档: 上面所有宽度都落定之后, 再算字号(顺序不能反) ----
        for _w in (self.title_lbl, self.status_lbl, self.mute_btn, self.round_btn,
                   self._rtp_title_lbl, self._bet_title_lbl, self._bead_lbl,
                   self.balance_lbl, self.stats_lbl, self.power_lbl,
                   self.reset_btn, self.fire_btn):
            self._fit1(_w)
        # (两行的按钮在各自那一轮里已经按**整排统一**定过字号了, 见上)

    def _frame_timed(self, dt):
        """给 `_frame` 计一个**本线程**的耗时, 喂给跑分面板(见 `_FRAME_SELF` 处的说明)。

        ⚠️ 用 `perf_counter` 而不是 `process_time`: 要量的就是"这一帧我们自己的代码在
            **这条线程**上花了多久", 不能把工作线程/Java 线程的 CPU 算进来。
        ⚠️ 包一层而不是改 `_frame` 内部: 那个函数里有好几处提前 return, 内嵌计时容易漏。
        """
        _t0 = time.perf_counter()
        try:
            self._frame(dt)
        finally:
            _FRAME_SELF[0] = (time.perf_counter() - _t0) * 1000.0
            _FRAME_CALLS[0] += 1
            _SINCE_LAUNCH[0] += 1

    def _frame(self, dt):
        self._check_title_hold()
        # 冷启动加载页收尾: 音效库**真的能播**了就摘掉(见 _LoadVeil / Sfx.audio_ready)。
        # ⚠️ 判据是"烘完 **且** 探到真能播(或硬超时)", 不是"烘完就摘" —— `SoundPool.load()`
        #    返回 sampleId ≠ 解码完, 没解码完 play() 返回 0 静默跳过。这正是 BUILD_APK.md 里
        #    记的「首次安装打开 App 必然全静音」的根因。
        # ⚠️ 绝不软锁: audio_ready() 里 `!enabled` 与"探针不可用"都直接放行, 且等待有硬超时。
        _veil = getattr(self, "_load_veil", None)
        if _veil is not None:
            # ⚠️ 就绪判据现在由 `_LoadVeil.tick()` 给 —— 它还要负责开场动画、最短停留和整页淡出,
            #    所以这里只问"能摘了吗", 不再自己看 `audio_ready()`。它自己不持有 Clock, 由这里驱动。
            if _veil.tick(self.sfx.audio_ready()):
                if _veil is getattr(self, "_replay_veil", None) and not _veil._hold:
                    # 「重放冷启动」完成: **不自动摘页** —— 摆出结果等玩家点一下。
                    # 玩家反馈「成功之后没有暂停, 直接回去了, 我啥都没有看清」: PC 上烘焙 1.2 秒、
                    # 探针一过就摘, 那几行数字等于闪一下。
                    _veil._hold = True
                    _veil.set_done()      # 只在最下面亮一行「测试已经完成」(统计去弹窗)
                    _veil._on_tap = self._finish_replay_veil
                elif not _veil._hold:
                    self._load_veil = None
                    _veil.drop()
            # ⚠️ 这里原来有一段"实时诊断": 往加载页印第二行「已加载 42 / 97　·　已用 1.0 秒」。
            #    玩家 2026-09-11 定稿: 「我要的是只显示一个加载界面即可」—— 整段删除。
            #    (现在这一页印的是**游戏名那五个字**, 见 `_LoadVeil._build_title`; 诊断数一个都没丢:
            #     同样的信息「启动信息」里全有, 而且更全, 启动完长按标题就能看。)

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
            self._accumulator = _clamp_accum(self._accumulator + dt)
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
                self._set_game_status("即将入袋…")
            elif b.y > PEG_TOP:
                self._set_game_status("弹跳中…")
            else:
                self._set_game_status("入场中…")
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
            self._accumulator = _clamp_accum(self._accumulator + dt)
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
            self._accumulator = _clamp_accum(self._accumulator + dt)
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
            # 硬兜底在 WinPileFX.busy() 里 —— 但**只覆盖 pending/win**(球还在进场/下落那段)。
            # 装满之后的出口是**玩家点击**(request_close), 那一段故意没有时间兜底。
            # _easter_hold: 彩蛋的弹窗+装杯整段(弹窗是模态的, 玩家只有"确定"一条出口,
            # 所以这个锁不会把谁困住)。
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
        # ⚠️ **余额不再"滚"上去了**(2026-09-14 玩家定案): 落杯子的动画已经承担了"钱到账"的
        #    演出, 数字再滚一遍纯属重复。而且代价不小 —— 滚动期内**每帧都在改
        #    `balance_lbl.text`**, 而 `text` 是 Kivy `Label._font_properties` 之一:
        #    一次赋值 = **重测字形 + 重光栅化整段文字 + 重建纹理**, 还会连锁触发挂在它上面的
        #    自适应字号 `_fit1`。桌面实测 `_fit1` 是 `_frame` 里**最大的单块**开销 ——
        #    45 秒累计 **1.06 秒**(每秒 24 毫秒), 是板面重画 `tick_draw`(0.26 秒) 的 4 倍。
        #    现在只在余额**真的变了**那一帧才动文字 ⇒ 每球一次, 不再是每帧一次。
        # ⚠️ **揭晓前仍然不许动**(`_anim_pending`): 数字必须跟大字/语音在同一刻出来,
        #    提前跳上去就是剧透 —— 这条语义一个字没改, 只是把"滚过去"换成了"直接给"。
        if not self._anim_pending:
            self.display_balance = self.balance
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
        _set_label_text(self.balance_lbl, str(int(round(self.display_balance))))
        self.game_area.tick_draw()
        # 装杯期把整块界面(减去游戏区)压暗 —— 板面那块覆盖不到 GameArea 之外, 见 _build_hud_dim。
        # ⚠️ 位置: 必须在 `tick_draw()` **之后**。演出由 tick_draw -> win_fx.tick() ->
        #    _redraw() 推进, 而 `_redraw` 会把**本帧真正画上去的** a_dim 存进 `_a_dim_now`;
        #    HUD 从这里取值 ⇒ 两边永远是同一个数(同帧同值)。放在前面的话 HUD 读到的是
        #    上一帧: 退场结束那一帧板面已经不画了, HUD 还会多黑一整帧(专家组实测指出)。
        # ⚠️ 它后面**不能再有早退**。下面 charging 分支的 `return` 在它之前是安全的:
        #    蓄力期不可能有装杯演出(演出期输入是锁的), `_a_dim_now` 此时本来就是 0。
        self._sync_hud_dim()
        # ⚠️ **本帧 `_frame` 算完的时刻**(见 `_FRAME_END` 与 `_swap_wrap` 里的 `_brk_add("尾", ...)`)。
        #    这是 `_frame` 的**最后一行** —— 放在中间的话"尾"会含住后面还没跑的部分, 读出来是假账;
        #    它后面**不许再加任何语句**(加了也不会被算进去, 会静默少记)。
        #    只在采样期记(与其它埋点同一条规矩: 别让埋点改变被测量的东西)。
        if _TEXUPD_ACTIVE[0]:
            _FRAME_END[0] = time.perf_counter()

# =============================================================================
# App 入口 / 冒烟
# =============================================================================
# ⚠️ 启动页的底色 —— **它现在是"没有突变"的唯一保证**, 三个地方必须是同一个值:
#      `VEIL_BG`(这一页) == `buildozer.spec` 的 `android.presplash_color`(系统那层图片的底色)
#                      == `p4a/hook.py` 注入的 `windowSplashScreenBackground`(应用窗口的底色)。
#    `fx_probe` 会去另外两个文件里把值读出来对 —— 改一个忘一个就会在一次启动里露出两种颜色,
#    那正是玩家说的"突变"。
#    ⚠️ 这一页**没有任何图**(玩家 2026-09-11 定稿: 「黑屏+汉字」「不用之前的背景图了」「棋盘图
#    没有用了 可以删了」): 系统那张 presplash 本身也已经换成纯 #0b1220, 于是从"点图标"到
#    "进游戏"整条链路上一直是一片同色, 本来就没有可跳的东西。
VEIL_BG = "#0b1220"
# =============================================================================
# 启动页那六个字(玩家 2026-09-11 定稿: 「启动的时候逐渐渲染五个比较大的汉字:
# **跳跳的弹珠机**。渲染完成后, 再次重复渲染 或从高级渲染变成超高级渲染, 反正最多 6 秒钟」,
# 总纲一句是「**总之这个东西不能是突变**」)。
#
# 整段是这样一条链(**一个突变都不许有**, 全程只有渐变):
#   一片 VEIL_BG(系统启动窗口 -> 系统 presplash -> 这一页, 同一个颜色)
#   -> 六个字从左到右逐个点亮
#   -> 一轮走完再走一轮, **一轮比一轮"高级"**(冷白 -> 亮白 -> 金 -> 亮金 + 光晕)
#   -> 音效就绪(最多 `VEIL_TITLE_MAX_SEC` 秒) -> 整页淡出, 露出底下的游戏
VEIL_TITLE = "跳跳的弹珠机"          # 六个字(逐字渲染的粒度就是它; 玩家口述"五个"不影响)
VEIL_TITLE_ROUND_GAP = 0.30         # 一轮扫完到下一轮之间的停顿(秒)。
#    ⚠️ 0.50 -> 0.30(2026-09-11, v0.6.43): 玩家嫌一轮里"前后摇"太长 —— 原节奏下**一轮里 58% 的
#       时间画面完全静止**(前摇 0.35 + 后停 0.50 = 0.85s, 真正在动的只有扫的 0.62s)。挑的"温和"档。
#       现在一轮 = 0.25(前摇) + 0.62(扫) + 0.30(后停) = **1.17s**(原 1.47s), 静止占比 58% -> 47%
#       (稳态一轮里静止 = 前摇 0.25 + 后停 0.30 = 0.55s, 0.55/1.17 = 47.0%; 旧的是 0.85/1.47 = 57.8%)。
VEIL_TITLE_MIN_SEC = 0.22           # **最短停留** = 那行字**完整淡入**要多久(`VEIL_TITLE_IN_SEC`)。
#   ⚠️ 2026-09-11 玩家报的 bug: 「我期望的是启动的时候, **加载完成就进入游戏界面**, 实际上是播放了
#      跳跳的弹珠机这几个字才进去」。**原来这里是 1.9, 那是 v0.6.37 实现 KTV 动画时自己加的, 玩家
#      从来没要过** —— 他的原话只有**上界**(「反正最多 6 秒钟」), 而 v0.6.36 及更早的摘页判据干脆
#      就是"音效一就绪**当帧**就摘"。1.9 也**不是**从"一轮 1.47s"推出来的(推出来该是 1.47): 它与
#      v0.6.29~35 那个已删掉的呼吸周期 `LOADVEIL_BREATH_SEC = 1.9` 同值, 是沿用的旧数字。
#   ⚠️ 实测代价(桌面端到端跑真 App 三次): 音效在 1.19~1.33s 就绪, 加载页却停到 1.90~1.91s ——
#      白等 1.44~1.64s; 把"就绪"从第 1 帧强行置真, 停留仍是 1.90s(差 <3ms) ⇒ 这 1.9s **与加载无关**。
#      真机录屏(2026-09-11)逐帧量: 那行字在屏 1821ms, 反解摘页 = 1.91s; 且这 1.9s 里约 **0.98s(52%)
#      画面完全静止**(淡入完到扫色起步 0.13 + 轮间隙 0.50 + 第二轮前摇 0.35) ⇒ 观感是
#      "演一半卡住 -> 进游戏"(录屏实测: 只看得清一次完整变色, 第二次变绿刚 4.6% 就被切走)。
#   ⚠️ 为什么**不能直接删成 0**(就绪即摘): 那行字有 `VEIL_TITLE_IN_SEC` 的**整行淡入** —— 加载快的
#      机器上会看到"字亮到一半就被摘掉", 而**半截动画比没有动画更像出 bug**。所以地板取淡入时长:
#      保证那六个字**完整亮起来过**一次。
#   ⚠️ 已知副作用(接受): 加载快的机器上只看得到"银色的字亮一下", 看不到 KTV 扫色(扫色要
#      `VEIL_TITLE_LEAD` 之后才起步, 现 0.25s)。加载慢的机器照旧一轮轮演下去, 不受影响。
#      ⚠️ 于是有一个**没人保护**的中间地带: 音效恰好在 0.25~0.87s 之间就绪的机器, 会看到"扫到
#         一半"(左黄右银)然后整页硬消失。这不是新问题(1.9s 时代摘页也切在第二轮扫色的 4.6%),
#         而且地板不能为了它往上抬 —— 抬到 0.87 就等于"必须看完一轮", 正是 v0.6.42 要消灭的东西。
#   ⚠️ **上界不在这一条**: 真正兜底的是 `Sfx.SFX_READY_TIMEOUT`(6s 硬超时), 本常量只是地板。
VEIL_TITLE_MAX_SEC = 6.0            # **最长停留**(玩家定稿「反正最多 6 秒钟」)。
#                                     ⚠️ 与 `Sfx.SFX_READY_TIMEOUT` 同值: 那条是"等音效就绪"的硬超时,
#                                     两个 6 秒一起兜底 —— 改一个记得看另一个。
# 每一轮的**暗色 / 亮色** —— KTV 歌词那条"填充线"扫过去, 就是把字从暗色变成亮色
# (玩家 2026-09-11: 「跳跳的弹珠机刚开始是都能看到的, 然后逐渐改变颜色」「效果类似KTV歌词的
# 变化效果」)。一轮扫完再扫一轮, **每一轮的亮色比上一轮更亮更冷** = "从高级渲染变成超高级渲染"。
# ⚠️ 这里**只有颜色**: 没有描边、没有阴影、没有辉光、没有缩放(玩家: 「高级不是土味审美」
#    「别tmd加奇怪的描边了 阴影了」—— 第一版做的"金色 + 厚光晕"就是这么被打回的)。
#    ⚠️ 三个色都取**低饱和**的莫兰迪调(玩家 2026-09-11: 「当前颜色肯定不行, 应该银色.黄色.红色
#    可以用那个**性冷淡的颜色**」)—— 亮金/正红在暗底上就是土, 沙金和砖红才是那个味道。
# ⚠️ 语义是**「从这一种颜色扫成下一种颜色」**(玩家 2026-09-11: 「应该是**默认是银色的**,
#    **第1次变为黄色**, 再变是红色, 再变是银色」)—— **不是**「暗 -> 亮」两个明暗层次:
#      · 圆点**左边** = 这一轮的颜色, 圆点**右边** = 上一轮的颜色(起点是**银色**);
#      · 一轮扫完, 整行都是新颜色, 停一下, 再扫向下一种; 三种颜色循环, 第 3 种之后回第 1 种。
#    所以这里只是一张**色表**, 顺序就是「变色的顺序」。
#    三个色都取**低饱和的莫兰迪调**(玩家: 「可以用那个性冷淡的颜色」)—— 亮金/正红在暗底上就是土
#    (第一版「金色 + 厚光晕」就是这么被打回的)。
VEIL_TITLE_COLORS = (
    (0.86, 0.88, 0.91),   # 银(默认)
    (0.88, 0.75, 0.44),   # 黄(沙金 —— 偏绿就成橄榄了)
    (0.56, 0.76, 0.60),   # 绿(灰绿/鼠尾草绿 —— 玩家 2026-09-11: 「这个红色不是很好, 是不是绿色更好?」)
)
# ⚠️ 那一页**只许看见这六个字** —— 曾经在填充线的头上画过一个小圆点, 玩家打回:
#    「扫描的时候, 那个小球应该是隐藏的啊, 这个界面中, 只能看到那6个字」。别再加回来。
# ⚠️ **前摇不得短于整行淡入**(`VEIL_TITLE_IN_SEC`) —— 那六个字得**先完全亮出来**再开始变色
#    (玩家 2026-09-11 定稿: 「跳跳的弹珠机刚开始是都能看到的, **然后**逐渐改变颜色」)。
#    0.35 -> 0.25(2026-09-11, v0.6.43, 与下面 `VEIL_TITLE_ROUND_GAP` 同一次): 玩家嫌一轮的"前后摇"
#    太长, 挑的"温和"档。0.25 只比淡入 0.22 多 0.03s = 刚好"亮完就开始扫"。
#    ⚠️ 别再往下压到 0.22 以下 —— 字会**一边淡入一边变色**, `fx_probe` 有门禁钉着这一条。
VEIL_TITLE_LEAD = 0.25
VEIL_TITLE_IN_SEC = 0.22             # 整行**一起**淡入的时长 —— 六个字一上来就都在, 只是别硬蹦出来
# ⚠️ 2026-09-11 试过"把同一行字也画进系统那张 presplash, 然后这页一上来就全亮"来让交接**零变化**
#    —— **失败了, 已回退**: presplash 那行字是 PIL 画的、这行是 Kivy 画的, 两边的字形渲染/
#    抗锯齿不一样, 交接时字会"变一下", 玩家报「有闪屏」。同一个字在两个渲染器里长得不一样,
#    这件事没法规避 —— 所以 presplash 保持**纯色**, 这页照旧淡入。

# 那条填充线**扫完整行**要多久(秒)。
# ⚠️ 它只决定「整行扫完要多久」, **粒度仍是像素级的**(靠那条裁剪, 不是逐字跳) ——
#    所以圆点能停在某个字的中间。
#    0.62s / 6 个字 ≈ **每 0.1s 走过一个字**, 这是玩家 2026-09-11 给的节奏
#    (「以字为单位快速改动 每0.1s改1个字的颜色」)。
VEIL_TITLE_SWEEP = 0.62

def _veil_title_round_len():
    """一轮(**先停一下** -> 填充线从左扫到右 -> 再停一下)有多长。"""
    return VEIL_TITLE_LEAD + VEIL_TITLE_SWEEP + VEIL_TITLE_ROUND_GAP

def _veil_title_state(t):
    """开场动画在"这一页开了 t 秒"时, 六个字各自的状态 —— **纯函数**(探针直接钉它)。

    效果就是**KTV 歌词**那一种(玩家 2026-09-11: 「这个东西的效果类似KTV歌词的变化效果 你懂的」
    「跳跳的弹珠机 跳.跳的弹珠机 跳跳.的弹珠机 应该是这个效果, **圆点左边的字体是新颜色**
    甚至颗粒度可以很细什么的」): 六个字一上来就都在(暗色), 然后一个**圆点**从左往右走,
    **圆点左边的字是这一轮的新颜色、右边还是旧的**; 走到头再从左边重来一轮,
    而**每一轮的新颜色比上一轮更亮更冷**(= "从高级渲染变成超高级渲染")。

    返回 `(lit, fill, grade, rl)`:
      · `lit` = 整行**一起**淡入的进度(0->1, 只走一次) —— 六个字同时到齐, 只是别硬蹦出来;
      · `fill` = 这条填充线扫到**整行的百分之几**(0->1 单调, 本轮内) —— 行级而不是逐字级,
        所以它能停在某个字的中间: 那个字左半边新色、右半边旧色(玩家要的"颗粒度可以很细");
      · `grade` = 第几轮(0 起) —— 调用方拿它去 `VEIL_TITLE_COLORS` 取"这一轮的颜色";
      · `rl` = 一轮多长(秒), 顺手带出来给调用方/探针用。"""
    n = len(VEIL_TITLE)
    rl = _veil_title_round_len()
    try:
        k = int(t / rl) if t > 0.0 else 0
    except Exception:
        k = 0
    # ① **整行一起**淡入(不分先后 —— 玩家定稿「刚开始是都能看到的」): 六个字同时到齐,
    #    只是别在第 0 帧硬蹦出来(那一下也算"突变")。
    u = t / VEIL_TITLE_IN_SEC
    lit = 0.0 if u <= 0.0 else (1.0 if u >= 1.0 else u)
    lit = lit * lit * (3.0 - 2.0 * lit)                  # smoothstep
    # ② KTV 那条"填充线"扫到整行的百分之几 —— **行级**的一条连续进度(不是逐字的),
    #    所以它可以停在某个字的中间: 那个字左半边是新颜色、右半边还是旧的。
    #    一轮之内 0 -> 1 **单调**; 下一轮从 0 重新扫。
    p = (t - k * rl - VEIL_TITLE_LEAD) / VEIL_TITLE_SWEEP
    p = 0.0 if p <= 0.0 else (1.0 if p >= 1.0 else p)
    p = p * p * (3.0 - 2.0 * p)                          # smoothstep
    return lit, p, k, rl

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

    # ⚠️ 普通启动路径印的是**游戏名那五个字**(玩家 2026-09-11 定稿: 「启动的时候逐渐渲染五个
    #    比较大的汉字: 跳跳的弹珠机 …… 反正最多 6 秒钟」), 见 `_build_title` / `_apply`。
    #    底下那张棋盘是**接住系统 presplash** 用的(同图/同尺寸/同位 ⇒ 交接处什么都看不见),
    #    然后自己淡成背景水印给字让位。
    #    只有「重放冷启动」那条路会带文字进来(那是作者主动点开、专门停下来读的一屏),
    #    那条路**不参与开场动画**(没有大字、不淡入淡出)。
    def __init__(self, text="", **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*hex_rgb(VEIL_BG) + (1,))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        # ⚠️ 这里原来还有一个 `_lbl`(印"正在重放冷启动…"那种进度文字), **已整段删除**:
        #    玩家 2026-09-11 定稿「重放冷启动界面**不应该显示各种文字**, 只显示 跳跳的弹珠机
        #    和 最下面的 测试已经完成」。所以这一页现在只有两块东西 —— 正中的标题 + 贴底那行。
        #    构造参数 `text` 仍然保留, 但它现在**只当"这是不是重放页"的标记**用(见 `_is_replay`),
        #    一个字都不显示。
        # 贴底那行状态字: **只服务「重放冷启动」**(`set_done` 往里写「测试已经完成」)。
        # ⚠️ 普通启动路径**绝不写它** —— 那一页只有游戏名那六个大字(见 `_build_title`),
        #    底下什么都没有。这里原来印的是实时诊断(「已加载 42 / 97 · 已用 1.0 秒」), 那种数
        #    在「启动信息」里全都有, 而且更全, 不需要在启动页上再占一行。
        self._sub = Label(text="", font_size=sp(17),
                          color=hex_rgb(COL_SUB) + (1,),
                          halign="center", valign="middle")
        # ⚠️ `_hold`: "重放冷启动"完成时置真 —— 那一屏**不自动摘**, 摆出结果等玩家点一下。
        #    玩家 2026-09-11 反馈: 「点击重放冷启动后, 成功之后没有暂停, 直接回去了, 我啥都没有看清」。
        #    PC 上烘焙只要 1.2 秒、探针一过就摘页, 结果那几行数字等于闪一下。
        #    普通冷启动**不用**这个(那里玩家要的是赶紧进游戏, 不是看数据)。
        self._hold = False
        self._on_tap = None
        # ---- 开场那五个字(只有**普通启动页**有; 「重放冷启动」那页带文字进来, 不参与动画) ----
        self._title_on = False
        self._title_box = (0.0, 0.0, 0.0, 0.0)   # 那一行字的外接框(x, y, w, h), 裁剪按它算
        self._title_fs = 0.0                     # 这一页当前的字号(圆点大小按它算)
        self._t0 = 0.0            # 动画起点 —— **第一帧才盖章**: 构造时刻这一页还没上屏, 从那里
        #                           算会让动画"没开始就过半"(v0.6.26~28 的进场就是这么废的)
        # ⚠️ 这一页**没有任何图**了(玩家 2026-09-11 定稿: 「黑屏+汉字」「不用之前的背景图了」)。
        #    以前它挂一张=系统 presplash 的棋盘图, 为的是"和系统那张逐像素一致" ⇒ 交接处看不见;
        #    现在**一致性由颜色保证**: 系统 presplash 那张图本身已经是纯 `#0b1220`(同一个值),
        #    主题那层的底色也是它, 这一页的底色还是它 ⇒ 整条链路上一直是同一个颜色, 本来就没有
        #    可跳的东西, 那张图自然就多余了。
        #    ⚠️ 三个地方的值**必须永远相同**(它们现在是"没有突变"的唯一保证, `fx_probe` 钉着):
        #       `VEIL_BG` == `buildozer.spec` 的 `android.presplash_color`
        #                 == `p4a/hook.py` 注入的 `windowSplashScreenBackground`。
        self.add_widget(self._sub)
        # ⚠️「重放冷启动」那一页**也要演这一行字**(玩家 2026-09-11: 「启动信息中的冷启动, 仍然可以
        #    无限播放这个启动界面 跳跳的弹珠机的 播放歌词版本」) —— 所以文案带不带进来都建。
        self._is_replay = bool(text)
        self._build_title()
        self.bind(pos=self._sync, size=self._sync)
        self._sub.bind(texture_size=self._sync)
        self._sync()

    def _build_title(self):
        """建那一行字 —— **两个字叠着 + 一条裁剪**:
        底下那层是这一轮的"暗色"(整行都在), 上面那层是"亮色", 但只**裁剪出圆点左边那一段**露出来。
        圆点走到哪儿, 哪儿就换成新颜色 —— 粒度是**像素级**的, 可以切在某个字的中间
        (玩家 2026-09-11: 「圆点左边的字体是新颜色 甚至颗粒度可以很细什么的」)。

        ⚠️ 没有描边、没有阴影、没有辉光、没有缩放 —— **只有颜色**(玩家: 「高级不是土味审美」
        「别tmd加奇怪的描边了 阴影了」)。
        ⚠️ 字号**只在 `_sync` 里改**(窗口尺寸变了才动): 逐帧改字号会让 Kivy 每帧重烘文字纹理;
        逐帧动的只有 `color` 和那条裁剪矩形的宽度。"""
        try:
            self._dim_lb = Label(text=VEIL_TITLE, color=(0.0, 0.0, 0.0, 0.0),
                                 size_hint=(None, None))
            self._hi_lb = Label(text=VEIL_TITLE, color=(0.0, 0.0, 0.0, 0.0),
                                size_hint=(None, None))
            self.add_widget(self._dim_lb)
            self.add_widget(self._hi_lb)          # 亮色那层压在上面
            # 裁剪 = 从整行左边缘到圆点的一条矩形(Kivy 的 Stencil 三连, 和 style.kv 里
            # Image 那套是同一个写法)。圆点右边的亮色被裁掉, 露出底下的暗色。
            with self._hi_lb.canvas.before:
                StencilPush()
                self._clip_a = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
                StencilUse()
            with self._hi_lb.canvas.after:
                StencilUnUse()
                self._clip_b = Rectangle(pos=(0.0, 0.0), size=(0.0, 0.0))
                StencilPop()
            self._dim_lb.bind(texture_size=self._sync)
            self._title_on = True
        except Exception:
            self._title_on = False    # 纯装饰: 建不出来就退化成"只有底色", 绝不把启动带崩

    def set_done(self):
        """「重放冷启动」跑完了: 只在**最下面**亮出一行「测试已经完成」。

        ⚠️ 玩家 2026-09-11 定稿: 「重放冷启动界面不应该显示各种文字, 只显示 跳跳的弹珠机 和
        最下面的 测试已经完成(**如果没有完成, 就不显示**)」—— 所以这一行只在完成时才出现,
        而那些统计数挪去了点击之后的弹窗(`RootWidget._show_replay_detail`)。
        ⚠️ 没完成时这行是空的(构造时默认 `text=""`), 不需要额外的"隐藏"逻辑。"""
        try:
            self._sub.text = "测试已经完成"
            self._sub.font_size = sp(22)
        except Exception:
            pass

    def drop(self):
        """把这一页摘掉(幂等)。"""
        try:
            if self.parent is not None:
                self.parent.remove_widget(self)
        except Exception:
            pass

    def _apply(self, t):
        """把这一帧该有的颜色写下去(唯一的写入口)。

        ⚠️ 这一页**没有淡出、也不该有**: 到点就整页摘掉, 底下直接是游戏。
        玩家 2026-09-11 报的 bug: 「成功打开游戏后, 游戏界面有一个发光效果(原来的启动界面
        loading 界面的, 跳跳的弹珠机的文字的**覆盖发光**), 没有立即消失, 持续了0.x秒。
        **这里应该是啥都看不见, 消失得干干净净, 而不是有淡出**」——
        字压在游戏画面上这件事, 一帧都不能有。
        ⚠️ **只改 `color`**, 绝不碰 `font_size`(那会每帧重烘文字纹理, 手机上掉帧),
        也不加描边/阴影/辉光(玩家定稿: 「高级不是土味审美」「别tmd加奇怪的描边了 阴影了」)。"""
        if not self._title_on:
            return
        lit, fill, grade, _rl = _veil_title_state(t)
        # 底下那层 = **上一轮**的颜色(起点是**银色**), 上面那层 = 这一轮要扫成的颜色。
        # 圈次**取模**不是夹断 —— 三种颜色循环, 玩家要的就是"无限演"。
        _n_col = len(VEIL_TITLE_COLORS)
        gi = grade % _n_col
        dim_rgb = VEIL_TITLE_COLORS[gi]
        hi_rgb = VEIL_TITLE_COLORS[(gi + 1) % _n_col]
        a = lit
        # ⚠️ **只在颜色真的变了才赋值**: Kivy 的 `Label.color` 一变就要重烘文字纹理(6 个汉字在
        #    手机上不便宜)。这里一轮才换一次色, 每帧赋值是白烧 —— 挡一道。
        #    alpha(`a`)只在开头那 0.22s 的淡入里逐帧变, 很短, 无所谓。
        _want = (dim_rgb, hi_rgb, round(a, 3))
        if _want != getattr(self, "_last_col", None):
            self._last_col = _want
            self._dim_lb.color = (dim_rgb[0], dim_rgb[1], dim_rgb[2], a)
            self._hi_lb.color = (hi_rgb[0], hi_rgb[1], hi_rgb[2], a)
        # 裁剪: 从整行左边缘到圆点。fill 是"扫到整行的百分之几" ⇒ 可以停在字的中间。
        x0, y0, w, h = self._title_box
        cut = max(x0, min(x0 + w, x0 + w * fill))
        sw = cut - x0
        for _r in (self._clip_a, self._clip_b):
            _r.pos = (x0, y0)
            _r.size = (sw, h)

    def tick(self, audio_ready=False):
        """每帧推进。由 `RootWidget._frame` 调 —— 自己不持有 Clock(切后台回来直接跳终态)。

        返回 **True = 这一页可以摘了**:
          · 普通启动页: 那行字**完整淡入**(`VEIL_TITLE_MIN_SEC`) + 音效就绪 -> **立刻摘**(零淡出);
            ⚠️ 注意 `VEIL_TITLE_MIN_SEC` 是**地板**(别让字亮到一半就被摘), **不是**"必须演完一轮" ——
               那个 1.9s 的老口径是 v0.6.37 自己加的, 2026-09-11 已按玩家 bug 报告改掉, 见常量处注释。
          · 「重放冷启动」页: 老行为(音效就绪即 True; 结果页靠 `_hold` 停住等玩家点一下)。
        ⚠️ **到点就整页消失, 没有任何淡出**(玩家 2026-09-11: 「这里应该是啥都看不见,
           消失得干干净净, 而不是有淡出」)—— 详见 `_apply` 的注释。
        ⚠️ `_t0` 在**第一个 tick** 才盖章 —— 构造时刻这一页还没上屏(安卓要等 presplash 撤掉),
           从那里算会让动画"没开始就过半"。
        ⚠️ 出任何意外一律返回 True 放行: 绝不能因为动画把玩家卡在启动页(项目红线: 绝不软锁)。"""
        try:
            now = time.time()
            if self._t0 == 0.0:
                self._t0 = now
            t = now - self._t0
            self._apply(t)
            if self._is_replay:
                # 「重放冷启动」: **无限演下去**(扫完一轮再扫一轮), 就绪也不自己摘 ——
                # 摘不摘由 `_frame` 按 `_hold` 决定(那一屏要停住等玩家点一下)。
                return bool(audio_ready)
            return bool(audio_ready) and t >= VEIL_TITLE_MIN_SEC
        except Exception:
            return True

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        # 底部那行状态字(只有「重放冷启动」用)。
        # ⚠️ 玩家 2026-09-11 定稿: 「重放冷启动界面**不应该显示各种文字**, 只显示 **跳跳的弹珠机**
        #    和**最下面的 测试已经完成**(如果没有完成, 就不显示)」⇒ 所以这一页只有两块:
        #    正中的标题 + 贴底的一行状态; 详细统计挪到**点击之后的弹窗**里(见 `_show_replay_detail`)。
        self._sub.text_size = (self.width, None)
        sh = max(sp(24), self._sub.texture_size[1] + sp(8))
        self._sub.size = (self.width, sh)
        self._sub.pos = (self.x, self.y + sp(30))        # **贴最下面**
        # 那一行字的排版。⚠️ 字号**只在这里改**(窗口尺寸变了才动): 逐帧改字号会让 Kivy 每帧
        #    重烘文字纹理, 手机上直接掉帧; 逐帧动的是 `color`(见 `_apply`)。
        # ⚠️ 字号跟着**这一页的宽度**走, 所以是裸数值(不是 sp()) —— 标题要占满宽度, 与屏幕密度无关;
        #    横向 n 个字 ≈ n x 字号宽, 所以取 0.86 / n 再拿高度 0.17 兜一道(别撑出屏)。
        # ⚠️ 标题**永远在正中**: 两块一上一下, 不需要"有文字就把标题顶上去"那套算术了。
        if self._title_on:
            n = max(1, len(VEIL_TITLE))
            fs = min(self.width * 0.86 / n, self.height * 0.17)
            self._title_fs = fs
            for lb in (self._dim_lb, self._hi_lb):
                lb.font_size = fs
                lb.text_size = (None, None)           # 单行, 不折行
            # ⚠️ 两层必须**逐像素同尺寸同位置**, 否则裁剪出来的边会和字错位。
            #    尺寸取两层的最大值(理论上一样, 取 max 是防字体回退那类意外)。
            w = max(self._dim_lb.texture_size[0], self._hi_lb.texture_size[0], fs)
            h = max(self._dim_lb.texture_size[1], self._hi_lb.texture_size[1], fs)
            x0 = self.center_x - w / 2.0
            y0 = self.center_y - h / 2.0
            for lb in (self._dim_lb, self._hi_lb):
                lb.size = (w, h)
                lb.pos = (x0, y0)
            self._title_box = (x0, y0, w, h)
        # ⚠️ 这里**绝不能调 `tick()`**: 它会用 `time.time()` 给动画盖章 `_t0`, 而 `_sync` 在
        #    `__init__` 结尾就会跑一次(那时这一页还没上屏) ⇒ 动画"没开始就过半"。
        #    排版只做上面那一段(字号/位置), 逐帧的颜色/缩放由 `tick() -> _apply()` 写。

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

# 给几个"大头"挂上子步骤计时(必须在**类都定义完之后**执行 —— 这里是 App 类之前)。
# 只包四个: 板面动态重画 / 重掷盘面 / 自适应字号 / 装杯重画。见 `_FRAME_BRK` 处的说明。
# ⚠️ "装杯"是**嵌套在"板面"里面**的(tick_draw -> win_fx.tick -> _redraw), 所以面板上
#    这几个数**不能相加**, 只按"谁最大"读。
_brk_wrap(GameArea, "tick_draw", "板面")
_brk_wrap(RootWidget, "park_ball", "重掷")
_brk_wrap(RootWidget, "_fit1", "字号")
_brk_wrap(WinPileFX, "_redraw", "装杯")
# ⚠️ 2026-09-15 补: **发射那一刻原来是完全没有计时的**。真机两份日志(开/关声音各一次)都显示
#    "蓄力→飞行"那一帧稳定 ~20 毫秒(4/4 次), 而它是全轮最大的尖峰, 却没有任何子步骤能归因。
#    包上之后日志头会直接印"最慢三帧的子步骤": 若这一格很小而整帧很大 ⇒ 钱花在 `launch`
#    外面(Kivy 延后的文字重排 / 渲染), 不是我们的代码。
_brk_wrap(RootWidget, "launch", "发射")
# ⚠️ 2026-09-15 补三个: 三份真机日志里都有一个**同形状的怪帧** —— 出现在"待机 -> 蓄力"那一拍,
#    帧间隔 25~49 毫秒、主线程 15~47 毫秒, 而 `_frame` 自算只有 0.03~0.25 毫秒、子步骤栏是空的。
#      v0.7.28 帧16 28.73 · v0.7.29 帧1184 48.74 · v0.7.30 帧1237 25.26
#    那一拍会跑的、**在 `_frame` 之外**(Clock 回调)的东西就是下面这三个, 外加"自算之后到交画面"
#    那一段(记成 `尾`, 见 `_swap_wrap`)。四个一起上, 下一份日志里那 25 毫秒会自己报名字。
#    ⚠️ 名字刻意与阶段标签(蓄力/装杯)错开, 免得看日志的人把"阶段"和"子步骤"读混。
_brk_wrap(RootWidget, "_auto_launch_tick", "自动发")
_brk_wrap(RootWidget, "start_charge", "起蓄")
_brk_wrap(GameArea, "_update_slots", "槽面")


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
        # 帧率上限/Android 高刷模式: 必须在起循环前设好。
        try:
            _apply_fps_cap()
        except Exception:
            pass
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
            # ⚠️ **周期从 0.7s 拉到 2.5s**(2026-09-14)。原来这条和方向守卫同节奏, 但两者
            #    的代价**根本不在同一个线程上**: 方向守卫那趟 IPC 已经搬到工作线程(v0.6.71),
            #    而这条的 `setSystemUiVisibility` 跑在 **Java UI 线程**, 每 0.7 秒让窗口
            #    重算一次 inset + 走一趟 SurfaceFlinger。桌面剖析证明我们的 Python 只占
            #    帧时间 0.9%, 所以安卓特有的周期性开销只剩这一条。
            #    为什么敢拉长: ① 回前台有 `on_resume()` 兜(那条不动); ② 转屏有 `_frame`
            #    的窗口尺寸轮询立即重申(双保险); ③ **IMMERSIVE_STICKY 本身就是"玩家从边缘
            #    划出来、几秒后系统自动收回"** —— 我们每 0.7 秒重申一次, 收的是一个系统
            #    自己就会收的东西。2.5s 只是"万一系统没收干净"的保险, 不是主路径。
            #    ⚠️ 万一真机上发现系统栏会赖着不走, 把它调回 0.7 即可 —— 代价就是那条尾巴。
            Clock.schedule_interval(self._enter_immersive, 2.5)
            # ⚠️ **守卫的工作线程要在这里就焐热, 不能等 prebake_step**(2026-09-14)。
            #    守卫第一次触发在 **0.7s**, 而预热链第一步在 ~1.6s —— 等它等于让第一次守卫
            #    现建线程 + 现 AttachCurrentThread(与 v0.6.69 修震动踩的是同一个坑)。
            try:
                _guard_warm()
            except Exception:
                pass
            try:
                _cfg_warm()          # 落盘线程也焐热: 别等第一球落袋才现建
            except Exception:
                pass
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
        瘦长手机: 持续重申竖屏锁(7), 任何运行时横屏自报都被顶掉。

        ⚠️ 2026-09-14: **真身已搬到工作线程**(见 `_guard_orient_now` / `_guard_post`)。
           这里只剩一次 `put_nowait`。逻辑一个字没改, 频率一个字没改。
           为什么搬: 它是全 app 唯一的**常驻周期性主线程 JNI**(每 0.7 秒一次), 而真机跑分里
           "每帧实算只有 4.7ms、却有一批**不分阶段**的慢帧(待机那帧都能烧 26.5ms CPU)"
           —— 周期性、跨阶段、纯 CPU, 只有它和沉浸重申两条。判据看面板那行:
           **主线程那档要掉到接近 0, 工作线程那档接手**; 若主线程档没掉, 说明没投出去。
        """
        if platform != "android":
            return
        _t0 = time.perf_counter()
        ok = _guard_post("orient")
        if not ok:                       # 队列建不起来 / 满: 退回同步 = 今天的行为
            try:
                _guard_orient_now()
            except Exception:
                _JNI_STAT[5] += 1
        _d = time.perf_counter() - _t0
        _JNI_STAT[0] += _d
        if _d > _JNI_STAT[1]:
            _JNI_STAT[1] = _d

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
                    # ⚠️ 这一段跑在 **Java UI 线程**上, 不是我们的 Python 线程 —— 所以它
                    #    根本不出现在 `_frame` 的剖析里, 也测不到"主线程实算"里。
                    #    而 `setSystemUiVisibility` 会让窗口重算 inset + 走一趟
                    #    SurfaceFlinger, 代价**每台机器差很多**。2026-09-14 给它单独计时:
                    #    判据 —— 若单次 ≥5 毫秒, 那"每 0.7 秒重申一次"就是在拿 UI 线程
                    #    换一个系统本来就自动隐藏的东西(IMMERSIVE_STICKY 会自己收回)。
                    try:
                        from jnius import autoclass
                        act = autoclass("org.kivy.android.PythonActivity").mActivity
                        View = autoclass("android.view.View")
                        # ⚠️ `_t0` **必须打在两次 `autoclass` 之后**(2026-09-14 修): 原来打在
                        #    它们之前, 于是面板上"系统栏重申·单次最慢"测的是**探针自己的查表
                        #    开销**(pyjnius 的 autoclass 在毫秒级), 不是 `setSystemUiVisibility`
                        #    的代价 —— 那一格一直在测自己。(压测段 2 号专家指出。)
                        _t0 = time.perf_counter()
                        act.getWindow().getDecorView().setSystemUiVisibility(
                            View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
                    except Exception:
                        pass
                    try:
                        _d = time.perf_counter() - _t0
                        _JNI_STAT[6] += _d
                        _JNI_STAT[7] += 1
                        if _d > _JNI_STAT[8]:
                            _JNI_STAT[8] = _d
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
        _t0 = time.perf_counter()
        ok = _guard_post("immerse")
        if not ok:
            try:
                _guard_immersive_now()
            except Exception:
                _JNI_STAT[5] += 1
        _d = time.perf_counter() - _t0
        _JNI_STAT[0] += _d
        if _d > _JNI_STAT[1]:
            _JNI_STAT[1] = _d

    # Android 生命周期: on_pause 必须返回 True 保持 GL 上下文
    def on_pause(self):
        try:
            self.rootw.sfx.pause_out()       # 切后台静音(SoundPool.autoPause)
        except Exception:
            pass
        # 设定是**异步落盘**的: 切后台前必须把挂着的那一份等完, 否则切走那一刻的
        # 余额/次数可能还没写下去(下次启动读到旧值)。超时就放弃, 绝不卡住系统回调。
        try:
            _cfg_flush()
        except Exception:
            pass
        return True

    def on_resume(self):
        if platform == "android":
            try:
                _apply_fps_cap()        # 屏幕刷新率会随智能刷新率/外接屏变, 回来重算一次
            except Exception:
                pass
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
        # ⚠️ 先清掉上一局残留的装杯: 装满后要玩家点击才退场(2026-09-12 定稿), 而冒烟里
        #    没有人点 —— 不清的话它停在 result, 下面那条"settle 那一刻 mode 本该是 idle"
        #    会被这个残值误判成 FAIL。
        r.game_area.win_fx._abort()
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
            # ⚠️ 装杯装满后要**玩家点击**才退场(玩家 2026-09-12 定稿), 而冒烟里没有人点 ——
            #    所以过半还没等到就**模拟一次玩家点击**, 走的是同一个 `request_close()`
            #    (别在冒烟里另写一份"点击该干什么")。不这么做的话 s8/s9 会静默走超时分支,
            #    在"杯子还立着"的状态下截图, 日志里只有一行超时 = 会骗人的绿。
            if left < tries // 2:
                try:
                    r.game_area.win_fx.request_close()
                except Exception:
                    pass
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
