# 弹珠机物理引擎：顶级PC vs 顶级移动设备性能差距？

## 问题

我写了一个弹珠游戏的物理引擎（纯 Python），想知道在**目前最顶级的 PC** 和**最顶级的移动设备**上，性能大概差多少倍？

具体来说：下面这段 `benchmark_trajectories` 代码，在两类设备上各能跑出多少次/秒？

## 已有实测数据

| 平台 | 设备 | 5秒模拟次数 | 每秒次数 | 每秒帧数 |
|------|------|-----------|---------|---------|
| PC | RTX 4070 + i7-13700K (桌面) | ~200 | ~40 | ~8200 |
| 移动 | 骁龙 8 Elite (手机) | 100~200 | 20~40 | 4100~8200 |

> 帧数按每次飞行平均 228 帧折算（73326 次实测，P50=231，P90=257，P99=289）

**疑问：**
1. 顶级 PC（如 i9-14900K / Ryzen 9 9950X）能跑到多少？
2. 顶级移动设备（如苹果 A18 Pro / 骁龙 8 Gen 4）能跑到多少？
3. PC 和手机的差距主要是单核性能还是 Python 解释器优化（如 PyPy vs CPython）？
4. 这段代码有没有明显的优化空间？

## 附录：性能测试代码

```python
def benchmark_trajectories(duration=5.0):
    """duration 秒内能模拟多少次完整飞行(发射→落地)。
    纯 CPU 单核测试, 不涉及渲染/音效。"""
    geo = build_geo()
    count = 0
    t0 = time.time()
    while time.time() - t0 < duration:
        board = roll_multipliers(random.choice((0.80, 1.00, 1.20)))
        target = choose_target(board, 1.00)
        tx = FIELD_L + (target + 0.5) * SLOT_W
        power = random.uniform(MISFIRE_POWER, 1.0)
        b = launch_ball(power)
        b["tease_dx"] = tease_dx(board, target)
        for _ in range(4000):
            landed = advance_flight(b, geo, tx)
            if landed is not None:
                count += 1
                break
    return count
```

### 核心物理循环 (`advance_flight`)

```python
def advance_flight(b, geo, target_x):
    steer_ball(b, target_x)          # 引导(只改 vx)
    if b["y"] > SLOT_TOP and b["vy"] > 0:
        b["vy"] *= SLOT_BRAKE_VY     # 槽区减速
        b["vx"] *= SLOT_BRAKE_VX
    return physics_step(b, geo, FIXED_DT)  # 碰撞检测+积分(6子步)
```

### 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| G | 1200 px/s² | 重力加速度 |
| SUBSTEPS | 6 | 每帧子步数(防穿透) |
| FIXED_DT | 1/60 s | 固定物理步长 |
| 钉子行数 | 7 | 交错网格 |
| 槽位数 | 9 | 底部倍率槽 |
| 平均飞行 | 228 帧/次 | 73326次实测(新物理) |

### 完整源代码

- PC版: `plinko.py` (tkinter)
- Android版: `main.py` (Kivy, 由PC版代码生成)
- 仓库: https://github.com/twoplate2/DanZhu

### 运行方式

```bash
# PC版性能测试
python plinko.py --selftest

# Android版性能测试(长按标题3秒触发)
python main.py
```
