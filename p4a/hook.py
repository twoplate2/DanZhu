# -*- coding: utf-8 -*-
"""p4a hook: 构建后往 AndroidManifest 主 activity 注入竖屏锁定属性。
解决 Android 12+ 大屏(平板/折叠屏, sw>=600dp)强制多窗口导致 screenOrientation 被忽略、
以及部分制造商(如联想 Y700 5代)主动覆盖方向的问题。

注意: p4a hook 函数只接收一个参数 self(ToolchainCL 实例), 不是 dist_dir。
dist 目录在 self.ctx.distribution.dist_dir; 且 after_apk_build 在
current_directory(dist.dist_dir) 块内执行, 所以 cwd 就是 dist 目录。
"""
import os
import re


def _find_manifest(self):
    """定位 AndroidManifest.xml, 返回绝对路径或 None。"""
    candidates = [
        os.path.join('src', 'main', 'AndroidManifest.xml'),
        'AndroidManifest.xml',
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    # 兜底: 用 self.ctx.distribution.dist_dir
    try:
        dist = self.ctx.distribution
        base = dist.dist_dir
        for c in candidates:
            p = os.path.join(base, c)
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return None


def after_apk_build(self):
    manifest = _find_manifest(self)
    if not manifest:
        print('[hook] AndroidManifest.xml 未找到, 跳过竖屏注入')
        return
    with open(manifest, 'r', encoding='utf-8') as f:
        xml = f.read()
    if 'android:resizeableActivity' in xml:
        print('[hook] 已注入过, 跳过')
        return

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
    print('[hook] 已注入 resizeableActivity=false + 方向覆盖退出到', manifest)
