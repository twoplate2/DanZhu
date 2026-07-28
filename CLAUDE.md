# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

跳跳的弹珠机 Android 版 (Kivy 2.3, 竖屏)。PC 版 `plinko.py`(tkinter) 的移植。
push 到 `main` → GitHub Actions 云构建 → Artifacts 下载 APK。
**打包/构建配置要动之前, 先读 `BUILD_APK.md`**(版本锁定组合 + 全套踩坑记录)。

## 命令

```
python main.py              # 桌面预览(540×960; 宽屏最大化内容列居中)
python main.py --selftest   # 无界面门禁自测(改完必跑; RTP 是统计检验, 偶发 3σ 假失败, 重跑一次)
python main.py --smoke      # 自动冒烟: 蓄力发射/必中盘特效/哑火/余额不足飘字 + 截图到 %TEMP%/plinko_smoke
python main.py --nosound    # 静音启动
python -m py_compile main.py
```

## main.py 是生成物 — 本仓库最重要的结构事实

`main.py` 由**父项目(不在本仓库)** 的 `tools/build_android_main.py` 生成:

- 常量/几何/物理/音效合成/selftest **原样抽取**自父项目 `plinko.py`(tkinter 版, 逻辑单一事实源)
- Kivy 手写段来自父项目 `tools/android_part_{head,backends,ui}.py`

本仓库(twoplate2/DanZhu)只含生成结果, **不含生成器和源文件**。因此:

- **在父项目环境里**(本地 `E:\AI_Tools\other\DanZhu`): 改源文件 → 重跑
  `python tools/build_android_main.py` → `python android/main.py --selftest` 验证 → 回本仓库 commit。**不要手改 main.py**。
- **只有本仓库时**(如 GitHub 上 clone): 直接改 main.py 可行, 但父项目下次重新生成会覆盖。
  物理/音效/盘面逻辑的重大改动**必须**回父项目做, 否则两版漂移。

## main.py 内部结构(自底向上)

1. **常量+几何**: 520×660 逻辑坐标系(y 向下), 全部绘制由它换算
2. **纯物理层**: `physics_step`(纯函数) + `steer_ball`(引导只改 vx 不改位置);
   `choose_target` 发射前预定落点 → RTP 精确(90/100/110 三档), 物理只是表演;
   哑火(power<0.15)走 `advance_misfire` 一维积分, 不扣珠不换盘面
3. **音效合成**: `bake_bank()` 程序化合成 34 个 16bit PCM(后台 daemon 线程)
4. **输出后端三级降级**: `_SoundPoolOut`(Android, pyjnius) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音。
   两种接口模式: `"pcm"`(winmm 播缩放后的 PCM) / `"named"`(SoundPool 按名播, gain 即音量)。
   `Sfx` 总线: gain 量化 10 档缓存、按名节流、`impact(bit, sp)` 按撞击速率选音色变体+音量
5. **Kivy UI**:
   - `GameArea(FloatLayout)`: 逻辑坐标→物理像素等比缩放居中。静态元素(墙/钉/槽/弧)仅尺寸变或换盘面时重绘; 球/力度条/柱塞每帧只改 pos; 特效(落袋浮字/中奖大字/余额不足 toast)是 FloatLayout 子 Label, 每帧 `tick_draw` 驱动
   - `RootWidget(BoxLayout)`: 5 行上下结构(顶栏/返还/投入/游戏区/信息/底行),
     状态机 ready→charging→flying/misfire→landing→landed, 每帧 `_frame(FIXED_DT)`
   - `PlinkoApp`: AnchorLayout 居中 + **每帧轮询窗口尺寸**调 `_fit_width`
     (`Window.bind(size)` 对启动期程序化 resize 不触发, 这是实测坑)

## 移植期踩过的坑(详解在 BUILD_APK.md 第三节)

- `import kivy` 前必须 `os.environ.setdefault("KIVY_NO_ARGS", "1")`, 否则 `--selftest` 被 Kivy argparse 抢走
- 图形指令只收关键字参数: `Rectangle(pos=..., size=...)`, 传位置参数直接 TypeError
- `Window.screenshot` 异步(下一次 on_flip 才抓帧), 同一 Clock 回调里"改状态+截图"必抓旧帧
- BoxLayout 两个 flex 子控件 50/50 平分宽度(顶栏状态文字折行的元凶)
- Kivy Label **不裁剪**超宽文本(会画到邻居地盘); 顶栏严格居中 = 左右两个等 flex 容器夹固定宽标题
- 字体层级: UI 文字用 sp; 场景内文字(槽位倍率)用逻辑 px 跟盘面缩放; 中奖 Hero 字按屏宽占比(48sp)不跟场景缩
- 震动必须 spec 里声明 `android.permissions = VIBRATE`, 否则 pyjnius 静默失败不抛异常
- 中文字体 `fonts/NotoSansSC-Medium.otf` 必须列进 `source.include_patterns`, 否则汉字全豆腐块

## 验证标准(selftest 门禁, 与 PC 版同一套)

RTP≈档位 ±0.05、引导命中>90%、卡死=0、撞钉音触发>90%、撞天花板弧<10%、
哑火 500 发零泄漏、蓄力区分度(冲顶 x 跨度)≥50px、首钉时刻恒在 80~95 帧、音效库体检 0 异常。
