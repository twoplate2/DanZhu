# -*- coding: utf-8 -*-
"""p4a hook: Android 12+ 大屏(平板/折叠屏, sw>=600dp)方向适配。

策略(2026-08-16 定稿): 不锁方向, 声明 fullSensor 四方向随重力——
  1) targetSdk 降到 30 → 不触发 12L+ 大屏"强制多窗口/忽略 screenOrientation",
     fullSensor 能真正生效(治本)
  2) manifest 强制 screenOrientation=fullSensor → 横拿时系统给全屏横窗口,
     app 内切"左盘面+右控制列"分栏布局(盘面竖直、正对玩家、铺满全屏),
     彻底绕开 letterbox 兼容小盒子(锁竖屏时被 ZUI 塞进近正方形半屏盒, 无解)

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
    """强制 screenOrientation=fullSensor(正竖/倒竖180°/横屏 四方向随重力)。
    p4a 对多个 --orientation 可能写 unspecified 或单值, 这里兜底强制改回。
    横拿时系统给全屏横窗口, app 内分栏布局; 不再锁竖屏(锁竖屏会被 12L+ 大屏
    letterbox 政策/ZUI 厂商策略关进近正方形半屏兼容盒, app 改不了盒子宽高)。"""
    manifest = _find_manifest(self)
    if not manifest:
        info('[hook] AndroidManifest.xml 未找到, 跳过')
        return 0
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()

    n0 = 0
    m_orient = re.search(r'android:screenOrientation="[^"]*"', xml)
    if m_orient:
        if m_orient.group(0) != 'android:screenOrientation="fullSensor"':
            xml = xml.replace(m_orient.group(0), 'android:screenOrientation="fullSensor"')
            n0 = 1
    else:
        m_act = re.search(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>', xml)
        if m_act:
            xml = xml[:m_act.end() - 1] + ' android:screenOrientation="fullSensor">' + xml[m_act.end():]
            n0 = 1

    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(xml)
    info('[hook] screenOrientation 强制 fullSensor: changed=%d' % n0)
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
