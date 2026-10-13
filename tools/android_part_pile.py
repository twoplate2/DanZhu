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
#    重光栅化 + 重建纹理。而淡出段 alpha 每帧都在变 ⇒ 不量化就等于**每帧重画一遍大字**。
#    量化到 20 档: 0.81s 的淡出分成每 40ms 一档、每档 5% alpha —— 配着同一时刻的上浮与缩放,
#    肉眼分辨不出台阶; 代价从"每帧一次"降到"一次演出 20 次"(实测重建 530 -> 170)。
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
# ⚠️ **必须惰算**: `sp()` 读当时的窗口密度, 而模块导入时窗口还没建 —— 在这里直接
#    写 `sp(36)` 会拿到错误的密度(真机上就是"预热了一堆没人用的字号")。
# ⚠️ **一次只碰一个**(见 prebake_step 的字体预热那段): 4 个挤一帧 = 一帧 100ms+。
_FONT_WARM_SIZES = None
# GC 冻结计数(gc.freeze() 之后为永久代里的对象数; 0 = 还没冻结)。只给跑分面板显示用 ——
# 让后来的人一眼看出"这版的 GC 冻结到底跑没跑", 而不是靠猜(本仓库栽过静默失效)。
# [冻结的对象数, 冻结**前**强制全量回收一次要多久(毫秒), 冻结**后**同一个动作要多久]
# 后两格是 v0.6.75 加的 —— 桌面实测**量不出**冻结的收益(桌面 30 秒只有 0.6ms 的 GC, 全是
# 零头), 所以只能让 App 在**真机上**自己量: 启动时冻结前后各强制做一次 gen-2 全量回收,
# 把两个数打进跑分面板。判据一眼可见: "全量回收 26.8 -> 0.0 毫秒"。
_GC_FROZEN = [0, 0.0, 0.0]
# 启动预热链是否**全部**跑完(球纹理/字形/球堆/GC 冻结)。
# ⚠️ 为什么跑分要等它: 采样窗口只有 7~12 秒(_target_launches = 3), 而玩家是启动后 3 秒
#    就长按标题开跑的 —— 真机上一个预热单步要 100~200 毫秒(桌面只要 16.6), 常常还没跑完。
#    混进采样里会把 1%Low 压下去, 而且量到的是**启动期**的数, 不是玩家平时玩的数。
_PREBAKE_DONE = [False]
_GLASS_TEX = None       # (back, front, over, fallback) 模块级缓存, 不按实例存
# 杯口环后半 + 杯底环后半("远侧那两半圈")现在由**独立的 assets/glass_tumbler_over.png**
# 提供, 运行时在压暗层**之后**整张画一次 —— 这里不再需要 `RIM_BACK_BANDS` /
# `RIM_BAND_STRIPS` / `RIM_BAND_ALPHA_TOP` 那套分段补画(已删除)。
#
# ⚠️ 为什么换掉它(2026-09-13, 玩家报「表演动画的玻璃杯上边缘的衔接处有明显痕迹」):
#    那两段原本画在 back 层里, 会被演出的压暗层(DIM_ALPHA=0.68)盖住 —— 实测后环亮度
#    被砍到 **12.7**, 而前环(front 层, 不压暗)约 150, **差 12 倍**。
#    补画是**叠加**、上限受 back 层自身 alpha 的限制(环的 alpha 只有 68/255): 整条补到
#    满格 1.0 也只能到 ~80, 而前环 ~120 ⇒ 这个落差**消不掉**, 只能靠渐变把它磨缓。
#    而 12 段渐变是按 **y 线性**分的, 环在椭圆顶部近乎水平(沿环走 y 几乎不变 ⇒ alpha 恒定
#    在 0.55)、在左右两端近乎竖直(y 急变 ⇒ alpha 猛升到 1.0) ⇒ 台阶恰好落在杯口的左右
#    两端, 也就是玩家指的那个"衔接处"。**这是结构性缺陷, 调参救不了。**
#    拆成独立层后这两段**完全不经过压暗**, 前后半环恢复成设计稿本来的比例(生成器实测
#    back 208.7 vs front 238.3, 差 14% —— 那是刻意的"远侧略暗"透视感), 而且运行时只要
#    画 1 个 Rectangle(原来 12 个)。生成器侧见 generate_glass_tumbler.py 里 `over` 的定义。
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


