# 交接:跳跳的弹珠机 Android 版 — 仓库速览

> **这是 Claude 的理解**,写于 2026-08-21 通读本目录全部文本文件之后。
> **不保证 100% 准确**。所有关键结论都给了 `main.py` 行号,拿不准时请回源码核对行号,以代码实际状态为准。
> 特别提醒:本目录 `android/CLAUDE.md` 与 `main.py` 实际代码**有几处对不上**(见文末「文档与代码不一致」),不要照抄文档。

---

## 0. 一句话

跳跳的弹珠机 Android 版,单文件 Kivy 2.3 竖屏弹珠机(plinko)。球从右侧竖井弹上天,穿钉阵落进 9 个倍率槽按倍率赔付。**PC 版 `plinko.py`(tkinter)的移植,`main.py` 是生成物。**

## 1. 最重要的一条:main.py 是生成物,勿手改

`android/main.py` 不是手写的,由父项目 `tools/build_android_main.py` 拼接生成:

- 常量/几何/物理/音效合成/自测段 —— 从父项目 `plinko.py` **原样抽取**
- Kivy 手写段 —— `tools/android_part_head.py`(入口/字体/参数)+ `android_part_backends.py`(音频后端)+ `android_part_ui.py`(UI/状态机/横屏)

**改物理/音效/selftest → 改父项目 `plinko.py` → 重跑 `python tools/build_android_main.py` → `python main.py --selftest`。**
**改 Android GUI(布局/帧循环/弹簧/横屏)→ 改父项目 `tools/android_part_ui.py`。**
只有本仓库时直接改 main.py 可行,但父项目重新生成会覆盖。

---

## 2. 文件地图

| 文件 | 是什么 |
|------|--------|
| `main.py` (4445 行) | 核心,生成物。物理 + 音效 + Kivy UI + 自测全在里面 |
| `buildozer.spec` | 打包配置:包名 org.danzhu.plinko、Kivy==2.3.0、p4a v2024.01.21、targetSdk 33、NDK 25b、VIBRATE 权限、四方向 orientation、p4a.hook |
| `p4a/hook.py` | 构建后 hook,向 manifest 注入 `screenOrientation=fullSensor` + `resizeableActivity=true`(我没直接读,作用是从 spec/README 得知) |
| `.github/workflows/build-apk.yml` | GitHub Actions 云构建。push main/master 触发,ubuntu-22.04 + Py3.10 + Java17 + cython<3.0 + buildozer 1.5.0,缓存 key 前缀 buildozer-v4,出 debug APK |
| `README.md` | 面向人的玩法/界面/横屏/性能/语音说明 |
| `BUILD_APK.md` | 云构建经验手册 + 20 条 tkinter→Kivy 移植弯路(最值钱的踩坑集) |
| `qachecklist.md` | QA 验收清单,用户诉求→可量化门禁(回弹/落袋/横向稳定性) |
| `project_steer_refactor.md` | 2026-08-03 steer 重构历史快照(部分参数已过时,见文末) |
| `d1.md` | 2026-08-19 云构建卡慢排查交接(与代码无关,排查 GitHub Actions) |
| `bench_no_preflight.py` | **旧版**物理快照的无预判基准测试(带 tkinter GUI),测速度踢能否替代 preflight |
| `sim_balance.py` | 余额模拟器,验证四档 RTP 下 500 发的破产率/余额分布(出 HTML) |
| `duibi.html` | 设计页:经典版 → 当前版 改动全对比 |
| `how_to_desigin.html` | 设计页:设计深潜 + 5 个技术附录(A音效/B物理/C数学/E进化/D架构) |
| `output/slot_dist.json` | 9 槽实测落格分布:`[0.09375, 0.11875, 0.10875, 0.145, 0.12875, 0.13, 0.10125, 0.08, 0.09375]`(slot4 最热,slot8 最冷) |
| `voice/*.wav` (51 个) | edge-tts 预录语音(不是合成,加载侧与合成音效同构) |
| `fonts/NotoSansSC-Medium.otf` | 中文字体,用 name="Roboto" 覆盖默认(不打必豆腐块) |

