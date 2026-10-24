package com.keji.classroomnote.store

import android.content.Context
import java.io.File

object LocalCacheStore {
    private const val PENDING_SUFFIX = ".done"

    data class PendingFile(
        val file: File,
        val courseId: String,
        val kind: String,
        val sizeBytes: Long,
    )

    fun sessionsRoot(context: Context): File =
        File(context.getExternalFilesDir(null), "sessions")

    fun pendingFiles(context: Context, courseId: String? = null): List<File> {
        val root = sessionsRoot(context)
        return root.listFiles()
            ?.filter { dir -> dir.isDirectory && (courseId == null || dir.name == courseId) }
            ?.flatMap { dir ->
                dir.listFiles()
                    ?.filter { file ->
                        file.isFile &&
                            !file.name.endsWith(PENDING_SUFFIX) &&
                            (file.name.startsWith("audio_") || file.name.startsWith("screen_")) &&
                            !file.name.endsWith(".bin")
                    }
                    .orEmpty()
            }
            .orEmpty()
    }

    fun pendingBytes(context: Context, courseId: String? = null): Long =
        pendingFiles(context, courseId).sumOf { it.length() }

    fun pendingFileInfos(context: Context): List<PendingFile> {
        val root = sessionsRoot(context)
        return root.listFiles()
            ?.filter { dir -> dir.isDirectory }
            ?.sortedBy { it.name }
            ?.flatMap { dir ->
                pendingFiles(context, dir.name).map { file ->
                    PendingFile(
                        file = file,
                        courseId = dir.name,
                        kind = if (file.name.startsWith("screen_")) "图片" else "音频",
                        sizeBytes = file.length(),
                    )
                }
            }
            ?.sortedByDescending { it.file.lastModified() }
            .orEmpty()
    }

    fun enforceQuota(context: Context, maxBytes: Long, protectedFile: File? = null) {
        if (maxBytes <= 0L) return
        val protectedPath = protectedFile?.absolutePath
        val files = pendingFiles(context)
            .filter { it.absolutePath != protectedPath }
            .sortedBy { it.lastModified() }
        var total = files.sumOf { it.length() }

        for (file in files) {
            if (total <= maxBytes) break
            val size = file.length()
            if (file.delete()) total -= size
        }

        cleanupEmptySessions(context)
    }

    fun deletePending(context: Context, courseId: String) {
        pendingFiles(context, courseId).forEach { file -> runCatching { file.delete() } }
        cleanupEmptySessions(context)
    }

    fun deleteFile(file: File) {
        runCatching { file.delete() }
        cleanupFileParents(file)
    }

    fun cleanupEmptySessions(context: Context) {
        sessionsRoot(context).listFiles()?.filter { file -> file.isDirectory }?.forEach { dir ->
            val hasData = dir.listFiles { file -> file.isFile }?.isNotEmpty() == true
            if (!hasData) dir.delete()
        }
    }

    private fun cleanupFileParents(file: File) {
        val parent = file.parentFile ?: return
        val dataFile = parent.listFiles { item ->
            item.isFile && !item.name.endsWith(PENDING_SUFFIX)
        }.isNullOrEmpty()
        if (dataFile) runCatching { parent.delete() }
    }
}
