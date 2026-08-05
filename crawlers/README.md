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

## Dongcheng public-institution recruitment

`dongcheng_public_institutions.py` crawls the newest 10 articles from the
Dongcheng District public-institution recruitment section. It saves article
text and raw HTML, detects and downloads attachments, and skips any individual
attachment larger than 5 MB.

The PA console invokes it as:

```text
python dongcheng_public_institutions.py \
  --url https://www.bjdch.gov.cn/zwgk/rsxx/sydwzlxx/ \
  --output-dir <runner-created-directory> \
  --run-id <run-id>
```

Useful optional arguments:

- `--max-articles 10`: number of newest list entries to crawl.
- `--request-delay 0.35`: minimum delay between HTTP requests.
- `--timeout 30`: timeout for each HTTP request in seconds.
- `--retries 3`: retry count for transient HTTP/network failures.
- `--max-attachment-mb 5`: per-attachment size limit.
- `--skip-attachments`: parse attachment metadata without downloading files.

## Beijing Chaoyang recruitment

`beijing_chaoyang_recruitment.py` crawls the newest entries from the Beijing
Chaoyang District recruitment list. It processes the newest 10 list entries in
their published order, saves each available article, and downloads attachments
up to 5 MiB. An unavailable article or attachment is logged and written to
`errors.jsonl` without stopping the remaining entries.

The PA console invokes it as:

```text
python beijing_chaoyang_recruitment.py \
  --url http://www.bjchy.gov.cn/affair/civilemployed/zhk/ \
  --output-dir <runner-created-directory> \
  --run-id <run-id>
```

Useful optional arguments:

- `--limit 10`: number of newest list entries to process.
- `--max-attachment-mb 5`: per-attachment size limit.
- `--timeout 60 --retries 3`: request timeout and transient failure retries.
- `--skip-attachments`: parse attachment metadata without downloading files.
