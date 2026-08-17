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


def before_apk_build(self):
    """gradle assemble 前: 注入 manifest(此时改才影响最终 APK)。
    ⚠️ 不再降 targetSdk——30 兼容模式是大屏 letterbox 盒的元凶, 保持 spec 的 33。"""
    info('[hook] before_apk_build 开始')
    _inject_manifest(self)


def after_apk_build(self):
    """兜底(幂等): 万一 before 没跑到, 再试一次。"""
    info('[hook] after_apk_build 兜底')
    _inject_manifest(self)
