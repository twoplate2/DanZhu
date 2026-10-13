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
# ⚠️ 为什么缓存: `_redraw` 每次重掷盘面都把整块画布重建一遍, 里面**每个非空槽都新建一个
#    `CoreLabel` 并 `refresh()`** —— 那是**一次完整的文字光栅化**。一次重掷最多 9 个槽。
#    而倍率取值只有 2/3/5/10/20/50/100 这几种 ⇒ 贴图建一次就够, 之后每次重掷都白建。
#    实测(桌面, 预热后连调 50 次): 中位 0.562 -> 0.503 毫秒, 最坏 2.775 -> 0.666 ——
#    **只买到 12%**, 老老实实记在这儿: 剩下的 0.5 毫秒是"clear + 重建 ~345 条指令"本身。
# ⚠️ 缓存键**必须带字号** `fs`: 它跟着画布缩放 `s` 走, 转屏/改窗口时字号会变。
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
FIT_HARD_FLOOR = 0.42       # 阶梯全试完后的硬下限(只防"小到看不见", 不参与塞不塞得下的判断)
                            # (0.5 时实测 1.5 倍字体 + 5 个档位按钮下 "5000%" 还差 5px)
_FIT_PX = {}


def text_px(text, fs, bold=False):
    """一段文字在字号 fs 下的**单行宽度**(px)。结果缓存。"""
    if not text:
        return 0.0
    key = (text, round(fs, 2), bool(bold))
    got = _FIT_PX.get(key)
    if got is None:
        try:
            _cl = CoreLabel(text=text, font_size=fs, bold=bold, text_size=(None, None))
            _cl.refresh()
            got = _cl.texture.size[0]
        except Exception:
            got = len(text) * fs * 0.55          # 量不出来按汉字宽粗估, 绝不抛
        if len(_FIT_PX) > 512:                   # 余额那类数字会一直变, 别让缓存无限长
            _FIT_PX.clear()
        _FIT_PX[key] = got
    return got


# `fit_font_size` 的**结果缓存**。键 = (文字, 基准字号, 可用宽, 粗体) —— 它是纯函数。
# ⚠️ 为什么值得缓存(2026-09-14, 桌面实测): `_fit1` 是 `_frame` 里**最大的单块**开销 ——
#    45 秒累计 **1.06 秒**(每秒 24 毫秒), 是板面重画 `tick_draw`(0.26 秒)的 4 倍。
#    而 `_install_fit` 把 `_fit1` 绑在 **`text` 和 `width` 两条路**上: **宽度变化那条
#    文字根本没变**, 却在用同一份输入把整个阶梯从头再算一遍。
# ⚠️ 缓存**不设超大**: 余额那类数字会一直变, 上限到了整体清空(与 `_FIT_PX` 同策)。
_FIT_SIZE_PX = {}

# 字号**量化网格**(px)。走过的字号一律先落到这个网格上再测量/返回。
# ⚠️ 为什么必须量化(2026-09-14): "碰一个新字号"在 Kivy 里 = **重新打开一次 TTF 字形表**
#    —— 桌面实测 **26 毫秒**。而 `fit_font_size` 的**二分**支会返回 `10.65` 这种任意值,
#    `_fit1` 又把它写进 `font_size` ⇒ **每挑出一个新字号就付一次 26 毫秒**, 而且那个字号
#    这辈子只用这一次。实测(桌面): 量化前 `_fit1` 45 秒累计 **1.041 秒**, 量化后 **0.569 秒**
#    (降 45%, 帧数相当)。它就是 `_frame` 里最大的单块开销。
#    量化误差**最大 0.25px**, 肉眼分辨不出(比 1sp 在 2.5 倍密度下还小一个量级)。
# ⚠️ 网格别调粗: 1px 网格在 360dp + 小字号(10~12px)上会有约 8% 的字号跳变, 就开始看得出了。
_FIT_GRID = 0.5