# ==================== 切面球烘焙(主球 + 杯中球共用同一套公式) ====================
# 玩家 2026-09-13 定稿的样式: 8 面 · 纯平涂 · 金色 · 棱线「中·只要暗缝」。
#
# ⚠️ 这是**贴图**路, 不是矢量路。矢量路(Mesh 平面多边形拼合)试过并已回退(2026-09-13):
#   ① 面与面之间那道棱线画不对 —— 正对镜头那两个面的投影多边形会**自交**, 扇形三角化多画
#      212.7% 的面积、糊到别处(玩家看到的"黑线"); 而且 `_fatten` 绕重心放大后 8 个面**全部**
#      越出球轮廓 1.3~2.0px, 球轮廓内还有 13% 不属于任何面、露出没受光的底色(亮黄月牙)。
#   ② 更要命: Kivy 的 Mesh **每个面只能吃一个纯色**, 装不下工坊那层**球面明暗(lamS)**
#      ⇒ 做出来每个面是一个平色块, 正是玩家说过"很丑"的那版
#      (工坊自己的注释写着: 「少了 lamS 那一项…那就是丑的那版」)。
#   烘成贴图两条都天然解决 —— 逐像素想怎么算就怎么算, 运行期还回到"1 张图"。
#
# ⚠️ 纯 Python 逐像素(**Android 上没有 PIL/numpy**, 见 BUILD_APK.md §3.8)。代价靠分帧预烘摊开,
#   绝不要在中奖那一帧现做。
#
# ⚠️ **光照图与颜色解耦**: `受光量 / 棱线量 / alpha` 只取决于几何(球径 d), **与颜色无关**
#   ⇒ 只算一次, 之后每档颜色只是"乘一下再写 3 个字节"。主球 + 4 个投注档**共用同一张 map**,
#   这是这套烘焙能在纯 Python 下跑得动的唯一原因。
_FACET_N = 8
_FACET_RIDGE_W = 0.080        # 棱线总宽(点积阈值)。玩家定稿的「中」档 —— 实测 58px 上仍读得出刻痕
_FACET_SEAM_K = 0.90          # 暗缝强度
_FACET_SEAM_RGB = (0.02, 0.02, 0.03)
_FACET_LIGHT = [-0.42, -0.52, 0.74]
_FACET_AMB, _FACET_DIF_F, _FACET_DIF_S = 0.46, 0.52, 0.46
_FACET_MAPS = {}              # d -> [(像素字节偏移, 受光量, 棱线量, alpha), ...]
_FACET_CENTERS = None


def _facet_centers():
    """球面斐波那契 8 点 —— 天然**不对称**(转起来才看得出在转, 不会转半圈就重复)。"""
    global _FACET_CENTERS
    if _FACET_CENTERS is None:
        pts = []
        ga = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(_FACET_N):
            z = 1.0 - (2.0 * i + 1.0) / _FACET_N
            r = math.sqrt(max(0.0, 1.0 - z * z))
            th = ga * i
            pts.append((math.cos(th) * r, math.sin(th) * r, z))
        _FACET_CENTERS = pts
    return _FACET_CENTERS


