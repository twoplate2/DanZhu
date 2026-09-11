# 交接文档：安卓启动画面「突兀的放大/缩小」问题

> 生成：2026-09-11。供其他 AI 接手排查。
> 涉及文件：`android/buildozer.spec`（打包配置，**在 git**）、`tools/android_part_ui.py`（UI 源，**不在 git**）、
> `tools/fx_probe.py`（自检探针，**不在 git**）、`android/main.py`（**生成物**，在 git，**绝不手改**）。
>
> 本文档每条论断都标了来源（**作者实测** / **读了源码** / **仅是推断**）。请优先信前两类。

---

## 0. 一句话现状

**症状**：安卓真机上，从系统启动图到游戏画面之间，那张图会**突然变小**（玩家原话），
并且在加载页上**完全静止不动** —— 而 **PC 上一切正常**。

**最可能的真凶**（§4，作者已用真 Kivy 2.3.1 复现）：
`AsyncImage` 的 `fit_mode` **默认是 `"scale-down"`**，它把**真正画出去的矩形**夹死在
`min(盒子, 贴图原生像素 1080×1920)`。于是盒子超过 1080×1920 的机器上，我们永远只画 1080×1920
（**0.75×，"小图"**），而 ±1.8% 的呼吸被夹成 **0 像素**（**"静止不动"**）。

⚠️ **但有一个反例必须先排除**（§4.4）：这个机制在 **1080 宽的屏幕**上，
"我们画的"和"系统画的"**一样大**，预测**不该有跳变**。
所以**第一步是先确认玩家那台设备的屏幕物理分辨率**。

---

## 1. 玩家报告的症状（原话，按时间顺序）

| # | 玩家原话 | 版本 |
|---|---|---|
| 1 | 「在真实安卓机器上，启动画面还是没有过渡，**都是各种突兀的放大和缩小**」 | v0.6.29 前后 |
| 2 | 「新版本的表现是：**固定一个启动画面静止不动一段时间，突然变成小图，然后突然就进游戏了**」 | v0.6.30/31 |
| 3 | 「但是 **pc 上会缩放，是个动态的**，你这个呼吸效果加的太有用了」 | v0.6.31（**PC 正常**） |
| 4 | 「**v0.6.32 和 6.29 版本在安卓手机上没有启动缩放上的变化**」 | v0.6.32 |
| 5 | 「点击**重放冷启动**，整个画面也不会缩放」 | v0.6.31 |

**两个关键差分**：

- **PC 正常、安卓不正常** —— 极大地收窄了候选（§4.6 有解释）。
- **v0.6.32 的修复完全无效** —— 因为那条修复的立论是空的（§3.3）。

**⚠️ 仍未拿到、且必须先拿到的两条信息**：

1. **那台安卓设备的屏幕物理分辨率**（尤其**宽度是不是 1080**）—— §4 的机制成立与否全看它。
2. 那台设备是**瘦长手机**（比 16:9 更细、被锁竖屏）还是**宽屏手机/平板**（走横屏反旋转路径）。

---

## 2. 代码事实

### 2.1 加载页是怎么来的（`tools/android_part_ui.py`）

- `PlinkoApp.build()`：`sfx = Sfx(...)` 之后
  ```python
  self.veil = None
  if not sfx.audio_ready():
      self.veil = _LoadVeil(size_hint=(1, 1))
      anchor.add_widget(self.veil)          # anchor 在 LandLayer 里面
      self.rootw._load_veil = self.veil
      self.rootw._load_veil_host = anchor
  ```
- `_LoadVeil`：背景矩形用 `VEIL_BG = "#0b1220"`（= `presplash.png` 自己的底色，四角实测一致）；
  `AsyncImage(source=presplash.png, size_hint=(None,None), mipmap=True)`；`self._t0 = time.time()`
- `_LoadVeil.tick()`：由 `RootWidget._frame` **每帧**调用 → 算 `_base_w/_base_h` → 写 `self._img.size/pos`
- `_veil_fit(w, h)`：**当前是 cover**（铺满、溢出裁掉）：`bw = max(h * 1080/1920, w)`
- `_veil_scale(t)`：**只有呼吸** `1.0 + 0.018 * sin(2π t / 1.9)`（"进场从小到大"那段已删）
- `RootWidget._frame` 摘页判据：`if self.sfx.audio_ready(): ... _veil.drop()`

