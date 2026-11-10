package app.personalagent.companion

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import org.json.JSONObject
import java.net.URL

class ActionException(message: String) : Exception(message)

/**
 * The strict allowlist: notify, schedule_reminder, cancel_reminder, open_url. Anything else is refused.
 * Every function returns a human-readable detail ONLY after the Android API call returned without an error;
 * it never claims the user saw or tapped anything.
 */
object Actions {
    const val CHANNEL = "agent_messages"
    const val EXTRA_ID = "reminder_id"
    private const val LATE_REMINDER_MS = 3_600_000L

    fun run(ctx: Context, commandId: Long, action: String, args: JSONObject, store: Store): String = when (action) {
        "notify" -> notify(ctx, commandId.toInt(), args.optString("title"), args.optString("body"), null)
        "open_url" -> openUrl(ctx, commandId.toInt(), args.optString("url"))
        "schedule_reminder" -> schedule(ctx, store, commandId, args)
        "cancel_reminder" -> cancel(ctx, store, args.optLong("reminder_id", -1))
        else -> throw ActionException("Unsupported action; this app only runs its allowlist.")
    }

    private fun clean(s: String, max: Int) = s.filter { it >= ' ' || it == '\n' }.trim().take(max)

    fun notificationsAllowed(ctx: Context): Boolean {
        if (Build.VERSION.SDK_INT >= 33 &&
            ctx.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return false
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return nm.areNotificationsEnabled()
    }

    fun notify(ctx: Context, id: Int, title: String, body: String, tap: PendingIntent?): String {
        val t = clean(title, 80)
        if (t.isEmpty()) throw ActionException("A notification needs a title.")
        if (!notificationsAllowed(ctx)) {
            throw ActionException("Notifications are not allowed for this app. Open the app and tap 'Allow notifications'.")
        }
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Assistant messages", NotificationManager.IMPORTANCE_DEFAULT))
        val b = Notification.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(t)
            .setContentText(clean(body, 500))
            .setStyle(Notification.BigTextStyle().bigText(clean(body, 500)))
            .setAutoCancel(true)
        if (tap != null) b.setContentIntent(tap)
        nm.notify(id, b.build())
        return "notification posted to the Android notification manager"
    }

    fun validUrl(raw: String): Uri {
        val u = try { URL(raw) } catch (e: Exception) { throw ActionException("Not a valid link.") }
        if (u.protocol != "https" || u.userInfo != null || !u.host.contains('.') || raw.length > 1000 ||
            raw.any { it.isWhitespace() } || (u.port != -1 && u.port != 443)
        ) throw ActionException("Only plain https links with a public host name are allowed.")
        return Uri.parse(raw)
    }

    private fun openUrl(ctx: Context, id: Int, raw: String): String {
        val uri = validUrl(raw)
        val view = Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(ctx, id, view, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        notify(ctx, id, "Open link?", uri.toString(), pi)
        return "notification with a tap-to-open link was posted; nothing opens until you tap it"
    }

    private fun alarmIntent(ctx: Context, id: Long): PendingIntent {
        val i = Intent(ctx, ReminderReceiver::class.java).putExtra(EXTRA_ID, id)
        return PendingIntent.getBroadcast(ctx, id.toInt(), i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    /** Returns "exact" or "inexact". Throws if Android refuses the alarm. */
    fun setAlarm(ctx: Context, id: Long, atMs: Long): String {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pi = alarmIntent(ctx, id)
        val exact = Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()
        try {
            if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            return if (exact) "exact" else "inexact"
        } catch (e: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            return "inexact"
        }
    }

    private fun schedule(ctx: Context, store: Store, id: Long, args: JSONObject): String {
        val at = args.optLong("at_epoch_ms", -1)
        if (at <= System.currentTimeMillis()) throw ActionException("The reminder time is missing or already in the past.")
        val title = clean(args.optString("title"), 80)
        if (title.isEmpty()) throw ActionException("A reminder needs a title.")
        val mode = setAlarm(ctx, id, at)          // throws if Android refuses; nothing is saved in that case
        synchronized(Store.LOCK) {
            val all = store.reminders()
            all.put(id.toString(), JSONObject().put("title", title).put("body", clean(args.optString("body"), 500)).put("at_ms", at))
            store.saveReminders(all)
        }
        return "reminder scheduled with the Android alarm service ($mode). " +
            (if (mode == "inexact") "Exact alarms are not allowed, so it may be delayed; tap 'Allow exact reminders' in the app. " else "") +
            "Android confirms scheduling, not that it will ring."
    }

    private fun cancel(ctx: Context, store: Store, reminderId: Long): String {
        synchronized(Store.LOCK) {
            val all = store.reminders()
            if (!all.has(reminderId.toString())) throw ActionException("No pending reminder with that id on this phone (it may have already fired).")
            all.remove(reminderId.toString())
            store.saveReminders(all)
        }
        (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).cancel(alarmIntent(ctx, reminderId))
        return "reminder cancelled"
    }

    /** Called by the alarm receiver and after boot. */
    fun fireReminder(ctx: Context, store: Store, id: Long) {
        val r = synchronized(Store.LOCK) {
            val all = store.reminders()
            val item = all.optJSONObject(id.toString())
            if (item != null) { all.remove(id.toString()); store.saveReminders(all) }
            item
        } ?: return
        try {
            notify(ctx, id.toInt() + 1_000_000, r.optString("title"), r.optString("body"), null)
        } catch (e: ActionException) {
            // notifications were switched off after scheduling; nothing else we can do
        }
    }

    /** After a reboot all alarms are gone: re-register the future ones, fire recent ones, drop stale ones. */
    fun restoreAfterBoot(ctx: Context, store: Store) {
        val now = System.currentTimeMillis()
        val all = synchronized(Store.LOCK) { store.reminders() }
        for (key in all.keys().asSequence().toList()) {
            val r = all.optJSONObject(key) ?: continue
            val at = r.optLong("at_ms")
            when {
                at > now -> try { setAlarm(ctx, key.toLong(), at) } catch (e: Exception) { /* keep it stored */ }
                now - at <= LATE_REMINDER_MS -> fireReminder(ctx, store, key.toLong())
                else -> synchronized(Store.LOCK) { val a = store.reminders(); a.remove(key); store.saveReminders(a) }
            }
        }
    }
}