def _qfs(x):
    """把一个字号落到量化网格上。

    ⚠️ **2026-09-14: 已停止使用(保留函数只为别处引用不报错)。**
    为什么回退: 真机上 `sp(N)` 是 `N x 密度` 的**非整数**, 落网格后挪了最多 0.25px ——
    而 Kivy 的字体缓存**按精确字号索引**, 于是**和启动期预热/布局烘出来的字号对不上**,
    每次挑字号都变成一次**冷开字形表**(桌面 26 毫秒, 真机更贵)。
    后果是主线程变忙 -> 物理 benchmark 那条线程拿到的 GIL 变少 -> **纯 CPU 吞吐掉约 6%**
    (玩家实测 10 次: 0.6.79 是 32000+ 步/秒, 0.6.81 只有 30000+)。
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
    """真正干活的那一半。**别直接调它**, 走带缓存的入口。"""
    for _k in FIT_SCALES:
        _fs = base_fs * _k
        if text_px(text, _fs, bold) <= avail_w:
            return _fs
    # ⚠️ 6 档全试完还是塞不下时, **不能**"给个地板档然后听天由命" —— 那样就退回了折行/
    #    盖邻居两态, 正是这套机制要消灭的东西(对抗性复核实测: 大字体下 360dp 上
    #    返还率按钮超 48%、顶栏状态超 16%, 全都落在这一支)。
    # ⚠️ 也**不能**按比例估一次: 实测字宽**不随字号线性变** —— 同一串在 14.95 和 14.05
    #    下量出来都是 139px(字形步进被取整), 估出来的 10.65 量出来仍是 102px > 可用 101。
    #    所以在 [硬下限, 阶梯最小档] 之间**二分**, 取"真的量得下"的最大字号。
    _lo = base_fs * FIT_HARD_FLOOR
    _hi = base_fs * FIT_SCALES[-1]
    if text_px(text, _hi, bold) <= avail_w:
        return _hi
    for _ in range(6):
        _mid = (_lo + _hi) / 2.0
        if text_px(text, _mid, bold) <= avail_w:
            _lo = _mid
        else:
            _hi = _mid
    return _lo


_BALL_TEX = None


def ball_texture():
    """主球贴图: 切面球(8 面 · 纯平涂 · 金色 · 暗缝), 由 `_facet_bake` 烘。

    ⚠️ **和杯中球共用同一套公式、同一张光照图**(`_facet_bake` 在 android_part_pile 段里,
       那段在本段之前拼接) —— 主球与杯里的球必须是同一颗球, 否则小球落袋那一瞬就穿帮。
    ⚠️ d 从 64 提到 128: 切面球的棱线是**高频内容**, 主球在 Y700 上要拉到 92px(64 的 1.44 倍),
       64 不够。而且 128 与杯中球同尺寸 ⇒ **光照图只算一份**, 反而省。
    """
    global _BALL_TEX
    if _BALL_TEX is not None:
        return _BALL_TEX
    d = 128
    buf = _facet_bake(d, (0.918, 0.702, 0.031))     # 金色 = 原球贴图中段的色标
    # ⚠️ `mipmap=True` + `min_filter="linear_mipmap_linear"`: 球在**演出时会被缩放**
    #    (进场 0.92→1.0、退场缩到 0.90), 那几帧是缩小采样; 没有 mipmap 时 Kivy 只取 2x2 个纹素,
    #    缩小就会漏采样出锯齿。Kivy 官方的 SvgWidget 就是靠 "mipmap-first" 做到无锯齿缩小的。
    #    切面球的暗缝是高频内容, 比原来的径向渐变更需要它。
    tex = Texture.create(size=(d, d), colorfmt="rgba", mipmap=True)
    tex.blit_buffer(buf, colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"                 # 放大用线性(球在真机上是放大的)
    tex.min_filter = "linear_mipmap_linear"   # 缩小走 mipmap 三线性
    _BALL_TEX = tex
    return tex


def _tex_coords_rot(angle):
    """把矩形默认的 tex_coords 绕中心转 angle 弧度(自转留在纹理里, 压扁留在 quad 上)。

    为什么需要它: 主球/球堆的"自转"和"受击压扁"原本共用同一个 `Rotate(angle=...)` ——
    而 `Rotate` 会把矩形**局部 y 轴**(=压扁轴)一起转走, 于是压扁方向取决于球滚到哪儿了,
    与"撞上了什么"无关(玩家看不到"沿撞击方向被压扁")。而物理层一直存着碰撞法线
    (`squash_nx/ny`), 渲染层**从来没用过** —— 典型的"数据齐了、接线没接"。
    拆开的办法: quad 转到**法线方向**(压扁轴 = 法线), 自转改由纹理坐标承担, 两者解耦。
    ⚠️ 旋转后四个角会采样到贴图外面, 靠 Texture 默认的 `wrap='clamp_to_edge'` 兜住;
       球贴图的四角本来就是全透明的, 所以不会看到边缘被拉伸。
    """
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for u, v in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        du, dv = u - 0.5, v - 0.5
        out.append(0.5 + du * c - dv * s)
        out.append(0.5 + du * s + dv * c)
    return out


_RAMP_TEX = {}


def _ramp_buffer(axis="v"):
    """金属渐变(灰度明暗图): 0.42 → 1.00(30% 处一条亮带) → 0.62, 两端再压 2 texel 暗边。

    轴的选择不是审美问题, 是**几何约束** —— 纹理的**行永远映射到矩形的高度**:
      'v' 出 8x64(渐变沿高度) → 给**横**着的墙(上/下墙、通道隔墙)
      'h' 出 64x8(渐变沿宽度) → 给**竖**着的墙(左右墙)和**隔板**(6x42 的细竖条)
    ⚠️ 给竖墙错用 'v', 渐变就会变成"上下走", 光看起来从侧面来。
    """
    w, h = (8, 64) if axis == "v" else (64, 8)
    n = h if axis == "v" else w
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            t = (y if axis == "v" else x) / (n - 1.0)
            if t < 0.30:
                v = 0.42 + (1.00 - 0.42) * (t / 0.30)
            else:
                v = 1.00 + (0.62 - 1.00) * ((t - 0.30) / 0.70)
            edge = min(t, 1.0 - t) * n          # 距两端的 texel 数
            if edge < 2.0:                      # 两端压暗 = 边沿遮挡
                v *= 0.30 + 0.70 * (edge / 2.0)
            c = int(255.0 * (0.0 if v < 0.0 else (1.0 if v > 1.0 else v)))
            i = (y * w + x) * 4
            buf[i] = c
            buf[i + 1] = c
            buf[i + 2] = c
            buf[i + 3] = 255
    return buf


def wall_ramp(axis="v"):
    """金属渐变贴图(按轴缓存, 一次性几百像素)。

    ⚠️ 要 `flip_vertical()` —— Kivy 的 `blit_buffer` 第 0 行落在**最下方**, 不翻的话
    "上亮下暗"会倒过来。
    （钉子那版贴图已因"小尺寸缩小采样出锯齿"**回退成矢量** —— 见钉子绘制处的说明。)
    """
    tex = _RAMP_TEX.get(axis)
    if tex is not None:
        return tex
    w, h = (8, 64) if axis == "v" else (64, 8)
    t = Texture.create(size=(w, h), colorfmt="rgba", mipmap=True)
    t.blit_buffer(bytes(_ramp_buffer(axis)), colorfmt="rgba", bufferfmt="ubyte")
    t.flip_vertical()
    t.mag_filter = "linear"
    t.min_filter = "linear_mipmap_linear"   # 墙在横向上是大倍数缩小(8px 贴图铺 1200px 宽)
    _RAMP_TEX[axis] = t
    return t


_SLOT_TEX = None


def _slot_buffer(w=64, h=36):
    """倍率槽色块贴图: 灰度明暗图(圆角 + 上亮下暗 + 上内唇高光/下内唇阴影 + 外圈暗描边)。

    ⚠️ 尺寸必须贴近槽位的**真实长宽比**。槽块逻辑尺寸约 45.7 x 26(1.76:1), 这里取 64x36
       (1.78:1)。**用正方形贴图拉过去会把圆角在 x 方向拉成椭圆角**, 一眼就假。
    ⚠️ 灰度 + 前置 Color 染色 ⇒ 结算时的**白闪**(把 `col.rgb` 置成 (1,1,1) 再恢复)原样工作,
       而且"每档倍率一个颜色"的色相语义一点不丢。**渲染处的染色值必须严格取自
       `slot_color(m)`** —— 白闪恢复那一行写的就是它, 两处一旦不同步, 闪完就回不到原色。
    """
    rad = 6.0 / 26.0 * h                 # 圆角半径按真实比例换算(逻辑 6px 圆角 / 26px 高)
    cx, cy = w / 2.0, h / 2.0
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            px, py = x + 0.5 - cx, y + 0.5 - cy
            qx = abs(px) - (cx - rad)
            qy = abs(py) - (cy - rad)
            # 圆角矩形 SDF: 负值在内部, 顺手把距离当 alpha 用 = 免费的抗锯齿
            sd = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - rad
            a = -sd / 2.0
            if a <= 0.0:
                continue
            a = 1.0 if a > 1.0 else a
            t = (y + 0.5) / h            # 0 = 顶
            v = 1.00 - 0.22 * t          # 上亮下暗
            if -2.0 < sd < 0.5:          # 靠近边缘的那一圈: 上半是内唇高光、下半是内唇阴影
                v += 0.20 if t < 0.5 else -0.24
            if sd > -1.5:                # 最外一圈压暗 = 描边
                v -= 0.30
            v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
            c = int(255.0 * v)
            i = (y * w + x) * 4
            buf[i] = c
            buf[i + 1] = c
            buf[i + 2] = c
            buf[i + 3] = int(255.0 * a)
    return buf


def slot_texture():
    """倍率槽贴图(模块级缓存)。同样要 flip —— 不然"上亮下暗"会倒过来。"""
    global _SLOT_TEX
    if _SLOT_TEX is not None:
        return _SLOT_TEX
    w, h = 64, 36
    t = Texture.create(size=(w, h), colorfmt="rgba", mipmap=True)
    t.blit_buffer(bytes(_slot_buffer(w, h)), colorfmt="rgba", bufferfmt="ubyte")
    t.flip_vertical()
    t.mag_filter = "linear"
    t.min_filter = "linear_mipmap_linear"
    _SLOT_TEX = t
    return t


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

# 方向守卫 / 沉浸重申的**主线程耗时**统计: [方向累计秒, 方向单次最慢秒, 方向次数,
# 沉浸累计秒, 沉浸次数]。⚠️ **必须定义在模块级**(不是某个类的类属性) —— 用它的人
# (`_orient_guard` / `_enter_immersive` / `_start_benchmark` / `_bench_collect_diag`)
# 写的都是**裸名**, 裸名找的是模块全局; 写成类属性会 NameError(本文件踩过一次)。
# 为什么单独立一个计数器: 这两条链**每 0.7 秒**在主线程各跑一次, 而且**跑分期间照跑**。
# 已经排掉的都是"事件路径、每球一次"的东西, 剩下能解释"1%Low 卡在某个数上不去"的,
# 恰恰是这种**周期性**的主线程停顿 —— 1%Low 只看最差的 1%(约十几帧), 每 0.7 秒来一记,
# 25 秒就是 35 记, 足够把那一档全占满。
# ⚠️ 这个 app 里 JNI/Binder 已经实测过是**灾难级的慢**(`SoundPool.play()` 单次 143.6ms、
#    平均 53.5ms) —— 所以"每 0.7 秒一次 system_server 往返"完全够格当 1%Low 的天花板。
# ⚠️ 桌面量不到(`platform != "android"` 直接 return), 只能靠真机跑分面板读那一行。
#    在没有这个数之前**不要动它** —— 砍错了没有门禁会红(selftest/fx_probe 都不走
#    android 分支), 而它管的是宽屏设备横拿抢 fullSensor、以及系统栏复活后重新隐藏。
# 方向守卫 / 沉浸重申的耗时统计:
#   [0]主线程投递累计秒 [1]主线程单次最慢秒 [2]发起次数
#   [3]工作线程累计秒   [4]工作线程单次最慢秒 [5]工作线程失败次数
#   [6]UI线程累计秒     [7]UI线程次数        [8]UI线程单次最慢秒
# ⚠️ [6]~[8] 量的是 `setSystemUiVisibility` 本身 —— 它在 **Java UI 线程**上跑, 不在我们的
#    线程里, 所以既不出现在 `_frame` 的剖析中, 也不在"主线程实算"里。桌面剖过: 我们的
#    Python 代码(整个 `_frame`)只占帧时间的 **0.9%**, 而"每帧实算"里那一大块是 Kivy 的
#    on_draw + 安卓的 Java 线程。要动安卓特有的周期性开销, 只剩这一条能量。
# ⚠️ **必须定义在模块级**(不是某个类的类属性) —— 用它的人写的都是**裸名**,
# 裸名找的是模块全局; 写成类属性会 NameError(本文件踩过一次, 探针逮住的)。
# 为什么单独立一个计数器: 这两条链**每 0.7 秒**各跑一次, 而且**跑分期间照跑** ——
# 它们是全 app 唯一的常驻周期性主线程 JNI, 而 1%Low 只看最差的 1%(约十几帧),
# 每 0.7 秒来一记正好能把那一档占满。搬出主线程之后判据是**两档对比**:
# 主线程那档该接近 0、工作线程那档接手(两边都看得见才知道是真搬走了还是没跑)。
# ⚠️ 这个 app 里 JNI/Binder 已实测过是**灾难级的慢**(SoundPool.play 单次 143.6ms),
#    所以"每 0.7 秒一次 system_server 往返"完全够格当 1%Low 的天花板。
# ⚠️ 桌面量不到(platform != "android" 直接 return), 只能靠真机跑分面板读那一行。
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
# 这两条链原来**每 0.7 秒各在主线程跑一次**, 而且是全 app 唯一的**常驻周期性主线程 JNI**。
# JNI/Binder 在本工程已实测过是灾难级的慢(SoundPool.play 单次 143.6ms), 所以它们是
# "每帧实算只有 4.7ms、却有一批**不分阶段**的慢帧(待机那帧都能烧 26.5ms CPU)"的头号嫌疑。
# 改法与发声/震动同策, 而且是本工程**已经验证过两次**的那套: 主线程只投递, 工作线程去付
# IPC 的钱; 队列建不起来就退回同步(= 改之前的行为), 绝不静默失效。
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

    ⚠️ 真正的 View 操作本来就在 UI 线程上(`runOnUiThread` 投递), 这里搬走的只是
    **`runOnUiThread` 这一次 JNI 调用**。
    ⚠️ `_immersive_task()` 是类级缓存 + 启动期预热过, 所以这里只是一次属性读 ——
    **绝不在工作线程上现造 PythonJavaClass**(那一步注册 Java 代理, 留在主线程)。
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

    ⚠️ Runnable **必须在主线程上建**(它要注册 Java 代理)。顺手把 autoclass 查表热一次,
    工作线程第一次用不必现付 AttachCurrentThread。
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
# (SoundPool.play() 143.6ms / 震动 Binder), 修法也都是同一套: 主线程只投递。
# ⚠️ **只保留最新一份**(后写覆盖先写): 配置是"当前状态"不是流水账, 中间态没有保留价值。
# ⚠️ 落盘走 **临时文件 + os.replace** 原子替换 —— 写一半被杀不会留下半个 JSON。
#    老写法 open(path,"w") 是先截断再写, 那种时刻被杀就是文件损坏(下次启动读不出来
#    = 进度清零)。这算顺带修的一个真 bug, 不只是性能。
# ⚠️ 切后台(on_pause)会 flush 一次, 保证切走时一定落了盘。
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
       同一条规矩本工程已经执行过四次(发声 665 / 震动 666 / 守卫 671 / 落盘 677):
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
        self._slot_txt_cols = []      # 9 个槽的"倍率文字颜色"指令(供 _update_slots 原地改)
        self._slot_txt_rects = []     # 9 个槽的"倍率文字"矩形(空的 size=(0,0))
        self._lamp_cols = []
        self._peg_cols = {}            # (px,py)→Color 钉子受击高亮
        self._peg_nodes = {}           # (px,py)→Rectangle(钉子贴图), 受击时改 col.rgb + 尺寸
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
            # 墙改成金属渐变贴图(灰度 + Color 染色, 颜色语义零变化)。
            # 轴按矩形宽高比选: 纹理的**行**永远映射到矩形的高, 所以竖墙必须用横过来的纹理。
            Color(*hex_rgb(COL_WALL))
            for w in g.geo["walls"]:
                x0, y0, x1, y1 = w
                Rectangle(texture=wall_ramp("v" if (x1 - x0) >= (y1 - y0) else "h"),
                          **self._rect(*w))
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
            self._peg_nodes.clear()
            # ⚠️ 钉子**保持矢量 Ellipse** —— 2026-09-13 试过贴图化两次, 都退回来了:
            #    ① 第一版(无 mipmap): 钉子贴图 64px、屏幕上只有约 12px ⇒ 5 倍缩小,
            #       而 Kivy 缩小时只取 2x2 纹素 ⇒ **欠采样、边缘出阶梯**。玩家:「锯齿感更强了」。
            #    ② 第二版(开 mipmap): 锯齿没了, 但边缘被 mip 层**平均成一片发虚的光晕**,
            #       而且整体显小 —— 变成"锯齿 vs 模糊"的取舍, 两头不讨好。
            #    ⇒ 这个尺寸上**矢量才是最优**: `Ellipse` 是显卡直接光栅化的, 边缘既清晰又平滑
            #      (还有 2x MSAA 兜着), 既不欠采样也不过采样。
            #    **判据: 屏幕上 < ~24px 的元素别走贴图这条路。**
            #    (mipmap 本身是好东西, 已开给球/玻璃/墙/槽 —— 它们在演出缩放时是真的在缩小。)
            for px, py in g.geo["pegs"]:
                col = Color(*hex_rgb(COL_PEG))
                e = Ellipse(**self._circle(px, py, PEG_R))
                self._peg_cols[(px, py)] = col
                self._peg_nodes[(px, py)] = e
            # 槽隔板
            Color(*hex_rgb(COL_BUMPER))
            for d in g.geo["dividers"]:
                Rectangle(texture=wall_ramp("h"), **self._rect(*d))   # 隔板是细竖条 -> 横轴
            # 倍率槽(圆角, 颜色随盘面)
            self._slot_cols = []
            for i in range(NUM_SLOTS):
                col = Color(*hex_rgb(slot_color(g.multipliers[i])))
                self._slot_cols.append(col)
                # 槽块从 RoundedRectangle 纯色改成"灰度明暗贴图 + slot_color 染色" ——
                # 白闪(把 col.rgb 置白再恢复)原样工作, 档位色相语义也一点不丢。
                Rectangle(texture=slot_texture(),
                          **self._rect(FIELD_L + i * SLOT_W + 2, SLOT_TOP + 3,
                                       FIELD_L + (i + 1) * SLOT_W - 2, FLOOR - 3))
            # 槽倍率文字(CoreLabel 烘成纹理; 逻辑 20px 跟盘面缩放, 手机上≈11sp)
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
            Rectangle(texture=wall_ramp("h"),      # 横截面明暗 -> 一根管子, 不是一块色板
                      **self._rect(RIGHT_INNER - 9, SLOT_TOP - 210,
                                   RIGHT_INNER - 4, SLOT_TOP - 6))
            Color(*hex_rgb(COL_FIRE))
            ty = (SLOT_TOP - 8) - MISFIRE_POWER * 200
            Rectangle(**self._rect(RIGHT_INNER - 12, ty - 1, RIGHT_INNER - 1, ty + 1))
            # 力度填充(动态)
            self._meter_col = Color(*hex_rgb(COL_METER))
            # ⚠️ 填充是**每帧改 pos/size 且改 col.rgb** 的(见下面 `if g.power > 0.01` 那段),
            #    贴图是灰度的、只挂这一次 ⇒ 蓄力那套"灰蓝 → 金黄"的变色动画一行都不用动。
            self._meter_fill = Rectangle(texture=wall_ramp("h"), pos=(0, 0), size=(0, 0))
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
        # 同上: 手绘文字, 按可见宽缩字号(隐藏档 5000% 时 "+500000" 会比屏幕还宽)
        size = fit_font_size(text, size, max(80.0, float(self.width) * 0.94), True)
        main = Label(text=text, font_size=size, bold=True,
                     color=hex_rgb(hexcolor) + (1,), size_hint=(None, None))
        main.bind(size=lambda w, _: setattr(w, "text_size", w.size))
        shadow = Label(text=text, font_size=size, bold=True,
                       color=(0, 0, 0, 0.6), size_hint=(None, None))
        shadow.bind(size=lambda w, _: setattr(w, "text_size", w.size))
        # ⚠️ **缩放入场走 GPU `Scale`, 绝不再逐帧写 `font_size`**(2026-09-13 性能优化)。
        #    原来 tick_draw 里写 `main.font_size = fs`(fs 每帧都变) —— font_size 在 Kivy 里
        #    属于 `_font_properties`, 每次赋值都会触发 `_trigger_texture` ⇒ **重新测量字形
        #    宽度 + 重新光栅化整段文字 + 重建纹理**。桌面实测: 一次中奖/未中大字(1.8s)要重测
        #    ~110 次 × 2 个 Label(main + shadow), 10 秒内 277 次, 占**全部文字重建的 65%**。
        #    同理 `color` 也在这张表里(颜色是**烘进字形纹理**的, Label 画布里那条 Color
        #    恒为 (1,1,1,1)), 所以淡出 alpha 也改成"量化 + 值没变就不写", 见 BIG_TEXT_ALPHA_STEPS。
        #    `Scale` 只是乘进 modelview 矩阵: 纹理一次生成、GPU 免费放大缩小。观感完全一致
        #    (1.0→1.2→1.0 的弹入曲线一个字没改, 且 font_size 恒定后动画更连续, 没有整数台阶)。
        #    放大 1.2 倍用线性插值, 对粗体大字只是极轻微发虚 —— 比"缩小"安全(缩小没 mipmap
        #    会闪), 所以基准纹理仍按 `size` 光栅化, 不做"按峰值预放大"。
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
            e = self._peg_nodes.get((px, py))
            if col is None:
                del self._peg_flash[(px, py)]
                continue
            elapsed = now - t0
            if elapsed > 0.30:
                col.rgb = hex_rgb(COL_PEG)
                if e is not None:
                    kw = self._circle(px, py, PEG_R)
                    e.pos, e.size = kw["pos"], kw["size"]
                del self._peg_flash[(px, py)]
            else:
                # ⚠️ 闪光色必须**避开球的颜色**。原来是电光金 #ffe500 —— 而主球是 #eab308 那一族金,
                #    撞上去两颗"球"贴在一起, 读不出"撞了"(玩家 2026-09-13 指出)。
                #    换成冰蓝: 与金球冷暖对撞最清楚, 又还在板面/钉子的冷色家族里
                #    (板 #0e1524、钉 #7b8fad、槽 #... 都是冷色), 不显得像外来元素。
                #    ⚠️ 改球色的话这里要一起看 —— 这两处是"同屏必须拉开"的一对。
                flash = (0.498, 0.910, 1.0)   # 冰蓝 #7fe8ff(原为电光金 #ffe500)
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
                    # ⚠️ 必须**同时**复位 pos 和 size。原来(Ellipse)只写 size, 中心会偏
                    #    (1 - 1/scale)/2 个半径 —— 放大 1.2 倍时偏 0.1×半径, 碰钉瞬间一晃而过看不出来;
                    #    换成 Rectangle 后左上角不动、往右下长, 偏得更显眼。
                    #    `_circle()` 本来就返回"以 (px,py) 为圆心、半径 R"的完整 pos+size, 直接用它。
                    kw = self._circle(px, py, PEG_R * scale)
                    e.pos, e.size = kw["pos"], kw["size"]
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
                # 走画布那条 Color(零重建); 拿不到才退回写 label.color + 量化
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
                # 走画布那条 Color(零重建)。两个 Label 的贴图颜色在创建时就烘好了,
                # 这里只需给它们乘一个 alpha。拿不到就退回老路。
                _cols = e.get("cols") or [None, None]
                _ok = (_fade_set(_cols[0], alpha) and _fade_set(_cols[1], alpha))
                if not _ok:
                    _q = int(alpha * BIG_TEXT_ALPHA_STEPS + 0.5) / float(BIG_TEXT_ALPHA_STEPS)
                    if _q != e.get("_qa"):
                        e["_qa"] = _q
                        main.color = e["rgb"] + (_q,)
                        shadow.color = (0, 0, 0, _q * 0.6)
                main.center = (e["cx"], e["cy"] + rise)
                shadow.center = (e["cx"] + 2, e["cy"] + rise - 2)
                # 缩放 = 纯 GPU 变换(见创建处 `_pop_sc` 的说明)。
                # ⚠️ `origin` 必须是**写完 center 之后**的当前中心 —— 大字一边缩放一边上浮,
                #    锚点不跟就会看到"绕着一个飘走的点放大"。shadow 多偏 (2,-2), 各自锚自己。
                for _lb in (main, shadow):
                    _ps = _lb._pop_sc
                    _ps.origin = _lb.center
                    _ps.x = _ps.y = sc


def _lbl_canvas_color(lbl):
    """取 `Label` 画布里那条 `Color` 指令 —— 用来做**不重建纹理**的淡出。

    ⚠️ Kivy 把颜色**烘进**字形纹理(画布里那条 `Color` 实测恒为 (1,1,1,1)), 所以写
       `label.color` 就等于"重测字形 + 重光栅化 + 重建纹理 + 上传"(实测写一次触发一次
       `texture_update`)。而淡出期 alpha 一直在变 ⇒ 中奖大字的 20 档量化 x 2 个 Label
       = 每次中奖 **40 次重建**。
    改写那条 `Color` 的 rgba ⇒ 给已烘好的纹理乘一个 alpha, 只是一次 uniform 写入,
       **零重建**(实测 texture_update 计数 0, 截图确认像素真的变淡)。
    ⚠️ 拿不到就返回 None, 调用方退回老路(写 `label.color` + 量化), 绝不静默不淡出。
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
        Clock.schedule_interval(self._frame_timed, FIXED_DT)
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
            _longest = max(rows, key=lambda w: text_px(w.text or "", base,
                                                       bool(getattr(w, "bold", False))))
            fs = fit_font_size(_longest.text or "", base, avail,
                               bool(getattr(_longest, "bold", False)))
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
        longest = max(btns, key=lambda b: text_px(b.text or "", base,
                                                  bool(getattr(b, "bold", False))))
        fs = fit_font_size(longest.text or "", base, avail,
                           bool(getattr(longest, "bold", False)))
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
        self._refresh_mute_btn()
        left_box.add_widget(self.mute_btn)
        self.round_btn = self._mk_button("每轮%d次" % self.max_plays,
            lambda _b: self._show_round_settings(), bg=COL_GREEN)
        self.round_btn.size_hint_x = None
        self.round_btn.width = dp(62)
        self.round_btn.font_size = "13sp"
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
        info.add_widget(self.balance_lbl)
        self.stats_lbl = self._mk_label("", "15sp", COL_TEXT, "center", True,
                                        size_hint_x=0.70)
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
        t2 = self._rtp_hold_start
        if t2 > 0 and not self._rtp_hold_fired and time.time() - t2 >= self.RTP_UNLOCK_HOLD:
            self._rtp_hold_fired = True
            self._ask_unlock_rtp()    # 长按 RTP_UNLOCK_HOLD 秒: 四选一档位弹窗

    def _show_bench_menu(self):
        """弹珠发射性能测试菜单弹窗: 开始测试 / 查看历史。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(12))
        title_lbl = self._fit_line(Label(text='弹珠发射性能测试', bold=True, halign='center',
                                         color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(30)), 20)
        content.add_widget(title_lbl)
        desc_lbl = Label(text='全程约 25 秒（含中奖装杯演出）。\n测试两项设备性能：\n1. 累计发射 5 颗弹珠，测屏幕渲染帧率\n2. 通过后台跑物理引擎测试 CPU 性能\nPython 写的引擎，吃单核浮点算力。',
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
        start_btn = Button(text='开始测试', font_size='17sp', bold=True,
                           background_normal='', background_color=hex_rgb(COL_FIRE) + (1,),
                           size_hint_y=None, height=dp(52))
        hist_btn = Button(text='查看历史', font_size='17sp', bold=True,
                          background_normal='', background_color=hex_rgb(COL_BTN) + (1,),
                          size_hint_y=None, height=dp(52))
        info_btn = Button(text='启动信息', font_size='17sp', bold=True,
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
        popup = self._popup(0.88, 470, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        start_btn.bind(on_release=lambda *_: (popup.dismiss(), self._start_bench_test()))
        hist_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_bench_history()))
        info_btn.bind(on_release=lambda *_: (popup.dismiss(), self._show_startup_info()))
        content.add_widget(start_btn)
        content.add_widget(hist_btn)
        content.add_widget(info_btn)
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
            return "\n".join(self.sfx.audio_detail())
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
        self._bench_saved_status = self.status_lbl.text
        self.status_lbl.text = "性能测试中…"
        self._set_controls_enabled(False)
        self._show_bench_dim()   # 第1阶段就开始: 全屏置灰
        self.game_area.center_toast("测试设备性能中", hexcolor=COL_TEXT, size=30, life=3.0)
        self._bench_toast_evt = Clock.schedule_interval(self._bench_toast_tick, 0.5)
        # ⚠️ **等启动预热跑完再采样**(2026-09-14)。采样窗口只有 7~12 秒
        #    (_target_launches = 3), 而玩家是启动后 3 秒就长按标题开跑的 —— 真机上一个
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
        """阶段1: 真实屏幕采样(on_flip, 自动发球3发), 发满后切阶段2物理吞吐。"""
        self._flip_times = []
        # ---- 诊断(2026-09-13 加): 光有"平均帧率/1%Low"没法定位卡在哪 —— 见 _bench_tag ----
        self._bench_frames = []          # [(帧间隔ms, 场景标签)]
        self._bench_gc = {}              # gen -> [次数, 总秒, 最坏秒]
        self._bench_gc_t0 = 0.0
        self._bench_cpu0 = time.process_time()
        self._bench_cpu_prev = self._bench_cpu0
        self._bench_thr_prev = _THREAD_TIME()
        _FRAME_CALLS[0] = 0
        self._bench_cpusplit0 = _cpu_split()
        self._bench_thr_prev = _THREAD_TIME()
        _TEXUPD[0] = 0
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
        self._target_launches = 3
        self._auto_evt = Clock.schedule_interval(self._auto_launch_tick, 0.5)

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
        # 量帧间隔必须用**单调钟**, 不能用 time.time(): 那是 CLOCK_REALTIME, 会被 NTP 校时/
        # 用户改时间**跳变**; 而这里记下的差值就是面板「平均帧率 / 1%Low / 10%Low / p99 / p90」
        # 的全部输入。一次 +30ms 的跳变会被原样记成"一帧 30 毫秒", 直接落进 1%Low 那一档
        # (那档只有 6~10 帧)。
        # 别顺手把别处的 time.time() 也换掉 -- 动画时间轴要的就是墙钟绝对值。
        now = time.perf_counter()
        cpu = time.process_time()
        # ⚠️ **这一格必须真的写**: `_FRAME_THR[0]` 原来只有"读进帧记录"和"面板打印",
        #    没有任何一处赋值 => 面板那格"主线程"永远是硬编码 0.0, 而格式串照常打印
        #    "主线程0.0"、看起来完全健康。最坏的一种: 专抓静默归因的面板用常量冒充测量值。
        #    (对抗性评审压测段的 1 号专家独立查出来的。)
        _tt = _THREAD_TIME()
        _FRAME_THR[0] = (_tt - getattr(self, "_bench_thr_prev", _tt)) * 1000.0
        self._bench_thr_prev = _tt
        prev = self._flip_times[-1] if self._flip_times else None
        pcpu = self._bench_cpu_prev
        self._bench_cpu_prev = cpu
        _tt = _THREAD_TIME()
        _FRAME_THR[0] = (_tt - self._bench_thr_prev) * 1000.0
        self._bench_thr_prev = _tt
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
                                            if v > 0.0002), reverse=True)[:2])))
            _FRAME_BRK.clear()
            _FRAME_PROBE[0] = 0
            _FRAME_PROBE[1] = 0
            _FRAME_PROBE[2] = 0

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
        self._render_lows = {}
        self._render_pct = {}
        if len(flips) >= 2:
            gaps = [flips[i + 1] - flips[i] for i in range(len(flips) - 1)]
            s = sorted(gaps)
            self._render_fps = 1.0 / s[len(s) // 2] if s[len(s) // 2] > 0 else 0.0
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
            self._render_1low = 0.0
        self._bench_diag = self._bench_collect_diag()
        self._wait_idle_then_bench()

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
        out["p50"] = pk(0.50)
        out["p99"] = pk(0.99)
        out["max"] = gaps[-1]
        # 最慢的 3 帧 + 当时在演什么 + **那一帧真烧了多少 CPU**。
        # 后两个数一起看才分流: 实算 ≈ 帧间隔 ⇒ 算出来的(处理器瓶颈);
        # 实算很小 ⇒ 等出来的(GC/IO/显卡/驱动阻塞) —— 真机上这是唯一能分清的地方。
        # 带上它在 `_bench_frames` 里的**下标** —— 三个最慢帧是不是**同一段忙碌里的连续帧**,
        # 看下标就知道(现在看不出来)。
        by_worst = [x for _i, x in sorted(enumerate(fr), key=lambda p: -p[1][0])[:3]]
        # 每一帧带上"自算"(_frame 在本线程上的耗时, 第 6 个字段)。
        # [帧间隔, 场景, 全进程实算, 本线程自算, 这一帧是不是启动预热]
        # [帧间隔, 场景, 全进程实算, 主线程, 自算, 是否预热, 本帧最大的两笔子步骤]
        # [帧间隔, 场景, 全进程实算, 主线程(x[7]), 自算(x[5]), 是否预热(x[6]), 子步骤(x[8])]
        _idx_of = {}
        for _i, _x in enumerate(fr):
            _idx_of[id(_x)] = _i
        out["worst"] = [[x[0], x[1], x[2], (x[7] if len(x) > 7 else 0.0),
                         (x[5] if len(x) > 5 else 0.0),
                         (x[6] if len(x) > 6 else 0),
                         (x[9] if len(x) > 9 else ()),      # breaks —— 加字段时别忘同步
                         _idx_of.get(id(x), -1)] for x in by_worst]
        # 全程自算的中位数 —— 与"实算"并排看: 两者都小 ⇒ 主线程既没算也没被我们的代码占,
        # 帧时间就是框架/出图的开销(优化我们的代码没用)。
        _self_sorted = sorted((x[5] if len(x) > 5 else 0.0) for x in fr)
        out["self_p50"] = _self_sorted[len(_self_sorted) // 2] if _self_sorted else 0.0
        out["self_max"] = _self_sorted[-1] if _self_sorted else 0.0
        # 主线程 CPU 与"自算"的**差额 = `_frame` 外面那一大块**(Kivy 渲染 / 延迟文字重排)。
        # ⚠️ 索引别记错: 帧记录尾部依次是 ..., _FRAME_SELF, _FRAME_PROBE[2], _FRAME_THR, breaks
        #    ⇒ **主线程在 [7]、子步骤在 [8]**。v0.6.82 写反了, 于是 thr_p50 拿到元组, 面板那行
        #    `%.1f` TypeError, 外面那个 except 把整块诊断**静默**返回空串。
        _thr_sorted = sorted((x[7] if len(x) > 7 else 0.0) for x in fr)
        out["thr_p50"] = _thr_sorted[len(_thr_sorted) // 2] if _thr_sorted else 0.0
        out["thr_max"] = _thr_sorted[-1] if _thr_sorted else 0.0
        out["texupd"] = _TEXUPD[0]
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
        try:
            _w20 = sorted(fr, key=lambda x: -x[0])[:20]
            _pos = [(x[8] if len(x) > 8 else -1) for x in _w20]
            _pos = [p for p in _pos if p is not None]
            _pos.sort()
            out["w20_pos"] = _pos
            out["w20_near"] = sum(1 for p in _pos if p <= 20)
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
        # ⚠️ 门槛**自适应**(2026-09-14 修): 原来硬编码 `BENCH_SLOW_MS = 90`, 而本机最慢帧
        #    只有 40ms ⇒ `_slow` **恒为空集** ⇒ 下面"慢帧的节拍 / 慢帧里在发声 / 在震动 /
        #    在启动预热"四行**永远不打印**。一个专门用来定位卡顿的面板, 在这台设备上把
        #    最该看的那几行静默掉了(压测段的专家挑出来的, 核对属实)。
        #    改成"中位帧的两倍, 但至少 25 毫秒" —— 每台设备按自己的节拍算。
        _slow_ms = max(25.0, 2.0 * out["p50"])
        out["slow_ms"] = _slow_ms
        _slow = [x for x in fr if x[0] >= _slow_ms]
        out["slow_n"] = len(_slow)
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
        # 方向守卫 / 沉浸重申: 每 0.7 秒各一次的主线程 JNI, **跑分期间照跑**。
        # 它是"周期性停顿"这一类里唯一的常驻项, 而 1%Low 只看最差的那十几帧 ——
        # 每 0.7 秒来一记正好能把那一档占满。单次够大(几毫秒以上)就该把它挪出主线程。
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
        title_lbl = self._fit_line(Label(text='性能测试', bold=True,
                                         halign='center', color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 20)
        content.add_widget(title_lbl)
        _lows = getattr(self, "_render_lows", {}) or {}
        _pct = getattr(self, "_render_pct", {}) or {}
        if _lows:
            # 排版(玩家 2026-09-13 定稿): 平均帧率**独占一行**, 其余四个两两一行。
            _low_txt = ('平均帧率： %.1f\n'
                        '1%%Low：%.1f    10%%Low：%.1f\n'
                        'p99帧率：%.1f    p90帧率：%.1f') % (
                            render_fps, _lows.get(1, 0.0), _lows.get(10, 0.0),
                            _pct.get(99, 0.0), _pct.get(90, 0.0))
        else:
            _low_txt = '平均帧率： %.1f\n1%%Low帧率：%.1f' % (render_fps, render_1low)
        # ⚠️ 这一屏**不放**版本/制作日期(用户 2026-09-11 定稿: "性能测试的成绩面板别加").
        # ⚠️ **2026-09-14 玩家改主意了**: 跑完分要能一眼看出"这是哪个版本的包跑出来的"
        #    (他手上有 PC 与两代 Y700 好几份成绩互相对照, 没版本号根本对不上号)。
        #    按他给的方案加在 **"安卓版本后面"**(不是标题后面), 和机器名排在一起。
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
        _dev_ver = (dev + "  " + _ver) if _ver and _ver not in dev else dev
        score = ('%s\n'
                 '运算速度：每秒 %d 步模拟\n'
                 '每次发射：需 %.0f 步模拟(用时 %.1f 毫秒)\n'
                 '%s') % (_dev_ver, int(phys_fps), avg_frames, cost_ms, _low_txt)
        score_lbl = Label(text=score, font_size='17sp', halign='left', valign='top',
                          color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(130))
        self._auto_h(score_lbl, dp(130), dp(6))
        content.add_widget(score_lbl)

        _sep = Widget(size_hint_y=None, height=dp(1))
        with _sep.canvas.before:                      # 同 `_row_bg` 的写法
            Color(*hex_rgb(COL_DIV))
            _sep._line = Rectangle(pos=_sep.pos, size=_sep.size)
        _sep.bind(pos=lambda w, *_: setattr(w._line, "pos", w.pos),
                  size=lambda w, *_: setattr(w._line, "size", w.size))
        content.add_widget(_sep)

        diag_lbl = Label(text=self._bench_diag_text(), font_size='15sp',
                         halign='left', valign='top',
                         color=hex_rgb(COL_SUB) + (1,), size_hint_y=None, height=dp(110))
        self._auto_h(diag_lbl, dp(0), dp(0))
        content.add_widget(diag_lbl)

        popup = self._popup(0.90, 460, title='', content=content,
                            auto_dismiss=True, separator_height=0)
        popup.open()
        self._popup_fit_content(popup, content)
        self.status_lbl.text = getattr(self, '_bench_saved_status', '按住蓄力发射')
        self._set_controls_enabled(True)
        self._bench_running = False
        self._bench_start = 0.0

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
            #    这些都跑在别的 Clock 回调里, 在它外面; Kivy 自己的 on_draw 也在它外面。
            #    所以"自算很小"**不等于**"我们没干活": 桌面实测那几帧 实算 62.5 而自算 0.1,
            #    正是预热(球纹理烘焙)干的。所以**预热帧必须直接标出来**。
            def _frame_cell(_g, _t, _c, _h, _s, _b, _k=(), _i=-1):
                # ⚠️ 占位符个数不同的分支**必须分开格式化** —— 写成
                #    ('A' if c else 'B') % (g,t,c) 会在一个分支抛 TypeError, 而外层
                #    那个 except 会把它静默吞掉(面板诊断整块消失)。踩过一次了。
                # **实算 / 主线程 / 自算** 并排 —— 玩家 2026-09-14 质疑"9 毫秒是不是小头",
                # 那三帧的账确实对不上(36毫秒那帧自算只有 0.3)。"主线程"是能看见 Kivy 渲染
                # 那一块的那一格。
                if _c >= 1.0 or _s >= 1.0:
                    _x = '%.0f毫秒(%s·实算%.1f·主线程%.1f·自算%.1f)' % (_g, _t, _c, _h, _s)
                else:
                    _x = '%.0f毫秒(%s)' % (_g, _t)
                if _b:
                    _x += '·预热'
                # 本帧最大的两笔子步骤 —— **"那 9 毫秒到底花在哪"的直接答案**。
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
            # 判据: "单次最慢 ≥ 5 毫秒" 就值得把它挪出主线程 —— 它每 0.7 秒来一次,
            # 1%Low 只看最差十几帧, 25 秒里它有 35 次机会把那一档占满。
            if d.get("jni_n"):
                parts.append('方向守卫： %d 次（每0.7秒）· 主线程单次最慢 %.2f 毫秒 · 累计 %.1f 毫秒'
                             '　工作线程累计 %.0f 毫秒（最慢 %.1f）· 失败 %d 次'
                             % (d.get("jni_n", 0), d.get("jni_main_worst", 0.0),
                                d.get("jni_main_sum", 0.0), d.get("jni_bg_sum", 0.0),
                                d.get("jni_bg_worst", 0.0), d.get("jni_err", 0)))
            # 系统栏那一次重申的**真实代价** —— 跑在 Java UI 线程上, 只有这里看得见。
            # 判据: 单次 >=5 毫秒 ⇒ 每 0.7 秒重申一次是在拿 UI 线程换一个系统本来就会
            # 自动隐藏的东西(IMMERSIVE_STICKY 自己会收回), 那就该把周期拉长。
            if d.get("ui_n"):
                parts.append('系统栏重申： %d 次 · 单次最慢 %.1f 毫秒 · 累计 %.0f 毫秒（UI线程，不在主线程实算里）'
                             % (d.get("ui_n", 0), d.get("ui_worst", 0.0),
                                d.get("ui_sum", 0.0)))
            # 慢帧那一行只在真有慢帧时出(它回答的是"停顿长什么样", 没停顿就没什么可说的)。
            if d.get("slow_n"):
                # ⚠️ "几帧在预热"必须单独报 —— 那几帧是**启动期**的账, 与稳态卡顿不是一回事
                #    (桌面实测: 最慢的 11 帧全在启动 0.6 秒内, 而那些帧我们自己的代码只花了
                #    0.03~0.10 毫秒)。混在一起看会把"启动慢"误读成"玩起来卡"。
                parts.append('慢帧： %d 帧≥%.0f毫秒（平均每 %.2f 秒一次）· 其中 %d 帧在发声'
                             ' / %d 帧在震动 / %d 帧在启动预热'
                             % (d["slow_n"], d.get("slow_ms", 25.0), d["slow_beat"] / 1000.0,
                                d.get("slow_snd", 0), d.get("slow_vib", 0),
                                d.get("slow_bake", 0)))
            # 瓶颈判断: 每帧真正花在计算上的时间 vs 帧间隔。差得远 = 大头在等画面
            #   (GPU 出图 / 垂直同步); 快追平 = 处理器就是瓶颈。
            _cpu = float(d.get("cpu_per_frame", 0.0))
            _p50 = float(d["p50"])
            # ⚠️ **Windows 上这一整行不成立**(2026-09-14 补): `process_time` 的精度只有
            #    15.6ms(上面刚写过), 可"瓶颈"仍然拿它除帧间隔, 于是 PC 上会打出
            #    「处理器算不过来 · 每帧实算 18.4 / 帧间隔 16.5」这种自相矛盾的结论
            #    (同一份面板里还有"20毫秒(待机·实算31.2)" —— 20ms 的帧不可能烧 31ms)。
            #    正是本仓库警告过的形状: **一个专抓静默归因的面板, 自己在做静默归因**。
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
            # 三个数并排才能分流"谁在吃时间": 实算大·主线程小 => 工作线程在忙;
            # 主线程大·_frame 小 => **Kivy 渲染 / 文字重排**在吃; _frame 大 => 我们自己的代码。
            _win_s = max(0.001, d.get("p50", 12.5) * max(1, d.get("n", 0)) / 1000.0)
            # 节拍: 直接回答"帧循环是不是自由跑" —— 比值 < 1 说明呈现比逻辑更新更频繁。
            _fc = int(d.get("frame_calls", 0))
            parts.append('节拍： maxfps=%s vsync=%s · `_frame` %d 次 / 采样 %d 帧（比值 %.2f）'
                         % (d.get("maxfps", "?"), d.get("vsync", "?"), _fc,
                            d.get("n", 0), _fc / float(max(1, d.get("n", 1)))))
            # 内核态占比当**分流器**: 高则查 JNI/Binder/文件写, 低则查 GIL。
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
            if d.get("cfg_n"):
                parts.append('　存档落盘： %d 次（工作线程）· 单次最慢 %.1f 毫秒'
                             % (d["cfg_n"], d.get("cfg_worst", 0.0)))
            # 冻结生效与否**必须显示** —— 否则"GC 没再拖后腿"既可能是真冻结了, 也可能是
            # 根本没跑到(这个仓库专门栽过这种静默)。
            if d.get("gc_frozen"):
                # 冻结**前后各强制做一次全量回收**的实测读数 —— 这是"冻结到底买到了什么"在
                # 真机上的直接答案, 不用靠推断(桌面量不出来: 30 秒总共才 0.6 毫秒的 GC)。
                parts.append('内存冻结： 已冻结 %d 个常驻对象 · 全量回收 %.1f -> %.1f 毫秒'
                             % (d.get("gc_frozen", 0), d.get("gc_frz_before", 0.0),
                                d.get("gc_frz_after", 0.0)))
            if _win:
                parts.append('⚠️ 每帧实算这一项在 Windows 上测不准(系统计时粒度 15.6 毫秒), '
                             '「瓶颈」不判 —— 要看它请用安卓机的成绩')
            return '\n'.join(parts)
        except Exception:
            return ''

    def _show_bench_history(self):
        """弹珠发射性能测试历史弹窗(最近100次, 每行只显示每秒步数)。"""
        content = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(8))
        title_lbl = self._fit_line(Label(text='测试历史（最近100次）', bold=True,
                                         halign='center', color=hex_rgb(COL_TEXT) + (1,),
                                         size_hint_y=None, height=dp(28)), 19)
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
            _rows = []          # 收齐后**整组**定一个字号(见 _fit_uniform)
            for r in reversed(self.bench_history[-100:]):
                # "2026-09-11 19:22    每秒 10971 步" 要 266px, 360dp 机器上只有 253px ⇒
                # 原来折成两行而格子只有 30px 高, 第二行直接被裁掉(玩家看到半行字)。
                # ⚠️ 字号/行高与另外两个列表弹窗**对齐**(见 _fit_uniform 上方那段说明):
                #    这里原来是 17sp/30dp —— 全 app 最大的正文, 比主界面正文(14~15)还大一档,
                #    而它是个要塞很多行的滚动列表。统一到 15sp(Body 档) + 26dp 行高:
                #    同一个滚动框里能多放约两行(玩家: 「这个设计的目的是放更多内容的」)。
                row = Label(
                    text='%s    每秒 %d 步' % (r.get('time', '--'), r.get('phys_fps', 0)),
                    font_size='15sp', halign='left', valign='middle',
                    color=hex_rgb(COL_TEXT) + (1,), size_hint_y=None, height=dp(26))
                row.bind(width=lambda w, *_: setattr(w, 'text_size', (w.width, None)))
                _rows.append(row)
                inner.add_widget(row)
            # ⚠️ 必须 `sp(17)` 而不是 `17.0` —— 这个形参是**绝对字号(px)**, 不是 sp 档位。
            #    传裸 17.0 在 density=2 的机器上就只有一半大(实测被探针的数字逮住:
            #    同一批行 17.0 而别的 17sp 行是 34.0)。
            self._fit_uniform(_rows, sp(15))     # 与另外两个列表弹窗同一个基准(见上)
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

    # ---- 期望返还比例: 常驻档 + 需要长按解锁的隐藏档 ----
    RTP_TIERS = (("80%", 0.80), ("120%", 1.20), ("200%", 2.00), ("360%", 3.60))
    # ⚠️ **隐藏档**(玩家 2026-09-11 定稿的入口): 长按"期望返还比例"标签 -> 弹窗选一个 ->
    #    **不保存**。长按时长见 `RTP_UNLOCK_HOLD`(2026-10-13: 3s -> 2s)。
    #    所以它只是内存里的一个开关: ① 存档那侧的档位白名单**不含** 50.0(重开时那份存档被忽略);
    #    ② `_boards` 里那份盘面也无所谓(选不中就用不到)。
    # 彩蛋档位(长按"期望返还比例"解锁, **不保存**)。弹窗让玩家从四选一里挑(或"关闭隐藏")。
    # ⚠️ 三档**全部"必中"**(plinko.py 的 K_DIST = {9: 1.0}), 所以每格均值被恒等式钉死在
    #    档位上 —— 实际落格分布见 plinko.py 的 VALUE_SHAPE 注释。
    RTP_HIDDEN = (("1000%", 10.0), ("2000%", 20.0), ("5000%", 50.0))
    RTP_UNLOCK_HOLD = 2.0      # 长按多久触发隐藏档弹窗(5.0 -> 3.0 -> 2.0, 玩家 2026-10-13 定稿)

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
            # 精简(玩家: 「把那几个换行的文字精简下」): 去掉尾省略号 —— "结算中"本身已含进行义,
            # 128px 收到 112px, 于是 360dp 的右侧份额(110px)里也塞得下, 不必再靠缩字号。
            self.status_lbl.text = "命中 x%d · 结算中" % m   # 中间态: 第一秒不发空, 余额"冻结"不像 bug
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
            # 那个双空格是笔误, 收成单空格(119 -> 116px)
            self.status_lbl.text = ("中奖! +%d (x%d)" % (payout, m)) if payout > 0 else "未中"
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
        if reroll:
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
            path = self._config_path()          # 路径在主线程算好(App 不能从工作线程问)
            if _cfg_post(cfg, path):
                return
            with open(path, "w") as f:          # 兜底: 与改之前逐字相同
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
        #    自适应字号 `_fit1`。实测(桌面, 数次数): 滚动时余额文字每秒变几十次,
        #    去掉之后 **53.8 秒只变 2 次**。
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
# ⚠️ "装杯"是**嵌套在"板面"里面**的(tick_draw -> win_fx.tick -> _redraw), 面板上这几个数
#    **不能相加**, 只按"谁最大"读。
_brk_wrap(GameArea, "tick_draw", "板面")
_brk_wrap(RootWidget, "park_ball", "重掷")
_brk_wrap(RootWidget, "_fit1", "字号")
_brk_wrap(WinPileFX, "_redraw", "装杯")


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
            # ⚠️ **周期从 0.7s 拉到 2.5s**(2026-09-14)。原来这条和方向守卫同节奏, 但两者
            #    的代价**根本不在同一个线程上**: 方向守卫那趟 IPC 已经搬到工作线程(v0.6.71),
            #    而这条的 `setSystemUiVisibility` 跑在 **Java UI 线程**, 每 0.7 秒让窗口
            #    重算一次 inset + 走一趟 SurfaceFlinger。桌面剖析证明我们的 Python 只占
            #    帧时间 0.9%, 所以安卓特有的周期性开销只剩这一条。
            #    为什么敢拉长: ① 回前台有 on_resume() 兜(那条不动); ② 转屏有 _frame 的
            #    窗口尺寸轮询立即重申(双保险); ③ **IMMERSIVE_STICKY 本身就是"玩家从边缘
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
        瘦长手机: 持续重申竖屏锁(7), 任何运行时横屏自报都被顶掉。"""
        if platform != "android":
            return
        _t0 = time.perf_counter()
        ok = _guard_post("orient")
        if not ok:                       # 队列建不起来 / 满: 退回同步 = 改之前的行为
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
                    #    判据 —— 若单次 >=5 毫秒, 那"每 0.7 秒重申一次"就是在拿 UI 线程
                    #    换一个系统本来就自动隐藏的东西(IMMERSIVE_STICKY 会自己收回)。
                    try:
                        from jnius import autoclass
                        act = autoclass("org.kivy.android.PythonActivity").mActivity
                        View = autoclass("android.view.View")
                        # ⚠️ `_t0` **必须打在两次 autoclass 之后**: 原来打在它们之前, 于是面板上
                        #    "系统栏重申·单次最慢"测的是**探针自己的查表开销**, 不是
                        #    setSystemUiVisibility 的代价。(压测段 2 号专家指出。)
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
