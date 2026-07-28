# 跳跳的弹珠机 — Android (Kivy) 版

PC 版 `plinko.py`(tkinter) 的 Android 移植。Kivy 2.3 重写界面, 竖屏,
玩法/物理/34 个程序化合成音效与 PC 版一致: 蓄力/哑火/RTP 三档/近10次积分/
落袋浮字/中奖大字/槽位白闪/滚分动画/渐变球贴图, 桌面端另支持空格键蓄力。

布局自适应: 窄屏(手机竖屏)铺满宽度, 宽屏(16:10 桌面)内容列居中并拉满高度。

字体层级体系(对齐 PC 版比例, 基准正文 14sp): Hero 48sp(中奖金额)/ Key 18sp(金色主数字+标题)/
Act 16sp(按钮)/ Body 14sp(正文)/ Aux 13sp(辅助)/ Minor 12sp(历史行);
场景内文字(槽位倍率/落袋浮字)用逻辑 20px 跟盘面缩放。原则: UI 文字用 sp 保证手机物理可读,
英雄字按屏宽占比设计不跟场景缩。

## 构建 APK (GitHub Actions 云构建, 无需本地装 Android SDK)

1. push 到 `main` 分支(或 Actions 页手动 `workflow_dispatch`)
2. 等 Actions 跑完(首次 15-20 分钟, 命中缓存后 5-8 分钟)
3. run 详情页底部 **Artifacts** → `plinko-apk.zip` → 解压得 `.apk` → 传手机安装

## 桌面运行 / 测试

```
python main.py              # 开窗口玩(模拟手机竖屏 420x780)
python main.py --selftest   # 无界面自测(RTP/命中/卡死/哑火/音效体检)
python main.py --smoke      # 自动冒烟: 蓄力发射 + 哑火, 截图到 %TEMP%/plinko_smoke
python main.py --nosound    # 静音启动
```

## main.py 是生成物, 不要直接编辑

`main.py` 由 `../tools/build_android_main.py` 自动拼接生成:

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
buildozer.spec           # 打包配置(锁 p4a v2024.01.21, 见 ../BUILD_APK 参考文档)
icon.png                 # 1024x1024 启动器图标
presplash.png            # 1080x1920 启动屏(边缘 #0b1220, 与 presplash_color 一致)
fonts/NotoSansSC-Medium.otf   # 中文字体(不打进 APK 汉字全豆腐块)
.github/workflows/build-apk.yml   # 云构建流水线
```

## 音效在 Android 上的实现

合成代码与 PC 版完全相同(34 个 PCM, 后台线程烘焙 ~330ms), 烘焙后写成 WAV 缓存文件,
用 `pyjnius` 调 Android `SoundPool` 加载播放(游戏音效专用 API, 多路并发由硬件 mixer 处理)。
桌面则走 winmm(Windows)或 Kivy SoundLoader 后备。

参考: `E:\AI_Tools\other\shalou_claude\android\BUILD_APK.md`(云构建全流程踩坑记录)
