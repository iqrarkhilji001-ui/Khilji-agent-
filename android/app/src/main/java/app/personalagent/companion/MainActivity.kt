package app.personalagent.companion

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** Minimal single-screen UI built in code (no layout XML). Shows connection state and every control the user needs. */
class MainActivity : Activity() {
    private lateinit var store: Store
    private lateinit var status: TextView
    private lateinit var server: EditText
    private lateinit var code: EditText
    private lateinit var pairBtn: Button
    private lateinit var checkBtn: Button
    private lateinit var disconnectBtn: Button
    private val ui = Handler(Looper.getMainLooper())
    @Volatile private var foreground = false
    // Each resume starts a new loop generation; an older loop notices it is stale and exits, so a quick
    // pause/resume can never leave two loops polling at once.
    @Volatile private var generation = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad, pad, pad) }

        root.addView(TextView(this).apply { text = "Agent Companion"; textSize = 22f })
        status = TextView(this).apply { textSize = 16f; setPadding(0, pad / 2, 0, pad / 2) }
        root.addView(status)

        server = EditText(this).apply {
            hint = "Server address (https://…)"; inputType = InputType.TYPE_TEXT_VARIATION_URI; setSingleLine(true)
        }
        code = EditText(this).apply { hint = "Pairing code (ABCD-2345)"; setSingleLine(true) }
        pairBtn = button("Pair this phone") { pair() }
        checkBtn = button("Check now") { checkNow() }
        disconnectBtn = button("Disconnect and forget this phone") { disconnect() }
        root.addView(server); root.addView(code); root.addView(pairBtn); root.addView(checkBtn)
        root.addView(button("Allow notifications") { askNotifications() })
        root.addView(button("Allow exact reminders") { askExactAlarms() })
        root.addView(disconnectBtn)
        root.addView(TextView(this).apply {
            textSize = 13f; setPadding(0, pad, 0, 0)
            text = "This app can only: show notifications, schedule or cancel its own reminders, and show a link " +
                "you tap to open. It cannot read messages, make calls, use the microphone, location or other apps. " +
                "While the app is closed, new commands arrive at most every ~15 minutes (Android limit)."
        })
        setContentView(ScrollView(this).apply { addView(root) })
        render()
        if (store.isPaired) PollWorker.schedule(this)
    }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; setOnClickListener { onClick() }
    }

    private fun render() {
        val paired = store.isPaired
        val notif = Actions.notificationsAllowed(this)
        status.text = (if (paired) "Paired with ${store.server}\n" else "Not paired.\n") + store.lastStatus +
            "\nNotifications: " + (if (notif) "allowed" else "NOT allowed") +
            "\nReminders pending on this phone: " + store.reminders().length()
        server.visibility = if (paired) View.GONE else View.VISIBLE
        code.visibility = server.visibility
        pairBtn.visibility = server.visibility
        checkBtn.visibility = if (paired) View.VISIBLE else View.GONE
        disconnectBtn.visibility = checkBtn.visibility
    }

    private fun runAsync(work: () -> String) {
        Thread {
            val msg = try { work() } catch (e: ApiException) {
                if (e.isRevoked) { store.token = null; "This phone was disconnected by the server. Pair again." }
                else e.message ?: "Error"
            } catch (e: Exception) { "Unexpected error: ${e.javaClass.simpleName}" }
            store.lastStatus = msg
            ui.post { render() }
        }.start()
    }

    private fun pair() {
        val s = server.text.toString(); val c = code.text.toString()
        store.lastStatus = "Pairing…"; render()
        runAsync {
            val m = Poller.pair(this, s, c, Build.MODEL ?: "Android phone")
            PollWorker.schedule(this)
            m
        }
    }

    private fun checkNow() { store.lastStatus = "Checking…"; render(); runAsync { Poller.pollOnce(this).message } }

    private fun disconnect() {
        PollWorker.cancel(this)
        runAsync { Poller.unpair(this); "Disconnected. This phone's credential was deleted." }
    }

    private fun askNotifications() {
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1)
        else startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
    }

    private fun askExactAlarms() {
        if (Build.VERSION.SDK_INT >= 31) {
            startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:$packageName")))
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        render()
    }

    // While the screen is open, poll at the server's interval. In the background WorkManager takes over.
    override fun onResume() {
        super.onResume(); foreground = true; render()
        val mine = ++generation
        Thread {
            while (foreground && mine == generation) {
                if (store.isPaired) {
                    try { store.lastStatus = Poller.pollOnce(this).message } catch (e: ApiException) {
                        if (e.isRevoked) store.token = null
                        store.lastStatus = e.message ?: "Error"
                    } catch (e: Exception) { /* try again next round */ }
                    ui.post { render() }
                }
                try { Thread.sleep(store.pollSeconds * 1000L) } catch (e: InterruptedException) { return@Thread }
            }
        }.start()
    }

    override fun onPause() { super.onPause(); foreground = false }
}