---

## 3. main.py 内部结构(按行号分区)

| 行号 | 内容 |
|------|------|
| 1–48 | import + `KIVY_NO_ARGS=1`(防 Kivy 抢命令行参数)+ `KIVY_ORIENTATION`(四方向)+ 中文字体注册 |
| 51–54 | `hex_rgb` |
| 56–93 | 几何常量(CW=520/CH=660、钉阵、槽位、竖井、PLUNGER 等) |
| 95–164 | 物理常量(G、弹性、摩擦、弧面、发射速度等) |
| 166–219 | **四旋钮离散表** `KNOB_POWERS` / `KNOB_ARC_ANGLE` / `KNOB_ARC_BOOST` / `KNOB_CEIL_ANGLE` / `KNOB_CEIL_BOOST` |
| 222–240 | `_sample_table`(按权重抽样)、`_power_band`(连续力度→最近档) |
| 241–271 | `LAND_*` / `MISFIRE_*` / `START_BEADS=1000` / `DEFAULT_BET=10` / `STALL_*` / `REWARD_EV=3.35` |
| 273–278 | 事件位 `EV_PEG=1 EV_CEIL=2 EV_WALL=4 EV_DIV=8 EV_ARC=16` |
| 280–301 | 配色 `COL_*` |
| 303–383 | `build_pegs` / `build_dividers` / `build_walls` / `build_deflectors` / `build_geo` |
| 389–411 | `clamp` / `_reflect` / `_mark` |
| 414–490 | `_collide_pegs`(碰钉:侧碰/冠碰/回弹限幅/摩擦/掠射/冲刺) |
| 494–542 | `_collide_rect`(墙/隔板;含天花板弹射 KNOB_CEIL 采样) |
| 545–590 | `_collide_arc`(弧面缓动带球) |
| 593–615 | `physics_step`(6 子步,落地返回槽号) |
| 618–620 | `power_u` |
| 623–644 | `Ball` 类(`__slots__`,兼容 `b.x` / `b["x"]` / `b.get()`) |
| 647–664 | `launch_ball` |
| 667–693 | `misfire_speed` / `launch_misfire` / `advance_misfire` |
| 696–702 | `advance_flight`(纯被动,`_ARC_FRAME += 1`) |
| 705–737 | `benchmark_trajectories`(性能测试,固定种子 12345) |
| 740–751 | `_reward_value`(x2 55%/x3 25%/x5 13%/x10 5.5%/x20 1.5%,EV=3.35) |
| 754–779 | `KNOB_K`(每档几格有奖)+ `roll_multipliers`(固定格数盘面) |
| 781–1266 | 音效层:SR=22050、合成基元、各配方、`iter_bank`、`bake_bank` |
| 1268–1393 | winmm `_WaveOut`(8 声道)+ `_scale_pcm` |
| 1395–1474 | named 后端 helpers(缓存目录/指纹/原子写 WAV/voice 读取) |
| 1476–1527 | `_SoundPoolOut`(Android) |
| 1530–1565 | `_KivySoundOut`(桌面兜底) |
| 1568–1589 | `open_output`(SoundPool > winmm > Kivy > 静音) |
| 1593–1827 | `Sfx` 音效总线(合成缓存+增益量化 11 档+节流+语音互斥 3.0s) |
| 1829–2193 | `selftest`(自测门禁,含 RTP/被动飞行/哑火/下落节奏/蓄力区分度/事件覆盖/音效体检) |
| 2195–2221 | `sfx_check`(音效体检:削波/直流/爆音/静音) |
| 2223–2241 | UI 常量(H_TOP=44 H_RTP=44 H_BETS=44 H_INFO=26 H_BOTTOM=64 BALL_VIEW=1.4) |
| 2244–2255 | `slot_color`(按倍率分档上色) |
| 2258–2331 | `ball_texture`(程序化小球贴图,径向渐变+猫眼色带) |
| 2334–2355 | `_vibrate`(中奖震动,仅 Android,振幅 255) |
| 2358–2415 | `number_voice_names` / `_read_4digits`(中文数字语音拼名) |
| 2418–2580 | 横屏反旋转层 `LandLayer` / `RotPopup` / `_device_is_wide` / `_land_angle` |
| 2582–2950 | `GameArea`(游戏画布,520×660 等比缩放,弹簧动画) |
| 2952–4236 | `RootWidget`(状态机 + 全部控件 + 帧循环 + 持久化) |
| 4242–4334 | `PlinkoApp`(入口,方向守卫) |
| 4337–4445 | `_smoke`(冒烟截图)+ `main()`(参数门禁) |

