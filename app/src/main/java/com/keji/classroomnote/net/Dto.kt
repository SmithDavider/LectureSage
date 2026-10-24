package com.keji.classroomnote.net

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class StartCourseRequest(
    @SerialName("course_id") val courseId: String,
    @SerialName("title") val title: String,
    @SerialName("source") val source: String = "android",
    @SerialName("screen_enabled") val screenEnabled: Boolean? = null,
    @SerialName("microphone_enabled") val microphoneEnabled: Boolean? = null,
    @SerialName("llm_base_url") val llmBaseUrl: String? = null,
    @SerialName("llm_api_key") val llmApiKey: String? = null,
    @SerialName("llm_model") val llmModel: String? = null,
)

@Serializable
data class StartCourseResponse(
    @SerialName("course_id") val courseId: String,
    @SerialName("status") val status: String = "recording",
)

@Serializable
data class FinishCourseRequest(
    @SerialName("duration_ms") val durationMs: Long,
)

@Serializable
data class GenerateRequest(
    @SerialName("model") val model: String? = null,
    @SerialName("detail_level") val detailLevel: String = "very_detailed",
)

@Serializable
data class ApiTask(
    @SerialName("task_id") val taskId: Long,
    @SerialName("status") val status: String,
    @SerialName("message") val message: String? = null,
)

@Serializable
data class ChangePasswordRequest(
    @SerialName("old_password") val oldPassword: String,
    @SerialName("new_password") val newPassword: String,
)

@Serializable
data class ChangePasswordResponse(
    @SerialName("status") val status: String,
)

@Serializable
data class SubtitleLine(
    @SerialName("id") val id: String = "",
    @SerialName("start_ms") val startMs: Long,
    @SerialName("end_ms") val endMs: Long = startMs,
    @SerialName("text") val text: String,
    @SerialName("speaker") val speaker: String? = null,
    @SerialName("page") val page: Int? = null,
    @SerialName("page_title") val pageTitle: String? = null,
    @SerialName("confirmed") val confirmed: Boolean = false,
    @SerialName("confidence") val confidence: Float? = null,
    @SerialName("review_state") val reviewState: String? = null,
)

@Serializable
data class TimelinePoint(
    @SerialName("timestamp_ms") val timestampMs: Long,
    @SerialName("page") val page: Int? = null,
    @SerialName("title") val title: String? = null,
    @SerialName("hash") val hash: String? = null,
)

@Serializable
data class CourseListItem(
    @SerialName("course_id") val courseId: String,
    @SerialName("title") val title: String,
    @SerialName("created_at") val createdAt: Long,
    @SerialName("duration_ms") val durationMs: Long = 0,
    @SerialName("status") val status: String = "unknown",
)

@Serializable
data class CourseSection(
    @SerialName("section_id") val sectionId: String,
    @SerialName("title") val title: String,
    @SerialName("start_ms") val startMs: Long,
    @SerialName("end_ms") val endMs: Long = 0,
    @SerialName("page") val page: Int? = null,
    @SerialName("summary") val summary: String = "",
)

@Serializable
data class MindMapNode(
    @SerialName("node_id") val nodeId: String,
    @SerialName("text") var text: String,
    @SerialName("children") val children: MutableList<MindMapNode> = mutableListOf(),
)

@Serializable
data class CourseDetail(
    @SerialName("course_id") val courseId: String,
    @SerialName("title") val title: String,
    @SerialName("status") val status: String,
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("duration_ms") val durationMs: Long = 0,
    @SerialName("summary") val summary: String = "",
    @SerialName("transcript") val transcript: String = "",
    @SerialName("timeline") val timeline: List<TimelinePoint> = emptyList(),
    @SerialName("sections") val sections: List<CourseSection> = emptyList(),
    @SerialName("mindmap") val mindmap: List<MindMapNode> = emptyList(),
)

@Serializable
data class UpdateCourseRequest(
    @SerialName("title") val title: String,
    @SerialName("summary") val summary: String,
    @SerialName("sections") val sections: List<CourseSection>,
    @SerialName("mindmap") val mindmap: List<MindMapNode>,
)

@Serializable
data class WebSocketEvent(
    @SerialName("type") val type: String,
    @SerialName("subtitle") val subtitle: SubtitleLine? = null,
    @SerialName("timeline") val timeline: TimelinePoint? = null,
    @SerialName("message") val message: String? = null,
    @SerialName("status") val status: String? = null,
)

@Serializable
data class UpdateCheckResponse(
    @SerialName("has_update") val hasUpdate: Boolean = false,
    @SerialName("version_code") val versionCode: Int = 0,
    @SerialName("version_name") val versionName: String = "",
    @SerialName("download_url") val downloadUrl: String = "",
    @SerialName("notes") val notes: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    @SerialName("sha256") val sha256: String = "",
)
