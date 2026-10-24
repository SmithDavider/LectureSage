package com.keji.classroomnote.net

import android.content.Context
import com.keji.classroomnote.store.AppPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.Response
import okhttp3.RequestBody
import okio.BufferedSink
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.util.Base64
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, val body: String) : IOException(body)

enum class ConnectionMode { CONNECTED, UNAVAILABLE }

class ApiClient(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = AppPreferences(appContext)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
    private val downloadClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .writeTimeout(5, TimeUnit.MINUTES)
        .build()
    suspend fun startCourse(request: StartCourseRequest): StartCourseResponse =
        postJson("/api/v1/courses", request)

    suspend fun uploadAudio(
        courseId: String,
        file: File,
        offsetMs: Long,
        realtime: Boolean = false,
    ) {
        uploadFile(
            courseId = courseId,
            path = "/api/v1/courses/$courseId/audio",
            file = file,
            mimeType = audioMimeType(file),
            offsetMs = offsetMs,
            realtime = if (realtime) "1" else "0",
        )
    }

    suspend fun uploadScreen(courseId: String, file: File, timestampMs: Long) {
        uploadFile(
            courseId = courseId,
            path = "/api/v1/courses/$courseId/screens",
            file = file,
            mimeType = "image/jpeg",
            offsetMs = timestampMs,
        )
    }

    suspend fun uploadAudioStream(
        courseId: String,
        fileName: String,
        contentType: String,
        contentLength: Long,
        openStream: () -> InputStream?,
        onProgress: ((Long, Long) -> Unit)? = null,
    ) = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("offset_ms", "0")
            .addFormDataPart(
                "file",
                fileName,
                object : RequestBody() {
                    override fun contentType() = contentType.toMediaType()

                    override fun contentLength() = contentLength

                    override fun writeTo(sink: BufferedSink) {
                        val input = openStream() ?: throw IOException("无法打开所选音频文件")
                        input.use { source ->
                            val buffer = ByteArray(64 * 1024)
                            var written = 0L
                            while (true) {
                                val read = source.read(buffer)
                                if (read < 0) break
                                sink.write(buffer, 0, read)
                                written += read
                                onProgress?.invoke(written, contentLength)
                            }
                        }
                    }
                },
            )
            .build()
        execute(
            Request.Builder().url(url("/api/v1/courses/$courseId/audio")).post(body).authorized().build(),
        )
    }

    suspend fun finishCourse(courseId: String, durationMs: Long): ApiTask =
        postJson<FinishCourseRequest, ApiTask>(
            "/api/v1/courses/$courseId/finish",
            FinishCourseRequest(durationMs),
        )

    suspend fun interruptCourse(courseId: String) {
        execute(
            Request.Builder()
                .url(url("/api/v1/courses/$courseId/interrupt"))
                .post("".toRequestBody(JSON_MEDIA))
                .authorized()
                .build(),
        )
    }

    suspend fun changePassword(
        oldPassword: String,
        newPassword: String,
    ): ChangePasswordResponse =
        postJson<ChangePasswordRequest, ChangePasswordResponse>(
            "/api/v1/me/password",
            ChangePasswordRequest(oldPassword, newPassword),
        )

    suspend fun generate(courseId: String, model: String?): ApiTask =
        postJson<GenerateRequest, ApiTask>(
            "/api/v1/courses/$courseId/generate",
            GenerateRequest(model),
        )

    suspend fun listCourses(): List<CourseListItem> = withContext(Dispatchers.IO) {
        val body = execute(Request.Builder().url(url("/api/v1/courses")).get().authorized().build())
        json.decodeFromString<List<CourseListItem>>(body)
    }

    suspend fun verifyLogin(): Boolean = withContext(Dispatchers.IO) {
        try {
            execute(Request.Builder().url(url("/api/v1/courses")).get().authorized().build())
            true
        } catch (error: ApiException) {
            if (error.code == 401) false else throw error
        }
    }

    suspend fun checkUpdate(): UpdateCheckResponse = withContext(Dispatchers.IO) {
        val body = execute(Request.Builder().url(url("/api/v1/updates/check")).get().authorized().build())
        json.decodeFromString<UpdateCheckResponse>(body)
    }

    suspend fun currentConnectionMode(): ConnectionMode = withContext(Dispatchers.IO) {
        if (probeConfiguredServer()) ConnectionMode.CONNECTED else ConnectionMode.UNAVAILABLE
    }

    fun absoluteUrl(path: String): String = url(path)

    fun download(
        url: String,
        target: File,
        onProgress: ((Long, Long) -> Unit)? = null,
    ) {
        val request = Request.Builder()
            .url(url)
            .get()
            .authorized()
            .build()
        downloadClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw ApiException(response.code, response.body?.string()?.take(200) ?: "")
            }
            val body = response.body ?: throw IOException("下载响应为空")
            val total = body.contentLength()
            var written = 0L
            body.byteStream().use { input ->
                FileOutputStream(target).use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        written += read
                        onProgress?.invoke(written, total)
                    }
                }
            }
            if (total >= 0L && written != total) {
                throw IOException("更新包下载不完整")
            }
        }
    }

    suspend fun getCourse(courseId: String): CourseDetail = withContext(Dispatchers.IO) {
        val body = execute(
                Request.Builder().url(url("/api/v1/courses/$courseId")).get().authorized().build(),
        )
        json.decodeFromString<CourseDetail>(body)
    }

    suspend fun updateCourse(courseId: String, request: UpdateCourseRequest): CourseDetail =
        withContext(Dispatchers.IO) {
            val requestBody = json.encodeToString(request).toRequestBody(JSON_MEDIA)
            val responseBody = execute(
                Request.Builder().url(url("/api/v1/courses/$courseId/content"))
                    .put(requestBody)
                    .authorized()
                    .build(),
            )
            json.decodeFromString<CourseDetail>(responseBody)
        }

    suspend fun deleteCourse(courseId: String): Unit = withContext(Dispatchers.IO) {
        execute(
            Request.Builder()
                .url(url("/api/v1/courses/$courseId"))
                .delete()
                .authorized()
                .build(),
        )
    }

    fun connectWebSocket(courseId: String, listener: WebSocketListener): WebSocket {
        val httpUrl = url("/api/v1/courses/$courseId/stream").toHttpUrl().newBuilder()
            .addQueryParameter("username", prefs.username)
            .addQueryParameter("password", prefs.password)
            .build()
        val wsUrl = httpUrl.toString().replaceFirst("http://", "ws://").replaceFirst("https://", "wss://")
        val request = Request.Builder().url(wsUrl).build()
        return client.newWebSocket(request, listener)
    }

    private suspend fun uploadFile(
        courseId: String,
        path: String,
        file: File,
        mimeType: String,
        offsetMs: Long,
        realtime: String = "0",
    ) = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("offset_ms", offsetMs.toString())
            .addFormDataPart("realtime", realtime)
            .addFormDataPart(
                "file",
                file.name,
                file.asRequestBody(mimeType.toMediaType()),
            )
            .build()
        execute(Request.Builder().url(url(path)).post(body).authorized().build())
    }

    private fun audioMimeType(file: File): String = when (file.extension.lowercase()) {
        "aac" -> "audio/aac"
        "mp3" -> "audio/mpeg"
        "opus" -> "audio/opus"
        "ogg" -> "audio/ogg"
        "m4a", "mp4" -> "audio/mp4"
        "wav" -> "audio/wav"
        else -> "application/octet-stream"
    }

    private suspend inline fun <reified TRequest, reified TResponse> postJson(
        path: String,
        request: TRequest,
    ): TResponse = withContext(Dispatchers.IO) {
        val requestBody = json.encodeToString(request).toRequestBody(JSON_MEDIA)
        val responseBody = execute(
            Request.Builder().url(url(path)).post(requestBody).authorized().build(),
        )
        json.decodeFromString<TResponse>(responseBody)
    }

    private fun url(path: String): String = resolvedBaseUrl() + path

    private fun resolvedBaseUrl(): String {
        val configured = prefs.serverUrl.trim()
        if (configured.isBlank()) throw IOException("请先在登录页填写服务器地址")
        return normalizedBaseUrl(configured)
    }

    private fun probeConfiguredServer(): Boolean {
        if (prefs.serverUrl.isBlank()) return false
        return try {
            val request = Request.Builder()
                .url(normalizedBaseUrl(prefs.serverUrl) + "/api/v1/courses")
                .build()
            client.newCall(request).execute().use { true }
        } catch (_: Exception) {
            false
        }
    }

    private fun normalizedBaseUrl(raw: String): String {
        var value = raw.trim().trimEnd('/')
        if (!value.startsWith("http://") && !value.startsWith("https://")) {
            value = "http://$value"
        }

        val uri = URI(value)
        val defaultPort = if (uri.scheme.equals("https", ignoreCase = true)) 443 else 8000
        val port = if (uri.port == -1) defaultPort else uri.port
        val path = (uri.path ?: "").trimEnd('/')
        val basePath = if (path.endsWith("/api/v1")) path.removeSuffix("/api/v1") else path

        return buildString {
            append(uri.scheme.lowercase())
            append("://")
            append(uri.host)
            if (port != 80 && port != 443) append(":").append(port)
            append(basePath)
        }
    }

    private fun Request.Builder.authorized(): Request.Builder {
        val credential = Base64.getEncoder().encodeToString(
            "${prefs.username}:${prefs.password}".toByteArray(Charsets.UTF_8)
        )
        header("Authorization", "Basic $credential")
        return this
    }

    private fun execute(request: Request): String =
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw ApiException(response.code, text.take(300))
            text
        }

        private companion object {
            private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        }
}
