import argparse
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    request = urllib.request.Request(
        args.url,
        headers={"User-Agent": "PA-Crawler-Console/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode("utf-8", errors="replace")

    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    record = {
        "schema_version": "1.0",
        "run_id": args.run_id,
        "source_url": args.url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "title": re.sub(r"\s+", " ", match.group(1)).strip() if match else "",
        "html_length": len(html),
    }

    output = Path(args.output_dir) / "records.jsonl"
    output.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote 1 record to {output}")


if __name__ == "__main__":
    main()
