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
python bench_gui.py         # 性能测试 GUI(多线程/多进程, 结果→output/)
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
   `choose_target` 发射前预定落点 → RTP 精确(80/100/120 三档), 物理只是表演;
   哑火(power<0.15)走 `advance_misfire` 一维积分, 不扣珠不换盘面。
   **near-miss 诱饵**: 球在钉阵中段朝高倍邻槽偏移 40px(TEASE_FRAC=0.80),
   仅 vy>150 时激活(慢了不推, 防卡死)。`tease_dx()` 返回偏移量。
   **preflight_check**: 发射前 600 帧快模拟, 卡死则重掷盘面(覆盖 P99.95, ~6ms)。
   **stall-retry**: 飞行中球位置不动超 1.2s → `relaunch_stalled` 退回柱塞重飞(最多 2 次)。
   **卡死兜底看位移不看表**: flying 帧里球"位置不动(位移≤1px/帧)超 MAX_FALL_SEC(4s)" 才强制 settle。
3. **音效合成**: `bake_bank()` 程序化合成 36 个 16bit PCM(~11.7s 素材)。
   `PlinkoApp.build()` 中 `Sfx(sync=True)` **同步烘焙**, 全部音效就绪后才建 UI。
   Cold-bake 后等 0.5s 让 SoundPool 异步解码完(否则首次安装必静音)。
   热启动命中磁盘 WAV 缓存(~20ms)。**flight 音效跳过 bake/prime 但保留在 iter_bank**(顺序即音色)。
   **另有 50 个 edge-tts 预录语音**(`voice/*.wav`, 22050Hz 16bit mono, 43 原有 + 7 RTP/bet 切换):
   named 后端直接 prime APK 内原路径(不落缓存、不进 stamp 指纹、语音更新即生效),
   pcm 后端剥 WAV 头并入 bank — 与合成音效完全同构。
4. **输出后端三级降级**: `_SoundPoolOut`(Android, pyjnius) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音。
   两种接口模式: `"pcm"`(winmm 播缩放后的 PCM) / `"named"`(SoundPool 按名播, gain 即音量)。
   `Sfx` 总线: gain 量化 10 档缓存、按名节流、`impact(bit, sp)` 按撞击速率选音色变体+音量。
   **全局语音互斥**: UI 交互语音(voice_rtp_/voice_bet_/voice_mode_)播完后 3.0s 内新语音不出声;
   结果/轮次语音序列不受影响。**click 节流 0.08s** 防连点重叠。