### 2.2 打包配置（`android/buildozer.spec`）

```
android.presplash_color = #0b1220     # 注释：必须和 presplash.png 边缘同色，避免闪屏黑闪
presplash.filename = %(source.dir)s/presplash.png
```
`presplash.png` = **1080×1920**；内容纵向占 19%~81%、横向占 13%~87%（作者像素扫描）；四角 `#0b1220`。

### 2.3 会改变窗口尺寸的时序（**重点**）

`PlinkoApp.build()` 末尾：
```python
if platform == "android":
    Clock.schedule_once(lambda *_: self._apply_orientation(), 1.0)   # setRequestedOrientation
    Clock.schedule_interval(self._orient_guard, 0.7)
    Clock.schedule_once(lambda *_: self._enter_immersive(), 1.0)     # 隐藏状态栏/导航栏
    Clock.schedule_interval(self._enter_immersive, 0.7)
```
- `_enter_immersive()` **隐藏系统栏 ⇒ 会改变窗口可用尺寸**。
- `_apply_orientation()` / `_orient_guard()` **重申方向 ⇒ 可能改变窗口宽高**。
- `RootWidget._frame` 里有 `if ws != self._last_win_size:` 分支 → `LandLayer.apply_orientation()`。
- `LandLayer.apply_orientation()`：横屏时把整棵树 `Rotate(angle, origin=(w/2, h/2))`，
  并把 `self._anchor.size = (h, w)`（**宽高对调**）。
  ⚠️ 加载页在这棵树**里面**，系统 splash 不经过它。

---

## 3. 已经试过的三版（**都没解决，其中一次改错了方向**）

### 3.1 v0.6.27 —— 把"按高度铺满"改成 contain（完整放下）

起因：玩家问「371×660 这个是不是不合理啊 我记得手机屏幕都是很高分辨率的?」。
**结果**：玩家说「然后迅速变小」。**方向错了一半**（§4.3）。

### 3.2 v0.6.29 —— 把 contain 改回 cover（铺满）+ 删掉"进场从小到大"

改回 cover 的依据是代码里的一句**错注释**：

> 「系统 presplash 是拿这张图当 `windowBackground` **铺满**画的」

**这句是错的**（§4.2，p4a 源码是 `FIT_CENTER` = contain）。
⇒ **v0.6.29 是照着错注释修错了方向。**

删掉"进场从小到大"倒是**对的**：它和 splash 的尺寸冲突，而且真机上 `_t0` 从构造算起、
第一帧要等 presplash 撤掉才画，多半来不及播。

**结果**：玩家说「还是各种突兀的放大和缩小」。

### 3.3 v0.6.32 —— 尺寸来源从"控件自己"改成"父容器"，并每帧现算

当时的实测发现（**这个缺陷是真的，但不是本 bug 的原因**）：
`_sync` 绑在控件自己的 `size` 上，而 Kivy 里控件**被布局之前** `size` 是默认的 `(100, 100)`
⇒ 算出 **100×178 的小图**：

```
① __init__ 之后(未加树)   veil.wh=(100,100)   base=(100,177.8)  img=(100,178)
③ 第1帧后                veil.wh=(432,936)   base=(526.5,936)  img=(527,937)
```

**但它对玩家的症状完全无效。** 专家团的判词（作者认同）：

> **v0.6.32 改的是"盒子的来源"，而画出去的矩形从来不由盒子决定** —— 那条修复**立论是空的**。

**结果**：玩家说「v0.6.32 和 6.29 版本在安卓手机上没有启动缩放上的变化」。

---

## 4. 真凶

### 4.1 机制：`AsyncImage` 真正画的是 `norm_image_size`，不是我们设的 `size`

- 我们写的是 `self._img.size`（**盒子**）。
- **它画的是 `norm_image_size`**（`kivy/data/style.kv` 里 `size: self.norm_image_size`）。
- `norm_image_size` 由 `fit_mode` 决定，而 **`fit_mode` 默认值是 `"scale-down"`**
  （`kivy/uix/image.py`：`fit_mode = OptionProperty(...)`）。
- `scale-down` = "**不放大**" ⇒ 画出来的矩形 = `min(盒子, 贴图原生像素)`。

