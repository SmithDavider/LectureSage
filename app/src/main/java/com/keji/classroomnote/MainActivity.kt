package com.keji.classroomnote

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.PopupWindow
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.keji.classroomnote.databinding.ActivityMainBinding
import com.keji.classroomnote.capture.CaptureBus
import com.keji.classroomnote.ClassNoteApp
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.ConnectionMode
import com.keji.classroomnote.net.UpdateCheckResponse
import com.keji.classroomnote.store.AppPreferences
import com.keji.classroomnote.store.CourseRepository
import com.keji.classroomnote.ui.CaptureFragment
import com.keji.classroomnote.ui.CoursesFragment
import com.keji.classroomnote.ui.SettingsFragment
import com.keji.classroomnote.update.AppUpdater
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {
    private var wasInBackground = false
    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val prefs = AppPreferences(this)
        if (prefs.username.isBlank() || prefs.password.isBlank()) {
            startActivity(Intent(this, LoginActivity::class.java))
            finish()
            return
        }
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        binding.bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_courses -> showTab(1)
                R.id.nav_settings -> showTab(2)
                else -> showTab(0)
            }
            true
        }

        if (savedInstanceState == null) {
            binding.bottomNav.selectedItemId = R.id.nav_capture
        }

        lifecycleScope.launch {
            val currentCourseId = CaptureBus.uiState.value.takeIf { it.running }?.courseId
            runCatching {
                CourseRepository(this@MainActivity).markStaleRecordingCoursesFinished(currentCourseId)
            }
        }

        showConnectionBannerOnce()
        checkForUpdateOnce()
    }

    override fun onStart() {
        super.onStart()
        if (wasInBackground) {
            showConnectionBannerOnce()
        }
        wasInBackground = false
        syncCourseLocalCache()
    }

    override fun onStop() {
        wasInBackground = true
        super.onStop()
    }

    private fun syncCourseLocalCache() {
        val prefs = AppPreferences(this)
        if (prefs.username.isBlank() || prefs.password.isBlank()) return
        lifecycleScope.launch {
            runCatching { CourseRepository(this@MainActivity).syncLocalCourseCache() }
        }
    }

    private fun checkForUpdateOnce() {
        if (ClassNoteApp.updateCheckStartedInProcess) return
        ClassNoteApp.updateCheckStartedInProcess = true
        lifecycleScope.launch {
            runCatching {
                val info = AppUpdater(this@MainActivity).check()
                val prefs = AppPreferences(this@MainActivity)
                if (!info.hasUpdate || info.versionCode <= AppUpdater(this@MainActivity).installedVersionCode()) return@runCatching
                if (prefs.skippedUpdateVersion == info.versionCode) return@runCatching
                MaterialAlertDialogBuilder(this@MainActivity)
                    .setTitle("检查到新版本")
                    .setMessage(buildString {
                        append("新版本 v${info.versionName} 已发布\n")
                        append(info.notes.takeIf { it.isNotBlank() }?.let { "$it\n" }.orEmpty())
                        append("更新安装不会清除登录状态和本地数据。")
                    })
                    .setPositiveButton("立即更新") { _, _ ->
                        startUpdateDownloadWithProgress(info) { success, error ->
                            Toast.makeText(
                                this@MainActivity,
                                if (success) "已开始安装，请按系统提示完成更新" else (error ?: "网络异常"),
                                Toast.LENGTH_LONG,
                            ).show()
                        }
                    }
                    .setNegativeButton("放弃本次更新") { _, _ ->
                        prefs.skippedUpdateVersion = info.versionCode
                    }
                    .show()
            }
        }
    }

    private fun startUpdateDownloadWithProgress(
        info: UpdateCheckResponse,
        onFinished: (Boolean, String?) -> Unit,
    ) {
        val progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
        }
        val messageView = TextView(this).apply {
            text = "正在准备下载..."
            setPadding(0, dp(10), 0, 0)
        }
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(24), dp(10), dp(24), dp(10))
            addView(progressBar)
            addView(messageView)
        }
        val dialog = MaterialAlertDialogBuilder(this)
            .setTitle("正在下载更新")
            .setView(container)
            .setCancelable(false)
            .create()
        dialog.show()
        var lastWritten = 0L
        var lastTime = System.currentTimeMillis()

        AppUpdater(this@MainActivity).downloadAndInstall(
            info,
            listener = { success, error ->
                runOnUiThread {
                    if (dialog.isShowing) dialog.dismiss()
                    onFinished.invoke(success, error)
                }
            },
            onProgress = { written, total ->
                runOnUiThread {
                    val percent = if (total > 0L) ((written * 100L) / total).toInt() else 0
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

    private fun showTab(position: Int) {
        supportFragmentManager.popBackStack(
            null,
            androidx.fragment.app.FragmentManager.POP_BACK_STACK_INCLUSIVE,
        )
        val fragment = when (position) {
            1 -> CoursesFragment()
            2 -> SettingsFragment()
            else -> CaptureFragment()
        }
        supportFragmentManager.beginTransaction().replace(R.id.container, fragment).commit()
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes >= 1024L * 1024L -> "%.1fMB".format(bytes / (1024.0 * 1024.0))
        bytes >= 1024L -> "%.1fKB".format(bytes / 1024.0)
        else -> "${bytes}B"
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density).toInt()

    private fun showConnectionBannerOnce() {
        lifecycleScope.launch {
            val mode = ApiClient(this@MainActivity).currentConnectionMode()
            if (binding.root.isShown) {
                showConnectionBanner(mode)
            }
        }
    }

    private fun showConnectionBanner(mode: ConnectionMode) {
        val label = TextView(this).apply {
            val content = when (mode) {
                ConnectionMode.CONNECTED -> "已连接"
                ConnectionMode.UNAVAILABLE -> "未连接服务端"
            }
            text = "当前连接：$content"
            textSize = 13f
            setTextColor(Color.WHITE)
            setPadding(dp(16), dp(8), dp(16), dp(8))
            setBackgroundColor(
                when (mode) {
                    ConnectionMode.CONNECTED -> Color.parseColor("#2E7D32")
                    ConnectionMode.UNAVAILABLE -> Color.parseColor("#C62828")
                }
            )
        }
        val popup = PopupWindow(
            label,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
        ).apply {
            isOutsideTouchable = true
            isFocusable = false
        }
        binding.root.post {
            popup.showAtLocation(
                binding.root,
                Gravity.TOP or Gravity.CENTER_HORIZONTAL,
                0,
                dp(12),
            )
        }
        binding.root.postDelayed({
            if (popup.isShowing) popup.dismiss()
        }, 1000L)
    }
}
