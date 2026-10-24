package com.keji.classroomnote.net

import android.content.Context
import com.keji.classroomnote.capture.CaptureBus
import java.io.File

object PendingUploadSync {
    data class UploadSummary(val uploaded: Int, val remaining: Int)

    suspend fun uploadPending(context: Context): UploadSummary {
        val api = ApiClient(context)
        val activeCourseId = CaptureBus.uiState.value.takeIf { it.running }?.courseId
        val root = File(context.getExternalFilesDir(null), "sessions")
        var uploaded = 0
        var remaining = 0

        root.listFiles { file -> file.isDirectory }?.sortedBy { it.name }?.forEach { dir ->
            if (dir.name == activeCourseId) return@forEach
            val files = dir.listFiles { file ->
                file.isFile &&
                    !file.name.endsWith(".done") &&
                    (file.name.startsWith("audio_") || file.name.startsWith("screen_")) &&
                    !file.name.endsWith(".bin") &&
                    file.length() >= 500
            }?.sortedBy { it.name }.orEmpty()

            files.forEach { file ->
                val offset = file.nameWithoutExtension.substringAfterLast('_').toLongOrNull() ?: 0L
                try {
                    when {
                        file.name.startsWith("audio_") -> api.uploadAudio(dir.name, file, offset)
                        file.name.startsWith("screen_") -> api.uploadScreen(dir.name, file, offset)
                    }
                    if (!file.delete()) {
                        file.renameTo(File(dir, file.name + ".done"))
                    }
                    uploaded += 1
                } catch (_: Exception) {
                    remaining += 1
                }
            }

            if (dir.listFiles { file -> file.isFile }?.isEmpty() == true) {
                dir.delete()
            }
        }

        return UploadSummary(uploaded, remaining)
    }
}
