package com.keji.classroomnote.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import androidx.fragment.app.Fragment
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.keji.classroomnote.capture.CaptureBus
import com.keji.classroomnote.databinding.FragmentSystemSettingsBinding
import com.keji.classroomnote.net.UpdateCheckResponse
import com.keji.classroomnote.store.AppPreferences
import com.keji.classroomnote.store.LocalCacheStore
import com.keji.classroomnote.update.AppUpdater
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

class SystemSettingsFragment : Fragment() {
    private var _binding: FragmentSystemSettingsBinding? = null
    private val binding get() = _binding!!
    private val prefs by lazy { AppPreferences(requireContext()) }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentSystemSettingsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.backButton.setOnClickListener { requireActivity().onBackPressedDispatcher.onBackPressed() }
        binding.screenIntervalInput.setText(prefs.screenIntervalSec.toString())
        binding.localQuotaInput.setText(prefs.localQuotaMb.toString())
        binding.screenEnabledSwitch.isChecked = prefs.screenEnabled
        binding.micEnabledSwitch.isChecked = prefs.microphoneEnabled
        binding.currentVersionText.text = "当前版本：${installedVersionName()}"

        binding.saveSystemButton.setOnClickListener {
            prefs.screenIntervalSec = binding.screenIntervalInput.text?.toString()?.toIntOrNull() ?: 5
            val localQuota = binding.localQuotaInput.text?.toString()?.toLongOrNull() ?: 500L
            if (localQuota <= 0L) {
                binding.localQuotaInput.error = "请输入大于 0 的容量"
                return@setOnClickListener
            }
            prefs.localQuotaMb = localQuota
            prefs.screenEnabled = binding.screenEnabledSwitch.isChecked
            prefs.microphoneEnabled = binding.micEnabledSwitch.isChecked

            val message = if (CaptureBus.uiState.value.running) {
                "设置已保存；采集/麦克风开关将在下一次采集生效"
            } else {
                "设置已保存"
            }
            Toast.makeText(requireContext(), message, Toast.LENGTH_SHORT).show()
        }
        binding.manageStorageButton.setOnClickListener { showStorageManager() }
        binding.llmSettingsButton.setOnClickListener { showLlmDialog() }
        binding.checkUpdateButton.setOnClickListener { checkForUpdate(showNoUpdate = true) }
    }

    private fun checkForUpdate(showNoUpdate: Boolean) {
        lifecycleScope.launch {
            try {
                val info = AppUpdater(requireContext()).check()
                if (!info.hasUpdate || info.versionCode <= installedVersionCode()) {
                    if (showNoUpdate) {
                        Toast.makeText(requireContext(), "当前已是最新版本", Toast.LENGTH_SHORT).show()
                    }
                    return@launch
                }
                val message = buildString {
                    append("发现新版本：v${info.versionName}\n")
                    append(info.notes.takeIf { it.isNotBlank() }?.let { "$it\n" }.orEmpty())
                    append("更新安装不会清除登录状态和本地数据。")
                }
                MaterialAlertDialogBuilder(requireContext())
                    .setTitle("检查到更新")
                    .setMessage(message)
                    .setPositiveButton("立即更新") { _, _ ->
                        startUpdateDownloadWithProgress(info) { success, error ->
                            Toast.makeText(
                                requireContext(),
                                if (success) "已开始安装，请按系统提示完成更新" else (error ?: "网络异常"),
                                Toast.LENGTH_LONG,
                            ).show()
                        }
                    }
                    .setNegativeButton("暂不更新", null)
                    .show()
            } catch (error: Exception) {
                if (showNoUpdate) {
                    Toast.makeText(requireContext(), "检查更新失败：${error.message ?: "网络异常"}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun startUpdateDownloadWithProgress(
        info: UpdateCheckResponse,
        onFinished: (Boolean, String?) -> Unit,
    ) {
        val progressBar = ProgressBar(requireContext(), null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
        }
        val messageView = TextView(requireContext()).apply {
            text = "正在准备下载..."
            setPadding(0, dp(10), 0, 0)
        }
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(24), dp(10), dp(24), dp(10))
            addView(progressBar)
            addView(messageView)
        }
        val dialog = MaterialAlertDialogBuilder(requireContext())
            .setTitle("正在下载更新")
            .setView(container)
            .setCancelable(false)
            .create()
        dialog.show()
        var lastWritten = 0L
        var lastTime = System.currentTimeMillis()

        AppUpdater(requireContext()).downloadAndInstall(
            info,
            listener = { success, error ->
                requireActivity().runOnUiThread {
                    if (dialog.isShowing) dialog.dismiss()
                    onFinished(success, error)
                }
            },
            onProgress = { written, total ->
                val percent = if (total > 0) ((written * 100L) / total).toInt() else 0
                requireActivity().runOnUiThread {
                    val now = System.currentTimeMillis()
                    val speed = if (now > lastTime) {
                        ((written - lastWritten) * 1000L) / (now - lastTime)
                    } else {
                        0L
                    }
                    lastTime = now
                    lastWritten = written
                    progressBar.progress = percent
                    messageView.text = if (total > 0L) {
                        "正在下载 ${formatBytes(written)} / ${formatBytes(total)}（$percent%） · ${formatBytes(speed)}/s"
                    } else {
                        "正在下载 ${formatBytes(written)}... · ${formatBytes(speed)}/s"
                    }
                }
            },
        )
    }

    private fun installedVersionCode(): Int = try {
        requireContext().packageManager.getPackageInfo(requireContext().packageName, 0).versionCode
    } catch (_: Exception) {
        0
    }

    private fun installedVersionName(): String = try {
        requireContext().packageManager.getPackageInfo(requireContext().packageName, 0).versionName ?: ""
    } catch (_: Exception) {
        ""
    }

    private fun formatBytes(bytes: Long): String = humanSize(bytes)

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    private fun showStorageManager() {
        val infos = LocalCacheStore.pendingFileInfos(requireContext())
        val bytes = infos.sumOf { it.sizeBytes }
        val audioCount = infos.count { it.kind == "音频" }
        val imageCount = infos.count { it.kind == "图片" }
        val quotaMb = prefs.localQuotaMb
        val summary = buildString {
            append("本地缓存：${humanBytes(bytes)} / ${quotaMb}MB\n")
            append("音频 ${audioCount} 个，图片 ${imageCount} 个\n")
            append(if (infos.isEmpty()) "暂无未上传的本地缓存" else "点按文件可查看，可清空全部缓存")
        }
        val items = infos.map { info ->
            "${info.kind} · ${formatFileName(info.file.name)} · ${humanSize(info.sizeBytes)}"
        }.toTypedArray()

        MaterialAlertDialogBuilder(requireContext())
            .setTitle("容量管理")
            .setMessage(summary)
            .setItems(if (items.isEmpty()) emptyArray() else items) { _, which ->
                openPendingFile(infos[which])
            }
            .setPositiveButton("关闭", null)
            .setNeutralButton(if (items.isEmpty()) null else "清空全部缓存") { _, _ ->
                infos.forEach { LocalCacheStore.deleteFile(it.file) }
                Toast.makeText(requireContext(), "已清空本地缓存", Toast.LENGTH_SHORT).show()
            }
            .show()
    }

    private fun openPendingFile(info: LocalCacheStore.PendingFile) {
        val providerUri = try {
            FileProvider.getUriForFile(
                requireContext(),
                "${requireContext().packageName}.fileprovider",
                info.file,
            )
        } catch (error: Exception) {
            Toast.makeText(requireContext(), "无法打开文件：${error.message}", Toast.LENGTH_LONG).show()
            return
        }
        val mime = if (info.kind == "音频") "audio/aac" else "image/jpeg"
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(providerUri, mime)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        try {
            startActivity(intent)
        } catch (error: ActivityNotFoundException) {
            Toast.makeText(requireContext(), "没有可打开该类文件的播放器或查看器", Toast.LENGTH_LONG).show()
        }
    }

    private fun formatFileName(name: String): String =
        if (name.length <= 34) name else name.take(15) + "..." + name.takeLast(16)

    private fun humanSize(bytes: Long): String = when {
        bytes >= 1024L * 1024L -> "%.1fMB".format(bytes / (1024.0 * 1024.0))
        bytes >= 1024L -> "%.1fKB".format(bytes / 1024.0)
        else -> "${bytes}B"
    }

    private fun humanBytes(bytes: Long): String = humanSize(bytes)

    private fun showLlmDialog() {
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            val paddingX = (12 * resources.displayMetrics.density).toInt()
            setPadding(paddingX, (4 * resources.displayMetrics.density).toInt(), paddingX, 0)
        }
        val baseUrlInput = TextInputEditText(requireContext()).apply {
            setText(prefs.llmBaseUrl)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            hint = "Base URL（OpenAI 兼容）"
        }
        val baseUrlLayout = TextInputLayout(requireContext()).apply { addView(baseUrlInput) }
        val apiKeyInput = TextInputEditText(requireContext()).apply {
            setText(prefs.llmApiKey)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            hint = "API Key"
        }
        val apiKeyLayout = TextInputLayout(requireContext()).apply { addView(apiKeyInput) }
        val modelInput = TextInputEditText(requireContext()).apply {
            setText(prefs.llmModel)
            inputType = InputType.TYPE_CLASS_TEXT
            hint = "模型名称"
        }
        val modelLayout = TextInputLayout(requireContext()).apply { addView(modelInput) }
        val spacing = (8 * resources.displayMetrics.density).toInt()
        container.addView(baseUrlLayout)
        container.addView(
            apiKeyLayout,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = spacing },
        )
        container.addView(
            modelLayout,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = spacing },
        )

        MaterialAlertDialogBuilder(requireContext())
            .setTitle("大模型自定义")
            .setView(container)
            .setPositiveButton("保存", null)
            .setNegativeButton("取消", null)
            .create()
            .apply {
                setOnShowListener {
                    getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                        val baseUrl = baseUrlInput.text?.toString()?.trim().orEmpty()
                        val apiKey = apiKeyInput.text?.toString()?.trim().orEmpty()
                        val model = modelInput.text?.toString()?.trim().orEmpty()
                        if (baseUrl.isNotEmpty()) {
                            if (!baseUrl.startsWith("http://") && !baseUrl.startsWith("https://")) {
                                baseUrlLayout.error = "Base URL 必须以 http:// 或 https:// 开头"
                                return@setOnClickListener
                            }
                            if (model.isBlank()) {
                                modelLayout.error = "请填写模型名称"
                                return@setOnClickListener
                            }
                        } else if (apiKey.isNotBlank() || model.isNotBlank()) {
                            baseUrlLayout.error = "使用自定义大模型时请填写 Base URL"
                            return@setOnClickListener
                        }
                        prefs.llmBaseUrl = baseUrl
                        prefs.llmApiKey = apiKey
                        prefs.llmModel = model
                        dismiss()
                    }
                }
            }
            .show()
    }
}
