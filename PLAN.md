# План: tg-toolkit

Entry point: open bugs, plans and ideas. Date 2026-11-05. Rule: закрытый пункт удаляется или получает ✅ со ссылкой на коммит.
(Repo docs are English, so items are in English.)

## Баги

HIGH
- [ ] Bare `collect`/`read_messages` has no default bound: `DEFAULT_MAX_SCAN` is exported but applied by nothing, a first run does a full-history scan and invites FloodWait. [reader.py:50](src/tg_toolkit/reader.py), logic-map #5, system-design Risks
- [ ] `max_scan` truncation is invisible to the caller; storing the last row id as next `min_id` skips messages for good. [reader.py:243](src/tg_toolkit/reader.py), logic-map #10, system-design Risks

MEDIUM
- [x] #13: no `--` before title/body in `notify-send`; text starting with `-` shows no toast and is not logged. [push.py:125](src/tg_toolkit/push.py), [fixes #13](docs/plans/2026-11-05-logic-map-fixes.md) ✅ 2026-11-05: `--` now precedes title/body in notify-send argv, tests in tests/test_push.py, не закоммичено
- [x] #1: `notify-send -A` is not retried without `-A` on a rejecting version. [push.py:91](src/tg_toolkit/push.py), [fixes #1](docs/plans/2026-11-05-logic-map-fixes.md) ✅ 2026-11-05: `notify_with_action` retries once without `-A` on non-zero exit, tests in tests/test_push.py, не закоммичено
- [x] #2: `subprocess.run` for notify-send has no timeout, a hung process blocks the caller. [push.py:84](src/tg_toolkit/push.py), [fixes #2](docs/plans/2026-11-05-logic-map-fixes.md) ✅ 2026-11-05: `notify` passes `timeout=NOTIFY_TIMEOUT_S` (10 s) and logs `TimeoutExpired`, tests in tests/test_push.py, не закоммичено
- [ ] #8: `client.session.flood_sleep_threshold` is dead in Telethon 1.x (and a test enshrines it). [reader.py:99](src/tg_toolkit/reader.py), [fixes #8](docs/plans/2026-11-05-logic-map-fixes.md)
- [ ] `iter_flood_safe` sleeps `exc.seconds` with no cap or repeat limit; a long ban stalls a caller for hours. [reader.py:178](src/tg_toolkit/reader.py), system-design Risks
- [ ] Concurrent callers on one `<session>.session` can hit `database is locked` (assumed, not reproduced). system-design Risks, [reader.py](src/tg_toolkit/reader.py) `make_client`

LOW (details in [the fixes plan](docs/plans/2026-11-05-logic-map-fixes.md))
- [ ] #4: half-set `api_id`/`api_hash` and a corrupt credentials cache fail unclearly or silently.
- [ ] #9: `O_TRUNC` empties the cache before `int(api_id)` validates, leaving an empty cache. [reader.py:70](src/tg_toolkit/reader.py), system-design Risks
- [ ] #11: naive `since`/`until` raises `TypeError` mid-generator.
- [ ] #3: FloodWait inside iterator `_init` and uncapped repeats (confirmed by reading Telethon 1.44).
- [ ] #6, #12: export shape differs from real Desktop `result.json` (text_link href, plain segments, channel ids, chat_type, media_type).
- [ ] `bot_send` has no retry; one transient failure drops the alert. [push.py](src/tg_toolkit/push.py), system-design Risks

## Планы и фичи

- [ ] Fix order from the logic-map plan: 1) #13, #1, #2; 2) #8, then #10; 3) #4, #9, #11, #5; 4) #12, #6. [order of work](docs/plans/2026-11-05-logic-map-fixes.md)
- [ ] Close the logic-map plan: every finding has ✅ or a "keep as is" decision; Known gaps in the map lists every open HIGH/MEDIUM finding. [done when](docs/plans/2026-11-05-logic-map-fixes.md)
- [ ] Expose a "truncated" signal from `collect` (callback or attribute), or document that the cursor must not advance after a cap hit. logic-map #10

## Идеи

- [ ] #5 (question for the owner): document `DEFAULT_MAX_SCAN`/`DEFAULT_WINDOW_DAYS` as caller constants, or apply them in `read_messages`. [fixes #5](docs/plans/2026-11-05-logic-map-fixes.md)
- [ ] #7: move telethon to an optional extra, or keep and say so. [pyproject.toml:18](pyproject.toml)
- [ ] Next rung: shared async client / per-chat scheduling. Trigger: wall-clock per run > 50% of the run interval, or repeated FloodWait > 60 s in logs. [system-design §8](docs/system-design.md)
- [ ] Give each concurrent caller its own session file (guidance, no code). [system-design §5](docs/system-design.md)

## Подробные планы

- [docs/plans/2026-11-05-logic-map-fixes.md](docs/plans/2026-11-05-logic-map-fixes.md): logic-map findings #1-#13
- [docs/logic-map.md](docs/logic-map.md): how a chat becomes rows and a message becomes a push
- [docs/system-design.md](docs/system-design.md): system-design review and Risks, 2026-11-05
- Cross-repo summary: `~/dev/docs/system-design-report-2026-11-05.md`
