# Specification: Live Conversation (Hands-Free Voice)

**Document:** `docs/specs/SPEC_LIVE_CONVERSATION.md`  
**Status:** Draft / Specification  
**Primary modules:** `eidos/voice/` (new session controller), `eidos/core/loop.py`, `eidos/ui/pill.py`, `eidos/ui/app.py`, `eidos/gateway/server.py`, `eidos/gateway/tts_live.py`, `web/src/components/ChatView.tsx`

---

## 1. Executive Summary

### 1.1. Problem

Voice today is push-to-talk plus read-aloud. The user pushes a button, Parakeet transcribes the finished clip, `AgentLoop.run_task` runs to completion, and Kokoro speaks the finished summary from `_complete_run`. The same wait exists on the desktop pill and in the web app. A hands-free loop on that path would be a like a walkie-talkie.

### 1.2. Decision

This feature stays on the current speech pipeline. Parakeet listens. The existing agent loop thinks and uses tools. Kokoro speaks. It does not add a speech-to-speech model.

Meta Model API has no speech-to-speech endpoint. Chutes AudioDojo is another speech-to-text and text-to-speech pipeline. A full-duplex model (Moshi locally, or GPT-Live / Gemini Live / Grok Voice / Nova Sonic hosted) is a different project and is out of scope here.

### 1.3. Solution

Add a **Live Conversation** mode the user can turn on and off. While it is on:

1. The active surface listens until the user stops talking.
2. That utterance is transcribed and submitted as the next chat turn.
3. Kokoro starts on the first spoken sentence of the reply, while the rest of the turn may still be running.
4. When playback has finished and the agent is idle, the microphone opens again.

One session in the agent process owns the mode. The desktop GUI and the web app are two surfaces on that session. Typed chat and push-to-talk keep their current behavior when the mode is off.

---

## 2. Current Behavior This Builds On

| Piece | Where | What it does today |
|---|---|---|
| Desktop capture | `eidos/voice/audio_capture.py` | `arecord` (or `ffmpeg`) writes one 16 kHz mono WAV. Recording ends only when the user toggles the mic. |
| Desktop turn | `eidos/voice/voice_manager.py`, `eidos/ui/app.py` | Hotkey `<ctrl>+<alt>+<space>` or the pill mic. `stop_listening` transcribes, then the UI may submit when `voice.auto_submit_transcription` is true. Starting the mic calls `stop_speaking`. |
| Web capture | `web/src/components/ChatView.tsx` | `MediaRecorder` posts the whole blob to `POST /api/stt`. `getUserMedia` already requests echo cancellation, noise suppression, and auto gain. Slices arrive every 500 ms and are still transcribed only after stop. |
| Transcription | `eidos/voice/stt.py`, `eidos/gateway/server.py` `_handle_stt` | Local Parakeet. The web route accepts WAV, WebM, and Ogg and converts before transcribe. |
| Speech after the task | `eidos/core/loop.py` `_speak_task_summary` | Called from `_complete_run` with the finished headline. `model.chat` returns only when the step is done. |
| Desktop playback | `eidos/voice/speech_stream.py` | `speak_text` chunks a **complete** reply. `begin` / `feed` / `finish` can accept text as it arrives and are unused by the loop. The first window waits for 80–140 characters (`eidos/voice/chunker.py`), so a one-sentence reply never flushes early. |
| Web playback | `eidos/gateway/tts_live.py` | One continuous WAV for the phone, including while the screen is off. The session wants the reply text up front, then synthesizes. |

---

## 3. User-Facing Behavior

### 3.1. Toggle

- Desktop: a Live Conversation control on the status pill, separate from Auto-Read and from push-to-talk.
- Web: the same control in the chat composer.
- Default is off. The choice lasts for the running process. It is not required to survive restart. `voice.conversation.enabled` in config sets the initial value.
- Turning it off stops the microphone, stops any in-progress reply speech that the mode started, and leaves the chat transcript as it is.
- Push-to-talk, Auto-Read, and typed send keep working while the mode is off. While the mode is on, push-to-talk is the interrupt control (section 6), not a second independent recorder.

