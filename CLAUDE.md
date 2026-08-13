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
2. **物理层**: `Ball`(__slots__ class, 21属性, 含 `_rng` 撞钉扰动流), `physics_step`, `launch_ball`, `advance_flight`(纯被动: 重力+碰撞, 零干预), `benchmark_trajectories`
   - **三段式轨迹**: 竖直上升(vx=0)→弧面反射(25°接触段, ARC_E=0.5)→抛体进钉阵
   - **弧面** = 根部R8弧 + 25°接触段15px + R400微弯弧30px(切线连续无转折), 碰撞半径=视觉半径(ARC_VISUAL=1.4, 球与弧面相切)
   - **彻底被动**: 无引导/无修订/无预定槽, 球完全被动下落, 结算==物理落格恒成立(零穿帮)
   - **碰钉回弹**: PEG_BOUNCE_VY_MAX=300(弹高≤45px≤一行钉距) + E_SLOW=0.70/E_FAST=0.40 + PEG_FRICTION=0.95/vy0.97 + PEG_GLANCE_UP=150(侧碰掠射向上, 回弹频率高) + PEG_SPRINT=0.7软化(末段保留横速, 落袋干净靠 ALIGN 收尾)
   - **弧面抖动**: 每发 ±6px 垂直平移(arc_dy), 档内首钉散布 ±10~15px
   - **卡死兜底看位移**: 球位置不动(≤1px/帧)超 MAX_FALL_SEC(4s)才强制 settle
   - **benchmark_trajectories(duration)**: 纯 CPU 性能测试
3. **音效**: 36 合成 PCM + 50 edge-tts 语音(voice/*.wav)。`Sfx.play()`: gain 10档缓存、按名节流
   - **全局语音互斥**: voice_rtp_/voice_bet_/voice_mode_ 3.0s 间隔。click throttle 0.08s。flight 跳过 bake/prime
   - **弧面接触音**: EV_ARC 事件播 rail 0.18(轻金属"擦"声, throttle 0.05)——转向瞬间的听觉反馈
4. **后端**: `_SoundPoolOut`(Android) > `_WaveOut`(winmm) > `_KivySoundOut` > 静音
5. **Kivy UI**: `GameArea`(FloatLayout) + `RootWidget`(BoxLayout 6行)
   - 弹簧: Z字形, k=120/damp=3.2 阻尼振荡回弹, 视觉倍率45, 过冲clamp=-0.25
   - 状态机: ready→charging→flying/misfire→landing→landed, 累加器驱动
   - 发射: frozen_power 保存力度, 音量分级(0.60→0.80), 震动(哑火8ms/正常14ms)
   - 落地: LAND_E=0.42, LAND_BOUNCE_MIN_VY=220, LAND_BOUNCE_MAX_VY=220(回弹vy上限, 删SLOT_BRAKE后防弹越隔板), ±8%随机, 不瞬移
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