**我们从来没设过 `fit_mode`。** 所以无论把盒子算成 cover / contain / 整窗，
只要盒子在某一维超过 **1080×1920**，画出去的东西就被**夹死在 1080×1920**。

### 4.2 系统 splash 那层是 `FIT_CENTER`（= contain），**不是铺满**

专家团查到的 p4a `v2024.01.21`（`bootstraps/sdl2/.../PythonActivity.java`）：

- `:521-524` —— `ViewGroup.LayoutParams.FILL_PARENT` + **`ImageView.ScaleType.FIT_CENTER`**
- `:461-468` 的 `setBackgroundColor` 是**留白**
- `:509-517` —— `openRawResource + BitmapFactory.decodeStream`，**不做密度缩放**

⇒ 系统那层 = **等比缩放、居中、留白**（contain），留白填 `presplash_color`。

⚠️ 与代码注释那句"铺满"**相反**。

### 4.3 两次失败串起来看

| 版本 | `_veil_fit` 给的盒子 | 实际画出去（被 `scale-down` 夹） | 系统那层 | 差 |
|---|---|---|---|---|
| v0.6.27（contain 盒） | 1080×1920 | 1080×1920 | 1080×1920 | 0× |
| v0.6.29/32（cover 盒） | 1316×2340 | **1080×1920** | 1080×1920 | 0× |

⇒ **在 1080 宽的屏上，改盒子是白改。**
这解释了「v0.6.27 和 v0.6.29 表现一样」，也解释了「v0.6.32 无效」。

### 4.4 ⚠️ 必须先排除的反例（**最重要的一条**）

§4.5 的表显示：**1080 宽的屏上，我们画的 = 系统画的 = 1080×1920 ⇒ 预测"不该有跳变"。**

> **如果玩家那台设备是 1080 宽的屏，本节这套机制解释不了"突然变成小图"。**

**先拿屏幕物理分辨率再往下走。**
（作者自己的平板是 1600 宽；PC 上测的窗口是 540×660 —— 两个都**不是** 1080 宽。）

### 4.5 作者的独立实测（真 Kivy 2.3.1 + 真 `presplash.png`）

```
Kivy 2.3.1
盒子 1600x2560, 贴图 1080x1920:
  im.size (盒子)     = [1600, 2560]
  im.norm_image_size = [1080, 1920.0]   <= 真正画出去的
  fit_mode           = 'scale-down'

剖面          盒子          我们画的(默认)   系统(FIT_CENTER)
16:10        1600x2560     1080x1920       1440x2560     <== 不一致(0.75x)
19.5:9       1080x2340     1080x1920       1080x1920     （一致）
20:9         1080x2400     1080x1920       1080x1920     （一致）
15:10        1620x2160     1080x1920       1215x2160     <== 不一致
21:9         1080x2520     1080x1920       1080x1920     （一致）
720p          720x1600      720x1280        720x1280     （一致）
1080p        1080x1920     1080x1920       1080x1920     （一致）
```

**读法**：夹断只在**盒子超过 1080×1920** 时才咬。`_veil_fit` 是 cover，盒子 = `max(h×0.5625, w) × h`：

- **1080 宽手机**（2340 高）→ 盒子 1316×2340 → 夹成 1080×1920；系统也是 1080×1920
  ⇒ **一样大，无跳变**，但**呼吸被夹成 0 像素**（"静止不动"是必然的）。
- **1600 宽平板** → 盒子 1440×2560 → 夹成 1080×1920，系统 1440×2560
  ⇒ **我们只有 0.75×，"变小"** ✓
- **720×1600** → 盒子 900×1600 → 夹成 720×1280，系统 720×1280 ⇒ 一致。

⚠️ 同一个夹断在不同机器上给出的**方向相反** —— 这**正好对上玩家原话里的"放大"和"缩小"**（症状 1 说的是"各种"）。

### 4.6 顺带解释"PC 上正常"

PC 窗口 540×660（或 `--landscape` 的 1000×1740）：cover 盒子 = 371×660，**小于 1080×1920**
⇒ **夹断不咬** ⇒ 画出来的正是算出来的 ⇒ **尺寸对、呼吸可见** ✓
—— 与玩家观察（症状 3）完全一致。

### 4.7 附带发现：占位图