### 3.2. A turn

Heard from the user:

1. The surface shows **Listening**.
2. Speech starts. A silence of `silence_ms` (default 700) after the last voice energy ends the utterance. Utterances shorter than `min_speech_ms` (default 400) are discarded and listening continues.
3. The surface shows **Transcribing**, then the transcript appears as a normal user message in the active conversation.
4. An empty transcript does not call the agent. Listening opens again.

Spoken by the agent:

1. The surface shows **Thinking** until the first speakable sentence exists, then **Speaking**.
2. That sentence is spoken as soon as it is complete. Later sentences follow through the same playback stream.
3. Tool work may still be running. The surface shows **Working** when playback has ended and the task has not.
4. The microphone stays closed during **Speaking** and **Working**.
5. When the task is idle and playback has ended, the surface returns to **Listening** without another button press.

The transcript and the reply are the same conversation history typed chat already uses. Thoughts, tool arguments, and recall text stay in the existing thought UI. Kokoro does not read them.

### 3.3. One owner

Desktop and web share one agent. Only one surface may hold Live Conversation.

- Starting it on one surface turns it off on the other and broadcasts `conversation_mode` (section 8).
- If the user starts it on a surface whose microphone cannot be opened, the mode does not arm and the other surface is left as it was.
- Closing the web client or the desktop window releases the hold.

---

## 4. Session Controller

New type `LiveConversation` in `eidos/voice/live_conversation.py`, owned by `VoiceManager`.

It is the only place that decides "listen again." The pill and the web app report audio events and render state. They do not each invent a turn loop.

### 4.1. States

`off` → `listening` → `transcribing` → `thinking` → `speaking` → `working` → `listening`

`speaking` and `working` can overlap in time (audio still playing while tools run). The reported state is `speaking` while playback is active, otherwise `working` while `AgentLoop` is busy, otherwise `listening` when the mode is on.

Any state returns to `off` when the user disables the mode, the kill switch trips, or the owning surface disconnects.

### 4.2. Responsibilities

- Track `owner`: `desktop` or `web`.
- Accept endpoint events from the owner: speech started, utterance closed (path or bytes), discarded (too short).
- Run Parakeet on the utterance off the UI thread.
- Submit the text through the same entry the desktop already uses for an auto-submitted transcript (`AgentLoop.run_task` on the desktop path, gateway task dispatch for a web-originated turn). The turn is marked `voice_conversation=True`.
- Receive speakable deltas for that turn and push them to the owner's playback adapter.
- Re-enter `listening` only when the mode is still on, the owner is unchanged, playback is idle, and the agent is idle.
- Ignore a second utterance while state is not `listening`.

### 4.3. What stays put

`AudioRecorder`, `ParakeetSTT`, `KokoroTTS`, `SpeechStreamer`, and `LiveTtsHub` stay the engines. The controller sequences them.

---

## 5. Hearing the End of a Turn

### 5.1. Desktop

`AudioRecorder` cannot endpoint. It records until something calls stop. Live Conversation needs a capture that exposes PCM while it is recording, so silence can end the turn.

- 16 kHz, mono, signed 16-bit, same contract Parakeet already expects.
- Voice activity is energy over a short window, with a noise floor taken from the first 300 ms. A later Silero VAD swap is allowed behind the same "speech / silence" events. The controller does not care which detector fired.
- On close, write the WAV the existing `ParakeetSTT.transcribe` already accepts, then delete it after transcription, as `stop_listening` does today.
- The push-to-talk path may keep `arecord` until it is convenient to share this capture. Live Conversation does not have to delete the `arecord` path in the first cut.

### 5.2. Web

The browser already holds the microphone and already asks for echo cancellation.

- An `AnalyserNode` (or a later WASM VAD) watches the same `MediaStream` as `MediaRecorder`.
- Silence for `silence_ms` calls the existing stop path, which posts the blob to `POST /api/stt`.
- The server transcribes. It does not run its own endpoint detector for web audio, because the clip arrives only after the browser has closed the turn.
- After a successful submit, the page waits for session state. It does not start another recorder until the broadcast says `listening`.

