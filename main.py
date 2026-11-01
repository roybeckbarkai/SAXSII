"""Start the SAXSII web app locally and open it in the default browser.

    python main.py [--port 5000] [--no-browser]
"""
import argparse
import threading
import webbrowser


def main():
    parser = argparse.ArgumentParser(description="SAXSII - SAXS image integration in the browser")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to listen on (default: this computer only)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    from server import create_app
    app = create_app()
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"SAXSII running at {url}  (Ctrl+C to stop)")
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
