package app.personalagent.companion

import android.content.Context
import org.json.JSONObject

/** Local state. The credential is stored only as Keystore-encrypted text; reminders and the done-map are plain JSON. */
class Store(ctx: Context) {
    private val sp = ctx.applicationContext.getSharedPreferences("agent_companion", Context.MODE_PRIVATE)

    var server: String?
        get() = sp.getString("server", null)
        set(v) = sp.edit().putString("server", v).apply()

    var token: String?
        get() = sp.getString("token", null)?.let { blob ->
            try { Vault.decrypt(blob) } catch (e: Exception) { null }
        }
        set(v) {
            if (v == null) sp.edit().remove("token").apply()
            else sp.edit().putString("token", Vault.encrypt(v)).apply()
        }

    var deviceId: Long
        get() = sp.getLong("device_id", -1L)
        set(v) = sp.edit().putLong("device_id", v).apply()

    var pollSeconds: Int
        get() = sp.getInt("poll_seconds", 30)
        set(v) = sp.edit().putInt("poll_seconds", v.coerceIn(10, 300)).apply()

    var lastStatus: String
        get() = sp.getString("last_status", "Not paired.") ?: "Not paired."
        set(v) = sp.edit().putString("last_status", v).apply()

    val isPaired: Boolean get() = server != null && token != null

    // reminders: { "<commandId>": {"title":..., "body":..., "at_ms": 123} }
    fun reminders(): JSONObject = JSONObject(sp.getString("reminders", "{}") ?: "{}")
    fun saveReminders(o: JSONObject) = sp.edit().putString("reminders", o.toString()).apply()

    // done: { "<commandId>": {"status":..., "detail":...} } so a redelivered command is never run twice
    fun done(): JSONObject = JSONObject(sp.getString("done", "{}") ?: "{}")
    fun saveDone(o: JSONObject) = sp.edit().putString("done", o.toString()).apply()

    fun clearAll() {
        sp.edit().clear().apply()
        Vault.deleteKey()
    }

    companion object {
        /** One lock for all read-modify-write cycles (the foreground poller and WorkManager can overlap). */
        val LOCK = Any()
    }
}
