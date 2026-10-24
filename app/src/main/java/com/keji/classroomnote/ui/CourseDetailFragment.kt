package com.keji.classroomnote.ui

import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.keji.classroomnote.R
import com.keji.classroomnote.databinding.FragmentCourseDetailBinding
import com.keji.classroomnote.net.CourseDetail
import com.keji.classroomnote.net.MindMapNode
import com.keji.classroomnote.store.CourseRepository
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class CourseDetailFragment : Fragment() {
    private var _binding: FragmentCourseDetailBinding? = null
    private val binding get() = _binding!!
    private val repository by lazy { CourseRepository(requireContext()) }
    private var course: CourseDetail? = null
    private var loading = false
    private var refreshJob: Job? = null
    private val sectionInputs = mutableMapOf<Int, EditText>()
    private var appContext: Context? = null
    private val mode: String
        get() = arguments?.getString(ARG_MODE) ?: MODE_OVERVIEW

    private val markdownExporter =
        registerForActivityResult(ActivityResultContracts.CreateDocument("text/markdown")) { uri ->
            uri?.let { target -> export(target, { CourseRepository.toMarkdown(it) }, "md") }
        }

    private val opmlExporter =
        registerForActivityResult(ActivityResultContracts.CreateDocument("text/xml")) { uri ->
            uri?.let { target -> export(target, { CourseRepository.toOpml(it) }, "opml") }
        }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentCourseDetailBinding.inflate(inflater, container, false)
        appContext = requireContext().applicationContext
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.backButton.setOnClickListener { parentFragmentManager.popBackStack() }
        binding.refreshButton.setOnClickListener { load() }
        binding.generateButton.setOnClickListener { generate() }
        binding.saveButton.setOnClickListener { save() }
        binding.exportMarkdownButton.setOnClickListener {
            markdownExporter.launch("${course?.title.orEmpty().ifBlank { "课迹" }}.md")
        }
        binding.exportOpmlButton.setOnClickListener {
            opmlExporter.launch("${course?.title.orEmpty().ifBlank { "课迹" }}.opml")
        }

        binding.summaryRow.setOnClickListener { openContent(MODE_SUMMARY) }
        binding.sectionsRow.setOnClickListener { openContent(MODE_SECTIONS) }
        binding.mindmapRow.setOnClickListener { openContent(MODE_MINDMAP) }
        binding.transcriptRow.setOnClickListener { openContent(MODE_TRANSCRIPT) }

        binding.deleteCourseButton.setOnClickListener { deleteCourse() }
        binding.addChildButton.setOnClickListener { addChild() }
        binding.renameButton.setOnClickListener { renameSelected() }
        binding.deleteNodeButton.setOnClickListener { deleteSelected() }
        binding.indentButton.setOnClickListener { indentSelected() }
        binding.outdentButton.setOnClickListener { outdentSelected() }
        binding.moveUpButton.setOnClickListener { moveSelected(-1) }
        binding.moveDownButton.setOnClickListener { moveSelected(1) }

        binding.mindMapView.onSelectionChanged = { node ->
            node?.text
        }
        binding.mindMapView.onDragMove = { _, direction -> moveSelected(direction) }

        load()
    }

    private fun openContent(mode: String) {
        val courseId = arguments?.getString(ARG_COURSE_ID) ?: return
        parentFragmentManager.beginTransaction()
            .replace(R.id.container, newInstance(courseId, mode))
            .addToBackStack(null)
            .commit()
    }

    private fun load() {
        val courseId = arguments?.getString(ARG_COURSE_ID) ?: return
        if (loading) return
        loading = true
        val appContext = this.appContext ?: return
        val safeBinding = _binding ?: run {
            loading = false
            return
        }
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                course = repository.localCourse(courseId)
                val detail = course
                if (safeBinding == _binding) detail?.let(::render)
                if (detail?.status == "generating") startAutoRefresh(courseId)
                val remote = runCatching { repository.refreshCourse(courseId) }.getOrNull()
                if (remote != null) {
                    course = remote
                    if (safeBinding == _binding) render(remote)
                    if (remote.status == "generating") startAutoRefresh(courseId)
                }
            } catch (error: Exception) {
                Toast.makeText(appContext, "加载课程失败：${error.message}", Toast.LENGTH_LONG).show()
            } finally {
                loading = false
            }
        }
    }

    private fun startAutoRefresh(courseId: String) {
        refreshJob?.cancel()
        refreshJob = viewLifecycleOwner.lifecycleScope.launch {
            var attempts = 0
            while (attempts < MAX_POLLS) {
                delay(POLL_INTERVAL_MS)
                attempts += 1
                val detail = runCatching { repository.course(courseId) }.getOrNull() ?: continue
                if (_binding == null) return@launch
                if (detail.status != "generating") {
                    course = detail
                    runCatching { render(detail) }
                        .onFailure {
                            Toast.makeText(appContext, "课程内容刷新失败：${it.message}", Toast.LENGTH_LONG).show()
                        }
                    return@launch
                }
            }
        }
    }

    private fun render(detail: CourseDetail) {
        runCatching {
            binding.courseTitleText.text = if (mode == MODE_OVERVIEW) detail.title else contentTitle(mode)
            binding.courseMetaText.text = if (mode == MODE_OVERVIEW) formatCreatedAt(detail.createdAt) else ""
            if (mode == MODE_TRANSCRIPT) {
                binding.transcriptText.text = CourseRepository.formatTranscript(detail.transcript)
            }
            if (mode == MODE_SUMMARY) {
                binding.summaryInput.setText(detail.summary)
            }
            if (mode == MODE_SECTIONS) {
                renderSections(detail.sections)
            }
            if (mode == MODE_MINDMAP) {
                binding.mindMapView.setNodes(detail.mindmap)
            }
            applyModeVisibility()
        }.onFailure { error ->
            appContext?.let {
                Toast.makeText(it, "课程内容加载失败：${error.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun applyModeVisibility() {
        binding.backButton.visibility = if (mode == MODE_OVERVIEW) View.GONE else View.VISIBLE
        binding.overviewControls.visibility = if (mode == MODE_OVERVIEW) View.VISIBLE else View.GONE
        binding.overviewMenu.visibility = if (mode == MODE_OVERVIEW) View.VISIBLE else View.GONE
        binding.deleteCourseButton.visibility = if (mode == MODE_OVERVIEW) View.VISIBLE else View.GONE
        binding.contentTitle.visibility = if (mode == MODE_OVERVIEW) View.GONE else View.VISIBLE
        binding.contentTitle.text = contentTitle(mode)
        binding.summaryLayout.visibility = if (mode == MODE_SUMMARY) View.VISIBLE else View.GONE
        binding.sectionsContainer.visibility = if (mode == MODE_SECTIONS) View.VISIBLE else View.GONE
        binding.mindMapControls.visibility = if (mode == MODE_MINDMAP) View.VISIBLE else View.GONE
        binding.mindMapView.visibility = if (mode == MODE_MINDMAP) View.VISIBLE else View.GONE
        binding.transcriptScrollView.visibility = if (mode == MODE_TRANSCRIPT) View.VISIBLE else View.GONE
        binding.saveButton.visibility = if (mode == MODE_SUMMARY || mode == MODE_SECTIONS || mode == MODE_MINDMAP) {
            View.VISIBLE
        } else {
            View.GONE
        }
        binding.courseTitleText.visibility =
            if (mode == MODE_OVERVIEW || mode == MODE_SUMMARY || mode == MODE_SECTIONS || mode == MODE_MINDMAP) {
                View.VISIBLE
            } else {
                View.GONE
            }
        binding.courseMetaText.visibility = if (mode == MODE_OVERVIEW) View.VISIBLE else View.GONE
    }

    private fun contentTitle(mode: String): String = when (mode) {
        MODE_SUMMARY -> "全文详细总结"
        MODE_SECTIONS -> "分节小结"
        MODE_MINDMAP -> "思维导图"
        MODE_TRANSCRIPT -> "服务器转写内容"
        else -> course?.title.orEmpty()
    }

    private fun formatCreatedAt(ms: Long): String {
        if (ms <= 0L) return ""
        return SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(ms))
    }

    private fun renderSections(sections: List<com.keji.classroomnote.net.CourseSection>) {
        binding.sectionsContainer.removeAllViews()
        sectionInputs.clear()
        sections.forEachIndexed { index, section ->
            val title = TextView(requireContext()).apply {
                text = "${section.title} · ${CourseRepository.formatTime(section.startMs)}"
                setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_TitleSmall)
                setPadding(0, dp(10), 0, dp(4))
            }
            val inputLayout = com.google.android.material.textfield.TextInputLayout(requireContext()).apply {
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(4) }
            }
            val input = com.google.android.material.textfield.TextInputEditText(inputLayout.context).apply {
                hint = "分节小结"
                inputType = android.text.InputType.TYPE_CLASS_TEXT or
                    android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
                setText(section.summary)
                minLines = 3
                gravity = android.view.Gravity.TOP
            }
            inputLayout.addView(input)
            binding.sectionsContainer.addView(title)
            binding.sectionsContainer.addView(inputLayout)
            sectionInputs[index] = input
        }
    }

    private fun save() {
        val current = course ?: return
        val appContext = appContext ?: return
        val safeBinding = _binding ?: return
        val updatedSections = current.sections.mapIndexed { index, section ->
            if (mode == MODE_SECTIONS) {
                section.copy(summary = sectionInputs[index]?.text?.toString().orEmpty())
            } else {
                section
            }
        }
        val updated = current.copy(
            title = current.title,
            summary = if (mode == MODE_SUMMARY) binding.summaryInput.text?.toString().orEmpty() else current.summary,
            sections = updatedSections,
            mindmap = if (mode == MODE_MINDMAP) binding.mindMapView.nodes() else current.mindmap,
        )

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                course = repository.save(updated)
                if (safeBinding == _binding) course?.let(::render)
                Toast.makeText(appContext, "已保存", Toast.LENGTH_SHORT).show()
            } catch (error: Exception) {
                Toast.makeText(appContext, "保存失败：${error.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun generate() {
        val current = course ?: return
        val appContext = appContext ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val task = repository.generate(current.courseId, null)
                Toast.makeText(appContext, "生成任务：${task.status}", Toast.LENGTH_SHORT).show()
                if (_binding != null) load()
            } catch (error: Exception) {
                Toast.makeText(appContext, "生成失败：${error.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun deleteCourse() {
        val current = course ?: return
        val appContext = appContext ?: return
        AlertDialog.Builder(requireContext())
            .setTitle("删除课程")
            .setMessage("确定要删除“${current.title}”吗？删除后不可恢复，服务端和本地的课程数据都会被移除。")
            .setNegativeButton("取消", null)
            .setPositiveButton("删除") { _, _ ->
                viewLifecycleOwner.lifecycleScope.launch {
                    try {
                        repository.deleteCourse(current.courseId)
                        Toast.makeText(appContext, "课程已删除", Toast.LENGTH_SHORT).show()
                        parentFragmentManager.popBackStack()
                    } catch (error: Exception) {
                        Toast.makeText(appContext, "删除失败：${error.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }
            .show()
    }

    private fun export(target: Uri, generator: (CourseDetail) -> String, suffix: String) {
        val current = course ?: return
        val context = appContext ?: return
        runCatching {
            context.contentResolver.openOutputStream(target)?.use { stream ->
                stream.write(generator(current).encodeToByteArray())
            }
        }.onSuccess {
            Toast.makeText(requireContext(), "已导出 $suffix", Toast.LENGTH_SHORT).show()
        }.onFailure { error ->
            Toast.makeText(requireContext(), "导出失败：${error.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun addChild() {
        val selected = binding.mindMapView.selectedNode()
        val newNode = MindMapNode(nodeId = CourseRepository.newId(), text = "新知识点")
        mutateMindMap { roots ->
            if (selected == null) roots.add(newNode) else findNode(roots, selected.nodeId)?.children?.add(newNode)
        }
        binding.mindMapView.select(newNode.nodeId)
    }

    private fun renameSelected() {
        val selected = binding.mindMapView.selectedNode() ?: return
        val input = EditText(requireContext()).apply { setText(selected.text) }
        AlertDialog.Builder(requireContext())
            .setTitle("重命名节点")
            .setView(input)
            .setPositiveButton("保存") { _, _ ->
                val value = input.text?.toString()?.trim().orEmpty()
                if (value.isNotBlank()) {
                    mutateMindMap { roots ->
                        findNode(roots, selected.nodeId)?.text = value
                    }
                    binding.mindMapView.select(selected.nodeId)
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun deleteSelected() {
        val selected = binding.mindMapView.selectedNode() ?: return
        mutateMindMap { roots -> removeNode(roots, selected.nodeId) }
    }

    private fun indentSelected() {
        val selected = binding.mindMapView.selectedNode() ?: return
        mutateMindMap { roots ->
            val parent = findParent(roots, selected.nodeId)
            val index = parent?.children?.indexOfFirst { it.nodeId == selected.nodeId } ?: -1
            if (parent != null && index > 0) {
                val previous = parent.children[index - 1]
                parent.children.removeAt(index)
                previous.children.add(selected)
            }
        }
    }

    private fun outdentSelected() {
        val selected = binding.mindMapView.selectedNode() ?: return
        mutateMindMap { roots ->
            val parent = findParent(roots, selected.nodeId) ?: return@mutateMindMap
            val indexInParent = parent.children.indexOfFirst { it.nodeId == selected.nodeId }
            if (indexInParent < 0) return@mutateMindMap
            parent.children.removeAt(indexInParent)
            val grandparent = findParent(roots, parent.nodeId)
            if (grandparent == null) {
                val parentIndex = roots.indexOfFirst { it.nodeId == parent.nodeId }
                if (parentIndex >= 0) roots.add(parentIndex + 1, selected) else roots.add(selected)
            } else {
                val parentIndex = grandparent.children.indexOfFirst { it.nodeId == parent.nodeId }
                if (parentIndex >= 0) grandparent.children.add(parentIndex + 1, selected)
                else grandparent.children.add(selected)
            }
        }
    }

    private fun moveSelected(direction: Int) {
        val selected = binding.mindMapView.selectedNode() ?: return
        mutateMindMap { roots ->
            val parent = findParent(roots, selected.nodeId)
            val siblings = parent?.children ?: roots
            val index = siblings.indexOfFirst { it.nodeId == selected.nodeId }
            val target = index + direction
            if (index >= 0 && target in siblings.indices) {
                siblings[index] = siblings[target]
                siblings[target] = selected
            }
        }
        binding.mindMapView.select(selected.nodeId)
    }

    private fun mutateMindMap(block: (MutableList<MindMapNode>) -> Unit) {
        val current = course ?: return
        val roots = current.mindmap.toMutableList()
        block(roots)
        course = current.copy(mindmap = roots)
        binding.mindMapView.setNodes(roots)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onDestroyView() {
        refreshJob?.cancel()
        _binding = null
        super.onDestroyView()
    }

    companion object {
        private const val ARG_COURSE_ID = "course_id"
        private const val ARG_MODE = "mode"
        private const val POLL_INTERVAL_MS = 10_000L
        private const val MAX_POLLS = 120

        const val MODE_OVERVIEW = "overview"
        const val MODE_SUMMARY = "summary"
        const val MODE_SECTIONS = "sections"
        const val MODE_MINDMAP = "mindmap"
        const val MODE_TRANSCRIPT = "transcript"

        fun newInstance(courseId: String): CourseDetailFragment =
            newInstance(courseId, MODE_OVERVIEW)

        fun newInstance(courseId: String, mode: String): CourseDetailFragment =
            CourseDetailFragment().apply {
                arguments = Bundle().apply {
                    putString(ARG_COURSE_ID, courseId)
                    putString(ARG_MODE, mode)
                }
            }
    }
}

private fun findNode(nodes: List<MindMapNode>, id: String?): MindMapNode? {
    if (id == null) return null
    nodes.forEach { node ->
        if (node.nodeId == id) return node
        findNode(node.children, id)?.let { return it }
    }
    return null
}

private fun findParent(nodes: List<MindMapNode>, id: String): MindMapNode? {
    nodes.forEach { node ->
        if (node.children.any { it.nodeId == id }) return node
        findParent(node.children, id)?.let { return it }
    }
    return null
}

private fun removeNode(nodes: MutableList<MindMapNode>, id: String): Boolean {
    val removed = nodes.removeAll { it.nodeId == id }
    if (removed) return true
    nodes.forEach { node ->
        if (removeNode(node.children as MutableList<MindMapNode>, id)) return true
    }
    return false
}