def _facet_map(d):
    """算一次"光照图": 逐像素的 (受光量, 棱线量, alpha)。**与颜色无关**, 按 d 缓存。

    受光 = 环境 + 面法线漫反射(块面感) + **球面法线漫反射**(明暗塑形)。
    ⚠️ 第二项就是矢量路装不下的那层 `lamS` —— 少了它整颗球会"转到可见面都不朝光"时发暗。
    """
    m = _FACET_MAPS.get(d)
    if m is not None:
        return m
    centers = _facet_centers()
    ln = math.sqrt(sum(c * c for c in _FACET_LIGHT))
    lx, ly, lz = (c / ln for c in _FACET_LIGHT)
    r = d / 2.0
    aa = 1.5 / r                       # 1.5 屏幕像素的抗锯齿带(**不随球径变宽**)
    out = []
    for y in range(d):
        for x in range(d):
            px = (x - r + 0.5) / (r - 0.5)
            py = (y - r + 0.5) / (r - 0.5)
            r2 = px * px + py * py
            if r2 >= 1.0:
                continue
            dist = math.sqrt(r2)
            pz = math.sqrt(1.0 - r2)
            # 找最近的两个面心(正交投影下的球面 Voronoi)
            best, second, bi = -2.0, -2.0, 0
            for k in range(_FACET_N):
                c = centers[k]
                dot = px * c[0] + py * c[1] + pz * c[2]
                if dot > best:
                    second, best, bi = best, dot, k
                elif dot > second:
                    second = dot
            c = centers[bi]
            # 面法线漫反射(块面感) + 球面法线漫反射(明暗塑形)
            lf = c[0] * lx + c[1] * ly + c[2] * lz
            ls = px * lx + py * ly + pz * lz
            lit = _FACET_AMB + _FACET_DIF_F * (lf if lf > 0.0 else 0.0) \
                + _FACET_DIF_S * (ls if ls > 0.0 else 0.0)
            e = (best - second) / _FACET_RIDGE_W
            tt = 1.0 - (1.0 if e > 1.0 else (e if e > 0.0 else 0.0))   # 1 = 在棱线上
            a = (1.0 - dist) / aa
            alpha = 1.0 if a > 1.0 else (a if a > 0.0 else 0.0)
            out.append(((y * d + x) * 4, lit, tt, alpha))
    _FACET_MAPS[d] = out
    return out


def _facet_bake(d, base_rgb):
    """按基色烘一张切面球 RGBA 字节串。base_rgb 是 0~1 的三元组。

    ⚠️ **基色只能有一处真源**: 主球传球色、杯中球传 `BET_COLORS[档]`。这里不另存颜色表 ——
       项目踩过"染色值两处不同步 ⇒ 闪一下回不到原色"的坑。
    ⚠️ **收正过曝**: 工坊那套 `lit` 上限 1.44, 乘基色后大片通道被截断成死白。这里夹到
       `1/max(基色)` —— 只有最亮的一小块贴顶, 不再成片死白。
    """
    map_ = _facet_map(d)
    # 三个锚点由基色派生: mid 就是基色本身 ⇒ 球的主色与投注按钮色标严格同源
    hi = tuple(base_rgb[i] + (1.0 - base_rgb[i]) * 0.60 for i in range(3))
    lo = tuple(base_rgb[i] * 0.45 for i in range(3))
    # 「纯平涂」: d=0.42 处的色 —— 与工坊 colorAt(col,0.42) 同一个取法
    f = 0.42 / 0.55
    bc = tuple(hi[i] + (base_rgb[i] - hi[i]) * f for i in range(3))
    lim = 1.0 / max(bc)
    sr, sg, sb = _FACET_SEAM_RGB
    k0 = _FACET_SEAM_K
    buf = bytearray(d * d * 4)
    for i, lit, tt, alpha in map_:
        L = lit if lit < lim else lim
        k = k0 * tt
        u = 1.0 - k
        buf[i] = int((bc[0] * L * u + sr * k) * 255.0)
        buf[i + 1] = int((bc[1] * L * u + sg * k) * 255.0)
        buf[i + 2] = int((bc[2] * L * u + sb * k) * 255.0)
        buf[i + 3] = int(alpha * 255.0)
    return bytes(buf)


