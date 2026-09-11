# -*- coding: utf-8 -*-
"""p4a hook: Android 12+ 大屏(平板/折叠屏, sw>=600dp)方向适配。

策略(2026-08-17 定稿, 像素取证后修正): 不锁方向 + 现代 targetSdk——
  1) buildozer.spec android.api=33(不再降 30): 30 的兼容模式在 12L+ 大屏会被塞
     固定比例 letterbox 盒(联想 ZUI 近正方形半屏盒, 实测声明 fullSensor 也躲不开,
     系统只认 targetSdk)。33+ 是全屏窗口的前提。
  2) manifest 强制 screenOrientation=fullSensor(四方向随重力) + resizeableActivity=true:
     横拿时系统给全屏横窗, app 内 LandLayer 把画面反转回竖拿构图铺满(用户扭头看/
     转回竖屏玩)。锁竖屏时代(sensorPortrait+targetSdk30)已彻底结束。

p4a hook 函数只接收一个参数 self(ToolchainCL 实例); before_apk_build 在
current_directory(dist.dist_dir) 块内执行, cwd 即 dist 目录。
"""
import os
import re

try:
    from pythonforandroid.logger import info
except Exception:
    info = print


def _find_manifest(self):
    candidates = [
        os.path.join('src', 'main', 'AndroidManifest.xml'),
        'AndroidManifest.xml',
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    try:
        base = self.ctx.distribution.dist_dir
        for c in candidates:
            p = os.path.join(base, c)
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def _inject_manifest(self):
    """主 activity 强制 screenOrientation=fullSensor(四方向随重力) + resizeableActivity=true。
    targetSdk>=31 时 12L+ 大屏对"可 resize+全方向"的 app 给全屏窗口; 不给这两个声明
    会被塞 letterbox 兼容盒(app 改不了盒子宽高)。p4a 对多个 --orientation 可能写
    unspecified, 这里兜底强制改回。"""
    manifest = _find_manifest(self)
    if not manifest:
        info('[hook] AndroidManifest.xml 未找到, 跳过')
        return 0
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()

    changed = 0
    m_act = re.search(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>', xml)
    if not m_act:
        info('[hook] PythonActivity 标签未找到, 跳过')
        return 0
    tag = m_act.group(0)

    # 1) screenOrientation=fullSensor
    m_orient = re.search(r'android:screenOrientation="[^"]*"', tag)
    if m_orient:
        if m_orient.group(0) != 'android:screenOrientation="fullSensor"':
            tag = tag.replace(m_orient.group(0), 'android:screenOrientation="fullSensor"')
            changed += 1
    else:
        tag = tag[:tag.rfind('>')] + ' android:screenOrientation="fullSensor">' + tag[tag.rfind('>') + 1:]
        changed += 1

    # 2) resizeableActivity=true(31+ 默认即 true, 显式写双保险)
    m_rs = re.search(r'android:resizeableActivity="[^"]*"', tag)
    if m_rs:
        if m_rs.group(0) != 'android:resizeableActivity="true"':
            tag = tag.replace(m_rs.group(0), 'android:resizeableActivity="true"')
            changed += 1
    else:
        tag = tag[:tag.rfind('>')] + ' android:resizeableActivity="true">' + tag[tag.rfind('>') + 1:]
        changed += 1

    if changed:
        xml = xml[:m_act.start()] + tag + xml[m_act.end():]
        with open(manifest, 'w', encoding='utf-8') as f:
            f.write(xml)
    info('[hook] fullSensor+resizeable 注入: changed=%d' % changed)
    return changed


def _inject_theme(self):
    """把"应用启动窗口"的底色改成和 presplash 同一个 #0b1220。

    病根(真机录屏量出来的): 冷启动 0.15~0.22s 那一段, 应用窗口已经开了、presplash 还没画上去,
    屏幕上是**一片中性灰黑**(实测 #181314) —— 既不是 presplash 的底色(#0b1220), 也不是纯黑,
    是系统那层 starting window 的默认底色。它和后面的棋盘之间就是一个**突变**(玩家原话:
    「总之这个东西不能是突变」)。

    Android 12+ 起, 这层由主题的 `windowSplashScreenBackground` 决定; 本 app 用的是框架主题
    (`@android:style/Theme.NoTitleBar`, p4a 默认), 没有任何自定义 style(在 APK 的 resources.arsc
    里查过: 只有 drawable/id/layout/mipmap/string 五种类型)。所以这里补一个继承它的主题,
    **只改底色**, 其余属性全部沿用 —— 改动面越小越不容易把 app 弄起不来。

    ⚠️ `parent` 必须和 p4a 原来那个一致(NoTitleBar), 否则会在别处改变行为;
    ⚠️ 图标**故意不动**: 系统会把图标从小放大到内容区, 那本身就是一段动画; 换成透明图标反而会让
       棋盘"凭空出现"(那也是突变)。底色对齐之后, "同一个图案放大"就是最顺的接法。"""
    res_dir = os.path.join('src', 'main', 'res', 'values')
    try:
        if not os.path.isdir(res_dir):
            os.makedirs(res_dir)
        styles = os.path.join(res_dir, 'styles.xml')
        with open(styles, 'w', encoding='utf-8') as f:
            f.write(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<resources>\n'
                '    <style name="PlinkoTheme" parent="@android:style/Theme.NoTitleBar">\n'
                '        <item name="android:windowSplashScreenBackground">#0b1220</item>\n'
                '        <item name="android:windowSplashScreenIconBackgroundColor">#0b1220</item>\n'
                '    </style>\n'
                '</resources>\n')
        info('[hook] 主题 res/values/styles.xml 已写入')
    except Exception as e:
        info('[hook] 主题写入失败(跳过): %r' % (e,))
        return 0

    manifest = _find_manifest(self)
    if not manifest:
        return 0
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()
    m_app = re.search(r'<application[^>]*?>', xml)
    if not m_app:
        info('[hook] <application> 标签未找到, 主题跳过')
        return 0
    tag = m_app.group(0)
    if 'android:theme="@style/PlinkoTheme"' in tag:
        return 0
    m_th = re.search(r'android:theme="[^"]*"', tag)
    if m_th:
        tag = tag.replace(m_th.group(0), 'android:theme="@style/PlinkoTheme"')
    else:
        tag = tag[:tag.rfind('>')] + ' android:theme="@style/PlinkoTheme">' + tag[tag.rfind('>') + 1:]
    xml = xml[:m_app.start()] + tag + xml[m_app.end():]
    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(xml)
    info('[hook] application theme -> @style/PlinkoTheme')
    return 1


def before_apk_build(self):
    """gradle assemble 前: 注入 manifest(此时改才影响最终 APK)。
    ⚠️ 不再降 targetSdk——30 兼容模式是大屏 letterbox 盒的元凶, 保持 spec 的 33。"""
    info('[hook] before_apk_build 开始')
    _inject_manifest(self)
    _inject_theme(self)


def after_apk_build(self):
    """兜底(幂等): 万一 before 没跑到, 再试一次。"""
    info('[hook] after_apk_build 兜底')
    _inject_manifest(self)
    _inject_theme(self)
