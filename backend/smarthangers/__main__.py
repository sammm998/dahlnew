"""Starta servern: python -m smarthangers [--host 0.0.0.0] [--port 8000]"""
import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("smarthangers.api:get_app", factory=True, host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
