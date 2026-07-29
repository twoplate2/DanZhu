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

语音/兜底的行为级验证脚本在父项目(不在本仓库): `scratch/archive/three_state_shots.py`(三态按钮截图)、
`scratch/archive/stall_fallback_check.py`(晃动不提前结算/钉死 4s 兜底)、`tools/generate_voice.py`(语音再生成)。

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
   `choose_target` 发射前预定落点 → RTP 精确(80/100/110 三档), 物理只是表演;
   哑火(power<0.15)走 `advance_misfire` 一维积分, 不扣珠不换盘面。
   **卡死兜底看位移不看表**: flying 帧里球"位置不动(位移≤1px/帧)超 MAX_FALL_SEC(4s)"
   才强制 settle —— 旧版"发射后 8s"会在球晃动久未落袋时提前结算(音效/飘字/震动全早于
   真实落袋)。判据不能用速度或碰撞事件: `steer_ball` 每帧注入 vx, 卡死球的速度数值和
   微碰撞从未停, 只有位置被碰撞钉死不说谎。
3. **音效合成**: `bake_bank()` 程序化合成 36 个 16bit PCM(~11.7s 素材)。
   `PlinkoApp.build()` 中 `Sfx(sync=True)` **同步烘焙**, 全部音效就绪后才建 UI,
   冷启动不空窗。Cold-bake 后等 0.5s 让 SoundPool 异步解码完(否则首次安装必静音)。
   热启动命中磁盘 WAV 缓存(~20ms)。SFX_MASTER=1.0, `_pack()` 峰值 +30%。
   **另有 20 个 edge-tts 预录语音**(`voice/*.wav`, 22050Hz 16bit mono 与合成音效同格式,
   由父项目 `tools/generate_voice.py` 生成): named 后端直接 prime APK 内原路径
   (不落缓存、不进 stamp 指纹、语音更新即生效), pcm 后端剥 WAV 头并入 bank ——
   两种播放路径与合成音效完全同构(gain/节流/并发都走 Sfx 总线同一套)。
4. **输出后端三级降级**: `_SoundPoolOut`(Android, pyjnius) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音。
   两种接口模式: `"pcm"`(winmm 播缩放后的 PCM) / `"named"`(SoundPool 按名播, gain 即音量)。
   `Sfx` 总线: gain 量化 10 档缓存、按名节流、`impact(bit, sp)` 按撞击速率选音色变体+音量。
   **不再用 OnLoadCompleteListener** — 那个跨线程 JNI 代理失灵就是全库永久静音,
   而 `play()` 对未加载完的 sample 本来就返回 0。缓存用 stamp 文件记指纹+逐文件字节数校验。
