# 跳跳的弹珠机 — Android (Kivy) 版

PC 版 `plinko.py`(tkinter) 的 Android 移植。Kivy 2.3 重写界面, 竖屏,
玩法/物理/36 个程序化合成音效与 PC 版一致。

## 玩法

按住「蓄力发射」或空格蓄力(力度条过红线才有效, 否则哑火不扣珠),
松手把球从右侧竖井弹上天, 穿钉阵落入 9 个倍率槽之一, 按倍率赔付。
落点由发射前随机数预定(RTP 精确 80%/100%/110% 三档), 物理表演与操作手感完全真实。

## 界面 (5 行全宽上下结构, 上设定/下信息)

```
[顶栏]   音效开关(左) + 标题居中 + 状态(右)
[返还]   期望返还比例: [80%] [100%] [110%]
[投入]   每次投入珠子: [1个] [10个] [50个] [100个]
[游戏区] 全宽钉阵 + 竖井 + 倍率槽(场景面积比右面板版 +32%)
[信息]   珠子 + 每次投x珠,累计x投x中(x%)
[底行]   [重置] —长距离— [力度] [蓄力发射]
```

特效: 中奖 48sp 金色大字、落袋浮字、槽位白闪、余额滚分、渐变球(1.4×)、
余额不足中央飘字"珠子数量不足/请降低投入或点击重置按钮"。

设备能力: 切后台自动静音(SoundPool autoPause)、x10+ 大奖滚分/停留 1.2s、
x2 起中奖震动(45/75/110/150ms)、顶栏声音三态开关(语音已开=绿底,默认 /
音效已开=蓝底 / 音效已关=深底, 点按循环)。

语音播报(edge-tts 预录, voice/*.wav 17 个): 语音已开时, 中奖播"珠子加xx"
(15 种 payout 全覆盖, 替换 win 琶音避免互盖), 余额不足播"珠子数量不足请重置
或降低投入"(替换 error 嗡声); "好遗憾"已制作暂未接入。生成: `python ../tools/generate_voice.py`。

字体层级: Hero 48sp(中奖金额)/ Key 18sp(金数+标题)/ Act 16sp(按钮)/
Body 14sp(正文)/ Aux 13sp(辅助); 场景内文字用逻辑 20px 跟盘面缩放。

## 构建 APK (GitHub Actions 云构建, 无需本地装 Android SDK)

1. push 到 `main` 分支(或 Actions 页手动 workflow_dispatch)
2. 等 Actions 跑完(首次 15-20 分钟, 缓存命中后 ~3 分钟)
3. run 详情页底部 **Artifacts** → `plinko-apk.zip` → 解压得 `.apk` → 传手机安装

**完整流程 + 踩坑记录(版本锁定/中文字体/移植弯路)见 `BUILD_APK.md`, 改代码前必读。**

## 桌面运行 / 测试

```
python main.py              # 开窗口玩(540×960, 16:9; 宽屏最大化内容居中)
python main.py --selftest   # 无界面自测(RTP/命中/卡死/哑火/音效体检)
python main.py --smoke      # 自动冒烟: 蓄力发射/必中盘/哑火/余额不足飘字 + 截图
python main.py --nosound    # 静音启动
```

## main.py 是生成物, 不要直接编辑

由 `../tools/build_android_main.py` 自动拼接生成:

| 要改什么 | 改哪里 |
|------|------|
| 物理/几何/盘面/落点预定/音效配方/自测 | `../plinko.py`(改完重跑生成脚本) |
| Kivy 界面/控件/帧循环 | `../tools/android_part_ui.py` |
| 音效后端(SoundPool/winmm/SoundLoader) | `../tools/android_part_backends.py` |
| 入口/字体/参数解析 | `../tools/android_part_head.py` |

改完执行 `python ../tools/build_android_main.py` 重新生成, 再 `python main.py --selftest` 验证。

## 文件说明

```
main.py                  # Kivy 应用(生成物)
buildozer.spec           # 打包配置(p4a v2024.01.21 锁定, VIBRATE 权限)
BUILD_APK.md             # 云构建流程 + 移植弯路集(下次做 APK 必读)
icon.png                 # 1024x1024 启动器图标
presplash.png            # 1080x1920 启动屏(边缘 #0b1220 = presplash_color)
fonts/NotoSansSC-Medium.otf   # 中文字体(不打进 APK 汉字全豆腐块)
.github/workflows/build-apk.yml   # 云构建流水线
```

## 音效实现

合成代码与 PC 版完全相同(36 个 PCM), 启动时同步烘焙(冷启动 ~3s, 热启动缓存 ~20ms),
完成后才显示界面。烘焙后写成 WAV 缓存文件,
用 `pyjnius` 调 Android `SoundPool` 加载播放(多路并发, 硬件 mixer)。
桌面则走 winmm(Windows)或 Kivy SoundLoader 后备, 无声卡自动静音不崩。

语音播报是 edge-tts 预录文件(voice/*.wav, 22050Hz 16bit mono 与合成音效同格式),
named 后端(SoundPool)直接 prime 原文件不落缓存, pcm 后端(winmm)剥头并入 bank ——
两种播放路径与合成音效完全同构, gain/节流/并发都走 Sfx 总线同一套。