5. **Kivy UI**:
   - `GameArea(FloatLayout)`: 逻辑坐标→物理像素等比缩放居中。弹簧位于地板下方暗色凹槽,
     Z 字形(上横→斜线→下横), 线宽 1.5px, 跨度 32px, 颜色灰蓝→金黄渐变。
     **释放后阻尼振荡回弹**(k=120/damp=3.2, 过冲 clamp=-0.25, 视觉倍率 45), 3~4 个可见周期。
     **中奖大字 life=3.0s**(缩放弹入 1.5s+静止 1.5s), `_redraw` 仅在尺寸真变时清特效。
     **大字 font_size 仅值变时写**, 144 次/发→约 10 次。
   - `RootWidget(BoxLayout)`: 6 行上下结构(顶栏/返还/投入/游戏区/信息/底行)。
     状态机 ready→charging→flying/misfire→landing→landed, 每帧 `_frame(FIXED_DT)`。
   - **发射音量按蓄力分级**: 哑火 0.35→0.50, 成功 0.60→0.80(frozen_power 在清零前保存)。
   - **发射震动**: 哑火 8ms / 正常 14ms(Android VIBRATOR_SERVICE, 振幅 255)。
   - **落地弹跳**: LAND_E=0.42(弹跳 3~4 次), LAND_BOUNCE_MIN_VY=220(首帧补初速),
     弹跳系数±8%随机, bounce SFX 阈值 60px/s。**落地不瞬移到槽中心** — 球停在哪就停在哪。
   - **飞行中灰化**: `_set_controls_enabled(False)` 时 round_btn/mute_btn 灰化 +
     rtp_title_lbl/bet_title_lbl/stats_lbl 文字变暗。balance_lbl(弹珠数字)/_bead_lbl("弹珠：")/status_lbl 保持亮色。
   - **满蓄力持续提示**: power≥1.0 后棘轮停止, 每 CHARGE_HOLD_SEC(0.60s) 轻响 charge_full(0.40 gain)。
   - **防沉迷持久化**: balance/round_plays/plays/hits 存入 config JSON, 杀进程不绕过。
     启动时 round_plays≥max_plays → `_auto_reset_on_start` 延迟到 UI 就绪后静默重置。
     `reset_balance` 末尾调 `_save_config()` + 先 `remove_widget` 再过滤 toast(防控件泄漏)。
   - **返回键拦截**: `_on_key_down` 拦截 key 27(Android 返回/ESC), 防止误触退出。
   - **声音状态一致**: `_load_config` 恢复 sound_mode="off" 后调 `sfx.set_enabled(False)`。
   - **轮次结束语音兜底**: `_play_voice_sequence` 返回 total, 兜底定时器用 total+3.0s。
   - **性能测试(隐藏功能)**: 长按标题标签 → `_check_title_hold` 检测 3s → 按钮全灰 → 后台 `benchmark_trajectories(10.0)` → Popup 弹窗显示结果(设备型号/累计帧数/次秒/205 帧基准)。
   - `PlinkoApp`: AnchorLayout 居中 + **每帧轮询窗口尺寸**调 `_fit_width`。
     **`_font_scale = min(1.0, width/360dp)` + `_ui_scale = min(1.0, height/680dp)`** 双因子缩放。
     余额不足 toast "弹珠数量不足\n请重置或降低投入"(与 voice_nomoney 文案对齐)。
   - **颜色五档**: x2绿(#39d98a) x3蓝(#3d8bfd) x5红(#e0533b) x10紫(#a335ee) x20金(#f0b000),
     大字/指示灯/槽位底三处统一。
   - **弹窗标题 19sp** ≥ 正文 18sp。reset_btn on_press 变色 + on_release 恢复(滑出兜底)。

## 语音播报与三态声音开关

顶栏声音钮三态循环(`RootWidget.sound_mode` + `_refresh_mute_btn`):
**语音已开**(绿底深字, 默认) → **音效已开**(蓝底白字) → **音效已关**(深底灰字)。
`sfx.enabled = (mode != "off")`; 切换时播对应提示音, "关闭声音"延迟 1s 才 set_enabled(False)。

语音档的播报规则:
- 中奖: 播 `voice_win{payout}`(语音档下 win 琶音以自适应闪避混入 voice_win*.wav, 琶音 0.08~0.42 档位递减增益, 语音开口 60ms 内闪避)
- 余额不足: 播 `voice_nomoney`("弹珠数量不足请重置或降低投入"), throttle=3.0
- 轮次结束: 模板 + 数字朗读片段队列拼接(5ms 间隔) + suffix, 兜底 total+3.0s
- 轮次设定/手动重置: 对应 voice_round_set/voice_reset_progress
- RTP/bet 切换: voice_rtp_{80,100,120} / voice_bet_{1,10,50,100}(共 7 新语音, silent=True 启动时抑制)
- 语音头部烘 130ms 前置静音(= SFX_RESULT_LEAD)
- voice_lose("好遗憾")已制作**有意不接入**(合成 lose 音更中性, 避免重复失败的负面累积)

语音再生成: 改父项目 `tools/generate_voice.py` 的 PHRASES 后 `python tools/generate_voice.py`(缺啥补啥)或 `--force`(全量);
edge-tts 需联网, XiaoxiaoNeural +10% 语速, 重采样 22050Hz, peak 0.65 归一。

## 验证标准(selftest 门禁, 与 PC 版同一套)

RTP≈档位 ±0.05、引导命中>90%、卡死=0、撞钉音触发>90%、撞天花板弧<10%、
哑火 500 发零泄漏、蓄力区分度(冲顶 x 跨度)≥50px、首钉时刻恒在 80~95 帧、
**near-miss 诱饵不越格**(槽口偏移<24.8px)+卡死=0、音效库体检 0 异常。

## 性能测试

- `bench_gui.py`: tkinter GUI, 可输入并发数/时长, 多线程(受 GIL)/多进程(真多核)两种模式,
  输出 `output/bench_*.md` + `bench_*.json`(含 P50/P90/P99/P99.9/P99.99 帧数分布)
- `benchmark_result.md`: 28584 次飞行实测, 均值 205 帧, P50=203, P95=240, P99=303, P99.9≈488, max=3912
- 隐藏触发: 游戏中长按标题 3s→10s 性能测试→弹窗显示结果
