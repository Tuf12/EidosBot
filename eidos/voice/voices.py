"""Kokoro-82M voice catalog — kept in sync with OptimalX electron/voice/voices.js."""

from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_VOICE_ID = "bm_george"

# First letter of voice id → Kokoro KPipeline lang_code
LANG_CODE_BY_PREFIX: Dict[str, str] = {
    "a": "a",  # American English
    "b": "b",  # British English
    "e": "e",  # Spanish
    "f": "f",  # French
    "h": "h",  # Hindi
    "i": "i",  # Italian
    "j": "j",  # Japanese
    "p": "p",  # Brazilian Portuguese
    "z": "z",  # Mandarin Chinese
}

VOICE_META: Dict[str, Dict[str, str]] = {
    "af_alloy": {"label": "Alloy", "locale": "en-US", "gender": "female"},
    "af_aoede": {"label": "Aoede", "locale": "en-US", "gender": "female"},
    "af_bella": {"label": "Bella", "locale": "en-US", "gender": "female"},
    "af_heart": {"label": "Heart", "locale": "en-US", "gender": "female"},
    "af_jessica": {"label": "Jessica", "locale": "en-US", "gender": "female"},
    "af_kore": {"label": "Kore", "locale": "en-US", "gender": "female"},
    "af_nicole": {"label": "Nicole", "locale": "en-US", "gender": "female"},
    "af_nova": {"label": "Nova", "locale": "en-US", "gender": "female"},
    "af_river": {"label": "River", "locale": "en-US", "gender": "female"},
    "af_sarah": {"label": "Sarah", "locale": "en-US", "gender": "female"},
    "af_sky": {"label": "Sky", "locale": "en-US", "gender": "female"},
    "am_adam": {"label": "Adam", "locale": "en-US", "gender": "male"},
    "am_echo": {"label": "Echo", "locale": "en-US", "gender": "male"},
    "am_eric": {"label": "Eric", "locale": "en-US", "gender": "male"},
    "am_fenrir": {"label": "Fenrir", "locale": "en-US", "gender": "male"},
    "am_liam": {"label": "Liam", "locale": "en-US", "gender": "male"},
    "am_michael": {"label": "Michael", "locale": "en-US", "gender": "male"},
    "am_onyx": {"label": "Onyx", "locale": "en-US", "gender": "male"},
    "am_puck": {"label": "Puck", "locale": "en-US", "gender": "male"},
    "am_santa": {"label": "Santa", "locale": "en-US", "gender": "male"},
    "bf_alice": {"label": "Alice", "locale": "en-GB", "gender": "female"},
    "bf_emma": {"label": "Emma", "locale": "en-GB", "gender": "female"},
    "bf_isabella": {"label": "Isabella", "locale": "en-GB", "gender": "female"},
    "bf_lily": {"label": "Lily", "locale": "en-GB", "gender": "female"},
    "bm_daniel": {"label": "Daniel", "locale": "en-GB", "gender": "male"},
    "bm_fable": {"label": "Fable", "locale": "en-GB", "gender": "male"},
    "bm_george": {"label": "George", "locale": "en-GB", "gender": "male"},
    "bm_lewis": {"label": "Lewis", "locale": "en-GB", "gender": "male"},
    "ef_dora": {"label": "Dora", "locale": "es", "gender": "female"},
    "em_alex": {"label": "Alex", "locale": "es", "gender": "male"},
    "em_santa": {"label": "Santa", "locale": "es", "gender": "male"},
    "ff_siwis": {"label": "Siwis", "locale": "fr-FR", "gender": "female"},
    "hf_alpha": {"label": "Alpha", "locale": "hi", "gender": "female"},
    "hf_beta": {"label": "Beta", "locale": "hi", "gender": "female"},
    "hm_omega": {"label": "Omega", "locale": "hi", "gender": "male"},
    "hm_psi": {"label": "Psi", "locale": "hi", "gender": "male"},
    "if_sara": {"label": "Sara", "locale": "it", "gender": "female"},
    "im_nicola": {"label": "Nicola", "locale": "it", "gender": "male"},
    "jf_alpha": {"label": "Alpha", "locale": "ja", "gender": "female"},
    "jf_gongitsune": {"label": "Gongitsune", "locale": "ja", "gender": "female"},
    "jf_nezumi": {"label": "Nezumi", "locale": "ja", "gender": "female"},
    "jf_tebukuro": {"label": "Tebukuro", "locale": "ja", "gender": "female"},
    "jm_kumo": {"label": "Kumo", "locale": "ja", "gender": "male"},
    "pf_dora": {"label": "Dora", "locale": "pt-BR", "gender": "female"},
    "pm_alex": {"label": "Alex", "locale": "pt-BR", "gender": "male"},
    "pm_santa": {"label": "Santa", "locale": "pt-BR", "gender": "male"},
    "zf_xiaobei": {"label": "Xiaobei", "locale": "zh", "gender": "female"},
    "zf_xiaoni": {"label": "Xiaoni", "locale": "zh", "gender": "female"},
    "zf_xiaoxiao": {"label": "Xiaoxiao", "locale": "zh", "gender": "female"},
    "zf_xiaoyi": {"label": "Xiaoyi", "locale": "zh", "gender": "female"},
    "zm_yunjian": {"label": "Yunjian", "locale": "zh", "gender": "male"},
    "zm_yunxi": {"label": "Yunxi", "locale": "zh", "gender": "male"},
    "zm_yunxia": {"label": "Yunxia", "locale": "zh", "gender": "male"},
    "zm_yunyang": {"label": "Yunyang", "locale": "zh", "gender": "male"},
}


def lang_code_for_voice(voice_id: str) -> str:
    prefix = str(voice_id or "a")[0].lower()
    return LANG_CODE_BY_PREFIX.get(prefix, "a")


def list_voices() -> List[Dict[str, Any]]:
    voices: List[Dict[str, Any]] = []
    for voice_id, meta in VOICE_META.items():
        voices.append(
            {
                "id": voice_id,
                "label": meta["label"],
                "locale": meta["locale"],
                "gender": meta["gender"],
                "langCode": lang_code_for_voice(voice_id),
                "displayName": f"{meta['label']} ({meta['locale']}, {meta['gender']})",
            }
        )
    return voices


def normalize_voice_id(voice_id: str | None) -> str:
    voice = str(voice_id or "").strip()
    if voice and voice in VOICE_META:
        return voice
    return DEFAULT_VOICE_ID


def get_voice(voice_id: str | None) -> Dict[str, Any]:
    voice = normalize_voice_id(voice_id)
    meta = VOICE_META[voice]
    return {
        "id": voice,
        "label": meta["label"],
        "locale": meta["locale"],
        "gender": meta["gender"],
        "langCode": lang_code_for_voice(voice),
        "displayName": f"{meta['label']} ({meta['locale']}, {meta['gender']})",
    }
