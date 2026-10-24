package com.keji.classroomnote.capture

import com.keji.classroomnote.net.SubtitleLine
import com.keji.classroomnote.net.TimelinePoint
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.concurrent.atomic.AtomicInteger

data class CaptureUiState(
    val running: Boolean = false,
    val courseId: String = "",
    val title: String = "",
    val elapsedMs: Long = 0,
    val status: String = "未开始",
    val pendingUploads: Int = 0,
    val subtitles: List<SubtitleLine> = emptyList(),
    val timeline: List<TimelinePoint> = emptyList(),
)

private fun SubtitleLine.withCurrentPage(current: CaptureUiState): SubtitleLine {
    val pagePoint = current.timeline.lastOrNull { it.timestampMs <= startMs } ?: return this
    val currentTitle = pagePoint.title?.takeIf { it.isNotBlank() }
    return copy(
        page = page.takeIf { it != null } ?: pagePoint.page,
        pageTitle = pageTitle?.takeIf { it.isNotBlank() } ?: currentTitle,
    )
}

object CaptureBus {
    private val pending = AtomicInteger(0)
    private val state = MutableStateFlow(CaptureUiState())
    val uiState: StateFlow<CaptureUiState> = state.asStateFlow()

    fun start(courseId: String, title: String) {
        pending.set(0)
        state.value = CaptureUiState(
            running = true,
            courseId = courseId,
            title = title,
            status = "正在初始化采集",
        )
    }

    fun update(transform: (CaptureUiState) -> CaptureUiState) {
        state.value = transform(state.value)
    }

    fun setStatus(status: String) {
        update { it.copy(status = status) }
    }

    fun addSubtitle(line: SubtitleLine) {
        update { current ->
            val enriched = line.withCurrentPage(current)
            val existing = current.subtitles.firstOrNull { it.id == enriched.id }
            if (existing != null) {
                val merged = if (enriched.confirmed && !existing.confirmed) {
                    enriched
                } else {
                    existing
                }
                return@update current.copy(
                    subtitles = (current.subtitles - existing + merged)
                        .sortedBy { item -> item.startMs }
                        .takeLast(400),
                )
            }
            current.copy(
                subtitles = (current.subtitles + enriched)
                    .sortedBy { item -> item.startMs }
                    .takeLast(400),
            )
        }
    }

    fun addConfirmedSubtitle(line: SubtitleLine) {
        update { current ->
            val enriched = line.withCurrentPage(current)
            val existing = current.subtitles.firstOrNull { it.id == enriched.id }
            if (existing != null) {
                return@update current.copy(
                    subtitles = (current.subtitles - existing + enriched)
                        .sortedBy { item -> item.startMs }
                        .takeLast(400),
                )
            }
            current.copy(
                subtitles = (current.subtitles + enriched)
                    .sortedBy { item -> item.startMs }
                    .takeLast(400),
            )
        }
    }

    fun addTimeline(point: TimelinePoint) {
        update { current ->
            current.copy(
                timeline = (current.timeline + point)
                    .sortedBy { item -> item.timestampMs }
                    .takeLast(1000),
            )
        }
    }

    fun incrementPending() {
        pending.incrementAndGet()
        update { it.copy(pendingUploads = pending.get()) }
    }

    fun decrementPending() {
        pending.updateAndGet { value -> (value - 1).coerceAtLeast(0) }
        update { it.copy(pendingUploads = pending.get()) }
    }

    fun stop(status: String) {
        update { it.copy(running = false, status = status) }
    }
}
