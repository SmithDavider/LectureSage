package com.keji.classroomnote.store

import android.content.Context
import android.content.SharedPreferences

class AppPreferences(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("keji_settings", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL
        set(value) = prefs.edit().putString(KEY_SERVER_URL, value.trim()).apply()

    var username: String
        get() = prefs.getString(KEY_USERNAME, "") ?: ""
        set(value) = prefs.edit().putString(KEY_USERNAME, value.trim()).apply()

    var password: String
        get() = prefs.getString(KEY_PASSWORD, "") ?: ""
        set(value) = prefs.edit().putString(KEY_PASSWORD, value.trim()).apply()

    var screenIntervalSec: Int
        get() = prefs.getInt(KEY_SCREEN_INTERVAL, 5)
        set(value) = prefs.edit().putInt(KEY_SCREEN_INTERVAL, value.coerceIn(1, 30)).apply()

    var screenEnabled: Boolean
        get() = prefs.getBoolean(KEY_SCREEN_ENABLED, true)
        set(value) = prefs.edit().putBoolean(KEY_SCREEN_ENABLED, value).apply()

    var microphoneEnabled: Boolean
        get() = prefs.getBoolean(KEY_MICROPHONE_ENABLED, true)
        set(value) = prefs.edit().putBoolean(KEY_MICROPHONE_ENABLED, value).apply()

    var llmBaseUrl: String
        get() = prefs.getString(KEY_LLM_BASE_URL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LLM_BASE_URL, value.trim()).apply()

    var llmApiKey: String
        get() = prefs.getString(KEY_LLM_API_KEY, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LLM_API_KEY, value.trim()).apply()

    var llmModel: String
        get() = prefs.getString(KEY_LLM_MODEL, "") ?: ""
        set(value) = prefs.edit().putString(KEY_LLM_MODEL, value.trim()).apply()

    var localQuotaMb: Long
        get() = prefs.getLong(KEY_LOCAL_QUOTA_MB, DEFAULT_LOCAL_QUOTA_MB)
        set(value) = prefs.edit().putLong(KEY_LOCAL_QUOTA_MB, value.coerceAtLeast(1L)).apply()

    var activeCourseId: String
        get() = prefs.getString(KEY_ACTIVE_COURSE_ID, "") ?: ""
        set(value) = prefs.edit().putString(KEY_ACTIVE_COURSE_ID, value.trim()).apply()

    var skippedUpdateVersion: Int
        get() = prefs.getInt(KEY_SKIPPED_UPDATE_VERSION, 0)
        set(value) = prefs.edit().putInt(KEY_SKIPPED_UPDATE_VERSION, value).apply()

    companion object {
        private const val KEY_SERVER_URL = "server_url"
        private const val KEY_USERNAME = "server_username"
        private const val KEY_PASSWORD = "server_password"
        private const val KEY_SCREEN_INTERVAL = "screen_interval_sec"
        private const val KEY_SCREEN_ENABLED = "screen_enabled"
        private const val KEY_MICROPHONE_ENABLED = "microphone_enabled"
        private const val KEY_LLM_BASE_URL = "llm_base_url"
        private const val KEY_LLM_API_KEY = "llm_api_key"
        private const val KEY_LLM_MODEL = "llm_model"
        private const val KEY_LOCAL_QUOTA_MB = "local_quota_mb"
        private const val KEY_ACTIVE_COURSE_ID = "active_course_id"
        private const val KEY_SKIPPED_UPDATE_VERSION = "skipped_update_version"
        private const val DEFAULT_SERVER_URL = ""
        private const val DEFAULT_LOCAL_QUOTA_MB = 500L
    }
}
