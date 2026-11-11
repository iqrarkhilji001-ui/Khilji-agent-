"""Start the Personal AI Agent server:  python run.py
Check your setup without starting the server:  python run.py --check   (makes one tiny real API call)"""
import logging
import sys

from agent_app import preflight
from agent_app.config import Config, ConfigError
from agent_app.llm import GeminiClient
from agent_app.server import create_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    for item in preflight.config_warnings(cfg):
        print(f"[{item['level'].upper()}] {item['message']}")
    if "--check" in sys.argv:
        result = preflight.check_api(cfg, GeminiClient(cfg))
        print(f"[{'OK' if result['ok'] else 'FAIL'}] API: {result['message']}")
        search = preflight.check_search(cfg)
        print(f"[{'OK' if search['ok'] else 'FAIL'}] Search: {search['message']}")
        return 0 if result["ok"] else 1
    if cfg.token_generated:
        print(f"\nA new access token was generated and saved to {cfg.token_file}")
        print(f"Access token (paste it in the app once): {cfg.token}\n")
    else:
        print(f"Access token is in AGENT_TOKEN or {cfg.token_file}")
    app = create_app(cfg)
    app.run(host=cfg.host, port=cfg.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