---

## 4. 核心机制(看懂这 6 个就懂了这个游戏)

### 4.1 彻底被动下落(最核心的架构原则)
球完全被动:发射 → 碰弧面(缓动带球转向)→ 重力穿钉阵 → 自然落袋。**无引导、无预定槽、无预演修订、零发射延迟**。`settle` 用物理落格,结算槽 == 物理落格槽恒成立(零穿帮)。历史上有过 steer 引导/choose_target 预定/预演,2026-08-04 全部删除(玩家投诉"小动物找回家的路")。

### 4.2 三段式轨迹
① 竖直上升(碰弧面前 vx=0,零干预)→ ② 弧面**缓动带球**转向(接触帧缓动 `ARC_EASE_FRAMES=3`,方向每帧 ~8.3° 缓动到出口角,出口速度=入射速度不耗能)→ ③ 抛体(纯重力)。弧面 = 右壁口部弧形导轨(R8 弧 + 35° 接触段 + R400 微弯弧),碰撞半径=视觉半径(`ARC_VISUAL=1.4`,球与弧面相切)。

### 4.3 四旋钮离散表(GA 优化定稿 2026-08-15)
力度分 10 档(`KNOB_POWERS=[0.15..1.00]`),每档 4 个旋钮各一张「离散值+概率权重」表:
- `KNOB_ARC_ANGLE` 弧面出口角偏移
- `KNOB_ARC_BOOST` 弧面出口力度乘子
- `KNOB_CEIL_ANGLE` 天花板反弹旋转角
- `KNOB_CEIL_BOOST` 天花板力度乘子

每发球按 `launch_power` 定位最近档,4 旋钮各 `_sample_table` 采一次。boost 限幅 [0.65,1.25](加速≤25%/减速≤35%,是体验红线)。目标:逐档打分(落袋均匀×0.7+首钉均匀×0.3)最大。

### 4.4 RTP 固定格数盘面
`roll_multipliers(rtp)`:先按 `KNOB_K` 掷"本局 K 个格子有奖"(80%→2/3格、120%→3/4格、200%→5/6格、300%→8/9格),再 `random.sample` 选 K 个位置填 `_reward_value`(x2~x20,EV=3.35)。数学:均值 K = 档位×9/3.35,故 E[K]×3.35/9 = 档位精确成立。**返还率与落袋是否均匀完全解耦**。四档盘面在 `RootWidget.__init__` 一次生成(`_boards`),切档只换引用不重掷。

### 4.5 音效系统
- `bake_bank()` 程序化合成 36 个 16bit PCM(~11.5s),`iter_bank()` 按**固定顺序** yield(顺序即音色,共用 `_ARNG` 随机流,新增音效必须追加末尾)
- `Sfx` 总线:合成一次(后台线程),之后只做取样+送声卡;增益量化成 11 档(0~10);pcm 后端(winmm)用缓存缩放 PCM,named 后端(SoundPool/Kivy)落盘 WAV 按名播
- 语音互斥:仅 `voice_rtp_/voice_bet_/voice_mode_` 三类 3.0s 互斥,结果/轮次语音不受限
- named 后端缓存用 stamp 文件记"文件名:字节数"指纹(防被截断的 WAV 永久静默)