`kivy/loader.py` 的 `Loader.loading_image` 是一张 **32×32** 占位图，`AsyncImage` 在贴图解码完成前画的是它。
桌面实测：头 3 帧 `texture.size == (32,32)`，第 4 帧才变 1080×1920。

⚠️ **这让下面那步改动必须配套**：改成 `contain` 后，"32×32 贴图 + contain + 盒子 1600×2560"
会被**放大**到 1600×1600 —— **从"屏幕正中一个小点"变成"一整屏糊斑"**，比现在更难看。

---

## 5. 建议的改法（**三步，第三步不是可选项**）

全部落在 `tools/android_part_ui.py`（出货文件一个字不手改，改完重跑生成器）。

### 改动 1：`_veil_fit` 从 cover 盒改成"**整窗**"

```python
def _veil_fit(w, h):
    """加载图的基准尺寸 —— **就是整窗**(不裁不缩, 怎么摆放交给 fit_mode)。"""
    try:
        return float(w), float(h)
    except Exception:
        return 0.0, 0.0
```
保留函数名（调用点与 fx_probe 都不用动），把"怎么摆放"交还给 `fit_mode`。

### 改动 2：`AsyncImage` 加 `fit_mode="contain"`

```python
self._img = AsyncImage(source=_src, size_hint=(None, None), mipmap=True,
                       fit_mode="contain")
```

**为什么是 contain 而不是 fill**：系统那层是 `FIT_CENTER`（contain），**contain 才可能逐像素一致**。
专家团里有人第一版建议 `fill`，**被另外两位独立攻击并撤回**：
`fill` 会让"画的 = 盒子"，而盒子是 cover 盒 ⇒ 在 1080×2400 手机上从"完全无缝"变成
"1.25× 放大 + 两侧裁掉"，**把目前唯一一批完全正确的机型改坏**。

### 改动 3（**必须配套**）：占位图门

```python
    def _img_ready(self):
        """贴图到位没有 —— 没到位时**不画**(免得把 32x32 的占位图放大成一整屏糊斑)。"""
        try:
            t = self._img.texture
            return t is not None and tuple(t.size) != (32, 32)
        except Exception:
            return False
```
并在 `tick()` 开头加 `or not self._img_ready()`。

### ⚠️ 不能做的事（专家团的一致结论）

- **不要**动 t=1.0s 的 `_enter_immersive` / `_apply_orientation`（把窗口变化挪别处）——
  那是另一层面的竞态，证据不足。**默认两条都不做。**
- **不要**回到 cover / fill。
- **不要**手改 `android/main.py`。

---

## 6. 怎么验证

### 6.1 桌面上可以做（**唯一有分辨力的判据**）

⚠️ **必须用几何判据，不能用纯函数断言。**
现在 `fx_probe.py` 里那条"15:10~21:9 全部铺满"钉的是 `_veil_fit` 的**返回值**，
而画出去的矩形不是它 —— **那是假绿灯，正是这一轮所有人踩过的坑。**

```python
from kivy.uix.image import Image as _KImage
_im = _KImage(source=_vsrc, size_hint=(None, None), fit_mode="contain")
_bad = []
for _nm, _w, _h in (("16:10", 1600, 2560), ("20:9", 1080, 2400),
                    ("19.5:9", 1080, 2340), ("15:10", 1620, 2160),
                    ("21:9", 1080, 2520), ("720p", 720, 1600)):
    _im.size = (_w, _h); _k = min(_w / 1080.0, _h / 1920.0)
    _g = _im.norm_image_size
    if abs(_g[0] - 1080 * _k) > 1.0 or abs(_g[1] - 1920 * _k) > 1.0:
        _bad.append("%s: 画出 %.0fx%.0f, 系统 %.0fx%.0f" % (_nm, _g[0], _g[1], 1080*_k, 1920*_k))
check(not _bad, "加载图画出来的矩形**逐像素等于**系统 presplash 的 FIT_CENTER", "; ".join(_bad))
```

**阴性对照（必做）**：去掉 `fit_mode="contain"`（回到默认 `scale-down`）⇒
1600×2560 那格应算出 1080×1920 vs 系统 1440×2560 ⇒ **门禁必须变红**。
（作者已实测：`contain` 给 1440×2560，`scale-down` 给 1080×1920，**有分辨力**。）

