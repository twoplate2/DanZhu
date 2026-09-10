# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

跳跳的弹珠机 Android 版 (Kivy 2.3, 竖屏)。PC 版 `plinko.py`(tkinter) 的移植。
push 到 `main` → GitHub Actions 云构建 → Artifacts 下载 APK。
**打包/构建配置要动之前, 先读 `BUILD_APK.md`**。

## 命令

```
python main.py              # 桌面预览(540×960)
python main.py --selftest   # 无界面门禁自测(改完必跑; 偶发 3σ 假失败, 重跑一次)
python main.py --smoke      # 自动冒烟 + 截图到 %TEMP%/plinko_smoke
python main.py --nosound    # 静音启动
python -m py_compile main.py
python ../tools/fx_probe.py # 中奖玻璃杯自检(纹理/堆形/缓存/Clock 回调签名/时长)
python ../tools/build_android_main.py --check   # 校验 main.py 与 tools/ 源同步
```

**测中奖务必先切 300% 档**: 默认 80% 档盘面抽不出 x50/x100, 拿固定盘面测等于没测到
真正会出问题的高倍率(颗数最多、锁输入最久、最容易掉帧)。

## main.py 是生成物

`main.py` 由**父项目** `tools/build_android_main.py` 生成:
- 常量/几何/物理/音效合成/selftest **原样抽取**自父项目 `plinko.py`
- 中奖杯球堆 `tools/pile3d.py`(纯 stdlib) + 表现层 `tools/android_part_pile.py`
- Kivy 手写段来自 `tools/android_part_{head,backends,ui}.py`

顺序: `[head, b1..b7, backends, pile3d, android_part_pile, ui]`(新两段插在 `ui` 之前,
因为 GameArea/RootWidget 要用到它们)。**`tools/` 与 `../wingui/` 都不在 git 里**,
仓库里只有生成物, 所以改完必须重跑生成器 —— 用 `--check` 兜底。

本仓库只含生成结果, **不含生成器和源文件**。因此:
- **在父项目环境里**: 改源文件 → `python tools/build_android_main.py` → `python android/main.py --selftest` → 回本仓库 commit。**不要手改 main.py**。
- **只有本仓库时**: 直接改 main.py 可行, 但父项目重新生成会覆盖。重大改动必须回父项目做。

## main.py 内部结构

1. **常量+几何**: 520×660 逻辑坐标系(y 向下)。发射槽在井底(PLUNGER_Y=FLOOR-BALL_R-2, 弹簧 Z 字形露出), 发射速度 1077~1114, G=1000
2. **物理层**: `Ball`(__slots__ class, 含 `_rng` 撞钉扰动流), `physics_step`, `launch_ball`, `advance_flight`(纯被动: 重力+碰撞, 零干预), `benchmark_trajectories`
   - **三段式轨迹**: 竖直上升(vx=0)→弧面缓动带球转向(35°接触段, ARC_EASE_FRAMES=3, 出口速度=入射速度不耗能)→抛体进钉阵
   - **4 旋钮离散表**: 弧面角度/力度、天花板角度/力度, 10 个力度档各一套「离散值+概率」表(`KNOB_*` 四张表), 每发按力度档采样(`_sample_table` + `_power_band`), boost 限幅 [0.65,1.25](加速≤25%/减速≤35%)
   - **力度互补 + 总体均匀**: 弱蓄首钉偏右/满蓄首钉偏左, 随机力度时首钉/落袋总体铺满 9 槽(绕开弱蓄档打不散的物理瓶颈)
   - **天花板 2 号弹射器**: 撞顶后按力度档采样角度旋转 + 力度缩放, 撞顶保底 vy≥180 防"吸住", 事件位记 EV_CEIL(已修死事件位)
   - **弧面** = 根部R8弧 + 35°接触段15px + R400微弯弧30px(切线连续无转折), 碰撞半径=视觉半径(ARC_VISUAL=1.4, 球与弧面相切)
   - **彻底被动**: 无引导/无修订/无预定槽, 球完全被动下落, 结算==物理落格恒成立(零穿帮)
   - **盘面固定格数**: `roll_multipliers` 按 `KNOB_K` 表掷"本局几个格子有奖"(80%档 2格85.1%/3格14.9%、120%档 3格/4格、200%档 5格/6格、300%档 8格/9格), 再 random.sample 选位置填 `_reward_value`(x2 55%/x3 25%/x5 13%/x10 5.5%/x20 1.5%, EV=3.35)。RTP 精确=档位, 消灭"只有1格有奖"烂盘, x10/x20 完整保留
   - **碰钉回弹**: PEG_BOUNCE_VY_MAX=280(弹高≤39px<行距55) + E_SLOW=0.70/E_FAST=0.40 + PEG_FRICTION=0.95/vy0.97 + PEG_SPRINT=0.7软化(末段保留横速, 落袋干净靠 ALIGN 收尾)
   - **弧面抖动**: 每发 ±6px 垂直平移(arc_dy), 档内首钉散布 ±10~15px
   - **卡死兜底看位移**: 球位置不动(≤1px/帧)超 MAX_FALL_SEC(4s)才强制 settle
   - **遗传算法优化器**: `scratch/optimize_knobs.py`, 三阶段 GA(每档独立→联合480维→降维arc-only120维+种子初始化), 评分=落袋总体均匀×0.7+首钉总体均匀×0.3
   - **benchmark_trajectories(duration)**: 纯 CPU 性能测试(返回 3 元组 flights/frames/fps_list)
