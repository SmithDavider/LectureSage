package com.keji.classroomnote.store

import android.content.Context
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.CourseDetail
import com.keji.classroomnote.net.CourseListItem
import com.keji.classroomnote.net.GenerateRequest
import com.keji.classroomnote.net.MindMapNode
import com.keji.classroomnote.net.UpdateCourseRequest
import kotlinx.serialization.encodeToString
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.io.File
import android.util.Log
import java.util.UUID

class CourseRepository(context: Context) {
    private val appContext = context.applicationContext
    private val api = ApiClient(appContext)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true; prettyPrint = true }
    private val courseDir = File(appContext.filesDir, "courses")

    suspend fun courses(): List<CourseListItem> = api.listCourses()

    suspend fun syncLocalCourseCache() {
        val list = api.listCourses()
        var progress = 0
        list.forEach { item ->
            progress += 1
            try {
                val detail = api.getCourse(item.courseId)
                saveLocal(detail)
            } catch (error: Exception) {
                Log.w("KejiCourses", "课程详情同步失败，已保留本地缓存：${item.courseId}", error)
            }
        }
        saveLocalList(list)
    }

    suspend fun localCourses(): List<CourseListItem> = localSummaries()

    suspend fun localCourse(courseId: String): CourseDetail? = loadLocal(courseId)

    suspend fun course(courseId: String): CourseDetail {
        val local = loadLocal(courseId)
        if (local != null) {
            CoroutineScope(Dispatchers.IO).launch {
                runCatching { api.getCourse(courseId) }.getOrNull()?.let { saveLocal(it) }
            }
            return local
        }
        return try {
            api.getCourse(courseId).also { saveLocal(it) }
        } catch (error: Exception) {
            loadLocal(courseId) ?: throw error
        }
    }

    suspend fun refreshCourse(courseId: String): CourseDetail =
        api.getCourse(courseId).also { saveLocal(it) }

    suspend fun save(course: CourseDetail): CourseDetail {
        saveLocal(course)
        return api.updateCourse(
            courseId = course.courseId,
            request = UpdateCourseRequest(
                title = course.title,
                summary = course.summary,
                sections = course.sections,
                mindmap = course.mindmap,
            ),
        ).also { saveLocal(it) }
    }

    suspend fun saveDraft(
        courseId: String,
        title: String,
        durationMs: Long,
        status: String,
        timeline: List<com.keji.classroomnote.net.TimelinePoint> = emptyList(),
        transcript: String = "",
    ): CourseDetail {
        val detail = CourseDetail(
            courseId = courseId,
            title = title,
            status = status,
            createdAt = System.currentTimeMillis(),
            durationMs = durationMs,
            timeline = timeline,
            transcript = transcript,
        )
        saveLocal(detail)
        upsertLocalListItem(detail)
        return detail
    }

    suspend fun generate(courseId: String, model: String?) =
        api.generate(courseId, model)

    suspend fun deleteCourse(courseId: String) {
        api.deleteCourse(courseId)
        deleteLocal(courseId)
    }

    suspend fun markStaleRecordingCoursesFinished(exceptCourseId: String? = null) {
        courses()
            .filter { it.status == "recording" && it.courseId != exceptCourseId }
            .forEach { course ->
                runCatching {
                    api.finishCourse(course.courseId, course.durationMs)
                }.onFailure {
                    runCatching { api.interruptCourse(course.courseId) }
                }
            }
    }

    suspend fun listOrEmpty(): List<CourseListItem> {
        return try {
            val local = localSummaries()
            if (local.isNotEmpty()) {
                CoroutineScope(Dispatchers.IO).launch {
                    runCatching { courses() }.getOrNull()?.let { saveLocalList(it) }
                }
                local
            } else {
                val remote = courses()
                saveLocalList(remote)
                remote
            }
        } catch (error: Exception) {
            val local = localSummaries()
            if (local.isNotEmpty()) {
                local
            } else {
                Log.e("KejiCourses", "服务端课程列表请求失败，回退本地缓存", error)
                localSummaries()
            }
        }
    }

    private fun loadLocal(courseId: String): CourseDetail? {
        val file = File(courseDir, "$courseId.json")
        if (!file.exists()) return null
        return json.decodeFromString<CourseDetail>(file.readText())
    }

    private fun saveLocal(course: CourseDetail) {
        courseDir.mkdirs()
        File(courseDir, "${course.courseId}.json").writeText(json.encodeToString(course))
    }

    private fun localSummaries(): List<CourseListItem> {
        if (!courseDir.exists()) return emptyList()
        val listFile = File(courseDir, "list.json")
        if (listFile.exists()) {
            return runCatching { json.decodeFromString<List<CourseListItem>>(listFile.readText()) }
                .getOrNull()
                ?.sortedByDescending { it.createdAt }
                .orEmpty()
        }
        return courseDir.listFiles { file -> file.isFile && file.extension == "json" && file.name != "list.json" }
            ?.mapNotNull { file ->
                runCatching { json.decodeFromString<CourseDetail>(file.readText()) }.getOrNull()
            }
            ?.map {
                CourseListItem(
                    courseId = it.courseId,
                    title = it.title,
                    createdAt = it.createdAt,
                    durationMs = it.durationMs,
                    status = it.status,
                )
            }
            ?.sortedByDescending { it.createdAt }
            .orEmpty()
    }

    private fun saveLocalList(list: List<CourseListItem>) {
        courseDir.mkdirs()
        File(courseDir, "list.json").writeText(json.encodeToString(list))
    }

    private fun upsertLocalListItem(course: CourseDetail) {
        val existing = runCatching {
            json.decodeFromString<List<CourseListItem>>(File(courseDir, "list.json").takeIf { it.exists() }?.readText() ?: "[]")
        }.getOrDefault(emptyList())
        val updated = ArrayList<CourseListItem>(existing)
        updated.removeAll { it.courseId == course.courseId }
        updated.add(
            CourseListItem(
                courseId = course.courseId,
                title = course.title,
                createdAt = course.createdAt,
                durationMs = course.durationMs,
                status = course.status,
            )
        )
        saveLocalList(updated.sortedByDescending { it.createdAt })
    }

    private fun deleteLocal(courseId: String) {
        File(courseDir, "$courseId.json").delete()
        val listFile = File(courseDir, "list.json")
        if (listFile.exists()) {
            val updated = runCatching {
                json.decodeFromString<List<CourseListItem>>(listFile.readText())
            }.getOrDefault(emptyList())
                .filterNot { it.courseId == courseId }
            saveLocalList(updated)
        }
    }

    companion object {
        fun newId(): String = UUID.randomUUID().toString()

        fun toMarkdown(course: CourseDetail): String {
            val builder = StringBuilder()
            builder.appendLine("# ${course.title}")
            builder.appendLine()
            if (course.summary.isNotBlank()) {
                builder.appendLine("## 全文详细总结")
                builder.appendLine()
                builder.appendLine(course.summary)
                builder.appendLine()
            }
            if (course.sections.isNotEmpty()) {
                builder.appendLine("## 分节小结")
                builder.appendLine()
                course.sections.forEach { section ->
                    val start = formatTime(section.startMs)
                    builder.appendLine("### ${section.title} [$start]")
                    if (section.page != null) builder.appendLine("页码：${section.page}")
                    builder.appendLine()
                    builder.appendLine(section.summary)
                    builder.appendLine()
                }
            }
            return builder.toString()
        }

        fun toOpml(course: CourseDetail): String {
            val builder = StringBuilder()
            builder.appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
            builder.appendLine("""<opml version="2.0">""")
            builder.appendLine("  <head><title>${course.title}</title></head>")
            builder.appendLine("  <body>")
            if (course.summary.isNotBlank()) {
                builder.appendLine("""    <outline text="全文详细总结" _note="${escapeXml(course.summary)}" />""")
            }
            course.sections.forEach { section ->
                val title = "${section.title} ${formatTime(section.startMs)}"
                builder.appendLine("""    <outline text="${escapeXml(title)}" _note="${escapeXml(section.summary)}" />""")
            }
            course.mindmap.forEach { root ->
                appendOutline(builder, root, depth = 2)
            }
            builder.appendLine("  </body>")
            builder.appendLine("</opml>")
            return builder.toString()
        }

        fun formatTime(ms: Long): String {
            val totalSeconds = ms / 1000
            val hours = totalSeconds / 3600
            val minutes = (totalSeconds % 3600) / 60
            val seconds = totalSeconds % 60
            return "%02d:%02d:%02d".format(hours, minutes, seconds)
        }

        fun formatTranscript(raw: String): String {
            if (raw.isBlank()) return "暂无转写内容"
            val array = runCatching { Json.parseToJsonElement(raw).jsonArray }.getOrNull()
                ?: return raw
            val lines = array.mapNotNull { element ->
                val obj = element as? JsonObject ?: return@mapNotNull null
                val text = obj["text"]?.jsonPrimitive?.content?.trim().orEmpty()
                if (text.isBlank()) return@mapNotNull null
                val startMs = obj["start_ms"]?.jsonPrimitive?.longOrNull
                    ?: obj["start"]?.jsonPrimitive?.doubleOrNull?.let { (it * 1000).toLong() }
                    ?: 0L
                "[${formatTime(startMs)}] $text"
            }
            return if (lines.isEmpty()) "暂无转写内容" else lines.joinToString("\n")
        }

        private fun appendOutline(builder: StringBuilder, node: MindMapNode, depth: Int) {
            val indent = "    ".repeat(depth)
            builder.appendLine("""$indent<outline text="${escapeXml(node.text)}" />""")
            node.children.forEach { child -> appendOutline(builder, child, depth + 1) }
        }

        private fun escapeXml(value: String): String = value
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
    }
}
