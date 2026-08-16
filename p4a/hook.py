# -*- coding: utf-8 -*-
"""p4a hook: 构建后往 AndroidManifest 主 activity 注入竖屏锁定属性。
解决 Android 12+ 大屏(平板/折叠屏, sw>=600dp)强制多窗口导致 screenOrientation 被忽略、
以及部分制造商(如联想 Y700 5代)主动覆盖方向的问题。

p4a hook 函数只接收一个参数 self(ToolchainCL 实例); after_apk_build 在
current_directory(dist.dist_dir) 块内执行, cwd 即 dist 目录, manifest 在
src/main/AndroidManifest.xml。
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


def after_apk_build(self):
    manifest = _find_manifest(self)
    info('[hook] manifest = {}'.format(manifest))
    if not manifest:
        info('[hook] AndroidManifest.xml 未找到, 跳过')
        return
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()
    if 'android:resizeableActivity' in xml:
        info('[hook] 已注入过, 跳过')
        return

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
