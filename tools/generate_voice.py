# -*- coding: utf-8 -*-
"""生成弹珠机语音播报 wav(edge-tts 预录制, 方案同 Clac/android/tools/generate_sounds.py)。

与计算器管线的三点差异:
  1. 重采样 24kHz -> 22050Hz: 弹珠机音效系统 SR=22050 写死(winmm WAVEFORMATEX),
     采样率不一致会被 winmm 按慢放播成怪音。
  2. 结果类语音(win*/lose)头部烘 130ms 静音(= plinko.py SFX_RESULT_LEAD),
     时序与被替换的 win 琶音完全对齐(等 pocket 入袋声先落地)。
  3. 统一峰值归一化(peak 0.65), 17 个文件响度一致。

用法:
    python tools/generate_voice.py           # 缺啥补啥(已存在则跳过)
    python tools/generate_voice.py --force   # 全量重生成(会改变文件字节, git diff 有噪音)

输出: android/voice/voice_*.wav (22050Hz 16bit mono)
依赖: pip install edge-tts miniaudio; 需要网络。
"""
import asyncio
import math
import os
import struct
import sys
import wave

EDGE_VOICE = "zh-CN-XiaoxiaoNeural"
EDGE_RATE = "+10%"
TRIM_THRESHOLD = 250          # 头尾静音判定阈值(≈0.7% 满幅, 同计算器)
TRIM_PAD_MS = 30              # 裁剪后头尾各留的缓冲
SR_OUT = 22050                # 对齐弹珠机音效系统采样率
RESULT_LEAD_MS = 130          # 结果音前置静音(SFX_RESULT_LEAD), 等 pocket 落地
PEAK = 0.65                   # 统一峰值