⚠️ 夹具必须同时满足两个条件，否则永远绿：
① 盒宽 > 1080（测夹断）② 宽高比不是 16:9（测 cover≠contain）。
不需要跑时钟：本地 PNG 的 `Image.texture` 同步加载，`norm_image_size` 立刻可读。

### 6.2 必须真机才能判的

1. **系统 splash 的实际绘制矩形** —— 录屏慢放，量"非底色(#0b1220)像素的外接框"。
2. **t=1.0s 的沉浸/方向重申有没有改窗口** —— 见下方命令。
3. **加载页到底被看见多久** —— 决定"静止不动一段时间"指的是系统那张还是我们这张。

### 6.3 可用命令

```bash
# 逐帧证据(需先加临时诊断日志; 一次构建就能定)
adb logcat -c; adb logcat -v time -s python:D SDL:V | grep -E "VEILDBG|appConfirmedActive|Running main function|loading screen"
# 窗口到底多大、有没有被塞进兼容盒
adb shell dumpsys window displays | grep -E "cur=|app=|mCurrentFocus"
adb shell wm size; adb shell wm size --physical
# 录屏慢放(最贴身): 数清楚跳几次、每跳多大
adb shell screenrecord --time-limit 8 --bit-rate 8000000 /sdcard/v.mp4
adb pull /sdcard/v.mp4 . && ffmpeg -i v.mp4 -vf fps=30 f_%04d.png
#   对每帧量"非底色像素外接框"宽高, 相邻帧突变处 = 一次跳
# 抓系统 splash 那一版(它一消失就抓不到了)
adb shell "for i in $(seq 1 40); do screencap -p /sdcard/s$i.png; done"
```

---

## 7. 未决清单

1. **玩家设备的屏幕物理分辨率**（尤其宽度是不是 1080）—— **§4 的机制成立与否全看这条。**
2. **`Window.size` 在安卓上是 px 还是 dp** —— 决定夹断的方向符号。
   旁证偏 px（`_device_is_wide()` 用物理分辨率），但没有一条真机打印。
   判读：诊断行打 `win=`，1080×2400 是 px，约 533×853 是 dp。
3. **残余一跳**：t=1.0s 的沉浸/方向重申到底有没有改掉窗口尺寸？
   今天被夹断**完全吸收**（零像素变化）；拆掉夹断之后会**裸露出来**（平板竖屏估 +4~8%）。
   仓库记录分裂（`BUILD_APK.md` §3.22 说竖屏启动沉浸标志上不去；之后加了 `runOnUiThread` 修复）。
   判读：诊断行 `box=` 在 drop 之前出现几次。
4. **横屏冷启动**：平板横拿启动时，加载页在 `LandLayer` 里被 `Rotate(90)`，系统那张是正的。
   这条路径**谁都没验过**。
5. **"与系统同规则"是否等于"玩家看不到跳"**：v0.6.27 用的就是 FIT_CENTER 公式，玩家却说"迅速变小"。
   本文档的解释是"那一版仍被 `scale-down` 夹死"，但**这只是解释，不是证据**。
   判读：装 contain 版后录屏量交接缝两侧缩放比是否为 1.00。

---

## 8. 项目纪律（**动手前必读**）

- **`android/main.py` 是生成物，绝不手改**。改源 → `python tools/build_android_main.py` → 再 `--check`。
- `tools/` 与 `../wingui/` **不在任何 git 仓库**；`android/` 仓库里只有生成物。
- **出包必须 bump `android/buildozer.spec` 的 `version`** —— 它是"装的是哪个包"的唯一肉眼证据。
- **绝不软锁**：加载页必须有硬超时能摘掉（`SFX_READY_TIMEOUT = 6.0`），这是项目红线。
- 改完必跑：`python android/main.py --selftest`、`python tools/fx_probe.py`、`python tools/build_android_main.py --check`。
- **改动要小**。作者会因"文件超标 / 改太多"而退回。
- 一次云构建 **25~90 分钟**：方案要么一次到位，要么自带诊断。
- 玩家反复强调：**别自作主张**。

---

## 附：当前版本状态

- 最新出货版本：**v0.6.33**（`android/buildozer.spec` 的 `version`）。
- 未推送的本地改动：**无**（工作区干净）。
- `android/main.py` 与 `tools/` 下的源**同步**（`--check` 通过）。
- 本文档写作时，**§5 的三步改动一个字都还没做**。