# ⚠️ **杯中球 = 光滑渐变, 不是刻面** —— 这是看过实物之后退回的(2026-09-13)。
#    刻面球单颗好看, 但成堆之后每颗都是同一个图案(只有 8 个面、球却有 100 颗),
#    100 颗摆在一起读起来像一堆相同的糖; 而且贴图里烘着方向光, 自转会把光一起转走,
#    一堆球各有各的光向, 更像复制粘贴。试过烘 12 个朝向变体(光照统一、图案不同),
#    好一些但 100 颗仍重复 8 次以上, 仍然看得出来。
#    结论: **刻面只给主球(单颗、飞行中)**; 杯中那堆是中奖的烟花, 干净比有细节重要。
#    ⚠️ 别为了「跟主球一致」把它改回刻面 —— 一致性输给成堆观感, 这是玩家看过实物拍的。
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
    # ⚠️ `mipmap=True` + `min_filter="linear_mipmap_linear"` —— **别退回纯 linear**。
    #    杯中球在演出里会被缩放(进场 0.92→1.0、退场缩到 0.90), 那是**缩小采样**;
    #    没有 mipmap 时 Kivy 只取 2x2 个纹素, 边缘会发虚、跟球身糊在一起
    #    (玩家 2026-09-13 原话: "边缘部分和主干部分没有区分度")。
    #    血泪: 这行曾经在"从 HEAD 取回实现"时被静默丢掉过 —— HEAD 那版没有它。
    tex = Texture.create(size=(d, d), colorfmt="rgba", mipmap=True)
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    tex.min_filter = "linear_mipmap_linear"   # 缩小走 mipmap 三线性
    _CUP_BALL_TEX[bet] = tex
    return tex


def _glass_textures():
    """玻璃后层/前层 + 远侧环补画层 + 兼容整图; 分层失败回退整图, 再失败返回空四元组。

    ⚠️ 最后一档回退是**静默**的 —— 会画出一个"没有杯子的一团弹珠"。所以这里显式
    print(CUP-TEX MISSING), 让它出现在 logcat 里, 而不是等玩家来问"杯子去哪了"。

    返回值第 3 项 `over` = 杯口环后半 + 杯底环后半(远侧那两半圈)的独立补画层,
    详见文件上方那段说明。它是**可选**的: 加载失败只降级成"后环被压暗"(即 2026-09-13
    之前的样子), 不让整只杯子消失 —— 所以它单独 try, 不并进下面那个 except 链。
    """
    global _GLASS_TEX
    if _GLASS_TEX is not None:
        return _GLASS_TEX
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        # ⚠️ `mipmap=True` 必须在 **CoreImage 加载时**传 —— 纹理建好之后再改 `tex.mipmap`
        #    不会补出 mip 链。玻璃在演出里会被缩放(0.90~1.0), 那是缩小采样。
        back = _CoreImage(os.path.join(assets, "glass_tumbler_back.png"), mipmap=True).texture
        front = _CoreImage(os.path.join(assets, "glass_tumbler_front.png"), mipmap=True).texture
        for texture in (back, front):
            texture.mag_filter = "linear"
            texture.min_filter = "linear_mipmap_linear"
        try:
            over = _CoreImage(os.path.join(assets, "glass_tumbler_over.png"), mipmap=True).texture
            over.mag_filter = "linear"
            over.min_filter = "linear_mipmap_linear"
        except Exception:
            over = None
            print("CUP-TEX NO-OVER: 远侧环补画层缺失, 杯口后半圈会偏暗(降级不崩)")
        _GLASS_TEX = (back, front, over, None)
    except Exception:
        try:
            fallback = _CoreImage(os.path.join(assets, "glass_tumbler.png"), mipmap=True).texture
            fallback.mag_filter = "linear"
            fallback.min_filter = "linear_mipmap_linear"
            _GLASS_TEX = (None, None, None, fallback)
            print("CUP-TEX FALLBACK: 分层玻璃图缺失, 退回整图")
        except Exception:
            _GLASS_TEX = (None, None, None, None)
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


