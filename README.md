# 跳跳的弹珠机 — Android (Kivy) 版

PC 版 `plinko.py`(tkinter) 的 Android 移植。Kivy 2.3 重写界面, 竖屏,
玩法/物理/36 个程序化合成音效与 PC 版一致。

## 玩法

按住「蓄力发射」或空格蓄力(力度条过红线才有效, 否则哑火不扣珠),
松手把球从右侧竖井弹上天, 穿钉阵落入 9 个倍率槽之一, 按倍率赔付。
落点由纯物理被动决定(无预定), RTP 精确 80%/120%/200%/300% 四档, 物理表演与操作手感完全真实。

## 防沉迷机制

每轮最多 N 次发射(20/50/100 可选, 默认 50), 达到上限弹出总结窗口并语音播报,
播完自动重置。轮次历史持久化保存(最近 100 条, 重启不丢失)。

## 界面 (6 行全宽上下结构)

```
[顶栏]   语音已开(左) + 每轮X次(左) + 标题居中 + 状态(右)
[返还]   期望返还比例: [80%] [120%] [200%] [300%]
[投入]   每次投入珠子: [1个] [10个] [50个] [100个]
[游戏区] 全宽钉阵 + 竖井 + 倍率槽 + 弹簧凹槽
[信息]   珠子 + 累计x投x中(x%)
[底行]   [重置] —长距离— [蓄力发射]
```

排版: 返还/投入/信息/底行四行首字符统一左沿 24dp, 行内 spacing 5dp。
顶栏标题右偏 36dp 防移动端重叠, 双因子缩放自适应窄屏(横屏由反旋转层接管, 见下节)。

## 特效与反馈

- 中奖 48sp 金色大字 + 槽位白闪 + 落袋浮字
- 程序化渐变球(1.4×视觉放大) + 力度条(哑火红线标识)
- **弹簧可视化**: 地板下方暗色凹槽内 Z 字形弹簧(上横→斜线→下横),
  线宽 1.5px, 跨度 32px, 颜色灰蓝→金黄渐变, 2 条→1 条随蓄力压缩, 释放后 0.5s 平滑回弹