### 4.6 Kivy 状态机
`RootWidget._frame` 累加器驱动,状态:`ready → charging → flying/misfire → landing → landed → (park_ball) → ready`。
- `launch()` 里才禁用控件(不是 start_charge,见文末不一致)
- 落袋分两段:飞行出 landed → `landing`(先回弹展示)→ 落定后才 `settle`(结算延迟到回弹后)
- 卡死兜底看**位移**:位置不动 >1.2s 沿接触法线踢球(最多 10 次),>4s 强制按物理槽结算

---

## 5. 关键常量速查(带行号)

| 常量 | 值 | 行号 |
|------|-----|------|
| CW / CH | 520 / 660 | 57 |
| WALL / FLOOR | 12 / 648 | 58–59 |
| FIELD_L / FIELD_R / FIELD_W | 12 / 459 / 447 | 65–67 |
| NUM_SLOTS / SLOT_W | 9 / ≈49.67 | 69–70 |
| PLUNGER_X / PLUNGER_Y | 487 / 637 | 90–91 |
| G | 1000 | 96 |
| E_SLOW / E_FAST / E_SIDE | 0.70 / 0.40 / 0.20 | 99–103 |
| PEG_BOUNCE_VY_MAX / MIN | 280 / 70 | 104,108 |
| PEG_KEEP_VY / FRICTION | 0.35 / 0.95(vx)·0.97(vy) | 116,119–122 |
| PEG_REFLECT_VX_MAX / MIN_ESCAPE | 300 / 150 | 112–113 |
| PEG_CROWN_ESCAPE | 60 | 126 |
| LAUNCH_MIN / MAX | 1077 / 1114 | 138–139 |
| ARC_OUT_ANGLE / ARC_EASE_FRAMES | 35 / 3 | 154,158 |
| ARC_VISUAL | 1.4 | 151 |
| ARC_EJECT_ANGLE | 12 | 160 |
| MISFIRE_POWER | 0.15 | 252 |
| START_BEADS | 1000 | 259 |
| PRESETS | [1,10,50,100] | 260 |
| DEFAULT_BET | 10 | 261 |
| MAX_FALL_SEC / STALL_RETRY_SEC / STALL_MAX_RETRY | 4.0 / 1.2 / 10 | 262–269 |
| LAND_E / LAND_BOUNCE_MIN_VY / MAX_VY | 0.42 / 220 / 220 | 244–246 |
| REWARD_EV | 3.35 | 271 |
| SR | 22050 | 784 |
| EV_PEG/CEIL/WALL/DIV/ARC | 1/2/4/8/16 | 274–278 |

---

## 6. 验证命令

```
python main.py              # 桌面预览(540×960)
python main.py --selftest   # 无界面门禁自测(改完必跑;偶发 3σ 假失败,重跑一次)
python main.py --smoke      # 自动冒烟 + 截图到 %TEMP%/plinko_smoke
python main.py --nosound    # 静音
python main.py --landscape  # 桌面模拟横屏反旋转(1740×1000)
python -m py_compile main.py
```

自测门禁覆盖:RTP≈档位±0.05、卡死=0、撞钉音>90%、哑火零泄漏、弧面接触率≈100%、首钉 75~110 帧、转向平滑、回弹感(≥10px 占比≥25%)、下落节奏(减速比 0.45~0.80)、横向稳定性(vx<80 突增>280 次数=0)、音效体检 0 异常。

---

## 7. 文档与代码不一致(已核实,以代码为准)

**这几处 android/CLAUDE.md 的「已知陷阱」与 main.py 实际代码对不上:**

