# PianoKT 流行钢琴 MIDI 一键流水线

将 `pop_oneclick.py` 和 `pop_catalog.py` 放进现有 `pianokt_300_auto/`（含 `build.py`、`phrase_v5.py`）中。

```bash
pip install requests beautifulsoup4 pretty_midi mido
python pop_oneclick.py --provider all --limit 500 --enrich 100 --download-limit 200
```

一次执行：发现候选 → 抓取详情页公开的 MIDI 链接 → 下载 → 识别 L/R 音轨 → 仅导入**已独立核实授权并填写相应字段**的曲目。每阶段更新 `pop_candidates.csv`，重复运行保留原 CSV 里手动补充的元数据，并跳过已下载文件。旧 `output/accepted/` 不被覆盖。

**关键限制**
- MidiShow 当前只包含 10 条已知页面种子，不是全站搜索；依网站正常下载并在 `local_midi` 填本地路径。
- Tuneonmusic 下载只支持在 HTML 中直接公开的 `.mid/.midi` 链接。JS 点击控件、登录限制或页面结构变化会导致 0 下载，脚本不绕过。
- 自动搜索不能证明版权许可。只有候选在 CSV 中具备真实作曲者、`license`、`license_url`、`rights_note`（`verified-for-research` 或 `verified-for-publication`），且通过原有左右手验证、难度和 v5 乐句分段，才可能进入 `accepted/`。
- 先只生成候选而不导入：加 `--no-ingest`。
- `--enrich 100` 代表最多读取 100 个详情页，不保证 100 首实际 MIDI。
- 请保持 `output/` 的完整备份。报告为 `output/pop_oneclick_summary.json`。

若已下载网页 MIDI 但无分手，状态仍是 `hand_mapping_pending`，不会强制误判。