3. **音效**: 36 合成 PCM + 57 edge-tts 语音(voice/*.wav)。`Sfx.play()`: gain 10档缓存、按名节流
   - **全局语音互斥**: voice_rtp_/voice_bet_/voice_mode_ 3.0s 间隔。click throttle 0.08s。flight 跳过 bake/prime
   - **弧面接触音**: EV_ARC 事件播 rail 0.18(轻金属"擦"声, throttle 0.05)——转向瞬间的听觉反馈
4. **后端**: `_SoundPoolOut`(Android) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音
5. **Kivy UI**: `GameArea`(FloatLayout) + `RootWidget`(BoxLayout 6行)
   - 弹簧: Z字形, k=120/damp=3.2 阻尼振荡回弹, 视觉倍率45, 过冲clamp=-0.25
   - 状态机: ready→charging→flying/misfire→landing→landed, 累加器驱动
   - 发射: frozen_power 保存力度, 音量分级(0.60→0.80), 震动(哑火8ms/正常14ms)
   - 落地: LAND_E=0.42, LAND_BOUNCE_MIN_VY=220, LAND_BOUNCE_MAX_VY=220(回弹vy上限, 删SLOT_BRAKE后防弹越隔板), ±8%随机, 不瞬移
   - 飞行中灰化: `_set_controls_enabled(False)` 时按钮+标签文字统一变暗
   - 中奖大字: life=BIG_TEXT_LIFE=1.8s(见 android_part_pile.py 顶部), font_size 仅值变时写
   - 满蓄力: 每0.60s轻响 charge_full(0.40)
   - 防沉迷: balance/round_plays/plays/hits 持久化, 启动自动处理打满状态
   - 返回键拦截(key 27), 声音状态一致, 轮次结束语音兜底 total+3.0s
   - **性能测试(隐藏)**: 长按标题3s→10s benchmark→Popup弹窗(设备/帧数/次秒)

## 语音播报

57条语音(含若干切换提示与数字片段)。两态开关: 音效已开(含语音播报,默认)→音效已关；
**声音设置不持久化**(不进配置文件, 每次启动都是已开)。
voice_lose 有意不接入(合成 lose 音更中性)。
语音互斥仅对 UI 交互生效, 结果/轮次序列不受影响。

> **RTP 语音已补齐（2026-08-16）**：`voice_rtp_80/120/200/300` 四条齐全，孤儿 `voice_rtp_100` 已删。切档四档都有语音。

## 中奖玻璃杯表现

中奖时结算后停 0.5s(让槽位白闪/绿灯先被看见)→ 整块游戏区压暗 → 玻璃杯浮在画面
**中下部**、弹珠从板面上方雨点般落下堆满杯子 → 全部落定播中奖音 → 停 0.45s 淡出 →
解锁。倍率 = 颗数, 投注档 = 球色(1绿/10蓝/50红/100紫)。

- `WinPileFX` 是 `GameArea` 的子控件(_restack_overlays 每帧重画的次序保证它是
  「板面 < 杯子 < 中奖大字」三层里的中间层)。帧推进由 `GameArea.tick_draw()` 调,
  **自己不持有任何 Clock** —— 切后台回来动画直接跳终态而不是卡住(busy 立刻转 False)。
- 运行期零物理: 球堆由 `pile3d.build_pile` 在生成期一次算完, 运行期只做斜投影 + 纯时基插值。
- **绝不软锁**: `busy()` 有 `FX_MAX_SEC=9s` 硬兜底, `play_win` 全程 try/except。
  输入锁一旦卡住玩家只能杀进程, 这是本模块唯一能出线上事故的地方。
- 时序: ×2 约 2.7s, ×100 约 4.2s(含 0.5s 起播延迟与 0.45s 尾巴)。
- **进场/退场是分层动画，不是一根透明度**（`WinPileFX._layers()` 返回 `(压暗alpha, 道具alpha, 缩放k, 位移dy)`）：
  - 进场错峰：压暗 0.30 起、杯子 0.34 起、0.50 落位正好接上雨钟；杯子从**上方 +22 逻辑px** 降入、0.97→1.0 微缩。
  - 退场：静止 `RESULT_HOLD=0.20` → 上浮 38px + 缩到 0.94 + 淡出，`RESULT_FADE=0.25`；压暗层早 0.04s 撤（"灯先亮回来、道具后撤走"）。
  - ⚠️ **`RESULT_HOLD + RESULT_FADE` 的和必须保持 0.45** —— 那就是解锁前的尾巴，`expected_sec`/`_reveal_deadline`/`fx_probe` 全按这个和算。只能调内部分配。
  - ⚠️ **位移只能朝上**：杯底在逻辑 578，槽区隔板顶在 606，只有 28px 余量；往下位移超过 28 就压住倍率槽。
  - ⚠️ **缩放必须 bw/bh 同比例**（`_map` 的 x 走 `_abw`、y 走 `_abh`、球半径走 `_abw`），只缩一个球会变椭圆或从杯口冒出去。动画只写 `_ab*/` 临时字段，**绝不写回 `_bx/_by`**。
  - ⚠️ `_layers()` **必须按 `mode` 分支**：`_settled_at` 初值是 0.0，退场曲线不看 mode 会算出天文数字 → clamp 成 1 → 整场装杯按"退场终态"渲染。selftest/fx_probe 都不碰这里，出错是静默的。
  - ⚠️ `tick()` 的 pending 分支**不能有 `if self._dirty` 掐帧**，否则错峰动画整段只画一帧。
- 球纹理 d=128 纯 Python 合成, 由 `prebake_step` 启动后分帧预烘(当前投注档优先);
  球堆缓存 LRU 上限 12。

## 验证标准

RTP≈档位±0.05、卡死=0、撞钉音>90%、哑火零泄漏、
**弧面接触率≈100%**、冲顶x跨度≥20px、首钉75~110帧、转向每发都有、
首钉前无碰撞段折角≤4°/相邻帧差≤1°、音效体检0异常。
**回弹感硬门禁**(玩家可见口径, 不用"1px"糊弄): 像素弹高≥10px 占比≥25%(实测26.9%)、≥20px≥5%(实测6.1%)、max≤45px。
**悬念率哨兵**(软目标): 末段变向≥15° 或 落格≠越行槽 占比≥35%(实测 100%)、混沌帽(落格偏离入口≥5槽)≤10%(实测 1%)。
**下落节奏门禁**(专家组标定口径: power=0.8 + 固定种子): 碰钉减速比 0.45~0.80
(防黏滞<0.45/防穿阵>0.80) + 滞留帧≤30/发 + 行穿行≥0.15s。
**横向稳定性**: 底部区域 vx 从<80 突增>280 的次数=0(防"凭空横向移动"反物理)。
GUI 真发验证: 落格==结算槽 0 穿帮。
## 已知陷阱（改代码时注意）

- **`start_charge()` 必须立即调 `_set_controls_enabled(False)`**：否则充电窗口(0.5~1.0s)内 bet 按钮可点，多点触控可切下注额导致余额变负。`launch()` 才禁用为时已晚。
- **哑火分支的累加器必须与飞行分支同构**：`_misfire_frames` 递增和 `advance_misfire()` 都必须在 `while self._accumulator >= FIXED_DT` 循环**内部**，每物理步+1 而非每渲染帧+1。否则 120Hz 上超时误杀、30Hz 上动画变慢。
- **stall 位移检测必须在累加器循环内每步做**：循环外看净位移会漏检（多步子步位移矢量抵消）。帧计数不用墙钟，与 selftest 一致。
- **预演/真发确定性 = 同种子各自新建 rng，不是共享 rng 实例**：共享实例串行时，预演消耗后 rng 状态已变，真发轨迹不同。同种子(launch_ball(power, random.Random(seed)))逐帧一致。
- **弧面延长只能沿 25° 方向（大半径微弯）**：球抛体路径(37.8°)与弧面线夹角 12.8°，距离单调增；向左弯接近球抛体路径会二次接触，向右弯成钩子。改弧面形状必须重跑接触率/二次接触门禁。
- **弧面碰撞半径必须=视觉半径(ARC_VISUAL=1.4)**：球渲染 12.6 比碰撞 9 大，弧面不用视觉半径会看到球"嵌进"弧面。
- **修订注入必须在累加器循环内逐物理帧消费 events(清位), 不能等 tick 末 _play_events**：events 是位或累积, 一次 tick 推进多物理帧时残留位会让撞钉计数虚高, 注入点错位 → 落格偏差(实测 35%)。正确做法: 循环内每帧 `tick_ev |= b.events` 收集给音效 + 计数/注入 + `b.events=0; b.amp.clear()`(与预演 `_sim_flight` 同构)。(注: 修订机制已删, 此条为历史教训, 事件消费约定仍有效)
- **plinko.py 与 main.py 架构不同**：plinko.py 用 `tkinter.after(FIXED_DT)` 定时间隔（无累加器），改物理/引导逻辑时两边要分别评估是否受影响。改物理/音效/selftest → 改 plinko.py 再重跑生成器; 改 Android GUI(每轮/弹簧/弧面绘制) → 改 tools/android_part_ui.py(不经过 plinko.py)。两版 GUI 差异(PC 无防沉迷、Android 有)是设计使然, 不是漂移。
- **`GameArea._redraw()` 的 `canvas.clear()` 会把子控件的 canvas 一起摘掉**（叠加层"指令都在、屏幕没有"，不报错）。每次重画板面后必须 `_restack_overlays()` 把中奖杯与大字的 canvas 按序重新 add 回末尾。判据: `canvas.indexof(child.canvas) == -1`。详见 BUILD_APK.md §3.24。
- **Kivy 子控件的 canvas 是绝对(窗口)坐标，父级不做平移**：子控件里画东西要用 `self.x/self.y + 偏移`，不要按"父级局部帧"减父容器坐标。详见 §3.25。
- **生成器不做任何去重，同名模块级变量静默覆盖**：新分片的名字一律加前缀。实踩: `_BALL_TEX` 撞上 ui 段的同名变量被重置成 `None` → 首次中奖 AttributeError。详见 §3.26。
- **测中奖先切 300% 档**：80% 档的盘面抽不出 x50/x100，用固定盘面测会漏掉高倍率（颗数最多、锁最久、最易掉帧）的真实场景。
- **彩蛋（弹珠返回发射槽）走 `_easter_hold` 全套锁**：它不设 `_land_hold`，不锁的话 `park_ball` 会在 `landed_at+0.6s` 就跑掉——重掷盘面、state 回 `ready`、按钮恢复，然后装杯才播。锁从 `settle()` 一路持续到**弹窗关掉**（`_easter_finish`）。
  - **顺序：先播 ×2 装杯 → 全部落定 → 弹对话框**（`_on_easter_settled`）→ 玩家点确定 → 解锁。`_on_easter_settled` 有 `_easter_hold` 守卫：玩家若中途按重置又发了一发（`launch` 会清锁），就不再弹窗盖在新一局上。
  - 这条路径**不设状态栏文字**（结果全交给弹窗说）；播报就一条：语音档播 `voice_easter_{1,10,50,100}`（按投注档穷举），否则播 ×2 轻赢琶音。
- **装杯的落地音只靠节流限速，不按颗数截断**（`BOUNCE_THROTTLE=0.10`）。曾有个 `BOUNCE_BUDGET=24` 的"只前 N 颗播"上限，是照搬"SoundPool 8 条流会被 coin 抢"的估计加的 —— 但 coin 早已挪到揭晓之后也不在装杯期响，而 `bounce` 音只有 0.11s，10/秒的重叠也就 1~2 条流。实测代价：×20 后半段静音 1.4s、×50 静音 1.8s、×100 静音 2.3s（弹珠还在落、声音没了）。
- **中奖杯尺寸改 `CUP_W` 时先量 PNG 的内容边界**：玻璃图 800 宽里真实内容只占 x 24~776，所以杯宽最多能到 553（内容刚好贴满 520 的画布）。底缘用 `CUP_BOTTOM` 锚定（不是杯顶），这样改杯高只往上长，不会压到倍率槽。
- **揭晓合流：中奖的数字/余额/语音一起在"最后一颗球落定"那一刻给**（`_reveal_win`）。t=0 只落账务 + 槽闪 + 灯 + 顶栏中间态（`命中 xN · 结算中…`）。以前大字 t=0 就报、语音等装杯播完才念，中间隔一整场（×2≈2.0s、×100≈3.7s），数字等于提前剧透。
  - **余额必须真冻结**：只把三行赋值搬到 `_reveal_win` 是不够的 —— `_anim_start_time` 还是旧值，`elapsed >= _anim_dur` 会走 else 分支 0.1 秒内追平。要靠 `_anim_pending` 让 `_frame` 整段跳过。
  - `elapsed` 要 `max(0.0, ...)` clamp：负值时噪声项 `(1-t)` > 1 反而放大，会抖出一帧偏移。
  - **`on_done` 里必须同时调 `_reveal_win` 和 `_play_win_voice`**（顺序：先立大字再响）。只挂语音的话揭晓会靠 deadline 兜底才出来，两者差 0.2s。
  - 兜底 deadline = `settle + expected_sec(m) - 0.45`；`expected_sec` 实测比真实 T 高 0.55~0.80s（它按 `FALL_MAX` 算），所以当兜底是安全的。
  - 大字生命 `BIG_TEXT_LIFE=1.8s`（原 3.0s）—— 揭晓已挪到 T，而解锁在 T+0.45，3.0s 会飘进下一局的蓄力期，且 `TEXT_CY_WIN=150` 恰等于 `PEG_TOP=150`。
- **槽位指示灯是「中了就绿 / 未中红」，不按倍率取色**（与 PC 版同一套）。按倍率取色会有两个毛病：`slot_color(5)` 恰好 `== COL_FIRE`（x5 中奖和未中一模一样），而且倍率槽色块本来就是 `slot_color(该格倍率)`，灯再上同色是重复信息。

## git commit 注意

本仓库的 shell 环境是 bash。**不要用 PowerShell 的 here-string 语法 `@'...'@`** 来写多行 commit message——bash 会把 `@` 当作文本内容，导致 GitHub 提交记录只显示一个 `@`。正确做法：

```
git commit -m "第一行标题" -m "第二行正文"   # 多行最简单
# 或
git commit -m "$(cat <<'EOF'
标题行
正文行
EOF
)"
```
