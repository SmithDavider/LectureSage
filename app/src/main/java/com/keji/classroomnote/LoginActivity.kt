package com.keji.classroomnote

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.keji.classroomnote.databinding.ActivityLoginBinding
import com.keji.classroomnote.net.ApiClient
import com.keji.classroomnote.net.ApiException
import com.keji.classroomnote.store.AppPreferences
import kotlinx.coroutines.launch

class LoginActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLoginBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        binding = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.serverUrlInput.setText(AppPreferences(this).serverUrl)
        binding.loginButton.setOnClickListener { startLogin() }
    }

    private fun startLogin() {
        val serverUrl = binding.serverUrlInput.text?.toString()?.trim().orEmpty()
        val username = binding.usernameInput.text?.toString()?.trim().orEmpty()
        val password = binding.passwordInput.text?.toString().orEmpty()
        if (serverUrl.isEmpty()) {
            binding.serverUrlLayout.error = "请填写服务器地址"
            return
        }
        binding.serverUrlLayout.error = null
        if (username.isEmpty()) {
            binding.usernameLayout.error = "请填写用户名"
            return
        }
        binding.usernameLayout.error = null
        if (password.isEmpty()) {
            binding.passwordLayout.error = "请填写密码"
            return
        }
        binding.passwordLayout.error = null

        binding.loginButton.isEnabled = false
        binding.loginButton.text = "登录中..."
        lifecycleScope.launch {
            try {
                val prefs = AppPreferences(this@LoginActivity)
                prefs.serverUrl = serverUrl
                prefs.username = username
                prefs.password = password
                val ok = ApiClient(this@LoginActivity).verifyLogin()
                if (ok) {
                    startActivity(Intent(this@LoginActivity, MainActivity::class.java))
                    finish()
                } else {
                    Toast.makeText(this@LoginActivity, "用户名或密码错误", Toast.LENGTH_LONG).show()
                    binding.loginButton.isEnabled = true
                    binding.loginButton.text = "登录"
                }
            } catch (error: ApiException) {
                val message = if (error.code == 401) {
                    "用户名或密码错误"
                } else {
                    "登录失败：${error.body.take(120)}"
                }
                Toast.makeText(this@LoginActivity, message, Toast.LENGTH_LONG).show()
                binding.loginButton.isEnabled = true
                binding.loginButton.text = "登录"
            } catch (error: Exception) {
                Toast.makeText(
                    this@LoginActivity,
                    "无法连接服务器，请检查网络后重试：${error.message ?: ""}",
                    Toast.LENGTH_LONG,
                ).show()
                binding.loginButton.isEnabled = true
                binding.loginButton.text = "登录"
            }
        }
    }
}
