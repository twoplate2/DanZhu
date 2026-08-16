[app]

# 启动器中显示的可见名称
title = 跳跳的弹珠机

# 内部包名(小写,无空格,无中文)
package.name = plinko
package.domain = org.danzhu

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
source.include_patterns = fonts/*.otf,voice/*.wav

version = 0.5.0

requirements = python3,kivy==2.3.0,pyjnius

# 锁定 python-for-android 到 2024 年的 tag,绕开新版默认下载 Python 3.14 alpha 的问题
p4a.branch = v2024.01.21

# 竖屏+180度: 两个值 => SDL hint "Portrait PortraitUpsideDown" => sensorPortrait(7), 正竖↔倒竖不横屏
orientation = portrait, portrait-reverse
# 显式 manifest 方向: 多值时 p4a 会把 manifest 合成成 unspecified(启动会横屏闪), 显式锁 portrait 兜底
android.manifest.orientation = portrait
fullscreen = 0

android.permissions = VIBRATE

android.api = 31
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
