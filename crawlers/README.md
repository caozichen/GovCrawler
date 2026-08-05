# Crawler scripts

## Beijing public recruitment

`beijing_hr_recruitment.py` crawls the Beijing Municipal Human Resources and
Social Security Bureau public recruitment section. By default, it includes
articles from the same Beijing calendar date one month ago through today,
including both boundary dates.

The PA console invokes it as:

```text
python beijing_hr_recruitment.py \
  --url https://rsj.beijing.gov.cn/xxgk/gkzp/ \
  --output-dir <runner-created-directory> \
  --run-id <run-id>
```

Useful optional arguments:

- `--lookback-days 30`: use a fixed day range instead of one calendar month.
- `--request-delay 0.35`: minimum delay between HTTP requests.
- `--timeout 30`: timeout for each HTTP request in seconds.
- `--retries 3`: retry count for transient HTTP/network failures.
- `--max-attachment-mb 100`: per-attachment size limit.
- `--max-pages 1 --max-articles 2`: small smoke-test limits.
- `--skip-attachments`: parse attachment metadata without downloading files.

Each run writes:

```text
records.jsonl
errors.jsonl                 # only when partial failures occur
summary.json
articles/<article-id>.html
attachments/<article-id>/<attachment-name>
```

Run the offline regression tests from the project root:

```text
python -m unittest discover -s crawlers/tests -p "test_*.py"
```
