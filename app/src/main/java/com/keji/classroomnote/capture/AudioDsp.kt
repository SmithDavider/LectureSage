package com.keji.classroomnote.capture

import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor

class AudioDsp(audioSessionId: Int) {
    private val aec = runCatching {
        AcousticEchoCanceler.create(audioSessionId)?.apply { enabled = true }
    }.getOrNull()
    private val ns = runCatching {
        NoiseSuppressor.create(audioSessionId)?.apply { enabled = true }
    }.getOrNull()
    private val agc = runCatching {
        AutomaticGainControl.create(audioSessionId)?.apply { enabled = true }
    }.getOrNull()

    private var gain = 1.0f
    private var noiseRms = 260.0

    val effectsDescription: String
        get() = listOfNotNull(
            if (aec != null) "AEC" else null,
            if (ns != null) "NS" else null,
            if (agc != null) "AGC" else null,
        ).joinToString("/").ifBlank { "软件DSP" }

    fun process(samples: ShortArray, count: Int): Boolean {
        if (count <= 0) return false
        val rawRms = rms(samples, count)
        applyGain(samples, count, rawRms)
        val processedRms = rms(samples, count)
        updateNoiseFloor(processedRms)
        return processedRms >= silenceThreshold()
    }

    private fun applyGain(samples: ShortArray, count: Int, rawRms: Double) {
        val desired = if (rawRms > 1) {
            (TARGET_RMS / rawRms).toFloat().coerceIn(MIN_GAIN, MAX_GAIN)
        } else {
            gain
        }
        gain += (desired - gain) * GAIN_SMOOTHING
        val peakGain = if (rawRms > 1) {
            minOf(gain, (CLIPPING_LIMIT / rawRms).toFloat().coerceAtLeast(0.2f))
        } else {
            gain
        }

        for (i in 0 until count) {
            val amplified = samples[i] * peakGain
            samples[i] = amplified.coerceIn(-32768f, 32767f).toInt().toShort()
        }
    }

    private fun updateNoiseFloor(rms: Double) {
        val threshold = silenceThreshold()
        if (rms < threshold) {
            noiseRms = (noiseRms * 0.95) + (rms * 0.05)
        }
    }

    private fun silenceThreshold(): Double =
        maxOf(MIN_SPEECH_RMS.toDouble(), noiseRms * 1.9, MIN_ABSOLUTE_RMS.toDouble())

    private fun rms(samples: ShortArray, count: Int): Double {
        if (count <= 0) return 0.0
        var total = 0.0
        for (i in 0 until count) {
            val value = samples[i].toDouble()
            total += value * value
        }
        return kotlin.math.sqrt(total / count)
    }

    fun release() {
        runCatching { aec?.release() }
        runCatching { ns?.release() }
        runCatching { agc?.release() }
    }

    private companion object {
        private const val TARGET_RMS = 2600.0
        private const val MIN_GAIN = 0.25f
        private const val MAX_GAIN = 32.0f
        private const val GAIN_SMOOTHING = 0.15f
        private const val CLIPPING_LIMIT = 30000.0
        private const val MIN_SPEECH_RMS = 80
        private const val MIN_ABSOLUTE_RMS = 40
    }
}
