# 用 GitHub Actions 云构建 Python → Android APK (DanZhu 经验版)

把 Python GUI 应用(Kivy)打成 Android APK,**全程不需要本地装 Android SDK / NDK / Buildozer**。
所有编译在 GitHub Actions 的 Ubuntu runner 上完成,Windows 开发者也能用。

本文分四部分:
1. **通用流水线**(沙漏项目验证过的版本组合,本项目继续沿用,5 次构建全绿)
2. **本项目的工作流**(main.py 是生成物!改代码别改错地方)
3. **tkinter → Kivy 移植弯路集**(本项目实测踩出的坑,下次直接绕)
4. **运维小贴士**(限流/代理/构建时长)

---

## 一、通用流水线(已验证 5 次构建全成功)

### 最稳组合 (2026)

```
Kivy 2.3.0  +  python-for-android v2024.01.21  +  buildozer 1.5.0  +  cython<3.0  +  host Python 3.10
```

**最关键的一行**(没有它必失败): `buildozer.spec` 里 `p4a.branch = v2024.01.21`。
新版 p4a 默认下载 Python 3.14 alpha,C API 变了(`_PyLong_AsByteArray` 从 5 参变 6 参),
Kivy 2.3 的 Cython 代码编译不过。锁 2024 年 tag 即可。tag 名格式必须严格 `v2024.01.21`
(带 v、月日补零),写 `2024.1.21` 会报 `Remote branch not found`。

### 项目骨架

```
project/
├── main.py                       # Kivy 应用入口(必须叫这个名)
├── buildozer.spec                # 构建配置
├── icon.png                      # 1024×1024 启动器图标
├── presplash.png                 # 1080×1920 启动屏
├── fonts/NotoSansSC-Medium.otf   # 中文字体(不打 = 汉字全豆腐块)
├── .github/workflows/build-apk.yml
└── .gitignore                    # .buildozer/ bin/ *.apk __pycache__/
```

### buildozer.spec 要点(本项目实测版)

```ini
[app]
title = 跳跳的弹珠机
package.name = plinko              # 小写/无空格/无中文
package.domain = org.danzhu
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
source.include_patterns = fonts/*.otf   # 子目录资源必须显式列, 否则不进 APK
version = 0.4.0
requirements = python3,kivy==2.3.0,pyjnius
p4a.branch = v2024.01.21          # 见上, 命根子
orientation = portrait
fullscreen = 0
android.permissions = VIBRATE     # 要震动必须声明, 否则 pyjnius 调用静默失败
android.api = 31
android.minapi = 21
android.ndk = 25b                 # 25c+/26 有兼容问题
android.archs = arm64-v8a,armeabi-v7a
icon.filename = %(source.dir)s/icon.png
android.presplash_color = #0b1220 # 必须和 presplash.png 边缘同色, 防闪屏黑闪
presplash.filename = %(source.dir)s/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1
```

### CI 流程 (.github/workflows/build-apk.yml)

要点(完整文件在仓库里):
- `runs-on: ubuntu-22.04`(别用 24.04,buildozer 没适配)
- host Python 3.10 + Java 17(temurin)
- `pip install "cython<3.0" "buildozer==1.5.0"` 两个都锁
- 缓存 `~/.buildozer` + `.buildozer`,key 里带 spec 哈希; 脏缓存就 bump key 里的版本号
- `yes | buildozer android debug` → `actions/upload-artifact` 收 `bin/*.apk`

**构建时长**: 首次 15-20 分钟(下载 SDK/NDK + 编译 Python/Kivy),
**命中缓存后 2.5-3 分钟**。改代码频繁推送毫无压力。

### 中文字体(不打必豆腐块)

- Android 不带中文字体,Kivy 默认 Roboto 不含 CJK → 所有汉字显示成 □□
- 解法: `fonts/NotoSansSC-Medium.otf`(~8MB, Apache 2.0 可商用可分发)
- `main.py` 最早期 `LabelBase.register(name="Roboto", fn_regular=字体路径)`
  —— 用**同名覆盖**默认字体, 所有控件自动生效, 不用逐个设 `font_name=`
