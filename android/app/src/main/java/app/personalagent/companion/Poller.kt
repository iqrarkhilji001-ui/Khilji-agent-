package app.personalagent.companion

import android.content.Context
import android.os.Build
import org.json.JSONObject

/** One poll cycle: send state, run each command at most once (by id), report the real outcome. */
object Poller {
    class Outcome(val ran: Int, val message: String)

    fun handle(ctx: Context, store: Store, cmd: JSONObject): Pair<String, String> {
        val id = cmd.optLong("id", -1)
        val key = id.toString()
        synchronized(Store.LOCK) {
            val prev = store.done().optJSONObject(key)
            if (prev != null) return prev.optString("status") to prev.optString("detail")   // redelivered: repeat outcome
        }
        val outcome: Pair<String, String> = try {
            "completed" to Actions.run(ctx, id, cmd.optString("action"), cmd.optJSONObject("args") ?: JSONObject(), store)
        } catch (e: ActionException) {
            "failed" to (e.message ?: "failed")
        } catch (e: Exception) {
            "failed" to "Unexpected error: ${e.javaClass.simpleName}"
        }
        synchronized(Store.LOCK) {
            val done = store.done()
            done.put(key, JSONObject().put("status", outcome.first).put("detail", outcome.second.take(400)))
            val keys = done.keys().asSequence().toList()
            if (keys.size > 200) keys.take(keys.size - 200).forEach { done.remove(it) }
            store.saveDone(done)
        }
        return outcome
    }

    /** Throws ApiException (isRevoked when the server no longer accepts this phone). */
    fun pollOnce(ctx: Context): Outcome {
        val store = Store(ctx)
        val server = store.server
        val token = store.token
        if (server == null || token == null) return Outcome(0, "Not paired.")
        val state = JSONObject()
            .put("notifications_enabled", Actions.notificationsAllowed(ctx))
            .put("pending_reminders", synchronized(Store.LOCK) { store.reminders().length() }.coerceAtMost(1000))
            .put("android_sdk", Build.VERSION.SDK_INT)
            .put("app_version", "0.1.0")
        val reply = Api.post(server, "/api/companion/poll", token, JSONObject().put("state", state))
        store.pollSeconds = reply.optInt("poll_seconds", store.pollSeconds)
        val cmds = reply.optJSONArray("commands")
        var ran = 0
        if (cmds != null) {
            for (i in 0 until cmds.length()) {
                val cmd = cmds.optJSONObject(i) ?: continue
                val (status, detail) = handle(ctx, store, cmd)
                try {
                    Api.post(server, "/api/companion/commands/${cmd.optLong("id")}/result", token,
                        JSONObject().put("status", status).put("detail", detail))
                } catch (e: ApiException) {
                    if (e.isRevoked) throw e   // otherwise the server redelivers and we repeat the stored outcome
                }
                ran++
            }
        }
        return Outcome(ran, "Connected. Ran $ran command(s).")
    }

    fun pair(ctx: Context, serverInput: String, code: String, name: String): String {
        val store = Store(ctx)
        val base = Api.normalizeServer(serverInput)
        val res = Api.post(base, "/api/companion/pair", null, JSONObject().put("code", code.trim()).put("name", name))
        store.server = base
        store.token = res.getString("token")
        store.deviceId = res.optLong("device_id", -1)
        store.pollSeconds = res.optInt("poll_seconds", 30)
        return "Paired. Credential valid until ${res.optString("expires_at")}."
    }

    /** Tell the server, then forget everything locally. Local cleanup happens even if the server is unreachable. */
    fun unpair(ctx: Context) {
        val store = Store(ctx)
        val server = store.server
        val token = store.token
        try {
            if (server != null && token != null) Api.post(server, "/api/companion/unpair", token, JSONObject())
        } catch (e: ApiException) {
            // unreachable or already revoked: still disconnect locally
        }
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
        val reminders = synchronized(Store.LOCK) { store.reminders() }
        for (key in reminders.keys().asSequence().toList()) {
            val i = android.content.Intent(ctx, ReminderReceiver::class.java).putExtra(Actions.EXTRA_ID, key.toLong())
            am.cancel(android.app.PendingIntent.getBroadcast(ctx, key.toLong().toInt(), i,
                android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT))
        }
        store.clearAll()
    }
}