- **发射按钮**: 充电时深棕(#8B6914), 哑火区间红色, 蓄力百分比文字已移除
- **重置按钮**: on_press 闪亮反馈, 强制中断任何状态重置,
  toast 28sp "珠子数量已重置" + 语音播报

## 设备能力

切后台自动静音(SoundPool autoPause)、x10+ 大奖滚分/停留 1.2s、
x2 起中奖震动(45/75/110/150ms)、顶栏声音三态开关(语音已开=绿底,默认 /
音效已开=蓝底 / 音效已关=深底, 点按循环)。飞行时语音按钮/轮次按钮同步灰化。

## 横屏适配(大屏平板, 2026-08 定稿)

平板/折叠屏(Android 12+ 大屏)上的方向行为:

- **四方向重力感应**: 正竖 / 倒竖 180° / 左横 / 右横全跟手;
- **横拿时画面保持竖屏构图、铺满全屏**: 玩家扭头看或转回竖屏玩——
  游戏不横屏化(不做左右分栏, 也不缩成小竖条)。

实现四件套, 缺一不可:

| 层 | 做法 | 位置 |
|----|------|------|
| manifest | `screenOrientation=fullSensor` + `resizeableActivity=true` | `p4a/hook.py` 注入 |
| 版本标签 | `targetSdk 33` | `buildozer.spec`(`android.api`, 30 的兼容模式在大屏=半屏盒) |
| **运行时方向守卫(关键解药)** | 启动/回前台/转屏瞬间/每 0.7s 调 `setRequestedOrientation(FULL_SENSOR)` | `../tools/android_part_ui.py` `PlinkoApp._force_fullsensor / _orient_guard` |
| 横屏反旋转层 | 横窗时按"等效竖屏窗口"(短边×长边)布局, 以屏幕中心整体旋转 ±90° 铺满; 触摸逆变换; 弹窗挂层 | `../tools/android_part_ui.py` `LandLayer / RotPopup` |

**病根(联想 Y700/ZUI 实测)**: 大屏系统对"竖屏 app"横拿时塞 1519×1754 半宽兼容盒;
manifest 方向声明对 ZUI 无效, **它只认运行时方向请求**——而 SDL 引擎每次启动/回前台
会自报"竖屏", 把 manifest 的声明顶掉。所以必须运行时持续重申 fullSensor(方向守卫),
系统才肯把半屏盒换成全屏窗口, 反旋转层才有机会工作。另外"锁竖屏"在 Android 12L+
大屏上不是"屏幕不转"而是"被 letterbox 成小窗", 是死路, 不要再走。

已知边界: 转屏瞬间有一帧级画面重排(系统真的把盒窗换成全屏窗, 无法完全无感);
若某个横拿方向画面上下颠倒, 翻转 `../tools/android_part_ui.py` 里 `_land_angle()`
的 90/-90 映射(一行)。

## 语音播报 (edge-tts 预录, voice/*.wav 51 个)

| 场景 | 语音 | 说明 |
|------|------|------|
| 中奖 | voice_win{payout} | 15 种 payout 全覆盖, 替换 win 琶音 |
| 余额不足 | voice_nomoney | 替换 error 嗡声, throttle=3.0s |
| 轮次结束 | voice_round_end_{N} + 数字朗读 + voice_round_suffix | 队列拼接(5ms间隔), 不拼接 PCM |
| 轮次设定 | voice_round_set_{N} | 切换后即时播报 |
| 手动重置 | voice_reset_progress | "珠子数量已重置", throttle=1.5s |
| 声音切换 | voice_mode_{voice,sfx,off} | 三态切换提示 |

数字朗读: 0~9 / 十百千万 / 两(二/两规则), 共 22 个独立片段, 对标 Clac 项目方案。

生成: `python ../tools/generate_voice.py`。51 个语音文件。

## 构建 APK (GitHub Actions 云构建)

1. push 到 `main` 分支(或 Actions 页手动 workflow_dispatch)
2. 等 Actions 跑完(首次 15-20 分钟, 缓存命中后 ~3 分钟)
3. run 详情页底部 **Artifacts** → `plinko-apk.zip` → 解压得 `.apk` → 传手机安装

**完整流程 + 踩坑记录见 `BUILD_APK.md`。**

## 桌面运行 / 测试

```
python main.py              # 开窗口玩(540×960; 宽屏最大化内容居中)
python main.py --selftest   # 无界面自测(RTP/命中/卡死/哑火/音效体检)
python main.py --smoke      # 自动冒烟 + 截图
python main.py --landscape  # 桌面模拟横屏反旋转(1740×1000 横窗, 验证横屏布局用)
python main.py --nosound    # 静音启动
```

## main.py 是生成物, 不要直接编辑

由 `../tools/build_android_main.py` 自动拼接生成:

| 要改什么 | 改哪里 |
|------|------|
| 物理/几何/盘面/落点预定/音效配方/自测 | `../plinko.py` |
| Kivy 界面/控件/帧循环 | `../tools/android_part_ui.py` |
| 音效后端(SoundPool/winmm/SoundLoader) | `../tools/android_part_backends.py` |
| 入口/字体/参数解析 | `../tools/android_part_head.py` |
| 语音文案/词条 | `../tools/generate_voice.py` |

改完执行 `python ../tools/build_android_main.py` → `python main.py --selftest` 验证。

## 文件说明

```
main.py                       # Kivy 应用(生成物, 3200+行)
buildozer.spec                # 打包配置(p4a v2024.01.21, VIBRATE, targetSdk 33)
p4a/hook.py                   # 构建后 hook: manifest 注入 fullSensor + resizeable
BUILD_APK.md                  # 云构建流程 + 移植弯路集
how_to_desigin.html           # 多专家协作汇报页面
icon.png / presplash.png      # 图标 + 启动屏
fonts/NotoSansSC-Medium.otf   # 中文字体
voice/*.wav                   # 预录语音 51 个(edge-tts)
.github/workflows/build-apk.yml  # 云构建流水线
```

## 音效实现

合成代码与 PC 版完全相同(36 个 PCM), 启动时同步烘焙(冷启动 ~3s, 热启动 ~20ms),
完成后才显示界面。桌面走 winmm(8声道), Android 走 SoundPool(硬件 mixer),
无声卡自动静音不崩。

语音播报是 edge-tts 预录文件, named 后端直接 prime 原文件不落缓存,
pcm 后端剥头并入 bank —— 两种路径与合成音效完全同构。

数字朗读采用队列拼接方案(对标 Clac 项目): 中文文本 tokenize 成 clip key 序列,
按 duration+5ms 间隔依次 `Clock.schedule_once` 排队播放, 每个片段保持 Xiaoxiao 原声音质。