# 原 `_rim_back_strips()` 已删除(2026-09-13): 远侧那两半环改成独立贴图层
# `assets/glass_tumbler_over.png`, 在压暗之后整张画一次, 见文件上方那段说明。
# 它当年是把 back 里那两半环"补画到压暗之上", 但补画是**叠加**、上限受 back 自身 alpha
# 的限制(环 alpha 只有 68/255), 落差消不掉 —— 玩家 2026-09-13 报的
# 「杯口上边缘的衔接处有明显痕迹」就是它留下的。保留这行是为了让后来的人搜得到这段历史。


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
        # ⚠️ 和主球同一个修法(见 android_part_ui.py 里 `_tex_coords_rot` 的说明): 落地的拉伸
        #    方向必须留在**屏幕轴**上(球是竖直落下来的, 压扁自然也该竖直), 自转交给纹理坐标。
        #    原来 `Rotate(angle=ring_angle + spin_deg*...)` 把拉伸方向一起转走了, 而 ring_angle
        #    是**每颗球独立随机**(±180°)、`_ball_screen` 里也确实把它和 sx_k/sy_k 一起返回 ⇒
        #    100 颗球落地时的压扁方向全是随机的。
        #    这里不再 PushMatrix/Rotate: quad 保持轴对齐(rx/ry 直接就是屏幕上的横竖半径),
        #    旋转全部落在纹理坐标上。顺带省掉每颗球一次矩阵进出。
        bead_e = Rectangle(texture=_ball_texture(b["value"]),
                           pos=(x - rx, y - ry), size=(rx * 2, ry * 2))
        bead_e.tex_coords = _tex_coords_rot(angle)

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
        back_tex, front_tex, over_tex, fb_tex = _glass_textures()
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
                # 远侧那两半环(杯口环后半 + 杯底环后半)在**压暗之上**整张画一次。
                # 它们不经过压暗 ⇒ 前后半环保持设计稿本来的比例(生成器实测差 14%),
                # 杯口上边缘那个接缝从根上消失(玩家 2026-09-13 报的)。见文件上方说明。
                # 放在球之前: 后环本来就在珠子后面, 球要能挡住它。
                # 同一张 3200x1840 画布, 所以矩形与 back 完全一致。
                if over_tex is not None:
                    Color(1.0, 1.0, 1.0, a_cup)
                    Rectangle(texture=over_tex, pos=(bx, by), size=(bw, bh))

                # 球按画家序一趟画完(远先近后) —— **含飞行中的球**。
                # ⚠️ 原来是两趟: 先画已落定球、再把飞行球**一律置顶**。那个写法之所以
                #    不出事, 唯一原因是发牌序 = z 降序 ⇒ 任意时刻已落定的恰好是最远的那批
                #    ⇒ 飞行球永远比它们近, 画在上面**是对的**。发牌改成随机拓扑序之后
                #    这个巧合就没了: 更远的飞行球会被画在更近的已落定球之上 —— 实测
                #    x100 一局 4750 帧的层次错乱(占重叠帧 25.5%), 落定那一帧还有约 46 颗
                #    球会"单帧掉掉一半像素"(从置顶切成画家序)。
                # 现在按深度插进同一趟: 近的球盖远的球, 飞行球也不例外。代价是飞行球
                #    可能被前面的球短暂遮住(物理上正确 —— 像球陷进堆里), 这是为顺序随机
                #    付的必要代价, 别改回去。
                for b in self._balls:
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
            Clock.schedule_once(self.prebake_step, 0.05)
            return
        _FRAME_PROBE[2] += 1      # 本帧跑了预热(供跑分面板把"启动慢"与"玩起来卡"分开)
        if _FONT_WARM_SIZES is None:
            # 首次走到这里才按**当时的窗口密度**算(模块导入时窗口还没建, 那时 sp() 是错的)。
            # 把**整个阶梯**都烘掉, 不能只烘第一档: 中奖大字的文字是 `+N`, 位数一多第一档就
            # 放不下, 必然往 0.94/0.88/... 走, 而每一档都是一个新的精确字号、每次都要现开
            # 一次字形表(桌面 26 毫秒, 真机更贵); 而 `big_result_text` 是在 `_frame` **里面**
            # 跑的(tick_draw -> win_fx.tick -> _pump_reveal -> _reveal_win) => 那几档冷开
            # 直接记在 `_frame` 头上。真机证据(0.6.81 均衡模式): "65毫秒(落袋·自算48.8)" /
            # "60毫秒(装杯·自算40.7)"。代价: 预热从 4 步变 22 步(多约 1 秒启动期)。
            # 注意这里**不落 0.5px 网格** —— 落网格会和布局烘出来的字号对不上, 反而全变冷开
            # (0.6.80 那个回退的教训)。
            globals()["_FONT_WARM_SIZES"] = tuple(
                _b * _k for _b in (sp(36), sp(48), sp(26), sp(30))
                for _k in FIT_SCALES)
        if self._font_prebaked < len(_FONT_WARM_SIZES):
            # ⚠️ **一帧只碰一个字号**(2026-09-14 改)。原来是 4 个字号挤在**同一帧**里跑,
            #    而"碰一个新字号"= 重新打开一次 TTF 字形表 = 桌面 26ms ⇒ 那一帧至少 **100ms**,
            #    真机上更贵。这是本方法自己引入的长帧(它要消灭的是"落袋那帧现开字形表",
            #    结果先在启动期造了一记更长的) —— 分帧摊开, 每帧只付一次。
            #    实测(桌面, 逐步计时): 最慢一步 119.4ms -> 33.2ms, ≥50ms 的帧 1 -> 0,
            #    总耗时不变(213 -> 225ms, 同一份工作)。
            _fs = _FONT_WARM_SIZES[self._font_prebaked]
            self._font_prebaked += 1
            try:
                text_px("未中", _fs, True)
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
        # 为什么本工程原先排除 GC 调优**不适用**于这里: CLAUDE.md 里那条排除的原话是
        #   "gc.freeze()/禁用换来的只是把一次 7.6ms 拆成更频繁的小停顿, **平均开销不变**"。
        #   那是对**平均帧**的算法 —— 而 1%Low 是"最慢那 1% 的均值", 只看最坏的那几帧,
        #   平均开销变不变与它无关。同一条注释自己也写着"它进不了平均值, 只能进 1% low"。
        #   => **目标换成 1%Low 之后, 这条排除作废。**
        #
        # 实测(桌面探针 gc_freeze_probe.py, 应用建完等预热跑完后):
        #   GC 跟踪的对象数  54145  ->  0（全部移入永久代, 永不再扫）
        #   强制 gen-2 全量回收 中位 2.9ms  ->  0.0ms
        # 泄漏复验(gc_freeze_wiring_probe.py, 24 轮重建盘面 + 连发):
        #   冻结计数 54542 -> 52898(只降不升, 引用计数仍在释放), Python 层内存每 6 轮
        #   增量 38/40/120 KB, **不线性** —— 冻的不是会被丢掉的那批。
        # ⚠️ **必须在预热全部跑完之后调**: 冻结的是"此刻活着的所有对象", 早调会把还没建好的
        #    缓存漏在外面(仍然被跟踪, 白白多一次 gen-2 的扫描量)。
        # ⚠️ **冻结 = 这些对象永不被 GC 回收**。只要它们真是长期活着的(控件树/贴图/缓存),
        #    这就是纯赚; 会**被淘汰**的缓存要留意 —— 本工程里 _PILE_CACHE 上限 128(现用 28)、
        #    _CUP_BALL_TEX 4 个且从不淘汰、_FIT_PX 超 512 会 clear(里面是 float 和元组,
        #    漏掉也无所谓), 都在可忽略的量级。
        # ⚠️ 每局新造的东西(球堆的 100 颗球、画布指令、_balls 列表)都是**冻结之后**建的,
        #    照常被跟踪、照常回收 —— 冻结不会让它们泄漏。
        # ⚠️ 冻结之后 gen-0/gen-1 也变便宜了。阈值**不动**(= CPython 默认 700/10/10) ——
        #    没有实测证据就别改它。
        try:
            import gc as _gc
            _gc.collect()                 # 先把垃圾收干净, 免得把垃圾也一起冻结
            # 冻结**前**强制一次 gen-2 全量回收并计时 —— 这就是"真机上一次全量回收要多久"
            # 的直接读数(面板那栏"内存回收 最坏一次 26.8"就是它造成的)。代价是启动期多
            # 一记停顿, 发生在预热链里、玩家看不见的地方。
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
