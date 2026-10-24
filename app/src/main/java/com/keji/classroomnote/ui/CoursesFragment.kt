package com.keji.classroomnote.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import android.util.Log
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.keji.classroomnote.R
import com.keji.classroomnote.databinding.FragmentCoursesBinding
import com.keji.classroomnote.store.CourseRepository
import kotlinx.coroutines.launch

class CoursesFragment : Fragment() {
    private var _binding: FragmentCoursesBinding? = null
    private val binding get() = _binding!!
    private val repository by lazy { CourseRepository(requireContext()) }
    private val adapter = CoursesAdapter(
        onClick = { course -> openCourse(course) },
        onDelete = { course -> confirmDelete(course) },
    )

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentCoursesBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.courseList.layoutManager = LinearLayoutManager(requireContext())
        binding.courseList.adapter = adapter
        binding.refreshButton.setOnClickListener { load(showError = true) }
        load(showError = false)
    }

    override fun onResume() {
        super.onResume()
        if (_binding != null) {
            load(showError = false)
        }
    }

    private fun load(showError: Boolean) {
        val appContext = requireContext().applicationContext
        val safeBinding = _binding ?: return
        viewLifecycleOwner.lifecycleScope.launch {
            safeBinding.stateText.text = "正在加载课程记录..."
            try {
                val courses = repository.listOrEmpty()
                adapter.submit(courses)
                safeBinding.stateText.text = if (courses.isEmpty()) {
                    "暂无课程记录"
                } else {
                    "共 ${courses.size} 条课程记录"
                }
            } catch (error: Exception) {
                Log.e("KejiCourses", "加载课程记录失败", error)
                safeBinding.stateText.text = "加载失败：${error.message}"
                if (showError) Toast.makeText(appContext, error.message, Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun openCourse(course: com.keji.classroomnote.net.CourseListItem) {
        parentFragmentManager.beginTransaction()
            .replace(R.id.container, CourseDetailFragment.newInstance(course.courseId))
            .addToBackStack(null)
            .commit()
    }

    private fun confirmDelete(course: com.keji.classroomnote.net.CourseListItem) {
        AlertDialog.Builder(requireContext())
            .setTitle("删除课程")
            .setMessage("确定要删除“${course.title}”吗？删除后不可恢复。")
            .setNegativeButton("取消", null)
            .setPositiveButton("删除") { _, _ ->
                viewLifecycleOwner.lifecycleScope.launch {
                    try {
                        repository.deleteCourse(course.courseId)
                        load(showError = false)
                        Toast.makeText(
                            requireContext().applicationContext,
                            "课程已删除",
                            Toast.LENGTH_SHORT,
                        ).show()
                    } catch (error: Exception) {
                        Toast.makeText(
                            requireContext().applicationContext,
                            "删除失败：${error.message}",
                            Toast.LENGTH_LONG,
                        ).show()
                    }
                }
            }
            .show()
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
