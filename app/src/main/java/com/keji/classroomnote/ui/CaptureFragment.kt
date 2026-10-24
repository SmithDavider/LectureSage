package com.keji.classroomnote.ui

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaMetadataRetriever
import android.media.projection.MediaProjectionManager
import android.media.projection.MediaProjectionConfig
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.keji.classroomnote.capture.CaptureBus
import com.keji.classroomnote.capture.CaptureService
import com.keji.classroomnote.databinding.FragmentCaptureBinding
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.StartCourseRequest
import com.keji.classroomnote.store.AppPreferences
import com.keji.classroomnote.store.CourseRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class CaptureFragment : Fragment() {
    private var _binding: FragmentCaptureBinding? = null
    private val binding get() = _binding!!
    private val subtitleAdapter = SubtitlesAdapter()
    private var pendingTitle = ""
    private var pendingCourseId = ""
    private var importing = false
    private var lastRenderedSubtitleCount = -1
    private val audioPickerLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            uri?.let { importAudio(it) }
        }

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
            val prefs = AppPreferences(requireContext())
            val audioGranted = grants[Manifest.permission.RECORD_AUDIO] != false
            if (audioGranted || !prefs.microphoneEnabled) {
                if (prefs.screenEnabled) {
                    startProjectionConsent()
                } else {
                    CaptureService.start(
                        context = requireContext(),
                        title = pendingTitle,
                        courseId = pendingCourseId,
                        enableScreen = false,
                        enableMicrophone = prefs.microphoneEnabled,
                    )
                }
            } else {
                CaptureBus.setStatus("需要麦克风权限才能开始课堂采集")
            }
        }

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode != Activity.RESULT_OK || result.data == null) {
                CaptureBus.setStatus("没有获得录屏授权")
                return@registerForActivityResult
            }
            val context = requireContext()
            CaptureService.start(
                context = context,
                title = pendingTitle,
                courseId = pendingCourseId.ifBlank { CourseRepository.newId() },
                resultCode = result.resultCode,
                data = result.data!!,
                enableScreen = true,
                enableMicrophone = AppPreferences(requireContext()).microphoneEnabled,
            )
        }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentCaptureBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.subtitleList.layoutManager = LinearLayoutManager(requireContext())
        binding.subtitleList.adapter = subtitleAdapter

        binding.titleInput.doAfterTextChanged { text -> pendingTitle = text?.toString().orEmpty() }
        binding.startButton.setOnClickListener { requestStart() }
        binding.stopButton.setOnClickListener { CaptureService.stop(requireContext()) }
        binding.importButton.setOnClickListener {
            audioPickerLauncher.launch(arrayOf("audio/*", "application/octet-stream"))
        }

        viewLifecycleOwner.lifecycleScope.launch {
            viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
                CaptureBus.uiState.collect { state -> render(state) }
            }
        }
    }

    private fun requestStart() {
        val prefs = AppPreferences(requireContext())
        if (!prefs.screenEnabled && !prefs.microphoneEnabled) {
            Toast.makeText(requireContext(), "请至少启用截屏或麦克风", Toast.LENGTH_LONG).show()
            return
        }
        pendingTitle = uniqueCourseTitle(binding.titleInput.text?.toString()?.trim().orEmpty())
        if (pendingTitle.isBlank()) {
            binding.titleInput.error = "请输入课程名称"
            return
        }
        pendingCourseId = CourseRepository.newId()
        val permissions = mutableListOf<String>()
        if (prefs.microphoneEnabled) {
            permissions.add(Manifest.permission.RECORD_AUDIO)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        val missing = permissions.filter {
            ContextCompat.checkSelfPermission(requireContext(), it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            if (prefs.screenEnabled) {
                startProjectionConsent()
            } else {
                CaptureService.start(
                    context = requireContext(),
                    title = pendingTitle,
                    courseId = pendingCourseId,
                    enableScreen = false,
                    enableMicrophone = prefs.microphoneEnabled,
                )
            }
        } else {
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    private fun startProjectionConsent() {
        val manager = requireContext().getSystemService(Context.MEDIA_PROJECTION_SERVICE)
            as MediaProjectionManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val config = MediaProjectionConfig.createConfigForDefaultDisplay()
            projectionLauncher.launch(manager.createScreenCaptureIntent(config))
        } else {
            projectionLauncher.launch(manager.createScreenCaptureIntent())
        }
    }

    private fun render(state: com.keji.classroomnote.capture.CaptureUiState) {
        binding.startButton.isEnabled = !state.running
        binding.stopButton.isEnabled = state.running
        binding.importButton.isEnabled = !state.running && !importing
        binding.statusText.text = state.status
        binding.timeText.text =
            "${CourseRepository.formatTime(state.elapsedMs)} · 待上传 ${state.pendingUploads}"
        subtitleAdapter.submit(state.subtitles)
        if (state.subtitles.size != lastRenderedSubtitleCount) {
            lastRenderedSubtitleCount = state.subtitles.size
            binding.subtitleList.scrollToPosition(state.subtitles.size - 1)
        }
    }

    private fun importAudio(uri: Uri) {
        if (importing || CaptureBus.uiState.value.running) return
        importing = true
        binding.importButton.isEnabled = false

        val appContext = requireContext().applicationContext
        val resolver = appContext.contentResolver
        var fileName = "音频文件"
        var fileSize = -1L
        resolver.query(
            uri,
            arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
            null,
            null,
            null,
        )?.use { cursor ->
            if (cursor.moveToFirst()) {
                cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    .takeIf { it >= 0 }
                    ?.let { cursor.getString(it)?.takeIf { name -> name.isNotBlank() }?.let { name -> fileName = name } }
                cursor.getColumnIndex(OpenableColumns.SIZE)
                    .takeIf { it >= 0 }
                    ?.let { fileSize = cursor.getLong(it) }
            }
        }

        val title = uniqueCourseTitle(
            binding.titleInput.text?.toString()?.trim().orEmpty()
                .ifBlank { fileName.substringBeforeLast('.', fileName) }
        )
        val courseId = CourseRepository.newId()
        val durationMs = audioDurationMs(uri)
        val prefs = AppPreferences(appContext)
        val api = ApiClient(appContext)
        CaptureBus.setStatus("正在准备上传音频：$fileName")

        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                api.startCourse(
                    StartCourseRequest(
                        courseId = courseId,
                        title = title,
                        source = "audio_import",
                        screenEnabled = false,
                        microphoneEnabled = true,
                        llmBaseUrl = prefs.llmBaseUrl.ifBlank { null },
                        llmApiKey = prefs.llmApiKey.ifBlank { null },
                        llmModel = prefs.llmModel.ifBlank { null },
                    ),
                )
                var lastPercent = -1
                api.uploadAudioStream(
                    courseId = courseId,
                    fileName = fileName,
                    contentType = resolver.getType(uri) ?: mimeByExtension(fileName),
                    contentLength = fileSize,
                    openStream = { resolver.openInputStream(uri) },
                    onProgress = { written, total ->
                        if (total > 0) {
                            val percent = (written * 100 / total).toInt()
                            if (percent != lastPercent) {
                                lastPercent = percent
                                CaptureBus.setStatus("正在上传音频：$fileName（$percent%）")
                            }
                        }
                    },
                )
                api.finishCourse(courseId, durationMs)
                CaptureBus.setStatus("导入完成，转写任务已提交")
                Toast.makeText(
                    appContext,
                    "$title 已提交转写，可在“课程”中查看",
                    Toast.LENGTH_LONG,
                ).show()
            } catch (error: Exception) {
                CaptureBus.setStatus("导入失败：${error.message}")
                Toast.makeText(appContext, "导入失败：${error.message}", Toast.LENGTH_LONG).show()
            } finally {
                importing = false
                _binding?.let { safe ->
                    safe.importButton.isEnabled = !CaptureBus.uiState.value.running
                }
            }
        }
    }

    private fun audioDurationMs(uri: Uri): Long = runCatching {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(requireContext(), uri)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
        } finally {
            retriever.release()
        }
    }.getOrDefault(0L)

    private fun mimeByExtension(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "mp3" -> "audio/mpeg"
        "m4a", "mp4" -> "audio/mp4"
        "aac" -> "audio/aac"
        "wav" -> "audio/wav"
        "ogg", "opus" -> "audio/ogg"
        else -> "application/octet-stream"
    }

    private fun uniqueCourseTitle(raw: String): String {
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date())
        return if (raw.isBlank()) raw else "$raw $stamp"
    }

    override fun onDestroyView() {
        _binding = null
        super.onDestroyView()
    }
}
