# -*- coding: utf-8 -*-
"""p4a hook: 构建后往 AndroidManifest 主 activity 注入竖屏锁定属性。
解决 Android 12+ 大屏(平板/折叠屏, sw>=600dp)强制多窗口导致 screenOrientation 被忽略、
以及部分制造商(如联想 Y700 5代)主动覆盖方向的问题。
"""
import os
import re


def after_apk_build(self, *args, **kwargs):
    dist = getattr(self, 'dist_dir', None)
    if not dist:
        return
    manifest = os.path.join(dist, 'AndroidManifest.xml')
    if not os.path.exists(manifest):
        return
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()
    if 'android:resizeableActivity' in xml:
        return  # 已注入, 幂等

    # 1. 主 activity 加 resizeableActivity="false"
    #    让大屏 App 进 letterbox 兼容模式(竖屏比例), 使 screenOrientation 重新生效
    if 'android:screenOrientation=' in xml:
        xml = xml.replace(
            'android:screenOrientation=',
            'android:resizeableActivity="false" android:screenOrientation=',
            1,
        )

    # 2. 主 activity 内加 property 子标签(退出制造商的方向覆盖)
    m = re.search(r'<activity[^>]*org\.kivy\.android\.PythonActivity[^>]*?>', xml)
    if m:
        prop = ('\n            <property '
                'android:name="android.window.PROPERTY_COMPAT_ALLOW_ORIENTATION_OVERRIDE" '
                'android:value="false"/>')
        xml = xml[:m.end()] + prop + xml[m.end():]

    with open(manifest, 'w', encoding='utf-8') as f:
        f.write(xml)