---

## 6. Echo and Interrupt

There is no acoustic echo canceller on the desktop `arecord` path. Kokoro playing through the speakers will be transcribed if the mic is open.

Rules:

- The microphone is open only in `listening`.
- Desktop playback and web playback both count as "playback active."
- Interrupt is explicit. The pill mic, the push-to-talk hotkey, and the web mic button stop playback (`VoiceManager.stop_speaking` and the live TTS session), cancel the open utterance if one is recording, and return to `listening`. They do not submit a half-heard clip.
- Automatic barge-in from speaker audio is out of scope. Headphones are the supported way to avoid hearing the reply in the room. The web constraints already include `echoCancellation: true`; that is not treated as permission to listen during playback in v1.
- The kill switch already stops speech (`AgentLoop._on_kill_stop_voice`). It also forces the session to `off`.

---

## 7. Speaking the First Sentence Early

### 7.1. Why the current hook is too late

`_speak_task_summary` runs after the task summary exists. A voice turn that waits for that hook has the same silence as today, with the button removed. Early speech is part of this feature, not a follow-up.

### 7.2. What may be spoken

Only user-visible reply prose, after `text_for_speech`. Never speak:

- `thinking` / reasoning traces
- tool-call JSON or tool results
- `<recall>` tags or retrieved memory text
- markdown syntax, paths, or code fences (the existing sanitizer)

### 7.3. How text gets to Kokoro

`model.chat` currently returns a finished `ModelResponse`. The recall interceptor already consumes a token stream before that return (`execute_chat_with_recall` / `execute_openai_chat_with_recall`).

For a turn with `voice_conversation=True`:

- Add an optional `on_visible_delta(text)` beside the existing recall callback, fired for visible reply tokens and not for thinking or tool-call deltas.
- `LiveConversation` appends those deltas. On each sentence boundary (`.`, `!`, `?`, or a newline that the sanitizer keeps), it flushes that sentence to playback immediately, even when it is shorter than `HEAD1_MIN` (80). The 80–140 growing window remains the rule for Auto-Read of a finished long reply. Live Conversation's first flush is the first sentence.
- `SpeechStreamer.feed` is the desktop sink. The web sink appends into the open `LiveTtsHub` WAV rather than starting a second clip. The phone path stays one audio element so screen-off playback keeps working.
- When the step ends, `finish` flushes any tail.
- `_speak_task_summary` must not speak that turn again. The finished headline is still stored and shown. It is not a second performance.

### 7.4. Tool turns

The voice-mode instruction is added only to turns marked `voice_conversation=True`. It tells the model to put one or two plain spoken sentences first, and to say what it is about to do before a tool call, with no lists and no tool names.

If the first step returns a tool call and no speakable sentence, the controller speaks one fixed line: "Working on that." It does not invent a model call to produce that line. Later visible sentences are spoken when they arrive. The mic stays closed until the task is idle (section 3.2).

### 7.5. Stop

User stop, kill switch, or mode off discards queued clips and aborts the in-flight model call the way Stop already does. A sentence already handed to the sound device may finish its current clip. Nothing further is queued.

---

## 8. Gateway Events

Broadcast on the existing gateway socket:

| Event | Payload | When |
|---|---|---|
| `conversation_mode` | `{ "on": bool, "owner": "desktop" \| "web" \| null, "state": "<state>" }` | Toggle, owner change, and every state change |

The web client sends:

| Call | Body | Effect |
|---|---|---|
| `POST /api/conversation/mode` | `{ "on": true }` or `{ "on": false }` | Claims or releases ownership as `web`. |
| Existing `POST /api/stt` | audio bytes | Unchanged transcription. The page submits the text with the existing task dispatch and `voice_conversation: true`. |
| Existing live TTS session | incremental text | Same session id for the whole reply. `submit` grows the text already in the session instead of requiring the full body before the WAV starts. |

Desktop does not go through these routes for its own microphone. It calls `LiveConversation` in-process and still broadcasts `conversation_mode` so an open web app can show the state and disable its toggle while `owner` is `desktop`.