- spec 的 `source.include_patterns` 必须列 `fonts/*.otf`(子目录不自动打包)
- **别用** 微软雅黑/黑体(商业字体, 打包分发侵权)

---

## 二、本项目的工作流: main.py 是生成物!

**最重要的结构决策**: `android/main.py` 不是手写的, 是 `../tools/build_android_main.py`
从 PC 版 `plinko.py` **原样抽取**纯逻辑段 + 手写的 Kivy 段拼接生成的。

```
plinko.py (2278行, tkinter版)
   ├─ 常量/几何/物理/引导/落点预定/盘面生成   ──┐
   ├─ 34个音效合成 + bake_bank + pcm_to_wav   ──┤  原样抽取(字符串切片)
   ├─ winmm _WaveOut (Windows 桌面后端)       ──┤
   └─ selftest + sfx_check                    ──┘
                                                  ↓ build_android_main.py
tools/android_part_head.py     (kivy imports/中文字体注册/hex_rgb)
tools/android_part_backends.py (SoundPool/SoundLoader 后端 + Sfx 总线)
tools/android_part_ui.py       (GameArea/RootWidget/App/冒烟)
                                                  ↓
                                        android/main.py (生成物, 勿手改)
```

**为什么这么做**: 移植最大的风险是"手抄 1000 行物理/音效代码抄错一个字符"。
生成器方案下, PC 版改了物理 → 重跑 `python ../tools/build_android_main.py` 就同步了,
两个版本永远不会漂移。下次做类似移植, 第一天就该把生成器搭好。

**改动入口**:
| 要改什么 | 改哪里 |
|---|---|
| 物理/几何/盘面/音效配方/自测 | `../plinko.py` 然后重跑生成器 |
| Kivy 界面/布局/帧循环 | `tools/android_part_ui.py` |
| 音效后端(SoundPool 等) | `tools/android_part_backends.py` |
| 字体/参数解析/入口 | `tools/android_part_head.py` |

### 桌面测试循环(打包前必跑)

```
python main.py --selftest   # 物理/RTP/哑火/音效体检(抽自 plinko.py, 与 PC 版同一套门禁)
python main.py --smoke      # 自动蓄力/发射/必中盘/哑火/余额不足飘字 + 截图到 %TEMP%/plinko_smoke
python main.py              # 手动玩(540×960 窗口, 16:9)
python main.py --nosound    # 静音
```

窗口尺寸回归: `python ../scratch/screen_16x10.py`(1920×1200)/ `screen_narrow.py`(360×740)。

注意 `--selftest` 的 RTP 判定是统计检验(±0.05 门, n=40000, σ≈0.015),
**偶尔 3σ 抖动假失败(实测 ~2/10 次), 重跑一次过了就没事**, 别去改物理。

---

## 三、tkinter → Kivy 移植弯路集(全是用时间换的)

### 3.1 Kivy 会抢你的命令行参数

`python main.py --selftest` 直接被 Kivy 的 argparse 吃掉并报 "option not recognized"。
**在 import kivy 之前** `os.environ.setdefault("KIVY_NO_ARGS", "1")`, 自己的参数自己解析。

### 3.2 图形指令只收关键字参数

`Rectangle(pos, size)` 报 `TypeError: __init__() takes exactly 0 positional arguments`。
必须 `Rectangle(pos=..., size=...)`。建议封装 `_rect()`/`_circle()` 返回 kwargs dict,
调用点 `Rectangle(**self._rect(...))`。

### 3.3 Window.bind(size=...) 对启动期程序化 resize 不可靠

