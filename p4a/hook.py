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
    """targetSdk 降到 30: targetSdk<31 不触发大屏强制多窗口, screenOrientation 生效。"""
    changed = 0
    mf = _find_manifest(self)
    if mf:
        with open(mf, encoding='utf-8') as f:
            s = f.read()
        s2 = re.sub(r'android:targetSdkVersion="\d+"',
                    'android:targetSdkVersion="30"', s)
        if s2 != s:
            with open(mf, 'w', encoding='utf-8') as f:
                f.write(s2)
            changed += 1
            info('[hook] targetSdk 降级 manifest: %s' % mf)
    for g in glob.glob('build.gradle'):
        with open(g, encoding='utf-8') as f:
            s = f.read()
        s2 = re.sub(r'targetSdkVersion\s+\d+', 'targetSdkVersion 30', s)
        if s2 != s:
            with open(g, 'w', encoding='utf-8') as f:
                f.write(s2)
            changed += 1
            info('[hook] targetSdk 降级 gradle: %s' % g)
    info('[hook] targetSdk 降级: changed=%d' % changed)
    return changed


def _inject_manifest(self):
    """注入 resizeableActivity=false + 退出方向覆盖属性。"""
    manifest = _find_manifest(self)
    if not manifest:
        info('[hook] AndroidManifest.xml 未找到, 跳过注入')
        return 0, 0
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()
    if 'android:resizeableActivity' in xml:
        info('[hook] 已注入过, 跳过')
        return 0, 0

    n1 = 0
    if 'android:screenOrientation=' in xml:
        xml = xml.replace(
            'android:screenOrientation=',
            'android:resizeableActivity="false" android:screenOrientation=',
            1,
        )
        n1 = 1

    n2 = 0
    m = re.search(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>', xml)
    if m:
        prop = ('\n            <property '
                'android:name="android.window.PROPERTY_COMPAT_ALLOW_ORIENTATION_OVERRIDE" '
                'android:value="false"/>')
        xml = xml[:m.end()] + prop + xml[m.end():]
        n2 = 1

    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(xml)
    info('[hook] 注入完成: resizeableActivity={}, property={}'.format(n1, n2))
    return n1, n2


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
