package app.personalagent.companion

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequest
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Background polling. Android limits periodic work to once per 15 minutes and may delay it further in Doze or
 * battery saver, so commands can arrive late while the app is closed. Reminders that were already delivered are
 * scheduled with the system alarm service and do not depend on this worker.
 */
class PollWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        val store = Store(applicationContext)
        if (!store.isPaired) return Result.success()
        return try {
            val out = Poller.pollOnce(applicationContext)
            store.lastStatus = out.message
            Result.success()
        } catch (e: ApiException) {
            if (e.isRevoked) {
                store.token = null
                store.lastStatus = "This phone was disconnected by the server (${e.reason}). Pair it again."
                Result.failure()
            } else {
                store.lastStatus = e.message ?: "Server error"
                Result.retry()
            }
        } catch (e: Exception) {
            Result.retry()
        }
    }

    companion object {
        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequest.Builder(PollWorker::class.java, 15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork("agent_poll", ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun cancel(ctx: Context) {
            WorkManager.getInstance(ctx).cancelUniqueWork("agent_poll")
        }
    }
}
