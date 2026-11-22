import { useEffect, useState } from 'react';
import { Cloud, Cpu, RefreshCw, Settings, X } from 'lucide-react';
import { CloudProvider, ModelPreference, ModelSettings } from '../types/gateway';

interface ModelSettingsModalProps {
  isOpen: boolean;
  token: string;
  onClose: () => void;
}

const MODES: { id: ModelPreference; label: string; hint: string }[] = [
  { id: 'local', label: 'Local', hint: 'This PC’s GPU' },
  { id: 'auto', label: 'Auto', hint: 'Local, then cloud' },
  { id: 'cloud', label: 'Cloud', hint: 'Always the cloud model' },
];

async function requestModels(token: string, refresh: boolean): Promise<ModelSettings> {
  const response = await fetch(refresh ? '/api/models?refresh=1' : '/api/models', {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    if (response.status === 401) {
      throw new Error('Sign in with the gateway token first.');
    }
    throw new Error(data.error || 'Could not load model settings.');
  }
  return data as ModelSettings;
}

function keepLonger(current: string[], incoming: string[]): string[] {
  return incoming.length >= current.length ? incoming : current;
}

function mergeSaved(current: ModelSettings, saved: ModelSettings): ModelSettings {
  return {
    ...saved,
    local: {
      ...saved.local,
      models: keepLonger(current.local.models, saved.local.models),
      reachable: saved.local.reachable ?? current.local.reachable,
    },
    chutes: {
      ...saved.chutes,
      models: keepLonger(current.chutes.models, saved.chutes.models),
    },
    meta: {
      ...saved.meta,
      models: keepLonger(current.meta.models, saved.meta.models),
      efforts: saved.meta.efforts?.length ? saved.meta.efforts : current.meta.efforts,
    },
  };
}

export function ModelSettingsModal({ isOpen, token, onClose }: ModelSettingsModalProps) {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotice(null);
    requestModels(token, false)
      .then((data) => {
        if (!cancelled) setSettings(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, token]);

  if (!isOpen) return null;

  const backend = !settings
    ? 'local'
    : settings.preference === 'local'
      ? 'local'
      : settings.cloud_provider === 'meta'
        ? 'meta'
        : 'chutes';
  const group = settings ? settings[backend] : null;
  const providerReady = !settings || backend === 'local' || settings[backend].configured;

  const updateGroup = (field: 'reasoning_model' | 'vision_model', value: string) => {
    setSettings((prev) => {
      if (!prev) return prev;
      const key = prev.preference === 'local' ? 'local' : prev.cloud_provider === 'meta' ? 'meta' : 'chutes';
      return { ...prev, [key]: { ...prev[key], [field]: value } };
    });
  };

  const refreshLists = async () => {
    setRefreshing(true);
    setError(null);
    try {
      const data = await requestModels(token, true);
      setSettings((prev) => (prev ? { ...data, ...selectionFrom(prev, data) } : data));
      setNotice('Model lists updated.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not refresh model lists.');
    } finally {
      setRefreshing(false);
    }
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    const body: Record<string, string> = {
      preference: settings.preference,
      cloud_provider: settings.cloud_provider,
    };
    const pairs: Array<[string, string]> = [
      ['local_reasoning_model', settings.local.reasoning_model],
      ['local_vision_model', settings.local.vision_model],
      ['chutes_reasoning_model', settings.chutes.reasoning_model],
      ['chutes_vision_model', settings.chutes.vision_model],
      ['meta_reasoning_model', settings.meta.reasoning_model],
      ['meta_vision_model', settings.meta.vision_model],
      ['meta_reasoning_effort', settings.meta.reasoning_effort],
    ];
    for (const [key, value] of pairs) {
      const text = value.trim();
      if (text) body[key] = text;
    }
    try {
      const response = await fetch('/api/models', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || 'Could not save the model.');
      }
      setSettings((prev) => (prev ? mergeSaved(prev, data as ModelSettings) : (data as ModelSettings)));
      const saved = data.persisted === false
        ? `${data.summary}. It is live now, but this PC could not save it for the next launch.`
        : `${data.summary}. The next reply uses this model.`;
      setNotice(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the model.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-3 sm:p-4">
      <div className="bg-[#1e1f29] border border-[#44475a] rounded-2xl w-full max-w-lg shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-[#44475a] bg-[#282a36]/50">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-[#8be9fd]/15 border border-[#8be9fd]/40 flex items-center justify-center text-[#8be9fd] shrink-0">
              <Settings className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-[#f8f8f2]">AI Model Settings</h2>
              <p className="text-[11px] text-[#8be9fd] truncate">
                {settings?.summary || 'Choose which model answers while you are away'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#6272a4] hover:text-[#f8f8f2] hover:bg-[#44475a] transition active:scale-95"
            aria-label="Close model settings"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto text-xs text-[#f8f8f2] flex-1">
          <p className="text-[11px] text-[#6272a4] leading-relaxed">
            API keys stay on this PC. This only switches which model answers the next message.
          </p>

          {loading && <p className="text-[#6272a4]">Loading the models on this PC…</p>}
          {error && (
            <p className="rounded-lg border border-[#ff5555]/40 bg-[#ff5555]/10 px-3 py-2 text-[#ff5555]">{error}</p>
          )}
          {notice && (
            <p className="rounded-lg border border-[#50fa7b]/40 bg-[#50fa7b]/10 px-3 py-2 text-[#50fa7b]">{notice}</p>
          )}

          {settings && group && (
            <>
              <section className="space-y-2">
                <span className="font-semibold">Where replies run</span>
                <div className="grid grid-cols-3 gap-2">
                  {MODES.map((mode) => (
                    <button
                      key={mode.id}
                      onClick={() => setSettings({ ...settings, preference: mode.id })}
                      className={`rounded-xl border px-2 py-2 text-left transition active:scale-95 ${
                        settings.preference === mode.id
                          ? 'border-[#bd93f9] bg-[#bd93f9]/15 text-[#f8f8f2]'
                          : 'border-[#44475a] bg-[#282a36] text-[#f8f8f2]'
                      }`}
                    >
                      <span className="block text-xs font-bold">{mode.label}</span>
                      <span className="block text-[10px] text-[#6272a4]">{mode.hint}</span>
                    </button>
                  ))}
                </div>
              </section>

              <section className="space-y-2">
                <span className="font-semibold">Cloud service</span>
                <div className="grid grid-cols-2 gap-2">
                  {(['chutes', 'meta'] as CloudProvider[]).map((provider) => (
                    <button
                      key={provider}
                      onClick={() => setSettings({ ...settings, cloud_provider: provider })}
                      className={`rounded-xl border px-3 py-2 text-left transition active:scale-95 ${
                        settings.cloud_provider === provider
                          ? 'border-[#8be9fd] bg-[#8be9fd]/10'
                          : 'border-[#44475a] bg-[#282a36]'
                      }`}
                    >
                      <span className="flex items-center gap-1.5 font-bold">
                        {provider === 'chutes' ? <Cloud className="w-3.5 h-3.5" /> : <Cpu className="w-3.5 h-3.5" />}
                        {provider === 'chutes' ? 'Chutes' : 'Meta Muse'}
                      </span>
                      <span className="block text-[10px] text-[#6272a4]">
                        {settings[provider].configured ? 'Key saved on this PC' : 'No key saved on this PC'}
                      </span>
                    </button>
                  ))}
                </div>
                {settings.preference === 'local' && (
                  <p className="text-[10px] text-[#6272a4]">Used when you switch to Auto or Cloud.</p>
                )}
              </section>

              <ModelField
                label={backend === 'local' ? 'Local chat model' : 'Chat model'}
                value={group.reasoning_model}
                options={group.models}
                listId="eidos-chat-models"
                onChange={(value) => updateGroup('reasoning_model', value)}
              />
              <ModelField
                label={backend === 'local' ? 'Local vision model' : 'Vision model'}
                value={group.vision_model}
                options={group.models}
                listId="eidos-vision-models"
                onChange={(value) => updateGroup('vision_model', value)}
              />

              {backend === 'meta' && (
                <label className="block space-y-1.5">
                  <span className="font-semibold">Thinking effort</span>
                  <select
                    value={settings.meta.reasoning_effort}
                    onChange={(event) =>
                      setSettings({
                        ...settings,
                        meta: { ...settings.meta, reasoning_effort: event.target.value },
                      })
                    }
                    className="w-full rounded-lg border border-[#44475a] bg-[#282a36] px-3 py-2 text-sm text-[#f8f8f2]"
                  >
                    {settings.meta.efforts.map((effort) => (
                      <option key={effort} value={effort}>
                        {effort}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              {!providerReady && (
                <p className="text-[11px] text-[#ffb86c]">
                  This provider has no API key on the PC. Replies will fail until the key is saved in the desktop settings.
                </p>
              )}
              {backend === 'local' && settings.local.reachable === false && (
                <p className="text-[11px] text-[#ffb86c]">
                  Ollama did not answer on this PC. You can still select a saved local model.
                </p>
              )}
            </>
          )}
        </div>

        <div className="p-4 border-t border-[#44475a] flex items-center justify-between gap-2">
          <button
            onClick={refreshLists}
            disabled={refreshing || loading}
            className="flex items-center gap-1.5 rounded-lg border border-[#44475a] bg-[#282a36] px-3 py-2 text-xs font-semibold text-[#f8f8f2] disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={save}
            disabled={
              !settings ||
              saving ||
              loading ||
              !group?.reasoning_model.trim() ||
              !group?.vision_model.trim()
            }
            className="rounded-lg bg-[#bd93f9] px-4 py-2 text-xs font-bold text-[#1e1f29] disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Use this model'}
          </button>
        </div>
      </div>
    </div>
  );
}

function selectionFrom(edited: ModelSettings, refreshed: ModelSettings): Partial<ModelSettings> {
  return {
    preference: edited.preference,
    cloud_provider: edited.cloud_provider,
    local: { ...refreshed.local, reasoning_model: edited.local.reasoning_model, vision_model: edited.local.vision_model },
    chutes: {
      ...refreshed.chutes,
      reasoning_model: edited.chutes.reasoning_model,
      vision_model: edited.chutes.vision_model,
    },
    meta: {
      ...refreshed.meta,
      reasoning_model: edited.meta.reasoning_model,
      vision_model: edited.meta.vision_model,
      reasoning_effort: edited.meta.reasoning_effort,
    },
  };
}

function ModelField({
  label,
  value,
  options,
  listId,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  listId: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="font-semibold">{label}</span>
      <input
        list={listId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-[#44475a] bg-[#282a36] px-3 py-2 text-sm text-[#f8f8f2]"
        placeholder="Type or pick a model id"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
      />
      <datalist id={listId}>
        {options.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
    </label>
  );
}
