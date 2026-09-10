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

# 0.5.19(2026-09-10): 装杯"回味"时长改按倍率分档(用户定案) —— x2 0.6 / x3 0.7 / x5 0.8 /
# x10 0.9 / x20 1.0 / x50 1.1 / x100 1.2 秒(档位线性 +0.1)。用户连报三次"消失太快"。
# 为什么地板选 0.6 而不是更常见的 0.4: x2 占中奖场次 55%(x2+x3 占 80%), 而 0.35->0.40
# 这种 +0.05s 低于可辨差(~10~15%) —— 改了等于没改。为什么按档位线性: 倍率逐档约翻倍,
# 按档位 +0.1 等价于按 log(倍率) 线性(行业标准做法, US20070010315A1: 10分->2s, 100分->10s)。
# 真源收敛成 hold_for(m) 一个函数(RESULT_HOLD 常量删除): 原常量被 expected_sec / tick /
# _layers(3处) / ui 的 _reveal_deadline / fx_probe 共 7 处读取, 按档后若漏改一处就是**静默**脱钩
# (已经踩过一次: _reveal_deadline 硬编码 0.45, 尾巴改成 0.60 后兜底提前触发, 数字提前剧透)。
# fx_probe [5] 新增三条门禁: 最小档停留 >=0.50s、七档严格递增、最高档尾巴离 9s 硬兜底有充裕余量。
# 0.5.18(2026-09-10): 跑分期间不弹窗/不播装杯 —— 跑分时弹珠回到发射槽会弹彩蛋窗, 打断灰屏;
# 且装杯最多画 100 颗球, 那份渲染开销会污染正在采样的帧率。_on_easter_settled 的兜底必须走
# _easter_finish 解锁(只 return 会软锁)。顺带修回归: 弹窗改回等装杯整场播完再弹。
# 0.5.17(2026-09-10): 落定判据改"第一次触地" + 回弹改纯竖直 —— 一石二鸟。
# ①主游戏: 进入 landing 时 `b.vx = 0`(回弹纯竖直)。landing 循环里只有重力/横向弹簧/地板,
#   **没有隔板碰撞**, 球带飞行末段横速入槽 -> 回弹期能滑过隔板停到隔壁槽(结算槽仍是本槽
#   = "钱算对、球停错地方")。清零横速后横向只剩 LAND_K 弹簧, 而弹簧只把球拉向本槽中心,
#   拉不出去 —— 跳槽从物理上不可能(实测落定全程球心越界量 -11px, 只挪 0.2px)。
# ②主游戏结算提前到第一次触地(原来 88.7% 的落定走 0.5s 超时兜底、平均晚 0.3~0.5s)。
# ③装杯 T 改 `_last_touch`(最后一颗第一次触地), 不再等 max(end) —— 整场短 0.22~0.31s
#   (x2 1.71->1.46s, x100 3.30->3.08s)。弹跳照播: tick() 的 result 段也调 _advance_balls,
#   否则未落定的球会冻在半空。
# fx_probe 补 [8] 落定横向不越界门禁, [5] 加"弹跳尾巴能在解锁前播完"门禁。
# 0.5.16(2026-09-10): 中奖杯"淡入淡出效果差"四修 —— ①退场透明度改线性(原三次缓入让
# 79% 的窗口空转、最后 0.15s 从 0.90 崩到 0, 就是"突然消失"), 位移/缩放改二次缓出,
# 上浮 38->52px 缩到 0.90; ②进场位移 22->56px、缩放 0.97->0.92(玻璃贴图极透, 位移是
# 唯一读得出来的通道), 窗口 0.34/0.16->0.32/0.18(和仍 <= WINDUP=0.50); ③整场雨前移
# (RAIN_ANCHOR=0.12)消掉杯子就位后 0.44~0.55s 的空杯静止; ④修 expected_sec 不是上界
# 的真 bug(x100 有 3.9% 的局估得比真实短 -> 兜底 deadline 提前触发 -> 数字提前剧透)。
# fx_probe 补 [6]/[7] 两项曲线门禁(这块原来是零自动覆盖, 改坏了完全静默)。
# 0.5.15(2026-09-10): 修连按音效开关时"声音不太对": 语音互斥改按真实时长判(原来写死
# 3 秒 -> 连按时 3 秒内全静音, 音画脱节); 安卓取消静音不再 autoResume(会把被掐在
# 半路的提示音续播出来)。
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
version = 0.5.19

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