---

## 9. Configuration

Under the existing `voice:` block in `config.example.yaml`:

```yaml
voice:
  conversation:
    enabled: false          # initial state of Live Conversation
    silence_ms: 700         # trailing silence that closes an utterance
    min_speech_ms: 400      # discard clips shorter than this
    ack_line: "Working on that."
```

`auto_speak_responses` still governs read-aloud of ordinary typed turns. Live Conversation speaks its own turns even when Auto-Read is off, because the user turned the mode on. It does not change the Auto-Read setting.

`push_to_talk_hotkey` is unchanged. While the mode is on, that hotkey interrupts (section 6).

---

## 10. Surfaces

### 10.1. Desktop pill (`eidos/ui/pill.py`, `eidos/ui/app.py`)

- Control label **Live** with states off / listening / transcribing / thinking / speaking / working.
- Enabling calls `LiveConversation.start("desktop")`.
- The existing mic button and hotkey follow section 6 while the mode is on.
- Status text stays the short state name. The transcript itself is the chat line, not the pill.

### 10.2. Web chat (`web/src/components/ChatView.tsx`)

- The same **Live** control.
- Enabling requests the microphone once, then claims `web`.
- Endpointing uses the analyser on that stream.
- Playback remains the single live-TTS audio element. The page reopens the recorder only after `conversation_mode.state` is `listening`.
- If `owner` is `desktop`, the control is disabled and the label shows that the desktop session has the conversation.

---

## 11. Safety and Overlap

- A Live Conversation turn is a normal `run_task`. The safety gate, allowlist, and confirmations still apply. A confirmation dialog is spoken as its prompt text, then the mode leaves `listening` until the user answers on the surface that raised it. It does not click through a confirmation by treating silence as yes.
- Scheduled `speak` notifications (`SPEC_SCHEDULED_WAKE.md`) wait until the session is `off` or `listening`, then play, and do not open the microphone by themselves.
- Background reflector and consolidator work stays under `SPEC_COMPUTE_RESOURCE_POLICY.md`. Live Conversation does not move Parakeet onto CUDA and does not start a second local chat model.
- Two Live Conversation loops must not run. The owner lock is the mechanism.

---

## 12. Phases

Each phase is usable on its own. Phase 1 is allowed to feel slow. Phase 2 is the one that makes the mode worth leaving on.

1. **Hands-free loop.** Session controller, owner lock, toggles on pill and web, endpointing, submit, speak the finished summary through the existing `_speak_task_summary` path, reopen the mic when idle. Push-to-talk and Auto-Read unchanged when the mode is off.
2. **Early speech.** `on_visible_delta`, first-sentence flush below the 80-character window, web WAV append, suppress the second reading in `_speak_task_summary`, fixed acknowledgement when the first step is only a tool call.
3. **Interrupt polish.** Hotkey and both mic buttons stop speech and return to listening. Kill switch forces `off`.

---

## 13. Acceptance

- Mode off: push-to-talk, Auto-Read, and typed send behave as they do now on desktop and web.
- Mode on, desktop: the user speaks a short sentence with no button, the sentence appears in the chat, Kokoro answers, and the mic is open again after playback without a click.
- Mode on, web: the same sequence through the browser microphone and the single live-TTS element, including after the phone screen locks during playback.
- Starting the mode on one surface turns it off on the other. The idle surface shows who holds it.
- While Kokoro is playing through desktop speakers, a recording is not running.
- A reply whose first sentence is shorter than 80 characters starts speaking at that sentence in phase 2, and is not spoken a second time when the task completes.
- A turn that only emits a tool call speaks "Working on that." once, then any later spoken sentences, and does not listen until the task is idle.
- Kill switch stops speech, closes the mic, and leaves the mode off.
- Thoughts and tool JSON are not in the audio.

---

## 14. Out of Scope

- Speech-to-speech models and any new paid voice API.
- Replacing Parakeet or Kokoro.
- Listening while the speakers are playing, and automatic barge-in.
- A different conversation history, persona, or tool policy for voice.
- Telegram voice notes as a third surface.
