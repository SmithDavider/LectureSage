package com.keji.classroomnote.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.keji.classroomnote.databinding.ItemSubtitleBinding
import com.keji.classroomnote.net.SubtitleLine
import com.keji.classroomnote.store.CourseRepository

class SubtitlesAdapter : RecyclerView.Adapter<SubtitlesAdapter.Holder>() {
    private val lines = mutableListOf<SubtitleLine>()
    private var lastSnapshot: List<SubtitleLine>? = null

    fun submit(values: List<SubtitleLine>) {
        if (lastSnapshot == values) return
        lastSnapshot = values
        lines.clear()
        lines.addAll(values)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val binding = ItemSubtitleBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return Holder(binding)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val line = lines[position]
        val page = line.page?.let { " · 第${it}页" }.orEmpty()
        val pageTitle = line.pageTitle?.takeIf { it.isNotBlank() }?.let { " · $it" }.orEmpty()
        val speaker = line.speaker?.let { " · $it" }.orEmpty()
        val state = when (line.reviewState) {
            "partial" -> "实时草稿"
            "pending" -> "待复核"
            "needs_correction" -> "初检需修正"
            "checked_ok" -> "已初检"
            else -> if (line.confirmed) "已确认" else "待复核"
        }
        val transparency = when (line.reviewState) {
            "partial" -> 0.65f
            "pending" -> 0.45f
            else -> 0f
        }
        holder.binding.metaText.text =
            "${CourseRepository.formatTime(line.startMs)}$page$pageTitle$speaker · $state"
        holder.binding.subtitleText.text = line.text
        holder.binding.subtitleText.alpha = 1f - transparency
        holder.binding.metaText.alpha = 1f - (transparency * 0.5f)
    }

    override fun getItemCount(): Int = lines.size

    class Holder(val binding: ItemSubtitleBinding) : RecyclerView.ViewHolder(binding.root)
}
