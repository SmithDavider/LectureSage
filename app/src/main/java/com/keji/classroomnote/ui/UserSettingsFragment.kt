package com.keji.classroomnote.ui

import android.os.Bundle
import android.text.InputType
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.keji.classroomnote.databinding.FragmentUserSettingsBinding
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.ApiException
import com.keji.classroomnote.store.AppPreferences
import com.keji.classroomnote.store.CourseRepository
import kotlinx.coroutines.launch
import java.io.IOException

class UserSettingsFragment : Fragment() {
    private var _binding: FragmentUserSettingsBinding? = null
    private val binding get() = _binding!!
    private val prefs by lazy { AppPreferences(requireContext()) }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        _binding = FragmentUserSettingsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        binding.backButton.setOnClickListener { requireActivity().onBackPressedDispatcher.onBackPressed() }
        binding.serverUrlInput.setText(prefs.serverUrl)
        binding.usernameInput.setText(prefs.username)
        binding.passwordInput.setText(prefs.password)

        binding.saveUserButton.setOnClickListener {
            val serverUrl = binding.serverUrlInput.text?.toString()?.trim().orEmpty()
            val username = binding.usernameInput.text?.toString()?.trim().orEmpty()
            val password = binding.passwordInput.text?.toString().orEmpty()
            if (serverUrl.isEmpty()) {
                binding.serverUrlInput.error = "请填写服务器地址"
                return@setOnClickListener
            }
            if (username.isEmpty()) {
                binding.usernameInput.error = "请填写用户名"
                return@setOnClickListener
            }
            if (password.isEmpty()) {
                binding.passwordInput.error = "请填写密码"
                return@setOnClickListener
            }
            lifecycleScope.launch {
                binding.saveUserButton.isEnabled = false
                binding.saveUserButton.text = "正在校验..."
                runCatching {
                    prefs.serverUrl = serverUrl
                    prefs.username = username
                    prefs.password = password
                    ApiClient(requireContext()).verifyLogin()
                }.onSuccess { ok ->
                    if (ok) {
                        Toast.makeText(
                            requireContext(),
                            "用户设置已保存，正在同步课程数据",
                            Toast.LENGTH_SHORT,
                        ).show()
                        runCatching { CourseRepository(requireContext()).syncLocalCourseCache() }
                    } else {
                        Toast.makeText(requireContext(), "用户名或密码错误", Toast.LENGTH_LONG).show()
                    }
                }.onFailure {
                    Toast.makeText(
                        requireContext(),
                        "无法连接服务器，请检查账号信息和网络",
                        Toast.LENGTH_LONG,
                    ).show()
                }
                binding.saveUserButton.isEnabled = true
                binding.saveUserButton.text = "保存用户设置"
            }
        }
        binding.forgotPasswordText.setOnClickListener { showChangePasswordDialog() }
    }

    private fun showChangePasswordDialog() {
        val username = binding.usernameInput.text?.toString()?.trim()
        val rowPadding = (12 * resources.displayMetrics.density).toInt()
        val columnPadding = (4 * resources.displayMetrics.density).toInt()
        val container = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(rowPadding, columnPadding, rowPadding, 0)
        }
        val currentInput = TextInputEditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            hint = "当前密码"
        }
        val currentLayout = TextInputLayout(requireContext()).apply { addView(currentInput) }
        val newInput = TextInputEditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            hint = "新密码"
        }
        val newLayout = TextInputLayout(requireContext()).apply { addView(newInput) }
        val confirmInput = TextInputEditText(requireContext()).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            hint = "确认新密码"
        }
        val confirmLayout = TextInputLayout(requireContext()).apply { addView(confirmInput) }
        container.addView(currentLayout)
        container.addView(
            newLayout,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = (8 * resources.displayMetrics.density).toInt() },
        )
        container.addView(
            confirmLayout,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { topMargin = (8 * resources.displayMetrics.density).toInt() },
        )

        MaterialAlertDialogBuilder(requireContext())
            .setTitle("忘记密码")
            .setMessage(if (username.isNullOrBlank()) "请先填写用户名后再重置密码。" else "账号：$username 请输入当前暂用密码和新密码。")
            .setView(container)
            .setPositiveButton("提交", null)
            .setNegativeButton("取消", null)
            .create()
            .apply {
                setOnShowListener {
                    getButton(android.app.AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                        val oldPassword = currentInput.text?.toString().orEmpty()
                        val newPassword = newInput.text?.toString().orEmpty()
                        val confirmPassword = confirmInput.text?.toString().orEmpty()
                        if (oldPassword.isEmpty()) {
                            currentLayout.error = "请填写当前密码"
                            return@setOnClickListener
                        }
                        if (newPassword.length < 6) {
                            newLayout.error = "新密码至少 6 位"
                            return@setOnClickListener
                        }
                        if (newPassword == oldPassword) {
                            newLayout.error = "新密码不能和当前密码相同"
                            return@setOnClickListener
                        }
                        if (newPassword != confirmPassword) {
                            confirmLayout.error = "两次新密码不一致"
                            return@setOnClickListener
                        }
                        dismiss()
                        changePassword(oldPassword, newPassword)
                    }
                }
            }
            .show()
    }

    private fun changePassword(oldPassword: String, newPassword: String) {
        lifecycleScope.launch {
            try {
                ApiClient(requireContext()).changePassword(oldPassword, newPassword)
                prefs.password = newPassword
                binding.passwordInput.setText(newPassword)
                Toast.makeText(requireContext(), "密码已更改", Toast.LENGTH_SHORT).show()
            } catch (error: ApiException) {
                val message = if (error.code == 401) {
                    "当前密码错误"
                } else {
                    "密码修改失败：${error.body.take(120)}"
                }
                Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show()
            } catch (error: IOException) {
                Toast.makeText(requireContext(), "密码修改失败：${error.message ?: "网络异常"}", Toast.LENGTH_LONG).show()
            }
        }
    }
}