# key -> (播报文本, 头部前置静音 ms)
# payout 全集合 = bet{1,10,50,100} x 倍率{2,3,5,10,20,50,100} 去重; 新增 2500/5000/10000
PHRASES = {
    "voice_lose":    ("好遗憾", RESULT_LEAD_MS),          # 先制作, 暂不接入播报
    "voice_nomoney": ("弹珠数量不足,请重置或降低投入", 0),  # 余额不足(替换 error 嗡声)
    "voice_win2":    ("弹珠加二", RESULT_LEAD_MS),
    "voice_win3":    ("弹珠加三", RESULT_LEAD_MS),
    "voice_win5":    ("弹珠加五", RESULT_LEAD_MS),
    "voice_win10":   ("弹珠加十", RESULT_LEAD_MS),
    "voice_win20":   ("弹珠加二十", RESULT_LEAD_MS),
    "voice_win30":   ("弹珠加三十", RESULT_LEAD_MS),
    "voice_win50":   ("弹珠加五十", RESULT_LEAD_MS),
    "voice_win100":  ("弹珠加一百", RESULT_LEAD_MS),
    "voice_win150":  ("弹珠加一百五", RESULT_LEAD_MS),
    "voice_win200":  ("弹珠加两百", RESULT_LEAD_MS),
    "voice_win250":  ("弹珠加两百五", RESULT_LEAD_MS),
    "voice_win300":  ("弹珠加三百", RESULT_LEAD_MS),
    "voice_win500":  ("弹珠加五百", RESULT_LEAD_MS),
    "voice_win1000": ("弹珠加一千", RESULT_LEAD_MS),
    "voice_win2000": ("弹珠加两千", RESULT_LEAD_MS),
    "voice_win2500": ("弹珠加两千五", RESULT_LEAD_MS),
    "voice_win5000": ("弹珠加五千", RESULT_LEAD_MS),
    "voice_win10000": ("弹珠加一万", RESULT_LEAD_MS),
    # 声音模式切换提示 —— ⚠️ **2026-09-16 玩家要求两个方向都删掉**:
    #   · 「关闭声音」:「删掉关闭声音的这个音效, 也不加载」
    #     (玩家在办公室想静音, 按下去它却先大声念了一句「关闭声音」)
    #   · 「开启中奖音效」:「开启声音也不要了。避免误触」
    #     (误触时同样会突然出声 —— 安静场合下两个方向都该是哑的)
    #   两个 wav 都已从仓库删除, `toggle_mute` 现在是**静默切换**, 反馈只走按钮显示。
    #   别把它们加回来: 加回来会被加载, 但没有任何地方会播。
    # 轮次结束播报模板(play count 20/50/100, 后接动态弹珠数+后缀)
    "voice_round_end_20":  ("本轮游戏二十次已结束,剩余", 100),
    "voice_round_end_50":  ("本轮游戏五十次已结束,剩余", 100),
    "voice_round_end_100": ("本轮游戏一百次已结束,剩余", 100),
    "voice_round_suffix":  ("个弹珠,弹珠数量已调整到一千个,欢迎你再次挑战", 0),
    # 轮次设定切换提示
    "voice_round_set_20":  ("每轮已设定为二十次,弹珠数量调整到一千个", 0),
    "voice_round_set_50":  ("每轮已设定为五十次,弹珠数量调整到一千个", 0),
    "voice_round_set_100": ("每轮已设定为一百次,弹珠数量调整到一千个", 0),
    # 手动重置提示
    "voice_reset_progress": ("弹珠数量已调整到一千个", 0),
    # 返还比例 / 投入弹珠 切换提示
    "voice_rtp_80":  ("弹珠返还比例调整到百分之八十", 0),
    "voice_rtp_120": ("弹珠返还比例调整到百分之一百二十", 0),
    "voice_rtp_200": ("弹珠返还比例调整到百分之二百", 0),
    "voice_rtp_360": ("弹珠返还比例调整到百分之三百六十", 0),
    # ⚠️ 彩蛋档(长按"期望返还比例"5 秒解锁的那个)。名字由 `set_rtp` 拼: "voice_rtp_%d" % (档位x100)。
    #    2026-09-12: 彩蛋弹窗从「开/不开」改成四选一, 三档(1000/2000/5000)都要有语音。
    "voice_rtp_1000": ("弹珠返还比例调整到百分之一千", 0),
    "voice_rtp_2000": ("弹珠返还比例调整到百分之两千", 0),
    "voice_rtp_5000": ("弹珠返还比例调整到百分之五千", 0),
    # 「关闭隐藏」播报(玩家 2026-09-12 定稿): **一档一条** —— 把"关掉的是哪一档"和
    # "现在回到哪一档"一次说完。后半句是必要的: 切回 360% 那句 `voice_rtp_360` 被
    # `silent=True` 压掉了(两条都走普通路径会被 Sfx.play 的 voice_rtp_ 互斥按"上一句
    # 真实时长"挤掉一条), 这条是唯一的信息出口。
    # ⚠️ 数字写汉字, 与 voice_rtp_* 的既有读法("百分之两千")一致, 别写成 "2000%"。
    "voice_rtp_hide_1000": ("百分之一千的隐藏倍率已关闭,返还比例调整到百分之三百六十", 0),
    "voice_rtp_hide_2000": ("百分之两千的隐藏倍率已关闭,返还比例调整到百分之三百六十", 0),
    "voice_rtp_hide_5000": ("百分之五千的隐藏倍率已关闭,返还比例调整到百分之三百六十", 0),
    "voice_bet_1":   ("每次投入弹珠数量调整到一个", 0),
    "voice_bet_10":  ("每次投入弹珠数量调整到十个", 0),
    "voice_bet_50":  ("每次投入弹珠数量调整到五十个", 0),
    "voice_bet_100": ("每次投入弹珠数量调整到一百个", 0),
    # 彩蛋(弹珠落回发射槽, 按 ×2 结算)播报: 按投注档穷举, 金额直接念出来。
    # 无前置静音 —— 弹窗是模态的, 玩家注意力已经在屏上, 不用像 win* 那样等入袋声。
    "voice_easter_1":   ("弹珠返回发射槽,返还两个弹珠", 0),
    "voice_easter_10":  ("弹珠返回发射槽,返还二十个弹珠", 0),
    "voice_easter_50":  ("弹珠返回发射槽,返还一百个弹珠", 0),
    "voice_easter_100": ("弹珠返回发射槽,返还两百个弹珠", 0),
    # 数字朗读片段(队列拼接, 对标 Clac 项目的预录制方案)
    "voice_d_0": ("零", 0), "voice_d_1": ("一", 0), "voice_d_2": ("二", 0),
    "voice_d_3": ("三", 0), "voice_d_4": ("四", 0), "voice_d_5": ("五", 0),
    "voice_d_6": ("六", 0), "voice_d_7": ("七", 0), "voice_d_8": ("八", 0),
    "voice_d_9": ("九", 0),
    "voice_u_10":    ("十", 0),
    "voice_u_100":   ("百", 0),
    "voice_u_1000":  ("千", 0),
    "voice_u_10000": ("万", 0),
    "voice_liang":   ("两", 0),   # 千位/万位的 2 读"两"而非"二"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "voice"))


def _resample_linear(samples, src_sr, dst_sr):
    """线性插值重采样。17 个短文件, 纯 Python 足够快。"""
    if src_sr == dst_sr:
        return samples
    ratio = src_sr / float(dst_sr)
    n = len(samples)
    n_out = int(n / ratio)
    out = [0] * n_out
    last = n - 1
    for i in range(n_out):
        pos = i * ratio
        k = int(pos)
        if k >= last:
            out[i] = samples[last]
        else:
            f = pos - k
            out[i] = int(samples[k] * (1.0 - f) + samples[k + 1] * f)
    return out


def _trim(samples, sr):
    """裁头尾静音(阈值 TRIM_THRESHOLD), 各留 TRIM_PAD_MS 缓冲。"""
    n = len(samples)
    i = 0
    while i < n and abs(samples[i]) < TRIM_THRESHOLD:
        i += 1
    j = n - 1
    while j > i and abs(samples[j]) < TRIM_THRESHOLD:
        j -= 1
    if i >= j:
        return samples
    pad = max(1, int(TRIM_PAD_MS * sr / 1000))
    i = max(0, i - pad)
    j = min(n - 1, j + pad)
    return samples[i:j + 1]


def _normalize(samples, peak):
    pk = max(max(samples), -min(samples)) if samples else 0
    if pk < 1:
        return samples
    g = peak * 32767.0 / pk
    return [max(-32768, min(32767, int(v * g))) for v in samples]


async def _gen_one(key, text, lead_ms, out_path):
    import edge_tts
    import miniaudio
    tmp_mp3 = out_path + ".tmp.mp3"
    try:
        comm = edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE)
        await comm.save(tmp_mp3)
        d = miniaudio.mp3_read_file_s16(tmp_mp3)
        samples = list(d.samples)
        if d.nchannels == 2:
            samples = [(samples[i] + samples[i + 1]) // 2
                       for i in range(0, len(samples), 2)]
        samples = _trim(samples, d.sample_rate)
        samples = _resample_linear(samples, d.sample_rate, SR_OUT)
        if lead_ms > 0:
            samples = [0] * int(lead_ms * SR_OUT / 1000) + samples
        samples = _normalize(samples, PEAK)
        # 尾部 5ms 淡出防爆音(头部已有 30ms pad 或 130ms 静音, 无需淡入)
        fade = max(1, int(0.005 * SR_OUT))
        for i in range(1, min(fade, len(samples)) + 1):
            samples[-i] = int(samples[-i] * (fade - i) / fade)
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SR_OUT)
            wf.writeframes(struct.pack("<%dh" % len(samples), *samples))
        ms = len(samples) * 1000 // SR_OUT
        return True, "%6dB  %4dms" % (os.path.getsize(out_path), ms)
    finally:
        try:
            os.remove(tmp_mp3)
        except OSError:
            pass


async def _main(force):
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Backend : edge-tts  voice=%s  rate=%s" % (EDGE_VOICE, EDGE_RATE))
    print("Output  : %s  (%dHz 16bit mono)" % (OUT_DIR, SR_OUT))
    print("Phrases : %d\n" % len(PHRASES))
    ok = total = skipped = 0
    for key, (text, lead_ms) in PHRASES.items():
        total += 1
        out = os.path.join(OUT_DIR, key + ".wav")
        if os.path.exists(out) and not force:
            print("  SKIP  %-14s (exists)" % key)
            ok += 1
            skipped += 1
            continue
        try:
            success, msg = await _gen_one(key, text, lead_ms, out)
        except Exception as e:
            success, msg = False, "%s: %s" % (type(e).__name__, e)
        print("  %-4s  %-14s  %s  %s" % ("OK" if success else "FAIL", key, msg, text))
        if success:
            ok += 1
        else:
            try:
                if os.path.exists(out) and os.path.getsize(out) == 0:
                    os.remove(out)
            except OSError:
                pass
    print("\nDone: %d/%d ready (%d skipped, %d generated)." %
          (ok, total, skipped, total - skipped))
    if ok != total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main("--force" in sys.argv))
