# 用 GitHub Actions 云构建 Python → Android APK

把 Python GUI 应用(Kivy)打成 Android APK,**全程不需要本地装 Android SDK / NDK / Buildozer**。所有编译在 GitHub Actions 的 Ubuntu runner 上完成,Windows 开发者也能用。

适用场景:Windows 开发(Buildozer 不支持 Windows 原生)、不想折腾 Linux/WSL/Docker。

---

## TL;DR (2026 年踩坑后的最稳组合)

```
Kivy 2.3.0  +  python-for-android v2024.01.21  +  buildozer 1.5.0  +  cython<3.0  +  host Python 3.10
```

**最关键的一行配置**(没有它必失败):
```ini
# buildozer.spec
p4a.branch = v2024.01.21
```

为什么这行至关重要,见下方 [Python 版本陷阱](#-python-版本陷阱-2026-年起核心) 小节。

---

## 技术栈

| 组件 | 作用 |
|---|---|
| **Kivy 2.3.0** | 跨平台 Python GUI,支持打包 Android。tkinter 不能直接转 Android,要重写 |
| **Buildozer 1.5.0** | Kivy 官方打包工具,调用 python-for-android 编译 APK。**必须锁版本**,否则装最新版可能引入新坑 |
| **python-for-android (p4a) v2024.01.21** | buildozer 底层调用的工具,负责把 Python 解释器和依赖编译进 APK。**必须锁 2024 年 tag**,见陷阱小节 |
| **GitHub Actions** | 云端 Ubuntu 22.04 runner,跑 Buildozer + 上传 APK 产物 |
| **Git Credential Manager** | Windows 下 push 自动用缓存的 GitHub 凭证(不用每次输密码) |

---

## 项目骨架

```
project/
├── main.py                       # Kivy 应用入口(必须叫这个名)
├── buildozer.spec                # 构建配置
├── icon.png                      # 1024×1024 启动器图标
├── presplash.png                 # 1080×1920 启动屏
├── <你的资源>.wav/png/...         # 音频/图片等
├── .github/workflows/
│   └── build-apk.yml             # CI 配置
├── .gitignore
└── README.md
```

---

## 关键文件模板

### `main.py` — Kivy app 必备约定

```python
from kivy.app import App
from kivy.utils import platform
from kivy.core.window import Window

class MyApp(App):
    def build(self):
        # 桌面预览模拟手机窗口;Android 上让系统决定
        if platform != "android":
            Window.size = (420, 760)
        return ...root_widget...

    # Android 生命周期 — on_pause 必须返回 True 保持 GL 上下文
    def on_pause(self):
        return True

    def on_resume(self):
        return True

if __name__ == "__main__":
    MyApp().run()
```

**资源路径**用 `os.path.join(os.path.dirname(os.path.abspath(__file__)), name)`,桌面和 Android 上都正确(buildozer 把资源解压到 `main.py` 同级目录)。

### `buildozer.spec` — 最小可工作版

```ini
[app]
title = 沙漏
package.name = hourglass              # 小写/无空格/无中文
package.domain = org.shalou
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
source.include_patterns = sand_loop.wav   # 显式列非 .py 资源,否则不会进 APK
version = 0.1.0
requirements = python3,kivy==2.3.0

# ⚠️ 关键:锁定 python-for-android 到 2024 年的 tag
# 不锁的话,2026 年的新版 p4a 默认下载 Python 3.14 alpha,与 Kivy 2.3 C API 不兼容必失败
# tag 名格式必须严格:v + 年份.月份补零.日期补零(写错任何一处 git clone 都会失败)
p4a.branch = v2024.01.21

orientation = portrait
fullscreen = 0
android.api = 31
android.minapi = 21
android.ndk = 25b                     # 25c+/26 当前有兼容性问题
android.archs = arm64-v8a,armeabi-v7a
icon.filename = %(source.dir)s/icon.png
android.presplash_color = #fdf6e3     # 必须和 app 背景同色,避免闪屏黑闪
presplash.filename = %(source.dir)s/presplash.png

[buildozer]
log_level = 2
warn_on_root = 1
```

### `.github/workflows/build-apk.yml` — CI 模板

```yaml
name: Build APK
on:
  push:
    branches: [ main, master ]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-22.04          # 不要用 24.04, buildozer 还没适配
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.10'   # host Python(给 buildozer 用),与嵌入 APK 的 target Python 是两回事
      - uses: actions/setup-java@v5
        with:
          distribution: 'temurin'
          java-version: '17'       # Android Gradle 要求 Java 17

      - name: Install system deps
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends \
            build-essential git zip unzip openjdk-17-jdk \
            python3-pip autoconf libtool pkg-config zlib1g-dev \
            libncurses-dev libncursesw5-dev libtinfo6 cmake \
            libffi-dev libssl-dev automake

      - name: Install buildozer & Cython (锁 2024 兼容版本)
        run: |
          python -m pip install --upgrade pip wheel
          pip install "cython<3.0" "buildozer==1.5.0"   # 两个都必须锁版本

      - uses: actions/cache@v5
        with:
          path: |
            ~/.buildozer
            .buildozer
          key: buildozer-v3-${{ runner.os }}-${{ hashFiles('buildozer.spec') }}
          restore-keys: |
            buildozer-v3-${{ runner.os }}-

      - name: Build APK (debug)
        run: yes | buildozer android debug
        env:
          BUILDOZER_WARN_ON_ROOT: "0"

      - uses: actions/upload-artifact@v7
        with:
          name: app-apk
          path: bin/*.apk
          if-no-files-found: error
          retention-days: 30
```

> 如果之前有失败的构建留下了脏缓存,把 cache key 的 `v3` 升到 `v4`(任意改个数字都行),会强制丢弃旧缓存重头来。

### `.gitignore` — 至少这几条

```
.buildozer/
bin/
*.apk
__pycache__/
*.pyc
```

---

## 完整流程(顺序操作)

1. **写代码**: 完成 `main.py` + `buildozer.spec` + workflow.yml + 资源文件
2. **本地 commit**:
   ```
   git init -b main
   git add .
   git commit -m "Initial commit"
   ```
3. **网页建空 repo**: https://github.com/new — **不勾任何** README/.gitignore/license(否则要 merge),Public 方便下载 Artifacts
   > ⚠️ **每个项目单独建 repo**——沙漏和计算器用的不同的仓库地址。别把新 app push 到旧项目里。
4. **关联远端 + push**:
   ```
   git remote add origin https://github.com/<owner>/<repo>.git
   git push -u origin main
   ```
   如果 Windows GCM 之前登录过 GitHub 账号,无需输密码
5. **等 Actions**: 进 repo Actions 标签页 — 黄圆点(跑中) → 绿勾(成功) → 红叉(失败,点进看 log)
6. **下载 APK**: run 详情页底部 **Artifacts** → `app-apk.zip` → 解压得 `.apk` → 传手机安装(需开"未知来源"权限)

**首次构建 15-20 分钟**(下载 Android SDK/NDK + 编译 Python/Kivy)。**后续命中 `~/.buildozer` 缓存 5-8 分钟**。

---

## 常见错误与解法

| 错误信息(节选) | 原因 | 解法 |
|---|---|---|
| `_PyLong_AsByteArray` too few arguments (expected 6, have 5) | p4a 默认下 Python 3.14 alpha,C API 变了,Kivy 2.3 编译失败 | spec 加 `p4a.branch = v2024.01.21` |
| `fatal: Remote branch X not found in upstream origin` | `p4a.branch` 的 tag 名格式错 | 必须 `v2024.01.21`(带 v + 月日补零),不是 `2024.1.21` 也不是 `2024.01.21` |
| `Cython compilation failed` / `error in Cython` | Cython 3.x 不兼容 Kivy 2.3 / 老 p4a | workflow 锁 `pip install "cython<3.0"` |
| `JAVA_HOME not set` | runner 没配 Java | `actions/setup-java@v5` + `java-version: '17'` |
| `NDK download timeout` / SDK 下载失败 | Google CDN 偶发 | 重跑 workflow,通常二次能过(或换时段) |
| `KeyError: 'AndroidManifest.xml'` | api/minapi/ndk 版本不匹配 | 锁 api=31, minapi=21, ndk=25b |
| Resource not found at runtime | wav/png 没进 APK | spec 加 `source.include_patterns = xxx.wav` |
| 闪屏后黑屏 | `presplash_color` ≠ app 背景 | spec 改 `android.presplash_color = #你的BG色` |
| **APK 装手机后所有汉字显示为豆腐块/乱码** | Android 系统不带中文字体,Kivy 默认 Roboto 字体不含 CJK 字形 | 项目里加 `fonts/NotoSansSC-Medium.otf`(~8MB,见下方[中文字体配置](#-中文字体配置)) + `LabelBase.register(name="Roboto", fn_regular=...)` 覆盖默认字体 + spec `source.include_patterns` 加 `fonts/*.otf` |
| App 切后台回来 GL 黑屏 | 没实现生命周期 | `on_pause` 返 True, `on_resume` 重载音频/纹理 |
| **Android 上短 wav 循环每隔几秒"咔"一下/有断续** | Kivy `SoundLoader` 在 Android 底层走 Java `MediaPlayer.setLooping(True)`,该 API 循环短音频每次切换有 50-100ms 静音 gap | **两步并用**: (a) `pyjnius` 调 `android.media.SoundPool` 替换 MediaPlayer,(b) wav 改长 (15s) + overlap-add crossfade 消除边界量化 click——只换 SoundPool 仍可能有每 ~10s 一次的微小残余 click。见下方[音效无缝循环](#-音效无缝循环-android) |
| **TextInput 文字无法居中** (没有 `text_align` 属性) | Kivy `TextInput` 设计缺陷,只暴露 `padding` 没暴露 `align` | 写子类用 `CoreLabel` 测真实文本宽度 → 动态计算 `padding=[(w-tw)/2, (h-th)/2, ...]`,bind `text`/`size`/`font_size` 自动刷新 |
| Windows 双击 .py 闪退 | tk.Frame 的 padx/pady 不接受 tuple | 把 padx/pady 移到 `pack()` 调用,或换 ttk.Frame.padding |

---

## ⚠️ Python 版本陷阱 (2026 年起,核心)

**这是一个会让所有 2024 年还能跑的旧 buildozer.spec 在 2026 年全部失败的"时间炸弹"**——本项目踩了 4 次坑才搞清楚,记录完整以便后续避坑。

### 现象

构建日志中编译 `kivy/graphics/compiler.c` 时报:
```
error: too few arguments to function call, expected 6, have 5
                                                  is_little, !is_unsigned);
.../python3.14/cpython/longobject.h:84:17: note: '_PyLong_AsByteArray' declared here
```

### 根因

python-for-android (p4a) 在 2026 年默认下载 **Python 3.14 alpha** 作为嵌入到 APK 的 target Python。Python 3.14 修改了 C API:

```c
// Python 3.13 及之前 — 5 个参数
int _PyLong_AsByteArray(PyLongObject* v, unsigned char* bytes, size_t n,
                        int little_endian, int is_signed);

// Python 3.14 — 6 个参数(新增 is_unsigned)
int _PyLong_AsByteArray(PyLongObject* v, unsigned char* bytes, size_t n,
                        int little_endian, int is_signed, int is_unsigned);
```

Kivy 2.3.0 的 Cython 生成代码 (`kivy/graphics/compiler.c`) 是按旧版 5 参数签名调用的,链接到 3.14 的头文件就编译不过。

### ⛔ 不要走的弯路

下面 5 条**全部无效**(本项目 4 次 push 全部踩过),节省你的时间:

| 尝试的修复 | 为什么不行 |
|---|---|
| workflow `env: PYTHON3_VERSION: "3.12"` | p4a 根本不读这个环境变量名 |
| workflow `env: P4A_PYTHON_VERSION: "3.12"` | p4a 也不读这个,纯属臆测的命名 |
| workflow `env: PYTHON_VERSION: "3.12"` | 这只影响 host 侧 setup-python,与嵌入 APK 的 target Python 完全是两回事 |
| spec 加 `p4a.python_version = 3.12` | **buildozer 不识别这个 key**,被静默忽略,完全没作用 |
| 删 `~/.buildozer/.../Python-3.14*` 文件 | 治标,p4a 重新跑会按 recipe 默认值再下一次 |

### ✅ 正确解法:锁 p4a 版本

让 buildozer 用 2024 年的 p4a tag(那时 p4a 默认 Python 还是 3.10/3.11):

**第一步:`buildozer.spec` 添加**
```ini
p4a.branch = v2024.01.21
```

`p4a.branch` 这个 key 是 buildozer 真正识别的,会触发 `git clone -b <branch> https://github.com/kivy/python-for-android.git`。

**第二步:tag 名格式必须严格** ⚠️

| 写法 | 结果 |
|---|---|
| `p4a.branch = v2024.01.21` | ✅ 正确(本项目 2026-05-21 验证可用) |
| `p4a.branch = 2024.1.21` | ❌ `fatal: Remote branch 2024.1.21 not found`(月份没补零、缺 v 前缀) |
| `p4a.branch = 2024.01.21` | ❌ 同上(缺 v 前缀) |
| `p4a.branch = V2024.01.21` | ❌ 大小写敏感 |

要查实际可用的 tag 列表:
```bash
git ls-remote --tags https://github.com/kivy/python-for-android.git
```

**第三步:workflow 同步锁 buildozer 版本**
```yaml
pip install "cython<3.0" "buildozer==1.5.0"
```

避免新版 buildozer 引入的不兼容行为(比如 spec key 解析变化)覆盖你的配置。

**第四步(可选):破旧缓存**

如果之前失败过,GitHub Actions 缓存里可能残留 Python 3.14 的下载产物。改 cache key 强制丢弃:
```yaml
key: buildozer-v3-${{ runner.os }}-${{ hashFiles('buildozer.spec') }}
#            ^^ 把 v3 改成 v4 / v5,任意改个不一样的就行
```

### 未来可考虑的更新路径

如果想跟进新 Python 版本,可以试:
- **p4a tag 升到 `v2026.05.09`** — 2026 年 5 月发布的新 stable,理论上已修复 Python 3.14 兼容
- **Kivy 升到 2.4 / 2.5** — 新版 Cython 应已适配 Python 3.14 的新 C API

但**升级前先备份能跑的 spec**——目前(2026-05-21)已知最稳定的就是顶部 TL;DR 那套组合。

---

## 🀄 中文字体配置

### 现象

APK 装到手机后,所有汉字显示成豆腐块(□□□□)或方框乱码,而英文/数字正常。桌面预览(`python main.py` 在 Windows 上)看不到这个问题,**只在装到 Android 手机后才暴露**。

### 根因

- Android 系统不带中文字体(只有少量预装机型例外)
- Kivy 默认字体名 `Roboto` 是 Google 拉丁字母字体,**不含任何 CJK 字形**
- 所有 `Label` / `Button` 的中文文本渲染时找不到字形 → 落回 .notdef 字形 = 豆腐块

### ✅ 标准解法 (3 步)

#### 第 1 步:下载中文字体

推荐 **Noto Sans SC Medium**(`NotoSansSC-Medium.otf`,~8MB OTF subset 版):

- 来源: https://github.com/notofonts/noto-cjk → `Sans/SubsetOTF/SC/`
- 为什么用 Medium 不用 Regular: Regular 在手机上笔画偏细发飘,Medium 的视觉重量与 iOS / Material 系应用一致
- 为什么用 OTF 不用 TTF: notofonts 官方 subset 版只发 OTF,SDL2/freetype 在 Android 上能透明解析
- **注意**: Noto Sans SC 是 Apache 2.0 开源协议,可商用 + 可分发

放到项目目录:
```
project/
└── fonts/
    └── NotoSansSC-Medium.otf
```

#### 第 2 步:`main.py` 注册字体覆盖默认 Roboto

在所有 `from kivy.uix.* import ...` 之后,创建任何 `App` / `Label` 之前,**最早期注册**:

```python
import os
from kivy.core.text import LabelBase

_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fonts",
    "NotoSansSC-Medium.otf",
)
try:
    if os.path.exists(_FONT_PATH):
        # 关键:用 name="Roboto" 覆盖 Kivy 默认字体名,所有控件自动生效
        LabelBase.register(name="Roboto", fn_regular=_FONT_PATH)
except Exception:
    pass
```

> **关键技巧**: `name="Roboto"` 不是笔误——Kivy 内部所有 `Label` / `Button` 默认 `font_name="Roboto"`,我们用同名注册就能**全局覆盖**,不用给每个控件单独加 `font_name=` 参数。

如果想更显式(不覆盖默认),可以用其他名字注册,然后控件里加 `font_name="zh"`,但要逐个改:
```python
LabelBase.register(name="zh", fn_regular=_FONT_PATH)
# 然后:
Label(text="中文", font_name="zh")
Button(text="确定", font_name="zh")  # 漏一个就乱码
```

#### 第 3 步:`buildozer.spec` 把字体打进 APK

```ini
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
# ↑ 已包含 otf,光这行还不够,子目录里的文件需要 include_patterns 显式列出

source.include_patterns = sand_loop.wav,fonts/*.otf
# ↑ fonts/*.otf 必须加,否则 fonts 子目录不会被打包,APK 里找不到字体文件 → 仍然乱码
```

### ❌ 常见失败原因

| 现象 | 原因 |
|---|---|
| 桌面跑没问题,Android 上还是乱码 | 字体文件没进 APK → spec 没加 `fonts/*.otf` 到 `source.include_patterns` |
| 仅部分 Label 显示中文,部分乱码 | 用了非默认字体名(如 `name="zh"`),漏了给某些控件设 `font_name=` |
| `LabelBase.register` 报错 `IOError` | 字体路径错,要用 `__file__` 推算的绝对路径,不要写相对 `"fonts/..."` |
| APK 体积突然 +10MB | 正常,Noto Sans SC Medium 本身就 8MB 左右,这是中文字体的物理体积下限 |

### 备选字体

如果 8MB 嫌太大可以换:

| 字体 | 大小 | 协议 | 备注 |
|---|---|---|---|
| Noto Sans SC Medium | ~8MB | OFL/Apache 2.0 | 推荐,平衡视觉效果和大小 |
| Noto Sans SC Regular | ~7MB | OFL/Apache 2.0 | 笔画细,手机上不如 Medium |
| 文泉驿微米黑 | ~5MB | GPL-3 + 字体例外 | 可商用,但部分罕见字缺失 |
| 思源黑体 (Source Han Sans) | ~10MB+ | OFL | Adobe 出品,与 Noto Sans CJK 同源 |
| 微软雅黑 / 黑体 (`msyh.ttc`) | ~15MB+ | **商业字体,不可分发** | 不要用 |

⚠️ **不要用 Windows 自带的微软雅黑、黑体、宋体**——这些是商业字体,把它们打包进 APK 上传 GitHub 公开仓库属于侵权。Noto Sans SC 是 Apache 2.0,可放心商用 + 公开分发。

---

## 🔊 音效无缝循环 (Android)

### 现象

短 wav (1-3 秒) 想做循环播放(背景白噪声、滴答声、沙沙声等),桌面 (Windows winsound `SND_LOOP` 或 Linux SDL2) 完全无缝,**但装到 Android 后每次循环边界有可听见的"咔"或几十毫秒静音断续**。

### 根因

Kivy `SoundLoader` 在 Android 上的 backend 是 `audio_android.SoundAudioPlayer`,底层调用 Java `MediaPlayer.setLooping(True)`。MediaPlayer 是 Android 给**长音频流**(歌曲、视频音轨)用的 API,循环时:

```
当前播放完 → 内部 buffer drain → seek to 0 → re-fill buffer → resume play
            └────── 这段总耗时 50-100ms,期间静音 ──────┘
```

短音频(< 5s)循环时 gap 占比高,人耳明显察觉。**这是 MediaPlayer API 的固有行为,不是 Kivy 的 bug**,设 `loop=True` 也救不回来。

### ✅ 标准解法: `SoundPool` API

Android 专门为短音效循环设计了 `SoundPool` API:
- 加载时**整个音频解压到内存**(不是流式 buffer)
- 循环点由硬件 audio mixer 直接处理,**真正无 gap**
- 同时支持多路并发(适合游戏音效)

但 SoundPool 没有 Java 之外的 Python 绑定,需要用 **pyjnius** 直接调 Android Java API。

#### 第 1 步: `buildozer.spec` 加 `pyjnius`

```ini
requirements = python3,kivy==2.3.0,pyjnius
```

> pyjnius 实际上是 Kivy/p4a 在 Android 上的隐式依赖(已经存在于 APK 中),显式加一行让 buildozer 跳过自动检测,加速构建。

#### 第 2 步: `main.py` 实现统一接口

```python
from kivy.utils import platform

class _SoundProxy:
    """Android 用 SoundPool 无 gap 循环,桌面 fallback 到 Kivy SoundLoader"""

    def __init__(self, wav_path):
        self._is_android = (platform == "android")
        self._sp = None
        self._sound_id = None
        self._stream_id = 0
        self._loaded = False
        self._listener = None  # 防 PythonJavaClass 被 GC
        self._kivy_sound = None

        if self._is_android:
            try:
                self._init_soundpool(wav_path)
                return
            except Exception:
                self._sp = None  # 任何失败 → fallback

        try:
            from kivy.core.audio import SoundLoader
            self._kivy_sound = SoundLoader.load(wav_path)
            if self._kivy_sound is not None:
                self._kivy_sound.loop = True
        except Exception:
            self._kivy_sound = None

    def _init_soundpool(self, wav_path):
        from jnius import autoclass, PythonJavaClass, java_method
        SoundPool = autoclass('android.media.SoundPool')
        AudioAttributes = autoclass('android.media.AudioAttributes')

        attrs = (AudioAttributes.Builder()
                 .setUsage(AudioAttributes.USAGE_MEDIA)
                 .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                 .build())
        self._sp = (SoundPool.Builder()
                    .setMaxStreams(1)
                    .setAudioAttributes(attrs)
                    .build())
        self._sound_id = self._sp.load(wav_path, 1)

        outer = self
        class _Listener(PythonJavaClass):
            __javainterfaces__ = ['android/media/SoundPool$OnLoadCompleteListener']
            __javacontext__ = 'app'

            @java_method('(Landroid/media/SoundPool;II)V')
            def onLoadComplete(self, soundpool, sample_id, status):
                if status == 0:
                    outer._loaded = True

        self._listener = _Listener()
        self._sp.setOnLoadCompleteListener(self._listener)

    def play(self):
        if self._sp is not None:
            if self._stream_id:  # 已经在播
                return
            if self._loaded:
                # play(soundID, leftVol, rightVol, priority, loop=-1 永久, rate)
                self._stream_id = self._sp.play(self._sound_id, 1.0, 1.0, 1, -1, 1.0)
            return
        if self._kivy_sound is not None:
            try:
                if self._kivy_sound.state != "play":
                    self._kivy_sound.play()
            except Exception:
                pass

    def stop(self):
        if self._sp is not None:
            if self._stream_id:
                try: self._sp.stop(self._stream_id)
                except Exception: pass
                self._stream_id = 0
            return
        if self._kivy_sound is not None:
            try: self._kivy_sound.stop()
            except Exception: pass
```

### 关键细节

| 细节 | 为什么 |
|---|---|
| `self._listener` 存到实例属性 | `PythonJavaClass` 被 Java 端持有但 Python 端只在 `_init_soundpool` 局部 → Python GC 回收后 Java 端 callback 会崩溃。**必须存到实例属性**防 GC |
| `play()` 第 5 参数 `-1` | SoundPool 的 loop 参数: `0`=播一次, `>0`=重复 N 次, **`-1`=永久循环** |
| `SoundPool.load()` 是异步的 | 必须等 `OnLoadCompleteListener.onLoadComplete` 回调 status=0 才能 play(),否则 play 会静默失败 |
| 文件大小限制 | SoundPool 默认对单个样本有内存上限(经验值 1MB 解压后),长于 30 秒的音频用错了 API,改用 `MediaPlayer` 流式 |
| 桌面 fallback | `from jnius import ...` 在 Windows 会 ImportError,顶层 try/except 兜底自动走 `SoundLoader` |

### 备选方案 (没 SoundPool 那么完美但更简单)

如果不想引入 pyjnius:

1. **延长 wav 文件**: 把 2s 的 sand_loop.wav 拉长到 10-30s,循环 gap 出现频次降到每 30 秒一次,听感上能接受
2. **双 SoundLoader 实例对换**: 用 `Clock.schedule_once` 在快播完前 0.1s 启动第二个实例,可以掩盖 gap,但同步精度难控
3. **OGG 替代 WAV**: 没用,gap 来自 MediaPlayer API 本身,不是解码格式问题

---

### ⚠️ SoundPool 之后还有第二层坑:wav 自身边界 click

**只换到 SoundPool 还不够。** 本项目 2026-05-21 装机实测,SoundPool 把 50-100ms 的 gap 消掉了,但用户仍在 ~10s 间隔听到一次微小"咔"——比 MediaPlayer 时代隐蔽得多但确实存在。

**根因**: 老 wav 是 2s,生成代码 `y[i] = x[i] - 0.7*y[i-1]` 只保证**滤波器状态在 i=0 用 y[-1]** 是连续的(防止 DC 跳变),**但白噪声序列本身在边界是离散跳变**——`whites[n-1]` 跟 `whites[0]` 完全无关。量化到 int16 时这个跳变会产生一个频谱毛刺,人耳作为"咔"感知。SoundPool 自身的循环是无缝的,但你给它的 wav 边界本身就有问题。

**修复 (2 步并用,本项目验证可行)**:

1. **wav 改长**: 2s → 15s。即使有边界 click,频次从每 2s 降到每 15s,大幅降低听觉干扰频率。SoundPool 单样本上限 ~1MB,22050Hz mono 16-bit × 15s = 660KB,在限内。

2. **overlap-add crossfade 真消除 click**:

   ```python
   # tools/generate_sand_loop.py 关键逻辑
   n_extended = n + crossfade           # 多生成 1s 的样本
   whites = [random... for _ in range(n_extended)]
   filtered = [...]                      # 高通滤波,filter[0] 用 whites[-1] 兜底
   
   samples = list(filtered[:n])
   for i in range(crossfade):
       t = i / crossfade  # 0 → 1
       # 把 [n..n+crossfade-1] 渐变叠到 [0..crossfade-1]
       samples[i] = filtered[n + i] * (1 - t) + filtered[i] * t
   ```

   播放时序: `..., sample[n-1], sample[0], sample[1], ...`
   - `sample[n-1] = filtered[n-1]`(crossfade 区域之外,不变)
   - `sample[0] = filtered[n] * 1 + filtered[0] * 0 = filtered[n]`
   
   关键洞察: `filtered[n-1]` → `filtered[n]` 是**同一个噪声生成 pass 内的连续样本**,自然无 click。crossfade 区域 `[0..crossfade-1]` 平滑过渡到非 crossfade 区域 `[crossfade..n-1]` 也是连续的。

**生成器**: `tools/generate_sand_loop.py`,运行一次覆盖 `sand_loop.wav`。

**写完测的检查清单**:
- 桌面 `python main.py` 跑 30s+,听不到 click(桌面侧 SoundLoader 仍走 MediaPlayer 但 wav 够长 click 极稀)
- APK 装机听 1 分钟,边界处不再"咔"
- 切周期 → reset → 重启沙漏,确认 SoundPool 没有副作用积累

---

## 进阶

- **Release 签名**: debug APK 用默认 debug key,Google Play 上架要 release key。spec 加 `android.release_artifact = aab`,workflow 加 keystore secret。
- **自动发布**: tag push 触发 `actions/create-release` + 上传 APK 作为 release asset。
- **多架构并行**: `strategy.matrix.arch: [arm64-v8a, armeabi-v7a]` 拆两个 job 并行。

---

## 本项目验证案例

- 桌面版: `..\hourglass.py` (tkinter,~700 行)
- Android 版: 本目录 (Kivy 精美化版,~720 行)
- GitHub: https://github.com/twoplate2/shalou
- **关键调试历史 (2026-05-20 → 2026-05-21)**:
  - 前 4 次 push 全部失败,反复尝试用 `PYTHON3_VERSION` / `P4A_PYTHON_VERSION` / `PYTHON_VERSION` 环境变量和 `p4a.python_version` spec key 强制 Python 3.12 — 全部无效(p4a 都不读这些)
  - 第 5 次首次试 `p4a.branch = 2024.1.21`(tag 名格式错),报 `Remote branch not found`
  - 第 6 次改成正确格式 `v2024.01.21`,**首次构建成功**
  - APK 装机后陆续暴露 5 个新坑,逐个修(都是装机才能复现的,云构建本身是绿勾):
    - 中文乱码 → 打包 Noto Sans SC + `LabelBase.register(name="Roboto", ...)` 全局覆盖
    - 短 wav 循环 2-3s 一次"咔" → `pyjnius` 调 `SoundPool` 替换 MediaPlayer
    - TextInput 数字未居中 → `CenterTextInput` 子类用 CoreLabel 测宽度算 padding
    - 漏完后下沙堆比上沙仓还高 → `MOUND_MAX_FILL` 上下对称
    - 应用名/按钮文案打磨 → 改"跳跳的沙漏"
  - 进一步精美化 (commit `8021f1b`): Mesh 沙体 + 漏斗坑 + 装饰颗粒 + 沙堆塌陷动画 + 颈部物理一致性 (uniform 填满缝隙、shrink 延后 6px、入颈起点、涌出沙柱) + 渐变粒子 + 触底反弹/闪光 + 完成尘埃
  - 同 commit 还修了 2 个用户实测反馈:
    - 上下满到顶/底显假 → `SAND_FILL_RATIO = 0.80`,各留 20% 余量
    - SoundPool 后仍 ~10s 一次微小 click → wav 改 15s + overlap-add crossfade,边界变成噪声序列的自然延续

---

## 参考

- Buildozer: https://buildozer.readthedocs.io
- python-for-android: https://python-for-android.readthedocs.io
- Kivy 2.3: https://kivy.org/doc/stable/
- p4a tag 列表: `git ls-remote --tags https://github.com/kivy/python-for-android.git`
- 模板灵感来源: `E:\AI_Tools\Clac\android\` (twoplate2/clac_ChinaStyle, 2024 年成功案例)
