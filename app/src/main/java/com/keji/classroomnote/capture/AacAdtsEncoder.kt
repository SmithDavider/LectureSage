package com.keji.classroomnote.capture

import android.media.MediaCodec
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.nio.ByteOrder

class AacAdtsEncoder(
    private val outputFile: File,
    private val sampleRate: Int = 16000,
    private val channels: Int = 1,
    private val bitRate: Int = 32000,
) : AutoCloseable {
    private val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
    private val info = MediaCodec.BufferInfo()
    private val output: OutputStream = FileOutputStream(outputFile)
    private var inputPresentationUs = 0L
    private var eosQueued = false
    private var eosReceived = false

    init {
        val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, sampleRate, channels).apply {
            setInteger(MediaFormat.KEY_AAC_PROFILE, android.media.MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            setInteger(MediaFormat.KEY_BIT_RATE, bitRate)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 65536)
        }
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        codec.start()
    }

    fun writePcm(samples: ShortArray, count: Int) {
        if (count <= 0 || eosQueued) return
        val totalBytes = count * 2
        var byteOffset = 0

        while (byteOffset < totalBytes) {
            val index = codec.dequeueInputBuffer(10_000)
            if (index < 0) {
                drain(final = false)
                continue
            }

            val inputBuffer = codec.getInputBuffer(index) ?: throw IllegalStateException("encoder input buffer missing")
            val bytesToWrite = minOf(inputBuffer.capacity(), totalBytes - byteOffset)
            inputBuffer.clear()
            val shortOffset = byteOffset / 2
            val shortCount = bytesToWrite / 2
            for (i in 0 until shortCount) {
                inputBuffer.putShort(i * 2, samples[shortOffset + i])
            }
            inputBuffer.position(0)
            inputBuffer.limit(bytesToWrite)

            val presentationUs = inputPresentationUs
            codec.queueInputBuffer(index, 0, bytesToWrite, presentationUs, 0)
            inputPresentationUs += (shortCount * 1_000_000L / sampleRate)
            byteOffset += bytesToWrite
            drain(final = false)
        }
    }

    override fun close() {
        if (!eosQueued) {
            val index = codec.dequeueInputBuffer(10_000)
            if (index >= 0) {
                codec.queueInputBuffer(index, 0, 0, inputPresentationUs, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                eosQueued = true
            }
            drain(final = true)
        }
        runCatching { codec.stop() }
        runCatching { codec.release() }
        runCatching { output.flush() }
        runCatching { output.close() }
    }

    private fun drain(final: Boolean) {
        while (!eosReceived) {
            val index = codec.dequeueOutputBuffer(info, if (final) 10_000 else 0)
            if (index == MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (!final) return
                continue
            }
            if (index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                codec.outputFormat
                continue
            }
            if (index < 0) continue

            val buffer = codec.getOutputBuffer(index)
            if (buffer != null && info.size > 0) {
                buffer.position(info.offset)
                buffer.limit(info.offset + info.size)
                writeAdtsFrame(buffer, info.size)
            }
            codec.releaseOutputBuffer(index, false)
            if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
                eosReceived = true
                break
            }
        }
    }

    private fun writeAdtsFrame(buffer: java.nio.ByteBuffer, payloadSize: Int) {
        val adts = ByteArray(ADTS_HEADER_SIZE)
        val frameLength = payloadSize + ADTS_HEADER_SIZE
        adts[0] = 0xFF.toByte()
        adts[1] = 0xF1.toByte()
        adts[2] = (((profile - 1) shl 6) + (frequencyIndex shl 2) + (channels shr 2)).toByte()
        adts[3] = (((channels and 3) shl 6) + ((frameLength shr 11) and 3)).toByte()
        adts[4] = ((frameLength shr 3) and 0xFF).toByte()
        adts[5] = (((frameLength and 7) shl 5) + 0x1F).toByte()
        adts[6] = 0xFC.toByte()
        output.write(adts)

        val bytes = ByteArray(payloadSize)
        buffer.get(bytes)
        output.write(bytes)
    }

    private val profile = 1
    private val frequencyIndex = sampleFrequencyIndex(sampleRate)

    private fun sampleFrequencyIndex(sampleRate: Int): Int = when (sampleRate) {
        96000 -> 0
        88200 -> 1
        64000 -> 2
        48000 -> 3
        44100 -> 4
        32000 -> 5
        24000 -> 6
        22050 -> 7
        16000 -> 8
        12000 -> 9
        11025 -> 10
        8000 -> 11
        else -> throw IllegalArgumentException("unsupported AAC sample rate: $sampleRate")
    }

    private companion object {
        private const val ADTS_HEADER_SIZE = 7
    }
}