启动时 `Window.size = (540, 960)` 是异步生效的, 之后设 `(1920, 1200)` 时
bind 的回调**根本没触发**(实测回调读到的是 Kivy 默认 800×600)。
**解法: 每帧轮询 `Window.width/height`, 变了才重算布局**(一次元组比较, 免费)。
桌面最大化/手机旋屏也顺带兼容。

### 3.4 Window.screenshot 是异步的

它绑定到下一次 on_flip 才抓帧。**在同一个 Clock 回调里"改状态 + 截图"**,
抓到的一定是改动前的旧帧(排查了我半个小时)。改状态和截图必须拆到两个
`Clock.schedule_once` 里, 至少隔一帧。

### 3.5 BoxLayout 里两个 flex 子控件会 50/50 平分

顶栏右侧容器里放了 `[Widget() spacer][status_label]`, 两个都 size_hint=1,
结果状态文字只分到一半宽度, 长文本折行溢出。**占位弹簧一侧用固定宽或干脆别放**。

### 3.6 Kivy Label 不裁剪超宽文本

texture 比 text_size 宽时会**画到相邻控件的地盘**(不会自动省略号)。
长文案要么缩短, 要么算好宽度。顶栏严格居中的做法: 左右两个**等 flex** 容器
夹住固定宽标题, 与两侧内容长短无关。

### 3.7 手机字体层级: sp 和"场景缩放 px"是两套体系, 别混

- UI 文字(按钮/标签)用 **sp**(密度自适应, 手机上物理可读)
- 画布内文字(槽位倍率)要跟盘面一起缩放 → 用**逻辑 px**(`int(20 * scene_scale)` 烘 CoreLabel 纹理)
- **中奖大字这类 Hero 字按屏宽占比设计, 不跟场景缩**: PC 上 40px 是画布的 7.7%,
  但手机上画布=整块屏, 跟场景缩出来只有屏宽 10%, 毫无冲击力 → 直接给 48sp
- 早期版本把三者混用, 用户评价"字体大小严重错乱没有规划"

### 3.8 Android 上 PIL 用不了? 程序化生成纹理

p4a 的 pillow recipe 能不用就不用。PC 版的 PIL 渐变小球, 在 Kivy 里
`Texture.create(size=(64,64))` + `blit_buffer` 直接灌 RGBA 字节,
纯 Python 算径向渐变, 零依赖, 效果一致。

### 3.9 SoundPool 是短音效唯一正道

- `SoundLoader` 在 Android 底层走 MediaPlayer, 循环有 50-100ms gap, 且并发差
- `SoundPool`: 烘焙出的 PCM 写成 WAV 缓存文件 → `pool.load(path)`(异步)
- **不再用 OnLoadCompleteListener**: 那个跨线程 JNI 代理失灵就全库永久静音,
  而 `play()` 对未加载完的 sample 本来就返回 0 什么都不做
- 缓存用 stamp 文件记指纹(mtime+size+seed+SR)+逐文件字节数校验, 防截断 WAV
- 36 个音效总时长 ~11.7s
- 冷启动 `PlinkoApp.build()` 中 `Sfx(sync=True)` 同步烘焙, 完成后才建 UI
- **Cold-bake 后必须 `time.sleep(0.5)`**: SoundPool.load() 是异步的, 36 个 WAV
  一口气加载完但后台解码器来不及处理。不加这 0.5s 延迟, 首次安装打开 App 必然全静音(
  第二次打开走缓存路径, 速度快, SoundPool 有时间缓冲, 所以能响)。
  这个 bug 极难排查: 杀进程重开就正常, 开发者永远以为是"偶发"。
- 切后台: `SoundPool.autoPause()/autoResume()` 挂到 `App.on_pause/on_resume`

### 3.10 震动: 权限 + 最大振幅

`buildozer.spec` 加 `android.permissions = VIBRATE`, 否则 pyjnius 调
`Vibrator.vibrate()` 静默失败(没权限不抛异常, 直接不振, 最难排查的一类)。

