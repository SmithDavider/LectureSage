package com.keji.classroomnote.capture

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.util.Log
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.view.WindowManager
import android.util.DisplayMetrics
import androidx.core.content.ContextCompat
import com.keji.classroomnote.R
import com.keji.classroomnote.capture.AacAdtsEncoder
import com.keji.classroomnote.capture.AudioDsp
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.PendingUploadWorker
import com.keji.classroomnote.net.StartCourseRequest
import com.keji.classroomnote.net.WebSocketEvent
import com.keji.classroomnote.store.LocalCacheStore
import com.keji.classroomnote.store.AppPreferences
import com.keji.classroomnote.store.CourseRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class CaptureService : Service() {
    private val logTag = "KejiCapture"
    private val prefs by lazy { AppPreferences(this) }
    private val api by lazy { ApiClient(this) }
    private val repository by lazy { CourseRepository(this) }
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val stopping = AtomicBoolean(false)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var audioRecord: AudioRecord? = null
    private var audioThread: Thread? = null
    private var mediaProjection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: android.media.ImageReader? = null
    private var screenHandler: Handler? = null
    private var screenThread: HandlerThread? = null
    private var webSocket: okhttp3.WebSocket? = null
    private var uploaderJob: Job? = null
    private var webSocketJob: Job? = null
    private var timerJob: Job? = null
    private var sessionDir: File? = null
    private var courseId = ""
    private var sessionStartMs = 0L
    private var notificationMinute = -1
    private var lastJpegBytes: ByteArray? = null
    private var pendingScreenFile: File? = null
    private var pendingScreenRunnable: Runnable? = null
    private var screenPollRunnable: Runnable? = null
    @Volatile
    private var activeAudioFile: File? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            "课堂采集",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "录音和截屏上传期间保持前台服务运行"
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val enableScreen = intent.getBooleanExtra(EXTRA_ENABLE_SCREEN, true)
                val enableMicrophone = intent.getBooleanExtra(EXTRA_ENABLE_MICROPHONE, true)
                try {
                    startAsForeground(
                        includeProjection = enableScreen,
                        enableMicrophone = enableMicrophone,
                    )
                    startCapture(intent)
                } catch (error: Exception) {
                    Log.e(logTag, "启动采集服务失败", error)
                    CaptureBus.setStatus("启动采集失败：${error.message}")
                    stopSelf()
                }
            }
            ACTION_STOP -> stopCapture()
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun startAsForeground(includeProjection: Boolean, enableMicrophone: Boolean = true) {
        val notification = buildNotification(0)
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> startForeground(
                NOTIFICATION_ID,
                notification,
                if (includeProjection) {
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION or
                        (if (enableMicrophone) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)
                } else {
                    if (enableMicrophone) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
                },
            )
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q -> startForeground(
                NOTIFICATION_ID,
                notification,
                if (includeProjection) ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION else 0,
            )
            else -> startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startCapture(intent: Intent) {
        if (stopping.get()) return
        stopping.set(false)
        val enableScreen = intent.getBooleanExtra(EXTRA_ENABLE_SCREEN, true)
        val enableMicrophone = intent.getBooleanExtra(EXTRA_ENABLE_MICROPHONE, true)
        if (!enableScreen && !enableMicrophone) {
            CaptureBus.setStatus("未启用任何采集功能")
            stopSelf()
            return
        }
        courseId = intent.getStringExtra(EXTRA_COURSE_ID) ?: CourseRepository.newId()
        AppPreferences(this).activeCourseId = courseId
        sessionStartMs = System.currentTimeMillis()
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty().ifBlank { "未命名课程" }
        val storageDir = getExternalFilesDir(null)
        if (storageDir == null) {
            CaptureBus.setStatus("外部存储不可用，无法开始采集")
            stopSelf()
            return
        }
        sessionDir = File(storageDir, "sessions/$courseId").apply { mkdirs() }
        lastJpegBytes = null
        pendingScreenFile = null
        pendingScreenRunnable = null
        CaptureBus.start(courseId, title)

        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
        @Suppress("DEPRECATION")
        val projectionData = intent.getParcelableExtra<Intent>(EXTRA_PROJECTION_DATA)
        if (enableScreen) {
            if (projectionData == null) {
                CaptureBus.setStatus("未获得录屏授权")
                stopSelf()
                return
            }
            startScreenCapture(resultCode, projectionData)
        } else {
            CaptureBus.setStatus("屏幕截屏已禁用")
        }
        if (enableMicrophone) {
            startAudioRecording()
            CaptureBus.setStatus("麦克风已启动，字幕等待服务端回推")
        } else {
            CaptureBus.setStatus("麦克风录音已禁用")
        }
        startTimer()
        uploaderJob = scope.launch { uploadLoop() }

        scope.launch {
            try {
                api.startCourse(
                    StartCourseRequest(
                        courseId = courseId,
                        title = title,
                        screenEnabled = enableScreen,
                        microphoneEnabled = enableMicrophone,
                        llmBaseUrl = prefs.llmBaseUrl.ifBlank { null },
                        llmApiKey = prefs.llmApiKey.ifBlank { null },
                        llmModel = prefs.llmModel.ifBlank { null },
                    )
                )
                runCatching { repository.saveDraft(courseId, title, 0, "recording") }
                CaptureBus.setStatus("已连接局域网服务")
            } catch (error: Exception) {
                CaptureBus.setStatus("服务暂不可达，数据仍会本地排队上传：${error.message}")
                runCatching { repository.saveDraft(courseId, title, 0, "recording") }
            }
            startWebSocketLoop()
        }
    }

    private fun startAudioRecording() {
        if (ContextCompat.checkSelfPermission(this, android.Manifest.permission.RECORD_AUDIO) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            CaptureBus.setStatus("缺少麦克风权限，只进行屏幕截屏")
            return
        }

        try {
            val minBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            audioRecord = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(maxOf(minBuffer, 32768))
                .build()
            audioRecord?.startRecording()
            if (audioRecord?.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                val state = audioRecord?.state
                CaptureBus.setStatus("麦克风启动失败：未进入录音状态（state=$state）")
                return
            }

            audioThread = Thread {
                val buffer = ShortArray(2048)
                val dsp = AudioDsp(audioRecord!!.audioSessionId)
                CaptureBus.setStatus("麦克风已启动，音频处理：${dsp.effectsDescription}")

                var totalSamples = 0L
                var samplesInSegment = 0
                var readFailures = 0
                var levelReportCountdown = 50
                var segmentIndex = 0
                var activeSpeechSamples = 0
                var samplesSinceSpeech = 0
                var encoder: AacAdtsEncoder? = null
                var currentAudioFile: File? = null

                try {
                    while (!stopping.get()) {
                        val count = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                        if (count <= 0) {
                            readFailures += 1
                            if (readFailures == 1 || readFailures % 50 == 0) {
                                CaptureBus.setStatus("麦克风读取失败 count=$count（连续第 $readFailures 次）")
                            }
                            continue
                        }
                        readFailures = 0
                        levelReportCountdown -= 1
                        if (levelReportCountdown <= 0) {
                            levelReportCountdown = 50
                            val peak = buffer.take(count).maxOfOrNull { kotlin.math.abs(it.toInt()) } ?: 0
                            CaptureBus.setStatus("输入电平：peak=$peak（静音即系统未送数据）")
                        }

                        val hasSpeech = dsp.process(buffer, count)
                        val chunkStartMs = totalSamples * 1000L / SAMPLE_RATE
                        if (encoder == null) {
                            val file = File(
                                sessionDir,
                                "audio_%05d_%010d.aac".format(segmentIndex, chunkStartMs),
                            )
                            encoder = AacAdtsEncoder(file, SAMPLE_RATE)
                            activeAudioFile = file
                            currentAudioFile = file
                            CaptureBus.incrementPending()
                        }

                        if (encoder != null) {
                            encoder.writePcm(buffer, count)
                            samplesInSegment += count
                            if (hasSpeech) {
                                activeSpeechSamples += count
                                samplesSinceSpeech = 0
                            } else {
                                samplesSinceSpeech += count
                            }

                            val maxDurationReached = samplesInSegment >= MAX_SEGMENT_SAMPLES
                            val silenceReached = samplesSinceSpeech >= SILENCE_CLOSE_SAMPLES &&
                                activeSpeechSamples >= MIN_ACTIVE_SPEECH_SAMPLES
                            val quietSegmentReached = samplesInSegment >= QUIET_SEGMENT_SAMPLES &&
                                activeSpeechSamples < MIN_ACTIVE_SPEECH_SAMPLES
                            if (maxDurationReached || silenceReached || quietSegmentReached) {
                                closeSegment(
                                    encoder = encoder,
                                    file = currentAudioFile,
                                    segmentSamples = samplesInSegment,
                                    speechSamples = activeSpeechSamples,
                                )
                                encoder = null
                                currentAudioFile = null
                                activeAudioFile = null
                                segmentIndex += 1
                                samplesInSegment = 0
                                activeSpeechSamples = 0
                                samplesSinceSpeech = 0
                            }
                        }
                        totalSamples += count
                    }
                } catch (error: Exception) {
                    Log.e(logTag, "音频线程异常", error)
                    CaptureBus.setStatus("录音线程异常：${error.message}")
                } finally {
                    runCatching { encoder?.close() }
                    activeAudioFile = null
                    dsp.release()
                }
            }.apply { start() }
        } catch (error: Exception) {
            CaptureBus.setStatus("麦克风启动失败：${error.message}")
        }
    }

    private fun closeSegment(
        encoder: AacAdtsEncoder,
        file: File?,
        segmentSamples: Int,
        speechSamples: Int,
    ) {
        encoder.close()
        if (file == null) return
        if (segmentSamples < MIN_SEGMENT_SAMPLES || speechSamples < MIN_SPEECH_SAMPLES) {
            file.delete()
            CaptureBus.decrementPending()
        }
    }

    private fun startScreenCapture(resultCode: Int, data: Intent) {
        val projectionManager = getSystemService(MediaProjectionManager::class.java)
        val projection = try {
            projectionManager.getMediaProjection(resultCode, data)
        } catch (error: Exception) {
            CaptureBus.setStatus("MediaProjection 初始化失败：${error.message}")
            return
        }

        val windowManager = getSystemService(WindowManager::class.java)
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.getRealMetrics(metrics)
        var width = metrics.widthPixels
        var height = metrics.heightPixels
        val maxSide = maxOf(width, height)
        if (maxSide > MAX_SCREEN_SIDE) {
            val scale = MAX_SCREEN_SIDE.toFloat() / maxSide
            width = (width * scale).toInt().coerceAtLeast(320)
            height = (height * scale).toInt().coerceAtLeast(320)
        }

        val thread = HandlerThread("keji-screen-capture").apply { start() }
        screenThread = thread
        screenHandler = Handler(thread.looper)
        val reader = android.media.ImageReader.newInstance(
            width,
            height,
            PixelFormat.RGBA_8888,
            2,
        )
        imageReader = reader

        mediaProjection = projection
        projection.registerCallback(
            object : MediaProjection.Callback() {
                override fun onStop() {
                    if (!stopping.get()) {
                        CaptureBus.setStatus("屏幕录制已停止")
                        stopCapture()
                    }
                }
            },
            screenHandler,
        )
        try {
            virtualDisplay = projection.createVirtualDisplay(
                "keji-screen",
                width,
                height,
                metrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.surface,
                null,
                screenHandler,
            )
        } catch (error: Exception) {
            CaptureBus.setStatus("录屏启动失败：${error.message}")
            return
        }

        val poll = object : Runnable {
            override fun run() {
                if (stopping.get() || screenHandler == null || imageReader !== reader) return
                var image: android.media.Image? = null
                try {
                    image = reader.acquireLatestImage()
                        ?: return@run
                    val now = System.currentTimeMillis()
                    saveScreenImage(image, now - sessionStartMs)
                } catch (error: Exception) {
                    CaptureBus.setStatus("截屏保存失败：${error.message}")
                } finally {
                    image?.close()
                }
                if (!stopping.get() && screenHandler !== null) {
                    screenHandler?.postDelayed(this, (prefs.screenIntervalSec * 1000L).coerceAtLeast(1000L))
                }
            }
        }
        screenPollRunnable = poll
        screenHandler?.postDelayed(poll, 500L)
        CaptureBus.setStatus("截屏采集已启动，每隔 ${prefs.screenIntervalSec} 秒抓取一次")
    }

    private fun saveScreenImage(image: android.media.Image, timestampMs: Long) {
        val plane = image.planes.first()
        val buffer = plane.buffer
        buffer.rewind()
        val rowStride = plane.rowStride
        val stridePixels = rowStride / 4
        val fullBitmap = Bitmap.createBitmap(stridePixels, image.height, Bitmap.Config.ARGB_8888)
        fullBitmap.copyPixelsFromBuffer(buffer)
        val cropped = if (stridePixels == image.width) {
            fullBitmap
        } else {
            Bitmap.createBitmap(fullBitmap, 0, 0, image.width, image.height).also {
                if (it != fullBitmap) fullBitmap.recycle()
            }
        }

        val scale = MAX_SCREEN_SIDE.toFloat() / maxOf(cropped.width, cropped.height)
        val outputBitmap = if (scale < 1f) {
            Bitmap.createScaledBitmap(
                cropped,
                (cropped.width * scale).toInt().coerceAtLeast(320),
                (cropped.height * scale).toInt().coerceAtLeast(320),
                true,
            )
        } else {
            cropped
        }

        val jpegBytes = java.io.ByteArrayOutputStream().use { stream ->
            outputBitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, stream)
            stream.toByteArray()
        }
        if (jpegBytes.contentEquals(lastJpegBytes)) {
            if (outputBitmap != cropped) outputBitmap.recycle()
            cropped.recycle()
            return
        }
        lastJpegBytes = jpegBytes
        pendingScreenRunnable?.let { screenHandler?.removeCallbacks(it) }
        val file = File(sessionDir, "screen_%05d_%010d.jpg".format(screenSequence++, timestampMs))
        pendingScreenFile = file
        val runnable = Runnable {
            if (pendingScreenFile != file) return@Runnable
            pendingScreenFile = null
            pendingScreenRunnable = null
            file.writeBytes(jpegBytes)
            CaptureBus.incrementPending()
        }
        pendingScreenRunnable = runnable
        if (outputBitmap != cropped) outputBitmap.recycle()
        cropped.recycle()
        screenHandler?.postDelayed(runnable, STABLE_SCREEN_DELAY_MS)
    }

    private suspend fun uploadLoop() {
        while (scope.isActive) {
            val dir = sessionDir
            val files = dir?.listFiles { item ->
                item.isFile && !item.name.endsWith(".done") &&
                    (item.name.startsWith("audio_") || item.name.startsWith("screen_"))
            }?.sortedBy { it.name }.orEmpty()

            files.forEach { file ->
                if (file.absolutePath == activeAudioFile?.absolutePath) return@forEach
                val offset = file.nameWithoutExtension.substringAfterLast('_').toLongOrNull() ?: 0L
                try {
                    when {
                        file.name.startsWith("audio_") -> api.uploadAudio(courseId, file, offset, realtime = true)
                        file.name.startsWith("screen_") -> api.uploadScreen(courseId, file, offset)
                    }
                    if (!file.delete()) {
                        file.renameTo(File(file.parentFile, file.name + ".done"))
                    }
                    CaptureBus.decrementPending()
                } catch (_: Exception) {
                    runCatching {
                        LocalCacheStore.enforceQuota(
                            applicationContext,
                            prefs.localQuotaMb * 1024L * 1024L,
                            activeAudioFile,
                        )
                    }
                    return@forEach
                }
            }
            delay(2000)
        }
    }

    private fun startTimer() {
        timerJob = scope.launch {
            var elapsed = 0L
            while (scope.isActive && !stopping.get()) {
                delay(1000)
                elapsed += 1000
                CaptureBus.update { it.copy(elapsedMs = elapsed) }
                val minute = (elapsed / 60000).toInt()
                if (minute != notificationMinute) {
                    notificationMinute = minute
                    getSystemService(NotificationManager::class.java).notify(
                        NOTIFICATION_ID,
                        buildNotification(elapsed),
                    )
                }
            }
        }
    }

    private fun startWebSocketLoop() {
        webSocketJob = scope.launch {
            var attempt = 0
            while (scope.isActive && !stopping.get()) {
                val connected = connectWebSocketOnce()
                if (connected) {
                    attempt = 0
                    Log.i(logTag, "字幕 WebSocket 已连接")
                    CaptureBus.setStatus("字幕通道已连接")
                } else {
                    attempt += 1
                    val delayMs = minOf(30_000L, 2000L * (1L shl minOf(attempt, 4)))
                    Log.w(logTag, "字幕 WebSocket 重试：第 $attempt 次")
                    CaptureBus.setStatus("字幕连接断开，${delayMs / 1000} 秒后自动重试")
                    delay(delayMs)
                }
            }
        }
    }

    private suspend fun connectWebSocketOnce(): Boolean {
        val handshake = kotlinx.coroutines.CompletableDeferred<Boolean>()
        val closed = kotlinx.coroutines.CompletableDeferred<Unit>()

        webSocket = try {
            api.connectWebSocket(courseId, object : okhttp3.WebSocketListener() {
                override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                    handshake.complete(true)
                }

                override fun onMessage(webSocket: okhttp3.WebSocket, text: String) {
                    scope.launch(Dispatchers.Default) {
                        try {
                            val event = json.decodeFromString<WebSocketEvent>(text)
                            event.subtitle?.let { subtitle ->
                                if (subtitle.confirmed) {
                                    CaptureBus.addConfirmedSubtitle(subtitle)
                                } else {
                                    CaptureBus.addSubtitle(subtitle)
                                }
                            }
                            event.timeline?.let(CaptureBus::addTimeline)
                            event.status?.let { status ->
                                CaptureBus.setStatus("$status ${event.message.orEmpty()}")
                            }
                        } catch (_: Exception) {
                        }
                    }
                }

                override fun onClosed(webSocket: okhttp3.WebSocket, code: Int, reason: String) {
                    handshake.complete(true)
                    closed.complete(Unit)
                }

                override fun onFailure(webSocket: okhttp3.WebSocket, t: Throwable, response: okhttp3.Response?) {
                    handshake.complete(false)
                    closed.complete(Unit)
                    CaptureBus.setStatus("字幕连接失败：${t.message}")
                }
            })
        } catch (error: Exception) {
            CaptureBus.setStatus("字幕 WebSocket 创建失败：${error.message}")
            return false
        }

        val opened = runCatching {
            kotlinx.coroutines.withTimeoutOrNull(10_000) { handshake.await() }
        }.getOrNull() == true

        if (!opened) {
            webSocket?.cancel()
            return false
        }

        closed.await()
        return true
    }

    private fun stopCapture() {
        if (stopping.getAndSet(true)) return
        val effectiveCourseId = courseId.ifBlank { AppPreferences(this).activeCourseId }
        CaptureBus.setStatus("正在结束采集并上传剩余数据")
        runCatching { audioRecord?.stop() }
        runCatching { audioRecord?.release() }
        audioRecord = null
        audioThread?.interrupt()
        val stoppingThread = audioThread
        audioThread = null
        runCatching { webSocket?.close(1000, "stop") }
        webSocket = null
        webSocketJob?.cancel()
        webSocketJob = null

        scope.launch {
            // 先等音频线程完成收尾（flush + 释放识别器），再继续清理，避免 native 竞态崩溃。
            if (stoppingThread != null) {
                runCatching { stoppingThread.join(10_000) }
            }
            var remainingPending = true
            var waited = 0
            while (waited < 12_000) {
                val pending = sessionDir?.listFiles { file ->
                    file.isFile && !file.name.endsWith(".done") &&
                        (file.name.startsWith("audio_") || file.name.startsWith("screen_"))
                }?.size ?: 0
                if (pending == 0) {
                    remainingPending = false
                    break
                }
                delay(500)
                waited += 500
            }

            val duration = CaptureBus.uiState.value.elapsedMs
                .takeIf { it > 0 }
                ?: (System.currentTimeMillis() - sessionStartMs)
                .takeIf { it > 0 }
                ?: 0L
            runCatching {
                val state = CaptureBus.uiState.value
                val transcript = state.subtitles.joinToString("\n") { line ->
                    "[${CourseRepository.formatTime(line.startMs)}] ${line.text}"
                }
                repository.saveDraft(
                    courseId = effectiveCourseId,
                    title = state.title.ifBlank { "未命名课程" },
                    durationMs = duration,
                    status = "completed",
                    timeline = state.timeline,
                    transcript = transcript,
                )
            }
            val finished = try {
                api.finishCourse(effectiveCourseId, duration)
                CaptureBus.stop("已结束，课后重转写任务已提交")
                true
            } catch (error: Exception) {
                CaptureBus.stop("已结束，但课后任务提交失败：${error.message}")
                false
            }

            releaseScreenCapture()
            uploaderJob?.cancel()
            timerJob?.cancel()
            if (finished && !remainingPending) {
                LocalCacheStore.deletePending(applicationContext, effectiveCourseId)
            } else {
                LocalCacheStore.enforceQuota(
                    applicationContext,
                    prefs.localQuotaMb * 1024L * 1024L,
                )
                PendingUploadWorker.schedule(applicationContext)
            }
            runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
            stopSelf()
        }
    }

    private fun releaseScreenCapture() {
        screenPollRunnable?.let { screenHandler?.removeCallbacks(it) }
        screenPollRunnable = null
        pendingScreenRunnable?.let { screenHandler?.removeCallbacks(it) }
        pendingScreenRunnable = null
        pendingScreenFile = null
        runCatching { virtualDisplay?.release() }
        runCatching { imageReader?.close() }
        runCatching { mediaProjection?.stop() }
        virtualDisplay = null
        imageReader = null
        mediaProjection = null
        screenHandler = null
        screenThread?.quitSafely()
        screenThread = null
    }

    override fun onDestroy() {
        val abnormalStop = !stopping.get()
        stopping.set(true)
        releaseScreenCapture()
        runCatching { audioRecord?.stop() }
        runCatching { audioRecord?.release() }
        audioRecord = null
        if (abnormalStop && courseId.isNotEmpty()) {
            Thread {
                runCatching {
                    kotlinx.coroutines.runBlocking { api.finishCourse(courseId, System.currentTimeMillis() - sessionStartMs) }
                }
            }.start()
        }
        scope.cancel()
        super.onDestroy()
    }

    private fun buildNotification(elapsedMs: Long): Notification {
        val minutes = elapsedMs / 60000
        val seconds = (elapsedMs / 1000) % 60
        val text = if (elapsedMs <= 0) "正在采集课堂内容" else "已采集 %d:%02d".format(minutes, seconds)
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("课迹正在运行")
            .setContentText(text)
            .setOngoing(true)
            .also { builder ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
                }
            }
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "keji_capture"
        private const val NOTIFICATION_ID = 1001
        private const val SAMPLE_RATE = 16000
        private const val SEGMENT_SEC = 20
        private const val MAX_SCREEN_SIDE = 1600
        private const val JPEG_QUALITY = 68
        private const val MAX_SEGMENT_SAMPLES = SAMPLE_RATE.toLong() * 30
        private const val SILENCE_CLOSE_SAMPLES = SAMPLE_RATE.toLong()
        private const val MIN_SEGMENT_SAMPLES = SAMPLE_RATE.toLong() * 3
        private const val MIN_SPEECH_SAMPLES = SAMPLE_RATE.toLong() * 2
        private const val MIN_ACTIVE_SPEECH_SAMPLES = SAMPLE_RATE.toLong() / 3
        private const val QUIET_SEGMENT_SAMPLES = SAMPLE_RATE.toLong() * 2
        private const val STABLE_SCREEN_DELAY_MS = 1500L

        const val ACTION_START = "com.keji.classroomnote.START"
        const val ACTION_STOP = "com.keji.classroomnote.STOP"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_PROJECTION_DATA = "projection_data"
        const val EXTRA_COURSE_ID = "course_id"
        const val EXTRA_TITLE = "title"
        const val EXTRA_ENABLE_SCREEN = "enable_screen"
        const val EXTRA_ENABLE_MICROPHONE = "enable_microphone"

        @Volatile
        private var screenSequence = 0

        fun start(
            context: Context,
            title: String,
            courseId: String,
            resultCode: Int = 0,
            data: Intent? = null,
            enableScreen: Boolean = true,
            enableMicrophone: Boolean = true,
        ) {
            val intent = Intent(context, CaptureService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_TITLE, title)
                putExtra(EXTRA_COURSE_ID, courseId)
                putExtra(EXTRA_RESULT_CODE, resultCode)
                if (data != null) putExtra(EXTRA_PROJECTION_DATA, data)
                putExtra(EXTRA_ENABLE_SCREEN, enableScreen)
                putExtra(EXTRA_ENABLE_MICROPHONE, enableMicrophone)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, CaptureService::class.java).apply {
                    action = ACTION_STOP
                },
            )
        }

    }
}