5. **Kivy UI**:
   - `GameArea(FloatLayout)`: 逻辑坐标→物理像素等比缩放居中。静态元素(墙/钉/槽/弧)仅尺寸变或换盘面时重绘; 球/力度条/柱塞每帧只改 pos; 特效(落袋浮字/中奖大字/余额不足 toast)是 FloatLayout 子 Label, 每帧 `tick_draw` 驱动
   - `RootWidget(BoxLayout)`: 6 行上下结构(顶栏/返还/投入/游戏区/信息/底行),
     行间距 10dp。状态机 ready→charging→flying/misfire→landing→landed, 每帧 `_frame(FIXED_DT)`。
     发射不再播飞行音(已移除); launch 音量按哑火/成功分级(0.35→0.50 / 0.60→1.00)。
     页边距和行间距也纳入 `_ui_scale` 缩放, 横屏时不浪费垂直空间。
   - `PlinkoApp`: AnchorLayout 居中 + **每帧轮询窗口尺寸**调 `_fit_width`
     (`Window.bind(size)` 对启动期程序化 resize 不触发, 这是实测坑)。
     `build()` 中 Android 运行时调 `setRequestedOrientation(PORTRAIT)` 强制竖屏,
     配合 `_fit_width()` 横屏容错(宽高比>1.2 时以宽度为限)。
     **`_font_scale = min(1.0, width/360dp)` + `_ui_scale = min(1.0, height/680dp)`**
     双因子缩放: 窗口窄时字体缩小, 窗口矮时(横屏)所有固定 UI(行高/按钮宽/字号)等比缩小,
     把垂直空间还给游戏区。`_apply_sizes()` 统一写到所有控件, 竖屏时两因子均为 1.0 不影响。
   - 标签统一右对齐(115dp), 顶栏标题左右等 flex 居中。底部 RootWidget 12dp padding,
     H_INFO 26dp, 底行双 flex(0.95/0.05)控制蓄力按钮位置。
   - 余额不足只在屏幕中央弹 toast。中奖大字 2.4s 停留, 落地 0.3~0.7s 后可再发射。
   - **颜色五档**: x2绿(#39d98a) x3蓝(#3d8bfd) x5红(#e0533b) x10紫(#a335ee) x20金(#f0b000),
     大字/指示灯/槽位底三处统一。槽位底色用深色版(绿#1e8a5a 金#c88800)保证白字对比度。

## 语音播报与三态声音开关

顶栏声音钮三态循环(`RootWidget.sound_mode` + `_refresh_mute_btn`):
**语音已开**(绿底深字, 默认) → **音效已开**(蓝底白字) → **音效已关**(深底灰字)。
`sfx.enabled = (mode != "off")`; 切换时播对应提示音(voice_mode_voice/sfx/off),
其中"关闭声音"**在静音前播**, 延迟 1s 才 `set_enabled(False)` 让播报收尾,
窗口内玩家又切回则取消关闭(`_apply_sound_off` 校验 mode)。

语音档的播报规则(`_play_result_sound` / `start_charge`):
- 中奖: 播 `voice_win{payout}`("珠子加xx")**替换 win 琶音** —— 同播会互盖(win rms 全库最响)。
  payout = bet{1,10,50,100} × 倍率{2,3,5,10,20} 共 20 种组合, 按数值去重 = 15 个文件全覆盖
  (20/100/200/500/1000 各有两种组合)。pocket 入袋声、coin 滚分音各档照播。
- 余额不足: 播 `voice_nomoney`("珠子数量不足请重置或降低投入")替换 error 嗡声,
  全长 2.9s 故 throttle=3.0 防重叠; toast 飘字三档都弹。
- 失败: `voice_lose`("好遗憾")已制作**未接入**, lose 音照播。
- 语音头部烘 130ms 前置静音(= SFX_RESULT_LEAD), 节奏与被替换的 win 音对齐(等 pocket 落地)。

语音再生成(改文案/新增词条): 改父项目 `tools/generate_voice.py` 的 PHRASES 后
`python tools/generate_voice.py`(缺啥补啥)或 `--force`(全量); edge-tts 需联网,
XiaoxiaoNeural +10% 语速, 重采样 22050Hz, peak 0.65 归一(实测 rms 0.119~0.158 与 win 音同档)。
生成后直接提交 `voice/*.wav` 即可, 加载侧零改动。

## 移植期踩过的坑(详解在 BUILD_APK.md 第三节)

- `import kivy` 前必须 `os.environ.setdefault("KIVY_NO_ARGS", "1")`, 否则 `--selftest` 被 Kivy argparse 抢走
- 图形指令只收关键字参数: `Rectangle(pos=..., size=...)`, 传位置参数直接 TypeError
- `Window.screenshot` 异步(下一次 on_flip 才抓帧), 同一 Clock 回调里"改状态+截图"必抓旧帧
- BoxLayout 两个 flex 子控件 50/50 平分宽度(顶栏状态文字折行的元凶)
- Kivy Label **不裁剪**超宽文本(会画到邻居地盘); 顶栏严格居中 = 左右两个等 flex 容器夹固定宽标题
- 字体层级: UI 文字用 sp; 场景内文字(槽位倍率)用逻辑 px 跟盘面缩放; 中奖 Hero 字按屏宽占比(48sp)不跟场景缩
- 震动必须 spec 里声明 `android.permissions = VIBRATE`, 否则 pyjnius 静默失败不抛异常。
  振幅用 `255`(最大值)而非 `DEFAULT_AMPLITUDE`(-1 / ~50%), 否则手机震感太弱。
- 中文字体 `fonts/NotoSansSC-Medium.otf` 必须列进 `source.include_patterns`, 否则汉字全豆腐块

## 验证标准(selftest 门禁, 与 PC 版同一套)

RTP≈档位 ±0.05、引导命中>90%、卡死=0、撞钉音触发>90%、撞天花板弧<10%、
哑火 500 发零泄漏、蓄力区分度(冲顶 x 跨度)≥50px、首钉时刻恒在 80~95 帧、音效库体检 0 异常。
