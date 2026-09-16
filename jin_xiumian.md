# 跑分期间防息屏失效原因

## 结论

问题集中在 `main.py`，有两个独立原因：

1. **真正执行安卓常亮标志时取错了 Java 类，导致 `FLAG_KEEP_SCREEN_ON` 根本没有成功设置。** 这是两个跑分模块都会受到的主因。
2. **“开始模拟测试”的防息屏开启得太晚。** 它被绑在后半段的黑屏/物理演算上，前面的预热等待和屏幕渲染跑分没有任何防息屏保护。

因此，这套功能并不是偶尔被安卓系统取消，而是代码本身没有可靠地建立常亮状态。表面上看起来“经常失效”，是因为屏幕超时时间、用户刚刚触摸过屏幕等条件不同；不是当前实现有时成功、有时失败。

## 原因一：`FLAG_KEEP_SCREEN_ON` 的所属类写错

实际代码位于 `main.py:17849-17854`：

```python
_w = act.getWindow()
_WM = autoclass('android.view.WindowManager')
if _WAKE_MODE[0]:
    _w.addFlags(_WM.FLAG_KEEP_SCREEN_ON)
else:
    _w.clearFlags(_WM.FLAG_KEEP_SCREEN_ON)
```

`FLAG_KEEP_SCREEN_ON` 并不定义在 `android.view.WindowManager` 上，而是定义在其嵌套类：

```text
android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
```

在 pyjnius 中对应的类名应是：

```text
android.view.WindowManager$LayoutParams
```

当前通过 `_WM.FLAG_KEEP_SCREEN_ON` 读取字段时会失败，后续的 `addFlags()` 或 `clearFlags()` 不会真正执行。

更麻烦的是，整段代码被 `main.py:17855-17856` 的下面逻辑静默吞掉了：

```python
except Exception:
    pass
```

所以真机上既不会闪退，也没有日志提示。由于设置系统栏的代码排在防息屏代码前面，可能出现“黑屏和全屏切换正常，但防息屏没生效”的假象。

这也解释了为什么权限不是病因：本实现选择的是窗口标志，本来就不需要 `WAKE_LOCK` 权限；问题发生在错误的常量访问上。

## 原因二：“开始模拟测试”的前半段没有开启防息屏

防息屏只挂在 `_show_bench_dim()` 中：

- `main.py:11418` 定义 `_show_bench_dim()`；
- `main.py:11434` 才调用 `_set_keep_awake(True)`；
- `main.py:11469-11479` 在 `_hide_bench_dim()` 中关闭常亮。

但是“开始模拟测试”不是一开始就调用 `_show_bench_dim()`：

1. `main.py:12407` 的 `_start_bench_test()` 已经把 `_bench_running` 设为 `True`，并开始等待预热；
2. `main.py:12468-12474` 的预热等待最长可达 20 秒，此时没有开启常亮；
3. `main.py:12476-12567` 开始第一阶段屏幕渲染跑分（自动发 5 发球），仍没有开启常亮；
4. 第一阶段结束后，`main.py:12804` 才进入 `_wait_idle_then_bench()`；
5. 直到 `main.py:13104`，后半段物理演算准备开始时才调用 `_show_bench_dim()`，也才间接尝试开启常亮。

也就是说，代码注释中“把防息屏挂在黑屏开关上即可覆盖整个跑分”的假设不成立。黑屏只覆盖后半段，不能代表“开始模拟测试”的完整生命周期。

## 两个跑分模块分别受什么影响

| 模块 | 防息屏调用覆盖 | 实际结果 |
|---|---|---|
| 开始模拟测试 | 只覆盖后半段黑屏物理演算；预热和屏幕渲染阶段漏掉 | 前半段必然无保护；后半段又因原因一无法真正设置安卓标志 |
| CPU 高压测试 | `main.py:11652` 在工作线程启动前调用 `_show_bench_dim()`，调用时机基本正确 | 仍因原因一无法真正设置安卓标志，长时间运行时最容易暴露 |

## 为什么现有自测没有发现

`temp/_sysui.py:84-120` 的测试只做了两类检查：

- 调用 `_show_bench_dim()` 后，确认 Python 变量 `_WAKE_MODE[0]` 变成 `True`；
- 用字符串搜索确认源码里出现了 `FLAG_KEEP_SCREEN_ON`、`addFlags` 和 `clearFlags`。

它没有在 Android 环境调用 pyjnius，也没有验证常量到底属于哪个 Java 类，更没有验证 Window 的 flags 是否真的包含 `FLAG_KEEP_SCREEN_ON`。因此即使真机 API 每次都失败，桌面测试依然会全部通过。

## 修复状态

2026-09-16 已按上述原因修复：

1. 改从 `android.view.WindowManager$LayoutParams` 取得 `FLAG_KEEP_SCREEN_ON`。
2. “开始模拟测试”在整个测试生命周期开始时就开启常亮，不再等到后半段黑屏出现。
3. CPU 高压测试的频率采样窗口由 `[1, 359]` 秒改为 `[12, 348]` 秒。

频率采样线程是“读完多核 sysfs，再等待 0.5 秒”，不是严格的每秒一次。旧窗口
`[1, 359]` 秒真机实测为 424 点时，新窗口按同一节拍预计约为
`424 × 336 / 358 ≈ 398` 点。点数会受设备 sysfs 读取和线程调度耗时影响，不是固定值。
