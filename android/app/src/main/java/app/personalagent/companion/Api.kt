package app.personalagent.companion

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

class ApiException(val http: Int, val reason: String?, message: String) : Exception(message) {
    /**
     * A 401 means the server refused this phone's credential: revoked, expired, or unknown (free hosts can wipe
     * their database). Callers stop polling and ask the user to pair again instead of retrying with a stale
     * credential, which would also trip the server's lockout.
     */
    val isRevoked: Boolean get() = http == 401
}

/** HTTPS-only JSON client. The credential goes in the Authorization header, never in a URL or a log line. */
object Api {
    fun normalizeServer(input: String): String {
        val u = try { URL(input.trim()) } catch (e: Exception) { throw ApiException(0, null, "Invalid server address.") }
        if (u.protocol != "https" || u.host.isNullOrEmpty()) {
            throw ApiException(0, null, "The server address must start with https://")
        }
        if (u.userInfo != null || u.query != null || u.ref != null) {
            throw ApiException(0, null, "The server address must not contain credentials or a query.")
        }
        val port = if (u.port == -1) "" else ":" + u.port
        return "https://" + u.host + port
    }

    fun post(base: String, path: String, token: String?, body: JSONObject): JSONObject {
        val url = URL(normalizeServer(base) + path)
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.connectTimeout = 15_000
            conn.readTimeout = 20_000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("User-Agent", "AgentCompanion/0.1.0")
            if (token != null) conn.setRequestProperty("Authorization", "Bearer $token")
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val json = try { JSONObject(if (text.isBlank()) "{}" else text) } catch (e: Exception) { JSONObject() }
            if (code !in 200..299) {
                throw ApiException(code, json.optString("reason").ifEmpty { null }, json.optString("error", "HTTP $code"))
            }
            return json
        } catch (e: IOException) {
            throw ApiException(0, null, "Could not reach the server (${e.javaClass.simpleName}).")
        } finally {
            conn.disconnect()
        }
    }
}
