package app.personalagent.companion

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getLongExtra(Actions.EXTRA_ID, -1L)
        if (id >= 0) Actions.fireReminder(context, Store(context), id)
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val store = Store(context)
        if (store.isPaired) {
            Actions.restoreAfterBoot(context, store)
            PollWorker.schedule(context)
        }
    }
}
