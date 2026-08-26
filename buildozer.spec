[app]

# 启动器中显示的可见名称
title = 跳跳的弹珠机

# 内部包名(小写,无空格,无中文)
package.name = plinko
package.domain = org.danzhu

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
source.include_patterns = fonts/*.otf,voice/*.wav

# 0.5.3(2026-08-26): 修复真机闪退真凶 —— _enter_immersive() 零参签名收不了
# schedule_interval 塞进来的 dt, 启动 0.7s 即 TypeError 崩溃(logcat 实锤)。
# ⚠️ 每次出包必须 bump: 版本号是"装的是哪个包"的唯一肉眼证据(APK 文件名含版本)。
version = 0.5.3

requirements = python3,kivy==2.3.0,pyjnius

# 锁定 python-for-android 到 2024 年的 tag,绕开新版默认下载 Python 3.14 alpha 的问题
p4a.branch = v2024.01.21
# 构建后 hook: 往 manifest 主 activity 强制 screenOrientation=fullSensor + resizeableActivity=true
p4a.hook = p4a/hook.py

# 四方向随重力: 正竖/倒竖180°/横拿全支持。横拿时 app 内切左盘面+右控制列分栏(盘面竖直满屏),
# 不再锁竖屏——锁竖屏会被 12L+ 大屏 letterbox/ZUI 关进半屏兼容盒(画面缩小的病根)
orientation = portrait, portrait-reverse, landscape, landscape-reverse
# 显式 manifest 方向: fullSensor(四方向随重力, Android原生值), 与 hook 注入一致
android.manifest.orientation = fullSensor
# 全屏沉浸(仅运行时实现): 状态栏/导航栏由 main.py _enter_immersive() 负责隐藏。
# fullscreen 保持=0(与已知可跑的 0.5.0 一致, 不折腾): 0.5.1(fullscreen=1)与
# 0.5.2(=0)当时都闪退, 但真凶是 _enter_immersive 的零参签名 bug(见 BUILD_APK.md
# 弯路 3.22), fullscreen=1 本身未被证明有问题, 只是也没必要开。
fullscreen = 0

android.permissions = VIBRATE

# targetSdk=33(2026-08-17): 30 的兼容模式在 12L+ 大屏(sw>=600dp)会被塞固定比例
# letterbox 盒(ZUI 近正方形半屏盒, 实测 fullSensor 四方向也躲不开, 像素取证确认)。
# 33+ 声明全方向+resizeable 才给全屏窗口; 锁竖屏时代(sensorPortrait 需兼容模式)已结束。
android.api = 33
android.minapi = 21
android.ndk = 25b

android.archs = arm64-v8a,armeabi-v7a

android.allow_backup = True

icon.filename = %(source.dir)s/icon.png

# 必须和 presplash.png 边缘同色(#0b1220),避免闪屏黑闪
android.presplash_color = #0b1220
presplash.filename = %(source.dir)s/presplash.png


[buildozer]

log_level = 2
warn_on_root = 1
