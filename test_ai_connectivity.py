from __future__ import annotations

from dotenv import load_dotenv

import gemini_ai


def test_provider(name: str, fn) -> None:
    system = "Return strict JSON only."
    prompt = (
        '{"decision":"OK","score":90,'
        '"pattern":"connectivity","reason":"test"}'
    )
    try:
        data = fn(system, prompt)
    except Exception as exc:
        print(f"{name}: FAIL {type(exc).__name__}: {str(exc)[:220]}")
        return
    print(f"{name}: OK decision={data.get('decision')} score={data.get('score')}")


def main() -> int:
    load_dotenv()
    print(f"configured_primary={gemini_ai.ai_provider()}")
    print(f"configured_fallback={gemini_ai.fallback_ai_provider(gemini_ai.ai_provider())}")
    print(f"ai_enabled={gemini_ai.ai_enabled()}")
    test_provider("nvidia", gemini_ai.call_nvidia_json)
    test_provider("gemini", gemini_ai.call_gemini_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
