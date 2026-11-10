// Unit tests for static/voice.js using FAKE browser APIs (mocks). They prove the state machine logic only;
// they do not prove real microphone, permission prompts or speech engines on a phone.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const VoiceController = require("../static/voice.js");

class FakeRecognition {
  static instances = [];
  constructor() { this.started = false; this.aborted = false; this.stopped = false; FakeRecognition.instances.push(this); }
  start() { if (FakeRecognition.failStart) throw new Error("InvalidStateError"); this.started = true; }
  stop() { this.stopped = true; }
  abort() { this.aborted = true; }
  emit(results, resultIndex = 0) { this.onresult({ resultIndex, results }); }
}
const res = (transcript, isFinal) => Object.assign([{ transcript }], { isFinal });

class FakeUtterance { constructor(text) { this.text = text; } }
class FakeSynth {
  constructor(voices = []) { this.spoken = []; this.cancelled = 0; this.voices = voices; }
  getVoices() { return this.voices; }
  speak(u) { this.spoken.push(u); }
  cancel() { this.cancelled++; }
}

function make(overrides = {}) {
  FakeRecognition.instances = [];
  FakeRecognition.failStart = false;
  const log = { states: [], interim: [], final: [], errors: [], speakEnd: 0 };
  const synth = overrides.synth === undefined ? new FakeSynth() : overrides.synth;
  const vc = new VoiceController(Object.assign({
    SpeechRecognition: FakeRecognition, speechSynthesis: synth, Utterance: synth ? FakeUtterance : null,
    lang: "en-US",
    onState: (s) => log.states.push(s), onInterim: (t) => log.interim.push(t), onFinal: (t) => log.final.push(t),
    onError: (c, m) => log.errors.push([c, m]), onSpeakEnd: () => { log.speakEnd++; }
  }, overrides));
  return { vc, log, synth, rec: () => FakeRecognition.instances[FakeRecognition.instances.length - 1] };
}

// 1. microphone permission granted -> recognition starts
test("start() begins listening with the selected language", () => {
  const { vc, log, rec } = make({ lang: "ur-PK" });
  assert.equal(vc.start(), true);
  assert.equal(vc.state, "listening");
  assert.equal(rec().started, true);
  assert.equal(rec().lang, "ur-PK");
  assert.deepEqual(log.states, ["listening"]);
});

// 2. microphone permission denied
test("permission denied surfaces a clear error and no transcript", () => {
  const { vc, log, rec } = make();
  vc.start();
  rec().onerror({ error: "not-allowed" });
  assert.equal(vc.state, "error");
  assert.equal(log.final.length, 0);
  assert.equal(log.errors[0][0], "not-allowed");
  assert.match(log.errors[0][1], /permission was denied/i);
  assert.match(log.errors[0][1], /HTTPS/);
  assert.equal(vc.start(), true, "can retry after an error");
});

// 3. recognition success (interim then final, delivered exactly once)
test("interim text is reported and the final transcript is delivered once", () => {
  const { vc, log, rec } = make();
  vc.start();
  rec().emit([res("set a remi", false)]);
  rec().emit([res("set a reminder", true)]);
  rec().emit([res("set a reminder", true)]);       // duplicate event from the engine
  rec().onend();                                    // engine ends after the result
  assert.deepEqual(log.interim, ["set a remi"]);
  assert.deepEqual(log.final, ["set a reminder"]);
  assert.equal(vc.state, "processing");
  assert.equal(rec().stopped, true);
  assert.equal(log.errors.length, 0);
  vc.done();
  assert.equal(vc.state, "idle");
});

// 4. recognition timeout / failure
test("ending without any result reports no-speech", () => {
  const { vc, log, rec } = make();
  vc.start();
  rec().onend();
  assert.equal(vc.state, "error");
  assert.equal(log.errors[0][0], "no-speech");
});

test("network, audio-capture and language errors have specific messages", () => {
  for (const code of ["network", "audio-capture", "language-not-supported", "service-not-allowed"]) {
    const { vc, log, rec } = make();
    vc.start();
    rec().onerror({ error: code });
    assert.equal(log.errors[0][0], code);
    assert.notEqual(log.errors[0][1], "Voice error: " + code, code);
  }
  assert.match(VoiceController.messageFor("network"), /internet connection/);
});

test("start() throwing is reported, not crashed on", () => {
  const { vc, log } = make();
  FakeRecognition.failStart = true;
  assert.equal(vc.start(), false);
  assert.equal(log.errors[0][0], "start-failed");
});

// duplicate-submission protection
test("repeated start() calls while listening or processing are ignored", () => {
  const { vc, rec } = make();
  assert.equal(vc.start(), true);
  assert.equal(vc.start(), false);
  assert.equal(FakeRecognition.instances.length, 1);
  rec().emit([res("hello", true)]);
  assert.equal(vc.state, "processing");
  assert.equal(vc.start(), false);
  assert.equal(FakeRecognition.instances.length, 1);
});

test("events from a cancelled session are ignored", () => {
  const { vc, log, rec } = make();
  vc.start();
  const old = rec();
  assert.equal(vc.stop(), true);
  assert.equal(old.aborted, true);
  old.emit([res("ghost", true)]);
  old.onend();
  assert.equal(log.final.length, 0);
  assert.equal(log.errors.length, 0);
  assert.equal(vc.state, "idle");
});

test("empty final result is ignored", () => {
  const { vc, log, rec } = make();
  vc.start();
  rec().emit([res("   ", true)]);
  assert.equal(log.final.length, 0);
  assert.equal(vc.state, "listening");
});