**振幅用 255 而不是 DEFAULT_AMPLITUDE(-1)**:
`VibrationEffect.createOneShot(ms, 255)` — DEFAULT_AMPLITUDE 通常只输出 ~50% 功率,
手机上震感微弱。255 是 API 允许的最大值。

### 3.11 单 Label 多色文字用 BBCode

历史记录"绿+灰"混排不用建一堆子控件:
`Label(markup=True)`, 文本 `"[color=39d98a]+50[/color] [color=8fa0c4]0[/color]"`。

### 3.12 画布文字 = CoreLabel 烘纹理

Kivy canvas 没有 draw_text。`CoreLabel(text=..., font_size=...)` → `refresh()`
→ `Rectangle(texture=cl.texture, ...)`, 要变色就在前面放 `Color`。

### 3.13 竖屏锁定不能只靠 manifest

`buildozer.spec` 的 `orientation = portrait` 只生成 manifest 声明。部分设备/ROM 的
系统级自动旋转(重力感应)会覆盖 manifest, 导致横屏时 `_fit_width()` 把内容列算成细条。

**解法: Android 运行时强制锁定**:
```python
if platform == "android":
    from jnius import autoclass
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    activity.setRequestedOrientation(1)  # SCREEN_ORIENTATION_PORTRAIT
```
运行时 API 优先级高于 manifest, 能覆盖系统级自动旋转。

**再加一道布局容错**: `_fit_width()` 检测 `Window.width > Window.height * 1.2` 时
改用宽度作为限制维度, 防止万一锁定失效时布局崩成细条。

### 3.14 字体固定尺寸在窄屏/横屏下会换行重叠

所有 UI 文字用 sp 单位(密度自适应), 但 sp 不随窗口宽度缩放。横屏时 RootWidget
宽度变窄, 19sp 的数字在 34dp 行高里必然换行。

**解法: 双因子缩放 `_font_scale` + `_ui_scale`**:
```python
self._font_scale = min(1.0, self.width / dp(360))   # 宽度缩放
self._ui_scale    = min(1.0, Window.height / dp(680)) # 高度缩放(横屏激活)
```
`_apply_sizes()` 方法在窗口尺寸变化时统一更新所有固定 UI 元素(5 行高、所有按钮宽、
所有字号), 复合因子 `fs = _font_scale * _ui_scale`。竖屏时两因子均为 1.0, 不影响原有布局。

**关键**: `_fit_width()` 计算可用高度时必须用**缩放后**的固定高度(`scaled_fixed`),
不能用未缩放的 `dp(FIXED_H)`。否则手机上 density=2.75 时会差 325px,
桌面 density=1 时差很小 —— 这就是"桌面拉伸完美, 手机横屏崩溃"的根因。


## 四、运维小贴士

| 坑 | 解法 |
|---|---|
| 匿名 GitHub REST API 限流 60 次/小时 | 盯构建状态改爬 HTML 页面(`grep '"conclusion":"success"'`), 不吃 API 配额 |
| 代理/VPN 断线 push 失败("Could not connect") | 本地 commit 不会丢, 挂个 `until curl github.com; do sleep 30; done` 探测, 恢复后再 push |
| 首次构建 15-20 分钟以为卡死 | 正常, 在下 SDK/NDK; 后续缓存命中 ~3 分钟 |
| selftest 偶发失败 | RTP 统计检验的 3σ 抖动, 重跑一次 |
| 截图验证布局 | Kivy `Window.screenshot()` + 直接读 PNG 检查, 比肉眼开窗口快 |

## 本项目构建记录

- GitHub: https://github.com/twoplate2/DanZhu
- 2026-07-28: 5 次构建全绿(Build #1 首构建 15m37s, #3 起缓存命中 ~2m49s)
- PC 版: `../plinko.py`(tkinter, 单文件)
- 参考来源: `E:\AI_Tools\other\shalou_claude\android\BUILD_APK.md`(通用版, 沙漏项目)
