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
```

## main.py 是生成物

`main.py` 由**父项目** `tools/build_android_main.py` 生成:
- 常量/几何/物理/音效合成/selftest **原样抽取**自父项目 `plinko.py`
- Kivy 手写段来自 `tools/android_part_{head,backends,ui}.py`

本仓库只含生成结果, **不含生成器和源文件**。因此:
- **在父项目环境里**: 改源文件 → `python tools/build_android_main.py` → `python android/main.py --selftest` → 回本仓库 commit。**不要手改 main.py**。
- **只有本仓库时**: 直接改 main.py 可行, 但父项目重新生成会覆盖。重大改动必须回父项目做。

## main.py 内部结构

1. **常量+几何**: 520×660 逻辑坐标系(y 向下)。发射槽在井底(PLUNGER_Y=FLOOR-BALL_R-2, 弹簧 Z 字形露出), 发射速度 1077~1114, G=1000
2. **物理层**: `Ball`(__slots__ class, 20属性, 含 `_rng` 撞钉扰动流), `physics_step`, `steer_ball`(碰钉瞬间一次性微调 + 入槽 ALIGN, 飞行途中零干预), `choose_target`, `solve_landing`(修订求解), `benchmark_trajectories`
   - **三段式轨迹**: 竖直上升(vx=0)→弧面反射(25°接触段, ARC_E=0.5)→抛体进钉阵
   - **弧面** = 根部R8弧 + 25°接触段15px + R400微弯弧30px(切线连续无转折), 碰撞半径=视觉半径(ARC_VISUAL=1.4, 球与弧面相切)
   - **预发射修订求解**(`solve_landing`): launch 时同种子预演, 落格≠预定槽 → 最后碰撞点注入 dvx(二分 ±800, 注入点前碰撞序列逐帧一致) → 仍失败换种子重来 —— 盘面/倍率一律不动。实测 1500 发零失败, 平均预演 ~5 次。真发按 (seed, 注入点k, dvx) 重放: `_frame` 累加器循环内计数撞钉, 第 k 次碰撞帧末 `b.vx += dvx`
   - **弧面抖动**: 每发 ±6px 垂直平移(arc_dy, 基于 _base_deflectors 重建, 预演/真发共享)
   - **stall 检测**: 累加器循环内每步位移检测(帧计数, 不用墙钟)。72步不动→速度踢向下(max(abs(vy)+80, 400) + 随机水平扰动150, 最多10次)/240步→强制结算
   - **帧率适配**: `_frame(dt)` 用累加器模式, `dt` 累加到≥FIXED_DT 才推物理步, 60/90/120/144Hz+30fps 物理速度精确一致
3. **音效**: 36 合成 PCM + 50 edge-tts 语音(voice/*.wav)。`Sfx.play()`: gain 10档缓存、按名节流
   - **全局语音互斥**: voice_rtp_/voice_bet_/voice_mode_ 3.0s 间隔。click throttle 0.08s。flight 跳过 bake/prime
   - **弧面接触音**: EV_ARC 事件播 rail 0.18(轻金属"擦"声, throttle 0.05)——转向瞬间的听觉反馈
4. **后端**: `_SoundPoolOut`(Android) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音
5. **Kivy UI**: `GameArea`(FloatLayout) + `RootWidget`(BoxLayout 6行)
   - 弹簧: Z字形, k=120/damp=3.2 阻尼振荡回弹, 视觉倍率45, 过冲clamp=-0.25
   - 状态机: ready→charging→flying/misfire→landing→landed, 累加器驱动
   - 发射: frozen_power 保存力度, 音量分级(0.60→0.80), 震动(哑火8ms/正常14ms)
   - 落地: LAND_E=0.42, LAND_BOUNCE_MIN_VY=220, ±8%随机, 不瞬移
   - 飞行中灰化: `_set_controls_enabled(False)` 时按钮+标签文字统一变暗
   - 中奖大字: life=3.0s, font_size 仅值变时写
   - 满蓄力: 每0.60s轻响 charge_full(0.40)
   - 防沉迷: balance/round_plays/plays/hits 持久化, 启动自动处理打满状态
   - 返回键拦截(key 27), 声音状态一致, 轮次结束语音兜底 total+3.0s
   - **性能测试(隐藏)**: 长按标题3s→10s benchmark→Popup弹窗(设备/帧数/次秒)

## 语音播报

50条语音(43原有+7 RTP/bet切换)。三态开关: 语音已开→音效已开→音效已关。
voice_lose 有意不接入(合成 lose 音更中性)。
语音互斥仅对 UI 交互生效, 结果/轮次序列不受影响。

## 验证标准

RTP≈档位±0.05、卡死=0、撞钉音>90%、天花板<10%、哑火零泄漏、
**弧面接触率≈100%**、冲顶x跨度≥20px、首钉70~90帧、转向每发都有、
首钉前无碰撞段折角≤4°/相邻帧差≤1°、音效体检0异常。
**修订求解成功率=100%**(300/300, 定向修正+换种子, 平均预演≤7次, 盘面不动)。
修订前命中率>35%(宽松哨兵, 防物理退化)。
**下落节奏门禁**: 行穿行p50≥0.18s + 碰钉减速比p50≤0.90(弹珠机手感, 防"嗖嗖穿过")。
GUI 真发验证: 落格==结算槽 0 穿帮。

## 已知陷阱（改代码时注意）

- **`start_charge()` 必须立即调 `_set_controls_enabled(False)`**：否则充电窗口(0.5~1.0s)内 bet 按钮可点，多点触控可切下注额导致余额变负。`launch()` 才禁用为时已晚。
- **哑火分支的累加器必须与飞行分支同构**：`_misfire_frames` 递增和 `advance_misfire()` 都必须在 `while self._accumulator >= FIXED_DT` 循环**内部**，每物理步+1 而非每渲染帧+1。否则 120Hz 上超时误杀、30Hz 上动画变慢。
- **stall 位移检测必须在累加器循环内每步做**：循环外看净位移会漏检（多步子步位移矢量抵消）。帧计数不用墙钟，与 selftest 一致。
- **预演/真发确定性 = 同种子各自新建 rng，不是共享 rng 实例**：共享实例串行时，预演消耗后 rng 状态已变，真发轨迹不同。同种子(launch_ball(power, random.Random(seed)))逐帧一致。
- **弧面延长只能沿 25° 方向（大半径微弯）**：球抛体路径(37.8°)与弧面线夹角 12.8°，距离单调增；向左弯接近球抛体路径会二次接触，向右弯成钩子。改弧面形状必须重跑接触率/二次接触门禁。
- **弧面碰撞半径必须=视觉半径(ARC_VISUAL=1.4)**：球渲染 12.6 比碰撞 9 大，弧面不用视觉半径会看到球"嵌进"弧面。
- **修订注入必须在累加器循环内逐物理帧消费 events(清位), 不能等 tick 末 _play_events**：events 是位或累积, 一次 tick 推进多物理帧时残留位会让撞钉计数虚高, 注入点错位 → 落格偏差(实测 35%)。正确做法: 循环内每帧 `tick_ev |= b.events` 收集给音效 + 计数/注入 + `b.events=0; b.amp.clear()`(与预演 `_sim_flight` 同构)。
- **plinko.py 与 main.py 架构不同**：plinko.py 用 `tkinter.after(FIXED_DT)` 定时间隔（无累加器），改物理/引导逻辑时两边要分别评估是否受影响。PC 版 launch 也有修订机制(与 Android 一致)。

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