1. **`start_charge` 未立即禁用控件。** 文档说「start_charge 必须立即调 `_set_controls_enabled(False)`」,但 `start_charge`(3581–3600)没有调。控件禁用实际发生在 `launch()`(3616 哑火 / 3640 正常)。充电窗口(约 0.5~1.0s)内 bet/rtp/reset 等按钮理论仍可点。`_set_controls_enabled` 全部调用点:3332(跑分)/3616/3640(发射)/3849(轮次结束)/3565·3691(恢复 True)。

2. **哑火累加器在循环外。** 文档说「哑火累加器必须每物理步+1」,但 `_frame` 的 misfire 分支(4159–4173)里 `self._misfire_frames += 1` 在 `while _accumulator >= FIXED_DT` 循环**外**(每渲染帧+1),`advance_misfire` 在 `stepped` 时也只调一次(即使一帧跨多个物理步)。

3. **stall 位移检测在循环外。** 文档说「stall 位移检测必须在累加器循环内每步做」,但代码(4125–4146)在循环外每渲染帧看一次净位移。代码注释(4145–4146)明确为此辩护:「卡死球的速度和微碰撞从未停过,但位置被碰撞钉死——位置不说谎」。

**其他值得注意的差异:**

4. **ARC_OUT_ANGLE 是 35° 不是 25°。** android/main.py 实际是 35°(154 行)。父项目 plinko.py 的 CLAUDE.md 写 25°。duibi.html / how_to_desigin.html 附录 B 里两处措辞混用(「缓动到 25°」是 PC 版残留)。

5. **`bench_no_preflight.py` 是旧版物理快照。** 它 docstring 声称「精确复制自 main.py」,但实际是旧版:G=1200(现 1000)、LAUNCH 1180/1220(现 1077/1114)、E_SLOW/E_FAST 0.60/0.18(现 0.70/0.40)、**含 steer_ball 引导层 + SLOT_BRAKE**(现都已删)、无 EV_ARC。它测的是旧版带引导物理,不能外推到当前无引导 main.py。

6. **`sim_balance.py` 的盘面模型是简化版。** 它用「每格独立 q=rtp/3.35」的 Bernoulli 模型,而 main.py 实际用 KNOB_K 固定格数 + random.sample。docstring 说「与 plinko.py 完全一致」不准确。

7. **三处盘面生成逻辑互不一致:** main.py(KNOB_K 固定格数)、sim_balance.py(每格独立 q)、bench_no_preflight.py(randint 2~7 格)。别把它们当成同一套。

8. **`project_steer_refactor.md` 是 2026-08-03 历史快照**,其中 E_SLOW/E_FAST 0.55/0.25、PEG_BOUNCE_VY_MIN 70 等参数已被后续 qachecklist 演进到 0.70/0.40 等,读它别当当前值。

---

## 8. 快速上手:想改 X 去哪改

| 想改 | 去哪 |
|------|------|
| 物理参数(重力/弹性/摩擦) | 父项目 `plinko.py` 常量区 → 重跑生成器 |
| 碰钉手感/回弹 | 父项目 `plinko.py` 的 `_collide_pegs` → 重跑生成器 |
| RTP 档位/盘面格数 | 父项目 `plinko.py` 的 `KNOB_K` + `_reward_value` → 重跑生成器 |
| 音效配方 | 父项目 `plinko.py` 的音效区 → 重跑生成器 |
| 自测门禁阈值 | 父项目 `plinko.py` 的 `selftest` → 重跑生成器 |
| Kivy 界面/布局/帧循环/横屏 | 父项目 `tools/android_part_ui.py` → 重跑生成器 |
| 音频后端(SoundPool) | 父项目 `tools/android_part_backends.py` → 重跑生成器 |
| 语音播报文案 | 父项目 `tools/generate_voice.py` → 重跑 → 提交 voice/*.wav |
| 打包配置/权限/SDK 版本 | `buildozer.spec` + `.github/workflows/build-apk.yml`(先读 BUILD_APK.md) |
