# -*- coding: utf-8 -*-
"""p4a hook: 解决 Android 12+ 大屏(平板/折叠屏, sw>=600dp)强制多窗口导致
screenOrientation 被忽略、以及联想 Y700 5代(ZUI)主动覆盖方向的问题。

根因: targetSdk>=31 的 app 在大屏上被系统强制多窗口, android:screenOrientation
完全失效。修复两招:
  1) targetSdk 降到 30  → 走兼容模式, sensorPortrait 正常生效(治本)
  2) 注入 resizeableActivity=false + PROPERTY_COMPAT_ALLOW_ORIENTATION_OVERRIDE=false
     → 退出制造商方向覆盖(治标, 对抗 ZUI)

p4a hook 函数只接收一个参数 self(ToolchainCL 实例); before_apk_build 在
current_directory(dist.dist_dir) 块内执行, cwd 即 dist 目录。
"""
import os
import re
import glob

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


def _lower_target_sdk(self):
    """targetSdk 降到 30: targetSdk<31 不触发大屏强制多窗口, screenOrientation 生效。
    同时改 manifest 的 uses-sdk 和所有 gradle 构建文件(递归), 覆盖多种写法。"""
    changed = 0
    # 1) manifest: android:targetSdkVersion="31"
    mf = _find_manifest(self)
    if mf:
        with open(mf, encoding='utf-8') as f:
            s = f.read()
        m = re.search(r'android:targetSdkVersion="(\d+)"', s)
        if m and m.group(1) != '30':
            s = s.replace(m.group(0), 'android:targetSdkVersion="30"')
            with open(mf, 'w', encoding='utf-8') as f:
                f.write(s)
            changed += 1
            info('[hook] manifest targetSdk: %s -> 30 (%s)' % (m.group(1), mf))
        else:
            info('[hook] manifest targetSdk 未改(现值=%s)' % (m.group(1) if m else '无'))
    # 2) gradle: targetSdkVersion 31 / targetSdkVersion = 31 / targetSdk 31 ...(递归)
    grads = glob.glob('**/*.gradle', recursive=True) + glob.glob('build.gradle')
    for g in grads:
        if not os.path.exists(g):
            continue
        with open(g, encoding='utf-8') as f:
            s = f.read()
        m = re.search(r'targetSdk(?:Version)?\s*[=:]?\s*(\d+)', s)
        if m and m.group(1) != '30':
            s = re.sub(r'targetSdk(?:Version)?\s*[=:]?\s*\d+',
                       'targetSdkVersion 30', s)
            with open(g, 'w', encoding='utf-8') as f:
                f.write(s)
            changed += 1
            info('[hook] gradle targetSdk: %s -> 30 (%s)' % (m.group(1), g))
        else:
            info('[hook] gradle targetSdk 未改(现值=%s) (%s)'
                 % (m.group(1) if m else '无', g))
    info('[hook] targetSdk 降级: changed=%d' % changed)
    return changed


def _inject_manifest(self):
    """强制 screenOrientation=sensorPortrait(正竖↔倒竖 180 度)。
    p4a 对多个 --orientation(portrait,portrait-reverse) 默认可能写 unspecified, 这里兜底强制改回。
    targetSdk=30 已自动走兼容模式, 不再注入 resizeableActivity=false 和方向覆盖属性
    (它们会干扰 letterbox 窗口尺寸, 导致横屏时窗口变成接近正方形、画面拉伸变形)。"""
    manifest = _find_manifest(self)
    if not manifest:
        info('[hook] AndroidManifest.xml 未找到, 跳过')
        return 0
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()

    n0 = 0
    m_orient = re.search(r'android:screenOrientation="[^"]*"', xml)
    if m_orient:
        if m_orient.group(0) != 'android:screenOrientation="sensorPortrait"':
            xml = xml.replace(m_orient.group(0), 'android:screenOrientation="sensorPortrait"')
            n0 = 1
    else:
        m_act = re.search(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>', xml)
        if m_act:
            xml = xml[:m_act.end() - 1] + ' android:screenOrientation="sensorPortrait">' + xml[m_act.end():]
            n0 = 1

    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(xml)
    info('[hook] screenOrientation 强制 sensorPortrait: changed=%d' % n0)
    return n0


def before_apk_build(self):
    """gradle assemble 前: 降 targetSdk + 注入 manifest(此时改才影响最终 APK)。"""
    info('[hook] before_apk_build 开始')
    _lower_target_sdk(self)
    _inject_manifest(self)


def after_apk_build(self):
    """兜底(幂等): 万一 before 没跑到, 再试一次。"""
    info('[hook] after_apk_build 兜底')
    _lower_target_sdk(self)
    _inject_manifest(self)
