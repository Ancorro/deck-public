package dev.deck.app;

import android.Manifest;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.util.ArrayList;
import java.util.Locale;

// Dictation for the message box. Android 13+: Google's on-device recognizer (the Pixel's newer speech model), with
// formatting (punctuation, capitals) and one long "segmented" session that keeps listening through pauses.
// Older phones, or no on-device model for the language yet: the regular recognizer, one phrase per session.
// Events: partial {text} (current phrase so far), segment {text} (a finished phrase), end, error {code, message}.
@CapacitorPlugin(name = "Dictation", permissions = { @Permission(strings = { Manifest.permission.RECORD_AUDIO }, alias = "microphone") })
public class DictationPlugin extends Plugin {
    private SpeechRecognizer rec;
    private boolean onDevice;

    @PluginMethod
    public void start(PluginCall call) {
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            requestPermissionForAlias("microphone", call, "micPermission");
            return;
        }
        begin(call, true);
    }

    @PermissionCallback
    private void micPermission(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED) begin(call, true);
        else call.reject("Microphone permission denied");
    }

    @PluginMethod
    public void stop(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            if (rec != null) rec.stopListening(); // the session ends with its last segment, then "end"
            call.resolve();
        });
    }

    @PluginMethod
    public void cancel(PluginCall call) {
        getActivity().runOnUiThread(() -> { release(); call.resolve(); });
    }

    private void release() {
        if (rec != null) { rec.cancel(); rec.destroy(); rec = null; }
    }

    private Intent intent(String lang) {
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang);
        i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        if (Build.VERSION.SDK_INT >= 33) {
            i.putExtra(RecognizerIntent.EXTRA_ENABLE_FORMATTING, RecognizerIntent.FORMATTING_OPTIMIZE_QUALITY);
            i.putExtra(RecognizerIntent.EXTRA_MASK_OFFENSIVE_WORDS, false);
            if (onDevice) {
                // One session for the whole dictation: a pause ends a segment, not the session.
                i.putExtra(RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS);
                i.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L);
            }
        }
        return i;
    }

    private void begin(PluginCall call, boolean tryOnDevice) {
        String lang = call.getString("language", Locale.getDefault().toLanguageTag());
        getActivity().runOnUiThread(() -> {
            release();
            onDevice = tryOnDevice && Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(getContext());
            try {
                rec = onDevice ? SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext())
                               : SpeechRecognizer.createSpeechRecognizer(getContext());
            } catch (Exception e) {
                call.reject(e.getMessage());
                return;
            }
            final Intent intent = intent(lang);
            final SpeechRecognizer mine = rec;
            // Callbacks from an older session (it reports "server disconnected" once replaced or idle) are ignored.
            rec.setRecognitionListener(new RecognitionListener() {
                boolean current() { return rec == mine; }
                void finished() { notifyListeners("end", new JSObject()); release(); } // close it: no late errors
                @Override public void onReadyForSpeech(Bundle b) {}
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float v) {}
                @Override public void onBufferReceived(byte[] b) {}
                @Override public void onEndOfSpeech() {}
                @Override public void onEvent(int t, Bundle b) {}

                @Override public void onPartialResults(Bundle b) { if (current()) emit("partial", b); }

                @Override public void onSegmentResults(Bundle b) { if (current()) emit("segment", b); }

                @Override public void onEndOfSegmentedSession() { if (current()) finished(); }

                @Override public void onResults(Bundle b) { // non-segmented: one phrase, then the session is over
                    if (!current()) return;
                    emit("segment", b);
                    finished();
                }

                @Override public void onError(int code) {
                    if (!current()) return;
                    boolean noModel = code == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED
                        || code == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE;
                    if (onDevice && noModel) {
                        // Fetch the on-device model for next time; carry on with the regular recognizer now.
                        try { SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext()).triggerModelDownload(intent); }
                        catch (Exception ignored) {}
                        begin(call, false);
                        return;
                    }
                    JSObject e = new JSObject();
                    e.put("code", code);
                    e.put("message", describe(code));
                    release();
                    notifyListeners("error", e);
                }
            });
            rec.startListening(intent);
            JSObject r = new JSObject();
            r.put("onDevice", onDevice);
            call.resolve(r);
        });
    }

    private void emit(String event, Bundle b) {
        ArrayList<String> m = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        JSObject o = new JSObject();
        o.put("text", m != null && !m.isEmpty() ? m.get(0) : "");
        notifyListeners(event, o);
    }

    private static String describe(int code) {
        switch (code) {
            case SpeechRecognizer.ERROR_NO_MATCH: return "didn't catch that";
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT: return "no speech heard";
            case SpeechRecognizer.ERROR_AUDIO: return "microphone error";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS: return "microphone permission needed";
            case SpeechRecognizer.ERROR_NETWORK: case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "network error";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY: return "recognizer busy";
            default: return "speech error " + code;
        }
    }
}
