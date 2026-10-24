package com.keji.classroomnote.update

import android.content.Context
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import android.provider.Settings
import androidx.core.content.FileProvider
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.UpdateCheckResponse
import java.io.File
import java.security.MessageDigest

class AppUpdater(private val context: Context) {
    private val api by lazy { ApiClient(context.applicationContext) }

    suspend fun check(): UpdateCheckResponse = api.checkUpdate()

    fun installedVersionCode(): Int = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionCode
    } catch (_: PackageManager.NameNotFoundException) {
        0
    }

    fun downloadAndInstall(
        info: UpdateCheckResponse,
        listener: ((Boolean, String?) -> Unit)? = null,
        onProgress: ((Long, Long) -> Unit)? = null,
    ) {
        if (!info.hasUpdate) {
            listener?.invoke(false, "没有可更新的版本")
            return
        }
        if (!context.packageManager.canRequestPackageInstalls()) {
            openInstallPermissionSettings()
            listener?.invoke(false, "请先允许安装未知应用，系统设置页已打开")
            return
        }
        val url = if (info.downloadUrl.startsWith("http://") || info.downloadUrl.startsWith("https://")) {
            info.downloadUrl
        } else {
            api.absoluteUrl(info.downloadUrl)
        }
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        val target = File(dir, "keji-${info.versionCode}.apk")
        Thread {
            try {
                target.delete()
                api.download(url, target, onProgress)
                verifyDownload(target, info)
                openInstaller(target)
                listener?.invoke(true, null)
            } catch (error: Exception) {
                target.delete()
                val detail = buildString {
                    append(error.message?.takeIf { it.isNotBlank() } ?: "网络异常")
                }
                listener?.invoke(false, detail)
            }
        }.start()
    }

    private fun verifyDownload(file: File, info: UpdateCheckResponse) {
        if (file.length() <= 0L) {
            throw IllegalStateException("下载内容为空")
        }
        if (info.sizeBytes > 0L && file.length() != info.sizeBytes) {
            throw IllegalStateException("更新包下载不完整，请重试")
        }
        if (info.sha256.isNotBlank() && !sha256(file).equals(info.sha256, ignoreCase = true)) {
            throw IllegalStateException("更新包校验失败，请重试")
        }
        val archive = context.packageManager.getPackageArchiveInfo(file.absolutePath, 0)
        if (archive == null || archive.packageName != context.packageName) {
            throw IllegalStateException("更新包无法解析，请重试")
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }

    private fun openInstaller(file: File) {
        if (!context.packageManager.canRequestPackageInstalls()) {
            openInstallPermissionSettings()
            throw IllegalStateException("需要允许安装未知应用，系统设置页已打开")
        }
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            clipData = ClipData.newRawUri("apk", uri)
        }
        if (intent.resolveActivity(context.packageManager) == null) {
            throw IllegalStateException("未找到安装器，请先允许安装未知应用")
        }
        try {
            context.startActivity(intent)
        } catch (error: Exception) {
            throw IllegalStateException("无法打开安装界面，请检查系统安装权限：${error.message}", error)
        }
    }

    private fun openInstallPermissionSettings() {
        val intent = Intent(
            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
            Uri.parse("package:${context.packageName}"),
        ).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching {
            context.startActivity(intent)
        }.onFailure {
            val fallback = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching {
                context.startActivity(fallback)
            }
        }
    }
}
