package com.keji.classroomnote.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import androidx.recyclerview.widget.RecyclerView
import com.keji.classroomnote.databinding.ItemCourseBinding
import com.keji.classroomnote.net.CourseListItem
import com.keji.classroomnote.store.CourseRepository

class CoursesAdapter(
    private val onClick: (CourseListItem) -> Unit,
    private val onDelete: (CourseListItem) -> Unit = {},
) : RecyclerView.Adapter<CoursesAdapter.Holder>() {
    private val courses = mutableListOf<CourseListItem>()

    fun submit(values: List<CourseListItem>) {
        courses.clear()
        courses.addAll(values)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val binding = ItemCourseBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return Holder(binding, courses, onClick, onDelete)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val course = courses[position]
        holder.binding.courseTitle.text = course.title
        val createdAt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
            .format(Date(course.createdAt))
        holder.binding.courseMeta.text =
            "$createdAt · 时长 ${CourseRepository.formatTime(course.durationMs)} · ${statusText(course.status)}"
    }

    override fun getItemCount(): Int = courses.size

    class Holder(
        val binding: ItemCourseBinding,
        private val courses: List<CourseListItem>,
        onClick: (CourseListItem) -> Unit,
        onDelete: (CourseListItem) -> Unit,
    ) : RecyclerView.ViewHolder(binding.root) {
        init {
            binding.root.setOnClickListener {
                val position = bindingAdapterPosition
                if (position != RecyclerView.NO_POSITION) {
                    onClick(courses[position])
                }
            }
            binding.deleteCourseButton.setOnClickListener {
                val position = bindingAdapterPosition
                if (position != RecyclerView.NO_POSITION) {
                    onDelete(courses[position])
                }
            }
        }
    }

    private fun statusText(status: String): String = when (status) {
        "recording" -> "采集中"
        "queued", "transcribing" -> "转写中"
        "transcribed" -> "转写完成"
        "transcription_failed" -> "转写失败"
        "generating" -> "生成中"
        "completed" -> "已完成"
        "generation_failed" -> "生成失败"
        "partial_success" -> "部分成功"
        else -> status
    }
}