// 5. assistant response spoken aloud
test("speak() reads cleaned text in the selected language, then returns to idle", () => {
  const synth = new FakeSynth([{ lang: "ur-PK", name: "Urdu" }, { lang: "en-US", name: "English" }]);
  const { vc, log } = make({ synth, lang: "ur-PK" });
  assert.equal(vc.speak("**Done.** See [docs](https://x.example/a) and `code`."), true);
  assert.equal(vc.state, "speaking");
  const u = synth.spoken[0];
  assert.equal(u.text, "Done. See docs and code.");
  assert.equal(u.lang, "ur-PK");
  assert.equal(u.voice.name, "Urdu");
  u.onend();
  assert.equal(vc.state, "idle");
  assert.equal(log.speakEnd, 1);
});

test("long replies are chunked and spoken in order", () => {
  const { vc, synth } = make();
  const sentence = "This is a fairly long sentence used for testing chunking behaviour. ";
  vc.speak(sentence.repeat(10));
  const seen = [];
  while (synth.spoken.length > seen.length) {
    const u = synth.spoken[seen.length];
    assert.ok(u.text.length <= 220, "chunk too long: " + u.text.length);
    seen.push(u);
    u.onend();
  }
  assert.ok(seen.length > 2);
  assert.equal(vc.state, "idle");
});

test("very long text is shortened with a pointer to the screen", () => {
  const { vc, synth } = make({ maxSpeakChars: 100 });
  vc.speak("word ".repeat(100));
  const all = [];
  while (synth.spoken.length > all.length) { const u = synth.spoken[all.length]; all.push(u.text); u.onend(); }
  assert.match(all.join(" "), /The rest is on screen\.$/);
});

test("code blocks and urls are not read out", () => {
  assert.equal(VoiceController.stripForSpeech("a\n```py\nprint(1)\n```\nb https://example.com/x"), "a (code omitted) b link");
});

// 6. speech interrupted by the user
test("stopSpeaking() cancels speech and later chunks never play", () => {
  const { vc, synth, log } = make();
  vc.speak("First sentence here. ".repeat(30));
  const first = synth.spoken[0];
  assert.equal(vc.stopSpeaking(), true);
  assert.equal(synth.cancelled >= 1, true);
  assert.equal(vc.state, "idle");
  first.onerror({ error: "interrupted" });          // browsers report cancellation as an error event
  first.onend();                                    // and may also fire end
  assert.equal(synth.spoken.length, 1, "no further chunk after stop");
  assert.equal(log.errors.length, 0, "an interruption is not an error");
});

test("starting to listen interrupts speech so the assistant does not hear itself", () => {
  const { vc, synth } = make();
  vc.speak("Talking now. ".repeat(5));
  vc.start();
  assert.equal(vc.state, "listening");
  assert.equal(synth.cancelled >= 1, true);
});

test("a real synthesis failure is reported", () => {
  const { vc, synth, log } = make();
  vc.speak("Hello there");
  synth.spoken[0].onerror({ error: "synthesis-failed" });
  assert.equal(log.errors[0][0], "speech-failed");
  assert.equal(vc.state, "idle");
});

// 7. text-only fallback
test("without SpeechRecognition the controller reports unsupported and never throws", () => {
  const { vc, log } = make({ SpeechRecognition: null });
  assert.equal(vc.canListen, false);
  assert.equal(vc.start(), false);
  assert.equal(log.errors[0][0], "unsupported");
});

test("without speechSynthesis speak() returns false", () => {
  const { vc } = make({ synth: null });
  assert.equal(vc.canSpeak, false);
  assert.equal(vc.speak("hi"), false);
  assert.equal(vc.stopSpeaking(), false);
});

test("hasVoiceFor reports unknown, missing and present voices", () => {
  assert.equal(make().vc.hasVoiceFor("ur-PK"), null);
  const synth = new FakeSynth([{ lang: "en-GB" }, { lang: "hi_IN" }]);
  const { vc } = make({ synth });
  assert.equal(vc.hasVoiceFor("en-US"), true);
  assert.equal(vc.hasVoiceFor("hi-IN"), true);
  assert.equal(vc.hasVoiceFor("ur-PK"), false);
});

// Watchdog (fake timers, no real waiting)
function fakeTimers() {
  const t = { fns: new Map(), n: 0, cleared: [] };
  t.setTimeout = (fn, ms) => { t.n++; t.fns.set(t.n, { fn, ms }); return t.n; };
  t.clearTimeout = (id) => { t.cleared.push(id); t.fns.delete(id); };
  t.fire = () => { for (const [id, { fn }] of [...t.fns]) { t.fns.delete(id); fn(); } };
  return t;
}

test("watchdog: an engine that never answers ends with a timeout error and aborts", () => {
  const timers = fakeTimers();
  const { vc, log, rec } = make({ setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
  vc.start();
  assert.equal([...timers.fns.values()][0].ms, 20000);
  timers.fire();
  assert.equal(vc.state, "error");
  assert.equal(log.errors[0][0], "timeout");
  assert.equal(rec().aborted, true);
  assert.equal(log.final.length, 0);
});

test("watchdog: a final result cancels the timer, so no late timeout appears", () => {
  const timers = fakeTimers();
  const { vc, log, rec } = make({ setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
  vc.start();
  rec().emit([res("hello", true)]);
  assert.equal(timers.fns.size, 0);
  timers.fire();
  assert.deepEqual(log.final, ["hello"]);
  assert.equal(log.errors.length, 0);
});

test("watchdog: manual stop cancels the timer", () => {
  const timers = fakeTimers();
  const { vc, log } = make({ setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout });
  vc.start();
  vc.stop();
  assert.equal(timers.fns.size, 0);
  timers.fire();
  assert.equal(vc.state, "idle");
  assert.equal(log.errors.length, 0);
});
