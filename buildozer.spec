[app]

# 启动器中显示的可见名称
title = 跳跳的弹珠机

# 内部包名(小写,无空格,无中文)
package.name = plinko
package.domain = org.danzhu

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,wav,mp3
# assets/*.png = 中奖玻璃杯表现的三张分层贴图(pile3d 的容器剖面与其同源)。
# 说明: png 本来就在 include_exts 白名单里, 这行是双保险 —— BUILD_APK.md 有
# "子目录资源必须显式列"的历史经验, 加它无害, 真伪由出包后解 private.tar 验证。
source.include_patterns = fonts/*.otf,voice/*.wav,assets/*.png

# 0.5.14(2026-09-10): 声音开关改两态(去掉了"语音已开"这档, 新的"音效已开"= 老的
# "语音已开"含语音); 声音设置不再持久化; 删孤儿语音 voice_mode_voice;
# 顺带修 PC 侧 off->on 崩溃(enabled=True 而 out=None -> play() AttributeError)。
# 0.5.13(2026-09-10): 中奖杯进场/退场改分层动画(压暗与道具错峰 + 整组位移/缩放),
# 治"突然插入/突然消失"; 退场尾巴内部分配 0.30/0.15 -> 0.20/0.25(和不变, 解锁不动)。
# 0.5.12(2026-09-10): 修复横屏时弹窗宽度错 —— _popup 的宽度改吃 _veq()(等效竖屏),
# 原来用 size_hint 取的是裸窗口宽, 横窗时比界面宽 60~80%。
# 0.5.11(2026-09-10): 彩蛋顺序改为"先播装杯 -> 落定 -> 弹对话框"(原来弹窗在前);
# 删掉装杯落地音的 BOUNCE_BUDGET=24 颗数上限(它让 ×20 后段静音 1.4s / ×100 静音 2.3s)。
# 0.5.10(2026-09-10): 揭晓合流 —— 大字/余额滚动/结果语音 全部推迟到"最后一颗球落定"
# 那一刻一起给(以前 t=0 就报数字, 语音等装杯播完才念, 中间隔一整场); 槽位指示灯改回
# "中了就绿/未中红"(原按倍率取色, 而 slot_color(5) 恰好等于未中的红); PC 大字生命
# 0.85->1.0s、状态栏量词改"个弹珠"。
# 0.5.9(2026-09-10): 彩蛋(弹珠返回发射槽)改中性正式文案 + 补晓晓语音(按投注档 4 条) +
# 弹窗关掉后补播 ×2 装杯; 中奖杯整体放大 16%(470->545 逻辑宽, 球径等比放大)。
# 0.5.8(2026-09-10): 中奖杯启动期分帧预热(球纹理按当前投注档优先 + 7 档球堆) +
# 球堆缓存 LRU 上限 12 + 投放间隔收进 _make_balls 一处算。补 fx_probe 与踩坑文档。
# 0.5.7(2026-09-10): 中奖演出期间锁输入(播完才放行) + 中奖大字上移让位 + 赢音改由
# 落定时刻回调触发 + 杯子位置改到画面中下部。含 FX_MAX_SEC 软锁兜底。
# 0.5.6(2026-09-10): 接线中奖玻璃杯渲染层(WinPileFX 挂进 GameArea, settle 时播放)。
# 0.5.5(2026-09-10): 引入中奖玻璃杯表现层(assets/ 三张 PNG + pile3d 球堆模块)。
# 0.5.4(2026-08-26): 竖屏启动沉浸不生效修复 —— setSystemUiVisibility 从 Python
# 线程直调被安卓线程检查静默拦截, 改投递 UI 线程(runOnUiThread)执行, 转屏自愈
# 的假象消失, 竖屏打开即全屏。0.5.3 是闪退真凶修复(零参签名)。
# ⚠️ 每次出包必须 bump: 版本号是"装的是哪个包"的唯一肉眼证据(APK 文件名含版本)。
version = 0.5.14

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
