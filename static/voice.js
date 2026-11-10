/* Voice controller: wraps the browser's SpeechRecognition and speechSynthesis behind a small state machine.
 *
 * States: idle -> listening -> processing -> (speaking ->) idle, or error.
 * The browser APIs are injected, so the logic is unit-tested in Node with fakes (tests/voice.test.js).
 *
 * Honest limits: browser speech recognition usually needs an internet connection (Chrome sends audio to a
 * speech service), needs HTTPS or localhost, and supports languages unevenly. Nothing here is verified on a
 * physical Android device.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.VoiceController = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ERROR_MESSAGES = {
    "not-allowed": "Microphone permission was denied. In Chrome, tap the lock icon next to the address, allow the microphone, then try again. Voice also needs HTTPS.",
    "service-not-allowed": "This browser blocked speech recognition. Check the microphone permission and that the page uses HTTPS.",
    "no-speech": "I did not hear anything. Tap the microphone and try again.",
  "timeout": "Listening timed out. Tap the microphone and try again, or type your message.",
    "audio-capture": "No microphone was found or it is in use by another app.",
    "network": "Speech recognition could not reach its online service. It usually needs an internet connection. You can type instead.",
    "language-not-supported": "This browser does not support speech recognition for the selected language. Choose another language or type.",
    "aborted": "Listening was cancelled.",
    "start-failed": "Could not start the microphone. Try again, or type instead.",
    "unsupported": "Voice input is not supported in this browser. Use Chrome on Android, or type below.",
    "speech-failed": "The browser could not speak the reply. The text is shown on screen."
  };

  function messageFor(code) {
    return ERROR_MESSAGES[code] || ("Voice error: " + code);
  }

  /** Remove markup that sounds bad when read aloud. */
  function stripForSpeech(text) {
    let t = String(text == null ? "" : text);
    t = t.replace(/```[\s\S]*?```/g, " (code omitted) ");
    t = t.replace(/`([^`]*)`/g, "$1");
    t = t.replace(/!\[[^\]]*\]\([^)]*\)/g, " ");
    t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
    t = t.replace(/https?:\/\/\S+/g, " link ");
    t = t.replace(/^\s{0,3}#{1,6}\s*/gm, "");
    t = t.replace(/^\s*[-*+]\s+/gm, "");
    t = t.replace(/[*_~]{1,3}/g, "");
    t = t.replace(/\s+/g, " ").trim();
    return t;
  }

  /** Split into short pieces; long utterances are unreliable on some mobile engines. */
  function chunkText(text, max) {
    max = max || 220;
    const sentences = text.split(/(?<=[.!?۔؟])\s+/);
    const chunks = [];
    let cur = "";
    const push = () => { if (cur) { chunks.push(cur); cur = ""; } };
    for (let s of sentences) {
      while (s.length > max) {
        let cut = s.lastIndexOf(" ", max);
        if (cut < max / 2) cut = max;
        push();
        chunks.push(s.slice(0, cut).trim());
        s = s.slice(cut).trim();
      }
      if (!s) continue;
      if ((cur + " " + s).trim().length > max) push();
      cur = (cur + " " + s).trim();
    }
    push();
    return chunks.filter(Boolean);
  }

  class VoiceController {
    /**
     * opts: { SpeechRecognition, speechSynthesis, Utterance, lang, maxSpeakChars,
     *         onState(state, info), onInterim(text), onFinal(text), onError(code, message), onSpeakEnd() }
     */
    constructor(opts) {
      opts = opts || {};
      this.SR = opts.SpeechRecognition || null;
      this.synth = opts.speechSynthesis || null;
      this.Utterance = opts.Utterance || null;
      this.lang = opts.lang || "en-US";
      this.maxSpeakChars = opts.maxSpeakChars || 1500;
      // Watchdog: some engines never fire onend/onerror (e.g. a stalled permission prompt). Injectable for tests.
      this.listenTimeoutMs = opts.listenTimeoutMs === undefined ? 20000 : opts.listenTimeoutMs;
      this._setTimer = opts.setTimeout || ((fn, ms) => setTimeout(fn, ms));
      this._clearTimer = opts.clearTimeout || ((id) => clearTimeout(id));
      this._watchdog = null;
      this.h = {
        onState: opts.onState || function () {},
        onInterim: opts.onInterim || function () {},
        onFinal: opts.onFinal || function () {},
        onError: opts.onError || function () {},
        onSpeakEnd: opts.onSpeakEnd || function () {}
      };
      this.state = "idle";
      this._rec = null;
      this._session = 0;
      this._gotFinal = false;
      this._speakToken = 0;
    }

    get canListen() { return !!this.SR; }
    get canSpeak() { return !!(this.synth && this.Utterance); }

    setLang(lang) { this.lang = lang; }

    /** true / false when voices are known, null when the browser has not loaded its voice list yet. */
    hasVoiceFor(lang) {
      if (!this.synth || typeof this.synth.getVoices !== "function") return null;
      const voices = this.synth.getVoices() || [];
      if (!voices.length) return null;
      const base = String(lang || this.lang).toLowerCase().split("-")[0];
      return voices.some((v) => String(v.lang || "").toLowerCase().split(/[-_]/)[0] === base);
    }

    _clearWatchdog() {
      if (this._watchdog !== null) { this._clearTimer(this._watchdog); this._watchdog = null; }
    }

    _set(state, info) {
      if (this.state === state) return;
      if (state !== "listening") this._clearWatchdog();
      this.state = state;
      this.h.onState(state, info);
    }

    _error(code) {
      this._set("error", code);
      this.h.onError(code, messageFor(code));
    }

    /** Begin listening. Ignored while already listening or while a result is being processed. */
    start() {
      if (!this.canListen) { this._error("unsupported"); return false; }
      if (this.state === "listening" || this.state === "processing") return false;
      this.stopSpeaking();
      let rec;
      try { rec = new this.SR(); } catch (e) { this._error("start-failed"); return false; }
      const session = ++this._session;
      this._gotFinal = false;
      this._rec = rec;
      rec.lang = this.lang;
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;

      rec.onresult = (ev) => {
        if (session !== this._session || this._gotFinal) return;
        let interim = "", finalText = "", hasFinal = false;
        for (let i = ev.resultIndex || 0; i < ev.results.length; i++) {
          const r = ev.results[i];
          const piece = (r[0] && r[0].transcript) || "";
          if (r.isFinal) { hasFinal = true; finalText += piece; } else { interim += piece; }
        }
        if (hasFinal) {
          finalText = finalText.trim();
          if (!finalText) return;
          this._gotFinal = true;                 // later events from the same session are ignored
          this._set("processing");
          try { rec.stop(); } catch (e) { /* already stopped */ }
          this.h.onFinal(finalText);
        } else if (interim.trim()) {
          this.h.onInterim(interim.trim());
        }
      };
      rec.onerror = (ev) => {
        if (session !== this._session) return;
        this._session++;                         // invalidate this session
        this._gotFinal = false;
        try { rec.abort(); } catch (e) { /* ignore */ }
        this._error((ev && ev.error) || "unknown");
      };
      rec.onend = () => {
        if (session !== this._session) return;
        if (this.state === "listening" && !this._gotFinal) {
          this._session++;
          this._error("no-speech");              // recognition ended without any result
        }
      };
      try {
        rec.start();
      } catch (e) {
        this._session++;
        this._error("start-failed");
        return false;
      }
      this._set("listening");
      if (this.listenTimeoutMs > 0) {
        this._watchdog = this._setTimer(() => {
          this._watchdog = null;
          if (session !== this._session || this.state !== "listening") return;
          this._session++;
          try { rec.abort(); } catch (e) { /* ignore */ }
          this._error("timeout");
        }, this.listenTimeoutMs);
      }
      return true;
    }

    /** Stop listening without delivering a result. */
    stop() {
      if (this.state !== "listening") return false;
      this._session++;
      try { this._rec && this._rec.abort(); } catch (e) { /* ignore */ }
      this._set("idle");
      return true;
    }

    /** The host calls this when it finished handling a transcript (or a typed message) and will not speak. */
    done() {
      if (this.state === "processing" || this.state === "error") this._set("idle");
    }

    /** Speak text aloud (markup removed, long text shortened). Returns false when speech is unavailable. */
    speak(text) {
      if (!this.canSpeak) return false;
      let clean = stripForSpeech(text);
      if (!clean) { this.done(); return false; }
      if (clean.length > this.maxSpeakChars) {
        clean = clean.slice(0, this.maxSpeakChars).replace(/\s+\S*$/, "") + ". The rest is on screen.";
      }
      const chunks = chunkText(clean);
      this.stopSpeaking();
      const token = ++this._speakToken;
      this._set("speaking");
      const finish = () => {
        if (token !== this._speakToken) return;
        this._set("idle");
        this.h.onSpeakEnd();
      };
      const voices = (this.synth.getVoices && this.synth.getVoices()) || [];
      const base = this.lang.toLowerCase().split("-")[0];
      const voice = voices.find((v) => String(v.lang || "").toLowerCase().split(/[-_]/)[0] === base) || null;
      const next = (i) => {
        if (token !== this._speakToken) return;
        if (i >= chunks.length) { finish(); return; }
        const u = new this.Utterance(chunks[i]);
        u.lang = this.lang;
        if (voice) u.voice = voice;
        u.onend = () => next(i + 1);
        u.onerror = (e) => {
          if (token !== this._speakToken) return;
          const code = e && e.error;
          if (code === "interrupted" || code === "canceled") return;   // user pressed stop
          this._speakToken++;
          this._set("idle");
          this.h.onError("speech-failed", messageFor("speech-failed"));
        };
        try { this.synth.speak(u); } catch (e) { u.onerror({ error: "synthesis-failed" }); }
      };
      next(0);
      return true;
    }

    stopSpeaking() {
      if (!this.synth) return false;
      const wasSpeaking = this.state === "speaking";
      this._speakToken++;
      try { this.synth.cancel(); } catch (e) { /* ignore */ }
      if (wasSpeaking) this._set("idle");
      return wasSpeaking;
    }
  }

  VoiceController.stripForSpeech = stripForSpeech;
  VoiceController.chunkText = chunkText;
  VoiceController.messageFor = messageFor;
  return VoiceController;
});
