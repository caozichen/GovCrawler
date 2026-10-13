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

Each Beijing HR run writes:

```text
records.jsonl
errors.jsonl                 # only when partial failures occur
summary.json
articles/<article-id>.html
attachments/<article-id>/<attachment-name>
```

## Community-worker recruitment (Dongcheng / Chaoyang)

`dongcheng_community_recruitment.py` scans Dongcheng's official notices and
public-institution sections, supplemented by the official site's JSON search
for archived community announcements. `chaoyang_community_recruitment.py` scans Chaoyang's
other-recruitment section. Both paginate across the selected date range and
identify community-worker recruitment and its follow-up announcements.

Use the existing console protocol, for example:

```text
python dongcheng_community_recruitment.py \
  --url https://www.bjdch.gov.cn/zwgk/tzgg/ \
  --output-dir <runner-created-directory> \
  --run-id <run-id>

python chaoyang_community_recruitment.py \
  --url http://www.bjchy.gov.cn/affair/civilemployed/zhk/ \
  --output-dir <runner-created-directory> \
  --run-id <run-id>
```

- `--mode auto` (default): first run looks back 730 days; later runs use each
  source's last complete scan date minus a seven-day overlap. A missed month
  is therefore included automatically on the next run.
- `--mode backfill --from-date 2025-01-01 --until-date 2025-12-31`: explicit
  historical discovery without advancing the normal incremental watermark.
- `--lookback-days 730`, `--overlap-days 7`, `--max-pages 200`: range and
  pagination controls. Hitting the page limit is an incomplete scan, not a
  successful empty result.
- `--state-dir <absolute-directory>`: persistent SQLite directory; defaults to
  `.crawler-console/community-recruitment` beneath the project root, independent
  of the console runner's working directory.
- `--request-delay 0.5 --timeout 30 --retries 3`: HTTP controls.
- `--max-attachment-mb 20` and `--skip-attachments`: attachment controls.

In addition to the existing raw HTML, attachments, `records.jsonl`,
`errors.jsonl`, and `summary.json`, each run writes `catalog.json`, containing
all known recruitment plans with current application status and related events.
The summary distinguishes new/updated announcements from the number of plans.
Office/PDF job tables are downloaded but not parsed into position/vacancy counts.
Dongcheng search responses are saved under `search-pages/`. Coverage refers to
available column pages and indexed community titles in the target columns;
it cannot guarantee discovery of unlisted, unindexed, or off-site announcements.

Application states are `upcoming`, `open`, `closed`, `cancelled`, and `unknown`.
Exam, interview, score and hiring-result notices do not create new application
opportunities. Deadlines without a time remain uncertain on their final day;
ambiguous changes and campaign relationships need review. Evidence and last
verification time are retained. Open/upcoming/unknown plans and recently closed
plans are rechecked even after leaving the discovery window; failed articles or
attachments are retried on the next run. Incomplete coverage or request errors
produce a nonzero exit status while preserving partial results.

Two new built-in district tasks are imported automatically when the console
server restarts. Their suggested interval is six hours; automatic scheduling
remains unavailable until the console's separate scheduler feature is enabled.

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
