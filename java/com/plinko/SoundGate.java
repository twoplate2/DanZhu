package com.plinko;

import android.media.SoundPool;
import android.os.SystemClock;
import java.util.HashSet;
import java.util.Set;

/**
 * Pure-Java readiness gate for SoundPool samples.
 *
 * ASCII ONLY, on purpose: javac's default source encoding on the build machine is
 * not guaranteed to be UTF-8, and one non-ASCII byte in a comment can fail the
 * whole APK build.  (Chinese notes about this class live in buildozer.spec.)
 *
 * Why this class exists
 * ---------------------
 * SoundPool.load() is asynchronous: a returned sampleId does NOT mean the sample
 * is decoded yet.  SoundPool.play() on a not-yet-ready sample returns 0 and does
 * nothing at all -- no exception, no log.  So the game must confirm that all 101
 * samples are playable before it dismisses the loading page.
 *
 * Confirming that by calling play() once per sample is what costs the time: on
 * some devices play() creates and starts an AudioTrack on the CALLING thread
 * (AOSP StreamManager kPlayOnCallingThread == true on Android 11..14), and
 * Stream.cpp only reuses an AudioTrack for the SAME soundID.  101 distinct
 * samples therefore means 101 track creations: measured 29..33 ms each, i.e.
 * about 3 seconds, on a Snapdragon 8+ Gen 1 / Android 13.
 *
 * OnLoadCompleteListener is the official "this sample is decoded" signal and it
 * costs nothing.  The app avoided it before because of the pyjnius bridge: a
 * Python object registered as a Java listener can be collected, or called from a
 * thread that is not attached to the JVM, and either failure would be silent.
 * This class removes that bridge -- the listener is a Java object, the counting
 * happens in Java, and Python only makes short down-calls that read plain ints.
 *
 * Threading: setOnLoadCompleteListener() creates its Handler on the calling
 * thread's Looper, or on the main Looper when the calling thread has none.  We
 * are called from a Python worker thread, which has no Looper, so callbacks are
 * delivered on the main (UI) thread.  That Handler holds this listener strongly,
 * so there is no GC hazard.
 */
public class SoundGate {

    // Process-wide diagnostics.  Static on purpose: they answer "how many times
    // did this process build a gate", i.e. whether a "hot start" is really a new
    // process or a reused one.
    private static final Object STATIC_LOCK = new Object();
    private static int sInitCount = 0;
    private static int sCallbackTotal = 0;
    private static String sCallbackThread = "";

    private final Object lock = new Object();
    private final SoundPool pool;                 // strong ref: keep it alive
    private final Set<Integer> expect = new HashSet<Integer>();
    private final Set<Integer> ready = new HashSet<Integer>();
    private final long t0 = SystemClock.elapsedRealtimeNanos();
    private long firstExpectAt = 0L;              // when the first load was registered
    private long allReadyAt = 0L;                 // 0 means "not yet"
    private int failed = 0;
    private int initSeq = 0;

    public SoundGate(SoundPool sp) {
        this.pool = sp;
        synchronized (STATIC_LOCK) {
            sInitCount++;
            this.initSeq = sInitCount;
        }
        // Must happen BEFORE the first load(): a load that already finished will
        // never fire again, and a missed callback means the gate can never open.
        sp.setOnLoadCompleteListener(new SoundPool.OnLoadCompleteListener() {
            public void onLoadComplete(SoundPool soundPool, int sampleId, int status) {
                synchronized (STATIC_LOCK) {
                    sCallbackTotal++;
                    if (sCallbackThread.length() == 0) {
                        try {
                            sCallbackThread = Thread.currentThread().getName();
                        } catch (Throwable t) {
                            sCallbackThread = "?";
                        }
                    }
                }
                synchronized (lock) {
                    if (status == 0) {
                        ready.add(Integer.valueOf(sampleId));
                    } else {
                        failed++;
                    }
                    refreshLocked();
                }
            }
        });
    }

    /** Called with "lock" held.  Latches the moment the last expected id arrived. */
    private void refreshLocked() {
        if (allReadyAt == 0L && !expect.isEmpty() && ready.containsAll(expect)) {
            allReadyAt = SystemClock.elapsedRealtimeNanos();
        }
    }

    /**
     * Register a sampleId we are waiting for.  Safe to call after that id's
     * callback already fired (Python calls this right after load() returns, and
     * the callback is posted asynchronously, so either order is possible).
     */
    public void expect(int sampleId) {
        synchronized (lock) {
            if (firstExpectAt == 0L) {
                firstExpectAt = SystemClock.elapsedRealtimeNanos();
            }
            expect.add(Integer.valueOf(sampleId));
            refreshLocked();
        }
    }

    public int expectCount() {
        synchronized (lock) { return expect.size(); }
    }

    /** How many of the expected ids are decoded. */
    public int readyCount() {
        synchronized (lock) {
            int n = 0;
            for (Integer id : expect) {
                if (ready.contains(id)) n++;
            }
            return n;
        }
    }

    /**
     * Latched on purpose: once every expected sample has been seen decoded, this
     * stays true forever.  The Python side can call prime() again later (the retry
     * path re-loads a failed sample, which registers one more expectation), and a
     * live recomputation would flip back to false -- i.e. the gate would "open and
     * then close again".  Use readyCount() for the live view.
     */
    public boolean allReady() {
        synchronized (lock) { return allReadyAt != 0L; }
    }

    /**
     * Exact ms from **the first registered load** to "every expected sample is
     * decoded", or -1 while not ready.
     *
     * Measured from the first expect() on purpose, NOT from construction: on a cold
     * start the pool is built long before the samples are even loaded (the bank is
     * still being synthesised), and a "since construction" number would then be
     * dominated by the synthesis, which is not what this is for.  From the first
     * load onward, this is the same quantity the play()-based probe measures, so the
     * two are directly comparable.
     *
     * Reading this instead of polling also removes the Python polling interval.
     */
    public double readyMs() {
        synchronized (lock) {
            if (allReadyAt == 0L || firstExpectAt == 0L) return -1.0;
            return (allReadyAt - firstExpectAt) / 1000000.0;
        }
    }

    /** Ms from construction to the first registered load (diagnostic only). */
    public double sinceAttachMs() {
        synchronized (lock) {
            if (firstExpectAt == 0L) return -1.0;
            return (firstExpectAt - t0) / 1000000.0;
        }
    }

    public int failedCount() {
        synchronized (lock) { return failed; }
    }

    /** 1 for the first gate built in this process, 2 for the next, ... */
    public int initSeq() {
        return initSeq;
    }

    public static int initCount() {
        synchronized (STATIC_LOCK) { return sInitCount; }
    }

    public static int callbackTotal() {
        synchronized (STATIC_LOCK) { return sCallbackTotal; }
    }

    /** Thread name of the first callback; "" means no callback ever arrived. */
    public static String callbackThreadName() {
        synchronized (STATIC_LOCK) { return sCallbackThread; }
    }
}
